"""Make the console able to print the product's own text.

Windows hands a redirected stdout the console code page (cp1252 by default), not
UTF-8. ``setup.cmd`` pipes the installer through PowerShell, so the install report
is exactly that case: the last line of a successful install raised
``UnicodeEncodeError: 'charmap' codec can't encode character '\\u2705'`` and the
person was left with a traceback after all the work had been done.

The marks stay in the files — only the write to the stream is made tolerant. UTF-8
when the stream allows it, a replacement character when it does not, so a report
always reaches the person who asked for it.
"""

from __future__ import annotations

import sys


def configure_console() -> None:
    """Print real text on a cp1252 console instead of dying on a check mark."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # A stream that cannot change its encoding after a write still lets
            # the error handler change, and that alone stops the exception.
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
