"""Visual Grounding System - Structured target representation with verification.

This module provides:

1. GroundedTarget: Structured representation of UI elements with:
   - Bounding box (x1, y1, x2, y2)
   - Confidence score
   - Role (button, link, field, etc.)
   - State (enabled, disabled, visible, etc.)
   - Safe-to-click determination

2. VisualGrounder: Uses vision model to ground text descriptions to screen locations.

3. Pre-click verification: Validates target is still where we think it is.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .world_state import WorldState


@dataclass
class BoundingBox:
    """Screen bounding box coordinates."""
    x1: float
    y1: float
    x2: float
    y2: float
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
    
    @property
    def width(self) -> float:
        return self.x2 - self.x1
    
    @property
    def height(self) -> float:
        return self.y2 - self.y1
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    def contains(self, x: float, y: float) -> bool:
        """Check if a point is inside the box."""
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2
    
    def overlap_ratio(self, other: "BoundingBox") -> float:
        """Calculate overlap ratio between two boxes."""
        x_overlap = max(0, min(self.x2, other.x2) - max(self.x1, other.x1))
        y_overlap = max(0, min(self.y2, other.y2) - max(self.y1, other.y1))
        overlap_area = x_overlap * y_overlap
        min_area = min(self.area, other.area)
        return overlap_area / min_area if min_area > 0 else 0.0
    
    def to_dict(self) -> Dict[str, float]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}
    
    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> "BoundingBox":
        return cls(
            x1=float(data.get("x1", 0)),
            y1=float(data.get("y1", 0)),
            x2=float(data.get("x2", 0)),
            y2=float(data.get("y2", 0)),
        )
    
    @classmethod
    def from_center(cls, cx: float, cy: float, width: float, height: float) -> "BoundingBox":
        """Create box from center point and dimensions."""
        return cls(
            x1=cx - width / 2,
            y1=cy - height / 2,
            x2=cx + width / 2,
            y2=cy + height / 2,
        )


@dataclass
class GroundedTarget:
    """A visually grounded UI element with full provenance."""
    name: str                          # Human-readable label
    bbox: BoundingBox                  # Screen location
    confidence: float = 0.0            # Detection confidence (0-1)
    role: str = "unknown"              # Element type
    state: str = "unknown"            # Element state
    text: str = ""                    # Text content
    element_type: str = ""             # HTML/Semantic type
    safe_to_click: bool = False        # Is it safe to click?
    clickable: bool = False            # Can be clicked?
    interactive: bool = False         # Is interactive?
    parent_region: Optional[str] = None # Parent container
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    
    # Vision metadata
    vision_model: Optional[str] = None
    vision_confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "bbox": self.bbox.to_dict(),
            "confidence": round(self.confidence, 4),
            "role": self.role,
            "state": self.state,
            "text": self.text,
            "element_type": self.element_type,
            "safe_to_click": self.safe_to_click,
            "clickable": self.clickable,
            "interactive": self.interactive,
            "parent_region": self.parent_region,
            "timestamp": self.timestamp,
            "vision_model": self.vision_model,
            "vision_confidence": round(self.vision_confidence, 4),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroundedTarget":
        bbox_data = data.get("bbox", {})
        bbox = BoundingBox.from_dict(bbox_data) if isinstance(bbox_data, dict) else BoundingBox(0, 0, 0, 0)
        return cls(
            name=str(data.get("name", "")),
            bbox=bbox,
            confidence=float(data.get("confidence", 0.0)),
            role=str(data.get("role", "unknown")),
            state=str(data.get("state", "unknown")),
            text=str(data.get("text", "")),
            element_type=str(data.get("element_type", "")),
            safe_to_click=bool(data.get("safe_to_click", False)),
            clickable=bool(data.get("clickable", False)),
            interactive=bool(data.get("interactive", False)),
            parent_region=data.get("parent_region"),
            timestamp=str(data.get("timestamp", datetime.now().astimezone().isoformat())),
            vision_model=data.get("vision_model"),
            vision_confidence=float(data.get("vision_confidence", 0.0)),
        )
    
    def get_click_point(self) -> Tuple[float, float]:
        """Get the recommended click point (center by default)."""
        cx, cy = self.bbox.center
        # Add small random offset to avoid systematic misclicks
        import random
        offset = min(5, self.bbox.width / 10, self.bbox.height / 10)
        cx += random.uniform(-offset, offset)
        cy += random.uniform(-offset, offset)
        return (cx, cy)


class VisualGrounder:
    """Grounds text descriptions to screen locations using vision.
    
    This is the "visual grounding" system mentioned in the roadmap:
    
    Target: Install button
    
    bbox: x1 y1 x2 y2
    
    confidence: 0.94
    
    role: button
    
    state: enabled
    
    safe_to_click: true
    """
    
    def __init__(
        self,
        vision_model: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
        min_confidence: float = 0.5,
        default_role: str = "unknown",
    ):
        self.vision_model = vision_model
        self.min_confidence = min_confidence
        self.default_role = default_role
        self._last_frame: Optional[Any] = None
        self._last_targets: List[GroundedTarget] = []
    
    def set_vision_model(self, model: Callable[[Any, str], Dict[str, Any]]) -> None:
        """Set the vision model for grounding."""
        self.vision_model = model
    
    def ground(
        self,
        frame: Any,
        description: str,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Optional[GroundedTarget]:
        """Ground a text description to a screen location.
        
        Args:
            frame: Current screen frame
            description: Text description of the target
            screen_size: Screen resolution for coordinate normalization
        
        Returns:
            GroundedTarget or None if not found
        """
        if self.vision_model is None:
            return None
        
        try:
            result = self.vision_model(frame, f"Find and locate: {description}")
        except Exception:
            return None
        
        if not isinstance(result, dict):
            return None
        
        # Parse the vision result
        target = self._parse_vision_result(result, description, screen_size)
        
        if target and target.confidence >= self.min_confidence:
            self._last_frame = frame
            self._last_targets.append(target)
            return target
        
        return None
    
    def ground_all(
        self,
        frame: Any,
        descriptions: List[str],
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> List[GroundedTarget]:
        """Ground multiple descriptions to screen locations."""
        targets = []
        for desc in descriptions:
            target = self.ground(frame, desc, screen_size)
            if target:
                targets.append(target)
        return targets
    
    def _parse_vision_result(
        self,
        result: Dict[str, Any],
        description: str,
        screen_size: Tuple[int, int],
    ) -> Optional[GroundedTarget]:
        """Parse vision model output into a GroundedTarget."""
        # Try structured format first
        if "targets" in result and isinstance(result["targets"], list):
            for t in result["targets"]:
                if isinstance(t, dict) and self._matches_description(t, description):
                    return self._target_from_structured(t, screen_size)
        
        # Try bounding_boxes format
        if "bounding_boxes" in result and isinstance(result["bounding_boxes"], list):
            boxes = result["bounding_boxes"]
            labels = result.get("labels", result.get("classes", []))
            
            for i, box in enumerate(boxes):
                if isinstance(box, (list, tuple)) and len(box) >= 4:
                    label = labels[i] if i < len(labels) else description
                    if self._matches_description({"name": label}, description):
                        bbox = BoundingBox(
                            x1=float(box[0]),
                            y1=float(box[1]),
                            x2=float(box[2]),
                            y2=float(box[3]),
                        )
                        return GroundedTarget(
                            name=str(label),
                            bbox=bbox,
                            confidence=float(result.get("confidences", [0.8])[i] if i < len(result.get("confidences", [])) else 0.8),
                            role=self._infer_role(label),
                            state="visible",
                            safe_to_click=self._is_likely_clickable(label),
                            clickable=True,
                        )
        
        # Try prose format
        if "description" in result or "text" in result:
            text = result.get("description", result.get("text", ""))
            coords = self._extract_coords_from_text(text, screen_size)
            
            if coords:
                return GroundedTarget(
                    name=description,
                    bbox=coords,
                    confidence=float(result.get("confidence", 0.7)),
                    role=self._infer_role(description),
                    state="visible",
                    safe_to_click=self._is_likely_clickable(description),
                    clickable=True,
                )
        
        # Try bbox directly
        if "bbox" in result:
            bbox_data = result["bbox"]
            if isinstance(bbox_data, (list, tuple)) and len(bbox_data) >= 4:
                bbox = BoundingBox(
                    x1=float(bbox_data[0]),
                    y1=float(bbox_data[1]),
                    x2=float(bbox_data[2]),
                    y2=float(bbox_data[3]),
                )
                return GroundedTarget(
                    name=description,
                    bbox=bbox,
                    confidence=float(result.get("confidence", 0.7)),
                    role=self._infer_role(description),
                    state="visible",
                    safe_to_click=self._is_likely_clickable(description),
                    clickable=True,
                )
        
        return None
    
    def _target_from_structured(
        self,
        data: Dict[str, Any],
        screen_size: Tuple[int, int],
    ) -> GroundedTarget:
        """Create GroundedTarget from structured vision output."""
        bbox_data = data.get("bbox", data.get("bounding_box", {}))
        
        if isinstance(bbox_data, dict):
            bbox = BoundingBox.from_dict(bbox_data)
        elif isinstance(bbox_data, (list, tuple)) and len(bbox_data) >= 4:
            bbox = BoundingBox(
                x1=float(bbox_data[0]),
                y1=float(bbox_data[1]),
                x2=float(bbox_data[2]),
                y2=float(bbox_data[3]),
            )
        else:
            # Default to center of screen
            w, h = screen_size
            bbox = BoundingBox(x1=w/4, y1=h/4, x2=3*w/4, y2=3*h/4)
        
        return GroundedTarget(
            name=str(data.get("name", data.get("label", "unknown"))),
            bbox=bbox,
            confidence=float(data.get("confidence", data.get("score", 0.7))),
            role=str(data.get("role", data.get("type", "unknown"))),
            state=str(data.get("state", "visible")),
            text=str(data.get("text", "")),
            element_type=str(data.get("element_type", "")),
            safe_to_click=bool(data.get("safe_to_click", self._is_likely_clickable(data.get("name", "")))),
            clickable=bool(data.get("clickable", data.get("interactive", False))),
            interactive=bool(data.get("interactive", False)),
            parent_region=data.get("parent_region"),
        )
    
    def _matches_description(self, target_data: Dict[str, Any], description: str) -> bool:
        """Check if a target matches the description."""
        desc_lower = description.lower()
        name = str(target_data.get("name", target_data.get("label", ""))).lower()
        text = str(target_data.get("text", "")).lower()
        
        return (desc_lower in name or 
                name in desc_lower or 
                desc_lower in text or
                any(word in name for word in desc_lower.split() if len(word) > 3))
    
    def _infer_role(self, text: str) -> str:
        """Infer the role/type of an element from its text."""
        text_lower = text.lower()
        
        if any(word in text_lower for word in ["button", "btn"]):
            return "button"
        if any(word in text_lower for word in ["link", "url"]):
            return "link"
        if any(word in text_lower for word in ["field", "input", "text", "search"]):
            return "field"
        if any(word in text_lower for word in ["menu", "dropdown", "select"]):
            return "menu"
        if any(word in text_lower for word in ["tab", "header", "sidebar"]):
            return "navigation"
        if any(word in text_lower for word in ["dialog", "modal", "popup", "alert"]):
            return "dialog"
        if any(word in text_lower for word in ["checkbox", "toggle", "switch"]):
            return "control"
        if any(word in text_lower for word in ["icon", "image", "img"]):
            return "icon"
        
        return "unknown"
    
    def _is_likely_clickable(self, text: str) -> bool:
        """Determine if an element is likely clickable based on text."""
        text_lower = text.lower()
        
        clickable_indicators = [
            "button", "btn", "link", "click", "submit", "save", "cancel",
            "ok", "yes", "no", "close", "delete", "edit", "add", "new",
            "download", "upload", "install", "start", "stop", "play",
            "pause", "next", "back", "previous", "continue", "proceed",
        ]
        
        return any(indicator in text_lower for indicator in clickable_indicators)
    
    def _extract_coords_from_text(
        self,
        text: str,
        screen_size: Tuple[int, int],
    ) -> Optional[BoundingBox]:
        """Extract coordinates from text description."""
        import re
        
        # Look for coordinate patterns
        patterns = [
            r"\((\d+),\s*(\d+)\)\s*-\s*\((\d+),\s*(\d+)\)",
            r"\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]",
            r"x1[:\s]*(\d+)[,\s]+y1[:\s]*(\d+)[,\s]+x2[:\s]*(\d+)[,\s]+y2[:\s]*(\d+)",
        ]
        
        w, h = screen_size
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                coords = [int(x) for x in match.groups()]
                return BoundingBox(
                    x1=float(coords[0]),
                    y1=float(coords[1]),
                    x2=float(coords[2]),
                    y2=float(coords[3]),
                )
        
        return None
    
    def get_last_targets(self) -> List[GroundedTarget]:
        """Get the last set of grounded targets."""
        return list(self._last_targets)


class PreClickVerifier:
    """Verifies targets are still where we think they are before clicking.
    
    This is critical for reducing false clicks - we verify immediately
    before clicking, not just at planning time.
    """
    
    def __init__(
        self,
        grounder: VisualGrounder,
        overlap_threshold: float = 0.7,
        confidence_threshold: float = 0.6,
    ):
        self.grounder = grounder
        self.overlap_threshold = overlap_threshold
        self.confidence_threshold = confidence_threshold
        self._verification_history: List[Dict[str, Any]] = []
    
    def verify(
        self,
        target: GroundedTarget,
        frame: Any,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Dict[str, Any]:
        """Verify a target is still valid before clicking.
        
        Returns:
            Dict with:
            - verified: bool
            - confidence: float
            - current_bbox: BoundingBox or None
            - overlap_ratio: float
            - reason: str
        """
        # Re-ground the target
        current = self.grounder.ground(frame, target.name, screen_size)
        
        result = {
            "verified": False,
            "target_name": target.name,
            "original_bbox": target.bbox.to_dict(),
            "current_bbox": None,
            "overlap_ratio": 0.0,
            "confidence": 0.0,
            "reason": "",
            "safe_to_click": False,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        
        if current is None:
            result["reason"] = "Target not found in current frame"
            result["confidence"] = 0.0
            self._verification_history.append(result)
            return result
        
        result["current_bbox"] = current.bbox.to_dict()
        result["confidence"] = current.confidence
        
        # Calculate overlap
        overlap = target.bbox.overlap_ratio(current.bbox)
        result["overlap_ratio"] = overlap
        
        # Check if still safe to click
        result["safe_to_click"] = (
            current.confidence >= self.confidence_threshold and
            overlap >= self.overlap_threshold and
            current.state in ("visible", "enabled", "unknown")
        )
        
        if result["safe_to_click"]:
            result["verified"] = True
            result["reason"] = "Target verified and safe to click"
        elif current.confidence < self.confidence_threshold:
            result["reason"] = f"Low confidence ({current.confidence:.2f} < {self.confidence_threshold})"
        elif overlap < self.overlap_threshold:
            result["reason"] = f"Target moved (overlap {overlap:.2f} < {self.overlap_threshold})"
        elif current.state in ("disabled", "hidden"):
            result["reason"] = f"Target in invalid state: {current.state}"
        else:
            result["reason"] = "Target verification failed for unknown reason"
        
        self._verification_history.append(result)
        return result
    
    def verify_and_get_click_point(
        self,
        target: GroundedTarget,
        frame: Any,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Tuple[Optional[Tuple[float, float]], Dict[str, Any]]:
        """Verify target and return click point if safe.
        
        Returns:
            Tuple of (click_point, verification_result)
        """
        verification = self.verify(target, frame, screen_size)
        
        if not verification["verified"]:
            return None, verification
        
        # Get click point from current target location
        if verification["current_bbox"]:
            bbox = BoundingBox.from_dict(verification["current_bbox"])
            cx, cy = bbox.center
            return (cx, cy), verification
        
        return None, verification
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Get verification history."""
        return list(self._verification_history)


