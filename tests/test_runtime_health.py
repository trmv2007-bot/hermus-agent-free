from core.runtime_health import RunTracker, classify_failure


def test_run_tracker_correlates_events_and_completion():
    tracker = RunTracker(run_id="run_test")
    tracker.event()
    health = tracker.finish(state="completed")
    assert health.run_id == "run_test"
    assert health.event_count == 1
    assert health.completed is True
    assert health.elapsed_ms >= 0


def test_failure_classification_is_structured():
    assert classify_failure({"failure": {"error_type": "TimeoutError", "recoverable": True}}) == ("transient", True)
    assert classify_failure({"failure": {"reason": "approval gate blocked", "recoverable": False}}) == ("policy", False)
    assert classify_failure({"failure": {"recoverable": False}}) == ("terminal", False)
