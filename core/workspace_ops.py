"""The agent's side of the workspace.

The room already owns a validated operation vocabulary on the client — `open`,
`close`, `move`, `resize`, `dock`, `focus` and the rest, all of which pass through
`validateOp` before they touch state. Until now nothing could *send* one: the
event stream only ever carried ops the frontend derived for itself from event
kinds, which meant the agent could watch the room but not touch it.

This module is the missing half, and it is deliberately small:

  - `validate_ops` re-checks an op server-side. The client is not a trust
    boundary — it is a browser, reachable by anything that can run script in the
    page, and it is also on a different side of a network hop. Validating twice
    is not redundancy for its own sake; it is what makes a refusal meaningful
    ("you asked for a surface that does not exist") rather than a silent drop.
  - `request` publishes the accepted ops as one `workspace_op` event, so a batch
    arrives at the room in a single re-render instead of racing several.

The agent can only ASK. It cannot inject markup, cannot run code in the shell,
and cannot bypass the same `applyOps` the UI uses, which is what makes a refusal
observable instead of mysterious.
"""

from __future__ import annotations

from typing import Any

# The vocabulary, mirrored from `validateOp` on the client. Kept as data rather
# than as per-op code so the two can be diffed by a test instead of by reading.
OP_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "close",
        "focus",
        "move",
        "resize",
        "dock",
        "minimize",
        "maximize",
        "unmaximize",
        "hide",
        "show",
        "restore",
        "pin",
        "rename",
    }
)

# Ops that address an existing surface and therefore need an id.
TARGETED_OPS: frozenset[str] = frozenset(OP_NAMES - {"open"})

DOCK_SIDES: frozenset[str] = frozenset({"left", "right"})

# Guards. The room is a viewport, not a coordinate space anyone should be able
# to park a surface at 10**9 in.
MAX_OPS_PER_BATCH = 32
MAX_TITLE_LEN = 120
MAX_DIMENSION = 20000.0


class OpRefusal(Exception):
    """A named op was rejected, with the reason a human can act on."""

    def __init__(self, reason: str, index: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.index = index


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpRefusal(f"{field!r} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise OpRefusal(f"{field!r} must be finite")
    return number


def validate_op(op: Any) -> dict[str, Any]:
    """Return the op normalised, or raise `OpRefusal` with a usable reason.

    Every failure names the field that failed. "invalid op" tells the agent
    nothing it can act on, and an agent that cannot learn why it was refused will
    simply try the same thing again.
    """
    if not isinstance(op, dict):
        raise OpRefusal("op must be an object")
    name = op.get("op")
    if not isinstance(name, str) or name not in OP_NAMES:
        raise OpRefusal(f"unknown op {name!r}; expected one of {', '.join(sorted(OP_NAMES))}")

    clean: dict[str, Any] = {"op": name}

    if name == "open":
        surface = op.get("surface")
        if not isinstance(surface, dict):
            raise OpRefusal("open needs a 'surface' object")
        kind = surface.get("kind")
        if not isinstance(kind, str) or not kind:
            raise OpRefusal("open.surface needs a 'kind'")
        clean["surface"] = {
            "kind": kind,
            "title": str(surface.get("title") or kind)[:MAX_TITLE_LEN],
            "source": surface.get("source") if isinstance(surface.get("source"), dict) else {"kind": "agent", "ref": ""},
        }
        if "act" in surface:
            clean["surface"]["act"] = bool(surface["act"])
        return clean

    ident = op.get("id")
    if not isinstance(ident, str) or not ident:
        raise OpRefusal(f"{name} needs a surface 'id'")
    clean["id"] = ident

    if name == "move":
        clean["x"] = _require_number(op.get("x"), "x")
        clean["y"] = _require_number(op.get("y"), "y")
    elif name == "resize":
        width = _require_number(op.get("w"), "w")
        height = _require_number(op.get("h"), "h")
        if width <= 0 or height <= 0:
            raise OpRefusal("resize needs positive dimensions")
        clean["w"] = min(width, MAX_DIMENSION)
        clean["h"] = min(height, MAX_DIMENSION)
    elif name == "dock":
        side = op.get("side")
        if side not in DOCK_SIDES:
            raise OpRefusal(f"dock.side must be one of {', '.join(sorted(DOCK_SIDES))}")
        clean["side"] = side
    elif name == "pin":
        clean["pinned"] = bool(op.get("pinned", True))
    elif name == "rename":
        title = op.get("title")
        if not isinstance(title, str) or not title.strip():
            raise OpRefusal("rename needs a non-empty 'title'")
        clean["title"] = title.strip()[:MAX_TITLE_LEN]

    return clean


def validate_ops(ops: Any) -> list[dict[str, Any]]:
    """Validate a whole batch. A bad op refuses the batch, not just itself.

    Half-applying a batch is how the room ends up in a state neither the agent
    nor the operator asked for — a panel closed and its replacement never
    opened. The caller can retry with the offending op removed, because the
    refusal names its index.
    """
    if not isinstance(ops, list):
        raise OpRefusal("ops must be a list")
    if not ops:
        raise OpRefusal("ops must not be empty")
    if len(ops) > MAX_OPS_PER_BATCH:
        raise OpRefusal(f"at most {MAX_OPS_PER_BATCH} ops per batch, got {len(ops)}")
    out: list[dict[str, Any]] = []
    for index, op in enumerate(ops):
        try:
            out.append(validate_op(op))
        except OpRefusal as refusal:
            raise OpRefusal(f"op {index}: {refusal.reason}", index=index) from None
    return out


def request(ops: Any, *, actor: str = "agent", trace_id: str | None = None) -> dict[str, Any]:
    """Validate and publish a batch of workspace ops.

    Published on the dashboard bus, not the canonical one, because that is the bus
    the room's WebSocket actually reads. `dashboard_events.publish` bridges onto
    the canonical log itself, so this still lands in the durable event history —
    but publishing only to the canonical bus reaches the logs and never the room,
    which is the whole point of the call.

    Returns the accepted ops and the event id, so the caller can quote the exact
    event in a log and follow what the room did with it.
    """
    from .dashboard_events import publish as publish_dashboard

    clean = validate_ops(ops)
    payload: dict[str, Any] = {"ops": clean, "actor": actor}
    if trace_id:
        payload["trace_id"] = trace_id
    event = publish_dashboard("workspace_op", payload)
    return {"ok": True, "accepted": clean, "event_id": str(event.get("id", "")), "count": len(clean)}


__all__ = [
    "OP_NAMES",
    "OpRefusal",
    "request",
    "validate_op",
    "validate_ops",
]
