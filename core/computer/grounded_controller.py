"""Grounded Action Controller — wraps the computer controller with pre-click
visual verification.

Every ``click_target`` action is preceded by a re-grounding step that
verifies the target is still at the expected screen location with high
confidence before allowing the click.  This drastically reduces false
clicks on stale, moved, or occluded targets.
"""
from __future__ import annotations

from typing import Any, Optional
from collections.abc import Callable

from .controller import ComputerActionController
from .events import publish
from .grounding import (
    GroundedTarget,
    GroundingSystem,
    PreClickVerifier,
    VisualGrounder,
)
from .recorder import decode_frame


class PreClickVerificationError(Exception):
    """Raised when pre-click verification fails."""


class GroundedActionController:
    """Controller wrapper that verifies targets before every click.

    Usage::

        controller = ComputerActionController(...)
        grounder = VisualGrounder(vision_model=model)
        grounded = GroundedActionController(controller, grounder)

        # Now every click_target goes through pre-click verification:
        result = grounded.click_target("Install")  # verifies first

    The verification step:
    1. Captures a fresh frame
    2. Re-grounds the target description in that frame
    3. Checks overlap with any previous bounding box
    4. Only proceeds if confidence >= threshold and overlap >= threshold
    """

    def __init__(
        self,
        controller: ComputerActionController,
        grounder: Optional[VisualGrounder] = None,
        grounding_system: Optional[GroundingSystem] = None,
        min_confidence: float = 0.5,
        overlap_threshold: float = 0.5,
        verification_enabled: bool = True,
        frame_provider: Optional[Callable[[], Any]] = None,
    ):
        self.controller = controller
        self.verification_enabled = verification_enabled
        self.min_confidence = min_confidence
        self.overlap_threshold = overlap_threshold

        if grounding_system:
            self.grounding = grounding_system
            self.grounder = grounding_system.grounder
            self.verifier = grounding_system.verifier
        elif grounder:
            self.grounder = grounder
            self.verifier = PreClickVerifier(
                grounder=grounder,
                overlap_threshold=overlap_threshold,
                confidence_threshold=min_confidence,
            )
            self.grounding = GroundingSystem(
                vision_model=grounder.vision_model,
                min_confidence=min_confidence,
                verification_threshold=min_confidence,
            )
        else:
            self.grounder = VisualGrounder()
            self.verifier = PreClickVerifier(
                grounder=self.grounder,
                overlap_threshold=overlap_threshold,
                confidence_threshold=min_confidence,
            )
            self.grounding = GroundingSystem(
                min_confidence=min_confidence,
                verification_threshold=min_confidence,
            )

        self.frame_provider = frame_provider
        self._last_target_cache: dict[str, GroundedTarget] = {}

    def _capture_frame(self) -> Any:
        """Get a fresh frame for verification."""
        if self.frame_provider:
            try:
                return self.frame_provider()
            except Exception:
                pass
        if hasattr(self.controller, "capture_screen"):
            try:
                return self.controller.capture_screen()
            except Exception:
                pass
        return None

    def _verify_target(
        self,
        target: str,
        use_cache: bool = True,
    ) -> tuple[bool, Optional[GroundedTarget], str]:
        """Verify a target is still visible and at the expected location.

        Args:
            target: The target description to verify
            use_cache: If True, compare against last known location

        Returns:
            Tuple of (verified, current_target, reason)
        """
        frame = self._capture_frame()
        if frame is None:
            return True, None, "no frame available — skipping verification"

        screen_size = self._get_screen_size(frame)

        # Ground the target in the current frame
        current = self.grounder.ground(frame, target, screen_size)

        if current is None:
            return False, None, f"target '{target}' not found on screen"

        if current.confidence < self.min_confidence:
            return (
                False,
                current,
                f"low confidence {current.confidence:.2f} < {self.min_confidence}",
            )

        # Check overlap with cached location
        if use_cache and target in self._last_target_cache:
            previous = self._last_target_cache[target]
            overlap = previous.bbox.overlap_ratio(current.bbox)
            if overlap < self.overlap_threshold:
                return (
                    False,
                    current,
                    f"target moved (overlap {overlap:.2f} < {self.overlap_threshold})",
                )

        # Update cache
        self._last_target_cache[target] = current
        return True, current, "target verified"

    def _get_screen_size(self, frame: Any) -> tuple[int, int]:
        """Extract screen size from a frame."""
        image = decode_frame(frame)
        if image is not None:
            try:
                return int(image.width), int(image.height)
            except Exception:
                pass
        return (1920, 1080)

    # -- Delegated methods -----------------------------------------------

    def click_target(self, target: str) -> dict[str, Any]:
        """Click a target with pre-click verification."""
        if self.verification_enabled:
            verified, current, reason = self._verify_target(target)
            publish("pre_click_verification", {
                "target": target,
                "verified": verified,
                "reason": reason,
                "bbox": current.bbox.to_dict() if current else None,
                "confidence": current.confidence if current else 0.0,
            })

            if not verified:
                return {
                    "ok": False,
                    "action": "click_target",
                    "target": target,
                    "error": f"pre-click verification failed: {reason}",
                    "grounding_failure": True,
                    "verified": False,
                    "reason": reason,
                }

            # Use grounded click point instead of blind target text
            if current:
                cx, cy = current.get_click_point()
                return {
                    **self.controller.click(int(cx), int(cy)),
                    "grounded": True,
                    "bbox": current.bbox.to_dict(),
                    "confidence": current.confidence,
                }

        return self.controller.click_target(target)

    def click(self, x: int, y: int) -> dict[str, Any]:
        """Direct coordinate click (no verification needed)."""
        return self.controller.click(x, y)

    def double_click(self, x: int, y: int) -> dict[str, Any]:
        return self.controller.double_click(x, y)

    def right_click(self, x: int, y: int) -> dict[str, Any]:
        return self.controller.right_click(x, y)

    def type_text(self, text: str) -> dict[str, Any]:
        return self.controller.type_text(text)

    def press_key(self, key: str) -> dict[str, Any]:
        return self.controller.press_key(key)

    def hotkey(self, *keys: str) -> dict[str, Any]:
        return self.controller.hotkey(*keys)

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> dict[str, Any]:
        return self.controller.scroll(amount, x, y)

    def open_application(self, name: str) -> dict[str, Any]:
        return self.controller.open_application(name)

    def close_application(self, name: str) -> dict[str, Any]:
        return self.controller.close_application(name)

    def focus_window(self, name: str) -> dict[str, Any]:
        return self.controller.focus_window(name)

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        return self.controller.move_mouse(x, y)

    def find_on_screen(self, target: str) -> dict[str, Any]:
        """Find a target and return structured grounding info."""
        frame = self._capture_frame()
        if frame is None:
            return {"found": False, "target": target, "error": "no frame"}
        screen_size = self._get_screen_size(frame)
        current = self.grounder.ground(frame, target, screen_size)
        if current is None:
            return {"found": False, "target": target}
        return {
            "found": True,
            "target": target,
            "x": current.bbox.center[0],
            "y": current.bbox.center[1],
            "bbox": current.bbox.to_dict(),
            "confidence": current.confidence,
            "role": current.role,
            "state": current.state,
            "safe_to_click": current.safe_to_click,
        }

    def toggle_verification(self, enabled: bool) -> None:
        """Enable or disable pre-click verification."""
        self.verification_enabled = enabled

    @property
    def history(self) -> list[dict[str, Any]]:
        """Delegate to underlying controller's action history."""
        return getattr(self.controller, "history", [])

    @property
    def mouse(self):
        return self.controller.mouse

    @mouse.setter
    def mouse(self, value):
        self.controller.mouse = value

    @property
    def keyboard(self):
        return self.controller.keyboard

    @keyboard.setter
    def keyboard(self, value):
        self.controller.keyboard = value

    @property
    def windows(self):
        return self.controller.windows

    @windows.setter
    def windows(self, value):
        self.controller.windows = value


def wrap_with_grounding(
    controller: ComputerActionController,
    vision_model: Optional[Callable[[Any, str], dict[str, Any]]] = None,
    **kwargs,
) -> GroundedActionController:
    """Wrap a ComputerActionController with pre-click visual verification.

    Usage::

        controller = ComputerActionController(...)
        grounded = wrap_with_grounding(controller, vision_model=my_vision_model)
        # Use grounded exactly like a ComputerActionController
    """
    grounder = VisualGrounder(vision_model=vision_model)
    return GroundedActionController(
        controller=controller,
        grounder=grounder,
        **kwargs,
    )