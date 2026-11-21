"""Make the process's standard streams safe to write to on any console.

Hermus prints status glyphs (``✅``, ``❌``, ``⚠️``, ``→``) and non-ASCII text to
stdout. A redirected stdout on Windows defaults to the ANSI code page (cp1252),
where one such character raises ``UnicodeEncodeError`` and takes the whole
command down mid-output: ``hermus doctor`` died before printing its first check,
and so did every CLI subprocess Hermus starts with captured output.

An interactive console already speaks UTF-8 (PEP 528); this only ever changes
redirected and piped output, where UTF-8 is both correct and what a reader
expects. ``errors="replace"`` is the backstop: if a stream still cannot carry a
glyph, output degrades to ``?`` instead of a traceback.
"""

from __future__ import annotations

import sys
from typing import TextIO

#: Characters the product actually prints; a sample is enough to decide.
_PROBE = "✅❌⚠→—✓"


def _needs_utf8(stream: TextIO) -> bool:
    """True when the stream's encoding cannot carry the glyphs Hermus prints."""
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        _PROBE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return True
    return False


def configure_stdio(*streams: TextIO | None) -> None:
    """Keep readable output and forbid an encoding crash.

    Called by every entry point (CLI, bootstrap, gateway) before the first line
    is printed. Streams without ``reconfigure`` (a test double, a closed
    handle, ``None`` under pythonw) are left alone.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            if _needs_utf8(stream):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


__all__ = ["configure_stdio"]
