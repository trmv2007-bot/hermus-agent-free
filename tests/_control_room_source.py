"""Test helper: concatenated control-room source text.

The control room used to be one ``gateway/control.html`` monolith with the CSS
and the application script inline. It is now structure (``control.html``) plus
assets (``static/control.css``, ``static/control-room.js``,
``static/control-client.js``), which is the same split the CLI went through
(``tests/_cli_source.py``).

Tests that pin control-room surface text — endpoints wired, honest-progress
wording, auth handling — should search this instead of ``control.html`` alone,
so the assertions survive the asset extraction.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Every file that makes up the production UI, in load order.
_ASSETS = (
    "gateway/control.html",
    "gateway/static/control.css",
    "gateway/static/control-client.js",
    "gateway/static/control-room.js",
    "gateway/static/console.js",
)


def control_room_source() -> str:
    """All control-room source (markup + CSS + both scripts) as one string."""
    return "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in _ASSETS)


def control_room_html() -> str:
    """Just the served markup, for assertions about the HTML document itself."""
    return (ROOT / "gateway/control.html").read_text(encoding="utf-8")
