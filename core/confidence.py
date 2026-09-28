"""A confidence bar, measured rather than guessed.

The problem
-----------
The room answers a simple turn on a 4B local model because that is free and
private, and escalates to a 120B hosted model when the small one is out of its
depth. The whole design rests on one question: how do we know when the small
model is out of its depth?

Asking the model is not an answer. A 4B model asked "how confident are you"
will say "quite confident" with the same flat tone whether it is right or
inventing a tool that does not exist. Self-report is the one signal that is
both unavailable and worthless here.

What is available is the logprob
--------------------------------
Ollama returns per-token logprobs, and a token the model is unsure about has a
much worse logprob than one it is confident about. That is a measurement of the
model's own distribution, not a claim it makes about itself, so it cannot be
flattered by the same overconfidence that makes the self-report useless.

So the bar is arithmetic over logprobs, plus structural signals that need no
model at all. Both are pure functions here, and the caller does the I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional

__all__ = [
    "Escalation",
    "ConfidenceReading",
    "token_confidence",
    "sequence_confidence",
    "assess",
]


class Escalation(str, Enum):
    """Whether the local answer stands, or the big model takes over."""

    TRUST_LOCAL = "trust_local"
    ESCALATE = "escalate"


# A token at -0.05 is roughly 95% confident, -0.7 is roughly 50%, -2.0 is
# roughly 14%. Averaging in log space is geometric mean probability, which is
# the right aggregation here: a long answer with one terrible token is not
# confident, and an arithmetic mean of probabilities would hide that behind
# many easy tokens.
#
# 0.65 is measured, not guessed. The same prompt varies run to run, so the bar
# has to sit clear of that spread rather than on a single lucky reading.
# "What is the capital of France?" through spark-x2.5-4b on this machine came
# back 0.88, 0.83, 0.88 and 0.71 across four runs. "Do the thing we discussed."
# read 0.56. At 0.75 the good answer escalated roughly half the time, which
# defeats the point of having a local tier at all; 0.65 keeps every observed
# good answer local and still catches the uncertain one.
#
# Tune with HERMUS_CONFIDENCE_BAR. Raise it to escalate more, lower it to keep
# more local.
DEFAULT_BAR = 0.65

# Confidence alone is not enough, and the reason is worth writing down. A 4B
# model asked to compare database architectures will happily spend 95 seconds
# and 1000 tokens on confident, fluent, mediocre prose. Every one of those
# tokens scores high, so the geometric mean lands well above the bar and the
# gate waves it through. Measured on this machine: that prompt reads ~0.9 and
# takes 95s.
#
# A turn that long from a small model is itself the signal. Length is a proxy
# for "stopped and think", and it costs nothing to check, so it escalates
# regardless of how sure the model sounded.
#
# 12s, not 25s. A clean local answer on this machine is 2-5s, so 12s is already
# three times too slow to be a normal reply. The first cut used 25s and a real
# database-architecture prompt landed at 24.8s and sailed straight through.
MAX_LOCAL_ANSWER_CHARS = 900
MAX_LOCAL_TURN_S = 12.0


def token_confidence(logprob: Optional[float]) -> Optional[float]:
    """One token's logprob as a probability, or None if it is unusable.

    NaN and -inf both mean "the model assigned this no probability at all",
    which is maximally uncertain rather than a number to average in.
    """
    if logprob is None:
        return None
    try:
        value = float(logprob)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(0.0, min(1.0, math.exp(value)))


def sequence_confidence(logprobs: Iterable[Optional[float]]) -> Optional[float]:
    """Geometric-mean confidence across tokens.

    Geometric, not arithmetic: a long answer with one token the model had no
    idea about is not a confident answer, and averaging raw probabilities would
    hide that single disaster behind many easy tokens. Averaging log-probs is
    the same thing done in a space where the mean is meaningful, then one exp
    at the end.
    """
    values = [p for p in (token_confidence(lp) for lp in logprobs) if p is not None]
    if not values:
        return None
    mean_logprob = sum(math.log(max(p, 1e-12)) for p in values) / len(values)
    return max(0.0, min(1.0, math.exp(mean_logprob)))


@dataclass(frozen=True)
class ConfidenceReading:
    """Why the gate decided what it decided."""

    escalate: bool
    confidence: Optional[float]
    bar: float
    reason: str
    # How long the local turn actually took. Kept on the reading so a slow
    # answer is explainable rather than mysterious, and kept after the
    # required fields because a defaulted field cannot precede a plain one.
    elapsed_s: Optional[float] = None
    # How this turn was actually resolved: "local", "grounded" (a search
    # answered it) or "escalated" (the main model did). A turn that took four
    # extra seconds needs to be able to say why, and "because it looked it up"
    # is a different answer from "because it was unsure".
    stage: str = "local"
    # The URLs behind a grounded answer, so the client can show them.
    sources: tuple = ()
    # Which tier actually answered. Without it a turn answered by a fast free
    # tier is indistinguishable from one answered by the big model.
    model_used: str = ""
    # Structural signals, kept separate from the numeric score so a caller can
    # see whether it was the maths or a hard rule that fired.
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def action(self) -> Escalation:
        return Escalation.ESCALATE if self.escalate else Escalation.TRUST_LOCAL


def assess(
    logprobs: Optional[Iterable[Optional[float]]] = None,
    *,
    text: Optional[str] = None,
    tools_offered: int = 0,
    tool_called: bool = False,
    bar: float = DEFAULT_BAR,
    elapsed_s: Optional[float] = None,
) -> ConfidenceReading:
    """Decide whether to trust the local answer or escalate.

    Three independent reasons to escalate, checked before the arithmetic, in
    order of how much they should worry us:

    1. It produced nothing. Empty output is not a low-confidence answer, it is
       no answer, and averaging zero tokens would return None.
    2. It was offered tools and called none, while the task needed one. This is
       the failure mode that actually bites: a 4B model asked to open a panel
       writes a confident sentence about opening the panel instead of emitting
       the call. Confidence in prose is not confidence in the tool.
    3. Its own measured confidence is below the bar.
    4. It ran long, or wrote a lot, while sounding certain. A small model
       asked for real depth does not get more correct, it gets longer, and
       every extra token scores high. See MAX_LOCAL_ANSWER_CHARS.
    """
    if text is not None and len(text) > MAX_LOCAL_ANSWER_CHARS:
        return ConfidenceReading(
            escalate=True,
            confidence=sequence_confidence(logprobs),
            bar=bar,
            elapsed_s=elapsed_s,
            reason=f"a local answer of {len(text)} chars is a small model overrunning",
            signals=("too_long",),
        )

    if elapsed_s is not None and elapsed_s > MAX_LOCAL_TURN_S:
        return ConfidenceReading(
            escalate=True,
            confidence=sequence_confidence(logprobs),
            bar=bar,
            elapsed_s=elapsed_s,
            reason=f"the local turn took {elapsed_s:.0f}s",
            signals=("too_slow",),
        )

    if tools_offered > 0 and not tool_called:
        return ConfidenceReading(
            escalate=True,
            confidence=None,
            bar=bar,
            reason="tools were offered and none was called",
            signals=("no_tool_call",),
        )

    if text is not None and not text.strip():
        return ConfidenceReading(
            escalate=True,
            confidence=None,
            bar=bar,
            reason="the model produced no text",
            signals=("empty_output",),
        )

    confidence = sequence_confidence(logprobs or ())
    if confidence is None:
        # No logprobs to read. Absence of evidence is not evidence of
        # confidence, but escalating every such call would mean a provider
        # that omits logprobs can never use the local tier at all. Trust the
        # structural checks that did run and say so.
        return ConfidenceReading(
            escalate=False,
            confidence=None,
            bar=bar,
            reason="no logprobs available; structural checks passed",
        )

    if confidence < bar:
        return ConfidenceReading(
            escalate=True,
            confidence=round(confidence, 4),
            bar=bar,
            reason=f"confidence {confidence:.2f} is below the bar {bar:.2f}",
            signals=("low_confidence",),
        )

    return ConfidenceReading(
        escalate=False,
        confidence=round(confidence, 4),
        bar=bar,
        reason=f"confidence {confidence:.2f} meets the bar {bar:.2f}",
    )
