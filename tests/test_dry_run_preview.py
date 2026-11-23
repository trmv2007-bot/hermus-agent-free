"""Preview before mutation: say the exact consequence, then wait for one keystroke.

The plan is the product's promise — the old name and the new name of every path,
the count, the setting's prior value — and the token is the keystroke: an action
that was never described has nothing to confirm. Anything that cannot be
described ahead of time is refused instead of attempted blind.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core import undo as undo_mod
from core.dry_run import ActionNotPreviewableError, plan_tool, preview, require_preview
from core.undo import UndoJournal


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


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "tracked.txt").write_bytes(b"tracked")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    return repo


# ---------------------------------------------------------------------------
# What a file change looks like before it happens
# ---------------------------------------------------------------------------


def test_file_write_previews_old_and_new_bytes(tmp_path):
    target = tmp_path / "preview.txt"
    target.write_bytes(b"12345")
    plan = plan_tool("file_write", {"path": str(target), "content": "a much longer body"})

    assert plan.count == 1
    change = plan.changes[0]
    assert change.old == str(target)
    assert change.new == str(target)
    assert change.before == "file (5 bytes)"
    assert change.after == "file (18 bytes)"
    assert "overwrites" in change.detail


def test_new_file_previews_as_created(tmp_path):
    plan = plan_tool("file_write", {"path": str(tmp_path / "brand-new.txt"), "content": "x"})
    assert plan.changes[0].before == "absent"
    assert "creates a new file" in plan.changes[0].detail


def test_delete_previews_the_path_going_away(tmp_path):
    target = tmp_path / "bye.txt"
    target.write_bytes(b"0123456789")
    plan = plan_tool("delete_file", {"path": str(target)})
    assert plan.count == 1
    assert plan.changes[0].old == str(target)
    assert plan.changes[0].new is None
    assert plan.changes[0].after == "absent"


def test_move_previews_old_name_to_new_name(tmp_path):
    source = tmp_path / "old-name.txt"
    source.write_bytes(b"payload")
    destination = tmp_path / "new-name.txt"
    plan = plan_tool("move_file", {"path": str(source), "destination": str(destination)})

    assert plan.count == 1
    assert plan.changes[0].old == str(source)
    assert plan.changes[0].new == str(destination)


def test_file_batch_counts_every_path_with_its_names(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    plan = plan_tool(
        "file_batch",
        {
            "ops": [
                {"action": "write", "path": str(a), "content": "one"},
                {"action": "move", "path": str(a), "destination": str(b)},
                {"action": "delete", "path": str(b)},
            ]
        },
    )

    assert plan.previewable is True
    assert plan.count == 3
    assert [change.old for change in plan.changes] == [str(a), str(a), str(b)]
    assert [change.new for change in plan.changes] == [str(a), str(b), None]


def test_moving_a_missing_file_is_refused_not_predicted(tmp_path):
    plan = plan_tool("move_file", {"path": str(tmp_path / "ghost.txt"), "destination": str(tmp_path / "x.txt")})
    assert plan.previewable is False
    assert "does not exist" in plan.refusal


# ---------------------------------------------------------------------------
# What a shell command looks like inside a repo
# ---------------------------------------------------------------------------


def test_shell_previews_the_files_it_would_touch(git_repo):
    plan = plan_tool("shell_execute", {"command": "rm tracked.txt && echo hi > fresh.txt", "cwd": str(git_repo)})

    assert plan.reversible is True
    touched = {Path(change.old).name for change in plan.changes}
    assert {"tracked.txt", "fresh.txt"} <= touched
    assert plan.certainty == "exact"


def test_shell_outside_a_repo_says_it_cannot_be_undone(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    plan = plan_tool("shell_execute", {"command": "echo hi > out.txt", "cwd": str(outside)})
    assert plan.reversible is False
    assert "cannot be undone" in plan.detail


def test_shell_with_a_missing_working_directory_is_refused(tmp_path):
    plan = plan_tool("shell_execute", {"command": "ls", "cwd": str(tmp_path / "nope")})
    assert plan.previewable is False
    assert "does not exist" in plan.refusal


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_policy_change_previews_old_value_to_new_value(permissions):
    assert permissions.set_policy("web_search", "ask")["success"] is True
    plan = plan_tool("perms_set", {"tool": "web_search", "decision": "deny"})
    change = plan.changes[0]
    assert change.before == "ask"
    assert change.after == "deny"


def test_a_settings_change_without_a_key_is_refused():
    plan = plan_tool("config_set", {"value": 3})
    assert plan.previewable is False


# ---------------------------------------------------------------------------
# Refusal: undocumented actions never run blind
# ---------------------------------------------------------------------------


def test_require_preview_raises_for_an_undescribable_action():
    with pytest.raises(ActionNotPreviewableError) as caught:
        require_preview("file_write", {})
    assert "path" in caught.value.reason


def test_registry_refuses_an_unpreviewable_mutation(tmp_path):
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    result = tool_registry.execute("file_write", {})
    assert result.get("error") == "unpreviewable_action"
    assert result.get("plan")["tool"] == "file_write"


def test_preview_returns_a_refusal_and_no_token(tmp_path):
    result = preview("move_file", {"path": str(tmp_path / "ghost"), "destination": str(tmp_path / "x")})
    assert result["ok"] is False
    assert "token" not in result
    assert result["previewable"] is False


# ---------------------------------------------------------------------------
# One keystroke: the token is the confirmation
# ---------------------------------------------------------------------------


def test_confirm_acts_only_on_the_previewed_action(journal, permissions, tmp_path):
    target = tmp_path / "keystroke.txt"
    shown = preview("file_write", {"path": str(target), "content": "confirmed"})
    assert shown["ok"] is True
    assert shown["token"]

    from core.dry_run import confirm

    result = confirm(shown["token"])
    assert result["ok"] is True, result
    assert target.read_bytes().decode("utf-8").startswith("confirmed")

    # The journal recorded it, so the confirmed action is undoable like any other.
    entry = journal.list(1)[0]
    assert entry["tool"] == "file_write"
    assert entry["state"] == "applied"

    # A token is single-use.
    again = confirm(shown["token"])
    assert again["ok"] is False
    assert "unknown or expired" in again["error"]


def test_confirm_refuses_an_invented_token():
    from core.dry_run import confirm

    result = confirm("not-a-real-token")
    assert result["ok"] is False


def test_preview_of_a_read_only_tool_is_a_refusal():
    result = preview("file_read", {"path": "README.md"})
    assert result["ok"] is False
    assert result["previewable"] is False
