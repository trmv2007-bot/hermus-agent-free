"""Accelerator detection + local engine routing (NPU via NoLlama, GPU via Ollama).

The routing table is the contract:

* Intel NPU              → NoLlama (Ollama cannot target it at all)
* NVIDIA / AMD GPU       → Ollama (mature CUDA/ROCm path; NoLlama is Intel-only)
* Intel iGPU / Arc       → NoLlama (OpenVINO INT4 beats Ollama's Vulkan decode)
* NPU + GPU              → pipelined: NPU keeps background work, GPU reasons
* neither                → Ollama on CPU

And every role must always resolve to a *concrete* engine — never a
half-decided state that leaves the dashboard spinning on "processing".
"""
from __future__ import annotations

import pytest

from core import accelerators as acc
from core.accelerators import (
    ENGINE_NOLLAMA,
    ENGINE_NONE,
    ENGINE_OLLAMA,
    MODE_CPU_ONLY,
    MODE_DISABLED,
    MODE_GPU_ONLY,
    MODE_NPU_ONLY,
    MODE_PIPELINED,
    ROLE_BACKGROUND,
    ROLE_DOCTOR,
    ROLE_REASONING,
    ROLE_VISION,
    STATUS_NEEDS_INSTALL,
    STATUS_NEEDS_MODEL,
    STATUS_NOT_APPLICABLE,
    STATUS_READY,
    Device,
    HardwareSnapshot,
    detect,
    model_ref_for,
    plan,
    role_assignment,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic hardware, so routing is testable on any machine.
# ---------------------------------------------------------------------------
def npu_intel() -> list[Device]:
    return [Device("npu", "intel", "Intel(R) AI Boost", "OpenVINO NPU plugin", source="openvino")]


def gpu_intel() -> list[Device]:
    return [Device("gpu", "intel", "Intel(R) Arc 140V", "OpenVINO GPU plugin", source="openvino")]


def gpu_nvidia() -> list[Device]:
    return [Device("gpu", "nvidia", "NVIDIA GeForce RTX 4070", "nvidia-smi", memory_mb=12288, source="nvidia-smi")]


def snapshot(npu=None, gpus=None, ram_mb=32000) -> HardwareSnapshot:
    return HardwareSnapshot(
        npu=list(npu or []),
        gpus=list(gpus or []),
        cpu_count=8,
        ram_mb=ram_mb,
        openvino_devices=["CPU"] + (["GPU"] if gpus else []) + (["NPU"] if npu else []),
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def test_detect_dedupes_and_records_probes():
    """Probes may report the same device twice (openvino + lspci); keep one."""

    def probe_a():
        return [Device("npu", "intel", "Intel AI Boost", "a", source="openvino")], "a ok"

    def probe_b():
        return [Device("npu", "intel", "Intel AI Boost", "b", source="lspci")], "b ok"

    def probe_boom():
        raise RuntimeError("probe exploded")

    snap = detect(probes=(probe_a, probe_b, probe_boom))
    assert len(snap.npu) == 1, snap.npu
    assert snap.has_npu and not snap.has_gpu
    # A probe that raises is recorded, not propagated — detection never breaks.
    assert any("probe exploded" in p["detail"] for p in snap.probes)
    assert {p["probe"] for p in snap.probes} == {"probe_a", "probe_b", "probe_boom"}


def test_detect_handles_devices_with_no_name():
    """A device with no name still dedupes on its source (no KeyError/crash)."""

    def probe():
        return [Device("gpu", "intel", "", "", source="sysfs"), Device("gpu", "intel", "", "", source="sysfs")], "x"

    snap = detect(probes=(probe,))
    assert len(snap.gpus) == 1
    assert snap.has_intel_gpu() and not snap.has_nonintel_gpu()


def test_primary_gpu_prefers_discrete_over_intel():
    snap = snapshot(gpus=gpu_intel() + gpu_nvidia())
    assert snap.primary_gpu().vendor == "nvidia"


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------
def test_npu_only_routes_everything_to_nollama():
    p = plan(snapshot(npu=npu_intel()))
    assert p["mode"] == MODE_NPU_ONLY
    for role in (ROLE_REASONING, ROLE_BACKGROUND, ROLE_DOCTOR, ROLE_VISION):
        assert p["roles"][role]["engine"] == ENGINE_NOLLAMA
        assert p["roles"][role]["device"] == "NPU"
    # NPU limitation is surfaced, not silently ignored.
    assert any("4096" in note for note in p["notes"])


def test_nvidia_gpu_routes_reasoning_to_ollama():
    p = plan(snapshot(gpus=gpu_nvidia()))
    assert p["mode"] == MODE_GPU_ONLY
    assert p["roles"][ROLE_REASONING]["engine"] == ENGINE_OLLAMA
    assert p["roles"][ROLE_VISION]["engine"] == ENGINE_OLLAMA
    assert "llava" in p["roles"][ROLE_VISION]["model"]


def test_intel_gpu_routes_to_nollama():
    p = plan(snapshot(gpus=gpu_intel()))
    assert p["mode"] == MODE_GPU_ONLY
    assert p["roles"][ROLE_REASONING]["engine"] == ENGINE_NOLLAMA
    assert p["roles"][ROLE_REASONING]["device"] == "GPU"
    # Ollama has no Intel vision path — vision must stay on NoLlama.
    assert p["roles"][ROLE_VISION]["engine"] == ENGINE_NOLLAMA


def test_cpu_only_routes_to_ollama():
    p = plan(snapshot())
    assert p["mode"] == MODE_CPU_ONLY
    assert {r["engine"] for r in p["roles"].values()} == {ENGINE_OLLAMA}
    assert {r["device"] for r in p["roles"].values()} == {"CPU"}


def test_pipelined_splits_background_npu_and_reasoning_gpu():
    """NPU + Intel GPU: background stays cool on the NPU, GPU does the work."""
    p = plan(snapshot(npu=npu_intel(), gpus=gpu_intel()))
    assert p["mode"] == MODE_PIPELINED
    assert p["roles"][ROLE_BACKGROUND]["device"] == "NPU"
    assert p["roles"][ROLE_DOCTOR]["device"] == "NPU"
    assert p["roles"][ROLE_REASONING]["device"] == "GPU"
    assert p["roles"][ROLE_VISION]["device"] == "GPU"
    assert p["roles"][ROLE_BACKGROUND]["engine"] == ENGINE_NOLLAMA


def test_pipelined_with_nvidia_gpu_keeps_ollama_for_reasoning():
    p = plan(snapshot(npu=npu_intel(), gpus=gpu_nvidia()))
    assert p["mode"] == MODE_PIPELINED
    assert p["roles"][ROLE_REASONING]["engine"] == ENGINE_OLLAMA
    assert p["roles"][ROLE_BACKGROUND]["engine"] == ENGINE_NOLLAMA
    assert p["roles"][ROLE_BACKGROUND]["device"] == "NPU"


def test_npu_roles_never_advertise_tool_calling():
    """The NPU has a hard prompt cap and no tool-calling; say so in the plan."""
    p = plan(snapshot(npu=npu_intel()))
    assert p["roles"][ROLE_REASONING]["supports_tools"] is False
    gpu_plan = plan(snapshot(gpus=gpu_intel()))
    assert gpu_plan["roles"][ROLE_REASONING]["supports_tools"] is True


def test_forced_modes_override_detection():
    hw = snapshot(npu=npu_intel(), gpus=gpu_nvidia())
    assert plan(hw, mode="npu")["mode"] == MODE_NPU_ONLY
    assert plan(hw, mode="gpu")["mode"] == MODE_GPU_ONLY
    assert plan(hw, mode="cpu")["mode"] == MODE_CPU_ONLY
    off = plan(hw, mode="off")
    assert off["mode"] == MODE_DISABLED
    assert {r["engine"] for r in off["roles"].values()} == {ENGINE_NONE}


def test_no_role_is_left_undecided():
    """Every hardware combination yields a concrete engine + reason per role."""
    combos = [
        snapshot(),
        snapshot(npu=npu_intel()),
        snapshot(gpus=gpu_intel()),
        snapshot(gpus=gpu_nvidia()),
        snapshot(npu=npu_intel(), gpus=gpu_intel()),
        snapshot(npu=npu_intel(), gpus=gpu_nvidia()),
    ]
    for hw in combos:
        for mode in ("auto", "npu", "gpu", "cpu", "off", "pipelined"):
            p = plan(hw, mode=mode)
            for role, assignment in p["roles"].items():
                assert assignment["engine"] in (ENGINE_OLLAMA, ENGINE_NOLLAMA, ENGINE_NONE)
                assert assignment["reason"], f"{mode}/{role} has no reason"
                assert assignment["device"]
                # "processing"/"unknown"/"" would be a dangling state.
                assert assignment["engine"] not in ("", "processing", "unknown")
                if assignment["engine"] == ENGINE_NONE:
                    assert assignment["base_url"] == ""
                else:
                    assert assignment["base_url"].startswith("http://localhost:")


def test_model_ref_is_provider_qualified():
    p = plan(snapshot(npu=npu_intel()))
    ref = model_ref_for(ROLE_DOCTOR, p)
    assert ref.startswith("nollama/")
    assert role_assignment(ROLE_DOCTOR, p)["provider"] == "nollama"


def test_unknown_role_falls_back_to_reasoning_not_nothing():
    p = plan(snapshot())
    assert role_assignment("mystery-role", p)["engine"] == p["roles"][ROLE_REASONING]["engine"]


def test_nollama_never_uses_the_gateway_port():
    """NoLlama defaults to 8000 — the Hermus gateway already owns that port."""
    p = plan(snapshot(npu=npu_intel()))
    for assignment in p["roles"].values():
        if assignment["engine"] == ENGINE_NOLLAMA:
            assert ":8000/" not in assignment["base_url"]


def test_low_ram_warning_is_reported():
    p = plan(snapshot(npu=npu_intel(), gpus=gpu_intel(), ram_mb=8000))
    assert any("16 GB" in note for note in p["notes"])


# ---------------------------------------------------------------------------
# Provider registry + dashboard state vocabulary
# ---------------------------------------------------------------------------
def test_nollama_is_a_registered_no_auth_provider():
    from core.providers import PROVIDER_PRESETS, get_provider, resolve_endpoint

    assert "nollama" in PROVIDER_PRESETS
    preset = get_provider("nollama")
    assert preset["no_auth"] is True
    assert ":8000/" not in preset["base_url"]
    assert resolve_endpoint("nollama").endswith("/v1/chat/completions")


def test_llm_routes_nollama_through_openai_compat():
    """``nollama/…`` must reach the OpenAI-compatible path, not the Ollama one."""
    from core.llm import FreeLLM

    llm = FreeLLM(model="nollama/MiniCPM5-1B-int4-g128-ov")
    assert llm.provider == "nollama"
    assert llm.model_name == "MiniCPM5-1B-int4-g128-ov"
    bundle = llm._resolve_bundle()
    assert ":8000/" not in (bundle.get("base_url") or "")


def test_state_status_uses_the_fixed_vocabulary(monkeypatch):
    """status ∈ {ready, needs_model, needs_install, unavailable, not_applicable}."""
    import core.nollama as nl

    monkeypatch.setattr(acc, "cached_plan", lambda refresh=False: plan(snapshot(npu=npu_intel())))
    monkeypatch.setattr(nl.NollamaManager, "status", lambda self, probe=True: {
        "installed": False, "running": False, "models": [], "model_count": 0,
        "models_dir": "/tmp/models", "home": "/tmp/nollama", "port": 8010,
    })
    monkeypatch.setattr(acc, "probe_endpoint", lambda base_url, timeout=2.0: {"reachable": False, "models": [], "detail": "no"})

    st = acc.state()
    assert st["status"] == STATUS_NEEDS_INSTALL
    assert st["action"] == "install"
    # An NPU-only box must be offered a channel-wise INT4 model: MiniCPM's
    # group-128 INT4 export dies in the Intel NPU compiler.
    assert st["recommended_model"]["id"] in ("npu-fast", "npu-chat", "smollm3", "canary")
    assert "NPU" in st["recommended_model"]["devices"]

    # Installed + a model on disk but not answering → needs starting, not installing.
    monkeypatch.setattr(nl.NollamaManager, "status", lambda self, probe=True: {
        "installed": True, "running": False, "models": [{"name": "x"}], "model_count": 1,
        "models_dir": "/tmp/models", "home": "/tmp/nollama", "port": 8010,
    })
    st = acc.state()
    assert st["status"] in (STATUS_NEEDS_MODEL, "unavailable")
    assert st["action"] in ("download_model", "start")

    # Installed, model present, answering → ready, and no action is requested.
    monkeypatch.setattr(acc, "probe_endpoint", lambda base_url, timeout=2.0: {"reachable": True, "models": ["m"], "detail": "200"})
    st = acc.state()
    assert st["status"] == STATUS_READY
    assert st["action"] == ""
    assert st["model_needed"] is False


def test_intel_gpu_box_is_offered_minicpm(monkeypatch):
    """On an Intel iGPU/Arc box the missing model the plan needs is MiniCPM."""
    import core.nollama as nl

    monkeypatch.setattr(acc, "cached_plan", lambda refresh=False: plan(snapshot(gpus=gpu_intel())))
    monkeypatch.setattr(nl.NollamaManager, "status", lambda self, probe=True: {
        "installed": True, "running": False, "models": [], "model_count": 0,
        "models_dir": "/tmp/models", "home": "/tmp/nollama", "port": 8010,
    })
    monkeypatch.setattr(acc, "probe_endpoint", lambda base_url, timeout=2.0: {"reachable": True, "models": [], "detail": "200"})

    st = acc.state()
    assert st["model_needed"] is True
    assert st["recommended_model"]["id"] == "minicpm"
    assert "doctor" in st["recommended_model"]["roles"]


def test_state_is_not_applicable_when_routing_is_off(monkeypatch):
    monkeypatch.setattr(acc, "cached_plan", lambda refresh=False: plan(snapshot(), mode="off"))
    monkeypatch.setattr(acc, "probe_endpoint", lambda base_url, timeout=2.0: {"reachable": False, "models": [], "detail": "no"})
    st = acc.state()
    assert st["status"] == STATUS_NOT_APPLICABLE
    assert st["action"] == ""


def test_cache_reset_forces_redetection(monkeypatch):
    calls = {"n": 0}

    def fake_detect(probes=None):
        calls["n"] += 1
        return snapshot()

    monkeypatch.setattr(acc, "detect", fake_detect)
    acc.reset_cache()
    acc.cached_hardware()
    acc.cached_hardware()
    assert calls["n"] == 1, "cached_hardware must not re-probe inside the TTL"
    acc.cached_hardware(refresh=True)
    assert calls["n"] == 2
    acc.reset_cache()
