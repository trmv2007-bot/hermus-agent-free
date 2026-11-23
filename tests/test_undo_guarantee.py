"""Undo is a guarantee: one test per mutating class, byte-identical afterwards.

Every test here performs the real action through the real tool path
(``tool_registry.execute``), then undoes it through the journal, and compares
the machine's bytes before and after — including a file whose original content
had no trailing newline, because that is where a "close enough" restore shows.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import undo as undo_mod
from core.undo import UndoJournal


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """A journal of our own, wired into the process-wide singleton."""
    store = UndoJournal(tmp_path / "journal")
    monkeypatch.setattr(undo_mod, "_journal", store)
    return store


@pytest.fixture
def permissions(tmp_path, monkeypatch):
    """A permission manager with no local overrides, so ASK tools run in tests."""
    import core.permissions as perms

    manager = perms.PermissionManager(
        overrides_path=tmp_path / "permissions.json",
        approvals_path=tmp_path / "grants.json",
    )
    monkeypatch.setattr(perms, "permission_manager", manager)
    return manager


@pytest.fixture
def registry(journal, permissions):
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    return tool_registry


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tree(root: Path) -> dict[str, str | None]:
    """Every file under ``root`` with its sha256 — the before/after evidence."""
    out: dict[str, str | None] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = sha(path)
        elif path.is_dir():
            out[str(path.relative_to(root)) + "/"] = None
    return out


def last_entry(journal: UndoJournal) -> dict:
    entries = journal.list(1)
    assert entries, "the action was not journaled"
    return entries[0]


# ---------------------------------------------------------------------------
# File write / overwrite
# ---------------------------------------------------------------------------


def test_undo_file_write_removes_the_new_file(registry, journal, tmp_path):
    target = tmp_path / "created.txt"
    assert registry.execute("file_write", {"path": str(target), "content": "hello"})["success"] is True
    assert target.exists()

    result = journal.undo(last_entry(journal)["id"])

    assert result["success"] is True, result
    assert not target.exists()


def test_undo_overwrite_restores_exact_prior_bytes_without_trailing_newline(registry, journal, tmp_path):
    target = tmp_path / "notes.txt"
    original = b"line one\nline two with no trailing newline"
    target.write_bytes(original)
    before = sha(target)

    assert registry.execute("file_write", {"path": str(target), "content": "replaced content\n"})["success"] is True
    assert sha(target) != before

    result = journal.undo(last_entry(journal)["id"])

    assert result["success"] is True, result
    assert target.read_bytes() == original
    assert sha(target) == before


def test_undo_append_restores_the_prior_file(registry, journal, tmp_path):
    target = tmp_path / "log.txt"
    target.write_bytes(b"existing")
    before = sha(target)

    assert registry.execute("append_file", {"path": str(target), "content": "-more"})["success"] is True
    assert target.read_bytes().endswith(b"-more")

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert target.read_bytes() == b"existing"
    assert sha(target) == before


def test_undo_append_removes_a_file_it_created(registry, journal, tmp_path):
    target = tmp_path / "fresh.txt"
    assert registry.execute("append_file", {"path": str(target), "content": "seed"})["success"] is True
    assert target.exists()

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert not target.exists()


def test_undo_edit_restores_the_prior_file(registry, journal, tmp_path):
    target = tmp_path / "config.txt"
    target.write_bytes(b"port = 8000\nhost = localhost")
    before = sha(target)

    assert registry.execute("file_edit", {"path": str(target), "old_text": "8000", "new_text": "9000"})["success"] is True
    assert b"9000" in target.read_bytes()

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert sha(target) == before


# ---------------------------------------------------------------------------
# Delete / move
# ---------------------------------------------------------------------------


def test_undo_delete_restores_the_deleted_file(registry, journal, tmp_path):
    target = tmp_path / "gone.txt"
    target.write_bytes(b"precious bytes\nno trailing newline")
    before = sha(target)

    assert registry.execute("delete_file", {"path": str(target)})["success"] is True
    assert not target.exists()

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert target.exists()
    assert sha(target) == before


def test_undo_move_returns_the_file_to_its_old_path(registry, journal, tmp_path):
    source = tmp_path / "here.txt"
    source.write_bytes(b"payload")
    dest = tmp_path / "moved" / "there.txt"
    before = sha(source)

    assert registry.execute("move_file", {"path": str(source), "destination": str(dest)})["success"] is True
    assert dest.exists() and not source.exists()

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert source.exists() and not dest.exists()
    assert sha(source) == before


# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------


def test_undo_write_removes_directories_it_created(registry, journal, tmp_path):
    target = tmp_path / "deep" / "nested" / "file.txt"
    assert registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True
    assert target.exists()

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert not (tmp_path / "deep").exists()
    assert tree(tmp_path / "deep") == {} if (tmp_path / "deep").exists() else True


def test_undo_keeps_directories_it_did_not_create(registry, journal, tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    target = existing / "file.txt"
    assert registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True

    assert journal.undo(last_entry(journal)["id"])["success"] is True
    assert existing.is_dir()
    assert not target.exists()


# ---------------------------------------------------------------------------
# Shell commands inside a git work tree
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "tracked.txt").write_bytes(b"tracked original")
    (repo / "dirty.txt").write_bytes(b"dirty original")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def test_undo_shell_command_restores_the_work_tree(registry, journal, git_repo):
    before = tree(git_repo)
    command = "echo changed> tracked.txt && echo new> added.txt && rm -f dirty.txt"

    result = registry.execute("shell_execute", {"command": command, "cwd": str(git_repo), "sandbox": "off"})
    assert result.get("success") is True, result
    assert tree(git_repo) != before

    undo_result = journal.undo(last_entry(journal)["id"])

    assert undo_result["success"] is True, undo_result
    assert tree(git_repo) == before
    assert (git_repo / "tracked.txt").read_bytes() == b"tracked original"
    assert (git_repo / "dirty.txt").read_bytes() == b"dirty original"
    assert not (git_repo / "added.txt").exists()


def test_shell_outside_a_git_work_tree_is_declared_not_reversible(registry, journal, tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    result = registry.execute("shell_execute", {"command": "echo hi > out.txt", "cwd": str(outside), "sandbox": "off"})
    assert result.get("success") is True

    entry = last_entry(journal)
    assert entry["reversible"] is False
    assert "not a git work tree" in entry["reason"]
    refused = journal.undo(entry["id"])
    assert refused["success"] is False
    assert "not reversible" in refused["error"]


# ---------------------------------------------------------------------------
# Settings and policy
# ---------------------------------------------------------------------------


def test_undo_policy_change_restores_the_prior_decision(permissions, journal, tmp_path):
    overrides = Path(permissions.overrides_path)
    assert not overrides.exists(), "this test needs a manager with no overrides file yet"

    result = permissions.set_policy("web_search", "deny")
    assert result["success"] is True
    assert json.loads(overrides.read_text(encoding="utf-8"))["tools"]["web_search"] == "deny"

    undo_result = journal.undo(result["undo_id"])

    assert undo_result["success"] is True, undo_result
    # The overrides file did not exist before, and the undo restores that state
    # rather than leaving an empty document behind.
    assert not overrides.exists()
    assert permissions._load_overrides()["tools"] == {}


def test_undo_policy_change_restores_a_prior_non_default_value(permissions, journal):
    first = permissions.set_policy("shell_execute", "ask")
    second = permissions.set_policy("shell_execute", "allow")
    assert second["success"] is True

    assert journal.undo(second["undo_id"])["success"] is True
    payload = json.loads(Path(permissions.overrides_path).read_text(encoding="utf-8"))
    assert payload["tools"]["shell_execute"] == "ask"
    assert first["undo_id"] != second["undo_id"]


# ---------------------------------------------------------------------------
# Computer actions
# ---------------------------------------------------------------------------


def test_computer_action_is_journaled_as_not_reversible_before_it_runs(tmp_path, journal):
    from core.computer.controller import ComputerActionController
    from core.computer.keyboard import DryRunKeyboard
    from core.computer.mouse import DryRunMouse
    from core.computer.window_manager import DryRunWindowBackend

    controller = ComputerActionController(
        mouse=DryRunMouse(), keyboard=DryRunKeyboard(), window_manager=DryRunWindowBackend(), journal=journal
    )
    record = controller.type_text("hello")
    assert record["ok"] is True
    assert record.get("undo_id"), "the action record must carry its journal entry"

    entry = journal.get(record["undo_id"])
    assert entry.reversible is False
    assert "cannot be replayed backwards" in entry.reason
    assert entry.state == undo_mod.NOT_REVERSIBLE


# ---------------------------------------------------------------------------
# Safety properties of the journal itself
# ---------------------------------------------------------------------------


def test_undo_refuses_when_the_file_changed_after_the_action(registry, journal, tmp_path):
    target = tmp_path / "shared.txt"
    target.write_bytes(b"v1")
    assert registry.execute("file_write", {"path": str(target), "content": "v2"})["success"] is True
    entry = last_entry(journal)

    target.write_bytes(b"a person edited this afterwards")

    refused = journal.undo(entry["id"])
    assert refused["success"] is False
    assert "changed after the action" in refused["error"]
    assert target.read_bytes() == b"a person edited this afterwards"

    forced = journal.undo(entry["id"], force=True)
    assert forced["success"] is True
    assert target.read_bytes() == b"v1"


def test_undo_twice_is_refused(registry, journal, tmp_path):
    target = tmp_path / "once.txt"
    target.write_bytes(b"original")
    assert registry.execute("file_write", {"path": str(target), "content": "new"})["success"] is True
    entry = last_entry(journal)

    assert journal.undo(entry["id"])["success"] is True
    again = journal.undo(entry["id"])
    assert again["success"] is False
    assert "already undone" in again["error"]


def test_undo_last_walks_backwards_through_actions(registry, journal, tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    assert registry.execute("file_write", {"path": str(first), "content": "a"})["success"] is True
    assert registry.execute("file_write", {"path": str(second), "content": "b"})["success"] is True

    assert journal.undo_last()["success"] is True
    assert not second.exists() and first.exists()
    assert journal.undo_last()["success"] is True
    assert not first.exists()


def test_journal_is_bounded_and_prunes_oldest(registry, tmp_path, monkeypatch):
    store = UndoJournal(tmp_path / "bounded", max_entries=5)
    monkeypatch.setattr(undo_mod, "_journal", store)
    for index in range(8):
        target = tmp_path / f"file{index}.txt"
        from core.tool_registry import tool_registry

        tool_registry.load(force=True)
        assert tool_registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True

    entries = store.list(50)
    assert len(entries) == 5
    assert entries[0]["targets"][0].endswith("file7.txt")


def test_journal_entry_shape_is_what_the_product_shows(journal, registry, tmp_path):
    target = tmp_path / "shown.txt"
    assert registry.execute("file_write", {"path": str(target), "content": "x"})["success"] is True
    entry = last_entry(journal)
    assert set(entry) >= {"id", "created_at", "tool", "kind", "description", "targets", "reversible", "state"}
    assert entry["kind"] == "file_write"
    assert entry["state"] == "applied"
    assert entry["reversible"] is True
    assert "shown.txt" in entry["targets"][0]


def test_unknown_mutating_actions_are_not_journaled_as_claims(registry, journal):
    result = registry.execute("file_read", {"path": "README.md"})
    assert result.get("error") is None
    assert journal.list(10) == []


def test_a_journal_that_cannot_write_refuses_the_mutation(registry, journal, tmp_path, monkeypatch):
    """CREW rule 2: no mutating code path without an undo record."""

    def broken(self, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(UndoJournal, "begin", broken)
    target = tmp_path / "must-not-exist.txt"

    result = registry.execute("file_write", {"path": str(target), "content": "x"})

    assert result.get("error") == "undo_journal_unavailable"
    assert "disk full" in result.get("reason", "")
    assert not target.exists()
