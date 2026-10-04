"""Test helper for the production HERMUS control room.

The production UI is the canonical dashboard-v2 shell and its dedicated assets.
This helper concatenates the real page and assets so contract tests do not depend
on browser rendering or a particular bundler.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_ASSETS = (
    "gateway/control.html",
    "gateway/static/dashboard-v2.css",
    "gateway/static/dashboard-v2.js",
)


def control_room_source() -> str:
    return "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in _ASSETS)


def control_room_html() -> str:
    return (ROOT / "gateway/control.html").read_text(encoding="utf-8")
