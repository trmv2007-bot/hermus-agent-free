from __future__ import annotations

from core.run_events import RunBus
from core.teach_mode import TeachMode


def test_teach_mode_reconstructs_run(monkeypatch, tmp_path):
    bus = RunBus()
    bus.start("run_teach")
    bus.publish("run_teach", "tool_call", {"step": 1, "tool": "file_read", "args": {"path": "a.txt"}})
    bus.publish("run_teach", "tool_result", {"step": 1, "tool": "file_read", "error": None, "preview": "ok"})
    bus.publish("run_teach", "verification", {"verified": True})
    bus.finish("run_teach", "finished", {"response": "The workflow completed successfully."})

    import core.teach_mode as mod
    monkeypatch.setattr(mod, "run_bus", bus)

    teach = TeachMode(tmp_path / "teach.json")
    session = teach.start("Read a file and verify it", run_id="run_teach")
    preview = teach.preview(session["id"])

    assert preview["trajectory"][0]["tool_calls"][0]["name"] == "file_read"
    assert preview["tool_results"][0]["tool"] == "file_read"
    assert preview["verification"]["verified"] is True
