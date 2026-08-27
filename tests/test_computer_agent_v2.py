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
    RepairEngine,
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


def test_repair_engine_popup_heuristic():
    from core.computer.repair import RepairEngine

    engine = RepairEngine(use_llm=False)
    plan = engine.repair("Unexpected popup detected", "YouTube loaded", {"description": "click address bar"})
    kinds = [s["action"]["kind"] for s in plan]
    assert "click_target" in kinds
    assert any("Close" in (s["action"].get("target") or "") for s in plan)
    diag = engine.diagnose("cookie banner blocking the page", "home page visible")
    assert diag.source == "heuristic"
    assert diag.plan[0]["action"]["target"] == "Accept all"


def test_state_machine_fails_explicitly_when_retries_exhausted():
    controller = _FreshController.make()
    plan = [{"name": "click", "expected": "ok", "action": {"kind": "type_text", "text": "hi"}}]
    states = VisualStateMachine.plan_to_states(plan)
    machine = VisualStateMachine(
        controller=controller,
        execute=lambda spec: dispatch_action(controller, spec),
        verify=lambda b, a, e: {"ok": False, "detail": "still on desktop"},
        repair=None,
        max_retries=1,
    )
    report = machine.run(states)
    assert report["success"] is False
    assert "still on desktop" in report["error"]
    assert report.get("reason") == "still on desktop"
    decisions = [v.get("decision") for v in report["states_visited"] if v.get("failure")]
    assert decisions == ["fail_task"]


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
    attempts = [v for v in report["states_visited"] if v.get("phase") == "original_action"]
    assert len(attempts) == 2  # one failure + one bounded retry success


def test_repair_engine_diagnoses_popup_without_llm_guessing():
    class NoLLM:
        def chat(self, messages):
            raise AssertionError("deterministic popup repair should not call the LLM")

    engine = RepairEngine(llm=NoLLM())
    plan = engine.create_plan(
        "Unexpected popup detected with a 'Not now' button",
        "YouTube is loaded",
        {
            "spec": {"kind": "click_target", "target": "address bar"},
            "result": {"ok": False, "error": "target not visible"},
            "verification": {"ok": False, "detail": "A popup is blocking the browser"},
        },
    )

    assert plan.available is True
    assert plan.source == "heuristic"
    assert plan.diagnosis.kind == "blocking_dialog"
    assert plan.steps[0].action == {"kind": "click_target", "target": "Not Now"}
    assert "no longer visible" in plan.steps[0].expected


def test_repair_engine_will_not_retry_permission_rejection():
    engine = RepairEngine(use_llm=False)
    plan = engine.create_plan(
        "permission policy denied",
        "Application is open",
        {"result": {"ok": False, "error": "permission policy denied"}},
    )
    assert plan.available is False
    assert plan.retry_original is False
    assert plan.diagnosis.retryable is False
    assert plan.diagnosis.kind == "action_rejected"


def test_repair_engine_sanitizes_model_actions():
    class FakeResponse:
        content = json.dumps({
            "retry_original": True,
            "steps": [
                {"name": "UNSAFE_COORDINATE", "action": {"kind": "click", "x": 10, "y": 10}, "expected": "gone"},
                {"name": "SAFE_ESCAPE", "action": {"kind": "press_key", "key": "escape"}, "expected": "The overlay is gone"},
            ],
        })

    class FakeLLM:
        def chat(self, messages):
            return FakeResponse()

    engine = RepairEngine(llm=FakeLLM())
    plan = engine.create_plan(
        "The screen shows different content than expected",
        "The settings page is visible",
        {
            "result": {"ok": True},
            "verification": {"ok": False, "changed": True, "detail": "Different content is visible"},
        },
    )
    assert plan.source == "llm"
    assert [step.name for step in plan.steps] == ["SAFE_ESCAPE"]
    assert plan.steps[0].action["kind"] == "press_key"


def test_state_machine_executes_verified_repair_before_original_retry():
    actions = []
    original_verifications = {"count": 0}

    def execute(spec):
        actions.append(spec["kind"])
        return {"ok": True, "action": spec["kind"], "description": spec["kind"]}

    def verify(before, after, expected):
        if "dialog" in expected.lower():
            return {"ok": True, "detail": "popup disappeared", "confidence": 0.95}
        original_verifications["count"] += 1
        if original_verifications["count"] == 1:
            return {"ok": False, "detail": "Unexpected popup with a Not now button", "confidence": 0.9}
        return {"ok": True, "detail": "YouTube loaded", "confidence": 0.95}

    states = VisualStateMachine.plan_to_states([{
        "name": "OPEN_YOUTUBE",
        "action": {"kind": "press_key", "key": "enter"},
        "expected": "YouTube is loaded",
    }])
    engine = RepairEngine(use_llm=False)
    report = VisualStateMachine(
        execute=execute,
        verify=verify,
        repair=engine.create_plan,
        max_retries=2,
    ).run(states)

    assert report["success"] is True
    assert actions == ["press_key", "click_target", "press_key"]
    phases = [event.get("phase") for event in report["states_visited"]]
    assert phases.index("diagnose") < phases.index("repair")
    assert any(event.get("outcome") == "retry_after_repair" for event in report["states_visited"])


