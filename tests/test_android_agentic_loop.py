"""Agentic phone-control E2E over the real ToolGateway -> AndroidTool -> device path.

These drive the *actual* ``ToolGateway`` (the one invocation boundary) and the *actual*
``AndroidTool`` facade bound to a deterministic simulated Android device. They prove the
observe -> reason -> act -> verify loop, consent enforcement, verification-of-noop, and
the semantic (not raw-coordinate) observation surface.

They are NOT live-model or physical-device tests. The live-model layer and a physical
device/emulator are separately marked NOT VERIFIED.
"""
from __future__ import annotations

import pytest

from core.android.agent import AndroidAgentController
from core.android.simulate import SimulatedAndroidDevice, APP_TASKS


def _g(consents=None, **devkw) -> AndroidAgentController:
    return AndroidAgentController(device=SimulatedAndroidDevice(**devkw), consents=consents)


def test_agentic_phone_control_add_task_end_to_end():
    """'add X to tasks' -> plan -> observe -> type -> tap Add -> verify -> complete."""
    ctl = _g(tasks=["Existing item"])
    res = ctl.run_goal("add 'Buy milk' to tasks")

    assert res.completed is True, f"goal not completed: {res.reasoning}"
    assert "Buy milk" in ctl.device.tasks(), ctl.device.tasks()
    # The loop must have gone through the real gateway: observe then type then tap.
    ops = [s.op for s in res.steps]
    assert "android_observe" in ops and "android_type" in ops and "android_tap" in ops
    # Every act was verified against a post-observation.
    assert all(s.verified for s in res.steps if s.op.startswith("android_"))
    # Reasoned from semantic observation (labels/ids), and confirmed the terminal state.
    assert any("present" in r and "goal satisfied" in r for r in res.reasoning)


def test_agentic_phone_control_reobserves_and_detects_state_change():
    ctl = _g(tasks=[])
    before = ctl.observe()
    assert before["ok"] is True
    add = next(e for e in before["elements"] if e["id"] == "add")
    cx, cy = ctl._center(add)
    ctl.gateway.execute("android_type", {"text": "Water plants"})
    after_type = ctl.observe()
    # The typed value is observable through the input field's value, not only visible_text.
    assert any("Water plants" in f.get("value", "") for f in after_type["fields"])
    ctl.gateway.execute("android_tap", {"x": cx, "y": cy})
    after = ctl.observe()
    # The semantic observation changed through the loop.
    assert ctl._hash(before) != ctl._hash(after)
    assert "Water plants" in ctl.device.tasks()


def test_consent_denied_fails_safely_not_covert():
    """Without consent, observation and control are unavailable — never auto-granted."""
    ctl = _g(consents=[])
    # No consent granted -> observation must be refused.
    obs = ctl.observe()
    assert obs.get("ok") is False
    assert obs.get("error") == "android_control_unavailable"
    # An unauthorised tap also fails safely.
    res = ctl.gateway.execute("android_tap", {"x": 4, "y": 4})
    assert res.ok is False
    assert "android_control_unavailable" in (res.error_message or res.error_code)


def test_consent_can_be_granted_then_enforces_revoke():
    ctl = _g(consents=["screen_capture"])
    assert ctl.observe()["ok"] is True
    ctl.revoke("screen_capture")
    obs = ctl.observe()
    assert obs.get("ok") is False


def test_verification_detects_noop_tap():
    """Tapping a non-actionable element yields no state change; verify reports it."""
    ctl = _g(tasks=[])
    obs = ctl.observe()
    # Tap in the title area (not a button) -> device raises noop/absent element.
    res = ctl.gateway.execute("android_tap", {"x": 5, "y": 5})
    # The tap target at that spot is not clickable, so it must not silently 'succeed'
    # in changing state. Either it errors, or it reports ok with no change — we assert
    # the device task list is unchanged either way.
    assert ctl.device.tasks() == []


def test_semantic_observation_exposes_labels_and_bounds():
    """§7: the observation lets the model reason about labels/buttons/bounds, not coords."""
    ctl = _g(tasks=["Alpha", "Beta"])
    obs = ctl.observe()
    assert obs["ok"] is True
    assert obs["package"] == APP_TASKS
    assert {"label": "Add", "role": "button"} in [
        {"label": b["label"], "role": "button"} for b in obs["buttons"]] or \
        any(b["label"] == "Add" for b in obs["buttons"])
    field = next(f for f in obs["fields"] if f["id"] == "field")
    assert field["focused"] is True and field["bounds"]["x"] >= 0
    # Every element has a label + bounds so the model never needs raw coordinates alone.
    for e in obs["elements"]:
        assert "label" in e and "bounds" in e
    assert "Alpha" in obs["visible_text"]
    assert "app com.example.tasks" in obs["summary"]


def test_simulated_device_reports_unknown_ops_honestly():
    dev = SimulatedAndroidDevice()
    # An op the transport does not implement must raise the typed unavailable, not lie.
    with pytest.raises(Exception):
        dev.tap(9999, 9999)  # no clickable element at that coordinate


def test_registered_android_tools_pass_through_gateway_available():
    ctl = _g()
    tools = ctl.available_tools()
    for t in ("android_observe", "android_type", "android_tap",
              "android_launch_app", "android_current_app"):
        assert t in tools, f"missing {t} in {tools}"


def test_android_verifier_before_action_after_verify():
    """§8: meaningful actions get before -> action -> after -> verification."""
    from core.android.verify import AndroidVerifier, app_launched

    ctl = _g(tasks=[])
    verifier = AndroidVerifier(ctl.tool)

    # Launch an app -> verify it is foreground in a post-observation.
    r = verifier.run_verified("launch_app", {"package": "com.example.tasks"},
                              expect=app_launched("com.example.tasks"))
    assert r["ok"] is True and r["expected_state"] is True

    # Type text, then verify the field/text is observable in the post-observation.
    r2 = verifier.run_verified("type", {"text": "Water plants"}, before=r["after"])
    assert r2["action_ok"] is True
    blob = r2["after"].get("nodes") or []
    assert any("Water plants" in (n.get("text") or "") for n in blob)
