"""Harness verifier — scores a finished agent run against a task's checks.

A check is a dict with a ``type`` and type-specific fields. Every check may
carry ``required`` (default true): the task succeeds only when **all required
checks pass**; ``score`` is passed/total over all checks.

Check types
-----------
Response checks (against the agent's final text):

* ``substring``       {value}        — value appears in the response (case-insensitive)
* ``not_substring``   {value}        — value does not appear
* ``regex``           {value}        — pattern matches the response (re.I)
* ``length_gt`` / ``length_lt`` {value} — response length comparison
* ``tool_called``     {name}         — a tool whose name contains `name` was called

File checks (paths are relative to the task workspace):

* ``file_exists`` / ``file_not_exists`` {path}
* ``file_contains``   {path, value}  — file text contains value (case-insensitive)
* ``file_regex``      {path, pattern}

Command checks (run with the shell inside the task workspace):

* ``command`` {cmd, expect_exit=0, stdout_contains, stdout_not_contains,
  stdout_regex, timeout=30}

Combinators (nest other checks under ``checks``):

* ``all`` — passes when every nested check passes
* ``any`` — passes when at least one nested check passes
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MISSING = object()


def _json_get(obj: Any, key: str) -> Any:
    """Traverse a dotted key path (``a.b.0.c``) through dicts/lists."""
    cur = obj
    for part in str(key).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a response (fenced, raw, or scanned)."""
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        try:
            return json.loads(m.group(1))
        except Exception:
            continue
    try:
        return json.loads(text)
    except Exception:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\{\[]", text):
        try:
            obj, _ = dec.raw_decode(text[m.start() :])
            if isinstance(obj, (dict, list)):
                return obj
        except Exception:
            continue
    return None


@dataclass
class CheckContext:
    """Everything a check is allowed to look at."""

    workspace: Path
    response: str = ""
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    type: str
    passed: bool
    required: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "passed": self.passed, "required": self.required, "detail": self.detail}


def _ws_path(ctx: CheckContext, raw_path: str) -> Path:
    p = Path(str(raw_path))
    return p if p.is_absolute() else (ctx.workspace / p)


