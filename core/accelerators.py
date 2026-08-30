"""Accelerator detection + local engine routing (NPU / GPU / CPU).

Hermus can now drive two local engines at once and picks between them from the
hardware it actually finds:

===========================  ==================================================
Detected                     Routing decision
===========================  ==================================================
Intel NPU only               everything → **NoLlama** on the NPU
Intel NPU + GPU              **pipelined**: NPU keeps the long, cheap
                             background work (Whisper, embedding/index passes,
                             the Hermus doctor), GPU takes the heavy
                             generative reasoning
NVIDIA / AMD GPU             reasoning → **Ollama** (llama.cpp's CUDA/ROCm
                             path is the mature one); NoLlama is Intel-only
Intel iGPU / Arc             **NoLlama** on the GPU (OpenVINO INT4 decodes
                             faster than Ollama's Vulkan path, and it is the
                             only local vision path for Intel)
CPU only                     **Ollama** (llama.cpp's CPU backend beats
                             OpenVINO on a strong desktop, per NoLlama's own
                             device table)
===========================  ==================================================

The point of the split is *pipelining*, not fallback: a Core Ultra box has an
NPU that will run a 1-3B model all day at ~20 tok/s while staying cool, and a
GPU that is much faster but hot and hungry.  So the NPU keeps the continuous
background roles and the GPU is reserved for the token-hungry generative ones.

**Nothing is left in a half-decided state.**  :func:`plan` always returns a
concrete engine per role, with the reason it chose it, and :func:`state`
reports one of a fixed set of statuses (``ready`` / ``needs_model`` /
``needs_install`` / ``unavailable`` / ``not_applicable``) — never a dangling
"processing"/"unknown" that a dashboard would spin on forever.

All probes are cheap, cached, individually guarded, and injectable, so the
routing table is testable on a machine with no accelerators at all.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from .config import config

# Fixed status vocabulary — a caller can render these without guessing.
STATUS_READY = "ready"
STATUS_NEEDS_MODEL = "needs_model"
STATUS_NEEDS_INSTALL = "needs_install"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_APPLICABLE = "not_applicable"

# Engine identifiers used across the codebase and the dashboard.
ENGINE_OLLAMA = "ollama"
ENGINE_NOLLAMA = "nollama"
ENGINE_NONE = "none"

# Roles the runtime assigns to an engine. "reasoning" is the heavy generative
# path; the others are the long-running, low-intensity background paths.
ROLE_REASONING = "reasoning"
ROLE_BACKGROUND = "background"
ROLE_DOCTOR = "doctor"
ROLE_VISION = "vision"

ALL_ROLES = (ROLE_REASONING, ROLE_BACKGROUND, ROLE_DOCTOR, ROLE_VISION)

# Modes the router can settle on.
MODE_PIPELINED = "pipelined"
MODE_NPU_ONLY = "npu_only"
MODE_GPU_ONLY = "gpu_only"
MODE_CPU_ONLY = "cpu_only"
MODE_DISABLED = "disabled"


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    """Run a probe command, returning stdout ('' on any failure)."""
    exe = shutil.which(cmd[0])
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, *cmd[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - a probe must never break detection
        return ""
    return (proc.stdout or "") if proc.returncode == 0 else ""


@dataclass
class Device:
    """One detected accelerator."""

    kind: str                      # "npu" | "gpu"
    vendor: str                    # "intel" | "nvidia" | "amd" | "unknown"
    name: str = ""
    detail: str = ""
    memory_mb: Optional[int] = None
    source: str = ""               # which probe found it

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "vendor": self.vendor,
            "name": self.name,
            "detail": self.detail,
            "memory_mb": self.memory_mb,
            "source": self.source,
        }


@dataclass
class HardwareSnapshot:
    """Everything detection found about this machine, plus how it found it."""

    npu: list[Device] = field(default_factory=list)
    gpus: list[Device] = field(default_factory=list)
    cpu_count: int = 0
    ram_mb: int = 0
    openvino_devices: list[str] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    detected_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------- convenience
    @property
    def has_npu(self) -> bool:
        return bool(self.npu)

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus)

    def gpu_vendors(self) -> list[str]:
        seen: list[str] = []
        for gpu in self.gpus:
            if gpu.vendor not in seen:
                seen.append(gpu.vendor)
        return seen

    def has_intel_gpu(self) -> bool:
        return any(g.vendor == "intel" for g in self.gpus)

    def has_nonintel_gpu(self) -> bool:
        return any(g.vendor in ("nvidia", "amd") for g in self.gpus)

    def primary_gpu(self) -> Optional[Device]:
        if not self.gpus:
            return None
        # Prefer a discrete NVIDIA/AMD card, then Intel, then anything.
        for vendor in ("nvidia", "amd", "intel", "unknown"):
            for gpu in self.gpus:
                if gpu.vendor == vendor:
                    return gpu
        return self.gpus[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "npu": [d.to_dict() for d in self.npu],
            "gpus": [d.to_dict() for d in self.gpus],
            "cpu_count": self.cpu_count,
            "ram_mb": self.ram_mb,
            "openvino_devices": list(self.openvino_devices),
            "probes": list(self.probes),
            "detected_at": self.detected_at,
            "has_npu": self.has_npu,
            "has_gpu": self.has_gpu,
        }


# ---------------------------------------------------------------------------
# Probes — each returns (devices, detail) and never raises.
# ---------------------------------------------------------------------------
def probe_openvino() -> tuple[list[Device], str]:
    """Ask OpenVINO itself which devices it can see (most accurate, if present)."""
    try:
        import openvino  # type: ignore

        core = openvino.Core()
        names = list(core.available_devices)
    except Exception as exc:  # noqa: BLE001 - openvino is optional
        return [], f"openvino unavailable ({type(exc).__name__})"
    devices: list[Device] = []
    for name in names:
        try:
            full = core.get_property(name, "FULL_DEVICE_NAME")
        except Exception:  # noqa: BLE001
            full = name
        vendor = "intel"
        if name == "NPU":
            devices.append(Device("npu", vendor, full or name, "OpenVINO NPU plugin", source="openvino"))
        elif name == "GPU":
            devices.append(Device("gpu", vendor, full or name, "OpenVINO GPU plugin", source="openvino"))
    return devices, f"openvino devices={','.join(names) or 'none'}"


def probe_linux_accel() -> tuple[list[Device], str]:
    """Intel NPU via the Linux accel subsystem (/sys/class/accel, /dev/accel)."""
    if platform.system() != "Linux":
        return [], "not linux"
    found: list[Device] = []
    details: list[str] = []
    sys_root = Path("/sys/class/accel")
    try:
        entries = sorted(sys_root.iterdir()) if sys_root.exists() else []
    except OSError:
        entries = []
    for entry in entries:
        vendor_id = ""
        try:
            vendor_id = (entry / "device" / "vendor").read_text().strip()
        except OSError:
            pass
        details.append(entry.name)
        found.append(
            Device(
                "npu",
                "intel" if vendor_id == "0x8086" else "unknown",
                entry.name,
                f"vendor={vendor_id or 'unknown'}",
                source="sysfs",
            )
        )
    if not found:
        # Driver present but no sysfs class entry (older kernels / WSL).
        dev_root = Path("/dev/accel")
        try:
            for node in sorted(dev_root.glob("accel*")):
                details.append(node.name)
                found.append(Device("npu", "intel", node.name, "/dev/accel node", source="devfs"))
        except OSError:
            pass
    return found, f"accel entries={','.join(details) or 'none'}"


def probe_npu_smi() -> tuple[list[Device], str]:
    """``npu-smi info`` (Intel's NPU management CLI)."""
    out = _run(["npu-smi", "info"])
    if not out:
        return [], "npu-smi not present"
    name = ""
    for line in out.splitlines():
        if "NPU" in line and name == "":
            name = line.strip()[:120]
            break
    return [Device("npu", "intel", name or "Intel NPU", "npu-smi reports an NPU", source="npu-smi")], "npu-smi present"


def probe_windows_npu() -> tuple[list[Device], str]:
    """Intel NPU on Windows via PowerShell PnP devices."""
    if platform.system() != "Windows":
        return [], "not windows"
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -match 'NPU|Neural|AI Boost|VPU' } | Select-Object -ExpandProperty FriendlyName",
        ],
        timeout=12.0,
    )
    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        return [], "no NPU class device"
    return [Device("npu", "intel", names[0], "Windows PnP device", source="pnp")] , f"{len(names)} device(s)"


