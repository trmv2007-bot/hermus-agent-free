"""Doctor must name whether desktop control is REAL or simulated.

The failure this guards: without the ``gui`` extra everything looks healthy
(plans render, action records exist, verify passes) while the agent cannot move
the mouse. A doctor that stays silent about that is worse than no doctor.
"""

from __future__ import annotations

from core import doctor as doctor_mod
from core.doctor import HermusDoctor as Doctor


def _finding(cap: dict) -> object:
    """Run only the computer-control check against a stubbed capability probe."""
    import core.computer as computer_mod

    original = computer_mod.detect_computer_capability
    computer_mod.detect_computer_capability = lambda *a, **k: cap
    try:
        findings = Doctor._analyze_computer_control()
    finally:
        computer_mod.detect_computer_capability = original
    return findings[0] if findings else None


def test_real_control_reported_as_info_not_a_problem() -> None:
    cap = {
        "available": True,
        "dry_run_only": False,
        "reason": "",
        "backends": {
            "mouse": {"backend": "PyAutoGUIMouse", "real": True, "dry_run": False},
            "keyboard": {"backend": "PyAutoGUIKeyboard", "real": True, "dry_run": False},
        },
    }
    f = _finding(cap)
    assert f is not None
    assert f.id.startswith("computer_control_real")
    # Real control is working, so it must not nag the user with a fix.
    assert f.severity == doctor_mod.SEVERITY_INFO
    assert not f.fixes


def test_simulated_control_is_flagged_with_the_reason_and_a_fix() -> None:
    cap = {
        "available": False,
        "dry_run_only": True,
        "reason": "pyautogui unavailable: No module named 'pyautogui'",
        "backends": {
            "mouse": {
                "backend": "DryRunMouse",
                "real": False,
                "dry_run": True,
                "fallback_reason": "pyautogui unavailable: No module named 'pyautogui'",
            },
            "keyboard": {"backend": "DryRunKeyboard", "real": False, "dry_run": True},
            "window": {"backend": "DryRunWindowBackend", "real": False, "dry_run": True},
        },
    }
    f = _finding(cap)
    assert f is not None
    assert f.id.startswith("computer_control_simulated")
    assert f.severity == doctor_mod.SEVERITY_MEDIUM
    # The user must learn WHICH dependency is missing, not just that it is.
    assert "pyautogui" in f.evidence
    assert any("gui" in fix for fix in f.fixes)


def test_probe_failure_is_reported_not_raised() -> None:
    """A broken probe must not take the whole doctor down."""
    import core.computer as computer_mod

    def boom(*a, **k):
        raise RuntimeError("display went away")

    original = computer_mod.detect_computer_capability
    computer_mod.detect_computer_capability = boom
    try:
        findings = Doctor._analyze_computer_control()
    finally:
        computer_mod.detect_computer_capability = original

    assert len(findings) == 1
    assert findings[0].id.startswith("computer_control_unknown")
    assert "display went away" in findings[0].evidence


def test_check_is_wired_into_the_full_analysis() -> None:
    """Wiring guard: a check that is defined but never called is dead code."""
    import inspect

    source = inspect.getsource(Doctor.analyze)
    assert "_analyze_computer_control()" in source