def test_failed_repair_never_blindly_retries_original_action():
    actions = []

    def execute(spec):
        actions.append(spec["kind"])
        return {"ok": True, "action": spec["kind"]}

    def verify(before, after, expected):
        return {"ok": False, "detail": "popup still visible"}

    repair = lambda detail, expected, context: [{  # noqa: E731
        "name": "DISMISS",
        "action": {"kind": "press_key", "key": "escape"},
        "expected": "popup is gone",
    }]
    states = VisualStateMachine.plan_to_states([{
        "name": "ACT",
        "action": {"kind": "click_target", "target": "Continue"},
        "expected": "Next page is visible",
    }])
    report = VisualStateMachine(
        execute=execute,
        verify=verify,
        repair=repair,
        max_retries=3,
    ).run(states)

    assert report["success"] is False
    assert actions == ["click_target", "press_key"]
    assert report["failure"]["category"] == "repair_failed"
    assert "repair step 'DISMISS' failed" in report["error"]


def test_non_retryable_diagnosis_stops_even_with_retry_budget():
    calls = {"actions": 0}

    def denied(spec):
        calls["actions"] += 1
        return {"ok": False, "action": spec["kind"], "error": "permission policy denied"}

    states = VisualStateMachine.plan_to_states([{
        "name": "DENIED",
        "action": {"kind": "click_target", "target": "Allow"},
        "expected": "Permission accepted",
    }])
    engine = RepairEngine(use_llm=False)
    report = VisualStateMachine(
        execute=denied,
        verify=lambda b, a, e: {"ok": False, "detail": "permission policy denied"},
        repair=engine.create_plan,
        max_retries=5,
    ).run(states)

    assert report["success"] is False
    assert calls["actions"] == 1
    assert report["failure"]["category"] == "non_retryable"
    assert "not safe to retry" in report["error"]


def test_plan_preserves_explicit_failure_transition():
    states = VisualStateMachine.plan_to_states([{
        "name": "ACT",
        "action": {"kind": "press_key", "key": "enter"},
        "expected": "Next page",
        "on_failure": "FAILURE",
    }], terminal="FAILURE")
    assert states[0].on_failure == "FAILURE"
    report = VisualStateMachine(
        execute=lambda spec: {"ok": False, "error": "backend failed"},
        verify=lambda b, a, e: {"ok": False, "detail": "no change"},
        max_retries=0,
    ).run(states)
    assert report["success"] is False
    assert report["final_state"] == "FAILURE"
    assert any(event.get("outcome") == "failure_transition" for event in report["states_visited"])


def test_state_machine_invokes_repair_then_retries_original():
    order = []
    controller = _FreshController.make(
        frame_provider=lambda: {"size": [64, 48], "image": Image.new("RGB", (64, 48), "white")},
        locator=lambda frame, target: {"found": True, "x": 1, "y": 1, "confidence": 0.9, "description": target},
    )

    def execute(spec):
        order.append(spec.get("kind") or spec.get("target"))
        return dispatch_action(controller, spec)

    def verify(before, after, expected):
        # Original action fails until a repair click has run.
        if expected == "page ready":
            return {"ok": "click_target" in order, "detail": "Unexpected popup detected"}
        return {"ok": True, "detail": "repaired"}

    def repair(detail, expected, last):
        assert "popup" in detail.lower()
        return [{"name": "close_popup", "expected": "popup gone",
                 "action": {"kind": "click_target", "target": "Close"}}]

    plan = [{"name": "go", "expected": "page ready", "action": {"kind": "type_text", "text": "youtube.com"}}]
    states = VisualStateMachine.plan_to_states(plan)
    machine = VisualStateMachine(
        controller=controller,
        execute=execute,
        verify=verify,
        repair=repair,
        max_retries=2,
        wait_until=lambda c, t: {"matched": True},
    )
    report = machine.run(states)
    assert report["success"] is True
    diagnoses = [v for v in report["states_visited"] if str(v.get("state", "")).startswith("DIAGNOSE:")]
    repairs = [v for v in report["states_visited"] if str(v.get("state", "")).startswith("REPAIR:")]
    assert diagnoses and repairs


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
    assert result["recording"] is None or Path(result["recording"]).exists()

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


def test_computer_agent_wires_injected_repair_engine_into_state_machine(tmp_path):
    class SpyRepairEngine:
        def __init__(self):
            self.calls = []

        def create_plan(self, detail, expected, context):
            self.calls.append({"detail": detail, "expected": expected, "context": context})
            return []  # no repair available; state machine may use one bounded retry

    spy = SpyRepairEngine()
    recorder = ScreenRecorder(
        source=CallableSource(lambda: Image.new("RGB", (64, 48), "white")),
        fps=20,
        max_seconds=30,
    )
    controller = _FreshController.make(frame_provider=recorder.latest)
    agent = ComputerAgent(
        controller=controller,
        recorder=recorder,
        planner=lambda task: [{
            "name": "TYPE",
            "action": {"kind": "type_text", "text": "hello"},
            "expected": "The text hello is visible",
        }],
        repair_engine=spy,
        policy=RecordingPolicy(str(tmp_path / "recordings")),
        skills=ComputerSkillStore(str(tmp_path / "recordings" / "skills")),
        learn_skills=False,
        max_retries=1,
    )

    result = agent.run("type hello", task_id="repair-wiring")
    assert result["success"] is False
    assert len(spy.calls) == 1
    assert spy.calls[0]["context"]["spec"]["kind"] == "type_text"
    assert spy.calls[0]["context"]["verification"]["ok"] is False
    assert Path(result["repairs_path"]).exists()


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
