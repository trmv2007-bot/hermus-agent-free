"""The tool tier is a choice, so it is tested as one.

The default sends tool calls to the main model, because a tool loop needs a
model that reliably emits a well-formed call and a small local model does
not. ``local_model_handles_tools`` inverts that: talking, seeing and tool
calling all run locally, and the main model is kept for the reasoning-heavy
turns that are actually worth its quota.

Both directions are asserted here. A flag that only ever adds a path is a flag
nobody notices is off.
"""

from core.model_router import ModelRouter, REASON_MAIN, REASON_TOOLS, REASON_TOOLS_LOCAL

MAIN = "nvidia/nemotron-3-super-120b-a12b"
LOCAL = "ollama/spark-x2.5-4b-q4"


def _router(**kw) -> ModelRouter:
    return ModelRouter(main_model=MAIN, local_model=LOCAL, max_chars=280, **kw)


def test_tool_calls_go_to_the_main_model_by_default():
    r = _router()
    d = r.route([{"role": "user", "content": "open the terminal"}], has_tools=True)
    assert d.provider == "nvidia"
    assert d.reason == REASON_TOOLS


def test_tool_calls_go_local_when_the_operator_asks_for_it():
    r = _router(local_handles_tools=True)
    d = r.route([{"role": "user", "content": "open the terminal"}], has_tools=True)
    assert d.provider == "ollama"
    assert d.reason == REASON_TOOLS_LOCAL


def test_thinking_still_goes_to_the_main_model_when_tools_are_local():
    """The whole point of the split: local handles the cheap work, the main
    model keeps the quota for the expensive reasoning. A long prompt must still
    escalate even though tool calls no longer do."""
    r = _router(local_handles_tools=True)
    long_turn = [{"role": "user", "content": "x" * 5000}]
    d = r.route(long_turn, has_tools=False)
    assert d.provider == "nvidia"
    assert d.reason == REASON_MAIN


def test_short_talk_stays_local_either_way():
    for flag in (False, True):
        d = _router(local_handles_tools=flag).route([{"role": "user", "content": "hi"}], has_tools=False)
        assert d.provider == "ollama", f"short talk left local with flag={flag}"


def test_the_flag_does_not_change_the_model_names():
    """Only the tier moves. Asking for tools locally must not silently rewrite
    which model each tier is, or the decision becomes unreportable."""
    r = _router(local_handles_tools=True)
    d = r.route([{"role": "user", "content": "go"}], has_tools=True)
    assert d.model_name == "spark-x2.5-4b-q4"
