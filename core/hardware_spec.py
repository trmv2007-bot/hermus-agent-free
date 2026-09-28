"""What this machine can actually do, measured rather than assumed.

Everything here reports a *source* alongside the number, because a detection
layer that lies is worse than one that admits ignorance. When a value cannot be
established on this platform the answer is ``value=None, source="unknown"``
rather than a plausible guess -- the recommender is built to show a gap, not to
paper over one.

The specific trap this exists to avoid: budget arithmetic that adds up file
sizes and forgets that a vision transformer builds a large activation buffer on
a full-screen image. A model can fit its *weights* in 6 GB and still OOM the
first time you point a camera at a 1920x1080 screenshot.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any

GB = 1024 ** 3


@dataclass
class GPU:
    name: str | None = None
    vendor: str | None = None          # nvidia | amd | apple | intel | unknown
    vram_total_gb: float | None = None
    vram_free_gb: float | None = None
    runtime: str | None = None         # cuda | rocm | metal | directml | none
    compute_capability: str | None = None
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MachineSpecs:
    os_name: str = ""
    os_version: str = ""
    arch: str = ""
    cpu_cores: int | None = None
    ram_total_gb: float | None = None
    ram_free_gb: float | None = None
    disk_free_gb: float | None = None
    gpu: GPU = field(default_factory=GPU)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpu"] = self.gpu.to_dict()
        return d


# --- nvidia-smi -------------------------------------------------------------
# The one path that gives a *free* VRAM number, which is the number that
# actually decides whether a model loads right now.

def _nvidia_smi() -> GPU | None:
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not (os.path.exists(exe) or shutil.which("nvidia-smi")):
        return None

    query = "name,memory.total,memory.free,memory.used,driver_version,compute_cap"
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=12,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None

    line = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 6:
        return None
    name, total_mb, free_mb, _used_mb, driver, cap = parts[:6]

    def num(s: str) -> float | None:
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    t, f = num(total_mb), num(free_mb)
    return GPU(
        name=name or None,
        vendor="nvidia",
        vram_total_gb=round(t / 1024, 2) if t else None,
        vram_free_gb=round(f / 1024, 2) if f else None,
        runtime="cuda",
        compute_capability=cap or None,
        source=f"nvidia-smi (driver {driver})" if driver else "nvidia-smi",
    )


# --- rocm -------------------------------------------------------------------

def _rocm() -> GPU | None:
    exe = shutil.which("rocm-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--showproductname", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=12,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        raw = out.stdout
    except Exception:
        return None
    import json as _json
    try:
        j = _json.loads(raw)
        card = list(j)[0]
        gpus = j.get(card, {})
        name = next(iter(gpus.get("card", {}).values()), None)
        total = next(iter(gpus.get("VRAM Total Memory (B)", {}).values()), None)
        free = next(iter(gpus.get("VRAM Total Memory Free (B)", {}).values()), None)
        return GPU(
            name=name, vendor="amd", runtime="rocm",
            vram_total_gb=round(float(total) / GB, 2) if total else None,
            vram_free_gb=round(float(free) / GB, 2) if free else None,
            source="rocm-smi",
        )
    except Exception:
        return None


# --- apple silicon ----------------------------------------------------------

def _apple() -> GPU | None:
    if platform.system() != "Darwin":
        return None
    chip = platform.machine()  # arm64 on Apple silicon
    # Apple silicon has no separate VRAM; the GPU shares the unified memory
    # pool. Reporting a made-up VRAM number here is exactly the kind of lie
    # that module is meant to prevent, so the field stays None and the note
    # explains that ram_free_gb is the real budget.
    return GPU(
        name=f"Apple {chip}",
        vendor="apple",
        vram_total_gb=None,
        vram_free_gb=None,
        runtime="metal",
        source="unified memory (no discrete VRAM)",
    )


# --- windows generic --------------------------------------------------------

def _windows_display_adapters() -> GPU | None:
    """Last resort on Windows with no vendor tool.

    Reports the adapter *name* only. It deliberately does not invent a VRAM
    figure: WMI's AdapterRAM is a 32-bit field that wraps to nonsense on
    anything above 4 GB, which is most modern cards.
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        class DISPLAY_DEVICE(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128), ("StateFlags", ctypes.c_ulong),
                ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128),
            ]
        user32 = ctypes.windll.user32
        user32.EnumDisplayDevicesW(0, i, ctypes.byref(dd := DISPLAY_DEVICE()), 0)
        dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
        i = 0
        while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            i += 1
            s = dd.DeviceString or ""
            low = s.lower()
            if any(k in low for k in ("nvidia", "radeon", "amd", "intel", "apple")):
                vendor = ("nvidia" if "nvidia" in low else "amd" if ("radeon" in low or "amd" in low)
                          else "apple" if "apple" in low else "intel")
                runtime = "cuda" if vendor == "nvidia" else "directml"
                return GPU(name=s, vendor=vendor, runtime=runtime,
                           source="EnumDisplayDevices (name only -- VRAM unknown)")
    except Exception:
        pass
    return None


def detect_gpu() -> tuple[GPU, list[str]]:
    notes: list[str] = []
    for probe in (_nvidia_smi, _rocm, _apple, _windows_display_adapters):
        try:
            got = probe()
        except Exception:
            got = None
        if got:
            if got.vram_total_gb is None and got.vendor in ("nvidia", "amd"):
                notes.append(
                    f"{got.vendor} GPU '{got.name}' detected, but no usable VRAM figure -- "
                    "recommendations will be conservative"
                )
            return got, notes
    notes.append("no GPU detected")
    return GPU(source="none found"), notes


def detect_ram() -> tuple[float | None, float | None]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(vm.total / GB, 2), round(vm.available / GB, 2)
    except Exception:
        pass
    try:  # fallback without psutil
        import ctypes
        if platform.system() == "Windows":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / GB, 2), round(m.ullAvailPhys / GB, 2)
    except Exception:
        pass
    return None, None


def detect_disk_free(path: str | None = None) -> float | None:
    try:
        return round(shutil.disk_usage(path or os.path.expanduser("~")).free / GB, 2)
    except Exception:
        return None


def detect_specs() -> MachineSpecs:
    gpu, notes = detect_gpu()
    ram_total, ram_free = detect_ram()
    s = MachineSpecs(
        os_name=platform.system(),
        os_version=platform.release(),
        arch=platform.machine(),
        cpu_cores=os.cpu_count(),
        ram_total_gb=ram_total,
        ram_free_gb=ram_free,
        disk_free_gb=detect_disk_free(),
        gpu=gpu,
        notes=notes,
    )
    if gpu.vendor == "apple":
        s.notes.append(
            "Apple silicon shares memory between CPU and GPU, so ram_free_gb "
            "(%.1f GB) is the real budget for model weights, not a VRAM figure" % (ram_free or 0)
        )
    if gpu.vram_free_gb is not None and gpu.vram_total_gb:
        used = round(gpu.vram_total_gb - gpu.vram_free_gb, 2)
        if used > 0.8:
            s.notes.append(
                f"%.1f GB of %.1f GB VRAM is already in use by another process, so "
                "recommendations use the free figure, not the total" % (used, gpu.vram_total_gb)
            )
    return s


if __name__ == "__main__":  # pragma: no cover - diagnostic
    import json
    print(json.dumps(detect_specs().to_dict(), indent=2))
