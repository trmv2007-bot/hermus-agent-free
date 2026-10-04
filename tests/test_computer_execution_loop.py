from core.computer.execution_loop import VerifiedComputerExecutor


class Controller:
    def click(self, x, y):
        return {"ok": True, "x": x, "y": y}


def test_observe_act_verify():
    states = iter(["before", "after"])
    executor = VerifiedComputerExecutor(
        Controller(),
        observer=lambda: next(states),
        verifier=lambda before, after, action: {"verified": before == "before" and after == "after"},
    )
    result = executor.execute("click", args={"x": 1, "y": 2})
    assert result["ok"] is True
    assert result["verification"]["verified"] is True


def test_unverified_action_can_recover():
    executor = VerifiedComputerExecutor(
        Controller(),
        observer=lambda: "state",
        verifier=lambda *_: {"verified": False, "reason": "state_unchanged"},
        recoverer=lambda result: {
            "verified": True,
            "reason": "recovery_succeeded",
        },
    )
    assert executor.execute("click", args={"x": 1, "y": 2})["ok"] is True