def _run_check(check: dict[str, Any], ctx: CheckContext, prefix: str = "") -> tuple[CheckResult, list[CheckResult]]:
    """Run one check. Returns (own result, flattened nested results)."""
    ctype = str(check.get("type") or "").strip().lower()
    required = bool(check.get("required", True))
    label = f"{prefix}{ctype}" if not prefix else f"{prefix}.{ctype}"
    resp = ctx.response or ""
    nested: list[CheckResult] = []

    try:
        if ctype == "substring":
            ok = str(check.get("value", "")).lower() in resp.lower()
            return CheckResult(label, ok, required, f"substring {check.get('value')!r} {'found' if ok else 'missing'}"), nested
        if ctype == "not_substring":
            ok = str(check.get("value", "")).lower() not in resp.lower()
            return CheckResult(label, ok, required, f"substring {check.get('value')!r} {'absent' if ok else 'present'}"), nested
        if ctype == "regex":
            ok = bool(re.search(str(check.get("value", "")), resp, re.I | re.S))
            return CheckResult(label, ok, required, f"regex {check.get('value')!r} {'matched' if ok else 'no match'}"), nested
        if ctype == "length_gt":
            ok = len(resp) > int(check.get("value", 0))
            return CheckResult(label, ok, required, f"len={len(resp)} > {check.get('value')}"), nested
        if ctype == "length_lt":
            ok = len(resp) < int(check.get("value", 0))
            return CheckResult(label, ok, required, f"len={len(resp)} < {check.get('value')}"), nested
        if ctype == "json":
            key = str(check.get("key", ""))
            value = check.get("value")  # optional: substring match on the extracted value
            obj = _extract_json(resp)
            if obj is None:
                return CheckResult(label, False, required, "no JSON object found in response"), nested
            val = _json_get(obj, key) if key else obj
            if val is _MISSING:
                top = list(obj.keys())[:10] if isinstance(obj, dict) else type(obj).__name__
                return CheckResult(label, False, required, f"key {key!r} missing (top-level: {top})"), nested
            if value is None:
                return CheckResult(label, True, required, f"key {key!r} = {str(val)[:100]!r}"), nested
            ok = str(value).lower() in str(val).lower()
            return CheckResult(label, ok, required, f"key {key!r} = {str(val)[:100]!r} vs {value!r} -> {ok}"), nested
        if ctype == "tool_called":
            needle = str(check.get("name", "")).lower()
            hits = [t for t in ctx.tool_calls if needle in t.lower()]
            return CheckResult(label, bool(hits), required, f"tool {check.get('name')!r} called={bool(hits)}"), nested
        if ctype in ("file_exists", "file_not_exists"):
            p = _ws_path(ctx, check.get("path", ""))
            exists = p.exists()
            ok = exists if ctype == "file_exists" else not exists
            return CheckResult(label, ok, required, f"{p.name} exists={exists}"), nested
        if ctype == "file_contains":
            p = _ws_path(ctx, check.get("path", ""))
            text = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
            needle = str(check.get("value", ""))
            ok = needle.lower() in text.lower()
            return CheckResult(label, ok, required, f"{check.get('path')} contains {needle[:60]!r}={ok}"), nested
        if ctype == "file_regex":
            p = _ws_path(ctx, check.get("path", ""))
            text = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
            ok = bool(re.search(str(check.get("pattern", check.get("value", ""))), text, re.I | re.S))
            return CheckResult(label, ok, required, f"{check.get('path')} regex {'matched' if ok else 'no match'}"), nested
        if ctype == "command":
            cmd = str(check.get("cmd", ""))
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(ctx.workspace),
                capture_output=True,
                text=True,
                timeout=float(check.get("timeout", 30)),
            )
            expect_exit = int(check.get("expect_exit", 0))
            ok = proc.returncode == expect_exit
            detail = f"exit={proc.returncode} (want {expect_exit})"
            if ok and check.get("stdout_contains"):
                ok = str(check["stdout_contains"]).lower() in (proc.stdout or "").lower()
                detail += f"; stdout_contains={ok}"
            if ok and check.get("stdout_not_contains"):
                ok = str(check["stdout_not_contains"]).lower() not in (proc.stdout or "").lower()
                detail += f"; stdout_not_contains={ok}"
            if ok and check.get("stdout_regex"):
                ok = bool(re.search(str(check["stdout_regex"]), proc.stdout or "", re.I | re.S))
                detail += f"; stdout_regex={ok}"
            return CheckResult(label, ok, required, detail), nested
        if ctype in ("all", "any"):
            children = check.get("checks") or []
            if not isinstance(children, list):
                return CheckResult(label, False, required, "combinator 'checks' must be a list"), nested
            results: list[CheckResult] = []
            for i, child in enumerate(children):
                if not isinstance(child, dict):
                    continue
                r, sub = _run_check(child, ctx, prefix=f"{label}[{i}]")
                results.append(r)
                nested.extend([r] + sub)
            if ctype == "all":
                ok = bool(results) and all(r.passed for r in results)
            else:
                ok = any(r.passed for r in results)
            return CheckResult(label, ok, required, f"{ctype} of {len(results)} nested -> {ok}"), nested
        return CheckResult(label, False, required, f"unknown check type {ctype!r}"), nested
    except Exception as e:  # a check that errors counts as failed, never crashes the run
        return CheckResult(label, False, required, f"error: {type(e).__name__}: {e}"), nested


def verify_task(
    task: Any,
    *,
    workspace: Path,
    response: str = "",
    tool_calls: list[str] | None = None,
) -> dict[str, Any]:
    """Verify a finished run. Returns summary + per-check results.

    ``task`` may be a :class:`~core.harness.tasks.Task` or a raw dict with
    ``checks`` (keeps the legacy eval format working).
    """
    checks = task.checks if hasattr(task, "checks") else (task.get("checks") or [])
    ctx = CheckContext(workspace=Path(workspace), response=response or "", tool_calls=list(tool_calls or []))
    results: list[CheckResult] = []
    for check in checks:
        if not isinstance(check, dict):
            results.append(CheckResult("invalid", False, True, "check must be an object"))
            continue
        r, nested = _run_check(check, ctx)
        results.append(r)
        results.extend(nested)
    required = [r for r in results if r.required]
    passed = [r for r in results if r.passed]
    if not results:
        success = False  # a task with no checks proves nothing
    elif required:
        success = all(r.passed for r in required)  # optional checks never fail a task
    else:
        success = all(r.passed for r in results)
    return {
        "success": success,
        "score": round(len(passed) / len(results), 3) if results else 0.0,
        "checks_total": len(results),
        "checks_passed": len(passed),
        "checks_required": len(required),
        "checks": [r.to_dict() for r in results],
    }
