from __future__ import annotations

from core.workspace import Workspace
from gateway.routes_workshop import _safe_path, _workspace_root


def test_workshop_safe_path_rejects_escape(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    assert _safe_path(root, "src/main.py") == (root / "src/main.py").resolve()
    try:
        _safe_path(root, "../outside.txt")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("path escape was accepted")


def test_workshop_root_uses_active_project(tmp_path, monkeypatch):
    ws = Workspace(str(tmp_path / "hermus"))
    ws.create_project("demo", "test")
    ws.set_current_project("demo")
    monkeypatch.setattr("core.workspace.workspace", ws)
    root = _workspace_root()
    assert root.name == "demo"
    assert (root / "project.yaml").exists()
