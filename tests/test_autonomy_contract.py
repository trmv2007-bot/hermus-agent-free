from core.autonomy import AutonomyFacade


class FakeLoop:
    def __init__(self, result):
        self.result = result

    def execute(self, text, **kwargs):
        return self.result


def test_autonomy_requires_completion_and_not_failed_verification():
    result = AutonomyFacade(FakeLoop({"state": "completed", "mission_id": "m1", "verified": True})).run("do it")
    assert result.ok is True
    assert result.mission_id == "m1"


def test_autonomy_does_not_promote_unverified_completion():
    result = AutonomyFacade(FakeLoop({"state": "completed", "mission_id": "m2", "verified": False})).run("do it")
    assert result.ok is False
    assert result.verified is False


def test_autonomy_does_not_treat_running_as_success():
    result = AutonomyFacade(FakeLoop({"state": "running", "mission_id": "m3"})).run("do it")
    assert result.ok is False
