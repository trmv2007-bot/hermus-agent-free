"""Shared test fixtures / isolation.

``core.computer.task_control.task_control`` is a process-wide singleton holding
global interrupt (emergency-stop) and task-registration state. Without resetting
it between tests, an emergency stop or a leaked task from one test can bleed into
a later one (e.g. ``VisualStateMachine.run`` aborting on a stale emergency-stop
flag, which is order-dependent and appears only under a broad combined order).
This autouse fixture clears that singleton before every test so each test starts
with a clean slate — a test-isolation root-cause fix, not a mock.
"""
from __future__ import annotations

import pytest

from core.computer.task_control import get_task_control


@pytest.fixture(autouse=True)
def _isolate_task_control_state():
    """Reset the process-wide TaskControl singleton before each test."""
    get_task_control().reset()
    yield
