"""Turn low-level frame changes into a small set of visual events."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .frame_sampler import FrameSampler, _image_diff


class EventDetector:
    """Debounce bursts of changed frames and retain the strongest evidence.

    A window opening often changes several consecutive captured frames.  Those
    should become one semantic event, not five vision-model calls.
    """

    def __init__(self, threshold: float = 0.02, debounce_seconds: float = 0.5):
        self.sampler = FrameSampler(threshold=threshold)
        self.debounce_seconds = max(0.0, float(debounce_seconds))

    @staticmethod
    def _offset(frame: Dict[str, Any], fallback: float) -> float:
        try:
            return float(frame.get("offset"))
        except (TypeError, ValueError):
            return fallback

    def detect(self, frames: List[Dict[str, Any]], max_events: Optional[int] = 12) -> List[Dict[str, Any]]:
        changes = self.sampler.detect_changes(frames)
        groups: List[List[Dict[str, Any]]] = []
        for change in changes:
            offset = self._offset(change, float(change.get("frame_index", 0)))
            if not groups:
                groups.append([change])
                continue
            previous = groups[-1][-1]
            previous_offset = self._offset(previous, float(previous.get("frame_index", 0)))
            if offset - previous_offset <= self.debounce_seconds:
                groups[-1].append(change)
            else:
                groups.append([change])

        events: List[Dict[str, Any]] = []
        for group in groups:
            strongest = max(group, key=lambda item: item.get("change_score", 0.0))
            frame_index = int(strongest.get("frame_index", 0))
            before_index = max(0, int(group[0].get("before_index", frame_index - 1)))
            after_index = min(len(frames) - 1, int(group[-1].get("frame_index", frame_index)))
            events.append({
                "type": "screen_change",
                "ts": strongest.get("ts"),
                "offset": strongest.get("offset"),
                "sequence": strongest.get("sequence"),
                "change_score": strongest.get("change_score", 0.0),
                "frame_index": frame_index,
                "before_index": before_index,
                "after_index": after_index,
                "burst_frames": len(group),
                # These references remain internal. VideoAnalyzer emits a
                # JSON-safe evidence object instead of serializing image data.
                "before_frame": frames[before_index],
                "frame": frames[frame_index],
                "after_frame": frames[after_index],
            })

        if max_events is not None and len(events) > max(0, int(max_events)):
            # Preserve the highest-information changes, then put them back in
            # chronological order for a readable timeline.
            events = sorted(events, key=lambda event: event["change_score"], reverse=True)[: int(max_events)]
            events.sort(key=lambda event: event.get("frame_index", 0))
        return events

    @staticmethod
    def json_event(event: Dict[str, Any]) -> Dict[str, Any]:
        """Strip in-memory frame blobs from an event for persistence."""
        return {
            key: value
            for key, value in event.items()
            if key not in {"before_frame", "frame", "after_frame"}
        }


class StreamingEventDetector:
    """Online, constant-frame-memory detection for arbitrarily long videos.

    It retains one previous compressed frame plus compact JSON metadata for
    debounced events. The detached recording service therefore covers the
    *full* session even though the recorder's frame buffer intentionally rolls
    over; metadata grows with meaningful events, not captured-frame count.
    """

    def __init__(self, threshold: float = 0.02, debounce_seconds: float = 0.5):
        self.threshold = max(0.0, min(float(threshold), 1.0))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self._previous: Optional[Dict[str, Any]] = None
        self._events: List[Dict[str, Any]] = []

    def observe(self, frame: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if frame is None:
            return None
        if self._previous is None:
            self._previous = frame
            return None
        # Ignore a frame already inspected by a slower service-status loop.
        if frame.get("sequence") == self._previous.get("sequence"):
            return None
        score = _image_diff(self._previous, frame)
        self._previous = frame
        if score < self.threshold:
            return None
        event = {
            "type": "screen_change",
            "ts": frame.get("ts"),
            "offset": frame.get("offset"),
            "sequence": frame.get("sequence"),
            "change_score": round(score, 4),
        }
        if self._events:
            try:
                elapsed = float(event.get("offset") or 0.0) - float(self._events[-1].get("offset") or 0.0)
            except (TypeError, ValueError):
                elapsed = self.debounce_seconds + 1
            if elapsed <= self.debounce_seconds:
                # Keep one event for an animation burst, using its strongest
                # frame while retaining no image blob.
                if event["change_score"] >= self._events[-1]["change_score"]:
                    self._events[-1] = event
                return self._events[-1]
        self._events.append(event)
        return event

    def events(self) -> List[Dict[str, Any]]:
        return list(self._events)