def probe_nvidia() -> tuple[list[Device], str]:
    """NVIDIA GPUs via nvidia-smi."""
    out = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
    )
    if not out.strip():
        return [], "nvidia-smi not present"
    devices: list[Device] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        name = parts[0] if parts else "NVIDIA GPU"
        mem = None
        if len(parts) > 1:
            try:
                mem = int(float(parts[1]))
            except ValueError:
                mem = None
        devices.append(Device("gpu", "nvidia", name, "nvidia-smi", memory_mb=mem, source="nvidia-smi"))
    return devices, f"{len(devices)} NVIDIA GPU(s)"


def probe_amd() -> tuple[list[Device], str]:
    """AMD GPUs via rocm-smi (best effort)."""
    out = _run(["rocm-smi", "--showproductname"], timeout=8.0)
    if not out.strip():
        return [], "rocm-smi not present"
    devices: list[Device] = []
    for line in out.splitlines():
        low = line.lower()
        if "card series" in low or "sku" in low:
            name = line.split(":", 1)[-1].strip()
            if name:
                devices.append(Device("gpu", "amd", name, "rocm-smi", source="rocm-smi"))
    return devices, f"{len(devices)} AMD GPU(s)"


def probe_lspci() -> tuple[list[Device], str]:
    """lspci fallback — catches Intel iGPUs/Arc when no vendor CLI exists."""
    out = _run(["lspci", "-nn"], timeout=8.0)
    if not out:
        return [], "lspci not present"
    devices: list[Device] = []
    for line in out.splitlines():
        low = line.lower()
        if not any(k in low for k in ("vga", "3d controller", "display", "processing accelerators", "neural")):
            continue
        vendor = "unknown"
        if "[8086:" in line:
            vendor = "intel"
        elif "[10de:" in line:
            vendor = "nvidia"
        elif "[1002:" in line:
            vendor = "amd"
        kind = "npu" if ("neural" in low or "processing accelerator" in low or "vpu" in low) else "gpu"
        name = line.split(":", 2)[-1].strip()[:120]
        devices.append(Device(kind, vendor, name, "lspci", source="lspci"))
    return devices, f"lspci matched {len(devices)} device(s)"


#: Ordered probe list. Tests can monkeypatch any of these.
DEFAULT_PROBES: tuple[Callable[[], tuple[list[Device], str]], ...] = (
    probe_openvino,
    probe_linux_accel,
    probe_npu_smi,
    probe_windows_npu,
    probe_nvidia,
    probe_amd,
    probe_lspci,
)


def _memory_mb() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:  # noqa: BLE001
        return 0


def detect(probes: Optional[tuple[Callable[[], tuple[list[Device], str]], ...]] = None) -> HardwareSnapshot:
    """Probe the machine once and return a :class:`HardwareSnapshot`."""
    probes = probes or DEFAULT_PROBES
    snapshot = HardwareSnapshot(cpu_count=os.cpu_count() or 1, ram_mb=_memory_mb())
    seen: set[tuple[str, str]] = set()
    for probe in probes:
        try:
            devices, detail = probe()
        except Exception as exc:  # noqa: BLE001
            devices, detail = [], f"{getattr(probe, '__name__', 'probe')} failed: {exc}"
        snapshot.probes.append({"probe": getattr(probe, "__name__", str(probe)), "detail": detail})
        for device in devices or []:
            key = (device.kind, (device.name or device.source).lower())
            if key in seen:
                continue
            seen.add(key)
            if device.kind == "npu":
                snapshot.npu.append(device)
            else:
                snapshot.gpus.append(device)
    # Record what OpenVINO itself reports, for the dashboard's device line.
    try:
        import openvino  # type: ignore

        snapshot.openvino_devices = list(openvino.Core().available_devices)
    except Exception:  # noqa: BLE001
        snapshot.openvino_devices = []
    return snapshot


# ---------------------------------------------------------------------------
# Engine plan
# ---------------------------------------------------------------------------
def _nollama_base_url() -> str:
    port = int(getattr(config, "nollama_port", 8010) or 8010)
    return f"http://localhost:{port}/v1"


def _ollama_base_url() -> str:
    return f"{str(getattr(config, 'ollama_base_url', 'http://localhost:11434')).rstrip('/')}/v1"


