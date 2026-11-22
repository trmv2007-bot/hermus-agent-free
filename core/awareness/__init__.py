"""HERMUS awareness: a read-only picture of the desktop, and quiet judgement.

Two layers live here.

**Perception** (``core.connectors.desktop``) answers *what is on this PC right
now* — focused window and its process, visible windows, power, idle time,
monitor geometry and DPI, and a clipboard *kind and hash*. It is read-only: no
keystrokes are synthesised, no window is focused, nothing is opened.

**Judgement** (``core.attention``) decides *whether this is a moment to speak*.
It suppresses while the person is typing, in a call, fullscreen, outside quiet
hours, or inside a hard rate limit, and batches everything it withheld into one
digest the person pulls when they are already in the product.

Collected, and what leaves the machine
--------------------------------------
| Signal | Read from | Kept in a prompt | Written to disk |
|---|---|---|---|
| Focused window title | ``GetForegroundWindow`` + ``GetWindowText`` | yes, redacted, capped | no |
| Focused process / pid | ``GetWindowThreadProcessId`` + psutil | yes (process name only) | process name, in the 24h summary |
| Visible windows | ``EnumWindows`` | app names, up to a cap | no |
| Battery / AC | psutil ``sensors_battery`` | yes (percent, AC) | no |
| Idle seconds | ``GetLastInputInfo`` | yes (rounded) | no |
| Monitor geometry / DPI | ``EnumDisplayMonitors`` | yes | no |
| Clipboard | ``OpenClipboard`` + ``EnumClipboardFormats`` | kind only (never the hash) | nothing; the hash lives in memory |
| Suggestions fired / withheld | this package | digest items only | pattern name and counts, 24h |
| Settings | ``data/awareness/settings.json`` | — | yes, that file |

Never collected: clipboard contents, keystrokes, screen pixels, the text of any
window, file paths, or a history of titles. Titles are held in memory for the
duration of one snapshot and are never written to disk in any form.

Privacy controls, all on by default where they can be: a pause switch
(``hermus awareness --pause``), a per-app exclusion list (an excluded app never
appears in any snapshot field), a private-window heuristic that blanks titles for
banking, password managers and incognito/InPrivate windows, and credential-shaped
string redaction before anything can reach a prompt.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """Lazy accessors keep ``import core.awareness.redact`` free of circularity."""
    if name in {"AwarenessService", "awareness", "get_awareness"}:
        from .service import AwarenessService, awareness, get_awareness

        return {"AwarenessService": AwarenessService, "awareness": awareness, "get_awareness": get_awareness}[name]
    raise AttributeError(f"module 'core.awareness' has no attribute {name!r}")


__all__ = ["AwarenessService", "awareness", "get_awareness"]
