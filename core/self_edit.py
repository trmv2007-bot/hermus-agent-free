"""Self-improvement with a leash: draft, test and propose — never apply.

``hermus improve "<goal>"`` is the one path by which Hermus may change its own
source. It is deliberately built so the interesting part is the boundary, not
the cleverness:

1. **A plan first.** The goal plus a bounded read of this repository goes to the
   model, and what comes back must be a plan: what it will change, why, and the
   exact edits as find/replace pairs. A plan that does not parse, names a file it
   is not allowed to touch, or points its edits at text that is not there, is a
   refusal with the reason — never a best-effort guess.
2. **A worktree, not the live tree.** The edits land in a throwaway ``git
   worktree`` on a new branch, created outside this checkout. The live tree is
   read-only here: the run records ``git status`` before and after so the claim
   "nothing changed where you work" is evidence, not a promise.
3. **The repository's own gates.** Ruff and the affected test files run inside
   that worktree, and their output is attached to the diff.
4. **An approval, not a merge.** The result is a diff, a patch file and a run
   record for the person to accept. This module contains no code path that
   writes to the live tree, commits, pushes, merges or deploys, and no code path
   that edits its own leash.

The protected set is :class:`core.evolution.EvolutionPolicy`'s — the same list
the rest of the product enforces — plus the few paths this module must never
reach: its own file, the undo journal, and every assertion under ``tests/``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Paths a self-edit may not touch, with the reason stated where it is refused.
PROTECTED_PATHS: dict[str, str] = {
    "RED_LINES.md": "the red lines are the person's brakes, not Hermus's to rewrite",
    "core/permissions.py": "the approval logic decides what Hermus may do; self-approval is Red Line 7",
    "core/safety_policy.py": "the safety policy decides what counts as risky; self-approval is Red Line 7",
    "core/self_edit.py": "this is the leash itself; a self-edit may not shorten it",
    "core/undo.py": "the undo journal is the person's recovery path; Hermus may not edit it",
    "core/evolution.py": "the independent control layer may not be rewritten by the process it controls",
}

#: Every assertion in the suite is off limits, whatever its name.
PROTECTED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("tests/", "the tests are the evidence this change is judged by; weakening them is faking the evidence"),
)

#: A gate may not run longer than this; the machine is a person's PC, not a CI runner.
GATE_TIMEOUT_SECONDS = 420.0

#: The model is asked for one bounded answer; this is how much repository context
#: it is given to work from. Kept small on purpose: the local models this ships
#: with answer a 3k-character prompt inside the provider timeout and a 6k one
#: does not, and a plan needs a real file's text, not the whole repository.
CONTEXT_CHARS = 3000


class PlanError(Exception):
    """A proposal that cannot be trusted as written."""


class ModelUnavailableError(Exception):
    """The model did not answer, so there is no plan to judge.

    Deliberately not a :class:`PlanError`: a provider timeout is not a refused
    proposal, and reporting it as one would put the blame on the plan.
    """


@dataclass
class Edit:
    path: str
    find: str
    replace: str


@dataclass
class Plan:
    summary: str
    why: str = ""
    edits: list[Edit] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "why": self.why,
            "edits": [{"path": e.path, "find": e.find, "replace": e.replace} for e in self.edits],
            "checks": list(self.checks),
        }


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------
def refusal_for(path: str) -> str | None:
    """Why this path may not be edited by a self-edit, or ``None`` when it may."""
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return "no path given"
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return "absolute paths are refused; a self-edit works on this repository only"
    if ".." in Path(normalized).parts:
        return "paths outside the repository are refused"
    for exact, reason in PROTECTED_PATHS.items():
        if normalized == exact:
            return reason
    for prefix, reason in PROTECTED_PREFIXES:
        if normalized.startswith(prefix):
            return reason
    try:
        from .evolution import EvolutionPolicy

        if EvolutionPolicy().protected_files([normalized]):
            return "it is part of the independent control plane (core/evolution.py's protected list)"
    except Exception:  # noqa: BLE001 - a policy that cannot load must not open the gate
        return "the protected-path policy could not be read, so every path is refused"
    return None


def check_refusals(paths: list[str]) -> list[dict[str, str]]:
    """Every path in ``paths`` that a self-edit must refuse, with its reason."""
    out: list[dict[str, str]] = []
    for path in paths:
        reason = refusal_for(path)
        if reason:
            out.append({"path": str(path), "reason": reason})
    return out


# ---------------------------------------------------------------------------
# Reading the repository, and the plan
# ---------------------------------------------------------------------------
def repo_root(start: str | Path | None = None) -> Path:
    """The checkout this run is allowed to read, resolved through git itself."""
    base = Path(start or Path.cwd())
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlanError(f"git is not usable here: {type(exc).__name__}: {exc}") from exc
    if result.returncode != 0:
        raise PlanError(f"not inside a git checkout: {result.stderr.strip()[:200]}")
    return Path(result.stdout.strip())


def _git(repo: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def repo_context(repo: Path, goal: str, limit: int = CONTEXT_CHARS) -> str:
    """A bounded read of this repository, aimed at the goal's own words.

    The model gets the layout, the files whose names match the goal, and the
    head of each — enough to name a real file and quote real text, which is what
    a find/replace plan needs. It is never the whole repository.
    """
    parts: list[str] = []
    status = _git(repo, "status", "--porcelain")
    if status.returncode == 0:
        dirty = [line for line in status.stdout.splitlines() if line.strip()][:10]
        parts.append("Uncommitted work in the checkout (do not touch it):\n" + ("\n".join(dirty) or "(none)"))
    parts.append("Top level:\n" + ", ".join(sorted(p.name for p in repo.iterdir() if not p.name.startswith("."))[:40]))

    words = [w for w in re.findall(r"[a-z0-9]{4,}", goal.lower())]
    candidates: list[tuple[int, Path]] = []
    # Root files count: a goal about the quickstart or a top-level script is as
    # real as one about core/, and a plan that never saw the file cannot quote it.
    scan = [*sorted(repo.glob("*.md")), *sorted(repo.glob("*.py"))]
    for folder in ("core", "tools", "hermus_cli", "gateway", "docs"):
        root = repo / folder
        if root.is_dir():
            scan.extend(sorted(root.rglob("*.md" if folder == "docs" else "*.py")))
    for path in scan:
        if "node_modules" in path.parts:
            continue
        score = sum(1 for w in words if w in path.as_posix().lower())
        if score:
            candidates.append((score, path))
    candidates.sort(key=lambda item: (-item[0], item[1].as_posix()))
    for _score, path in candidates[:3]:
        try:
            excerpt = _file_excerpt(path, words)
        except OSError:
            continue
        parts.append(f"--- {path.relative_to(repo).as_posix()} ---\n{excerpt}")
    text = "\n\n".join(parts)
    return text[:limit]


def _file_excerpt(path: Path, words: list[str], budget: int = 900) -> str:
    """The head of a file plus the lines the goal actually points at.

    A find/replace plan has to quote text that exists, and the text worth
    changing is usually past the first screenful. So the excerpt is the head,
    then a little window around each early line that mentions the goal.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= budget:
        return text
    lines = text.splitlines()
    picked: list[int] = []
    for index, line in enumerate(lines):
        if any(word in line.lower() for word in words):
            picked.append(index)
        if len(picked) >= 3:
            break
    body = [text[:500]]
    seen: set[int] = set()
    for index in picked:
        window = range(max(0, index - 5), min(len(lines), index + 6))
        chunk = [f"{n + 1}: {lines[n]}" for n in window if n not in seen]
        seen.update(window)
        if chunk:
            body.append("…\n" + "\n".join(chunk))
    return "\n".join(body)[:budget]


