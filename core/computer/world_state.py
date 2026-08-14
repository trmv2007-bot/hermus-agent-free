"""World State — persistent understanding of the current desktop environment."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class WorldState:
    """A snapshot of the current desktop environment as understood by vision."""

    application: Optional[str] = None
    window: Optional[str] = None
    elements: List[str] = field(default_factory=list)
    modal: Optional[str] = None
    task: Optional[str] = None
    current_state: Optional[str] = None
    confidence: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def update(self, observation: Dict[str, Any]) -> None:
        """Update the world model from a vision observation record."""
        self.timestamp = datetime.now().astimezone().isoformat()
        self.confidence = float(observation.get("confidence", self.confidence))
        
        # If the observation provides semantic details, try to extract them.
        detail = observation.get("detail", "")
        if not detail:
            return
            
        # Very simple heuristic parsing of vision descriptions.
        # In a real system, this would be a structured LLM output.
        if "window" in detail.lower():
            self.window = detail.split("window")[0].strip().split()[-1]
        
        if "button" in detail.lower() or "link" in detail.lower():
            # Add to elements if not already there
            self.elements.append(detail)
            
    def to_dict(self) -> Dict[str, Any]:
        return {
            "application": self.application,
            "window": self.window,
            "elements": self.elements[-10:], # Keep recent elements
            "modal": self.modal,
            "task": self.task,
            "current_state": self.current_state,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }
