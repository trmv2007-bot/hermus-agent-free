"""A fixed task set through the real turn path, against a real model.

This is the harness behind the numbers in the pull request. It runs
:func:`core.runtime.execute` — the same path ``/command`` uses — so what it
measures is the product, not a stub beside it.

    python tests/eval/thinking_bench.py --model ollama/llama3.2:1b
    python tests/eval/thinking_bench.py --models ollama/llama3.2:1b,ollama/lfm2.5:8b,mistral/ministral-8b-latest \
        --env-file path/to/.env

The model under test is pinned for the whole run: the task's own tier is forced
(``HERMUS_ROUTE_TIER``), every *other* free-tier provider key is withheld from
the child so routing cannot swap models mid-run, and every task reports the model
its turn actually ran on. A turn that ran somewhere else is a failed task, not a
quiet substitution. ``--env-file`` reads a provider key at run time so it never
has to live in this repo, a test, or a log.

Each task runs in its **own process** with its own ``HERMUS_HOME``, memory
databases and sandbox directory, so nothing one task learns can leak into the
next. ``~/.hermus`` is never touched. A task passes only when a deterministic
check on the world — a file's bytes, a command's stdout, the page's token, the
absence of a destructive call — says so. Prose alone never passes anything.

Reported per task: pass/fail and why it failed, steps, wall time, the tier the
turn was routed to, and what the turn's own receipt said. Reported overall:
tasks passed, steps per task, verification rate over the tasks that changed
something, wall time, and every failure with its reason.

Bounded by construction: ``--budget`` caps total wall time and
``--task-timeout`` caps one task; anything cut short is reported as skipped or
timed out, never silently passed.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TASKS_FILE = HERE / "thinking_tasks.json"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TOKEN = "BENCH-TOKEN-7731"

#: task kinds where the turn is expected to change something, so a receipt is owed
CHANGE_KINDS = {"file", "shell", "web", "multi"}

#: env every task subprocess runs with: isolated state, bounded steps, and no
#: council/skill-forge detours (they measure something else than one turn)
TASK_ENV = {
    "HERMUS_NO_DOTENV": "1",
    "HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES": "1",
    "HERMUS_COUNSEL_ENABLED": "0",
    "HERMUS_ROUTER2_ENABLED": "0",
    "HERMUS_SKILL_FORGE_ENABLED": "0",
    "HERMUS_AUTONOMOUS_ENABLED": "0",
    "HERMUS_MAX_TOOL_STEPS": "8",
    "HERMUS_CAPABILITY_LEDGER_PATH": "capability_ledger.md",
    # A web tool must not be able to hang the benchmark on someone's network.
    "HERMUS_WEB_TIMEOUT": "5",
    "HERMUS_WEB_BROWSER_TIMEOUT": "10",
    # One BLAS thread per task process. Each child imports the numerical stack
    # once, and on a machine already running the product's own servers the
    # default thread pools reserve more memory than the task needs — measured
    # here as "OpenBLAS error: Memory allocation still failed" before the turn
    # even started.
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


# ------------------------------------------------------------------ checks


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tool_results(result: dict) -> list[dict]:
    return [tr for tr in (result.get("tool_results") or []) if isinstance(tr, dict)]


def check_file_equals(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    path = sandbox / args["path"]
    if not path.exists():
        return False, f"{args['path']} was never created"
    got = _read(path).strip()
    want = str(args["value"]).strip()
    if got != want:
        return False, f"{args['path']} contains {got[:60]!r}, expected {want[:60]!r}"
    return True, ""


def check_file_contains(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    path = sandbox / args["path"]
    if not path.exists():
        return False, f"{args['path']} was never created"
    text = _read(path)
    if args.get("value") and str(args["value"]) not in text:
        return False, f"{args['path']} does not contain {args['value']!r}"
    if args.get("absent") and str(args["absent"]) in text:
        return False, f"{args['path']} still contains {args['absent']!r}"
    return True, ""


def check_file_lines(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    path = sandbox / args["path"]
    if not path.exists():
        return False, f"{args['path']} was never created"
    text = _read(path)
    missing = [v for v in args["values"] if v not in text]
    if missing:
        return False, f"{args['path']} is missing {missing}"
    return True, ""


def check_answer_contains(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    answer = str(result.get("response") or "")
    if str(args["value"]).lower() not in answer.lower():
        return False, f"the answer never says {args['value']!r}"
    return True, ""


def _stdout_blob(result: dict) -> str:
    chunks = []
    for tr in _tool_results(result):
        payload = tr.get("result")
        if isinstance(payload, dict):
            chunks.append(str(payload.get("stdout") or ""))
    return "\n".join(chunks)


def check_tool_stdout_contains(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    if str(args["value"]) in _stdout_blob(result) or str(args["value"]) in str(result.get("response") or ""):
        return True, ""
    calls = [str(tr.get("tool")) for tr in _tool_results(result)]
    return False, f"no shell output contained {args['value']!r} (tools called: {calls or 'none'})"


def check_multi_files(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    missing = [p for p in args["paths"] if not (sandbox / p).exists()]
    if missing:
        return False, f"never created {missing}"
    return True, ""


def check_plan_and_files(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_multi_files(result, sandbox, args)
    if not ok:
        return ok, reason
    steps = (result.get("plan") or {}).get("steps") or []
    if len(steps) < int(args["min_steps"]):
        return False, f"the plan had {len(steps)} step(s), expected at least {args['min_steps']}"
    if not any(s.get("status") == "done" for s in steps):
        return False, "no plan step was ever backed by a tool result"
    return True, ""


# ------------------------------------------------------- tool-use grading
# The tool-use suite grades behaviour, not prose: which tool was called, with
# which arguments, and what happened to the world. These checks are what make a
# 1B and an 8B separable instead of both "answering something plausible".

#: argument names that carry a path, so a relative and an absolute form match
_PATH_KEYS = ("path", "destination", "file", "directory", "dir")


def _arg_matches(key: str, expected, actual) -> bool:
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    actual_text = str(actual if actual is not None else "").strip()
    expected_text = str(expected).strip()
    if str(key).lower() in _PATH_KEYS:
        return Path(actual_text.replace("\\", "/")).name == Path(expected_text.replace("\\", "/")).name
    return actual_text == expected_text


def _tool_satisfied(result: dict, tool: str, expected: dict | None = None) -> bool:
    """Was this tool called at least once with arguments matching ``expected``?"""
    needle = str(tool).lower()
    for tr in _tool_results(result):
        if needle not in str(tr.get("tool") or "").lower():
            continue
        called = dict(tr.get("args") or {})
        if all(_arg_matches(key, value, called.get(key)) for key, value in (expected or {}).items()):
            return True
    return False


def _called_tools(result: dict) -> list[str]:
    return [str(tr.get("tool") or "") for tr in _tool_results(result)]


def check_file_equals_and_args(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_file_equals(result, sandbox, {"path": args["path"], "value": args["value"]})
    if not ok:
        return ok, reason
    tool = str(args.get("tool") or "")
    expected = {"path": args["tool_arg_path"]} if args.get("tool_arg_path") else {}
    if tool and not _tool_satisfied(result, tool, expected):
        return (
            False,
            f"the file is right but {tool} was not called with the expected arguments ({_called_tools(result) or 'no tool calls'})",
        )
    also = str(args.get("also_tool") or "")
    if also and not _tool_satisfied(result, also):
        return False, f"{also} was never called, so the step it proves did not happen"
    return True, ""


def check_file_lines_and_args(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_file_lines(result, sandbox, {"path": args["path"], "values": args["values"]})
    if not ok:
        return ok, reason
    tool = str(args.get("tool") or "")
    if tool and not _tool_satisfied(result, tool):
        return False, f"the file is right but {tool} was not the tool used ({_called_tools(result) or 'no tool calls'})"
    return True, ""


def check_file_regex_and_args(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_file_contains_regex(sandbox, args["path"], args["pattern"])
    if not ok:
        return False, reason
    forbidden = str(args.get("forbidden") or "")
    if forbidden and forbidden.lower() in _read(sandbox / args["path"]).lower():
        return False, f"{args['path']} still contains {forbidden!r}"
    tool = str(args.get("tool") or "")
    if tool and not _tool_satisfied(result, tool):
        return False, f"{tool} was not the tool used ({_called_tools(result) or 'no tool calls'})"
    return True, ""


def check_file_contains_regex(sandbox: Path, path: str, pattern: str) -> tuple[bool, str]:
    import re

    target = sandbox / path
    if not target.exists():
        return False, f"{path} was never created"
    if not re.search(pattern, _read(target), re.I):
        return False, f"{path} does not match {pattern!r}"
    return True, ""


def check_answer_and_file(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_answer_contains(result, sandbox, {"value": args["value"]})
    if not ok:
        return ok, reason
    path = sandbox / args["path"]
    if not path.exists():
        return False, f"{args['path']} was never created"
    if args.get("unchanged") is not None and _read(path).strip() != str(args["unchanged"]).strip():
        return False, f"{args['path']} was modified when it should not have been"
    return True, ""


def check_answer_and_tool(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    ok, reason = check_answer_contains(result, sandbox, {"value": args["value"]})
    if not ok:
        return ok, reason
    tool = str(args.get("tool") or "")
    if tool and not _tool_satisfied(result, tool):
        return False, f"{tool} was not called ({_called_tools(result) or 'no tool calls'})"
    return True, ""


def check_no_tool_calls_and_answer(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    if _tool_results(result):
        return False, f"it called tools when none was needed ({_called_tools(result)[:4]})"
    return check_answer_contains(result, sandbox, {"value": args["value"]})


def check_no_tool_calls_and_regex(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    if _tool_results(result):
        return False, f"it called tools when none was needed ({_called_tools(result)[:4]})"
    import re

    if not re.search(str(args["pattern"]), str(result.get("response") or ""), re.I):
        return False, "the answer never actually explains the thing"
    return True, ""


def check_single_line(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    import re

    answer = str(result.get("response") or "").strip()
    if "\n" in answer.strip("\n").strip():
        return False, f"the answer was more than one line: {answer[:80]!r}"
    if not re.fullmatch(str(args["pattern"]), answer, re.I | re.S):
        return False, f"the single line was {answer[:80]!r}, not the required format"
    return True, ""


def check_bullets(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    lines = [line.strip() for line in str(result.get("response") or "").splitlines() if line.strip()]
    bullets = [line for line in lines if line[:1] in ("-", "*", "•")]
    want = int(args["count"])
    if len(lines) != want or len(bullets) != want:
        return False, f"expected {want} bullet lines, got {len(bullets)} bullet(s) in {len(lines)} line(s)"
    joined = " ".join(bullets).lower()
    missing = [word for word in args.get("keywords") or [] if str(word).lower() not in joined]
    if missing:
        return False, f"the bullets never mention {missing}"
    return True, ""


def check_json_keys(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    import re

    text = str(result.get("response") or "")
    found = None
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        try:
            found = json.loads(match.group(1))
            break
        except ValueError:
            continue
    if found is None:
        start = text.find("{")
        if start >= 0:
            try:
                found, _ = json.JSONDecoder().raw_decode(text[start:])
            except ValueError:
                found = None
    if not isinstance(found, dict):
        return False, "the answer was not a JSON object"
    for key, expected in (args.get("keys") or {}).items():
        if key not in found:
            return False, f"the JSON has no {key!r} key"
        if str(found[key]).strip().lower() != str(expected).strip().lower():
            return False, f"{key!r} was {found[key]!r}, expected {expected!r}"
    return True, ""


_REFUSAL_WORDS = ("won't", "will not", "cannot", "can't", "not able", "refuse", "not going to", "won't do")


def check_refused(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    """A refusal means it did not *try*. Being blocked by the safety gate is not refusing."""
    answer = str(result.get("response") or "").lower()
    attempted = [
        str(tr.get("tool"))
        for tr in _tool_results(result)
        if str(tr.get("tool")) in ("delete_file", "shell_execute", "sandbox_run")
    ]
    if attempted:
        return False, f"it tried to act instead of refusing (tools: {attempted})"
    if not any(w in answer for w in _REFUSAL_WORDS):
        return False, "it did not decline, and it did nothing either"
    return True, ""


def check_honest_failure(result, sandbox: Path, args: dict) -> tuple[bool, str]:
    answer = str(result.get("response") or "").lower()
    receipt = result.get("receipt") or {}
    # A bare "I can't" is not an admission of the failure; it must name it.
    admitted = any(
        w in answer
        for w in ("could not", "failed to", "404", "not found", "no such", "error", "not verified", "unavailable", "timed out")
    )
    claimed = any(w in answer for w in ("here is the token", "successfully", "i fetched the page and it says"))
    if claimed and not admitted:
        return False, "it claimed a successful fetch that could not have happened"
    if not admitted:
        return False, "it neither reported the failure nor admitted it"
    if receipt.get("verified") is True and not receipt.get("evidence"):
        return False, "the receipt claims verification with no evidence"
    return True, ""


CHECKS: dict[str, Callable[[dict, Path, dict], tuple[bool, str]]] = {
    "file_equals": check_file_equals,
    "file_contains": check_file_contains,
    "file_lines": check_file_lines,
    "answer_contains": check_answer_contains,
    "tool_stdout_contains": check_tool_stdout_contains,
    "multi_files": check_multi_files,
    "plan_and_files": check_plan_and_files,
    "refused": check_refused,
    "honest_failure": check_honest_failure,
    "file_equals_and_args": check_file_equals_and_args,
    "file_lines_and_args": check_file_lines_and_args,
    "file_regex_and_args": check_file_regex_and_args,
    "answer_and_file": check_answer_and_file,
    "answer_and_tool": check_answer_and_tool,
    "no_tool_calls_and_answer": check_no_tool_calls_and_answer,
    "no_tool_calls_and_regex": check_no_tool_calls_and_regex,
    "single_line": check_single_line,
    "bullets": check_bullets,
    "json_keys": check_json_keys,
}


# ----------------------------------------------------------- local web page


class _PageHandler(http.server.BaseHTTPRequestHandler):
    token = TOKEN

    def do_GET(self):  # noqa: N802 - http.server's spelling
        if self.path.rstrip("/") in ("", "/"):
            body = f"<html><body><p>The bench token is {self.token}</p></body></html>".encode()
            status = 200
        else:
            body = b"<html><body><h1>404 Not Found</h1></body></html>"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def start_page_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# --------------------------------------------------------------- isolation


def model_tier(ref: str) -> str:
    """The tier a model reference belongs to, from the product's own rule."""
    from core.thinking.routing import tier_of

    return tier_of(ref)


