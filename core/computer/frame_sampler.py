"""Frame sampler — promote only important frames for vision analysis.

Inputs may contain legacy PIL images or the recorder's compressed JPEG records.
Images are decoded lazily and downscaled before comparison, keeping the hot
change-detection path inexpensive.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .recorder import decode_frame


def _comparison_image(value: Any, max_size: Tuple[int, int] = (320, 180)) -> Any:
    image = decode_frame(value)
    if image is None:
        return None
    try:
        img = image.convert("RGB")
        img.thumbnail(max_size)
        return img
    except Exception:
        return image


def _image_diff(img_a: Any, img_b: Any) -> float:
    """Return a normalized 0..1 mean-pixel difference for two frames."""
    a, b = _comparison_image(img_a), _comparison_image(img_b)
    if a is None or b is None:
        return 1.0 if (a is None) != (b is None) else 0.0
    try:
        from PIL import ImageChops, ImageStat

        if a.size != b.size:
            b = b.resize(a.size)
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        mean = sum(stat.mean) / max(1, len(stat.mean))
        return min(mean / 255.0, 1.0)
    except Exception:
        return 0.0


class FrameSampler:
    def __init__(self, threshold: float = 0.02):
        self.threshold = max(0.0, min(float(threshold), 1.0))

    def detect_changes(self, frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Mark frames that differ from the previous frame beyond threshold."""
        events: List[Dict[str, Any]] = []
        prev: Optional[Dict[str, Any]] = None
        for index, frame in enumerate(frames):
            if prev is None:
                prev = frame
                continue
            diff = _image_diff(prev, frame)
            if diff >= self.threshold:
                events.append({
                    **frame,
                    "change_score": round(diff, 4),
                    "type": "change",
                    "frame_index": index,
                    "before_index": index - 1,
                })
            prev = frame
        return events

    def select_important(
        self,
        frames: List[Dict[str, Any]],
        max_frames: int = 5,
        chronological: bool = False,
    ) -> List[Dict[str, Any]]:
        """Pick frames with the largest changes, optionally restoring time order."""
        events = self.detect_changes(frames)
        events.sort(key=lambda event: event.get("change_score", 0.0), reverse=True)
        selected = events[:max(0, int(max_frames))]
        if chronological:
            selected.sort(key=lambda event: event.get("frame_index", 0))
        return selected

    def before_after(
        self,
        frames: List[Dict[str, Any]],
        event_index: int,
    ) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
        """Return decoded ``(before, event, after)`` around a frame index."""
        if not frames or event_index < 0 or event_index >= len(frames):
            return None, None, None
        before = decode_frame(frames[event_index - 1]) if event_index > 0 else None
        event = decode_frame(frames[event_index])
        after = decode_frame(frames[event_index + 1]) if event_index + 1 < len(frames) else None
        return before, event, after

    def summarize(self, frames: List[Dict[str, Any]]) -> Dict[str, Any]:
        events = self.detect_changes(frames)
        important = self.select_important(frames, chronological=True)
        return {
            "frames": len(frames),
            "events": len(events),
            "important": [event.get("ts") for event in important],
            "first_event": events[0].get("ts") if events else None,
            "last_event": events[-1].get("ts") if events else None,
            "buffer_bytes": sum(int(frame.get("bytes") or 0) for frame in frames),
        }
