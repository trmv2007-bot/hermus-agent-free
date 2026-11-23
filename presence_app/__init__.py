"""HERMUS presence overlay — the floating companion's brain.

The desktop face of HERMUS: a holographic character that lives on the person's
screen, breathes while idle, and physically points at what the agent is about to
touch. Everything here is process-local and dependency-free so that

* the gateway can serve it (``gateway/routes_presence_overlay.py``),
* the computer-use controller can emit into it without a network hop
  (``core/presence_pointer.py``), and
* the CLI can drive it (``hermus presence …``).

The renderer itself is a plain web page (``gateway/static/presence/``) hosted by
a thin always-on-top shell (``presence_app/shell/``) — no framework, no build
step, and fully screenshot-verifiable in a normal browser.
"""

from __future__ import annotations

from .bridge import get_presence_bridge
from .geometry import PresencePayloadError, parse_rect
from .states import STATES

__all__ = ["STATES", "PresencePayloadError", "get_presence_bridge", "parse_rect"]
