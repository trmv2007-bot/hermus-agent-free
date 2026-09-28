"""Filesystem probing that tolerates a flaky volume.

Why this exists
---------------
Several features gate on "is this file present?" -- the Piper voice model,
local vision models, config assets. On this machine a single ``Path.exists()``
call is not a reliable answer. Measured, on a file that
``Get-ChildItem -Recurse`` lists without difficulty::

    is_file() on models/.../en_US-amy-medium.onnx   0/40
    is_file() on pyproject.toml (same volume)      20/20
    [System.IO.File]::Exists()                     False
    Get-ChildItem -Recurse                         lists it fine

Directory *enumeration* is reliable; direct *stat* on a deep path fails in
bursts. So a feature that is actually installed intermittently reports itself
as missing, and the honest response -- ``spoken: false``, ``available: false``
-- is a lie caused by the probe, not by the product.

The fix is not to trust the first answer. Ask more than once, and fall back to
enumeration, which does not suffer from this.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

__all__ = ["file_exists", "dir_exists"]

# A burst of failures long enough that the first one is clearly not a transient
# race. Windows volume filters can take tens of milliseconds to settle.
_ATTEMPTS = 4
_BACKOFF_S = (0.0, 0.05, 0.15, 0.4)


def _by_enumeration(target: Path) -> bool:
    """Does the parent directory list this entry?

    Slower than stat, and immune to the failure this module exists for: the
    directory handle resolves and the entry shows up, even when stat on the
    child does not.
    """
    try:
        parent = target.parent
        with os.scandir(parent) as entries:
            for entry in entries:
                try:
                    if os.path.normcase(entry.name) == os.path.normcase(target.name):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def file_exists(path: str | os.PathLike[str]) -> bool:
    """True when the file is really there, checked more than once.

    Never raises. A probe that throws is a probe that reports an exception to a
    user who asked a yes/no question about their own machine.
    """
    target = Path(path)
    for attempt in range(_ATTEMPTS):
        try:
            if target.is_file():
                return True
        except OSError:
            pass
        # A directory with this name is not a file with this name. If we land
        # here the caller asked about something that is genuinely absent, and
        # retrying will not change that.
        try:
            if target.is_dir():
                return False
        except OSError:
            pass
        if attempt < len(_BACKOFF_S) - 1:
            time.sleep(_BACKOFF_S[attempt + 1])
    return _by_enumeration(target)


def dir_exists(path: str | os.PathLike[str]) -> bool:
    """True when the directory is there, checked more than once."""
    target = Path(path)
    for attempt in range(_ATTEMPTS):
        try:
            if target.is_dir():
                return True
        except OSError:
            pass
        if attempt < len(_BACKOFF_S) - 1:
            time.sleep(_BACKOFF_S[attempt + 1])
    try:
        return any(target.iterdir())
    except OSError:
        return False
