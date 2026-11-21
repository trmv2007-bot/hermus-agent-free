"""Make the process's standard streams safe to write to on any console.

Hermus prints status glyphs (``✅``, ``❌``, ``⚠️``, ``→``) and non-ASCII text to
stdout. On Windows a *redirected* stdout/stderr defaults to the ANSI code page
(cp1252), where one such character raises ``UnicodeEncodeError`` and takes the
whole command down mid-output: ``hermus doctor`` died before printing its first
check, and so did every CLI subprocess Hermus starts with captured output.

An interactive console already speaks UTF-8 (PEP 528), so this only ever
affects redirected output — where an unencodable glyph becoming ``?`` is
strictly better than a traceback.
"""

from __future__ import annotations

import sys
from typing import TextIO


def configure_stdio(*streams: TextIO | None) -> None:
    """Keep a stream's encoding but stop it from raising on unencodable text.

    Called by every entry point (CLI, bootstrap, gateway) before the first
    line is printed. Streams without ``reconfigure`` (a test double, a closed
    handle, ``None`` under pythonw) are left alone.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            continue


__all__ = ["configure_stdio"]
