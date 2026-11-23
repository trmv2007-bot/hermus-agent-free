"""Every text read and write in the product states UTF-8.

``open()``, ``Path.read_text``/``write_text`` and ``subprocess(text=True)`` all
fall back to the locale encoding when no encoding is given, which is cp1252 on a
default Windows install. A report, memory file, event log or generated skill that
carries an em dash, a Cyrillic name or an emoji then raises ``UnicodeEncodeError``
on write or produces bytes only cp1252 can read back — a failure Linux CI cannot
see. This gate walks the ASTs instead of sampling greps, so a new unqualified
text call fails here rather than on a Windows user's machine.

Binary paths are expected to stay binary: they declare a ``"b"`` mode and are
allowed. Anything that cannot take an encoding at all (a raw descriptor or a
non-filesystem ``open``) is listed in ``ALLOWLIST`` with the reason it is exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PACKAGES = ("core", "gateway", "hermus_cli", "tools", "scheduler", "subagents")

TEXT_PATH_METHODS = {"read_text", "write_text"}
OPEN_ATTRS = {"open", "fdopen"}
SUBPROCESS_CALLS = {"run", "Popen", "call", "check_output", "check_call"}

ALLOWLIST: dict[tuple[str, str], str] = {
    # Raw file descriptors: the mode is a flags int, not a text/binary string, and
    # the surrounding code reads and writes bytes (fsync on a directory handle,
    # one-byte advisory lock files).
    ("core/atomic_io.py", "os.open"): "fsync/lock descriptors, byte-level by design",
    ("core/harness/_store.py", "os.open"): "exclusive lock descriptor, byte-level by design",
    # Not filesystem opens: PIL decodes an in-memory buffer, and MissionFileScope
    # exposes a classmethod that builds a scope object rather than a stream.
    ("core/computer/recorder.py", "Image.open"): "PIL decodes an in-memory BytesIO buffer",
    ("core/document_ingest.py", "Image.open"): "PIL decodes an in-memory BytesIO buffer",
    ("core/mission.py", "MissionFileScope.open"): "classmethod building a scope, not a stream",
}


def _dotted(node: ast.expr) -> str:
    """Render a callee as source-ish text: ``open``, ``os.open``, ``p.open``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ast.dump(node)


def _kwarg(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _open_mode(call: ast.Call) -> str | None:
    """Mode of an ``open`` call: the keyword, or the positional slot Python uses."""
    mode = _literal(_kwarg(call, "mode"))
    if mode is not None:
        return mode
    # open(file, mode) and os.fdopen(fd, mode) take the mode second;
    # <path>.open(mode) takes it first.
    func = call.func
    is_module_call = (
        isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in {"os", "io", "codecs"}
    )
    if isinstance(func, ast.Attribute) and not is_module_call:
        return _literal(call.args[0]) if call.args else None
    return _literal(call.args[1]) if len(call.args) >= 2 else None


def _violations(path: Path) -> list[str]:
    rel = path.relative_to(REPO).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _dotted(node.func)
        if callee in {"run", "Popen", "call", "check_output", "check_call"} or callee.startswith("subprocess."):
            if node.func.attr if isinstance(node.func, ast.Attribute) else None:
                attr = node.func.attr
                if attr in SUBPROCESS_CALLS:
                    text = _kwarg(node, "text") or _kwarg(node, "universal_newlines")
                    if isinstance(text, ast.Constant) and text.value is True and _kwarg(node, "encoding") is None:
                        found.append(
                            f"{rel}:{node.lineno}: subprocess.{attr}(text=True) without encoding"
                            " — external output would be decoded with the locale encoding"
                        )
                    continue
        is_path_method = callee.rsplit(".", 1)[-1] in TEXT_PATH_METHODS
        is_open = callee == "open" or callee.rsplit(".", 1)[-1] in OPEN_ATTRS
        if not (is_path_method or is_open) or _kwarg(node, "encoding") is not None:
            continue
        if not is_path_method:
            mode = _open_mode(node)
            if mode is not None and "b" in mode:
                continue  # a declared binary path
        if (rel, callee) in ALLOWLIST:
            continue
        found.append(f"{rel}:{node.lineno}: {callee}(...) without encoding — falls back to the locale encoding off Linux")
    return found


def _targets() -> list[Path]:
    paths: list[Path] = []
    for package in PACKAGES:
        paths.extend(sorted((REPO / package).rglob("*.py")))
    return paths


def test_every_text_io_call_declares_utf8():
    violations: list[str] = []
    for path in _targets():
        violations.extend(_violations(path))
    assert not violations, "text I/O without an explicit encoding:\n" + "\n".join(violations)


def test_every_package_is_still_scanned():
    """A moved or renamed package must not silently drop out of the sweep."""
    for package in PACKAGES:
        assert (REPO / package).is_dir(), f"{package}/ is gone; update PACKAGES or the allowlist"
        assert list((REPO / package).rglob("*.py")), f"{package}/ has no Python modules to scan"


def test_allowlist_entries_are_live():
    """A stale exemption is a hole: every entry must still match a real call site."""
    seen = set()
    for path in _targets():
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = _dotted(node.func)
                if (rel, callee) in ALLOWLIST:
                    seen.add((rel, callee))
    stale = sorted(set(ALLOWLIST) - seen)
    assert not stale, f"allowlist entries no longer used, delete them: {stale}"


@pytest.mark.parametrize(
    "snippet",
    [
        "open(p)",
        "open(p, 'w')",
        "p.open('a')",
        "p.read_text()",
        "p.write_text('x')",
        "subprocess.run(cmd, text=True)",
    ],
)
def test_the_gate_catches_unqualified_calls(tmp_path, snippet):
    """The gate itself must fail on the patterns it exists to catch."""
    sample = tmp_path / "core" / "sample.py"
    sample.parent.mkdir(parents=True)
    sample.write_text(f"def f(p, cmd):\n    return {snippet}\n", encoding="utf-8")
    tree = ast.parse(sample.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    qualifies = False
    for call in calls:
        callee = _dotted(call.func)
        text_call = (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in SUBPROCESS_CALLS
            and isinstance(_kwarg(call, "text"), ast.Constant)
            and _kwarg(call, "encoding") is None
        )
        io_call = callee == "open" or callee.rsplit(".", 1)[-1] in OPEN_ATTRS | TEXT_PATH_METHODS
        if text_call or (io_call and _kwarg(call, "encoding") is None):
            qualifies = True
    assert qualifies, f"the gate would miss {snippet!r}"
