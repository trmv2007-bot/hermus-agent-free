"""The leash: `hermus improve` may draft, test and propose — and nothing else.

Every test here is about a boundary rather than a feature, because a self-editing
agent is only as safe as its refusals:

* each protected path is refused with a stated reason — the red lines, the
  approval logic, the safety policy, the undo journal, and every assertion under
  ``tests/``;
* a plan that cannot be placed exactly (missing text, ambiguous text, a path
  outside the checkout) is refused instead of guessed at;
* a run leaves the live checkout byte-identical, edits only inside a throwaway
  worktree, and records the diff, the gates and the evidence somewhere the
  person can read.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from core.self_edit import (
    PROTECTED_PATHS,
    Edit,
    Plan,
    PlanError,
    check_refusals,
    parse_plan,
    refusal_for,
    repo_root,
    run,
)

GREET_SOURCE = "def greet():\n    return 'hi'\n"
GREET_TEST = "from core.greet import greet\n\n\ndef test_greet():\n    assert greet() == 'hi'\n"


# ---------------------------------------------------------------------------
# The boundary, path by path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "RED_LINES.md",
        "core/permissions.py",
        "core/safety_policy.py",
        "core/undo.py",
        "core/self_edit.py",
        "core/evolution.py",
        "policies/red_lines.json",
        "tests/test_self_edit.py",
        "tests/test_red_lines_policy.py",
        ".env",
        "core/.env.local",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
        "../outside.py",
    ],
)
def test_protected_paths_are_refused_with_a_reason(path):
    reason = refusal_for(path)
    assert reason, f"{path} was not refused"
    assert len(reason) > 10, f"{path} was refused without a reason"


def test_every_brief_protected_path_is_in_the_refusal_set():
    for path in ("RED_LINES.md", "core/permissions.py", "core/safety_policy.py", "core/undo.py"):
        assert path in PROTECTED_PATHS, f"{path} is missing from the stated refusal set"
    assert refusal_for("tests/test_anything.py"), "the whole tests/ tree must be refused"


def test_ordinary_files_are_allowed():
    for path in ("core/agent.py", "core/self_model.py", "hermus_cli/g_self.py", "docs/NOTES.md"):
        assert refusal_for(path) is None, f"{path} should be editable"


def test_check_refusals_lists_every_bad_path():
    refused = check_refusals(["core/agent.py", "RED_LINES.md", "tests/test_x.py"])
    assert [item["path"] for item in refused] == ["RED_LINES.md", "tests/test_x.py"]
    assert all(item["reason"] for item in refused)


# ---------------------------------------------------------------------------
# Plans that cannot be trusted
# ---------------------------------------------------------------------------
def test_parse_plan_accepts_a_fenced_json_answer():
    text = (
        '```json\n{"summary": "s", "why": "w", "edits": [{"path": "core/a.py", "find": "x", "replace": "y"}], '
        '"checks": ["pytest -q tests/test_a.py"]}\n```'
    )
    plan = parse_plan(text)
    assert plan.summary == "s"
    assert plan.edits[0].path == "core/a.py"
    assert plan.checks == ["pytest -q tests/test_a.py"]


@pytest.mark.parametrize(
    "answer, reason",
    [
        ("", "nothing"),
        ("I would improve the tests.", "no json"),
        ('{"summary": "no edits"}', "no edits"),
        ('{"summary": "s", "edits": [{"path": "core/a.py"}]}', "missing path/find/replace"),
        ('{"summary": "s", "edits": [{"path": "RED_LINES.md", "find": "a", "replace": "b"}]}', "red lines"),
        ('{"summary": "s", "edits": [{"path": "tests/test_x.py", "find": "a", "replace": "b"}]}', "tests"),
    ],
)
def test_a_plan_that_cannot_be_trusted_is_refused_with_the_reason(answer, reason):
    with pytest.raises(PlanError) as error:
        parse_plan(answer)
    assert reason in str(error.value).lower()


# ---------------------------------------------------------------------------
# A whole run, in a temporary checkout
# ---------------------------------------------------------------------------
def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in a test repository with a throwaway identity, never the person's."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "core").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "core" / "greet.py").write_text(GREET_SOURCE, encoding="utf-8")
    (repo / "tests" / "test_greet.py").write_text(GREET_TEST, encoding="utf-8")
    monkeypatch.setenv("HERMUS_SELF_EDIT_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("HERMUS_SELF_EDIT_WORKTREES", str(tmp_path / "worktrees"))
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def _digest(root: pathlib.Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _plan(path="core/greet.py", find="return 'hi'", replace="return 'hello'") -> Plan:
    plan = {"summary": "greet says hello", "why": "clearer", "edits": [{"path": path, "find": find, "replace": replace}]}
    return parse_plan(json.dumps(plan))


def test_a_run_edits_only_a_worktree_and_leaves_the_live_tree_byte_identical(fake_repo):
    before = _digest(fake_repo)
    status_before = git(fake_repo, "status", "--porcelain").stdout

    result = run(
        "say hello instead of hi",
        repo=fake_repo,
        plan=_plan(),
        gates=lambda _wt, _plan: [[sys.executable, "-c", "print('ok')"]],
    )

    assert result["status"] == "proposed", result
    assert result["live_tree_unchanged"] is True
    assert _digest(fake_repo) == before, "the live checkout changed"
    assert git(fake_repo, "status", "--porcelain").stdout == status_before

    worktree = pathlib.Path(result["worktree"])
    assert worktree.is_dir() and fake_repo not in worktree.parents
    assert "return 'hello'" in (worktree / "core" / "greet.py").read_text(encoding="utf-8")
    assert "-    return 'hi'" in result["diff"] and "+    return 'hello'" in result["diff"]
    assert result["record"] and pathlib.Path(result["record"]).exists()
    assert pathlib.Path(result["patch"]).read_text(encoding="utf-8") == result["diff"]

    branches = git(fake_repo, "branch", "--list", "hermus/self-*").stdout
    assert result["branch"] in branches


def test_a_failing_gate_is_reported_with_its_tail(fake_repo):
    result = run(
        "say hello",
        repo=fake_repo,
        plan=_plan(),
        gates=lambda _wt, _plan: [[sys.executable, "-c", "import sys; print('boom'); sys.exit(1)"]],
    )
    assert result["status"] == "gates_failed"
    assert result["gates"][0]["ok"] is False
    assert "boom" in result["gates"][0]["tail"]
    assert result["live_tree_unchanged"] is True


def test_plan_only_stops_before_any_worktree(fake_repo):
    result = run("say hello", repo=fake_repo, plan=_plan(), plan_only=True)
    assert result["status"] == "planned"
    assert result["worktree"] is None
    assert result["live_tree_unchanged"] is True


def test_a_plan_pointing_at_text_that_is_not_there_is_refused(fake_repo):
    result = run("say hello", repo=fake_repo, plan=_plan(find="return 'absent'", replace="return 'hello'"))
    assert result["status"] == "refused"
    assert "not in the file" in result["error"]
    assert result["worktree"] and not pathlib.Path(result["worktree"]).exists(), "a refused plan leaves no worktree"


def test_an_ambiguous_plan_is_refused(fake_repo):
    (fake_repo / "core" / "greet.py").write_text("x = 1\ny = 1\n", encoding="utf-8")
    git(fake_repo, "add", "-A")
    git(fake_repo, "commit", "-qm", "two")
    result = run("change a line", repo=fake_repo, plan=_plan(find="1", replace="2"))
    assert result["status"] == "refused"
    assert "ambiguous" in result["error"]


def test_a_plan_for_a_protected_file_is_refused_before_any_edit(fake_repo):
    plan = Plan(summary="loosen the leash", edits=[Edit(path="RED_LINES.md", find="# Hermus Red Lines", replace="")])
    result = run("loosen", repo=fake_repo, plan=plan)
    assert result["status"] == "refused"
    assert "RED_LINES.md" in result["error"] and "red lines" in result["error"]
    assert not (fake_repo / "RED_LINES.md").exists()
    assert result["live_tree_unchanged"] is True


def test_the_worktree_never_lands_inside_the_products_home(monkeypatch, tmp_path):
    import core.self_edit as self_edit

    monkeypatch.setenv("HERMUS_SELF_EDIT_WORKTREES", str(pathlib.Path.home() / ".hermus" / "wt"))
    with pytest.raises(PlanError):
        self_edit._worktree_base()
    monkeypatch.setenv("HERMUS_SELF_EDIT_WORKTREES", str(tmp_path / "ok"))
    assert self_edit._worktree_base() == tmp_path / "ok"


def test_repo_root_resolves_a_real_checkout(fake_repo):
    assert repo_root(fake_repo) == fake_repo.resolve()


def test_a_plan_cannot_aim_a_gate_outside_the_worktree():
    """A check is model output too: it may name a test, not a path escape."""
    from core.self_edit import _safe_gate

    assert _safe_gate("pytest -q tests/test_cli_dispatch.py") == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_cli_dispatch.py",
    ]
    assert _safe_gate("python -m pytest tests/test_x.py::test_y") is not None
    for refused in (
        "pytest ../outside/test_x.py",
        "pytest /etc/passwd",
        "pytest C:/Windows/System32/test_x.py",
        "pytest -p no:cacheprovider tests/test_x.py",
        "pytest tests/test_x.py; rm -rf /",
        "pytest $(whoami)",
    ):
        assert _safe_gate(refused) is None, f"{refused} was accepted as a gate"
