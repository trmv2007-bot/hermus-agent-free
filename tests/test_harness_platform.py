"""The harness must run on the platform the product ships to.

Every one of these tests is a statement about *how* the suite runs a child
process, not about HERMUS behaviour, because the failure they pin is silent: a
harness that hands hundreds of kilobytes of JavaScript to ``node -e`` works on
Linux, where ``ARG_MAX`` is about 2 MB, and dies on Windows, where the whole
command line is capped at 32,767 characters with
``FileNotFoundError: [WinError 206]``.  The tests never run, nothing goes red,
and every Control Room change merges unverified on the target platform.

The rule this pins: a script goes in a file, and every child process is spoken
to in UTF-8.  ``tests/_node_harness.run_node`` is the one path the Node
harnesses take.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._node_harness import (
    INLINE_SCRIPT_LIMIT,
    check_argv,
    inline_flags,
    program_name,
    run_node,
)

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

#: subprocess entry points whose argv must obey the bound.
SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


def _subprocess_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return the local names bound to the subprocess module and to its calls."""
    modules: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_CALLS:
                    calls.add(alias.asname or alias.name)
    return modules, calls


def _argv_nodes(call: ast.Call) -> list[ast.expr]:
    """The expressions that become argv for this subprocess call."""
    if not call.args:
        return []
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)):
        return list(first.elts)
    return [first]


def _static_text(node: ast.expr) -> str | None:
    """Best-effort text of an argv element, or None when it cannot be known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules, calls = _subprocess_names(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
            if func.attr not in SUBPROCESS_CALLS:
                continue
        elif isinstance(func, ast.Name) and func.id in calls:
            pass
        else:
            continue
        elements = _argv_nodes(node)
        # The program decides which flags are source code: ``-p`` is print to
        # Node and a plugin switch to pytest. An argv head that cannot be known
        # statically gets no flag guess — the length and newline rules still
        # apply to it.
        head = _static_text(elements[0]) if elements else None
        flags = inline_flags(program_name(head)) if head is not None else ()
        for element in elements:
            text = _static_text(element)
            if text is None:
                continue
            if "\n" in text:
                problems.append(f"{path.name}:{node.lineno} passes a multi-line script as one argv element")
            elif len(text) > INLINE_SCRIPT_LIMIT:
                problems.append(
                    f"{path.name}:{node.lineno} passes a {len(text)}-character argv element "
                    f"(limit {INLINE_SCRIPT_LIMIT}); write it to a file"
                )
            elif text in flags:
                problems.append(f"{path.name}:{node.lineno} passes inline source with {text} — pass a script path instead")
    return problems


def test_no_test_hands_a_script_to_a_command_line():
    """A source file never travels through argv; a path does."""
    problems: list[str] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        problems.extend(_violations(path))
    assert not problems, "command lines that break on Windows:\n" + "\n".join(problems)


def test_the_rule_refuses_the_shapes_that_broke_windows():
    with pytest.raises(AssertionError, match="inline source flag"):
        check_argv(["node", "-e", "console.log(1)"])
    with pytest.raises(AssertionError, match="characters"):
        check_argv(["node", "x" * (INLINE_SCRIPT_LIMIT + 1)])
    with pytest.raises(AssertionError, match="newline"):
        check_argv(["python", "-c", "import sys\nprint(1)"])
    check_argv(["node", "tests/js/scheduler.test.js"])
    # ``-p`` is inline source to Node and a plugin switch to pytest: the argv is
    # judged by the program it names, or every pytest run would read as a defect.
    check_argv(["python", "-m", "pytest", "-p", "no:cacheprovider"])
    with pytest.raises(AssertionError, match="inline source flag"):
        check_argv(["node", "--print", "1+1"])


def test_a_script_far_past_the_windows_limit_runs_from_a_file():
    """The proof of the defect: 200 KB of JavaScript still runs.

    Windows rejects a command line over 32,767 characters, so this would have
    raised ``WinError 206`` before reaching Node.  It runs the same way on every
    platform, which is the point — the harness has no platform branch.
    """
    filler = "// padding to overflow the Windows command line\n" * 5000
    assert len(filler) > 200_000
    proc = run_node(filler + f"\nconsole.log(JSON.stringify({{ok: true, len: {len(filler)}}}));", cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == f'{{"ok":true,"len":{len(filler)}}}'


def test_child_output_is_decoded_as_utf8_not_the_console_code_page():
    """cp1252 cannot bite: punctuation the Control Room really uses survives."""
    text = "gateway ready — accepting traffic… 15s · “quoted”"
    proc = run_node(f"console.log(JSON.stringify({{text: {_js_string(text)}}}));", cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "gateway ready — accepting traffic…" in proc.stdout


def _js_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
