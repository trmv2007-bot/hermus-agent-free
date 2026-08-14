"""Hermus Computer Agent v2: autonomous plan → act → record → verify → repair."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from core.computer import (
    ComputerActionController,
    ComputerAgent,
    ComputerPolicy,
    ComputerSkillStore,
    ControlCenter,
    DryRunKeyboard,
    DryRunMouse,
    DryRunWindowBackend,
    EmergencyStop,
    RecordingPolicy,
    ScreenRecorder,
    CallableSource,
    TargetDetector,
    VisualStateMachine,
    dispatch_action,
    extract_json_object,
)


class _FreshController:
    """Build an isolated controller (no shared global emergency-stop state)."""

    @staticmethod
    def make(frame_provider=None, locator=None, emergency=None, policy=None):
        return ComputerActionController(
            mouse=DryRunMouse(),
            keyboard=DryRunKeyboard(),
            window_manager=DryRunWindowBackend(),
            frame_provider=frame_provider,
            target_detector=TargetDetector(locator=locator),
            emergency=emergency or EmergencyStop(),
            policy=policy or ComputerPolicy(),
        )


# -- primitives ---------------------------------------------------------

def test_controller_action_primitives_are_recorded_dry_run():
    controller = _FreshController.make()
    result = controller.click(100, 200)
    assert result["ok"] and result["dry_run"] is True
    assert result["action"] == "click" and result["args"] == {"x": 100, "y": 200, "button": "left"}
    assert controller.type_text("hello")["ok"]
    assert controller.press_key("ENTER")["ok"]
    assert controller.hotkey("CTRL", "L")["ok"]
    assert controller.double_click(1, 2)["ok"]
    assert controller.right_click(3, 4)["ok"]
    assert controller.scroll(-3)["ok"]
    assert controller.move_mouse(5, 6)["ok"]
    assert controller.open_application("Firefox")["ok"]
    assert controller.close_application("Firefox")["ok"]
    assert controller.focus_window("Terminal")["ok"]
    assert len(controller.history) == 11


def test_high_risk_action_requires_approval():
    policy = ComputerPolicy()
    controller = _FreshController.make(policy=policy)
    # shell/sudo escalates to HIGH and is denied until approved.
    denied = controller._gate("sudo", {"cmd": "rm -rf /"})
    assert denied["allowed"] is False and denied["risk"] == "high"
    policy.approve("sudo", scope=controller.scope)
    allowed = controller._gate("sudo", {"cmd": "rm -rf /"})
    assert allowed["allowed"] is True


def test_emergency_stop_blocks_all_actions(tmp_path):
    emergency = EmergencyStop(str(tmp_path / ".computer-stop.json"))
    controller = _FreshController.make(emergency=emergency)
    assert controller.click(1, 1)["ok"]
    emergency.halt("user aborted")
    result = controller.click(2, 2)
    assert result["ok"] is False and "user aborted" in result["error"]
    # A separate EmergencyStop instance (another process) also sees the halt.
    assert EmergencyStop(str(tmp_path / ".computer-stop.json")).halted is True
    emergency.release()
    assert controller.click(3, 3)["ok"]


def test_emergency_stop_persists_across_instances(tmp_path):
    path = str(tmp_path / ".computer-stop.json")
    EmergencyStop(path).halt("runaway loop")
    assert EmergencyStop(path).halted is True
    assert EmergencyStop(path).reason == "runaway loop"
    EmergencyStop(path).release()
    assert EmergencyStop(path).halted is False


# -- target detection ---------------------------------------------------

def test_target_detector_scales_to_screen_coordinates():
    locator = lambda frame, target: {  # noqa: E731
        "found": True, "x": 50, "y": 25, "confidence": 0.9, "description": target
    }
    detector = TargetDetector(locator=locator)
    image = Image.new("RGB", (320, 160), "white")
    frame = {"size": [640, 320], "data": None, "image": image}
    result = detector.find_on_screen(frame, "Install button")
    assert result["found"] is True
    # 50,25 in the 320x160 decoded image → 100,50 on the 640x320 screen.
    assert result["x"] == 100.0 and result["y"] == 50.0


def test_extract_json_object_from_vision_prose():
    text = 'The Install button is visible. {"found": true, "x": 742, "y": 381, "confidence": 0.94, "description": "Install button"}'
    parsed = extract_json_object(text)
    assert parsed["found"] is True and parsed["x"] == 742 and parsed["confidence"] == 0.94
    assert extract_json_object("no json here") is None


def test_click_target_locates_then_clicks():
    state = {"frame": Image.new("RGB", (64, 48), "white")}
    controller = _FreshController.make(
        frame_provider=lambda: {"size": [64, 48], "image": state["frame"]},
        locator=lambda frame, target: {"found": True, "x": 10, "y": 10, "confidence": 0.8, "description": target},
    )
    result = controller.click_target("Install button")
    assert result["ok"] and result["action"] == "click_target"
    assert result["located_x"] == 10.0 and result["confidence"] == 0.8


# -- state machine ------------------------------------------------------

def test_state_machine_runs_plan_to_success():
    controller = _FreshController.make(
        frame_provider=lambda: {"size": [64, 48], "image": Image.new("RGB", (64, 48), "white")},
        locator=lambda frame, target: {"found": True, "x": 1, "y": 1, "confidence": 0.9, "description": target},
    )
    plan = [
        {"name": "open", "expected": "", "action": {"kind": "open_application", "name": "Installer"}},
        {"name": "click", "expected": "Install button appears", "action": {"kind": "click_target", "target": "Install button"}},
    ]
    states = VisualStateMachine.plan_to_states(plan)
    machine = VisualStateMachine(
        controller=controller,
        wait_until=lambda c, t: {"matched": True, "success": True},
        execute=lambda spec: dispatch_action(controller, spec),
        verify=lambda b, a, e: {"ok": True, "detail": "pass", "confidence": 0.9},
    )
    report = machine.run(states)
    assert report["success"] is True and report["final_state"] == "SUCCESS"


def test_state_machine_repairs_on_failure():
    calls = {"n": 0}
    controller = _FreshController.make()
    # First attempt fails; the runner retries and succeeds on attempt 2.
    def flaky_verify(before, after, expected):
        calls["n"] += 1
        return {"ok": calls["n"] >= 2, "detail": f"attempt {calls['n']}"}

    plan = [{"name": "click", "expected": "", "action": {"kind": "type_text", "text": "hi"}}]
    states = VisualStateMachine.plan_to_states(plan)
    machine = VisualStateMachine(
        controller=controller,
        execute=lambda spec: dispatch_action(controller, spec),
        verify=flaky_verify,
        max_retries=2,
    )
    report = machine.run(states)
    assert report["success"] is True
    attempts = [v for v in report["states_visited"] if "attempt" in v]
    assert len(attempts) == 2  # one failure + one success


# -- skill store --------------------------------------------------------

def test_skill_store_save_recall_and_list(tmp_path):
    store = ComputerSkillStore(str(tmp_path / "skills"))
    store.save_skill(
        "Install Foo",
        [{"name": "open", "action": "open_application", "args": {"name": "Foo"}},
         {"name": "click", "action": "click_target", "args": {"target": "Install"}}],
        evidence={"recording": "recording.mp4"},
    )
    assert len(store.list_skills()) == 1
    recalled = store.recall("Install Foo application")
    assert recalled is not None and recalled.name == "install-foo"
    assert store.recall("bake a cake") is None


# -- full autonomous agent ----------------------------------------------

def test_agent_run_produces_evidence_bundle_and_skill(tmp_path):
    state = {"n": 0}

    def source():
        state["n"] += 1
        return Image.new("RGB", (64, 48), "black" if state["n"] % 2 == 1 else "white")

    recorder = ScreenRecorder(source=CallableSource(source), fps=20, max_seconds=30)
    controller = _FreshController.make(
        frame_provider=recorder.latest,
        locator=lambda frame, target: {"found": True, "x": 10, "y": 10, "confidence": 0.9, "description": target},
    )
    policy = RecordingPolicy(str(tmp_path / "recordings"))
    skills = ComputerSkillStore(str(tmp_path / "recordings" / "skills"))
    agent = ComputerAgent(controller=controller, recorder=recorder, policy=policy, skills=skills)

    result = agent.run("Click the Install button", task_id="install-app")
    assert result["success"] is True and result["result"] == "SUCCESS"
    assert len(result["actions"]) == 1
    assert len(result["verifications"]) == 1
    assert result["recording"] is None  # no FFmpeg in CI → RAM-only, still succeeds

    task_dir = Path(result["artifacts"]["directory"])
    assert (task_dir / "timeline.json").exists()
    assert (task_dir / "actions.json").exists()
    assert (task_dir / "result.json").exists()
    assert (task_dir / "verification.json").exists()
    assert (task_dir / "summary.md").exists()
    summary = (task_dir / "summary.md").read_text()
    assert "Result: SUCCESS" in summary and "Actions: 1" in summary

    # A successful run promotes a reusable skill.
    assert result["skill"]["success"] is True
    assert skills.recall("install the app") is not None


def test_agent_failure_is_diagnosed_not_blindly_repeated(tmp_path):
    # A detector that cannot find the target → the loop reports FAILURE with a
    # description instead of emitting infinite clicks.
    recorder = ScreenRecorder(source=CallableSource(lambda: Image.new("RGB", (64, 48), "white")), fps=20, max_seconds=30)
    controller = _FreshController.make(
        frame_provider=recorder.latest,
        locator=lambda frame, target: {"found": False, "confidence": 0.0, "description": "target not visible"},
    )
    policy = RecordingPolicy(str(tmp_path / "recordings"))
    agent = ComputerAgent(
        controller=controller,
        recorder=recorder,
        policy=policy,
        skills=ComputerSkillStore(str(tmp_path / "recordings" / "skills")),
        learn_skills=False,
    )
    result = agent.run("Click the missing button", task_id="missing")
    assert result["success"] is False and result["result"] == "FAILURE"
    assert result["error"]


# -- control center -----------------------------------------------------

def test_control_center_renders_panel():
    controller = _FreshController.make()
    controller.click(1, 1)
    text = ControlCenter(controller).render()
    assert "HERMUS COMPUTER" in text and "ACTIVE" in text
