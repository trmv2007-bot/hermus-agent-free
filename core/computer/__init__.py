"""Hermus Computer Agent: Record → Detect → Understand → Verify → Act.

v1 added the recording pipeline (rolling capture, event detection, semantic
video analysis, timelines, before/after verification, and condition watching).
v2 added the gated action layer. v3 adds a shared persistent WorldState,
validated task graphs, diagnosed/verified repair plans, evidence-backed skill
statistics, crash-safe task resume, persistent computer-operator workers, and
dependency-aware multi-agent delegation. v4 adds:
- Enhanced WorldState with observation types (OBSERVED/INFERRED/EXPECTED/UNKNOWN)
- Adaptive Replanner for dynamic plan modification
- Task Control System (Pause/Resume/Cancel)
- Visual Grounding System with pre-click verification

GUI input remains serialized through the same permission and emergency-stop gates.

The package is headless-safe and dependency-injectable. Capture uses a bounded
compressed RAM buffer; full sessions can stream to MP4/WebM; only debounced
important frames are promoted to local vision analysis. Real pointer/keyboard
control uses ``pyautogui``/``pygetwindow`` when present and degrades to an
auditable dry-run otherwise, so every path is testable offline.
"""
from .controller import ComputerActionController
from .computer_agent import ComputerAgent
from .control_center import ControlCenter
from .delegation import DelegationPlan, MultiAgentDelegator, WorkUnit
from .episodes import Episode, EpisodeStore, get_episode_store, record_episode
from .event_detector import EventDetector, StreamingEventDetector
from .frame_sampler import FrameSampler
from .grounded_controller import GroundedActionController, PreClickVerificationError, wrap_with_grounding
from .grounding import (
    BoundingBox,
    GroundedTarget,
    GroundingSystem,
    PreClickVerifier,
    VisualGrounder,
    create_grounding_system,
)
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
from .planner import ComputerPlanner, PlanNode, TaskGoal, TaskGraph
from .repair import FailureDiagnosis, FailureKind, RepairEngine, RepairPlan, RepairStep
from .replanner import (
    AdaptiveReplanner,
    PlanDelta,
    ReplanContext,
    ReplanReason,
    ReplanStrategy,
    create_replanner,
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
from .task_control import (
    ControlAction,
    ControlEvent,
    TaskControlContext,
    TaskControlManager,
    TaskControlState,
    get_task_control,
    task_control,
)
from .task_store import TaskCheckpoint, TaskStore
from .timeline import TaskArtifacts, Timeline, TimelineEvent
from .verifier import ActionVerificationManager, ScreenVerifier
from .video_analyzer import OllamaVisionModel, VideoAnalyzer
from .video_writer import VideoWriter
from .watcher import ScreenWatcher
from .world_state import WorldObservation, WorldState
from .simulation import (
    SimulatedScreen,
    SimulatedWindow,
    SimulatedElement,
    SimulatedMouse,
    SimulatedKeyboard,
    SimulatedWindowManager,
    SimulatedGrounder,
    calculator_scenario,
    notepad_scenario,
    browser_scenario,
    popup_scenario,
    installer_scenario,
    download_error_scenario,
)
from .benchmark import (
    BenchmarkResult,
    BenchmarkRunner,
    COMPUTER_TASKS,
    TaskSpec,
    get_task,
    list_tasks,
    run_benchmark,
)
from .world_state_v2 import (
    CertaintyLevel,
    DesktopContext,
    GroundedTarget as GroundedTargetV2,
    ObservationType,
    RichObservation,
    TaskContext,
    WorldStateV2,
    create_world_state,
)
from .window_manager import (
    DryRunWindowBackend,
    PyGetWindowBackend,
    WindowBackend,
    default_window_manager,
)
from .remote import (
    ApprovalPrompt,
    PromptState,
    RemoteApprovalGate,
    RemoteControlHub,
    remote_approval,
    remote_control,
)
from .resources import ResourceMonitor, get_resource_monitor, resource_monitor

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
    "FailureKind",
    "FailureDiagnosis",
    "RepairStep",
    "RepairPlan",
    "RepairEngine",
    "WorldObservation",
    "WorldState",
    "TaskGoal",
    "PlanNode",
    "TaskGraph",
    "ComputerPlanner",
    "TaskCheckpoint",
    "TaskStore",
    "ComputerSkill",
    "ComputerSkillStore",
    "ComputerAgent",
    "WorkUnit",
    "DelegationPlan",
    "MultiAgentDelegator",
    "ControlCenter",
    # v4 — Phase A: Enhanced capabilities
    # Task Control
    "TaskControlManager",
    "TaskControlState",
    "TaskControlContext",
    "ControlAction",
    "ControlEvent",
    "task_control",
    "get_task_control",
    # Visual Grounding
    "BoundingBox",
    "GroundedTarget",
    "VisualGrounder",
    "PreClickVerifier",
    "GroundingSystem",
    "create_grounding_system",
    # Adaptive Replanning
    "AdaptiveReplanner",
    "PlanDelta",
    "ReplanContext",
    "ReplanReason",
    "ReplanStrategy",
    "create_replanner",
    # Phase B — Reliability
    # Episode Memory
    "Episode",
    "EpisodeStore",
    "get_episode_store",
    "record_episode",
    # Grounded Controller
    "GroundedActionController",
    "wrap_with_grounding",
    # Simulation
    "SimulatedScreen",
    "SimulatedWindow",
    "SimulatedElement",
    "SimulatedMouse",
    "SimulatedKeyboard",
    "SimulatedWindowManager",
    "SimulatedGrounder",
    "calculator_scenario",
    "notepad_scenario",
    "browser_scenario",
    "popup_scenario",
    "installer_scenario",
    "download_error_scenario",
    # Benchmark
    "BenchmarkResult",
    "BenchmarkRunner",
    "COMPUTER_TASKS",
    "TaskSpec",
    "get_task",
    "list_tasks",
    "run_benchmark",
    # Enhanced World State
    "WorldStateV2",
    "ObservationType",
    "CertaintyLevel",
    "DesktopContext",
    "TaskContext",
    "RichObservation",
    "GroundedTargetV2",
    "create_world_state",
    # Phase C — Remote / approval control
    "RemoteApprovalGate",
    "RemoteControlHub",
    "ApprovalPrompt",
    "PromptState",
    "remote_approval",
    "remote_control",
    # Phase D — Performance / resources
    "ResourceMonitor",
    "get_resource_monitor",
    "resource_monitor",
]
