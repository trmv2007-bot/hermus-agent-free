from core.autonomy import AutonomyFacade


class FakeLoop:
    def __init__(self, result):
        self.result = result

    def execute(self, text, on_event=None, **kwargs):
        assert text
        if on_event:
            on_event("request_started", {"text": text})
        return dict(self.result)


def test_autonomy_result_requires_completed_state():
    result = AutonomyFacade(FakeLoop({"state": "completed", "verified": True, "mission_id": "m1"})).run("do work")
    assert result.ok is True
    assert result.mission_id == "m1"


def test_autonomy_result_rejects_explicit_verification_failure():
    result = AutonomyFacade(FakeLoop({"state": "completed", "verified": False})).run("do work")
    assert result.ok is False


def test_autonomy_accepts_completed_unverified_result_without_claiming_verification():
    result = AutonomyFacade(FakeLoop({"state": "completed", "verified": None})).run("do work")
    assert result.ok is True
    assert result.verified is None
