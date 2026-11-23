"""Run a JavaScript harness from a temporary file, never from the command line.

Several tests prove browser behaviour by booting the real ``gateway/static``
sources in Node against a minimal DOM stub.  That script is assembled in Python
and runs to tens of kilobytes, so handing it to ``node -e <script>`` only works
where the platform allows enormous arguments.  On Windows the whole command
line is capped at 32,767 characters and ``subprocess`` raises
``FileNotFoundError: [WinError 206] The filename or extension is too long``
before a single assertion runs -- the tests silently vanish on the product's
own target platform.

This module is the one path such a harness takes: the script is written to a
UTF-8 temp file and Node is invoked as ``node <file>``, which has no argument
limit anywhere.  Output is decoded as UTF-8 explicitly so a Windows console
code page (cp1252) cannot turn the result into a ``UnicodeDecodeError``.

``tests/test_harness_platform.py`` guards the invariant: no test may pass a
script through an inline flag again.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

#: An argv element longer than this is a script, not an argument.
INLINE_SCRIPT_LIMIT = 4000

#: Flags that make Node treat the next argument as source code.
NODE_INLINE_FLAGS = ("-e", "--eval", "-p", "--print")

#: Flags that make Python treat the next argument as source code.
PYTHON_INLINE_FLAGS = ("-c",)


def check_argv(argv: Sequence[str]) -> None:
    """Reject an argv that carries source code instead of a file path.

    Both shapes are refused: the multi-kilobyte argument that overflows
    Windows' command line, and the short inline flag that makes the limit a
    matter of luck.
    """
    parts = [str(a) for a in argv]
    for part in parts:
        if len(part) > INLINE_SCRIPT_LIMIT:
            raise AssertionError(
                f"subprocess argument is {len(part)} characters, over the {INLINE_SCRIPT_LIMIT} limit: "
                "write the script to a temp file and pass its path"
            )
        if "\n" in part:
            raise AssertionError("subprocess argument contains a newline; a script belongs in a file, not argv")
    for flag in (*NODE_INLINE_FLAGS, *PYTHON_INLINE_FLAGS):
        if flag in parts:
            raise AssertionError(f"inline source flag {flag!r} in {parts[0]!r}: write the script to a temp file instead")


def run_node(
    script: str,
    *,
    cwd: Path,
    timeout: float = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute ``script`` as ``node <temp file>.js`` and return its result.

    The file is written UTF-8 with ``\\n`` newlines so it arrives byte-identical
    to what the tests assembled, and the temp directory is removed either way.
    """
    with tempfile.TemporaryDirectory(prefix="hermus-node-") as workdir:
        path = Path(workdir) / "harness.js"
        path.write_text(script, encoding="utf-8", newline="\n")
        child_env = dict(os.environ) if env is None else dict(env)
        argv = ["node", str(path)]
        check_argv(argv)
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            timeout=timeout,
            env=child_env,
        )


def last_json_line(stdout: str) -> dict:
    """Parse the trailing JSON line a harness prints as its result."""
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise AssertionError("no output from node; did the harness print its result?")
    return json.loads(lines[-1])
