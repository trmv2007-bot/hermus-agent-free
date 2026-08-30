"""NoLlama manager: install without weights, model downloads with real states.

Two invariants matter:

1. ``setup.sh`` (and ``install()``) must never pull multi-GB model weights —
   the dashboard does that on demand, so a fresh clone stays small.
2. A download must always reach a *terminal* state (``ready`` / ``failed`` /
   ``cancelled``).  Nothing is allowed to sit in a "processing" state the UI
   would poll forever.
"""
from __future__ import annotations

import json
import time

import pytest

import core.nollama as nl
from core.nollama import (
    DEFAULT_MODEL_ID,
    MODEL_CATALOG,
    STATE_CANCELLED,
    STATE_DOWNLOADING,
    STATE_FAILED,
    STATE_QUEUED,
    STATE_READY,
    TERMINAL_STATES,
    NollamaManager,
    model_dir_ready,
)


@pytest.fixture
def mgr(tmp_path):
    return NollamaManager(home=tmp_path / "nollama", models_dir=tmp_path / "models")


def _write_ir(path, declared=1024, actual=2048):
    """Create a directory that looks like a complete OpenVINO IR export."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "openvino_model.xml").write_text(
        '<net><weights><blob offset="0" size="%d"/></weights></net>' % declared, encoding="utf-8"
    )
    (path / "openvino_model.bin").write_bytes(b"x" * actual)
    return path


# ---------------------------------------------------------------------------
# Install: server only, never weights
# ---------------------------------------------------------------------------
def test_install_reports_missing_git_instead_of_guessing(mgr, monkeypatch):
    monkeypatch.setattr(nl, "_which", lambda name: "")
    result = mgr.install()
    assert result["success"] is False
    assert result["stage"] == "clone"
    assert "git" in result["error"]
    assert result["hint"]


def test_install_downloads_no_model_weights(mgr, monkeypatch):
    """A successful install must leave the models directory empty."""
    monkeypatch.setattr(nl, "_which", lambda name: "/usr/bin/git")

    def fake_shell(cmd, timeout=120):
        import pathlib

        if "clone" in cmd:
            target = next(c for c in cmd if c.endswith(".tmp"))
            pathlib.Path(target).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(target) / "nollama.py").write_text("# server\n", encoding="utf-8")
            return 0, "cloned"
        if "venv" in cmd:
            # Stand in for `python -m venv`: the real thing creates the interpreter.
            venv = pathlib.Path(cmd[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            return 0, ""
        return 0, ""

    monkeypatch.setattr(nl, "_shell", fake_shell)
    result = mgr.install()
    assert result["success"] is True, result
    assert result["models_downloaded"] is False
    assert mgr.installed() and mgr.venv_ready()
    assert mgr.installed_models() == []
    assert mgr.models_dir.exists() is False or list(mgr.models_dir.iterdir()) == []
    # State file records the install so the dashboard can show it.
    assert json.loads(mgr.state_path.read_text(encoding="utf-8"))["installed"] is True


def test_install_reports_a_venv_without_an_interpreter(mgr, monkeypatch):
    """`python -m venv` exiting 0 is not proof: verify the interpreter exists."""
    monkeypatch.setattr(nl, "_which", lambda name: "/usr/bin/git")

    def fake_shell(cmd, timeout=120):
        if "clone" in cmd:
            target = next(c for c in cmd if c.endswith(".tmp"))
            import pathlib

            pathlib.Path(target).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(target) / "nollama.py").write_text("# server\n", encoding="utf-8")
        return 0, ""  # claims success but creates no venv

    monkeypatch.setattr(nl, "_shell", fake_shell)
    result = mgr.install()
    assert result["success"] is False
    assert result["stage"] == "venv"
    assert "python3-venv" in result["hint"]


def test_start_refuses_before_install(mgr):
    result = mgr.start(device="NPU")
    assert result["success"] is False
    assert result["action"] == "install"


def test_start_never_binds_the_gateway_port(mgr, monkeypatch, tmp_path):
    """NoLlama's 8000 default would shadow the Hermus gateway."""
    mgr.home.mkdir(parents=True, exist_ok=True)
    (mgr.home / "nollama.py").write_text("# server\n", encoding="utf-8")
    (mgr.home / "venv" / "bin").mkdir(parents=True)
    (mgr.home / "venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (mgr.home / "venv" / "bin" / "python").chmod(0o755)

    captured = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(nl.subprocess, "Popen", fake_popen)
    result = mgr.start(device="GPU", port=8000)
    assert result["success"] is True
    assert result["port"] != 8000
    assert "--ollama-port" in captured["cmd"], "must not fight a real Ollama for 11434"
    assert captured["cmd"][captured["cmd"].index("--ollama-port") + 1] == "0"
    assert "GPU" in captured["cmd"]


# ---------------------------------------------------------------------------
# Model directory sanity
# ---------------------------------------------------------------------------
def test_model_dir_ready_requires_the_ir_pair(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert model_dir_ready(empty) is False

    good = _write_ir(tmp_path / "good", declared=1024, actual=2048)
    assert model_dir_ready(good) is True


def test_truncated_download_is_rejected(tmp_path):
    """An interrupted multi-GB download must not look like a working model."""
    broken = _write_ir(tmp_path / "broken", declared=5_000_000, actual=10)
    assert model_dir_ready(broken) is False


def test_unreadable_xml_falls_back_to_presence(tmp_path):
    path = tmp_path / "weird"
    path.mkdir()
    (path / "openvino_model.xml").write_bytes(b"\x00\x01binary junk")
    (path / "openvino_model.bin").write_bytes(b"x" * 64)
    assert model_dir_ready(path) is True


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def test_catalog_has_a_minicpm_entry_for_the_doctor_role(mgr):
    entry = mgr.get_spec(DEFAULT_MODEL_ID)
    assert entry is not None
    assert "minicpm" in entry.repo.lower()
    assert "doctor" in entry.roles
    assert entry.est_size_gb < 2, "the doctor model must be small enough for background use"
    # INT4 group-128 is not NPU-compatible (channel-wise only) — record it.
    assert "NPU" not in entry.devices
    assert "npu" not in entry.notes.lower().split("channel-wise")[0] or "not" in entry.notes.lower()


def test_catalog_entries_are_unique_and_sized(mgr):
    ids = [m.id for m in MODEL_CATALOG]
    assert len(ids) == len(set(ids))
    repos = [m.repo for m in MODEL_CATALOG]
    assert len(repos) == len(set(repos))
    for spec in MODEL_CATALOG:
        assert spec.est_size_gb > 0
        assert "/" in spec.repo, "repo must be an org/name Hugging Face id"
        assert spec.devices, f"{spec.id} declares no devices"
        listed = {m["id"]: m for m in mgr.list_catalog()}
        assert listed[spec.id]["installed"] is False


def test_installed_models_are_detected(mgr):
    spec = mgr.get_spec("minicpm")
    _write_ir(mgr.model_dir(spec))
    found = mgr.installed_models()
    assert len(found) == 1
    assert found[0]["model_id"] == "minicpm"
    assert found[0]["complete"] is True


def test_recommended_model_disappears_once_installed(mgr):
    """The dashboard banner is driven by this: installed → nothing recommended."""
    plan = {"roles": {"doctor": {"engine": "nollama", "device": "GPU"},
                     "background": {"engine": "nollama", "device": "GPU"}}}
    first = mgr.recommended_model(plan)
    assert first is not None and first["id"] == "minicpm"
    _write_ir(mgr.model_dir(mgr.get_spec("minicpm")))
    second = mgr.recommended_model(plan)
    assert second is None or second["id"] != "minicpm"


def test_recommended_model_falls_back_to_default(mgr):
    assert mgr.recommended_model({})["id"] == DEFAULT_MODEL_ID


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------
def test_unknown_model_is_rejected_with_the_catalog(mgr):
    result = mgr.download_model("does-not-exist")
    assert result["success"] is False
    assert DEFAULT_MODEL_ID in result["available"]


def test_already_downloaded_model_is_a_no_op(mgr):
    _write_ir(mgr.model_dir(mgr.get_spec("minicpm")))
    result = mgr.download_model("minicpm")
    assert result["started"] is False
    assert result["job"]["state"] == STATE_READY
    assert result["job"]["progress"] == 1.0
    assert result["job"]["terminal"] is True


def test_download_reaches_a_terminal_state(mgr, monkeypatch):
    """Happy path: queued → downloading → ready, with progress and a path."""
    import huggingface_hub

    def fake_snapshot_download(**kwargs):
        target = _write_ir(nl.Path(kwargs["local_dir"]), declared=512, actual=1024)
        return str(target)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    result = mgr.download_model("npu-fast")
    assert result["started"] is True
    assert result["job"]["state"] in (STATE_QUEUED, STATE_DOWNLOADING)

    job_id = result["job"]["id"]
    final = None
    for _ in range(80):
        final = mgr.download_status(job_id)
        if final["terminal"]:
            break
        time.sleep(0.1)
    assert final["state"] == STATE_READY, final
    assert final["progress"] == 1.0
    assert final["path"].endswith("LFM2.5-1.2B-Instruct-int4-cw-ov")
    assert mgr.installed_models(), "the downloaded model must be visible to NoLlama"


def test_incomplete_download_is_reported_failed(mgr, monkeypatch):
    """A download that lands without the IR pair is a failure, not a success."""
    import huggingface_hub

    def fake_snapshot_download(**kwargs):
        nl.Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        return kwargs["local_dir"]

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    result = mgr.download_model("whisper-base")
    final = None
    for _ in range(80):
        final = mgr.download_status(result["job"]["id"])
        if final["terminal"]:
            break
        time.sleep(0.1)
    assert final["state"] == STATE_FAILED
    assert "openvino_model" in final["error"]


def test_download_error_is_captured_not_raised(mgr, monkeypatch):
    import huggingface_hub

    def boom(**kwargs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    result = mgr.download_model("canary")
    final = None
    for _ in range(80):
        final = mgr.download_status(result["job"]["id"])
        if final["terminal"]:
            break
        time.sleep(0.1)
    assert final["state"] == STATE_FAILED
    assert "network unreachable" in final["error"]


def test_second_download_of_the_same_model_reuses_the_running_job(mgr, monkeypatch):
    """No double-download: the UI can hammer the button safely."""
    import threading

    import huggingface_hub

    release = threading.Event()

    def slow_snapshot(**kwargs):
        release.wait(5)
        _write_ir(nl.Path(kwargs["local_dir"]))
        return kwargs["local_dir"]

    monkeypatch.setattr(huggingface_hub, "snapshot_download", slow_snapshot)
    first = mgr.download_model("smollm3")
    second = mgr.download_model("smollm3")
    assert first["started"] is True
    assert second["started"] is False
    assert second["job"]["id"] == first["job"]["id"]
    release.set()

    job_id = first["job"]["id"]
    for _ in range(80):
        if mgr.download_status(job_id)["terminal"]:
            break
        time.sleep(0.1)


def test_cancel_download(mgr, monkeypatch):
    import threading

    import huggingface_hub

    release = threading.Event()

    def slow_snapshot(**kwargs):
        release.wait(5)
        return kwargs["local_dir"]

    monkeypatch.setattr(huggingface_hub, "snapshot_download", slow_snapshot)
    job = mgr.download_model("vision")["job"]
    cancelled = mgr.cancel_download(job["id"])
    assert cancelled["cancelled"] is True
    assert cancelled["job"]["state"] == STATE_CANCELLED
    assert cancelled["job"]["terminal"] is True
    # Cancelling a terminal job reports the state instead of lying.
    again = mgr.cancel_download(job["id"])
    assert again["cancelled"] is False
    assert mgr.cancel_download("nope")["cancelled"] is False
    release.set()


def test_downloads_list_reports_every_job(mgr, monkeypatch):
    _write_ir(mgr.model_dir(mgr.get_spec("minicpm")))
    mgr.download_model("minicpm")
    mgr.download_model("nope")  # rejected: no job
    rows = mgr.downloads()
    assert len(rows) == 1
    assert rows[0]["model_id"] == "minicpm"
    assert rows[0]["terminal"] is True


def test_status_shape_is_dashboard_ready(mgr):
    status = mgr.status(probe=False)
    for key in ("installed", "running", "port", "base_url", "models", "catalog",
                "downloads", "models_dir", "home", "model_count"):
        assert key in status
    assert status["installed"] is False
    assert status["model_count"] == 0
    assert len(status["catalog"]) == len(MODEL_CATALOG)


def test_stop_without_a_managed_process_is_a_no_op(mgr):
    assert mgr.stop() == {"stopped": False, "pid": None}
    assert mgr.stop_if_managed()["stopped"] is False
