"""Screen geometry for the presence overlay: validate, scale, and never occlude.

Three jobs, all pure functions so the router, the computer-use seam and the
renderer's reference tests can share one definition:

1. **Validate.** A rect arrives from a model, a vision grounder or a shell. Any
   of those can hand us ``None``, a bool, ``NaN``, a 40 000 px side or a negative
   width. Bad payloads raise :class:`PresencePayloadError`, which the router
   turns into a 400 — a malformed rect can never reach the render loop, and can
   never take the gateway down with it.

2. **Scale.** ``pyautogui`` reports physical pixels; the renderer paints in the
   DIP coordinates Electron reports. On a single 100 % display those agree, so
   the bug only appears on the multimonitor / 125 % machines people actually
   own. :func:`physical_to_dip` resolves a physical point through the registered
   display layout one monitor at a time, so `x=2560` on the second 150 % screen
   lands on the second screen.

3. **Never cover the target.** The arrow, ring and label are all derived here as
   geometry strictly *outside* the rect (:func:`ring_rect`, :func:`label_box`),
   so "the overlay never hides what it is pointing at" is an invariant a test
   can check instead of a hope.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

#: Anything beyond this on either axis is not a real desktop coordinate.
MAX_ABS_COORD = 100_000.0
#: A 20 000 px side is larger than an 8K ultrawide; past it we are being lied to.
MAX_SIDE = 20_000.0
#: Hairline targets still need a ring with room to breathe.
MIN_SIDE = 1.0
#: The gap the ring keeps outside the target rect, in DIP.
RING_GAP = 7.0
#: Default bubble/label limits — two lines of subtitle is the product contract.
MAX_SAY_CHARS = 240
MAX_LABEL_CHARS = 96


class PresencePayloadError(ValueError):
    """A presence payload the overlay refuses to draw. Message names the field."""


def _finite(value: Any, field: str) -> float:
    """Coerce to float, rejecting bools, strings that merely look numeric, NaN/inf."""
    if isinstance(value, bool) or value is None:
        raise PresencePayloadError(f"{field} must be a number")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except (TypeError, ValueError) as exc:
            raise PresencePayloadError(f"{field} must be a number") from exc
    if not isinstance(value, (int, float)):
        raise PresencePayloadError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise PresencePayloadError(f"{field} must be finite")
    return number


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_rect(payload: Any) -> dict[str, float]:
    """Validate a ``{x, y, w, h}`` rect and return it normalized.

    Raises :class:`PresencePayloadError` naming the offending field.
    """
    if not isinstance(payload, Mapping):
        raise PresencePayloadError("rect must be an object with x, y, w, h")
    missing = [key for key in ("x", "y") if key not in payload]
    if missing:
        raise PresencePayloadError(f"rect is missing {', '.join(missing)}")
    width_key = "w" if "w" in payload else "width"
    height_key = "h" if "h" in payload else "height"
    if width_key not in payload or height_key not in payload:
        raise PresencePayloadError("rect is missing w/h")
    rect = {
        "x": _finite(payload["x"], "x"),
        "y": _finite(payload["y"], "y"),
        "w": _finite(payload[width_key], "w"),
        "h": _finite(payload[height_key], "h"),
    }
    if rect["w"] < MIN_SIDE or rect["h"] < MIN_SIDE:
        raise PresencePayloadError(f"rect side must be at least {MIN_SIDE:g}")
    if rect["w"] > MAX_SIDE or rect["h"] > MAX_SIDE:
        raise PresencePayloadError(f"rect side must be at most {MAX_SIDE:g}")
    if abs(rect["x"]) > MAX_ABS_COORD or abs(rect["y"]) > MAX_ABS_COORD:
        raise PresencePayloadError(f"rect origin must be within ±{MAX_ABS_COORD:g}")
    return {key: round(value, 2) for key, value in rect.items()}


def point_rect(x: Any, y: Any, w: float = 72.0, h: float = 40.0) -> dict[str, float]:
    """A click target as a rect centred on the point, clamped to legal bounds.

    The controller only knows a cursor coordinate; the overlay needs an area to
    ring. Centring keeps the ring honest for both a button and a bare pixel.
    """
    cx = _finite(x, "x")
    cy = _finite(y, "y")
    width = _clamp(_finite(w, "w"), MIN_SIDE, MAX_SIDE)
    height = _clamp(_finite(h, "h"), MIN_SIDE, MAX_SIDE)
    return parse_rect({"x": cx - width / 2, "y": cy - height / 2, "w": width, "h": height})


def rect_center(rect: Mapping[str, float]) -> tuple[float, float]:
    return (float(rect["x"]) + float(rect["w"]) / 2, float(rect["y"]) + float(rect["h"]) / 2)


def inflate(rect: Mapping[str, float], pad: float) -> dict[str, float]:
    """The rect grown by ``pad`` on every side. ``pad`` may be negative (inset)."""
    return {
        "x": round(float(rect["x"]) - pad, 2),
        "y": round(float(rect["y"]) - pad, 2),
        "w": round(float(rect["w"]) + 2 * pad, 2),
        "h": round(float(rect["h"]) + 2 * pad, 2),
    }


def ring_rect(rect: Mapping[str, float], gap: float = RING_GAP) -> dict[str, float]:
    """The stroke box the renderer rings the target with.

    The box sits ``gap`` px *outside* the target, so the ring's inner edge never
    crosses the target's outer edge at any stroke width up to ``gap``.
    """
    return inflate(rect, abs(gap))


def label_box(
    rect: Mapping[str, float],
    size: tuple[float, float],
    canvas: tuple[float, float],
    gap: float = RING_GAP,
) -> dict[str, Any]:
    """Place the point's label chip outside the target, inside the canvas.

    Tries above the ring, then below it, then beside it. Returns
    ``{"x", "y", "w", "h", "placement"}`` — always disjoint from ``rect`` — so a
    label can never sit on top of the thing being pointed at, and never runs off
    the edge of a screen.
    """
    width, height = float(size[0]), float(size[1])
    canvas_w, canvas_h = float(canvas[0]), float(canvas[1])
    ring = inflate(rect, gap)
    cx = ring["x"] + ring["w"] / 2
    x = _clamp(cx - width / 2, 0.0, max(0.0, canvas_w - width))
    above_y = ring["y"] - height - gap
    below_y = ring["y"] + ring["h"] + gap
    if above_y >= 0:
        y, placement = above_y, "above"
    elif below_y + height <= canvas_h:
        y, placement = below_y, "below"
    else:
        # Neither band fits: go to the roomier side, mirroring to stay on screen.
        room_above = ring["y"]
        room_below = canvas_h - (ring["y"] + ring["h"])
        if room_above >= room_below:
            y, placement = 0.0, "above"
        else:
            y, placement = max(0.0, canvas_h - height), "below"
    box = {"x": round(x, 2), "y": round(_clamp(y, 0.0, max(0.0, canvas_h - height)), 2)}
    box["w"] = round(width, 2)
    box["h"] = round(height, 2)
    box["placement"] = placement
    return box


def overlaps(a: Mapping[str, float], b: Mapping[str, float]) -> bool:
    """True when two rects share any area (edge contact is not overlap)."""
    return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


# ---------------------------------------------------------------- displays
def parse_displays(payload: Any) -> list[dict[str, Any]]:
    """Validate the shell's display layout: ``[{x, y, w, h, scale, index?}]`` in DIP.

    Each display may carry an explicit ``physical`` bounds (the shell reports it
    when it knows it); otherwise it is derived as ``dip × scale``.
    """
    if not isinstance(payload, (list, tuple)):
        raise PresencePayloadError("displays must be a list")
    if len(payload) > 16:
        raise PresencePayloadError("displays must contain at most 16 entries")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise PresencePayloadError(f"display[{index}] must be an object")
        scale = _finite(item.get("scale", item.get("scaleFactor", 1.0)), f"display[{index}].scale")
        if scale <= 0 or scale > 8:
            raise PresencePayloadError(f"display[{index}].scale must be between 0 and 8")
        bounds = parse_rect(item.get("bounds") if isinstance(item.get("bounds"), Mapping) else item)
        physical_raw = item.get("physical")
        physical = parse_rect(physical_raw) if isinstance(physical_raw, Mapping) else inflate(bounds, 0.0)
        if not isinstance(physical_raw, Mapping):
            physical = {key: round(value * scale, 2) for key, value in bounds.items()}
        out.append({"index": int(item.get("index", index)), "scale": round(scale, 4), "bounds": bounds, "physical": physical})
    return out


def virtual_bounds(displays: Iterable[Mapping[str, Any]], space: str = "dip") -> dict[str, float]:
    """Union of every display's bounds in ``space`` (``dip`` or ``physical``)."""
    key = "physical" if space == "physical" else "bounds"
    rects = [d[key] for d in displays]
    if not rects:
        return {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    left = min(r["x"] for r in rects)
    top = min(r["y"] for r in rects)
    right = max(r["x"] + r["w"] for r in rects)
    bottom = max(r["y"] + r["h"] for r in rects)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def _contains(rect: Mapping[str, float], x: float, y: float) -> bool:
    return rect["x"] <= x <= rect["x"] + rect["w"] and rect["y"] <= y <= rect["y"] + rect["h"]


def physical_to_dip(rect: Mapping[str, float], displays: Iterable[Mapping[str, Any]]) -> tuple[dict[str, float], bool]:
    """Map a physical-pixel rect into the renderer's DIP space.

    Returns ``(rect, mapped)``. ``mapped`` is False when no display contained the
    rect's centre — the rect is then returned unscaled and the caller must say so
    (the renderer falls back to dividing by its own device pixel ratio), because
    silently guessing a scale on a machine we could not measure is exactly how a
    pointer ends up on the wrong monitor.
    """
    layout = list(displays)
    cx, cy = rect_center(rect)
    for display in layout:
        physical = display.get("physical") or display.get("bounds") or {}
        if not physical or not _contains(physical, cx, cy):
            continue
        scale = float(display.get("scale") or 1.0)
        bounds = display.get("bounds") or {}
        origin_x = float(bounds.get("x", 0.0))
        origin_y = float(bounds.get("y", 0.0))
        phys_x = float(physical.get("x", 0.0))
        phys_y = float(physical.get("y", 0.0))
        return (
            {
                "x": round(origin_x + (float(rect["x"]) - phys_x) / scale, 2),
                "y": round(origin_y + (float(rect["y"]) - phys_y) / scale, 2),
                "w": round(float(rect["w"]) / scale, 2),
                "h": round(float(rect["h"]) / scale, 2),
            },
            True,
        )
    return {key: float(value) for key, value in rect.items()}, False


def normalize_space(
    rect: Mapping[str, float], space: str, displays: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, float], bool]:
    """Apply :func:`physical_to_dip` only for physical payloads; DIP passes through."""
    if str(space).lower() in ("physical", "px", "device"):
        return physical_to_dip(rect, displays)
    return {key: float(value) for key, value in rect.items()}, True


__all__ = [
    "MAX_LABEL_CHARS",
    "MAX_SAY_CHARS",
    "MIN_SIDE",
    "RING_GAP",
    "PresencePayloadError",
    "inflate",
    "label_box",
    "normalize_space",
    "overlaps",
    "parse_displays",
    "parse_rect",
    "physical_to_dip",
    "point_rect",
    "rect_center",
    "ring_rect",
    "virtual_bounds",
]
