"""The undo and preview endpoints, and their wiring into the gateway."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from core import undo as undo_mod
from core.undo import UndoJournal

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = UndoJournal(tmp_path / "journal")
    monkeypatch.setattr(undo_mod, "_journal", store)
    return store


@pytest.fixture
def permissions(tmp_path, monkeypatch):
    import core.permissions as perms

    manager = perms.PermissionManager(
        overrides_path=tmp_path / "permissions.json",
        approvals_path=tmp_path / "grants.json",
    )
    monkeypatch.setattr(perms, "permission_manager", manager)
    return manager


def run(coro):
    return asyncio.run(coro)


def test_gateway_mounts_the_undo_router():
    source = (ROOT / "gateway" / "gateway.py").read_text(encoding="utf-8")
    assert "from gateway.routes_undo import router as _undo_router" in source
    assert "app.include_router(_undo_router" in source


def test_undo_list_and_undo_one_over_the_api(journal, permissions, tmp_path):
    from core.tool_registry import tool_registry
    from gateway.routes_undo import undo_list, undo_one

    tool_registry.load(force=True)
    target = tmp_path / "routed.txt"
    assert tool_registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True

    listing = run(undo_list())
    assert listing["count"] == 1
    assert listing["undoable"] == 1
    entry_id = listing["entries"][0]["id"]

    undone = run(undo_one(entry_id, None))
    assert undone["success"] is True
    assert not target.exists()


def test_undo_of_an_unknown_id_is_a_404(journal):
    from gateway.routes_undo import undo_one

    with pytest.raises(HTTPException) as caught:
        run(undo_one("undo_missing", None))
    assert caught.value.status_code == 404


def test_undo_last_over_the_api(journal, permissions, tmp_path):
    from core.tool_registry import tool_registry
    from gateway.routes_undo import undo_last

    tool_registry.load(force=True)
    target = tmp_path / "last.txt"
    assert tool_registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True

    assert run(undo_last(None))["success"] is True
    assert not target.exists()


def test_preview_route_refuses_what_it_cannot_describe(journal):
    from gateway.routes_undo import preview_action

    with pytest.raises(HTTPException) as caught:
        run(preview_action({"tool": "file_write", "args": {}}))
    assert caught.value.status_code == 422
    assert caught.value.detail["previewable"] is False


def test_preview_then_confirm_through_the_routes(journal, permissions, tmp_path):
    from gateway.routes_undo import confirm_action, preview_action

    target = tmp_path / "confirm.txt"
    shown = run(preview_action({"tool": "file_write", "args": {"path": str(target), "content": "through the api"}}))
    assert shown["plan"]["count"] == 1

    acted = run(confirm_action(shown["token"]))
    assert acted["ok"] is True
    assert target.exists()
    assert journal.list(1)[0]["state"] == "applied"


def test_confirm_with_an_unknown_token_is_a_409(journal):
    from gateway.routes_undo import confirm_action

    with pytest.raises(HTTPException) as caught:
        run(confirm_action("nope"))
    assert caught.value.status_code == 409