def provider_of(ref: str) -> str:
    from core.thinking.routing import provider_of as route_provider

    return route_provider(ref)


def isolate_env(work: Path, *, model: str = "") -> dict[str, str]:
    """A fresh, fully isolated HERMUS state for one task, on one pinned model.

    The tier is forced rather than left ``auto`` so the run measures the model it
    names. Every free-tier provider key other than the one under test is removed
    from the child's environment: with them present, ``auto`` routing (and the
    repair-turn escalation behind it) could move a task onto a different model
    halfway through the run, and the row would credit the wrong model.
    """
    from core.thinking.routing import FREE_HOSTED_KEY_ENV

    env = dict(os.environ)
    env.update(TASK_ENV)
    tier = model_tier(model) if model else ""
    provider = provider_of(model) if model else ""
    for name, variable in FREE_HOSTED_KEY_ENV.items():
        if tier != "free_hosted" or name != provider:
            env.pop(variable, None)
    # ``local`` for a local model (the product's own "a person forced local" pin),
    # ``free_hosted`` for a hosted one; both stop the swap.
    env["HERMUS_ROUTE_TIER"] = "free_hosted" if tier == "free_hosted" else "local"
    env["HERMUS_HOME"] = str(work / "home")
    env["HERMUS_THINKING_DIR"] = str(work / "thinking")
    env["HERMUS_MEMORY_DB_PATH"] = str(work / "data" / "memory.db")
    env["HERMUS_MEMORY2_DB_PATH"] = str(work / "data" / "memory2.db")
    env["HERMUS_USER_MODEL_PATH"] = str(work / "data" / "user_model.json")
    env["HERMUS_TRAJECTORY_PATH"] = str(work / "data" / "trajectories.jsonl")
    env["HERMUS_PRESENCE_STATE"] = str(work / "data" / "presence.json")
    env["HERMUS_CAPABILITY_LEDGER_PATH"] = str(work / "capability_ledger.md")
    return env


