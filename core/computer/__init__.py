"""Hermus Computer Agent: Record → Detect → Understand → Verify → Act.

v1 added the recording pipeline (rolling capture, event detection, semantic
video analysis, timelines, before/after verification, and condition watching).
v2 adds the action layer that closes the loop into an *autonomous* computer
agent: gated mouse/keyboard/window backends, vision-driven target detection, a
visual state machine, skill learning from successful recordings, a permission
+ emergency-stop layer, and a task control center.

The package is headless-safe and dependency-injectable. Capture uses a bounded
compressed RAM buffer; full sessions can stream to MP4/WebM; only debounced
important frames are promoted to local vision analysis. Real pointer/keyboard
control uses ``pyautogui``/``pygetwindow`` when present and degrades to an
auditable dry-run otherwise, so every path is testable offline.
"""
from .controller import ComputerActionController
from .computer_agent import ComputerAgent
from .control_center import ControlCenter
from .event_detector import EventDetector, StreamingEventDetector
from .frame_sampler import FrameSampler
from .keyboard import DryRunKeyboard, KeyboardBackend, PyAutoGUIKeyboard, default_keyboard
from .mouse import DryRunMouse, MouseBackend, PyAutoGUIMouse, default_mouse
from .permissions import (
    ACTION_RISK,
    ComputerPolicy,
    EmergencyStop,
    RecordingPolicy,
    RiskLevel,
    computer_policy,
    emergency_stop,
    recording_policy,
)
from .recorder import (
    CallableSource,
    ImageGrabSource,
    NullSource,
    ScreenRecorder,
    ScreenSource,
    decode_frame,
    encode_image,
)
from .skills import ComputerSkill, ComputerSkillStore
from .state_machine import VisualState, VisualStateMachine, dispatch_action
from .target_detector import TargetDetector, extract_json_object
from .timeline import TaskArtifacts, Timeline, TimelineEvent
from .verifier import ActionVerificationManager, ScreenVerifier
from .video_analyzer import OllamaVisionModel, VideoAnalyzer
from .video_writer import VideoWriter
from .watcher import ScreenWatcher
from .window_manager import (
    DryRunWindowBackend,
    PyGetWindowBackend,
    WindowBackend,
    default_window_manager,
)

__all__ = [
    # v1 — recording / understanding / verification
    "ScreenRecorder",
    "ScreenSource",
    "ImageGrabSource",
    "NullSource",
    "CallableSource",
    "encode_image",
    "decode_frame",
    "FrameSampler",
    "VideoWriter",
    "EventDetector",
    "StreamingEventDetector",
    "OllamaVisionModel",
    "VideoAnalyzer",
    "Timeline",
    "TimelineEvent",
    "TaskArtifacts",
    "ScreenVerifier",
    "ActionVerificationManager",
    "ScreenWatcher",
    "RecordingPolicy",
    "recording_policy",
    # v2 — action engine / autonomy
    "MouseBackend",
    "PyAutoGUIMouse",
    "DryRunMouse",
    "default_mouse",
    "KeyboardBackend",
    "PyAutoGUIKeyboard",
    "DryRunKeyboard",
    "default_keyboard",
    "WindowBackend",
    "PyGetWindowBackend",
    "DryRunWindowBackend",
    "default_window_manager",
    "ComputerActionController",
    "TargetDetector",
    "extract_json_object",
    "ComputerPolicy",
    "RiskLevel",
    "ACTION_RISK",
    "computer_policy",
    "EmergencyStop",
    "emergency_stop",
    "VisualState",
    "VisualStateMachine",
    "dispatch_action",
    "ComputerSkill",
    "ComputerSkillStore",
    "ComputerAgent",
    "ControlCenter",
]
