from core.autonomy import AutonomyFacade


class FakeLoop:
    def __init__(self, result):
        self.result = result

    def execute(self, text, **kwargs):
        callback = kwargs.get("on_event")
        if callback:
            callback("mission_started", {"state": "running"})
            callback("mission_finished", {"state": self.result.get("state")})
        return self.result


def test_autonomy_attaches_run_health_and_correlates_events():
    events = []
    result = AutonomyFacade(FakeLoop({"state": "completed", "mission_id": "m6", "verified": True})).run(
        "finish it", run_id="run_fixed", on_event=lambda t, p: events.append((t, p))
    )

    assert result.ok is True
    assert result.result["run_id"] == "run_fixed"
    assert result.result["run_health"]["run_id"] == "run_fixed"
    assert result.result["run_health"]["event_count"] == 2
    assert all(payload["run_id"] == "run_fixed" for _, payload in events)


def test_autonomy_classifies_failed_runs_without_changing_success_contract():
    result = AutonomyFacade(
        FakeLoop(
            {
                "state": "failed",
                "mission_id": "m7",
                "verified": False,
                "failure": {
                    "error_type": "TimeoutError",
                    "reason": "provider timeout",
                    "recoverable": True,
                },
            }
        )
    ).run("finish it", run_id="run_fail")

    assert result.ok is False
    assert result.result["run_health"]["failure_class"] == "transient"
    assert result.result["run_health"]["retryable"] is True
