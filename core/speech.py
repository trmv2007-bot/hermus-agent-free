"""Local speech synthesis for Hermus Talking Mode.

The speech subsystem keeps one canonical owner for spoken output while allowing
optional backends to come and go honestly.

Backends discovered in order:

1. OmniVoice for advanced multilingual TTS / voice cloning / voice design when
   explicitly requested or when no lighter local backend is available.
2. Piper (neural TTS) when ``piper`` and ``HERMUS_PIPER_MODEL`` are available.
3. ``espeak-ng`` / ``espeak`` for a lightweight offline fallback.
4. ``pyttsx3`` when the optional Python package is installed.

Generated audio is stored under ``data/speech`` and can be served by the
FastAPI gateway. Missing TTS software is reported as a normal unavailable state
rather than breaking chat or the dashboard.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import sys
from ctypes.util import find_library
from datetime import datetime, timezone
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpeechEngine:
    """Discover and invoke local speech backends."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else speech_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._omnivoice_cache: dict[str, Any] = {}

    # ---------------------------------------------------------------- prompts
    def prompt_root(self) -> Path:
        root = config.resolve_path(getattr(config, "omnivoice_prompt_dir", "data/speech/prompts"))
        root.mkdir(parents=True, exist_ok=True)
        return root

    def list_clone_prompts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for meta in sorted(self.prompt_root().glob("*.json"), reverse=True):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                data.setdefault("metadata_path", str(meta))
                out.append(data)
            except Exception:
                continue
        return out

    def create_clone_prompt(
        self,
        ref_audio: str,
        *,
        ref_text: Optional[str] = None,
        prompt_id: Optional[str] = None,
        label: str = "",
    ) -> dict[str, Any]:
        path = self._resolve_input_file(ref_audio)
        if path is None:
            return {"success": False, "backend": "omnivoice", "error": f"reference audio not found: {ref_audio}"}
        runtime = self._load_omnivoice_runtime()
        if runtime.get("error"):
            return {"success": False, "backend": "omnivoice", "error": runtime["error"], "status": self.status()}
        model = runtime["model"]
        with self._lock:
            prompt = model.create_voice_clone_prompt(ref_audio=str(path), ref_text=ref_text)
        pid = _safe_name(prompt_id or uuid.uuid4().hex[:12], "prompt")
        prompt_path = self.prompt_root() / f"{pid}.pt"
        meta_path = self.prompt_root() / f"{pid}.json"
        prompt.save(str(prompt_path))
        meta = {
            "prompt_id": pid,
            "label": label or path.stem,
            "backend": "omnivoice",
            "ref_audio": str(path),
            "ref_text": ref_text or "",
            "prompt_path": str(prompt_path),
            "created_at": _now_iso(),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {"success": True, "backend": "omnivoice", **meta, "metadata_path": str(meta_path)}

    # -------------------------------------------------------------- capability
    def _basic_backends(self) -> dict[str, dict[str, Any]]:
        requested = os.getenv("HERMUS_TTS_BACKEND", "auto").strip().lower()
        piper = shutil.which("piper")
        piper_model = os.getenv("HERMUS_PIPER_MODEL", "").strip()
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        pyttsx3_ok = False
        pyttsx3_reason = None
        try:
            import pyttsx3  # noqa: F401
            pyttsx3_ok = not sys.platform.startswith("linux") or bool(find_library("espeak-ng") or find_library("espeak"))
            if not pyttsx3_ok:
                pyttsx3_reason = "pyttsx3 imported but no native eSpeak library is available"
        except Exception as exc:
            pyttsx3_reason = str(exc)
        return {
            "piper": {
                "available": bool(piper and piper_model and Path(piper_model).expanduser().exists()),
                "requested": requested in ("auto", "piper"),
                "detail": {"executable": piper, "model": piper_model or None},
            },
            "espeak": {
                "available": bool(espeak),
                "requested": requested in ("auto", "espeak", "espeak-ng"),
                "detail": {"executable": espeak},
            },
            "pyttsx3": {
                "available": pyttsx3_ok,
                "requested": requested in ("auto", "pyttsx3"),
                "detail": {"reason": pyttsx3_reason},
            },
        }

    def _omnivoice_status(self) -> dict[str, Any]:
        enabled = bool(getattr(config, "omnivoice_enabled", True))
        package = _module_available("omnivoice")
        soundfile = _module_available("soundfile")
        torch = _module_available("torch")
        prompt_dir = self.prompt_root()
        prompt_count = len(list(prompt_dir.glob("*.pt")))
        return {
            "available": bool(enabled and package and soundfile and torch),
            "enabled": enabled,
            "package": package,
            "soundfile": soundfile,
            "torch": torch,
            "model": getattr(config, "omnivoice_model", "k2-fsa/OmniVoice"),
            "device": getattr(config, "omnivoice_device", "auto"),
            "prompt_dir": str(prompt_dir),
            "prompt_count": prompt_count,
            "features": ["multilingual_tts", "voice_cloning", "voice_design", "prompt_cache"],
            "reason": None if (enabled and package and soundfile and torch) else (
                "Install optional dependencies for OmniVoice (omnivoice, torch, soundfile)"
                if enabled else "OmniVoice backend disabled by configuration"
            ),
        }

    def _discover(self, *, requested: Optional[str] = None, needs_advanced: bool = False) -> tuple[Optional[str], dict[str, Any]]:
        requested = (requested or os.getenv("HERMUS_TTS_BACKEND", "auto") or "auto").strip().lower()
        requested = "espeak" if requested == "espeak-ng" else requested
        omni = self._omnivoice_status()
        basic = self._basic_backends()

        if requested == "omnivoice":
            return ("omnivoice", omni) if omni["available"] else (None, omni)
        if needs_advanced and omni["available"]:
            return "omnivoice", omni
        for name in ("piper", "espeak", "pyttsx3"):
            row = basic[name]
            if requested not in ("auto", name):
                continue
            if row["available"]:
                return name, row["detail"]
        if omni["available"]:
            return "omnivoice", omni
        detail = {
            "requested": requested,
            "basic": basic,
            "omnivoice": omni,
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
            "backends": {**self._basic_backends(), "omnivoice": self._omnivoice_status()},
            "clone_prompts": {"count": len(self.list_clone_prompts()), "directory": str(self.prompt_root())},
            "setup": (
                "Install optional OmniVoice dependencies for multilingual voice cloning/design, or set "
                "HERMUS_PIPER_MODEL and install piper, or install espeak-ng. Optional fallback: pyttsx3 "
                "with a supported OS speech driver."
            ) if not backend else None,
        }

    # ---------------------------------------------------------------- synth
    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: int = 165,
        *,
        backend: Optional[str] = None,
        language: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        instruct: Optional[str] = None,
        duration: Optional[float] = None,
        speed: Optional[float] = None,
        prompt_id: Optional[str] = None,
        create_prompt_id: Optional[str] = None,
        normalize_text: bool = False,
    ) -> dict[str, Any]:
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

        advanced = any((backend == "omnivoice", language, ref_audio, ref_text, instruct, duration, speed, prompt_id, create_prompt_id, normalize_text))
        if str(backend or "").strip().lower() == "omnivoice":
            return self._synthesize_omnivoice(
                spoken,
                language=language,
                ref_audio=ref_audio,
                ref_text=ref_text,
                instruct=instruct,
                duration=duration,
                speed=speed,
                prompt_id=prompt_id,
                create_prompt_id=create_prompt_id,
                normalize_text=normalize_text,
                rate=rate,
            )
        selected_backend, detail = self._discover(requested=backend, needs_advanced=advanced)
        if selected_backend == "omnivoice":
            return self._synthesize_omnivoice(
                spoken,
                language=language,
                ref_audio=ref_audio,
                ref_text=ref_text,
                instruct=instruct,
                duration=duration,
                speed=speed,
                prompt_id=prompt_id,
                create_prompt_id=create_prompt_id,
                normalize_text=normalize_text,
                rate=rate,
            )
        if not selected_backend:
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
                if selected_backend == "piper":
                    command = [
                        detail["executable"], "--model", detail["model"],
                        "--output_file", str(path),
                    ]
                    if voice:
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
                elif selected_backend == "espeak":
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
                return {"success": False, "error": f"{selected_backend} did not produce audio"}
            words = max(1, len(spoken.split()))
            return {
                "success": True,
                "audio_id": audio_id,
                "path": str(path),
                "backend": selected_backend,
                "characters": len(spoken),
                "estimated_duration": round(words / max(80, int(rate)) * 60, 2),
                "generation_ms": round((time.monotonic() - started) * 1000),
            }
        except subprocess.TimeoutExpired:
            path.unlink(missing_ok=True)
            return {"success": False, "error": f"{selected_backend} timed out"}
        except Exception as exc:  # noqa: BLE001
            path.unlink(missing_ok=True)
            return {"success": False, "error": f"Speech synthesis failed: {exc}", "backend": selected_backend}

    def _synthesize_omnivoice(
        self,
        text: str,
        *,
        language: Optional[str],
        ref_audio: Optional[str],
        ref_text: Optional[str],
        instruct: Optional[str],
        duration: Optional[float],
        speed: Optional[float],
        prompt_id: Optional[str],
        create_prompt_id: Optional[str],
        normalize_text: bool,
        rate: int,
    ) -> dict[str, Any]:
        runtime = self._load_omnivoice_runtime()
        if runtime.get("error"):
            return {"success": False, "backend": "omnivoice", "error": runtime["error"], "status": self.status()}
        model = runtime["model"]
        sf = runtime["soundfile"]
        VoiceClonePrompt = runtime["VoiceClonePrompt"]

        loaded_prompt = None
        prompt_meta = None
        if create_prompt_id and ref_audio:
            created = self.create_clone_prompt(ref_audio, ref_text=ref_text, prompt_id=create_prompt_id)
            if not created.get("success"):
                return created
            prompt_id = created.get("prompt_id")
        if prompt_id:
            loaded_prompt, prompt_meta = self._load_clone_prompt(prompt_id, VoiceClonePrompt)
            if loaded_prompt is None:
                return {"success": False, "backend": "omnivoice", "error": f"clone prompt not found: {prompt_id}"}

        gen_kw: dict[str, Any] = {"text": text}
        if language:
            gen_kw["language"] = language
        if instruct:
            gen_kw["instruct"] = instruct
        if duration not in (None, ""):
            gen_kw["duration"] = float(duration)
        if speed not in (None, ""):
            gen_kw["speed"] = float(speed)
        elif rate and rate != 165:
            gen_kw["speed"] = max(0.5, min(2.0, round(float(rate) / 165.0, 3)))
        if normalize_text:
            gen_kw["normalize_text"] = True
        if loaded_prompt is not None:
            gen_kw["voice_clone_prompt"] = loaded_prompt
        elif ref_audio:
            ref_path = self._resolve_input_file(ref_audio)
            if ref_path is None:
                return {"success": False, "backend": "omnivoice", "error": f"reference audio not found: {ref_audio}"}
            gen_kw["ref_audio"] = str(ref_path)
            if ref_text:
                gen_kw["ref_text"] = ref_text

        audio_id = uuid.uuid4().hex
        path = self.root / f"{audio_id}.wav"
        started = time.monotonic()
        try:
            with self._lock:
                audio = model.generate(**gen_kw)
            if not audio or not hasattr(audio[0], "__len__") or len(audio[0]) <= 0:
                return {"success": False, "backend": "omnivoice", "error": "OmniVoice returned empty audio"}
            sample_rate = int(getattr(model, "sampling_rate", 24000) or 24000)
            sf.write(str(path), audio[0], sample_rate)
            est = round(len(audio[0]) / sample_rate, 2) if sample_rate and hasattr(audio[0], "__len__") else None
            out = {
                "success": True,
                "audio_id": audio_id,
                "path": str(path),
                "backend": "omnivoice",
                "characters": len(text),
                "estimated_duration": est,
                "generation_ms": round((time.monotonic() - started) * 1000),
                "language": language,
                "voice_design": bool(instruct),
                "voice_clone": bool(loaded_prompt is not None or ref_audio),
            }
            if prompt_id:
                out["prompt_id"] = prompt_id
            if prompt_meta:
                out["prompt"] = prompt_meta
            return out
        except Exception as exc:  # noqa: BLE001
            path.unlink(missing_ok=True)
            return {"success": False, "backend": "omnivoice", "error": f"OmniVoice synthesis failed: {exc}"}

    # -------------------------------------------------------------- runtime
    def _load_omnivoice_runtime(self) -> dict[str, Any]:
        if not getattr(config, "omnivoice_enabled", True):
            return {"error": "OmniVoice backend disabled by configuration"}
        try:
            import soundfile as sf
            import torch
            from omnivoice import OmniVoice, VoiceClonePrompt
        except Exception as exc:  # noqa: BLE001
            return {"error": f"OmniVoice dependencies unavailable: {type(exc).__name__}: {exc}"}

        device = self._resolve_omnivoice_device(torch)
        dtype = torch.float16 if device != "cpu" else torch.float32
        cache_key = f"{getattr(config, 'omnivoice_model', 'k2-fsa/OmniVoice')}|{device}|{dtype}"
        cached = self._omnivoice_cache.get("runtime")
        if cached and self._omnivoice_cache.get("key") == cache_key:
            return cached
        try:
            model = OmniVoice.from_pretrained(
                getattr(config, "omnivoice_model", "k2-fsa/OmniVoice"),
                device_map=device,
                dtype=dtype,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"OmniVoice model load failed: {type(exc).__name__}: {exc}"}
        runtime = {"model": model, "soundfile": sf, "VoiceClonePrompt": VoiceClonePrompt, "device": device}
        self._omnivoice_cache = {"key": cache_key, "runtime": runtime}
        return runtime

    @staticmethod
    def _resolve_omnivoice_device(torch_mod) -> str:
        requested = str(getattr(config, "omnivoice_device", "auto") or "auto").strip().lower()
        if requested not in ("", "auto"):
            return requested
        try:
            if getattr(torch_mod, "cuda", None) is not None and torch_mod.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        try:
            xpu = getattr(torch_mod, "xpu", None)
            if xpu is not None and xpu.is_available():
                return "xpu"
        except Exception:
            pass
        try:
            mps = getattr(getattr(torch_mod, "backends", None), "mps", None)
            if mps is not None and mps.is_available():
                return "mps"
        except Exception:
            pass
        return "cpu"

    def _load_clone_prompt(self, prompt_id: str, prompt_cls):
        pid = _safe_name(prompt_id, "prompt")
        prompt_path = self.prompt_root() / f"{pid}.pt"
        meta_path = self.prompt_root() / f"{pid}.json"
        if not prompt_path.exists():
            return None, None
        try:
            prompt = prompt_cls.load(str(prompt_path))
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"prompt_id": pid}
            meta.setdefault("prompt_path", str(prompt_path))
            return prompt, meta
        except Exception:
            return None, None

    # ---------------------------------------------------------------- general
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

    @staticmethod
    def _resolve_input_file(value: Optional[str]) -> Optional[Path]:
        text = str(value or "").strip()
        if not text:
            return None
        path = Path(os.path.expanduser(text))
        if not path.is_absolute():
            path = config.resolve_path(text)
        try:
            path = path.resolve()
        except Exception:
            pass
        return path if path.exists() and path.is_file() else None


def _module_available(name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _safe_name(value: str, default: str = "item") -> str:
    text = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or ""))
    text = text.strip("_")
    return text[:120] or default


speech_engine = SpeechEngine()
