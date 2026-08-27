"""Continuous, event-driven visual condition watching."""
from __future__ import annotations

import time
from typing import Any, Optional
from collections.abc import Callable

from .frame_sampler import _image_diff
from .video_analyzer import VideoAnalyzer


class ScreenWatcher:
    def __init__(
        self,
        recorder: Any,
        analyzer: Optional[VideoAnalyzer] = None,
        evaluator: Optional[Callable[[Any, str], dict[str, Any]]] = None,
        change_threshold: float = 0.02,
    ):
        self.recorder = recorder
        self.analyzer = analyzer or VideoAnalyzer()
        self.evaluator = evaluator or self.analyzer.evaluate_condition
        self.change_threshold = max(0.0, min(float(change_threshold), 1.0))

    def watch(
        self,
        condition: str,
        timeout: float = 60.0,
        poll_interval: float = 0.25,
        stable_matches: int = 1,
        start_if_needed: bool = False,
    ) -> dict[str, Any]:
        """Stop when ``condition`` is seen on an initial or changed frame."""
        timeout = max(0.1, float(timeout))
        poll_interval = max(0.05, float(poll_interval))
        stable_matches = max(1, int(stable_matches))
        owned_recorder = False
        if not self.recorder.running:
            if not start_if_needed:
                return {"success": False, "matched": False, "error": "screen recorder is not running"}
            started = self.recorder.start()
            if not started.get("success"):
                return {"success": False, "matched": False, "error": started.get("error", "could not start recorder")}
            owned_recorder = True

        started_at = time.monotonic()
        previous = None
        last_sequence = None
        consecutive = 0
        checked = 0
        last_result: dict[str, Any] = {}
        try:
            while time.monotonic() - started_at < timeout:
                frame = self.recorder.latest()
                if frame is None or frame.get("sequence") == last_sequence:
                    time.sleep(poll_interval)
                    continue
                last_sequence = frame.get("sequence")
                # Normally call vision only for changes. Once a match is seen,
                # inspect subsequent frames too so ``stable_matches`` can
                # confirm that the state did not merely flash for one frame.
                should_check = (previous is None
                                or _image_diff(previous, frame) >= self.change_threshold
                                or consecutive > 0)
                previous = frame
                if not should_check:
                    time.sleep(poll_interval)
                    continue
                checked += 1
                try:
                    last_result = self.evaluator(frame, condition) or {}
                except Exception as exc:
                    return {"success": False, "matched": False, "error": f"condition evaluator failed: {exc}", "frames_checked": checked}
                if last_result.get("error"):
                    return {
                        "success": False,
                        "matched": False,
                        "error": last_result.get("error"),
                        "detail": last_result.get("detail", ""),
                        "frames_checked": checked,
                    }
                if last_result.get("matched"):
                    consecutive += 1
                    if consecutive >= stable_matches:
                        elapsed = round(time.monotonic() - started_at, 3)
                        return {
                            "success": True,
                            "matched": True,
                            "condition": condition,
                            "elapsed": elapsed,
                            "confidence": last_result.get("confidence", 0.0),
                            "detail": last_result.get("detail", ""),
                            "frames_checked": checked,
                            "evidence": {
                                "sequence": frame.get("sequence"),
                                "timestamp": frame.get("ts"),
                                "offset": frame.get("offset"),
                            },
                        }
                else:
                    consecutive = 0
                time.sleep(poll_interval)
        finally:
            if owned_recorder:
                self.recorder.stop()
        return {
            "success": False,
            "matched": False,
            "condition": condition,
            "timeout": timeout,
            "frames_checked": checked,
            "detail": last_result.get("detail", "condition was not observed"),
        }
