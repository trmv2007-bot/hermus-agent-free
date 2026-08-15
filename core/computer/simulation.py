"""Simulation Environment — test Hermus's computer agent without a real display.

The simulation provides:
- A virtual "screen" with named elements at known positions
- Simulated mouse and keyboard with state tracking
- Deliberate error injection (popups, missing buttons, delays, failed clicks)
- Expected-state tracking for verification

This lets us run the full plan->act->verify->repair chain in a deterministic
environment and test recovery flows safely.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .grounding import BoundingBox, GroundedTarget, VisualGrounder
from .mouse import MouseBackend
from .keyboard import KeyboardBackend
from .window_manager import WindowBackend
from .world_state import WorldState


@dataclass
class SimulatedElement:
    """A UI element on the simulated screen."""

    name: str
    bbox: BoundingBox
    role: str = "button"
    state: str = "enabled"  # enabled, disabled, hidden, loading
    text: str = ""
    clickable: bool = True
    visible: bool = True


@dataclass
class SimulatedWindow:
    """A window on the simulated desktop."""

    title: str
    application: str
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    minimized: bool = False
    focused: bool = False
    elements: List[SimulatedElement] = field(default_factory=list)


class SimulatedScreen:
    """A virtual screen with windows and elements that the agent interacts with.

    The screen can be configured to:
    - Show specific windows and elements at known positions
    - Inject popups/dialogs at specific times
    - Make certain clicks fail (simulating stale targets)
    - Verify expected post-action states
    """

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
        self.windows: Dict[str, SimulatedWindow] = {}
        self.popups: List[SimulatedWindow] = []
        self.active_popup: Optional[SimulatedWindow] = None
        self._mouse_pos: Tuple[int, int] = (0, 0)
        self._click_history: List[Dict[str, Any]] = []
        self._fail_next_click: bool = False
        self._fail_next_target: Optional[str] = None
        self._inject_popup_at: Optional[int] = None
        self._state_change_callbacks: List[Callable] = []
        self._current_time: float = 0.0
        self._planned_errors: List[Dict[str, Any]] = []

    def add_window(self, window: SimulatedWindow) -> None:
        """Add a window to the desktop."""
        self.windows[window.title] = window

    def remove_window(self, title: str) -> bool:
        """Remove a window from the desktop."""
        return self.windows.pop(title, None) is not None

    def get_active_window(self) -> Optional[SimulatedWindow]:
        """Get the currently focused window."""
        for win in self.windows.values():
            if win.focused:
                return win
        # First window is active by default
        for win in self.windows.values():
            if not win.minimized:
                win.focused = True
                return win
        return None

    def focus_window(self, title: str) -> bool:
        """Focus a specific window."""
        for win in self.windows.values():
            win.focused = win.title == title
        return title in self.windows

    def minimize_window(self, title: str) -> bool:
        """Minimize a window."""
        if title in self.windows:
            self.windows[title].minimized = True
            self.windows[title].focused = False
            return True
        return False

    def restore_window(self, title: str) -> bool:
        """Restore a minimized window."""
        if title in self.windows:
            self.windows[title].minimized = False
            self.windows[title].focused = True
            return True
        return False

    def add_popup(self, popup: SimulatedWindow) -> None:
        """Add a popup/blocking dialog."""
        self.popups.append(popup)
        self.active_popup = popup

    def dismiss_popup(self) -> bool:
        """Dismiss the active popup."""
        if self.active_popup:
            self.popups = [p for p in self.popups if p.title != self.active_popup.title]
            self.active_popup = None
            return True
        return False

    def get_element(self, name: str) -> Optional[SimulatedElement]:
        """Find an element by name across all visible windows."""
        # Check active popup first
        if self.active_popup:
            for elem in self.active_popup.elements:
                if elem.name.lower() == name.lower() and elem.visible:
                    return elem

        # Check windows
        for win in self.windows.values():
            if win.minimized:
                continue
            for elem in win.elements:
                if elem.name.lower() == name.lower() and elem.visible:
                    return elem
        return None

    def get_elements_by_role(self, role: str) -> List[SimulatedElement]:
        """Get all visible elements with a specific role."""
        elements = []
        # From active popup
        if self.active_popup:
            for elem in self.active_popup.elements:
                if elem.role == role and elem.visible:
                    elements.append(elem)
        # From windows
        for win in self.windows.values():
            if win.minimized:
                continue
            for elem in win.elements:
                if elem.role == role and elem.visible:
                    elements.append(elem)
        return elements

    def schedule_error(self, error_type: str, target: Optional[str] = None,
                       at_step: int = 0) -> None:
        """Schedule a deliberate error at a specific step."""
        self._planned_errors.append({
            "type": error_type,
            "target": target,
            "at_step": at_step,
            "triggered": False,
        })

    def simulate_click(self, x: int, y: int) -> Dict[str, Any]:
        """Simulate a click on the virtual screen.

        Returns the result with info about what was clicked.
        """
        x, y = int(x), int(y)
        self._mouse_pos = (x, y)

        # Check for planned errors
        for error in self._planned_errors:
            if not error["triggered"] and error["type"] == "click_fails":
                error["triggered"] = True
                self._click_history.append({
                    "x": x, "y": y, "success": False,
                    "reason": "simulated click failure",
                    "time": self._current_time,
                })
                return {"ok": False, "x": x, "y": y, "error": "simulated click failure"}

        # Find what was clicked
        clicked_element = None
        clicked_window = None

        # Check active popup first
        if self.active_popup:
            for elem in self.active_popup.elements:
                if elem.bbox.contains(x, y) and elem.visible and elem.clickable:
                    clicked_element = elem
                    clicked_window = self.active_popup
                    break

        # Check windows
        if not clicked_element:
            for win in self.windows.values():
                if win.minimized:
                    continue
                for elem in win.elements:
                    if elem.bbox.contains(x, y) and elem.visible and elem.clickable:
                        clicked_element = elem
                        clicked_window = win
                        break

        success = clicked_element is not None

        # Handle special element interactions
        if clicked_element:
            # If it's a "close" or dismiss action on a popup
            if clicked_element.role == "close" and self.active_popup:
                self.dismiss_popup()

            # If element becomes disabled after click
            if clicked_element.state == "enabled":
                clicked_element.state = "clicked"

        self._click_history.append({
            "x": x, "y": y, "success": success,
            "element": clicked_element.name if clicked_element else None,
            "window": clicked_window.title if clicked_window else None,
            "time": self._current_time,
        })

        for cb in self._state_change_callbacks:
            try:
                cb({"type": "click", "x": x, "y": y, "success": success,
                    "element": clicked_element.name if clicked_element else None})
            except Exception:
                pass

        return {
            "ok": success,
            "x": x, "y": y,
            "clicked": clicked_element.name if clicked_element else None,
            "window": clicked_window.title if clicked_window else None,
        }

    def simulate_type(self, text: str) -> Dict[str, Any]:
        """Simulate typing text."""
        return {"ok": True, "text": text, "length": len(text)}

    def simulate_keypress(self, key: str) -> Dict[str, Any]:
        """Simulate pressing a key."""
        result = {"ok": True, "key": key}

        # Handle special keys
        if key.lower() == "escape" and self.active_popup:
            self.dismiss_popup()
            result["popup_dismissed"] = True

        return result

    def get_visible_text(self) -> str:
        """Get a text representation of the visible screen state."""
        lines = []
        lines.append(f"=== Simulated Desktop ({self.width}x{self.height}) ===")

        for win in self.windows.values():
            status = " (minimized)" if win.minimized else " (focused)" if win.focused else ""
            lines.append(f"\nWindow: {win.title} [{win.application}]{status}")
            if not win.minimized:
                for elem in win.elements:
                    if elem.visible:
                        state = f" [{elem.state}]" if elem.state != "enabled" else ""
                        lines.append(f"  - {elem.name} ({elem.role}) at {elem.bbox}{state}")

        if self.active_popup:
            lines.append(f"\n!! POPUP: {self.active_popup.title} !!")
            for elem in self.active_popup.elements:
                if elem.visible:
                    lines.append(f"  - {elem.name} ({elem.role})")

        return "\n".join(lines)

    def get_world_state(self) -> Dict[str, Any]:
        """Get current world state as a dict (compatible with WorldState)."""
        active = self.get_active_window()
        return {
            "active_application": active.application if active else None,
            "active_window": active.title if active else None,
            "visible_targets": self._get_visible_targets(),
            "dialogs": [p.title for p in self.popups if p == self.active_popup],
            "mouse_position": list(self._mouse_pos),
            "task_state": "RUNNING",
            "confidence": 0.95,
        }

    def _get_visible_targets(self) -> List[str]:
        targets = []
        if self.active_popup:
            for elem in self.active_popup.elements:
                if elem.visible:
                    targets.append(elem.name)
        for win in self.windows.values():
            if win.minimized:
                continue
            for elem in win.elements:
                if elem.visible:
                    targets.append(elem.name)
        return targets

    def on_state_change(self, callback: Callable) -> None:
        """Register a callback for state changes."""
        self._state_change_callbacks.append(callback)


class SimulatedMouse(MouseBackend):
    """Mouse backend that operates on a SimulatedScreen."""

    def __init__(self, screen: SimulatedScreen):
        self.screen = screen
        self._position: Tuple[int, int] = (0, 0)

    def move(self, x: int, y: int) -> bool:
        self._position = (int(x), int(y))
        self.screen._mouse_pos = (int(x), int(y))
        return True

    def click(self, button: str = "left") -> bool:
        x, y = self._position
        result = self.screen.simulate_click(x, y)
        return result.get("ok", False)

    def double_click(self, button: str = "left") -> bool:
        return self.click(button) and self.click(button)

    def right_click(self) -> bool:
        return self.click("right")

    def scroll(self, amount: int) -> bool:
        return True

    def position(self) -> Tuple[int, int]:
        return self._position

    def drag(self, x: int, y: int) -> bool:
        self._position = (int(x), int(y))
        return True


class SimulatedKeyboard(KeyboardBackend):
    """Keyboard backend for simulation."""

    def __init__(self, screen: SimulatedScreen):
        self.screen = screen
        self._history: List[str] = []

    def type(self, text: str) -> bool:
        self._history.append(f"type:{text}")
        self.screen.simulate_type(text)
        return True

    def press(self, key: str) -> bool:
        self._history.append(f"press:{key}")
        self.screen.simulate_keypress(key)
        return True

    def hotkey(self, *keys: str) -> bool:
        combo = "+".join(keys)
        self._history.append(f"hotkey:{combo}")
        for key in keys:
            self.screen.simulate_keypress(key)
        return True


class SimulatedWindowManager(WindowBackend):
    """Window backend for simulation."""

    def __init__(self, screen: SimulatedScreen):
        self.screen = screen

    def list_windows(self) -> List[Dict[str, Any]]:
        return [
            {"title": win.title, "application": win.application,
             "minimized": win.minimized, "focused": win.focused}
            for win in self.screen.windows.values()
        ]

    def focus_window(self, title: str) -> bool:
        return self.screen.focus_window(title)

    def minimize_window(self, title: str) -> bool:
        return self.screen.minimize_window(title)

    def restore_window(self, title: str) -> bool:
        return self.screen.restore_window(title)

    def close_window(self, title: str) -> bool:
        return self.screen.remove_window(title)


class SimulatedGrounder(VisualGrounder):
    """Grounder that operates on SimulatedScreen instead of images."""

    def __init__(self, screen: SimulatedScreen):
        super().__init__()
        self.screen = screen

    def ground(
        self,
        frame: Any,
        description: str,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> Optional[GroundedTarget]:
        """Find a target on the simulated screen by name."""
        element = self.screen.get_element(description)
        if element is None:
            return None
        return GroundedTarget(
            name=element.name,
            bbox=element.bbox,
            confidence=0.95,
            role=element.role,
            state=element.state,
            text=element.text,
            safe_to_click=element.clickable and element.state == "enabled",
        )

    def ground_all(
        self,
        frame: Any,
        descriptions: List[str],
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> List[GroundedTarget]:
        targets = []
        for desc in descriptions:
            target = self.ground(frame, desc, screen_size)
            if target:
                targets.append(target)
        return targets


# ---- Helper: create standard test scenarios ----


def calculator_scenario() -> SimulatedScreen:
    """Create a SimulatedScreen showing a Calculator window."""
    screen = SimulatedScreen()
    screen.add_window(SimulatedWindow(
        title="Calculator",
        application="Calculator",
        x=100, y=100, width=400, height=500,
        focused=True,
        elements=[
            SimulatedElement("Display", BoundingBox(120, 120, 460, 170), "display",
                            text="0"),
            SimulatedElement("1", BoundingBox(120, 190, 200, 260), "button", text="1"),
            SimulatedElement("2", BoundingBox(210, 190, 290, 260), "button", text="2"),
            SimulatedElement("3", BoundingBox(300, 190, 380, 260), "button", text="3"),
            SimulatedElement("+", BoundingBox(390, 190, 470, 260), "button", text="+"),
            SimulatedElement("=", BoundingBox(390, 270, 470, 340), "button", text="="),
            SimulatedElement("Clear", BoundingBox(120, 270, 200, 340), "button", text="C"),
        ],
    ))
    return screen


def notepad_scenario() -> SimulatedScreen:
    """Create a SimulatedScreen showing a Notepad window."""
    screen = SimulatedScreen()
    screen.add_window(SimulatedWindow(
        title="Untitled - Notepad",
        application="Notepad",
        x=200, y=100, width=800, height=600,
        focused=True,
        elements=[
            SimulatedElement("Text Area", BoundingBox(10, 50, 790, 590), "text_area",
                            text="", clickable=True),
            SimulatedElement("File Menu", BoundingBox(10, 10, 80, 30), "menu", text="File"),
            SimulatedElement("Edit Menu", BoundingBox(90, 10, 160, 30), "menu", text="Edit"),
            SimulatedElement("Close", BoundingBox(770, 10, 790, 30), "close", text="X"),
        ],
    ))
    return screen


def browser_scenario(url: str = "example.com") -> SimulatedScreen:
    """Create a SimulatedScreen showing a browser window."""
    screen = SimulatedScreen()
    screen.add_window(SimulatedWindow(
        title=f"{url} - Browser",
        application="Firefox",
        x=50, y=50, width=1200, height=800,
        focused=True,
        elements=[
            SimulatedElement("Address Bar", BoundingBox(50, 60, 700, 85), "input", text=url),
            SimulatedElement("Reload", BoundingBox(720, 60, 760, 85), "button", text="⟳"),
            SimulatedElement("Content Area", BoundingBox(50, 90, 1150, 750), "content",
                            text=f"Welcome to {url}"),
            SimulatedElement("Close", BoundingBox(1170, 50, 1190, 70), "close", text="X"),
        ],
    ))
    return screen


def popup_scenario() -> SimulatedScreen:
    """Create a screen with a blocking popup."""
    screen = browser_scenario()
    screen.add_popup(SimulatedWindow(
        title="Permission Required",
        application="System",
        x=300, y=200, width=500, height=300,
        elements=[
            SimulatedElement("Allow", BoundingBox(320, 440, 420, 480), "button",
                            text="Allow", clickable=True),
            SimulatedElement("Block", BoundingBox(440, 440, 540, 480), "button",
                            text="Block", clickable=True),
            SimulatedElement("Close", BoundingBox(770, 200, 790, 220), "close", text="X"),
        ],
    ))
    screen.active_popup = screen.popups[0] if screen.popups else None
    return screen


def installer_scenario() -> SimulatedScreen:
    """Create an installer scenario with unexpected UI."""
    screen = SimulatedScreen()
    screen.add_window(SimulatedWindow(
        title="Install Wizard",
        application="Installer",
        x=150, y=150, width=600, height=450,
        focused=True,
        elements=[
            SimulatedElement("Next", BoundingBox(400, 520, 500, 550), "button",
                            text="Next >", clickable=True),
            SimulatedElement("Cancel", BoundingBox(300, 520, 390, 550), "button",
                            text="Cancel", clickable=True),
            SimulatedElement("License Agreement", BoundingBox(160, 180, 740, 350),
                            "text_area", text="License terms..."),
            SimulatedElement("I Agree", BoundingBox(400, 370, 500, 400), "checkbox",
                            text="I agree to the terms", clickable=True),
        ],
    ))
    return screen


def download_error_scenario() -> SimulatedScreen:
    """Create a scenario where download fails with an error dialog."""
    screen = browser_scenario("download.com/file")
    # Add an error popup
    screen.add_popup(SimulatedWindow(
        title="Download Error",
        application="Browser",
        x=350, y=250, width=500, height=200,
        elements=[
            SimulatedElement("Retry", BoundingBox(370, 400, 470, 435), "button",
                            text="Retry", clickable=True),
            SimulatedElement("Cancel", BoundingBox(490, 400, 590, 435), "button",
                            text="Cancel", clickable=True),
            SimulatedElement("Close", BoundingBox(820, 250, 840, 270), "close", text="X"),
        ],
    ))
    screen.active_popup = screen.popups[0] if screen.popups else None
    return screen