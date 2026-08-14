"""Before/action/after visual verification for GUI work."""
from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from .frame_sampler import _image_diff
from .recorder import decode_frame


class ScreenVerifier:
    def __init__(
        self,
        vision_model: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
        change_threshold: float = 0.02,
        transition_model: Optional[Callable[[Any, Any, str], Dict[str, Any]]] = None,
    ):
        # vision_model(after, expected) -> matched/detail/confidence
        # transition_model(before, after, expected) compares both boundaries.
        self.vision_model = vision_model
        self.transition_model = transition_model
        self.change_threshold = max(0.0, min(float(change_threshold), 1.0))

    def screen_changed(self, before: Any, after: Any) -> Dict[str, Any]:
        diff = _image_diff(before, after)
        return {"changed": diff >= self.change_threshold, "diff": round(diff, 4)}

    def verify(
        self,
        before: Any,
        after: Any,
        expected_state: str = "",
        action: str = "",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Confirm the screen reached ``expected_state`` after an action."""
        change = self.screen_changed(before, after)
        result: Dict[str, Any] = {
            "action": action,
            "changed": change["changed"],
            "diff": change["diff"],
            "expected_state": expected_state,
            "evidence": evidence or {},
        }
        if expected_state and (self.transition_model or self.vision_model):
            try:
                if self.transition_model:
                    response = self.transition_model(
                        decode_frame(before), decode_frame(after), expected_state
                    )
                else:
                    response = self.vision_model(decode_frame(after), expected_state)
                if isinstance(response, str):
                    response = {"detail": response, "matched": False}
                result["matched"] = bool(response.get("matched"))
                result["detail"] = response.get("detail") or response.get("description") or ""
                result["confidence"] = float(response.get("confidence", 0.7))
            except Exception as exc:
                result["matched"] = False
                result["detail"] = f"vision error: {exc}"
                result["confidence"] = 0.0
        else:
            # Pixel change is useful offline evidence, but explicitly low
            # confidence: it cannot prove a semantic state such as "installed".
            result["matched"] = change["changed"]
            result["detail"] = "screen change only (no semantic vision model)"
            result["confidence"] = min(0.5, max(0.1, change["diff"] * 2)) if change["changed"] else 0.0
        result["ok"] = bool(result["matched"])
        result["visual_result"] = result["detail"]
        return result

    def verify_action(
        self,
        action: str,
        before: Any,
        after: Any,
        expected_state: str,
        recording: Optional[str] = None,
        offset: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return an agent-memory-ready ACTION/VISUAL RESULT evidence record."""
        evidence: Dict[str, Any] = {}
        if recording:
            evidence["recording"] = recording
        if offset is not None:
            evidence["offset"] = round(float(offset), 3)
        result = self.verify(before, after, expected_state, action=action, evidence=evidence)
        return {
            **result,
            "memory": {
                "action": action,
                "visual_result": result["visual_result"],
                "confidence": result["confidence"],
                "evidence": evidence,
                "success": result["ok"],
            },
        }

    def verify_sequence(
        self,
        frames: List[Dict[str, Any]],
        expected_state: str = "",
        action: str = "",
        recording: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not frames:
            return {"ok": False, "detail": "no frames", "confidence": 0.0}
        available = [frame for frame in frames if decode_frame(frame) is not None]
        if not available:
            return {"ok": False, "detail": "no images", "confidence": 0.0}
        last = available[-1]
        evidence = {
            "recording": recording,
            "offset": last.get("offset"),
            "timestamp": last.get("ts"),
            "sequence": last.get("sequence"),
        }
        return self.verify(
            available[0],
            last,
            expected_state=expected_state,
            action=action,
            evidence={key: value for key, value in evidence.items() if value is not None},
        )


class ActionVerificationManager:
    """Capture exact BEFORE/AFTER boundaries around an externally-run action."""

    def __init__(self, recorder: Any):
        self.recorder = recorder
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def before(self, action: str, expected_state: str = "") -> Dict[str, Any]:
        if not self.recorder.running:
            return {"success": False, "error": "screen recorder is not running"}
        frame = self.recorder.capture_now(store=True)
        if frame is None:
            return {"success": False, "error": "could not capture BEFORE frame"}
        action_id = uuid.uuid4().hex[:12]
        item = {
            "action_id": action_id,
            "action": action,
            "expected_state": expected_state,
            "before": frame,
        }
        with self._lock:
            self._pending[action_id] = item
            while len(self._pending) > 100:
                self._pending.pop(next(iter(self._pending)))
        self.recorder.mark(action, kind="action_before", metadata={"action_id": action_id})
        return {
            "success": True,
            "action_id": action_id,
            "action": action,
            "expected_state": expected_state,
            "before": {
                "timestamp": frame.get("ts"),
                "offset": frame.get("offset"),
                "sequence": frame.get("sequence"),
            },
        }

    def after(self, action_id: str, verifier: Optional[ScreenVerifier] = None) -> Dict[str, Any]:
        with self._lock:
            item = self._pending.pop(action_id, None)
        if item is None:
            return {"success": False, "error": f"unknown or completed action_id: {action_id}"}
        frame = self.recorder.capture_now(store=True)
        if frame is None:
            with self._lock:
                self._pending[action_id] = item
            return {"success": False, "error": "could not capture AFTER frame", "action_id": action_id}
        marker = self.recorder.mark(item["action"], kind="action_after", metadata={"action_id": action_id})
        video = self.recorder.status().get("video") or {}
        result = (verifier or ScreenVerifier()).verify_action(
            item["action"],
            item["before"],
            frame,
            item["expected_state"],
            recording=video.get("path"),
            offset=frame.get("offset"),
        )
        return {"success": True, "action_id": action_id, "marker": marker, "verification": result}

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {key: value for key, value in item.items() if key != "before"}
                for item in self._pending.values()
            ]
