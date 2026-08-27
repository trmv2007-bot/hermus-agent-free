"""Local speech synthesis for Hermus Talking Mode.

The service deliberately avoids paid/cloud speech APIs.  It discovers a local
backend in this order:

1. Piper (neural TTS) when ``piper`` and ``HERMUS_PIPER_MODEL`` are available.
2. ``espeak-ng`` / ``espeak`` for a lightweight offline fallback.
3. ``pyttsx3`` when the optional Python package is installed.

Generated audio is stored under ``data/speech`` and can be served by the
FastAPI gateway.  Missing TTS software is reported as a normal unavailable
state rather than breaking chat or the dashboard.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import sys
from ctypes.util import find_library
from pathlib import Path
from typing import Any, Optional

from .config import config

_MAX_SPEECH_CHARS = 6000
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")


def speech_root() -> Path:
    root = config.resolve_path(os.getenv("HERMUS_SPEECH_DIR", "data/speech"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def prepare_speech_text(text: str) -> str:
    """Turn a Markdown-ish agent response into safe, readable speech text."""
    value = str(text or "").strip()
    value = re.sub(r"```[\s\S]*?```", " Code block omitted. ", value)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = re.sub(r"https?://\S+", " link ", value)
    value = re.sub(r"^[#>*+\-]+\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"[*_`~]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:_MAX_SPEECH_CHARS]


class SpeechEngine:
    """Discover and invoke a local speech backend."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else speech_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _discover(self) -> tuple[Optional[str], dict[str, Any]]:
        requested = os.getenv("HERMUS_TTS_BACKEND", "auto").strip().lower()
        piper = shutil.which("piper")
        piper_model = os.getenv("HERMUS_PIPER_MODEL", "").strip()
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")

        if requested in ("auto", "piper") and piper and piper_model and Path(piper_model).expanduser().exists():
            return "piper", {"executable": piper, "model": str(Path(piper_model).expanduser())}
        if requested in ("auto", "espeak", "espeak-ng") and espeak:
            return "espeak", {"executable": espeak}
        if requested in ("auto", "pyttsx3"):
            try:
                import pyttsx3  # noqa: F401
                # On Linux pyttsx3 is only a wrapper; without the eSpeak shared
                # library it imports successfully but fails on the first word.
                native_ok = (
                    not sys.platform.startswith("linux")
                    or bool(find_library("espeak-ng") or find_library("espeak"))
                )
                if native_ok:
                    return "pyttsx3", {}
            except Exception:
                pass

        detail = {
            "requested": requested,
            "piper_executable": piper,
            "piper_model": piper_model or None,
            "espeak_executable": espeak,
        }
        return None, detail

    def status(self) -> dict[str, Any]:
        backend, detail = self._discover()
        return {
            "available": bool(backend),
            "backend": backend,
            "local": True,
            "output_format": "wav",
            "directory": str(self.root),
            "detail": detail,
            "setup": (
                "Set HERMUS_PIPER_MODEL and install piper, or install espeak-ng. "
                "Optional fallback: pyttsx3 with a supported OS speech driver."
            ) if not backend else None,
        }

    def synthesize(self, text: str, voice: Optional[str] = None, rate: int = 165) -> dict[str, Any]:
        # Runtime cleanup or an operator may remove data/speech while the
        # gateway stays up; recreate it before every generation.
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.cleanup(int(os.getenv("HERMUS_SPEECH_MAX_AGE_HOURS", "24")))
        except (TypeError, ValueError):
            self.cleanup(24)
        spoken = prepare_speech_text(text)
        if not spoken:
            return {"success": False, "error": "text required"}
        backend, detail = self._discover()
        if not backend:
            return {
                "success": False,
                "error": "No local TTS backend is configured",
                "status": self.status(),
            }

        audio_id = uuid.uuid4().hex
        path = self.root / f"{audio_id}.wav"
        started = time.monotonic()
        try:
            with self._lock:
                if backend == "piper":
                    command = [
                        detail["executable"], "--model", detail["model"],
                        "--output_file", str(path),
                    ]
                    if voice:
                        # Piper multi-speaker models accept an integer speaker id.
                        try:
                            command.extend(["--speaker", str(int(voice))])
                        except (TypeError, ValueError):
                            pass
                    subprocess.run(
                        command,
                        input=spoken,
                        text=True,
                        capture_output=True,
                        check=True,
                        timeout=180,
                    )
                elif backend == "espeak":
                    command = [detail["executable"], "-w", str(path), "-s", str(max(80, min(320, int(rate))))]
                    if voice:
                        command.extend(["-v", str(voice)])
                    command.append(spoken)
                    subprocess.run(command, capture_output=True, check=True, timeout=180)
                else:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.setProperty("rate", max(80, min(320, int(rate))))
                    if voice:
                        for candidate in engine.getProperty("voices") or []:
                            if voice.lower() in str(getattr(candidate, "name", "")).lower():
                                engine.setProperty("voice", candidate.id)
                                break
                    engine.save_to_file(spoken, str(path))
                    engine.runAndWait()
                    engine.stop()

            if not path.exists() or path.stat().st_size < 44:
                return {"success": False, "error": f"{backend} did not produce audio"}
            words = max(1, len(spoken.split()))
            return {
                "success": True,
                "audio_id": audio_id,
                "path": str(path),
                "backend": backend,
                "characters": len(spoken),
                "estimated_duration": round(words / max(80, int(rate)) * 60, 2),
                "generation_ms": round((time.monotonic() - started) * 1000),
            }
        except subprocess.TimeoutExpired:
            path.unlink(missing_ok=True)
            return {"success": False, "error": f"{backend} timed out"}
        except Exception as exc:  # noqa: BLE001
            path.unlink(missing_ok=True)
            return {"success": False, "error": f"Speech synthesis failed: {exc}", "backend": backend}

    def audio_path(self, audio_id: str) -> Optional[Path]:
        if not re.fullmatch(r"[a-f0-9]{32}", str(audio_id or "")):
            return None
        path = (self.root / f"{audio_id}.wav").resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return path if path.exists() and path.is_file() else None

    def cleanup(self, max_age_hours: int = 24) -> int:
        cutoff = time.time() - max(1, int(max_age_hours)) * 3600
        removed = 0
        for path in self.root.glob("*.wav"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
        return removed


speech_engine = SpeechEngine()
