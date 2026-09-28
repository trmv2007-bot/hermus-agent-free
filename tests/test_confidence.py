"""The confidence bar, proven against real model output.

The fixtures here are logprobs captured from the actual local model on this
machine, not numbers chosen to make an assertion pass. A confidence gate
tested only against invented logprobs is a gate tested against nothing.

  "What is the capital of France?"  first token logprob -0.0710
  a made-up tool name, unprompted   first token logprob -2.41
"""

import math

import pytest

from core.confidence import (
    DEFAULT_BAR,
    Escalation,
    assess,
    sequence_confidence,
    token_confidence,
)

# Captured from spark-x2.5-4b-q4 via /api/chat with logprobs:true.
CONFIDENT = [-0.0710, -0.1102, -0.2301, -0.0512, -0.0930]
UNSURE = [-2.4100, -1.8800, -0.3400, -2.0100]


class TestTokenConfidence:
    def test_a_logprob_becomes_a_probability(self):
        assert token_confidence(0.0) == pytest.approx(1.0)
        assert token_confidence(-0.6931) == pytest.approx(0.5, abs=1e-3)

    def test_none_stays_unknown_rather_than_becoming_zero(self):
        # Zero would silently drag a sequence mean down for a token that was
        # never measured. Unknown has to stay distinguishable from certain.
        assert token_confidence(None) is None

    @pytest.mark.parametrize("bad", [float("nan"), float("-inf")])
    def test_non_finite_logprobs_are_maximal_uncertainty(self, bad):
        assert token_confidence(bad) == 0.0

    def test_nonsense_is_rejected_not_crashed_on(self):
        assert token_confidence("not a number") is None


class TestSequenceConfidence:
    def test_no_tokens_is_unknown_not_confident(self):
        assert sequence_confidence([]) is None
        assert sequence_confidence([None, None]) is None

    def test_confident_sequence_reads_high(self):
        assert sequence_confidence(CONFIDENT) > 0.85

    def test_unsure_sequence_reads_low(self):
        assert sequence_confidence(UNSURE) < 0.35

    def test_geometric_mean_beats_a_plain_mean_on_a_lone_bad_token(self):
        # Why this is a geometric mean and not an arithmetic one over
        # probabilities. 19 easy tokens and one impossible token: the plain
        # mean of probabilities reads ~0.90 and would clear any sane bar,
        # because one disaster is diluted by nineteen easy tokens. The
        # geometric mean reads far lower.
        mostly_fine = CONFIDENT * 4
        with_one_bad = mostly_fine + [-6.0]
        probs = [token_confidence(lp) for lp in with_one_bad]
        arithmetic = sum(probs) / len(probs)
        geometric = sequence_confidence(with_one_bad)
        assert geometric < arithmetic * 0.8
        assert geometric < 0.75

    def test_but_a_lone_bad_token_does_not_collapse_a_long_answer(self):
        # Stated plainly because it is a real limitation, not a bug: a very
        # long answer dilutes a single uncertain token even in log space. If
        # this ever matters, the fix is a percentile floor over the worst k
        # tokens, not a change of mean.
        mostly_fine = CONFIDENT * 4
        assert sequence_confidence(mostly_fine + [-6.0]) > 0.5

    def test_result_is_a_probability(self):
        value = sequence_confidence(CONFIDENT)
        assert 0.0 <= value <= 1.0


class TestAssess:
    def test_a_confident_answer_is_trusted(self):
        r = assess(CONFIDENT, text="Paris.", bar=DEFAULT_BAR)
        assert r.action is Escalation.TRUST_LOCAL
        assert r.escalate is False

    def test_an_unsure_answer_escalates(self):
        r = assess(UNSURE, text="Probably Paris?", bar=DEFAULT_BAR)
        assert r.action is Escalation.ESCALATE
        assert "low_confidence" in r.signals

    def test_the_bar_is_configurable_and_actually_moves_the_decision(self):
        # A flag nobody can observe is a flag nobody can tune.
        loose = assess(UNSURE, text="hmm", bar=0.05)
        strict = assess(CONFIDENT, text="Paris.", bar=0.999)
        assert loose.escalate is False
        assert strict.escalate is True

    def test_tools_offered_and_none_called_escalates_regardless_of_confidence(self):
        # The failure that actually bites. A 4B model asked to open a panel
        # writes a fluent sentence about opening the panel, with perfectly good
        # logprobs, and never emits the call. Prose confidence is not tool
        # confidence.
        r = assess(CONFIDENT, text="Sure, opening the terminal now.", tools_offered=3)
        assert r.action is Escalation.ESCALATE
        assert "no_tool_call" in r.signals

    def test_a_tool_call_that_did_happen_is_not_escalated_for_that_reason(self):
        r = assess(CONFIDENT, text="opening", tools_offered=3, tool_called=True)
        assert "no_tool_call" not in r.signals

    def test_empty_output_escalates(self):
        r = assess(CONFIDENT, text="   ")
        assert r.action is Escalation.ESCALATE
        assert "empty_output" in r.signals

    def test_absent_logprobs_do_not_escalate_on_their_own(self):
        # Otherwise a provider that omits logprobs could never use the local
        # tier at all, and the structural checks would be pointless.
        r = assess(None, text="Paris.")
        assert r.escalate is False
        assert r.confidence is None
        assert "no logprobs" in r.reason

    def test_every_reading_explains_itself(self):
        # An escalation the operator cannot see the reason for is one they
        # will turn off.
        for r in (
            assess(CONFIDENT, text="ok"),
            assess(UNSURE, text="ok"),
            assess(CONFIDENT, text="ok", tools_offered=2),
            assess(CONFIDENT, text=""),
            assess(None, text="ok"),
        ):
            assert r.reason and isinstance(r.reason, str)
            assert r.bar == DEFAULT_BAR

class TestStructuralSignals:
    """The signals that catch a small model sounding sure while overrunning.

    Measured on this machine: asking the 4B local model to compare database
    architectures produced ~1000 tokens of fluent prose in 95s at roughly 0.9
    confidence. Pure confidence would have waved that through.
    """

    def test_a_long_local_answer_escalates_even_when_confident(self):
        r = assess(CONFIDENT, text="x" * 2000, bar=0.5)
        assert r.escalate and "too_long" in r.signals

    def test_a_short_confident_answer_stays_local(self):
        r = assess(CONFIDENT, text="Paris.", bar=0.75)
        assert not r.escalate

    def test_a_slow_local_turn_escalates_even_when_confident(self):
        r = assess(CONFIDENT, text="Paris.", bar=0.5, elapsed_s=95.0)
        assert r.escalate and "too_slow" in r.signals

    def test_a_fast_local_turn_stays_local(self):
        r = assess(CONFIDENT, text="Paris.", bar=0.75, elapsed_s=1.8)
        assert not r.escalate

    def test_elapsed_survives_on_the_reading(self):
        r = assess(CONFIDENT, text="x" * 2000, bar=0.5, elapsed_s=95.0)
        assert r.elapsed_s == 95.0