PLAN_INSTRUCTIONS = """You are Hermus editing your own source code. Reply with ONLY a JSON object, no prose, shaped:
{"summary": "one line", "why": "one line", "edits": [{"path": "relative/path.py", "find": "exact existing text", "replace": "replacement text"}], "checks": ["pytest -q tests/test_x.py"]}
Rules: every "find" must be text that exists exactly in the file; keep edits small; do not edit tests, RED_LINES.md, core/permissions.py, core/safety_policy.py or core/undo.py; never add a paid service."""


def parse_plan(text: str) -> Plan:
    """A plan from the model's answer, or :class:`PlanError` saying why not."""
    raw = (text or "").strip()
    if not raw:
        raise PlanError("the model returned nothing")
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise PlanError(f"no JSON object in the answer: {raw[:200]!r}")
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError as exc:
        raise PlanError(f"the answer was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PlanError("the plan is not a JSON object")
    edits_raw = data.get("edits")
    if not isinstance(edits_raw, list) or not edits_raw:
        raise PlanError("the plan has no edits")
    edits: list[Edit] = []
    for item in edits_raw[:12]:
        if not isinstance(item, dict):
            raise PlanError("an edit is not an object")
        path = str(item.get("path") or "").strip()
        find = item.get("find")
        replace = item.get("replace")
        if not path or not isinstance(find, str) or not isinstance(replace, str) or not find:
            raise PlanError(f"an edit is missing path/find/replace: {item!r}"[:300])
        edits.append(Edit(path=path.replace("\\", "/"), find=find, replace=replace))
    refused = check_refusals([e.path for e in edits])
    if refused:
        raise PlanError("; ".join(f"{r['path']}: {r['reason']}" for r in refused))
    checks = [str(c) for c in (data.get("checks") or []) if isinstance(c, str)]
    return Plan(summary=str(data.get("summary") or "")[:300], why=str(data.get("why") or "")[:300], edits=edits, checks=checks)


def propose(goal: str, *, repo: Path | None = None, model: str | None = None, ask: Callable[[str], str] | None = None) -> Plan:
    """Ask the model for a plan for ``goal`` against this repository."""
    repo = repo or repo_root()
    context = repo_context(repo, goal)
    prompt = f"{PLAN_INSTRUCTIONS}\n\nRepository context:\n{context}\n\nGoal: {goal}\nJSON now."
    if ask is None:
        from .config import config
        from .harness import governed_call
        from .llm import FreeLLM

        llm = FreeLLM(model=model or config.model)

        def ask(text: str) -> str:  # noqa: F811 - the real transport, defined late
            # A planning call is still a model turn: it goes through the
            # harness so the budget and the event log apply to it like any other.
            messages = governed_call([{"role": "user", "content": text}], session_id="self_edit.propose", model=model or "")
            answer = str(llm.chat(messages).content or "")
            # FreeLLM reports transport failures as text rather than raising; a
            # provider that never answered produced no plan to judge.
            if answer.startswith(("Ollama error", "Provider error", "HuggingFace error", "No provider")):
                raise ModelUnavailableError(answer[:300])
            return answer

    return parse_plan(ask(prompt))


# ---------------------------------------------------------------------------
# Running a plan inside a worktree
# ---------------------------------------------------------------------------
def _worktree_base() -> Path:
    """Where worktrees live: outside this checkout, never the product's home."""
    override = os.environ.get("HERMUS_SELF_EDIT_WORKTREES", "").strip()
    base = Path(override).expanduser() if override else Path(os.environ.get("TEMP") or "/tmp") / "hermus-self-edit"
    home = Path.home() / ".hermus"
    try:
        base.resolve().relative_to(home.resolve())
    except (ValueError, OSError):
        return base
    raise PlanError(f"{base} is inside {home}; a self-edit never builds where the product lives")


def records_dir() -> Path:
    override = os.environ.get("HERMUS_SELF_EDIT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    from .config import config

    return config.resolve_path("data/self_edits")


def _safe_gate(command: str) -> list[str] | None:
    """Turn a plan's check into an argv this module is willing to run.

    The plan is model output, so a check is only run when it is a plain
    ``pytest`` invocation with file/test arguments and no shell metacharacters.
    Anything else is reported as skipped rather than handed to a shell.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    if argv[0] in ("python", "python3") and argv[1:3] == ["-m", "pytest"]:
        argv = argv[2:]
    if not argv or argv[0] != "pytest":
        return None
    for arg in argv[1:]:
        if not re.match(r"^[A-Za-z0-9_\-./:=]+$", arg):
            return None
    return [sys.executable, "-m", "pytest", *argv[1:]]


def default_gates(repo: Path, plan: Plan) -> list[list[str]]:
    """The repository's own gates for the files this plan touches."""
    py_files = sorted({e.path for e in plan.edits if e.path.endswith(".py")})
    gates: list[list[str]] = []
    if py_files:
        gates.append([sys.executable, "-m", "ruff", "check", *py_files])
        gates.append([sys.executable, "-m", "ruff", "format", "--check", *py_files])
    tests: list[str] = []
    for path in py_files:
        stem = Path(path).stem
        for candidate in (repo / "tests" / f"test_{stem}.py",):
            if candidate.exists() and candidate.relative_to(repo).as_posix() not in tests:
                tests.append(candidate.relative_to(repo).as_posix())
    for command in plan.checks:
        argv = _safe_gate(command)
        if argv:
            gates.append(argv)
    if tests:
        gates.append([sys.executable, "-m", "pytest", "-q", *tests])
    if not gates:
        gates.append([sys.executable, "-m", "pytest", "-q", "tests/test_architecture.py"])
    return gates


def _run(argv: list[str], cwd: Path, timeout: float = GATE_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        done = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"command": " ".join(argv), "ok": False, "code": None, "tail": f"timed out after {timeout:.0f}s"}
    except OSError as exc:
        return {"command": " ".join(argv), "ok": False, "code": None, "tail": f"{type(exc).__name__}: {exc}"}
    output = (done.stdout or "") + (done.stderr or "")
    return {
        "command": " ".join(argv),
        "ok": done.returncode == 0,
        "code": done.returncode,
        "tail": output.strip()[-600:],
    }


def _apply_edits(worktree: Path, plan: Plan) -> list[str]:
    """Write the plan's edits inside the worktree; returns what was written."""
    written: list[str] = []
    for edit in plan.edits:
        reason = refusal_for(edit.path)
        if reason:
            raise PlanError(f"{edit.path}: {reason}")
        target = (worktree / edit.path).resolve()
        try:
            target.relative_to(worktree.resolve())
        except ValueError as exc:
            raise PlanError(f"{edit.path}: outside the worktree") from exc
        if not target.is_file():
            raise PlanError(f"{edit.path}: no such file in the checkout")
        text = target.read_text(encoding="utf-8")
        occurrences = text.count(edit.find)
        if occurrences == 0:
            raise PlanError(f"{edit.path}: the plan's text is not in the file, so the edit cannot be placed exactly")
        if occurrences > 1:
            raise PlanError(f"{edit.path}: the plan's text appears {occurrences} times, so the edit is ambiguous")
        target.write_text(text.replace(edit.find, edit.replace, 1), encoding="utf-8")
        written.append(edit.path)
    return written


def run(
    goal: str,
    *,
    repo: Path | None = None,
    plan_only: bool = False,
    plan: Plan | None = None,
    ask: Callable[[str], str] | None = None,
    model: str | None = None,
    gates: Callable[[Path, Plan], list[list[str]]] | None = None,
    session_id: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Draft, edit in a worktree, gate, and hand back a diff. Never applies."""
    say = progress or (lambda message: None)
    run_id = f"imp-{uuid.uuid4().hex[:10]}"
    result: dict[str, Any] = {
        "id": run_id,
        "goal": goal,
        "status": "started",
        "plan": None,
        "worktree": None,
        "branch": None,
        "gates": [],
        "diff": "",
        "wrote": [],
        "live_status_before": "",
        "live_status_after": "",
        "live_tree_unchanged": False,
        "error": "",
    }

    try:
        repo = repo or repo_root()
    except PlanError as exc:
        result.update(status="error", error=str(exc))
        return _record(result)

    before = _git(repo, "status", "--porcelain")
    result["live_status_before"] = before.stdout.strip() if before.returncode == 0 else ""

    say("Reading the repository…")
    if plan is None:
        say("Asking the model for a plan…")
        try:
            plan = propose(goal, repo=repo, model=model, ask=ask)
        except PlanError as exc:
            result.update(status="refused", error=str(exc))
            return _finish(result, repo)
        except ModelUnavailableError as exc:
            result.update(status="error", error=f"the model did not answer, so there is no plan yet: {exc}")
            return _finish(result, repo)
        except Exception as exc:  # noqa: BLE001 - a missing model is a stated failure, not a crash
            result.update(status="error", error=f"no plan could be produced: {type(exc).__name__}: {exc}")
            return _finish(result, repo)
    result["plan"] = plan.to_dict()
    say(f"Plan: {plan.summary}")

    if plan_only:
        result["status"] = "planned"
        return _finish(result, repo)

    branch = f"hermus/self-{run_id}"
    try:
        worktree = _worktree_base() / run_id
        worktree.parent.mkdir(parents=True, exist_ok=True)
    except PlanError as exc:
        result.update(status="refused", error=str(exc))
        return _finish(result, repo)
    add = _git(repo, "worktree", "add", "-b", branch, str(worktree), "HEAD")
    if add.returncode != 0:
        result.update(status="error", error=f"could not create the worktree: {add.stderr.strip()[:300]}")
        return _finish(result, repo)
    result["worktree"] = str(worktree)
    result["branch"] = branch
    say(f"Worktree: {worktree} (branch {branch})")

    try:
        result["wrote"] = _apply_edits(worktree, plan)
    except PlanError as exc:
        result.update(status="refused", error=str(exc))
        _remove_worktree(repo, worktree)
        return _finish(result, repo)

    say("Running the repository's gates…")
    gate_plan = gates(worktree, plan) if gates else default_gates(repo, plan)
    for argv in gate_plan:
        gate = _run(argv, worktree)
        result["gates"].append(gate)
        say(f"  {'ok  ' if gate['ok'] else 'FAIL'} {gate['command']}")

    diff = _git(worktree, "diff")
    result["diff"] = diff.stdout
    failed = [g for g in result["gates"] if not g["ok"]]
    result["status"] = "gates_failed" if failed else "proposed"
    result["patch"] = str(_write_patch(run_id, result["diff"]))
    return _finish(result, repo)


def _remove_worktree(repo: Path, worktree: Path) -> None:
    """Take the worktree away after a refused plan. The branch is left alone."""
    try:
        _git(repo, "worktree", "remove", "--force", str(worktree))
    except Exception:  # noqa: BLE001 - leftovers in a temp directory are not worth an exception
        pass


def _finish(result: dict[str, Any], repo: Path) -> dict[str, Any]:
    after = _git(repo, "status", "--porcelain")
    result["live_status_after"] = after.stdout.strip() if after.returncode == 0 else ""
    result["live_tree_unchanged"] = result["live_status_before"] == result["live_status_after"] and after.returncode == 0
    return _record(result)


def _write_patch(run_id: str, diff: str) -> Path | None:
    if not diff:
        return None
    try:
        directory = records_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.patch"
        path.write_text(diff, encoding="utf-8")
        return path
    except OSError:
        return None


def _record(result: dict[str, Any]) -> dict[str, Any]:
    """Keep one JSON record per run so the diagnosis and the person can read it."""
    try:
        directory = records_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{result['id']}.json"
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        result["record"] = str(path)
    except OSError:
        result["record"] = ""
    return result


def last_run() -> dict[str, Any] | None:
    """The newest recorded self-edit run, or ``None``."""
    try:
        files = sorted(records_dir().glob("imp-*.json"), reverse=True)
    except OSError:
        return None
    for path in files[:5]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


__all__ = [
    "Edit",
    "ModelUnavailableError",
    "GATE_TIMEOUT_SECONDS",
    "PROTECTED_PATHS",
    "Plan",
    "PlanError",
    "check_refusals",
    "default_gates",
    "last_run",
    "parse_plan",
    "propose",
    "records_dir",
    "refusal_for",
    "repo_context",
    "repo_root",
    "run",
]
