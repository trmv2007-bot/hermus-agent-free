"""Vision-driven UI target detection.

Instead of asking an LLM to guess pixel coordinates blind, :class:`TargetDetector`
asks a vision model to locate a described target *within a captured frame* and
returns screen coordinates with a confidence and a description.  The vision
model is injectable, so the detector is testable offline; a pure-PIL template
matcher is also provided for the (rare) case where an exact image is known.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Callable, Dict, Optional, Tuple

from .recorder import decode_frame, encode_image


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort parse of a JSON object embedded in model prose."""
    if not text:
        return None
    text = text.strip()
    # Prefer a direct object.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    # Fall back to the first balanced { ... } block.
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                    return parsed if isinstance(parsed, dict) else None
                except Exception:
                    return None
    return None


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TargetDetector:
    """Locate a described UI target in a frame and return screen coordinates."""

    def __init__(
        self,
        vision_model: Optional[Callable[[Any, str], Any]] = None,
        locator: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
        min_confidence: float = 0.4,
    ):
        # vision_model(image, prompt) -> dict|str  (same contract as VideoAnalyzer)
        # locator(frame, target) -> dict — a fully custom lookup for tests.
        self.vision_model = vision_model
        self.locator = locator
        self.min_confidence = max(0.0, min(float(min_confidence), 1.0))

    def _call_vision(self, image: Any, prompt: str) -> Dict[str, Any]:
        if self.vision_model is None:
            return {"success": False, "error": "no vision model configured"}
        try:
            response = self.vision_model(image, prompt)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"vision model error: {exc}"}
        if isinstance(response, str):
            return {"success": True, "description": response}
        if not isinstance(response, dict):
            return {"success": False, "error": "vision model returned an unsupported result"}
        text = (
            response.get("description")
            or response.get("detail")
            or response.get("response")
            or response.get("result")
            or ""
        )
        return {**response, "success": response.get("success", True), "description": str(text).strip()}

    @staticmethod
    def _screen_size(frame: Any) -> Optional[Tuple[int, int]]:
        if isinstance(frame, dict) and frame.get("size"):
            try:
                width, height = frame["size"]
                if int(width) > 0 and int(height) > 0:
                    return int(width), int(height)
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _decode_size(frame: Any) -> Optional[Tuple[int, int]]:
        image = decode_frame(frame)
        if image is None:
            return None
        try:
            return int(image.width), int(image.height)
        except Exception:
            return None

    @staticmethod
    def _scale_point(x: float, y: float, from_size, to_size) -> Tuple[float, float]:
        """Map a point from one image size to another."""
        if from_size is None or to_size is None or from_size == to_size:
            return float(x), float(y)
        sx = float(to_size[0]) / float(from_size[0])
        sy = float(to_size[1]) / float(from_size[1])
        return float(x) * sx, float(y) * sy

    def find_on_screen(self, frame: Any, target: str) -> Dict[str, Any]:
        """Return ``{found, x, y, confidence, description}`` for ``target``.

        Coordinates are reported in *screen* pixels (the frame's original
        capture size), so a click at ``(x, y)`` lands on the target regardless
        of the downscaling used for vision analysis.
        """
        screen_size = self._screen_size(frame)
        decoded_size = self._decode_size(frame)
        image = decode_frame(frame)
        if image is None:
            return {"found": False, "confidence": 0.0, "description": "no frame image", "target": target}

        if self.locator is not None:
            raw = self.locator(frame, target) or {}
        else:
            raw = self._locate_with_vision(image, decoded_size, target)

        found = bool(raw.get("found"))
        x = _number(raw.get("x"))
        y = _number(raw.get("y"))
        confidence = _number(raw.get("confidence", 0.0)) or 0.0

        # Model coordinates are relative to the decoded (possibly downscaled)
        # image; rescale them to the original screen size.
        if found and x is not None and y is not None:
            x, y = self._scale_point(x, y, decoded_size, screen_size)

        result: Dict[str, Any] = {
            "found": found and x is not None and y is not None,
            "target": target,
            "confidence": round(max(0.0, min(confidence, 1.0)), 3),
            "description": raw.get("description", ""),
            "box": raw.get("box"),
        }
        if result["found"]:
            result["x"] = round(float(x), 1)
            result["y"] = round(float(y), 1)
        if result["confidence"] < self.min_confidence:
            # A low-confidence match is reported but not actionable.
            result["found"] = False
            result.setdefault("description", "")
        return result

    def _locate_with_vision(self, image: Any, decoded_size, target: str) -> Dict[str, Any]:
        prompt = (
            f"Locate the UI element described as: {target}\n"
            "Respond with ONLY a JSON object, no prose, in the form:\n"
            '{"found": true, "x": 742, "y": 381, "confidence": 0.94, "description": "Install button"}\n'
            "Use pixel coordinates relative to the top-left of the image. "
            "If the element is not visible, use found: false and x/y as 0."
        )
        response = self._call_vision(image, prompt)
        if response.get("error") and not response.get("description"):
            return {"found": False, "confidence": 0.0, "description": response["error"]}
        parsed = extract_json_object(response.get("description", "")) or {}
        return {
            "found": bool(parsed.get("found")),
            "x": parsed.get("x"),
            "y": parsed.get("y"),
            "confidence": parsed.get("confidence", 0.0),
            "description": parsed.get("description", ""),
            "box": parsed.get("box"),
        }

    def find_template(self, frame: Any, template: Any, threshold: float = 0.05) -> Dict[str, Any]:
        """Locate an exact image ``template`` in a frame (pure PIL, no OpenCV).

        Downscales both to a small search grid so it stays cheap; accuracy is
        coarse by design — prefer the vision-model path for semantic targets.
        """
        screen = decode_frame(frame)
        needle = decode_frame(template)
        if screen is None or needle is None:
            return {"found": False, "confidence": 0.0, "description": "could not decode images"}
        try:
            from PIL import Image

            screen_rgb = screen.convert("RGB")
            needle_rgb = needle.convert("RGB")
            max_side = 320
            if screen_rgb.width > max_side:
                ratio = max_side / float(screen_rgb.width)
                screen_rgb = screen_rgb.resize((max_side, max(1, round(screen_rgb.height * ratio))))
            if needle_rgb.width > screen_rgb.width or needle_rgb.height > screen_rgb.height:
                return {"found": False, "confidence": 0.0, "description": "template larger than screen"}
            best = 1.0
            best_xy = (0, 0)
            for y in range(0, screen_rgb.height - needle_rgb.height + 1, 4):
                for x in range(0, screen_rgb.width - needle_rgb.width + 1, 4):
                    diff = _mean_abs_diff(screen_rgb, needle_rgb, x, y)
                    if diff < best:
                        best = diff
                        best_xy = (x, y)
            found = best <= threshold
            scale = self._screen_size(frame)
            decoded = self._decode_size(frame)
            sx, sy = best_xy
            if found and scale and decoded:
                sx, sy = self._scale_point(sx, sy, decoded, scale)
            return {
                "found": found,
                "x": round(float(sx), 1),
                "y": round(float(sy), 1),
                "confidence": round(1.0 - best, 3),
                "description": f"template match (error {best:.3f})",
            }
        except Exception as exc:  # noqa: BLE001
            return {"found": False, "confidence": 0.0, "description": f"template match failed: {exc}"}


def _mean_abs_diff(screen, needle, x: int, y: int) -> float:
    total = 0
    count = 0
    for dy in range(needle.height):
        for dx in range(needle.width):
            a = screen.getpixel((x + dx, y + dy))
            b = needle.getpixel((dx, dy))
            total += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
            count += 3
    return total / max(1, count) / 255.0