def _role(
    role: str,
    engine: str,
    device: str,
    model: str,
    reason: str,
    *,
    tools: Optional[bool] = None,
) -> dict[str, Any]:
    """Build one role assignment (always concrete — never 'processing')."""
    if engine == ENGINE_NOLLAMA:
        base_url = _nollama_base_url()
    elif engine == ENGINE_OLLAMA:
        base_url = _ollama_base_url()
    else:
        base_url = ""
    if tools is None:
        # The Intel NPU has a hard prompt cap and no tool-calling; every other
        # target can take the full tool registry.
        tools = device != "NPU"
    return {
        "role": role,
        "engine": engine,
        "device": device,
        "provider": engine if engine != ENGINE_NONE else "",
        "base_url": base_url,
        "model": model,
        "supports_tools": bool(tools),
        "reason": reason,
    }


def plan(hw: Optional[HardwareSnapshot] = None, mode: Optional[str] = None) -> dict[str, Any]:
    """Decide which engine runs which role, and why.

    ``mode`` overrides the automatic choice (``auto`` / ``pipelined`` / ``npu``
    / ``gpu`` / ``cpu`` / ``off``).  Every role always resolves to a concrete
    engine: when the requested accelerator is absent the role falls back
    instead of staying undecided.
    """
    hw = hw or cached_hardware()
    mode = (mode or getattr(config, "local_engine_mode", "auto") or "auto").strip().lower()

    npu_model = getattr(config, "nollama_npu_model", "") or "openvino/qwen3-8b-int4-cw-ov"
    gpu_model = getattr(config, "nollama_gpu_model", "") or "openvino/minicpm5-1b-int4-g128-ov"
    vision_model = getattr(config, "nollama_vision_model", "") or "openvino/qwen3-vl-8b-instruct-int8-ov"
    doctor_model = getattr(config, "doctor_model", "") or gpu_model
    ollama_model = getattr(config, "ollama_default_model", "") or "llama3.1:8b"
    ollama_vision = getattr(config, "ollama_vision_model", "") or "llava:7b"

    notes: list[str] = []
    has_npu, has_gpu = hw.has_npu, hw.has_gpu

    if mode == "off":
        roles = {
            role: _role(role, ENGINE_NONE, "none", "", "local engines disabled (HERMUS_LOCAL_ENGINE=off)")
            for role in ALL_ROLES
        }
        return {
            "mode": MODE_DISABLED,
            "roles": roles,
            "notes": ["Local engine routing disabled; Hermus uses configured API keys only."],
            "hardware": hw.to_dict(),
        }

    # ---- automatic mode selection ---------------------------------------
    if mode in ("auto", ""):
        if has_npu and has_gpu:
            mode = MODE_PIPELINED
        elif has_npu:
            mode = MODE_NPU_ONLY
        elif has_gpu:
            mode = MODE_GPU_ONLY
        else:
            mode = MODE_CPU_ONLY
    elif mode == "npu":
        mode = MODE_NPU_ONLY
    elif mode == "gpu":
        mode = MODE_GPU_ONLY
    elif mode == "cpu":
        mode = MODE_CPU_ONLY

    roles: dict[str, dict[str, Any]] = {}

    if mode == MODE_PIPELINED:
        # NPU: long, cheap, always-on background work. GPU: heavy generation.
        roles[ROLE_BACKGROUND] = _role(
            ROLE_BACKGROUND, ENGINE_NOLLAMA, "NPU", npu_model,
            "NPU present — background roles (Whisper, indexing, summaries) run cool on it",
        )
        roles[ROLE_DOCTOR] = _role(
            ROLE_DOCTOR, ENGINE_NOLLAMA, "NPU", doctor_model,
            "self-repair triage is short and frequent — the NPU keeps it off the GPU",
        )
        gpu = hw.primary_gpu()
        if hw.has_nonintel_gpu():
            roles[ROLE_REASONING] = _role(
                ROLE_REASONING, ENGINE_OLLAMA, "GPU", ollama_model,
                f"{gpu.vendor if gpu else 'GPU'} GPU — Ollama's CUDA/ROCm path is the mature one",
            )
            roles[ROLE_VISION] = _role(
                ROLE_VISION, ENGINE_OLLAMA, "GPU", ollama_vision,
                "Ollama serves LLaVA on NVIDIA/AMD GPUs",
            )
        else:
            roles[ROLE_REASONING] = _role(
                ROLE_REASONING, ENGINE_NOLLAMA, "GPU", gpu_model,
                "Intel iGPU/Arc — OpenVINO INT4 decodes faster than Ollama's Vulkan path",
            )
            roles[ROLE_VISION] = _role(
                ROLE_VISION, ENGINE_NOLLAMA, "GPU", vision_model,
                "Ollama has no Intel path for local vision models",
            )
        notes.append(
            "Pipelined: the NPU keeps continuous background work (voice, embeddings, "
            "self-repair triage) so the laptop stays cool, while the GPU is reserved "
            "for token-heavy reasoning."
        )

    elif mode == MODE_NPU_ONLY:
        for role in ALL_ROLES:
            model = vision_model if role == ROLE_VISION else (doctor_model if role == ROLE_DOCTOR else npu_model)
            roles[role] = _role(role, ENGINE_NOLLAMA, "NPU", model, "NPU only — NoLlama is the only engine that targets it")
        notes.append(
            "NPU-only box: prompts cap at 4096 tokens and the NPU has no tool-calling, "
            "so heavy agent loops should use an API key or a CPU fallback."
        )

    elif mode == MODE_GPU_ONLY:
        gpu = hw.primary_gpu()
        intel_gpu = bool(gpu and gpu.vendor == "intel")
        if intel_gpu:
            roles[ROLE_REASONING] = _role(
                ROLE_REASONING, ENGINE_NOLLAMA, "GPU", gpu_model,
                "Intel iGPU/Arc — NoLlama (OpenVINO) is faster than Ollama's Vulkan path",
            )
            roles[ROLE_VISION] = _role(
                ROLE_VISION, ENGINE_NOLLAMA, "GPU", vision_model,
                "Only local vision path for Intel GPUs",
            )
            roles[ROLE_BACKGROUND] = _role(
                ROLE_BACKGROUND, ENGINE_NOLLAMA, "GPU", npu_model,
                "No NPU — background work shares the Intel GPU",
            )
            roles[ROLE_DOCTOR] = _role(
                ROLE_DOCTOR, ENGINE_NOLLAMA, "GPU", doctor_model,
                "No NPU — self-repair triage runs on the Intel GPU",
            )
        else:
            roles[ROLE_REASONING] = _role(
                ROLE_REASONING, ENGINE_OLLAMA, "GPU", ollama_model,
                f"{gpu.vendor if gpu else 'GPU'} GPU — Ollama's CUDA/ROCm backend",
            )
            roles[ROLE_VISION] = _role(
                ROLE_VISION, ENGINE_OLLAMA, "GPU", ollama_vision, "Ollama vision on NVIDIA/AMD")
            roles[ROLE_BACKGROUND] = _role(
                ROLE_BACKGROUND, ENGINE_OLLAMA, "GPU", ollama_model,
                "No NPU — background work shares the GPU via Ollama",
            )
            roles[ROLE_DOCTOR] = _role(
                ROLE_DOCTOR, ENGINE_OLLAMA, "GPU", ollama_model,
                "No NPU — self-repair triage uses the Ollama model",
            )
        notes.append("No NPU detected, so background and generative work share one device.")

    else:  # MODE_CPU_ONLY
        for role in ALL_ROLES:
            model = ollama_vision if role == ROLE_VISION else ollama_model
            roles[role] = _role(
                role, ENGINE_OLLAMA, "CPU", model,
                "No accelerator — Ollama's llama.cpp CPU backend is the mature path",
            )
        notes.append("CPU-only: NoLlama is not the right engine here (Intel-only, and llama.cpp CPU is faster).")

    if has_npu:
        notes.append("NPU prompt cap is 4096 tokens and tool-calling is unavailable there.")
    if hw.ram_mb and hw.ram_mb < 16000 and mode in (MODE_PIPELINED, MODE_GPU_ONLY):
        notes.append("Under 16 GB RAM: prefer the 1-3B INT4 builds; 8B models may fall back to CPU.")

    return {
        "mode": mode,
        "roles": roles,
        "notes": notes,
        "hardware": hw.to_dict(),
    }


