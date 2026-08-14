"""Frame sampler — promote only *important* frames for vision analysis.

Event-based recording: instead of analyzing every frame, detect *change*
(motion / UI updates) and surface just the frames where something happened,
plus before/after pairs for verification.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _image_diff(img_a: Any, img_b: Any) -> float:
    """Return a 0..1 measure of how different two PIL images are."""
    if img_a is None or img_b is None:
        return 1.0 if (img_a is None) != (img_b is None) else 0.0
    try:
        from PIL import ImageChops, ImageStat

        diff = ImageChops.difference(img_a.convert("RGB"), img_b.convert("RGB"))
        stat = ImageStat.Stat(diff)
        # mean pixel difference across channels, normalized 0..1
        mean = sum(stat.mean) / len(stat.mean)
        return min(mean / 255.0, 1.0)
    except Exception:
        return 0.0


class FrameSampler:
    def __init__(self, threshold: float = 0.02):
        self.threshold = threshold

    def detect_changes(self, frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Mark frames that differ from the previous one beyond threshold."""
        events = []
        prev = None
        for f in frames:
            img = f.get("image")
            if prev is None:
                prev = img
                continue
            diff = _image_diff(prev, img)
            if diff >= self.threshold:
                events.append({**f, "change_score": round(diff, 4), "type": "change"})
            prev = img
        return events

    def select_important(self, frames: List[Dict[str, Any]],
                         max_frames: int = 5) -> List[Dict[str, Any]]:
        """Pick up to ``max_frames`` frames with the largest change scores."""
        events = self.detect_changes(frames)
        events.sort(key=lambda e: e.get("change_score", 0.0), reverse=True)
        return events[:max_frames]

    def before_after(self, frames: List[Dict[str, Any]], event_index: int) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
        """Return (before, event, after) frames around a given event index."""
        if not frames or event_index < 0 or event_index >= len(frames):
            return None, None, None
        before = frames[event_index - 1].get("image") if event_index > 0 else None
        event = frames[event_index].get("image")
        after = frames[event_index + 1].get("image") if event_index + 1 < len(frames) else None
        return before, event, after

    def summarize(self, frames: List[Dict[str, Any]]) -> Dict[str, Any]:
        """High-level summary of what happened in a frame sequence."""
        events = self.detect_changes(frames)
        return {
            "frames": len(frames),
            "events": len(events),
            "important": [e.get("ts") for e in self.select_important(frames)],
            "first_event": events[0].get("ts") if events else None,
            "last_event": events[-1].get("ts") if events else None,
        }
