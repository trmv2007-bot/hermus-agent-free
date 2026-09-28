"""Intent judge: does HERMUS speak right now, and if so what does it say?

This is a pure, dependency-free decision function. It has no imports outside the
standard library, performs no IO, calls no model, and never blocks. Given a
snapshot of the room it returns a :class:`Decision`. Determinism is a
requirement, not a nicety: the behaviour of an assistant has to be replayable
and testable without a gateway, a network, or a model in the loop.

The whole design exists to defend one property: **silence is a first class
outcome.** A judge that always speaks is worse than no judge, because it turns
a useful channel into wallpaper. The supporting evidence and the exact
citations are in ``docs/design/INTENT_JUDGE.md``. The load bearing claims:

* Horvitz, "Principles of Mixed-Initiative User Interfaces" (CHI 1999) models
  the timing of service on the user's attention, and LookOut deliberately
  *extends its dwell* when it hears the user thinking ("hmmm...", "uh..."),
  waiting for the end of the sentence before speaking. Mid-sentence is not a
  cheap moment to talk.
* Amershi et al., "Guidelines for Human-AI Interaction" (CHI 2019) guideline 3
  says time services based on context, guideline 8 says make dismissal easy,
  guideline 10 says scope service when in doubt, and guideline 11 says make
  clear why the system did what it did. Guideline 11 is the reason every
  decision carries a specific ``reason`` and not merely a boolean.
* "When not to help" (arXiv 2508.01837) models alert fatigue as a cost the
  system pays for itself, and concludes that strategic silence can be worth as
  much as speaking. Always-on and always-off policies both lose to adaptive
  timing.
* Vance et al., "The Fog of Warnings" (SOUPS 2019) shows habituation
  generalises across *look and feel*: habituating a user to a stream of
  non-critical notifications blunts a novel critical one too. This is the
  reason the judge rations interrupts instead of styling them louder.

Two rules that are judgement calls, stated plainly so they can be argued with:

* An error or a blocked room is allowed to interrupt, because silence there
  reads as the system not noticing, and the cost asymmetry favours speaking.
  This is the only path to ``Urgency.INTERRUPT``, and it is heavily rationed.
* A result the system cannot vouch for is never announced as a result. An
  unverified tool return, or a mission that reports completion without one, is
  silence with an explicit reason. A haunted object is an assistant that acts
  without explanation, and an assistant that claims progress it did not verify
  is the same failure wearing a suit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

__all__ = [
    "EventKind",
    "Decision",
    "IntentJudge",
    "JudgeConfig",
    "Moment",
    "RoomState",
    "SAY_NOTHING",
    "SpeechHistory",
    "SpokenMark",
    "Urgency",
    "VERIFIED",
    "UNVERIFIED",
    "decide",
    "event_signature",
    "format_duration",
]


class RoomState(str, Enum):
    """What the room is doing, independent of who spoke last.

    ``IDLE``       nothing is running, the user is present or away.
    ``WORKING``    a tool, mission, or background job is in flight.
    ``VERIFYING``  HERMUS is checking its own output before committing to it.
    ``ATTENTION``  the user already has the floor or is looking at us.
    ``BLOCKED``    work has stopped and cannot continue without a decision.
    ``COMPACTING`` internal bookkeeping (context compaction and the like).
    """

    IDLE = "idle"
    WORKING = "working"
    VERIFYING = "verifying"
    ATTENTION = "attention"
    BLOCKED = "blocked"
    COMPACTING = "compacting"


class EventKind(str, Enum):
    """What just happened, if anything. ``NONE`` means the clock ticked."""

    NONE = "none"
    TOOL_FINISHED = "tool_finished"
    ERROR = "error"
    MEMORY_WRITTEN = "memory_written"
    MISSION_COMPLETE = "mission_complete"


class Urgency(str, Enum):
    """How hard to push, from doing nothing to cutting in.

    ``SILENT``      say nothing. The default, and the most common outcome.
    ``ACKNOWLEDGE`` fold it into the reply already in flight, do not open a
                    new speaking turn.
    ``INFORM``      speak, but queue behind the current user turn if needed.
    ``INTERRUPT``   cut in now. Rationed to errors and long blocks only.
    """

    SILENT = "silent"
    ACKNOWLEDGE = "acknowledge"
    INFORM = "inform"
    INTERRUPT = "interrupt"


#: The literal content of ``Decision.line`` when the judge chose silence. The
#: contract is explicit rather than an empty string, so a caller that forgets
#: to branch fails loudly in a log instead of quietly speaking "".
SAY_NOTHING = "say nothing"

VERIFIED = "verified"
UNVERIFIED = "unverified"

#: Room states in which an interrupt is never allowed. ``ATTENTION`` because the
#: user is already looking at us, ``COMPACTING`` because an alarm during
#: internal maintenance reads as a crash rather than as news.
_FLOOR_HELD = (RoomState.ATTENTION, RoomState.COMPACTING)

#: How many prior occurrences of a signature map to each verdict, for events
#: that are allowed to escalate. Index is "how many times already said inside
#: the escalation window". This is a table and not a formula so the behaviour
#: is readable in a test and arguable in review.
_URGENT_ESCALATION: dict[int, Urgency] = {
    0: Urgency.INTERRUPT,
    1: Urgency.SILENT,
    2: Urgency.INFORM,
    3: Urgency.SILENT,
}

#: Events worth surfacing just because the room has been quiet for a while.
#: A memory write is deliberately absent: it is internal bookkeeping the user
#: did not ask about, and a quiet room is not a reason to volunteer it.
_QUIET_PROMOTABLE = frozenset(
    {
        EventKind.TOOL_FINISHED,
        EventKind.MISSION_COMPLETE,
        EventKind.ERROR,
    }
)


def format_duration(seconds: float) -> str:
    """Render a duration the way a person would say it out loud.

    Reasons that say "41s" and "4m 12s" are auditable in a way that "a while"
    is not, and the whole point of the reason string is that it can be read
    back and argued with.
    """
    if seconds < 0:
        return "0s"
    if seconds < 1:
        return "0s" if seconds == 0 else f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s" if rest else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def event_signature(event: EventKind, detail: str = "") -> str:
    """A stable key for "the same thing happened again".

    ``detail`` is expected to be a stable identifier (an error code, a tool
    name, a mission name), never a timestamp. Timestamps make every occurrence
    unique and defeat repeat suppression entirely, which is the single easiest
    way to get this module wrong at the call site.
    """
    return f"{event.value}:{detail.strip()}" if detail else event.value


@dataclass(frozen=True)
class Moment:
    """An immutable snapshot of the world at one instant.

    ``now``
        Caller-supplied monotonic seconds. The judge never reads a clock, so
        a test can place two events three minutes apart without sleeping.
    ``event_detail``
        Stable identifier for the event, see :func:`event_signature`.
    ``event_verified``
        Did the system actually check this result. A tool that returned
        without a verified outcome is not a result, and the judge will not
        announce it as one.
    ``seconds_since_last_speech``
        Overrides the gap derived from history. Needed when a session resumes
        and the gap is known but the history is empty. ``None`` means derive it.
    """

    now: float = 0.0
    room: RoomState = RoomState.IDLE
    event: EventKind = EventKind.NONE
    event_detail: str = ""
    event_verified: bool = False
    user_just_spoke: bool = False
    user_mid_sentence: bool = False
    user_typing: bool = False
    blocked_for_seconds: float = 0.0
    seconds_since_last_speech: Optional[float] = None
    prior_occurrences: int = 0
    """Times this exact signature was seen before now, by the caller.

    Deliberately an input rather than something read from history. The
    read-only :meth:`IntentJudge.evaluate` path never writes to history, so
    an error branch that infers "have I seen this before" from history alone
    reports zero prior occurrences every time, and a first error gets filed as
    a repeat. The caller that observed the event knows this number; passing it
    in keeps the decision function honest on both entry points.
    """


@dataclass(frozen=True)
class Decision:
    """The verdict.

    ``rule``
        Stable identifier for the branch that fired. Tests assert on it and
        an operator reading a log can tell which policy spoke.
    ``reason``
        Specific and quantified, never "an event occurred". This is the
        auditable part; guidelines for human-AI interaction say a user should
        be able to find out why the system did what it did.
    ``line``
        Suggested utterance, or the literal :data:`SAY_NOTHING`.
    ``defers``
        True when the content is worth saying but must wait for the end of the
        user's current sentence. The caller should queue it, not drop it.
    ``fold_into_reply``
        True when the content belongs inside a reply already in flight rather
        than in a new speaking turn.
    """

    should_speak: bool
    urgency: Urgency
    reason: str
    line: str
    rule: str
    defers: bool = False
    fold_into_reply: bool = False
    confidence: str = "none"


@dataclass(frozen=True)
class SpokenMark:
    """One utterance the judge actually emitted, for later suppression."""

    signature: str
    at: float
    urgency: Urgency


@dataclass
class SpeechHistory:
    """What the judge has seen and what it has said.

    Two separate records, because conflating them is a bug that hides itself:
    every moment the judge evaluates is an *occurrence*, but only the moments
    it actually spoke are *announcements*. Keying escalation off
    announcements looks right and is dead code, because the second occurrence
    is suppressed and therefore never announced, so the count can never reach
    the escalation rung.

    Held outside the judge so the decision function stays pure: the same
    ``(moment, history)`` pair always produces the same decision.
    """

    marks: list[SpokenMark] = field(default_factory=list)
    occurrences: list[SpokenMark] = field(default_factory=list)

    def note(self, moment: Moment) -> None:
        """Record that this event happened, whether or not we spoke.

        Call this once per evaluated moment, before :meth:`record`. Pure
        :func:`decide` never mutates history, so this is the caller's job and
        :meth:`IntentJudge.observe` does it for you.
        """
        self.occurrences.append(
            SpokenMark(
                event_signature(moment.event, moment.event_detail),
                moment.now,
                Urgency.SILENT,
            )
        )

    def record(self, decision: Decision, moment: Moment) -> None:
        """Record an utterance. Only call this for decisions that spoke.

        A folded acknowledgement is deliberately not recorded: it was never a
        standalone announcement, so it must not start a cooldown of its own.
        """
        if not decision.should_speak or decision.urgency is Urgency.SILENT:
            return
        if decision.fold_into_reply:
            return
        signature = event_signature(moment.event, moment.event_detail)
        self.marks.append(SpokenMark(signature, moment.now, decision.urgency))

    def most_recent(self) -> Optional[SpokenMark]:
        if not self.marks:
            return None
        return max(self.marks, key=lambda m: m.at)

    def gap_seconds(self, now: float) -> Optional[float]:
        """Seconds since the last utterance, or ``None`` if it never spoke."""
        latest = self.most_recent()
        if latest is None:
            return None
        return max(0.0, now - latest.at)

    def seconds_since_signature(self, signature: str, now: float) -> Optional[float]:
        """Seconds since ``signature`` was last announced. ``None`` if never."""
        seen = [m.at for m in self.marks if m.signature == signature]
        if not seen:
            return None
        return max(0.0, now - max(seen))

    def count_signature(self, signature: str, now: float, window: float) -> int:
        """How many times ``signature`` was announced inside ``window``.

        Counts announcements, used for cooldown.
        """
        return sum(
            1 for m in self.marks if m.signature == signature and now - m.at < window
        )

    def count_occurrences(self, signature: str, now: float, window: float) -> int:
        """How many times ``signature`` was *seen* inside ``window``.

        Counts occurrences, not announcements, used for escalation. This is the
        difference between a fault that happened once and a fault that keeps
        happening, and it is the only signal that survives suppression.
        """
        return sum(
            1
            for m in self.occurrences
            if m.signature == signature and now - m.at < window
        )


@dataclass(frozen=True)
class JudgeConfig:
    """Every threshold in one place, so it can be tuned and argued about.

    Defaults are chosen for a voice room, not a dashboard, and are all
    conservative in the same direction: speak late, speak rarely, and never
    claim what was not verified.
    """

    #: Floor between any two utterances of non-interrupt kind.
    min_gap_seconds: float = 20.0
    #: How long a routine event stays suppressed after being announced.
    routine_cooldown_seconds: float = 180.0
    #: Suppression window for an announced error or block.
    urgent_cooldown_seconds: float = 45.0
    #: Window over which repeat count is measured for escalation.
    escalate_window_seconds: float = 900.0
    #: Silence after which a routine but notable event is allowed through.
    quiet_window_seconds: float = 180.0
    #: A block older than this is news again: it has changed state.
    blocked_escalate_seconds: float = 60.0
    #: Ceiling on urgency per room, before the floor protections apply.
    room_ceiling: dict = field(
        default_factory=lambda: {
            RoomState.ATTENTION: Urgency.INFORM,
            RoomState.COMPACTING: Urgency.INFORM,
        }
    )

    def ceiling_for(self, room: RoomState) -> Urgency:
        return self.room_ceiling.get(room, Urgency.INTERRUPT)


def _floor_protected(moment: Moment) -> bool:
    """Is the user mid utterance. Never cut in on a half finished sentence.

    LookOut raised its dwell time when it detected thinking noises precisely so
    it would not talk over someone. When the floor is held we may still speak,
    but only as a deferred ``INFORM`` that the caller queues for the turn
    boundary.
    """
    return moment.user_mid_sentence or moment.user_typing


def decide(
    moment: Moment,
    history: Optional[SpeechHistory] = None,
    config: Optional[JudgeConfig] = None,
) -> Decision:
    """Pure decision function. Same inputs, same decision, every time.

    Precedence, highest first:

    1. ``nothing_to_say``      no event, so there is nothing to weigh.
    2. ``cannot_vouch``        an outcome we cannot verify is never announced.
    3. ``user_has_floor``      the user just spoke; non urgent news folds in.
    4. ``urgent`` / ``repeat`` error or block, rationed by a table.
    5. ``cooldown`` / ``rate``  say less, less often.
    6. ``quiet_window``        long silence buys the right to report.
    7. ``unremarkable``        the default answer, which is silence.
    """
    history = history if history is not None else SpeechHistory()
    config = config if config is not None else JudgeConfig()

    detail = moment.event_detail.strip() or "unspecified"
    signature = event_signature(moment.event, moment.event_detail)
    gap = (
        moment.seconds_since_last_speech
        if moment.seconds_since_last_speech is not None
        else history.gap_seconds(moment.now)
    )
    # No utterance on record is not "infinitely quiet", it is "unproven
    # attention". Silence only buys the right to speak once there is a silence.
    gap_text = format_duration(gap) if gap is not None else "never spoken to"
    protected = _floor_protected(moment)
    where = f"room is {moment.room.value}"

    # 1. Nothing happened. Silence needs no justification, but it still gets
    #    one, because an unexplained silence is indistinguishable from a crash.
    if moment.event is EventKind.NONE:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=f"no event and {where}; last utterance was {gap_text}",
            line=SAY_NOTHING,
            rule="nothing_to_say",
        )

    # 2. Honesty gate. This sits above every other rule on purpose. Urgency
    #    never buys permission to claim something unverified.
    if moment.event is EventKind.MISSION_COMPLETE and not moment.event_verified:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=(
                f"mission {detail} reported complete but no verified result "
                f"backed it; announcing it would be a promise"
            ),
            line=SAY_NOTHING,
            rule="cannot_vouch",
            confidence=UNVERIFIED,
        )
    if moment.event is EventKind.TOOL_FINISHED and not moment.event_verified:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=(
                f"tool {detail} returned without a verified result and {where}; "
                f"there is no outcome to report, only a return value"
            ),
            line=SAY_NOTHING,
            rule="cannot_vouch",
            confidence=UNVERIFIED,
        )

    # 3. The user is talking. Only an urgent event earns the right to add to
    #    that turn, and then only as an acknowledgement folded into the reply.
    if moment.user_just_spoke and not protected:
        if moment.event is EventKind.ERROR:
            return Decision(
                should_speak=True,
                urgency=Urgency.ACKNOWLEDGE,
                reason=(
                    f"error on {detail} landed while the user was already "
                    f"addressing us, so it goes in the same breath instead of "
                    f"opening a second turn"
                ),
                line=f"{detail} failed. Folding that into what I am saying now.",
                rule="user_has_floor",
                fold_into_reply=True,
                confidence=VERIFIED,
            )
        if moment.event in _QUIET_PROMOTABLE:
            return Decision(
                should_speak=False,
                urgency=Urgency.SILENT,
                reason=(
                    f"{moment.event.value} on {detail} landed mid utterance; "
                    f"a standalone announcement would talk over the user, so it "
                    f"is queued for the end of the sentence"
                ),
                line=SAY_NOTHING,
                rule="user_has_floor",
                defers=True,
            )

    # 4. Errors and blocks are the only things allowed to interrupt, because
    #    silence there reads as the system not noticing. They are rationed by
    #    an explicit table so the third repeat can escalate while the fourth
    #    goes quiet again.
    if moment.event is EventKind.ERROR:
        # prior is occurrences *including* right now, so the first sighting is
        # 1. An explicit input, so the read-only path cannot misfile a first
        # error as a repeat.
        prior = moment.prior_occurrences + 1
        rung = _URGENT_ESCALATION.get(prior - 1, Urgency.SILENT)
        if rung is Urgency.SILENT:
            last = history.seconds_since_signature(signature, moment.now)
            return Decision(
                should_speak=False,
                urgency=Urgency.SILENT,
                reason=(
                    f"error on {detail} was already reported "
                    f"{format_duration(last or 0.0)} ago inside the "
                    f"{format_duration(config.escalate_window_seconds)} "
                    f"escalation window (occurrence {prior} of the same fault); "
                    f"same fault, not new news"
                ),
                line=SAY_NOTHING,
                rule="repeat",
            )
        if rung is Urgency.INFORM:
            return _deliver(
                moment,
                config,
                urgency=Urgency.INFORM,
                protected=protected,
                reason=(
                    f"error on {detail} is occurrence {prior} inside "
                    f"{format_duration(config.escalate_window_seconds)}; "
                    f"escalating so a persistent fault does not look resolved"
                ),
                line=f"{detail} failed again. Same fault as before, not a new one.",
                rule="urgent",
            )
        return _deliver(
            moment,
            config,
            urgency=Urgency.INTERRUPT,
            protected=protected,
            reason=(
                f"error on {detail} in a {where} room, first occurrence inside "
                f"{format_duration(config.escalate_window_seconds)} and "
                f"unreported for {gap_text}; silence here would read as not "
                f"noticing"
            ),
            # No claim of agency. Whatever went wrong, HERMUS did not "stop"
            # anything -- a template that says it did is a lie told to the user
            # in the exact moment they are relying on the report.
            line=f"{detail} failed. Here is what I saw, not a guess.",
            rule="urgent",
        )

    if moment.room is RoomState.BLOCKED:
        held = moment.blocked_for_seconds
        if held > config.blocked_escalate_seconds and not protected:
            return _deliver(
                moment,
                config,
                urgency=Urgency.INFORM,
                protected=protected,
                reason=(
                    f"room has been blocked for {format_duration(held)}, past "
                    f"the {format_duration(config.blocked_escalate_seconds)} "
                    f"mark where the block itself becomes the news"
                ),
                line=(
                    f"Still blocked after {format_duration(held)}. "
                    f"Nothing is moving until that is decided."
                ),
                rule="block_age",
            )
        return _deliver(
            moment,
            config,
            urgency=Urgency.INTERRUPT,
            protected=protected,
            reason=(
                f"room is blocked and has been for {format_duration(held)}; "
                f"work has stopped and only a decision restarts it"
            ),
            line="Blocked. I am holding here until you say go.",
            rule="blocked",
        )

    # 5. Rate and repeat suppression for everything that is not urgent. This
    #    is the rule that stops nagging, and it is checked before the quiet
    #    window on purpose: a long silence does not make the same event
    #    interesting twice.
    last_same = history.seconds_since_signature(signature, moment.now)
    if last_same is not None and last_same < config.routine_cooldown_seconds:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=(
                f"{moment.event.value} on {detail} was announced "
                f"{format_duration(last_same)} ago, inside the "
                f"{format_duration(config.routine_cooldown_seconds)} cooldown "
                f"for routine events"
            ),
            line=SAY_NOTHING,
            rule="cooldown",
        )

    ceiling = config.ceiling_for(moment.room)
    if gap is not None and gap < config.min_gap_seconds:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=(
                f"{moment.event.value} on {detail} is only "
                f"{format_duration(gap)} after the last utterance, under the "
                f"{format_duration(config.min_gap_seconds)} minimum gap"
            ),
            line=SAY_NOTHING,
            rule="rate",
        )

    # 6. Compaction is internal. An error here is real, but the user is not
    #    waiting on our machinery and an alarm reads as a crash.
    if moment.room is RoomState.COMPACTING:
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=(
                f"{moment.event.value} on {detail} happened while compacting; "
                f"that is internal machinery, and a user waiting on it has "
                f"nothing to act on"
            ),
            line=SAY_NOTHING,
            rule="internal",
        )

    # 7. A quiet room promotes notable events. Silence is not a licence to
    #    narrate, so only the promotable set gets through, and an unverified
    #    result was already turned away at rule 2.
    if (
        moment.event in _QUIET_PROMOTABLE
        and gap is not None
        and gap >= config.quiet_window_seconds
    ):
        return _deliver(
            moment,
            config,
            urgency=Urgency.INFORM,
            protected=protected,
            reason=(
                f"{moment.event.value} on {detail}, verified, and the room has "
                f"been quiet for {gap_text}, past the "
                f"{format_duration(config.quiet_window_seconds)} quiet window; "
                f"first result worth reporting"
            ),
            line=_inform_line(moment, gap, detail),
            rule="quiet_window",
        )

    # 8. Default. Unremarkable, unverified of importance, nothing pending. Say
    #    nothing, and say why.
    if moment.room is RoomState.VERIFYING:
        reason = (
            f"{moment.event.value} on {detail} arrived while verifying; the "
            f"answer is not final yet and an interim claim is one I cannot "
            f"take back"
        )
    elif moment.event is EventKind.MEMORY_WRITTEN:
        reason = (
            f"memory written for {detail} in a {where} room; it is bookkeeping "
            f"the user did not ask about, and quiet is not a reason to volunteer it"
        )
    else:
        reason = (
            f"{moment.event.value} on {detail} is routine and "
            f"{where} with nothing pending; last utterance was {gap_text}"
        )
    return Decision(
        should_speak=False,
        urgency=Urgency.SILENT,
        reason=reason,
        line=SAY_NOTHING,
        rule="unremarkable",
        confidence=VERIFIED if moment.event_verified else UNVERIFIED,
    )


def _inform_line(moment: Moment, gap: float, detail: str) -> str:
    if moment.event is EventKind.MISSION_COMPLETE:
        return f"{detail} is done, verified."
    return f"{detail} finished. First result in {format_duration(gap)}."


def _deliver(
    moment: Moment,
    config: JudgeConfig,
    urgency: Urgency,
    protected: bool,
    reason: str,
    line: str,
    rule: str,
) -> Decision:
    """Apply the two ceilings every decision passes through.

    The room ceiling caps how hard we may push. The floor protection caps it
    again the moment the user starts talking, and converts the decision into a
    queued one rather than dropping it.
    """
    ceiling = config.ceiling_for(moment.room)
    order = [Urgency.SILENT, Urgency.ACKNOWLEDGE, Urgency.INFORM, Urgency.INTERRUPT]
    final = urgency
    if order.index(final) > order.index(ceiling):
        final = ceiling
    if protected and final is not Urgency.INTERRUPT:
        return Decision(
            should_speak=True,
            urgency=Urgency.INFORM,
            reason=reason
            + f"; the user is mid sentence, so this is queued for the turn "
            f"boundary rather than cut in",
            line=line,
            rule=rule,
            defers=True,
            confidence=VERIFIED,
        )
    if protected:
        # The one case where the floor wins outright. An interrupt is the only
        # thing the user cannot dismiss cheaply, and talking over a half
        # finished sentence is exactly the failure this rule exists to prevent.
        # Dropped rather than queued: a stale interrupt delivered after the
        # sentence ends is worse than not delivering it.
        return Decision(
            should_speak=False,
            urgency=Urgency.SILENT,
            reason=reason
            + f"; the user is mid sentence and this was the only path to an "
            f"interrupt, so it is dropped rather than cut in",
            line=SAY_NOTHING,
            rule=rule,
            confidence=VERIFIED,
        )
    return Decision(
        should_speak=True,
        urgency=final,
        reason=reason,
        line=line,
        rule=rule,
        confidence=VERIFIED,
    )


class IntentJudge:
    """Stateful shell around :func:`decide`.

    Holds the history so callers do not have to, while keeping the decision
    itself a pure function that any test can call with no objects at all.
    """

    def __init__(self, config: Optional[JudgeConfig] = None) -> None:
        self.config = config or JudgeConfig()
        self.history = SpeechHistory()

    def evaluate(self, moment: Moment) -> Decision:
        """Decide. Does not record anything.

        If the caller left ``prior_occurrences`` at zero and this judge has seen
        the signature before, the real count is filled in here, so the
        read-only path agrees with the stateful one.
        """
        return decide(self._with_prior_count(moment), self.history, self.config)

    def _with_prior_count(self, moment: Moment) -> Moment:
        if moment.prior_occurrences:
            return moment
        signature = event_signature(moment.event, moment.event_detail)
        if moment.event is EventKind.NONE:
            return moment
        return replace(
            moment,
            prior_occurrences=self.history.count_occurrences(
                signature, moment.now, self.config.escalate_window_seconds
            ),
        )

    def observe(self, moment: Moment) -> Decision:
        """Decide, note the occurrence, and remember it if we spoke.

        This is the normal entry point. :meth:`evaluate` is the read-only one,
        for callers that want a verdict without advancing any state.
        Note the occurrence *after* deciding: the count fed into the decision
        must be "seen before now", and counting first makes every first
        sighting look like a repeat.
        """
        decision = self.evaluate(moment)
        self.history.note(moment)
        self.history.record(decision, moment)
        return decision

    def reset(self) -> None:
        self.history = SpeechHistory()

    def with_config(self, config: JudgeConfig) -> "IntentJudge":
        """A fresh judge sharing this history under different thresholds.

        Returns a new object rather than mutating, so a config experiment can
        never corrupt the live judge's suppression state.
        """
        return IntentJudge(config=config)