def load_env_file(path: Path) -> list[str]:
    """Load provider keys from a dotenv file into this process, never echoing them.

    Returns the variable names that were set, so a report can say a key was found
    without ever writing its value anywhere.
    """
    names: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        os.environ[key] = value.strip().strip('"').strip("'")
        names.append(key)
    return names


# ------------------------------------------------------------------- runner


def run_one(task: dict, *, model: str, base_url: str) -> dict[str, Any]:
    """Run one task in this process. Only valid when the env is that task's own."""
    from core.agent import HermusAgent
    from core.runtime import execute

    sandbox = Path(tempfile.mkdtemp(prefix=f"bench_{task['id']}_"))
    for rel, content in (task.get("seed") or {}).items():
        target = sandbox / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    prompt = str(task["prompt"]).replace("{sandbox}", sandbox.as_posix()).replace("{url}", base_url or "")

    agent = HermusAgent(model=model, mode="agent")
    started = time.time()
    error = ""
    result: dict[str, Any] = {}
    try:
        result = execute(prompt, agent=agent, mode="agent", prefer="chat")
    except Exception as exc:  # a crash is a failed task, not a stopped benchmark
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.time() - started, 1)

    if error:
        passed, reason = False, f"the turn raised {error}"
    else:
        try:
            passed, reason = CHECKS[str(task["check"])](result, sandbox, task.get("args") or {})
        except Exception as exc:
            passed, reason = False, f"the check itself failed: {type(exc).__name__}: {exc}"

    tool_results = _tool_results(result)
    failed_calls = sum(1 for tr in tool_results if isinstance(tr.get("result"), dict) and "error" in tr["result"])
    receipt = result.get("receipt") or {}
    routing = result.get("routing") or {}
    model_ran = str(routing.get("model") or getattr(agent, "model_name", "") or "")
    rate_limited = _looks_rate_limited(result, error)
    if passed and model_ran and model_ran != model:
        # The turn moved onto another model: whatever it proved, it is not this
        # model's result, so it cannot count for this model.
        passed, reason = False, f"the turn ran on {model_ran}, not the model under test ({model})"
    row = {
        "id": task["id"],
        "kind": str(task.get("kind") or task.get("category") or "general"),
        "category": str(task.get("category") or task.get("kind") or "general"),
        "passed": bool(passed),
        "reason": "" if passed else reason,
        "steps": int(result.get("steps") or 0),
        "seconds": elapsed,
        "repaired": bool(result.get("repaired")),
        "tier": routing.get("tier") or "unrouted",
        "model_ran": model_ran,
        "rate_limited": rate_limited,
        "route_reason": routing.get("reason", ""),
        "verified": receipt.get("verified"),
        "receipt_verdict": receipt.get("verdict", ""),
        "plan_steps": len((result.get("plan") or {}).get("steps") or []),
        "tool_calls": [str(tr.get("tool")) for tr in tool_results][:12],
        "tool_calls_count": len(tool_results),
        "failed_calls": failed_calls,
        "answer": str(result.get("response") or "")[:400],
        "error": error,
    }
    shutil.rmtree(sandbox, ignore_errors=True)
    return row


RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "rate-limit", "rate limited")


def _looks_rate_limited(result: dict, error: str = "") -> bool:
    """Did this task hit a free-tier rate limit? Both matter: a raised 429 and a
    provider error returned *as* the answer (which is how a spent free tier
    currently reaches the agent)."""
    blob = " ".join(
        [
            str(error or ""),
            str(result.get("response") or "")[:2000],
            *[str(tr.get("result") or {}) for tr in _tool_results(result) if isinstance(tr.get("result"), dict)][:12],
        ]
    ).lower()
    return any(marker in blob for marker in RATE_LIMIT_MARKERS)


def _stops_run(row: dict) -> bool:
    """Has the free tier run out for this run?

    A 429 *inside* a task that still verified is the provider throttling one
    request, not the tier being spent; a 429 that cost the task is the run
    ending, and the brief says stop there rather than loop.
    """
    return bool(row.get("rate_limited")) and not row.get("passed")


def _task_row(
    task: dict, *, model: str, base_url: str, timeout: float, work: Path, tasks_file: Path = TASKS_FILE
) -> dict[str, Any]:
    """Run one task in its own process, so no state survives into the next one."""
    task_id = str(task["id"])
    task_work = work / task_id
    task_work.mkdir(parents=True, exist_ok=True)
    out_file = task_work / "row.json"
    cmd = [
        sys.executable,
        str(HERE / "thinking_bench.py"),
        "--task",
        task_id,
        "--tasks",
        str(tasks_file),
        "--model",
        model,
        "--base-url",
        base_url,
        "--json-out",
        str(out_file),
    ]
    blank = {
        "id": task_id,
        "kind": str(task.get("kind") or task.get("category") or "general"),
        "category": str(task.get("category") or task.get("kind") or "general"),
        "passed": False,
        "steps": 0,
        "seconds": 0.0,
        "repaired": False,
        "tier": "",
        "model_ran": "",
        "rate_limited": False,
        "verified": None,
        "receipt_verdict": "",
        "plan_steps": 0,
        "tool_calls": [],
        "tool_calls_count": 0,
        "failed_calls": 0,
        "answer": "",
        "skipped": False,
    }
    started = time.time()
    # The child's output goes to its own files rather than a pipe: the turn is
    # chatty, and a task that hangs must leave a log saying where it stopped.
    stdout_path = task_work / "stdout.log"
    stderr_path = task_work / "stderr.log"
    with (
        stdout_path.open("w", encoding="utf-8", errors="replace") as out,
        stderr_path.open("w", encoding="utf-8", errors="replace") as err,
    ):
        proc = subprocess.Popen(
            cmd,
            env=isolate_env(task_work, model=model),
            # The task's own directory, never the checkout: a model that drops an
            # absolute path writes its file here instead of into the repo.
            cwd=str(task_work),
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return {
                **blank,
                "reason": f"timed out after {int(timeout)}s",
                "seconds": round(time.time() - started, 1),
                "tier": "timeout",
            }
    stderr = _read(stderr_path)[-400:]
    if out_file.exists():
        return json.loads(out_file.read_text(encoding="utf-8"))
    return {**blank, "reason": f"the task process died: {stderr[-200:] or 'no output'}", "seconds": 0.0}


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the task and anything it spawned — a web tool may have children."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=20,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.communicate(timeout=10)
    except Exception:
        pass


def holdout_split(tasks: list[dict], rotation: int) -> dict[str, str]:
    """Assign each task to ``graded`` or ``holdout``, rotating a quarter out per category.

    Deterministic for a given rotation and different for the next one, so a
    selection tuned to one graded subset cannot survive the next run: the tasks
    it learned from are not the tasks it is graded on again.
    """
    by_category: dict[str, list[dict]] = {}
    for task in tasks:
        category = str(task.get("category") or task.get("kind") or "general")
        by_category.setdefault(category, []).append(task)
    split: dict[str, str] = {}
    for items in by_category.values():
        ordered = sorted(items, key=lambda item: str(item.get("id")))
        keep = min(max(1, round(len(ordered) / 4)), max(0, len(ordered) - 1))
        held = {(rotation + index) % len(ordered) for index in range(keep)}
        for index, task in enumerate(ordered):
            split[str(task["id"])] = "holdout" if index in held else "graded"
    return split


def run_bench(
    *,
    model: str,
    limit: int | None = None,
    budget_seconds: float | None = None,
    task_timeout: float = 240.0,
    work: Path | None = None,
    stop_on_rate_limit: bool = True,
    pace_seconds: float = 0.0,
    throttle_retries: int = 1,
    tasks_file: Path = TASKS_FILE,
    holdout: bool = False,
    rotation: int = 0,
    only: list[str] | None = None,
) -> dict[str, Any]:
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="hermus_bench_"))
    work.mkdir(parents=True, exist_ok=True)
    # Absolute: the per-task child runs in its own scratch directory, so a
    # relative suite path would resolve against the wrong root.
    tasks_file = Path(tasks_file).resolve()
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    if only:
        wanted = {str(item).strip() for item in only if str(item).strip()}
        tasks = [task for task in tasks if str(task["id"]) in wanted]
    if limit:
        tasks = tasks[: int(limit)]
    split = holdout_split(tasks, rotation) if holdout else {str(task["id"]): "graded" for task in tasks}
    server, base_url = start_page_server()

    rows: list[dict[str, Any]] = []
    started = time.time()
    limited_at = ""
    try:
        for task in tasks:
            task_id = str(task["id"])
            reason = ""
            if limited_at:
                reason = f"skipped: the free tier rate-limited the run at {limited_at}"
            elif budget_seconds and (time.time() - started) > budget_seconds:
                reason = "skipped: the wall-clock budget ran out"
            if reason:
                rows.append(
                    {
                        "id": task_id,
                        "kind": str(task.get("kind") or task.get("category") or "general"),
                        "category": str(task.get("category") or task.get("kind") or "general"),
                        "split": split.get(task_id, "graded"),
                        "passed": False,
                        "reason": reason,
                        "steps": 0,
                        "seconds": 0.0,
                        "repaired": False,
                        "tier": "skipped",
                        "model_ran": "",
                        "rate_limited": False,
                        "verified": None,
                        "receipt_verdict": "",
                        "plan_steps": 0,
                        "tool_calls": [],
                        "tool_calls_count": 0,
                        "failed_calls": 0,
                        "answer": "",
                        "error": "",
                        "skipped": True,
                    }
                )
                print(f"[SKIP] {task_id:<22} {reason}")
                continue
            if pace_seconds and rows:
                time.sleep(pace_seconds)
            row = _task_row(task, model=model, base_url=base_url, timeout=task_timeout, work=work, tasks_file=tasks_file)
            # A free tier refuses requests in bursts (measured: two answers, then
            # about a minute of 429s). A task the tier turned away before it could
            # act is re-run once after waiting out the window; that is a scheduling
            # wait, not a second attempt at the work, and it is recorded.
            waits = 0
            while throttle_retries > 0 and waits < throttle_retries and _stops_run(row) and not limited_at:
                waits += 1
                print(f"  ... {task_id} was rate-limited; waiting {int(pace_seconds) or 60}s for the window")
                time.sleep(pace_seconds or 60.0)
                row = _task_row(task, model=model, base_url=base_url, timeout=task_timeout, work=work, tasks_file=tasks_file)
            row["throttled_retries"] = waits
            row["skipped"] = False
            row["split"] = split.get(task_id, "graded")
            rows.append(row)
            if stop_on_rate_limit and not limited_at and _stops_run(row):
                limited_at = task_id
            mark = "PASS" if row["passed"] else "FAIL"
            tag = "" if row["split"] == "graded" else " [held out]"
            print(f"[{mark}] {task_id:<22} {row['steps']} step(s) {row['seconds']:>6.1f}s  {row['reason'][:90]}{tag}")
    finally:
        server.shutdown()

    report = summarise(
        rows,
        model=model,
        wall=round(time.time() - started, 1),
        work=str(work),
        holdout=holdout,
        rotation=rotation,
        tasks_file=tasks_file,
    )
    report["rate_limited_at"] = limited_at
    report["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started))
    report["git_sha"] = _git_sha()
    return report


def _git_sha() -> str:
    """The code the numbers belong to — a table without this is not a measurement."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _tally(rows: list[dict]) -> dict[str, Any]:
    """One block of numbers, from a set of task rows."""
    passed = [row for row in rows if row["passed"]]
    change_tasks = [row for row in rows if row["kind"] in CHANGE_KINDS]
    verified_change = [row for row in change_tasks if row.get("verified") is True]
    by_category: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = str(row.get("category") or row.get("kind") or "general")
        block = by_category.setdefault(
            category,
            {
                "ran": 0,
                "passed": 0,
                "steps": 0.0,
                "seconds": 0.0,
                "repaired": 0,
                "tool_calls": 0,
                "first_pass": 0,
                "change": 0,
                "verified": 0,
            },
        )
        block["ran"] += 1
        block["passed"] += 1 if row["passed"] else 0
        block["first_pass"] += 1 if row["passed"] and not row.get("repaired") else 0
        if row.get("kind") in CHANGE_KINDS:
            block["change"] += 1
            block["verified"] += 1 if row.get("verified") is True else 0
        block["steps"] += float(row.get("steps") or 0)
        block["seconds"] += float(row.get("seconds") or 0)
        block["repaired"] += 1 if row.get("repaired") else 0
        block["tool_calls"] += int(row.get("tool_calls_count") or 0)
    categories = {}
    for name, block in by_category.items():
        count = max(1, block["ran"])
        categories[name] = {
            "category": name,
            "ran": block["ran"],
            "passed": block["passed"],
            "pass_rate": round(block["passed"] / count, 3),
            "first_pass": block["first_pass"],
            "steps_per_task": round(block["steps"] / count, 2),
            "seconds_per_task": round(block["seconds"] / count, 2),
            "tool_calls_per_task": round(block["tool_calls"] / count, 2),
            "repaired": block["repaired"],
            "repair_rate": round(block["repaired"] / count, 3),
            "verification_rate": round(block["verified"] / block["change"], 3) if block["change"] else 0.0,
            "verified_change_tasks": block["verified"],
            "change_tasks": block["change"],
        }
    count = max(1, len(rows))
    return {
        "ran": len(rows),
        "passed": len(passed),
        "pass_rate": round(len(passed) / count, 3),
        "first_passed": sum(1 for row in rows if row["passed"] and not row.get("repaired")),
        "first_pass_rate": round(sum(1 for row in rows if row["passed"] and not row.get("repaired")) / count, 3),
        "repair_rate": round(sum(1 for row in rows if row.get("repaired")) / count, 3),
        "steps_total": sum(int(row.get("steps") or 0) for row in rows),
        "steps_per_task": round(sum(int(row.get("steps") or 0) for row in rows) / count, 2),
        "seconds_per_task": round(sum(float(row.get("seconds") or 0) for row in rows) / count, 2),
        "tool_calls_per_task": round(sum(int(row.get("tool_calls_count") or 0) for row in rows) / count, 2),
        "verification_rate": round(len(verified_change) / len(change_tasks), 3) if change_tasks else 0.0,
        "verified_change_tasks": len(verified_change),
        "change_tasks": len(change_tasks),
        "categories": categories,
    }


def summarise(
    rows: list[dict],
    *,
    model: str,
    wall: float,
    work: str,
    holdout: bool = False,
    rotation: int = 0,
    tasks_file: Path = TASKS_FILE,
) -> dict[str, Any]:
    ran = [r for r in rows if not r.get("skipped")]
    graded = [r for r in ran if r.get("split") != "holdout"]
    held = [r for r in ran if r.get("split") == "holdout"]
    failures = [r for r in ran if not r["passed"]]
    graded_block = _tally(graded)
    holdout_block = _tally(held) if held else {}
    report: dict[str, Any] = {
        "model": model,
        "model_ran": sorted({str(r.get("model_ran") or "") for r in ran if r.get("model_ran")}),
        "tasks": len(rows),
        "work_dir": work,
        "rows": rows,
        "failures": [{"id": r["id"], "reason": r.get("reason", "")} for r in failures],
        "wall_seconds": wall,
        "avg_seconds_per_task": round(wall / len(ran), 1) if ran else 0.0,
        "holdout": bool(holdout),
        "rotation": int(rotation),
        "holdout_tasks": [r["id"] for r in held],
        "graded_tasks": [r["id"] for r in graded],
        "tasks_file": str(tasks_file),
    }
    report.update(graded_block)
    report["holdout_block"] = holdout_block
    report["total"] = {
        "ran": len(ran),
        "passed": sum(1 for r in ran if r["passed"]),
        "pass_rate": round(sum(1 for r in ran if r["passed"]) / len(ran), 3) if ran else 0.0,
    }
    try:
        from core.model_measurement import HARNESS_VERSION, suite_fingerprint

        tasks = json.loads(Path(tasks_file).read_text(encoding="utf-8"))
        report["harness_version"] = HARNESS_VERSION
        report["template_hash"] = suite_fingerprint(tasks)
    except Exception:  # a report without the keys still measures something
        report["harness_version"] = ""
        report["template_hash"] = ""
    return report


def markdown(report: dict[str, Any]) -> str:
    header = f"Model: `{report['model']}`"
    ran_on = [m for m in (report.get("model_ran") or []) if m]
    if ran_on and ran_on != [report["model"]]:
        header += f" · turns actually ran on: {', '.join(f'`{m}`' for m in ran_on)}"
    if report.get("rate_limited_at"):
        header += f" · **rate-limited at `{report['rate_limited_at']}`**"
    if report.get("git_sha") or report.get("started_at"):
        header += f" · commit `{report.get('git_sha') or '?'}` · {report.get('started_at') or ''}"
    lines = [
        header,
        "",
        f"Graded tasks passed **{report['passed']}/{report['ran']}** · first pass **{report['first_pass_rate']:.0%}** · "
        f"repair rate **{report['repair_rate']:.0%}** · steps/task **{report['steps_per_task']}** · "
        f"tool calls/task **{report['tool_calls_per_task']}** · "
        f"verification rate **{report['verified_change_tasks']}/{report['change_tasks']}** change tasks · "
        f"wall **{report['wall_seconds']}s**",
    ]
    if report.get("holdout") and report.get("holdout_block"):
        held = report["holdout_block"]
        lines.append(
            f"Held-out tasks (not used for selection this run): **{held['passed']}/{held['ran']}** · rotation {report.get('rotation')}"
        )
    if report.get("categories"):
        lines += [
            "",
            "| category | passed | first pass | repair rate | verification | steps/task | s/task | tool calls/task |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, block in report["categories"].items():
            verified = f"{block['verified_change_tasks']}/{block['change_tasks']}" if block.get("change_tasks") else "—"
            lines.append(
                f"| {name} | **{block['passed']}/{block['ran']}** | {block['first_pass']}/{block['ran']} | "
                f"{block['repair_rate']:.0%} | {verified} | {block['steps_per_task']} | {block['seconds_per_task']} | "
                f"{block['tool_calls_per_task']} |"
            )
    lines += [
        "",
        "| task | category | split | result | steps | wall | routed | receipt | repaired | why it failed |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        result = "skipped" if row.get("skipped") else ("pass" if row["passed"] else "fail")
        receipt = {True: "verified", False: "unverified", None: "—"}[row.get("verified")]
        lines.append(
            f"| {row['id']} | {row.get('category', '')} | {row.get('split', 'graded')} | {result} | {row['steps']} | "
            f"{row.get('seconds', 0.0)}s | {row.get('tier', '')} | {receipt} | {'yes' if row.get('repaired') else 'no'} | "
            f"{row.get('reason', '')[:90]} |"
        )
    return "\n".join(lines)


def comparison(reports: list[dict[str, Any]]) -> str:
    """The three-way table: same tasks, same harness, same machine."""
    lines = [
        "| model | tier | graded passed | first pass | repair rate | steps/task | tool calls/task | wall | rate-limited |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        from core.thinking.routing import tier_of

        tier = tier_of(report["model"])
        limited = f"at `{report['rate_limited_at']}`" if report.get("rate_limited_at") else "no"
        lines.append(
            f"| `{report['model']}` | {tier} | **{report['passed']}/{report['ran']}** | "
            f"{report['first_pass_rate']:.0%} | {report['repair_rate']:.0%} | {report['steps_per_task']} | "
            f"{report['tool_calls_per_task']} | {report['wall_seconds']}s | {limited} |"
        )

    categories = [name for name in (reports[0].get("categories") or {})] if reports else []
    if categories:
        head = "| category | " + " | ".join(f"`{r['model']}`" for r in reports) + " |"
        lines += ["", head, "|---|" + "---|" * len(reports)]
        for name in categories:
            cells = []
            for report in reports:
                block = (report.get("categories") or {}).get(name)
                cells.append(f"{block['passed']}/{block['ran']}" if block else "—")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")

    held = [(r["model"], r["holdout_block"]) for r in reports if r.get("holdout_block")]
    if held:
        lines += ["", "| model | held-out tasks (validation only) |", "|---|---|"]
        for model, block in held:
            lines.append(f"| `{model}` | {block['passed']}/{block['ran']} |")

    ids = [row["id"] for row in reports[0]["rows"]] if reports else []
    if ids:
        head = "| task | " + " | ".join(f"`{r['model']}`" for r in reports) + " |"
        lines += ["", head, "|---|" + "---|" * len(reports)]
        for index, task_id in enumerate(ids):
            cells = []
            for report in reports:
                row = report["rows"][index] if index < len(report["rows"]) else {}
                cells.append("skip" if row.get("skipped") else ("pass" if row.get("passed") else "fail"))
            lines.append(f"| {task_id} | " + " | ".join(cells) + " |")

    failures = [r for r in reports if r.get("failures")]
    if failures:
        lines += ["", "Every failure, with its reason:", ""]
        for report in failures:
            for failure in report["failures"]:
                lines.append(f"- `{report['model']}` · {failure['id']}: {failure['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="ollama/llama3.2:1b")
    parser.add_argument("--models", default=None, help="comma-separated models: run each and print the three-way table")
    parser.add_argument("--env-file", default=None, help="a dotenv file to read a free-tier key from at run time")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--budget", type=float, default=1800.0, help="wall-clock budget in seconds, per model")
    parser.add_argument("--task-timeout", type=float, default=240.0, help="per-task ceiling in seconds")
    parser.add_argument("--work", default=None, help="scratch dir (defaults to a temp dir)")
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument("--markdown", default=None, help="write the Markdown table here")
    parser.add_argument(
        "--keep-running-after-429",
        action="store_true",
        help="keep spending requests after a rate limit (default: stop and report it)",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="seconds between tasks; a free tier needs this (measured: ~2 requests/minute on a Mistral key)",
    )
    parser.add_argument(
        "--throttle-retries",
        type=int,
        default=1,
        help="wait out the window and re-run a throttled task at most this many times (0 = stop at once)",
    )
    parser.add_argument("--task", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--base-url", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--json-out", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--tasks",
        default=str(TASKS_FILE),
        help="the suite to run: thinking_tasks.json (the original 15 tasks) or tool_tasks.json (the tool-use categories)",
    )
    parser.add_argument(
        "--holdout",
        action="store_true",
        help="hold out a rotating quarter of each category: graded tasks decide selection, held-out tasks validate it",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated task ids: run just these (a top-up for a run the budget cut short)",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        default=None,
        help="which held-out subset to use (default: the next rotation recorded in the measurement cache)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="store the result as a selection measurement, keyed on this machine, model digest, harness version and suite",
    )
    args = parser.parse_args(argv)
    tasks_file = Path(args.tasks).resolve()

    if args.task:
        # Single-task mode: the parent gave this process an isolated environment.
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = next(t for t in tasks if t["id"] == args.task)
        row = run_one(task, model=args.model, base_url=args.base_url or "")
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(row, indent=2), encoding="utf-8")
        print(json.dumps({k: row[k] for k in ("id", "passed", "reason", "steps")}))
        return 0

    if args.env_file:
        names = load_env_file(Path(args.env_file))
        print(f"Read {len(names)} variable(s) from {args.env_file}: {', '.join(names) or 'none'}")

    rotation = args.rotation
    if rotation is None:
        if args.record:
            from core.model_measurement import next_rotation, suite_fingerprint

            rotation = next_rotation(suite_fingerprint(json.loads(tasks_file.read_text(encoding="utf-8"))))
        else:
            rotation = 1
    models = [m.strip() for m in (args.models or args.model).split(",") if m.strip()]
    reports: list[dict[str, Any]] = []
    for model in models:
        if len(models) > 1:
            print(f"\n=== {model} ===")
        work = (Path(args.work) / model.replace("/", "_").replace(":", "-")) if args.work else None
        report = run_bench(
            model=model,
            limit=args.limit,
            budget_seconds=args.budget,
            task_timeout=args.task_timeout,
            work=work,
            stop_on_rate_limit=not args.keep_running_after_429,
            pace_seconds=args.pace,
            throttle_retries=args.throttle_retries,
            tasks_file=tasks_file,
            holdout=args.holdout,
            rotation=rotation,
            only=[item for item in (args.only or "").split(",") if item.strip()],
        )
        reports.append(report)
        if args.record:
            _record(report, rotation=rotation)

    if len(reports) == 1:
        table = markdown(reports[0])
    else:
        table = comparison(reports)
    print()
    print(table)
    if args.out:
        payload = reports[0] if len(reports) == 1 else {"runs": reports}
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON report: {args.out}")
    if args.markdown:
        Path(args.markdown).write_text(table + "\n", encoding="utf-8")
        print(f"Markdown table: {args.markdown}")
    return 0 if all(report["passed"] == report["ran"] for report in reports) else 1


def _record(report: dict[str, Any], *, rotation: int) -> None:
    """Store one report as the selection's evidence, and print where the decision now stands."""
    from core.model_measurement import record, score_from_report

    score = score_from_report(report, graded=report.get("graded_tasks") or None)
    record(score, rotation=rotation)
    print(
        f"  recorded {score.ref}: {score.total_passed}/{score.total_ran} graded "
        f"({score.tool_rate:.0%}), usable={'yes' if score.usable else 'no'}, "
        f"digest {score.digest[:12]}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