def role_assignment(role: str, plan_dict: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One role's engine assignment, resolving through the cached plan."""
    plan_dict = plan_dict or cached_plan()
    roles = plan_dict.get("roles") or {}
    if role in roles:
        return roles[role]
    # Unknown role → behave like reasoning rather than returning nothing.
    return roles.get(ROLE_REASONING) or _role(role, ENGINE_NONE, "none", "", "no plan available")


def model_ref_for(role: str, plan_dict: Optional[dict[str, Any]] = None) -> str:
    """``provider/model`` string usable with :class:`core.llm.FreeLLM`."""
    assignment = role_assignment(role, plan_dict)
    provider = assignment.get("provider") or ""
    model = assignment.get("model") or ""
    if not provider or not model:
        return ""
    return f"{provider}/{model}"


# ---------------------------------------------------------------------------
# Cache (detection is cheap but not free: lspci/PowerShell probes fork).
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {"hw": None, "plan": None, "at": 0.0}
_CACHE_TTL = 300.0


def cached_hardware(refresh: bool = False) -> HardwareSnapshot:
    now = time.time()
    hw = _cache.get("hw")
    if refresh or hw is None or (now - float(_cache.get("at") or 0)) > _CACHE_TTL:
        hw = detect()
        _cache["hw"] = hw
        _cache["at"] = now
        _cache["plan"] = None
    return hw  # type: ignore[return-value]


def cached_plan(refresh: bool = False) -> dict[str, Any]:
    plan_dict = _cache.get("plan")
    if refresh or plan_dict is None:
        plan_dict = plan(cached_hardware(refresh=refresh))
        _cache["plan"] = plan_dict
    return plan_dict  # type: ignore[return-value]


def reset_cache() -> None:
    """Drop cached detection (used by tests and by ``POST /engine/refresh``)."""
    _cache.update({"hw": None, "plan": None, "at": 0.0})


# ---------------------------------------------------------------------------
# Reachability + dashboard-facing state
# ---------------------------------------------------------------------------
def probe_endpoint(base_url: str, timeout: float = 2.0) -> dict[str, Any]:
    """Is this OpenAI-compatible endpoint answering? Never raises."""
    import requests

    url = f"{str(base_url).rstrip('/')}/models"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            models = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
            return {"reachable": True, "models": models, "detail": "HTTP 200"}
        return {"reachable": False, "models": [], "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "models": [], "detail": f"{type(exc).__name__}: {exc}"[:160]}


def state(refresh: bool = False, probe: bool = True) -> dict[str, Any]:
    """Dashboard-ready summary: hardware, plan, and whether each engine answers.

    Statuses come from the fixed vocabulary at the top of this module so the UI
    never has to render an open-ended state.
    """
    from .nollama import nollama_manager

    plan_dict = cached_plan(refresh=refresh)
    engines: dict[str, Any] = {}
    if probe:
        for engine_id, base_url in (
            (ENGINE_OLLAMA, _ollama_base_url()),
            (ENGINE_NOLLAMA, _nollama_base_url()),
        ):
            engines[engine_id] = probe_endpoint(base_url)
    else:
        for engine_id in (ENGINE_OLLAMA, ENGINE_NOLLAMA):
            engines[engine_id] = {"reachable": None, "models": [], "detail": "not probed"}

    nollama_info = nollama_manager.status(probe=False)
    used_engines = {a["engine"] for a in plan_dict["roles"].values()}

    if ENGINE_NOLLAMA in used_engines:
        # Models are checked before reachability: a server that answers with no
        # model on disk still has nothing to serve, and "ready" would hide the
        # one thing the user has to do (download a model).
        served_models = engines[ENGINE_NOLLAMA].get("models") or []
        if not nollama_info.get("installed"):
            overall = STATUS_NEEDS_INSTALL
            action = "install"
        elif not nollama_info.get("models") and not served_models:
            overall = STATUS_NEEDS_MODEL
            action = "download_model"
        elif not engines[ENGINE_NOLLAMA].get("reachable"):
            overall = STATUS_UNAVAILABLE
            action = "start"
        else:
            overall = STATUS_READY
            action = ""
    elif ENGINE_OLLAMA in used_engines:
        overall = STATUS_READY if engines[ENGINE_OLLAMA].get("reachable") else STATUS_UNAVAILABLE
        action = "" if overall == STATUS_READY else "start_ollama"
    else:
        overall = STATUS_NOT_APPLICABLE
        action = ""

    # The overview banner disappears once the model is on disk.
    model_needed = overall == STATUS_NEEDS_MODEL
    recommended = nollama_manager.recommended_model(plan_dict)

    return {
        "status": overall,
        "action": action,
        "model_needed": model_needed,
        "plan": plan_dict,
        "engines": engines,
        "nollama": nollama_info,
        "recommended_model": recommended,
        "nollama_base_url": _nollama_base_url(),
        "ollama_base_url": _ollama_base_url(),
    }