class GroundingSystem:
    """Complete visual grounding system combining all components."""
    
    def __init__(
        self,
        vision_model: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
        min_confidence: float = 0.5,
        verification_threshold: float = 0.6,
    ):
        self.grounder = VisualGrounder(vision_model=vision_model, min_confidence=min_confidence)
        self.verifier = PreClickVerifier(
            grounder=self.grounder,
            confidence_threshold=verification_threshold,
        )
        self._targets: List[GroundedTarget] = []
    
    def set_vision_model(self, model: Callable[[Any, str], Dict[str, Any]]) -> None:
        """Set the vision model for grounding."""
        self.grounder.set_vision_model(model)
    
    def ground_target(
        self,
        frame: Any,
        description: str,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Optional[GroundedTarget]:
        """Ground a target description to a screen location."""
        target = self.grounder.ground(frame, description, screen_size)
        if target:
            self._targets.append(target)
        return target
    
    def verify_before_click(
        self,
        target: GroundedTarget,
        frame: Any,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Tuple[bool, Optional[Tuple[float, float]], Dict[str, Any]]:
        """Verify target and get click point.
        
        Returns:
            Tuple of (verified, click_point, verification_result)
        """
        point, result = self.verifier.verify_and_get_click_point(target, frame, screen_size)
        return result["verified"], point, result
    
    def get_all_targets(self) -> List[GroundedTarget]:
        """Get all grounded targets."""
        return list(self._targets)
    
    def clear_targets(self) -> None:
        """Clear all grounded targets."""
        self._targets = []


# Factory function
def create_grounding_system(
    vision_model: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
    **kwargs,
) -> GroundingSystem:
    """Create a visual grounding system."""
    return GroundingSystem(vision_model=vision_model, **kwargs)
