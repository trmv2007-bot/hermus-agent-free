"""Test helper for the production HERMUS control room.

The production UI is intentionally a thin Nexus shell plus its dedicated
Nexus assets.  This helper concatenates those real assets so contract tests do
not depend on browser rendering or a particular bundler.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_ASSETS = (
    "gateway/control.html",
    "gateway/static/nexus.css",
    "gateway/static/nexus.js",
)


def control_room_source() -> str:
    return "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in _ASSETS)


def control_room_html() -> str:
    return (ROOT / "gateway/control.html").read_text(encoding="utf-8")
