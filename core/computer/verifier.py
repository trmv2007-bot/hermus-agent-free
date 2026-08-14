"""Screen verifier — confirm an expected UI state from before/after frames.

Used by the autonomous loop's VERIFY phase for GUI tasks: after an action
(click/type/launch), compare the screen to a recorded expectation.

- ``screen_changed``: did anything on screen move?
- ``verify``: optional vision-model callback for semantic checks
  (e.g. "did an error dialog appear?").
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .frame_sampler import _image_diff


class ScreenVerifier:
    def __init__(self, vision_model: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
                 change_threshold: float = 0.02):
        # vision_model(image, description) -> {"matched": bool, "detail": str}
        self.vision_model = vision_model
        self.change_threshold = change_threshold

    def screen_changed(self, before: Any, after: Any) -> Dict[str, Any]:
        diff = _image_diff(before, after)
        return {"changed": diff >= self.change_threshold, "diff": round(diff, 4)}

    def verify(self, before: Any, after: Any, expected_state: str = "") -> Dict[str, Any]:
        """Confirm the screen reached ``expected_state`` after an action."""
        change = self.screen_changed(before, after)
        result = {"changed": change["changed"], "diff": change["diff"], "expected_state": expected_state}
        if expected_state and self.vision_model:
            try:
                v = self.vision_model(after, expected_state)
                result["matched"] = bool(v.get("matched"))
                result["detail"] = v.get("detail", "")
            except Exception as e:  # noqa: BLE001
                result["matched"] = False
                result["detail"] = f"vision error: {e}"
        else:
            # without a vision model, "verified" = something changed
            result["matched"] = change["changed"]
            result["detail"] = "screen change only (no vision model)"
        result["ok"] = bool(result.get("matched", result["changed"]))
        return result

    def verify_sequence(self, frames: List[Dict[str, Any]], expected_state: str = "") -> Dict[str, Any]:
        """Verify across a recorded sequence (first vs last meaningful frame)."""
        if not frames:
            return {"ok": False, "detail": "no frames"}
        images = [f.get("image") for f in frames if f.get("image") is not None]
        if not images:
            return {"ok": False, "detail": "no images"}
        return self.verify(images[0], images[-1], expected_state)
