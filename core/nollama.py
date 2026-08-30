"""NoLlama — local LLM server for the Intel stack (NPU / Arc iGPU / CPU).

`NoLlama <https://github.com/aweussom/NoLlama>`_ is an Ollama/OpenAI-compatible
server built on OpenVINO.  Hermus uses it for the devices Ollama cannot reach —
above all the Intel NPU — and keeps Ollama for NVIDIA/AMD GPUs and CPU-only
boxes (see :mod:`core.accelerators` for the routing table).

This module owns the whole lifecycle so nothing is left half-configured:

* :meth:`NollamaManager.install`  — fetch the server + build its venv.
  **No model weights**: ``setup.sh`` stays a runtime-only installer and the
  multi-GB download happens later, from the dashboard.
* :meth:`NollamaManager.download_model` — background download with real
  progress and terminal states (``queued`` → ``downloading`` → ``ready`` or
  ``failed``; never a state the UI has to guess about).
* :meth:`NollamaManager.start` / :meth:`stop` — run the server pinned to a
  device, with a port that never collides with the gateway's own 8000.
* :meth:`NollamaManager.status` — one dict for the dashboard: installed,
  running, models on disk, health, and what is still missing.

The default OpenAI port is **8010**, not NoLlama's 8000, because the Hermus
gateway already serves 8000.  The Ollama shim is started with
``--ollama-port 0`` so it never fights a real Ollama on 11434.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import config

REPO_URL = "https://github.com/aweussom/NoLlama"
SERVER_FILE = "nollama.py"

# Download states — a fixed vocabulary, so the UI always knows the terminal
# states and never has to keep polling a "processing" value forever.
STATE_QUEUED = "queued"
STATE_DOWNLOADING = "downloading"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
TERMINAL_STATES = (STATE_READY, STATE_FAILED, STATE_CANCELLED)


@dataclass
class ModelSpec:
    """One downloadable OpenVINO model in the Hermus catalog."""

    id: str
    name: str
    repo: str
    roles: tuple[str, ...]
    devices: tuple[str, ...]
    est_size_gb: float
    notes: str = ""
    source: str = "pre-exported"
    trust_remote_code: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "repo": self.repo,
            "roles": list(self.roles),
            "devices": list(self.devices),
            "est_size_gb": self.est_size_gb,
            "notes": self.notes,
            "source": self.source,
            "trust_remote_code": self.trust_remote_code,
        }


#: Curated catalog.  Sizes/repos verified against the upstream NoLlama
#: ``models.json`` and the Hugging Face file listings.
MODEL_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="minicpm",
        name="MiniCPM5 1B (INT4 g128) — Hermus doctor",
        repo="HarmenWessels/MiniCPM5-1B-int4-g128-ov",
        roles=("doctor", "background"),
        devices=("GPU", "CPU"),
        est_size_gb=0.8,
        notes=(
            "The small model Hermus uses to triage and repair itself. OpenVINO IR "
            "(openvino_model.bin 755 MB), Apache-2.0, 128k context, no-think template. "
            "INT4 group-128 — the Intel NPU needs channel-wise INT4, so run this on "
            "GPU/CPU, not NPU."
        ),
    ),
    ModelSpec(
        id="npu-chat",
        name="Qwen3 8B (INT4-CW) — NPU chat",
        repo="OpenVINO/Qwen3-8B-int4-cw-ov",
        roles=("reasoning", "background"),
        devices=("NPU", "GPU"),
        est_size_gb=5.0,
        notes="Best quality verified on the Intel NPU (NoLlama's recommended NPU pick).",
    ),
    ModelSpec(
        id="npu-fast",
        name="LFM2.5 1.2B Instruct (INT4-CW) — NPU background",
        repo="aweussom/LFM2.5-1.2B-Instruct-int4-cw-ov",
        roles=("background", "doctor"),
        devices=("NPU",),
        est_size_gb=1.0,
        notes="Fastest model verified on an NPU (~38 tok/s on a Core Ultra 9 285K). NPU-only build.",
    ),
    ModelSpec(
        id="smollm3",
        name="SmolLM3 3B (INT4-CW)",
        repo="aweussom/SmolLM3-3B-int4-cw-ov",
        roles=("background", "doctor", "reasoning"),
        devices=("NPU", "GPU", "CPU"),
        est_size_gb=2.0,
        notes="Runs on every device class — good default background model on an NPU box.",
    ),
    ModelSpec(
        id="canary",
        name="DeepSeek R1 Distill Qwen 1.5B (INT4-CW) — canary",
        repo="OpenVINO/DeepSeek-R1-Distill-Qwen-1.5B-int4-cw-ov",
        roles=("background",),
        devices=("NPU", "GPU", "CPU"),
        est_size_gb=1.0,
        notes="Known-good ~1 GB canary. Output quality is poor by design; use it to prove the stack loads.",
    ),
    ModelSpec(
        id="vision",
        name="Qwen3-VL 8B (INT8) — GPU vision",
        repo="OpenVINO/Qwen3-VL-8B-Instruct-int8-ov",
        roles=("vision",),
        devices=("GPU",),
        est_size_gb=9.0,
        notes="Verified GPU vision pairing; INT8 keeps OCR/small-number detail. Drop to the INT4 build if VRAM is tight.",
    ),
    ModelSpec(
        id="minicpm-vision",
        name="MiniCPM-V 2.6 (INT4) — community OpenVINO export",
        repo="yangsu0423/MiniCPM-V-2_6-ov-int4",
        roles=("vision",),
        devices=("GPU",),
        est_size_gb=4.5,
        notes="Community OpenVINO export (not Intel-published). Apache-2.0. Prefer the Qwen3-VL build unless you specifically want MiniCPM-V.",
    ),
    ModelSpec(
        id="whisper-base",
        name="Whisper Base (INT8) — background transcription",
        repo="OpenVINO/whisper-base-int8-ov",
        roles=("background",),
        devices=("NPU", "GPU", "CPU"),
        est_size_gb=0.2,
        notes="Small, quick download. Lets the NPU own voice transcription while the GPU generates.",
    ),
)

CATALOG_BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODEL_CATALOG}

# What ``setup.sh`` must NOT do: pull weights. The dashboard does it on demand.
DEFAULT_MODEL_ID = "minicpm"

# Speech-to-text on the local engine. NoLlama advertises Whisper transcription
# (docs/API.md) and serves the OpenAI-compatible audio route; a build without it
# answers 404/405 and the caller falls back to faster-whisper on the CPU.
WHISPER_MODEL_ID = "whisper-base"
TRANSCRIPTION_PATH = "/v1/audio/transcriptions"
ENGINE_LABEL = "nollama"


@dataclass
class DownloadJob:
    """One model download with observable progress."""

    id: str
    model_id: str
    repo: str
    state: str = STATE_QUEUED
    progress: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    path: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "repo": self.repo,
            "state": self.state,
            "progress": round(self.progress, 4),
            "percent": round(self.progress * 100, 1),
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "path": self.path,
            "error": self.error,
            "started": self.started,
            "finished": self.finished,
            "terminal": self.state in TERMINAL_STATES,
        }


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


class NollamaManager:
    """Install / serve / stop NoLlama and manage its model downloads."""

    def __init__(self, home: Optional[str] = None, models_dir: Optional[str] = None) -> None:
        self.home = Path(
            home
            or getattr(config, "nollama_dir", "")
            or (Path.home() / ".hermus" / "nollama")
        ).expanduser()
        self.models_dir = Path(
            models_dir
            or getattr(config, "nollama_models_dir", "")
            or (Path.home() / "models")
        ).expanduser()
        self.port = int(getattr(config, "nollama_port", 8010) or 8010)
        # State lives beside the server it describes unless the user pinned a
        # path: two installs (or a test manager) must not read each other's pid.
        pinned_state = os.getenv("HERMUS_NOLLAMA_STATE")
        if pinned_state:
            self.state_path = config.resolve_path(pinned_state)
        else:
            self.state_path = self.home / "state.json"
        pinned_log = os.getenv("HERMUS_NOLLAMA_LOG")
        self.log_path = config.resolve_path(pinned_log) if pinned_log else (self.home / "nollama.log")
        self._lock = threading.RLock()
        self._downloads: dict[str, DownloadJob] = {}
        self._proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------ paths
    @property
    def server_path(self) -> Path:
        return self.home / SERVER_FILE

    @property
    def venv_python(self) -> Path:
        if os.name == "nt":  # pragma: no cover - Windows layout
            return self.home / "venv" / "Scripts" / "python.exe"
        return self.home / "venv" / "bin" / "python"

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}/v1"

    def model_dir(self, spec: ModelSpec) -> Path:
        """Where a catalog model lands — NoLlama names models by directory."""
        return self.models_dir / spec.repo.split("/")[-1]

    # ------------------------------------------------------------------ install
    def installed(self) -> bool:
        return self.server_path.exists()

    def venv_ready(self) -> bool:
        return self.venv_python.exists()

    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - missing/corrupt state is "no state"
            return {}

    def _save_state(self, **updates: Any) -> dict[str, Any]:
        data = self._state()
        data.update(updates)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        return data

    def install(self, *, python_exe: Optional[str] = None, timeout: int = 900) -> dict[str, Any]:
        """Fetch the NoLlama server and build its venv — **without** model weights.

        Returns a terminal result dict; every failure mode is reported with the
        command that failed so the dashboard can show a real reason.
        """
        with self._lock:
            steps: list[dict[str, Any]] = []
            self.home.mkdir(parents=True, exist_ok=True)

            if not self.installed():
                git = _which("git")
                if not git:
                    return {
                        "success": False,
                        "stage": "clone",
                        "error": "git is required to fetch NoLlama",
                        "hint": "install git, or download the release ZIP from https://github.com/aweussom/NoLlama/releases/latest",
                        "steps": steps,
                    }
                target = self.home.parent / f"{self.home.name}.tmp"
                if target.exists():
                    _rmtree(target)
                code, out = _shell(
                    [git, "clone", "--depth", "1", REPO_URL, str(target)], timeout=300
                )
                if code != 0 or not (target / SERVER_FILE).exists():
                    return {
                        "success": False,
                        "stage": "clone",
                        "error": (out or "git clone failed")[-800:],
                        "hint": f"check network access to {REPO_URL}",
                        "steps": steps,
                    }
                for item in target.iterdir():
                    item.replace(self.home / item.name)
                _rmtree(target)
                steps.append({"step": "clone", "ok": True, "detail": REPO_URL})
            else:
                steps.append({"step": "clone", "ok": True, "detail": "already present"})

            py = python_exe or sys.executable
            if not self.venv_ready():
                code, out = _shell([py, "-m", "venv", str(self.home / "venv")], timeout=300)
                # Trust the interpreter on disk, not the exit code: a venv whose
                # python is missing would otherwise be reported as installed and
                # every later start would fail with a confusing "no such file".
                if code != 0 or not self.venv_python.exists():
                    return {
                        "success": False,
                        "stage": "venv",
                        "error": (out or "python -m venv failed")[-800:]
                        or f"venv created but {self.venv_python} is missing",
                        "hint": "install python3-venv (Debian/Ubuntu) or python3-virtualenv",
                        "steps": steps,
                    }
                steps.append({"step": "venv", "ok": True, "detail": str(self.venv_python)})

                requirements = self.home / "requirements.txt"
                pip = [str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"]
                _shell(pip, timeout=300)
                pkgs = ["openvino-genai", "fastapi", "uvicorn[standard]", "openai", "huggingface_hub"]
                if requirements.exists():
                    pkgs = ["-r", str(requirements)]
                code, out = _shell(
                    [str(self.venv_python), "-m", "pip", "install", *pkgs], timeout=timeout
                )
                if code != 0:
                    return {
                        "success": False,
                        "stage": "pip",
                        "error": (out or "pip install failed")[-1200:],
                        "hint": "openvino-genai needs Python 3.10+; on Linux the NPU also needs intel-npu-driver + intel-npu-compiler",
                        "steps": steps,
                    }
                steps.append({"step": "pip", "ok": True, "detail": "openvino-genai + fastapi"})

            self._save_state(installed=True, installed_at=time.time(), home=str(self.home))
            return {
                "success": True,
                "stage": "done",
                "home": str(self.home),
                "python": str(self.venv_python),
                "port": self.port,
                "models_downloaded": False,
                "next": "download a model from the dashboard (System Overview → Local AI Engine)",
                "steps": steps,
            }

    # ------------------------------------------------------------------ models
    def list_catalog(self) -> list[dict[str, Any]]:
        """Catalog merged with what is already on disk."""
        out = []
        for spec in MODEL_CATALOG:
            entry = spec.to_dict()
            path = self.model_dir(spec)
            ready = model_dir_ready(path)
            entry.update(
                {
                    "installed": ready,
                    "path": str(path),
                    "size_mb": round(_dir_size(path) / (1024 * 1024), 1) if path.exists() else 0.0,
                }
            )
            out.append(entry)
        return out

    def get_spec(self, model_id: str) -> Optional[ModelSpec]:
        return CATALOG_BY_ID.get(str(model_id or "").strip().lower())

    def installed_models(self) -> list[dict[str, Any]]:
        """Every OpenVINO IR directory actually present under the models dir."""
        found: list[dict[str, Any]] = []
        try:
            candidates = sorted(p for p in self.models_dir.iterdir() if p.is_dir())
        except OSError:
            return found
        catalog_by_name = {spec.repo.split("/")[-1]: spec for spec in MODEL_CATALOG}
        for path in candidates:
            xml = path / "openvino_model.xml"
            if not xml.exists():
                continue
            spec = catalog_by_name.get(path.name)
            found.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "model_id": spec.id if spec else "",
                    "size_mb": round(_dir_size(path) / (1024 * 1024), 1),
                    "complete": model_dir_ready(path),
                }
            )
        return found

    def recommended_model(self, plan_dict: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """The one model this machine is missing that unlocks the routed plan."""
        from .accelerators import ENGINE_NOLLAMA

        plan_dict = plan_dict or {}
        roles = plan_dict.get("roles") or {}
        wanted: list[str] = []
        for role in ("doctor", "background", "reasoning", "vision"):
            assignment = roles.get(role) or {}
            if assignment.get("engine") != ENGINE_NOLLAMA:
                continue
            for spec in MODEL_CATALOG:
                if role in spec.roles and assignment.get("device") in spec.devices:
                    if spec.id not in wanted:
                        wanted.append(spec.id)
        if not wanted:
            wanted = [DEFAULT_MODEL_ID]
        for model_id in wanted:
            spec = CATALOG_BY_ID.get(model_id)
            if spec and not model_dir_ready(self.model_dir(spec)):
                return spec.to_dict()
        return None

    # ---------------------------------------------------------------- downloads
    def downloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.to_dict() for job in self._downloads.values()]

    def download_status(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._downloads.get(job_id)
            return job.to_dict() if job else None

    def download_model(self, model_id: str, *, force: bool = False) -> dict[str, Any]:
        """Start a background download; returns immediately with the job state.

        Idempotent: an in-flight download of the same model returns the running
        job instead of starting a second one, and an already-complete model
        reports ``ready`` without re-downloading.
        """
        spec = self.get_spec(model_id)
        if spec is None:
            return {
                "success": False,
                "error": f"unknown model '{model_id}'",
                "available": [m.id for m in MODEL_CATALOG],
            }
        target = self.model_dir(spec)
        with self._lock:
            for job in self._downloads.values():
                if job.model_id == spec.id and job.state not in TERMINAL_STATES:
                    return {"success": True, "started": False, "job": job.to_dict(),
                            "detail": "download already in progress"}
            if model_dir_ready(target) and not force:
                job = DownloadJob(
                    id=f"{spec.id}-{int(time.time())}",
                    model_id=spec.id,
                    repo=spec.repo,
                    state=STATE_READY,
                    progress=1.0,
                    path=str(target),
                    finished=time.time(),
                )
                self._downloads[job.id] = job
                return {"success": True, "started": False, "job": job.to_dict(),
                        "detail": "already on disk"}
            job = DownloadJob(
                id=f"{spec.id}-{int(time.time())}",
                model_id=spec.id,
                repo=spec.repo,
                state=STATE_QUEUED,
                bytes_total=int(spec.est_size_gb * 1024 * 1024 * 1024),
                path=str(target),
            )
            self._downloads[job.id] = job
        thread = threading.Thread(
            target=self._download_worker, args=(job.id, spec), name=f"dl-{spec.id}", daemon=True
        )
        thread.start()
        return {"success": True, "started": True, "job": job.to_dict()}

    def cancel_download(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._downloads.get(job_id)
            if job is None:
                return {"cancelled": False, "error": f"unknown job '{job_id}'"}
            if job.state in TERMINAL_STATES:
                return {"cancelled": False, "state": job.state, "reason": "already terminal"}
            job.state = STATE_CANCELLED
            job.finished = time.time()
            return {"cancelled": True, "job": job.to_dict()}

    def _download_worker(self, job_id: str, spec: ModelSpec) -> None:
        """Do the actual download off the request path, tracking real progress."""
        job = self._downloads[job_id]
        target = self.model_dir(spec)
        job.state = STATE_DOWNLOADING
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                from huggingface_hub import snapshot_download  # type: ignore
            except ImportError:
                job.state = STATE_FAILED
                job.error = "huggingface_hub is not installed (pip install huggingface_hub)"
                job.finished = time.time()
                return

            token = os.getenv("HF_TOKEN") or getattr(config, "hf_token", None) or None
            kwargs: dict[str, Any] = {
                "repo_id": spec.repo,
                "local_dir": str(target),
                "max_workers": 4,
            }
            if token:
                kwargs["token"] = token
            if spec.trust_remote_code:
                kwargs["trust_remote_code"] = True

            stop = threading.Event()
            watcher = threading.Thread(
                target=self._watch_progress, args=(job, target, stop), daemon=True
            )
            watcher.start()
            try:
                snapshot_download(**kwargs)
            finally:
                stop.set()
                watcher.join(timeout=5)

            if job.state == STATE_CANCELLED:
                return
            if model_dir_ready(target):
                job.state = STATE_READY
                job.progress = 1.0
                job.bytes_done = _dir_size(target)
                job.bytes_total = job.bytes_done or job.bytes_total
            else:
                job.state = STATE_FAILED
                job.error = (
                    "download finished but openvino_model.bin/.xml are missing or truncated "
                    "in " + str(target)
                )
            job.finished = time.time()
        except Exception as exc:  # noqa: BLE001 - report, never crash the gateway
            job.state = STATE_FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:600]
            job.finished = time.time()

    def _watch_progress(self, job: DownloadJob, target: Path, stop: threading.Event) -> None:
        """Poll the target directory so the dashboard can show a real percentage."""
        total = job.bytes_total or 1
        while not stop.wait(1.0):
            if job.state in TERMINAL_STATES:
                return
            done = _dir_size(target)
            job.bytes_done = done
            job.progress = max(job.progress, min(0.99, done / total)) if total else 0.0

    # ------------------------------------------------------------------ serve
    def running(self) -> bool:
        """Is something answering on the NoLlama port?"""
        return _port_open(self.port)

    def health(self, timeout: float = 3.0) -> dict[str, Any]:
        """``GET /health`` from the server (device, models, readiness)."""
        import requests

        try:
            resp = requests.get(f"http://localhost:{self.port}/health", timeout=timeout)
            if resp.status_code == 200:
                data = resp.json() if resp.content else {}
                return {"ok": True, "detail": data}
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}

    # ------------------------------------------------------- speech-to-text
    # The NPU's job is the continuous background work: Whisper voice commands
    # and indexing passes.  NoLlama serves an OpenVINO Whisper model on the
    # OpenAI-standard audio path, so Hermus posts the microphone clip there and
    # keeps faster-whisper (CPU) as the fallback.
    def whisper_model(self) -> Optional[dict[str, Any]]:
        """Installed Whisper IR, if the user downloaded one from the dashboard."""
        whisper_names = {spec.repo.split("/")[-1] for spec in MODEL_CATALOG if "background" in spec.roles and "whisper" in spec.repo.lower()}
        for entry in self.installed_models():
            if entry.get("name") in whisper_names and entry.get("complete"):
                spec = CATALOG_BY_ID.get(entry.get("model_id") or "")
                return {**entry, "repo": spec.repo if spec else "", "api_name": entry.get("name")}
        return None

    def transcribe(
        self,
        audio_path: str,
        *,
        language: Optional[str] = None,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Transcribe one audio file on the local engine.

        Returns ``{"success": False, "error": ...}`` for every non-happy path
        so callers can fall back to the CPU backend instead of surfacing an
        exception to the user.
        """
        import requests

        path = Path(audio_path)
        if not path.exists():
            return {"success": False, "error": f"audio file not found: {audio_path}"}
        if not self.running():
            return {"success": False, "error": "NoLlama is not running", "action": "start"}
        model = self.whisper_model()
        if not model:
            return {
                "success": False,
                "error": "no Whisper model downloaded for the local engine",
                "action": "download_model",
                "model_id": WHISPER_MODEL_ID,
            }

        try:
            with open(path, "rb") as handle:
                resp = requests.post(
                    f"http://localhost:{self.port}{TRANSCRIPTION_PATH}",
                    files={"file": (path.name, handle)},
                    data={"model": model["api_name"], **({"language": language} if language else {})},
                    timeout=timeout,
                )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

        if resp.status_code in (404, 405):
            # An older NoLlama build without the audio route: fall back, and say why.
            return {"success": False, "error": f"engine has no transcription route (HTTP {resp.status_code})"}
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:160]}"}
        try:
            text = str((resp.json() or {}).get("text") or "").strip()
        except ValueError:
            text = (resp.text or "").strip()
        if not text:
            return {"success": False, "error": "engine returned an empty transcription"}
        return {
            "success": True,
            "backend": ENGINE_LABEL,
            "engine": ENGINE_LABEL,
            "model": model["api_name"],
            "device": resp.headers.get("X-Device") or None,
            "text": text,
            "segments": [],
            "duration": None,
        }

    def best_installed_model(self, device: str = "", roles: Optional[tuple[str, ...]] = None) -> Optional[dict[str, Any]]:
        """Pick an *already-downloaded* catalog model for a device/role.

        Used by ``start`` (so an engine actually serves the model a user
        downloaded, even a custom OpenVINO export like MiniCPM) and by the
        hardware router (so a CPU-only box with MiniCPM does not silently fall
        back to ``ollama/llama3.1:8b``).
        """
        device = (device or "AUTO").upper()
        wanted_roles = set(roles or ())
        best: Optional[dict[str, Any]] = None
        best_rank = 10_000
        for row in self.list_catalog():
            if not row.get("installed"):
                continue
            spec = CATALOG_BY_ID.get(row["id"])
            if spec is None:
                continue
            devices = {str(d).upper() for d in spec.devices}
            role_perf = {r: i for i, r in enumerate(("doctor", "background", "reasoning", "vision"))}
            if device not in ("AUTO", "CPU", "GPU", "NPU"):
                continue
            if device != "AUTO" and device not in devices:
                continue
            if wanted_roles and not (wanted_roles & set(spec.roles)):
                continue
            rank = 0
            # Prefer the model a given role wants most (e.g. MiniCPM for doctor).
            if "doctor" in wanted_roles and "doctor" in spec.roles:
                rank = 0
            elif "background" in wanted_roles and "background" in spec.roles:
                rank = 1
            elif "reasoning" in wanted_roles and "reasoning" in spec.roles:
                rank = 2
            else:
                rank = role_perf.get(next(iter(spec.roles or ()), ""), 10)
            # Always prefer the small, locally-drivable model (MiniCPM/SmolLM)
            # over a much larger build for the doctor/background roles.
            if "doctor" in spec.roles or "background" in spec.roles:
                rank -= 1
            if best is None or rank < best_rank:
                best = row
                best_rank = rank
        return best

    def start(
        self,
        *,
        device: str = "",
        model_dir: Optional[str] = None,
        gpu_model_dir: Optional[str] = None,
        port: Optional[int] = None,
        extra_args: Optional[list[str]] = None,
        idle_timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Launch the server pinned to a device (defaults to auto-detect).

        If ``model_dir``/``gpu_model_dir`` are omitted, Hermus auto-resolves
        them from the installed model catalog. This is what makes a downloaded
        MiniCPM/OpenVINO export usable without also knowing NoLlama's internal
        registry — the server is pointed at the directory Hermus downloaded.
        """
        if not self.installed():
            return {"success": False, "error": "NoLlama is not installed", "action": "install"}
        if not self.venv_ready():
            return {"success": False, "error": "NoLlama venv is missing", "action": "install"}
        resolved: dict[str, Any] = {}
        if not model_dir:
            row = self.best_installed_model(device=device, roles=("doctor", "background", "reasoning"))
            if row:
                model_dir = str(row["path"])
                resolved["model_dir"] = model_dir
                resolved["model_id"] = row["id"]
        if not gpu_model_dir:
            row = self.best_installed_model(device="GPU", roles=("doctor", "background", "reasoning", "vision"))
            if row:
                candidate = str(row["path"])
                if candidate != model_dir:
                    gpu_model_dir = candidate
                    resolved["gpu_model_dir"] = gpu_model_dir
                    resolved["gpu_model_id"] = row["id"]
        if resolved:
            self._save_state(**resolved)
        port = int(port or self.port)
        if port == int(getattr(config, "gateway_port", 8000) or 8000):
            # The gateway owns 8000; starting NoLlama there would shadow it.
            port = self.port = int(getattr(config, "nollama_port", 8010) or 8010)

        cmd: list[str] = [str(self.venv_python), str(self.server_path), "--port", str(port)]
        if device:
            cmd += ["--device", device.upper()]
        if model_dir:
            cmd += ["--model-dir", str(model_dir)]
        if gpu_model_dir:
            cmd += ["--gpu-model-dir", str(gpu_model_dir)]
        # Never fight a real Ollama for 11434, and keep models resident for agent use.
        cmd += ["--ollama-port", "0"]
        cmd += ["--idle-timeout", str(int(idle_timeout if idle_timeout is not None else 0))]
        cmd += list(extra_args or [])

        log_path = self.log_path
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "ab")
        except OSError as exc:
            return {"success": False, "error": f"cannot open log file: {exc}"}

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.home),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"failed to launch: {exc}", "cmd": " ".join(map(shlex.quote, cmd))}

        self._save_state(
            running=True,
            pid=self._proc.pid,
            port=port,
            device=device.upper() or "AUTO",
            cmd=" ".join(map(shlex.quote, cmd)),
            started_at=time.time(),
            log=str(log_path),
        )
        return {
            "success": True,
            "pid": self._proc.pid,
            "port": port,
            "device": device.upper() or "AUTO",
            "cmd": " ".join(map(shlex.quote, cmd)),
            "log": str(log_path),
            "model_dir": model_dir,
            "gpu_model_dir": gpu_model_dir,
            "model_id": resolved.get("model_id") or resolved.get("gpu_model_id"),
            "note": "model load takes 30-60s; poll /engine/status until it reports ready",
        }

    def stop(self) -> dict[str, Any]:
        """Stop a server this manager started (no-op if we did not start one)."""
        state = self._state()
        pid = int(state.get("pid") or 0)
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":  # pragma: no cover
                    proc.terminate()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self._save_state(running=False, pid=None)
            return {"stopped": True, "pid": pid}
        if pid and not _port_open(self.port):
            self._save_state(running=False, pid=None)
        return {"stopped": False, "pid": pid or None}

    def stop_if_managed(self) -> dict[str, Any]:
        """Gateway-shutdown hook: stop only what we launched."""
        return self.stop()

    # ----------------------------------------------------------------- status
    def status(self, *, probe: bool = True) -> dict[str, Any]:
        """Everything the dashboard needs in one call."""
        models = self.installed_models()
        state = self._state()
        running = self.running()
        out: dict[str, Any] = {
            "installed": self.installed(),
            "venv_ready": self.venv_ready(),
            "running": running,
            "port": self.port,
            "base_url": self.base_url,
            "home": str(self.home),
            "models_dir": str(self.models_dir),
            "repo_url": REPO_URL,
            "models": models,
            "model_count": len(models),
            "catalog": self.list_catalog(),
            "downloads": self.downloads(),
            "pid": state.get("pid"),
            "device": state.get("device"),
            "log": state.get("log"),
        }
        if probe and running:
            out["health"] = self.health()
        return out


# ---------------------------------------------------------------------------
def model_dir_ready(path: Path) -> bool:
    """NoLlama's own sanity check: the IR pair must exist and the .bin must be
    at least as large as the .xml declares (catches interrupted downloads)."""
    xml = path / "openvino_model.xml"
    bin_ = path / "openvino_model.bin"
    if not (xml.exists() and bin_.exists()):
        return False
    try:
        declared = _declared_weights_size(xml)
    except Exception:  # noqa: BLE001 - unreadable XML → fall back to "present"
        declared = 0
    actual = bin_.stat().st_size
    if declared and actual < declared:
        return False
    return actual > 0


def _declared_weights_size(xml: Path) -> int:
    """Read the external-data size from the IR header without parsing all of it."""
    with open(xml, "r", encoding="utf-8", errors="ignore") as handle:
        head = handle.read(65536)
    marker = 'offset="0" size="'
    idx = head.find(marker)
    if idx < 0:
        return 0
    start = idx + len(marker)
    end = head.find('"', start)
    if end < 0:
        return 0
    try:
        return int(head[start:end])
    except ValueError:
        return 0


def _which(name: str) -> str:
    import shutil

    return shutil.which(name) or ""


def _shell(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command, returning (code, combined output). Never raises."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(Path.cwd())
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _rmtree(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


nollama_manager = NollamaManager()
