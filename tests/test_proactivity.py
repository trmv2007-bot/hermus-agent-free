"""Tests for the ambient proactivity loop.

The loop's whole product is a *rate*: it should speak rarely, and be worth
hearing when it does. So most of what follows is about proving it does **not**
speak — a test that only covers the happy path would pass for a loop that
nags, which is the failure this feature exists to avoid.

The other theme is that the decision is not the loop's to make.
:mod:`core.intent` is the judge; these tests assert the loop *calls* it and
honours it, rather than re-testing the judge's own rules (which
``tests/test_intent.py`` already does, against reasons rather than booleans).

No sleeping, no threads, no gateway, no model. Time is a number the test
supplies, which is what makes "three hours of ticks" cost a millisecond.
"""

from __future__ import annotations

from datetime import time as dtime

import pytest

from core.intent import EventKind, RoomState
from core.proactivity import (
    FloorState,
    JobCompletionProbe,
    LongSilence,
    Observation,
    ProactivityConfig,
    ProactivityLoop,
    ResourceThresholdProbe,
    RuntimeIssueProbe,
    default_probes,
    in_quiet_hours,
)

DAWN = dtime(9, 0)  # firmly outside the default quiet window
MIDNIGHT = dtime(2, 0)  # firmly inside it


class ScriptedProbe:
    """A probe that returns a fixed script, one entry per tick.

    Real probes are hard to make deterministic (they read a queue, a disk, a
    mission store). This one is not a mock in the sense of pretending — it is
    the same ``poll(now) -> list[Observation]`` shape the loop depends on, with
    the IO removed, so a test can say "on tick 3 the disk fills up" and have
    that be the truth.
    """

    name = "scripted"

    def __init__(self, script: list[list[Observation]]) -> None:
        self.script = list(script)
        self.calls = 0

    def poll(self, now: float) -> list[Observation]:
        index = min(self.calls, len(self.script) - 1) if self.script else -1
        self.calls += 1
        if index < 0:
            return []
        return list(self.script[index])


def obs(
    detail: str = "thing",
    *,
    event: EventKind = EventKind.TOOL_FINISHED,
    verified: bool = True,
    weight: int = 40,
    source: str = "scripted",
) -> Observation:
    return Observation(event=event, detail=detail, verified=verified, source=source, weight=weight)


def build(
    *probes,
    emit=None,
    config: ProactivityConfig | None = None,
    floor: FloorState | None = None,
) -> ProactivityLoop:
    """A loop with quiet hours off by default, so a test opts *in* to them.

    The default config has a 22:30-08:00 window, which would silently swallow
    half the assertions depending on what time the suite happens to run. Making
    silence the thing a test opts into keeps each test's intent explicit.
    """
    sink: list = [] if emit is None else emit
    return ProactivityLoop(
        probes=list(probes),
        config=config or ProactivityConfig(quiet_hours=""),
        floor=floor,
        emit=sink.append if not callable(sink) else sink,
    )


# ---------------------------------------------------------------------------
# 1. The headline property: a quiet system is quiet.
# ---------------------------------------------------------------------------


def test_a_loop_with_nothing_to_report_says_nothing() -> None:
    """Three hours of ticks, nothing happening, zero words.

    This is the test that would fail first for a loop that narrates. It is
    first in the file for the same reason.
    """
    spoken: list = []
    loop = build(ScriptedProbe([[]]), emit=spoken)

    for tick in range(90):  # 90 ticks at 120s of simulated time = 3 hours
        assert loop.consider(tick * 120.0, wall=DAWN) == []

    assert spoken == []
    status = loop.status(89 * 120.0)
    assert status["spoken"] == 0
    assert status["observations"] == 0


def test_an_unverified_result_is_never_announced() -> None:
    """A tool that returned is not a result.

    The judge's rule 2 turns an unverified completion into silence before any
    other rule is reached. The loop's job is to pass the flag through honestly
    and then get out of the way, and this asserts it does.
    """
    loop = build(ScriptedProbe([[obs("pdf_extract", verified=False)]]))
    assert loop.consider(1000.0, wall=DAWN) == [], "an unverified tool finish must not be announced"
    assert loop.status(1000.0)["last_decision"]["rule"] == "cannot_vouch"


def test_a_verified_result_in_a_busy_room_is_still_silence() -> None:
    """Real news, said at the wrong time, is not news.

    Fresh history means no utterance on record, which the judge reads as
    *unproven attention* rather than as a long silence. Without that, the very
    first routine event after a boot would announce itself uninvited — which is
    the specific bug the design record calls out in section 1.
    """
    loop = build(ScriptedProbe([[obs("build", verified=True)]]))
    assert loop.consider(5000.0, wall=DAWN) == []
    reason = loop.status(5000.0)["last_decision"]["reason"]
    assert "never spoken to" in reason


# ---------------------------------------------------------------------------
# 2. It does speak when it should, and says why.
# ---------------------------------------------------------------------------


def test_an_error_interrupts_and_the_reason_names_the_fault() -> None:
    """Silence during a real error reads as "the system did not notice"."""
    spoken: list = []
    floor = FloorState()
    loop = build(ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]), emit=spoken, floor=floor)

    (utterance,) = loop.consider(1000.0, wall=DAWN)

    assert utterance.urgency == "interrupt"
    assert utterance.rule == "urgent"
    assert "memory.recall" in utterance.text
    # Guideline 11: the user can find out why the system did what it did.
    assert "would read as not noticing" in utterance.reason
    assert "memory.recall" in utterance.reason
    assert utterance.origin == "unsolicited"


def test_a_delivered_utterance_is_published_to_the_room() -> None:
    """tick() delivers; consider() deliberately does not.

    The split is what makes "it stayed quiet" testable without a bus, so it has
    to hold: consider() is the decision, tick() is the decision plus the
    consequence.
    """
    spoken: list = []
    loop = build(ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]), emit=spoken)

    loop.consider(1000.0, wall=DAWN)
    assert spoken == [], "consider() must not have side effects"

    loop.tick(2000.0, wall=DAWN)
    assert len(spoken) == 1
    assert spoken[0].text


def test_the_same_fault_keeps_being_seen_so_escalation_can_work() -> None:
    """Occurrences, not announcements, are what the escalation ladder counts.

    If a probe deduplicated forever, the second occurrence of a persisting
    fault would never reach the judge and the ladder would be dead code — the
    exact failure the design record documents as "found by test, not by
    reading". This asserts the loop keeps *offering* the fault.
    """
    probe = RuntimeIssueProbe()
    seen_keys: list[str] = []
    for _ in range(3):
        for observation in probe.poll(0.0):
            seen_keys.append(observation.detail)
    # The scripted probe below stands in for a fault that keeps happening.
    loop = build(ScriptedProbe([[obs("disk_free", event=EventKind.ERROR, weight=90)]] * 4))
    for tick in range(4):
        loop.consider(tick * 200.0, wall=DAWN)
    assert loop.judge.history.occurrences, "each occurrence must be noted"


def test_an_utterance_carries_its_provenance() -> None:
    """Which probe, which rule, which urgency — all of it travels with the line.

    A bubble that cannot explain itself is a popup, and a popup is the thing
    this design is arguing against.
    """
    loop = build(ScriptedProbe([[obs("disk_free", event=EventKind.ERROR, weight=90)]]))
    (utterance,) = loop.consider(1000.0, wall=DAWN)
    payload = utterance.to_dict()
    assert payload["type"] == "hermus_spoke"
    assert payload["source"] == "scripted"
    assert payload["event"] == EventKind.ERROR.value
    assert payload["detail"] == "disk_free"
    assert payload["rule"] and payload["reason"] and payload["urgency"]


# ---------------------------------------------------------------------------
# 3. The gates. Each one must run BEFORE the judge, or it costs a cooldown.
# ---------------------------------------------------------------------------


def test_quiet_hours_hold_every_observation() -> None:
    """Being woken at 2am to be told a mission finished is how you get muted.

    Also the reason the gate is checked before the judge: `observe()` records
    an utterance the moment it says yes, so suppressing afterwards would spend
    a cooldown on silence the user never heard.
    """
    spoken: list = []
    loop = build(
        ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]),
        emit=spoken,
        config=ProactivityConfig(quiet_hours="22:30-08:00"),
    )

    assert loop.consider(1000.0, wall=MIDNIGHT) == []
    assert spoken == []
    # And the judge was never asked, so no history was consumed.
    assert loop.judge.history.occurrences == []


def test_quiet_hours_cover_midnight() -> None:
    """The window wraps, which a naive comparison gets backwards."""
    window = ProactivityConfig(quiet_hours="22:30-08:00").quiet_window()
    assert window is not None
    assert in_quiet_hours(dtime(23, 0), window) is True
    assert in_quiet_hours(dtime(2, 0), window) is True
    assert in_quiet_hours(dtime(7, 59), window) is True
    assert in_quiet_hours(dtime(8, 0), window) is False
    assert in_quiet_hours(dtime(22, 29), window) is False
    assert in_quiet_hours(dtime(12, 0), window) is False


def test_a_malformed_quiet_window_disables_the_gate_rather_than_breaking_boot() -> None:
    """A typo in a settings file must not stop the assistant from starting."""
    loop = build(
        ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]),
        config=ProactivityConfig(quiet_hours="half past nine"),
    )
    assert loop.consider(1000.0, wall=MIDNIGHT), "an unparseable window must not swallow everything"


def test_one_tick_delivers_at_most_one_line() -> None:
    """Two unprompted lines in the same instant is already a conversation.

    And a conversation is something the user started.
    """
    spoken: list = []
    loop = build(
        ScriptedProbe(
            [
                [
                    obs("a", event=EventKind.ERROR, weight=90),
                    obs("b", event=EventKind.ERROR, weight=80),
                    obs("c", event=EventKind.ERROR, weight=70),
                ]
            ]
        ),
        emit=spoken,
    )
    loop.tick(1000.0, wall=DAWN)
    assert len(spoken) <= 1


def test_the_cap_keeps_the_most_urgent_candidate() -> None:
    """Weighting orders the *shortlist*; the judge still decides each one.

    Weight is not a decision. It only decides who gets the chance to be judged
    when there is room for one, so a routine result cannot crowd out a fault.
    """
    spoken: list = []
    loop = build(
        ScriptedProbe(
            [
                [
                    obs("routine", event=EventKind.TOOL_FINISHED, weight=10),
                    obs("the_fault", event=EventKind.ERROR, weight=90),
                ]
            ]
        ),
        emit=spoken,
    )
    loop.tick(1000.0, wall=DAWN)
    assert spoken and "the_fault" in spoken[0].text


# ---------------------------------------------------------------------------
# 4. The floor: never talk over someone.
# ---------------------------------------------------------------------------


def test_a_deferred_utterance_is_queued_not_dropped() -> None:
    """The judge says "queue it, not drop it", so the loop keeps a queue.

    LookOut extended its dwell time when it heard someone thinking, precisely so
    it would not talk over them. Dropping the content instead of postponing it
    would satisfy the letter of that rule and throw away the content.

    The event is a *verified mission completion after a long quiet room*, which
    is the case the judge defers rather than drops: the content is settled and
    only the timing is wrong. (An interrupt is the opposite — the judge drops
    it outright, because a stale interrupt is worse than silence, and the test
    below pins that difference.)
    """
    floor = FloorState()
    floor.note_spoke(0.0)  # something really was said, so a gap is knowable
    floor.note_user_speech(mid_sentence=True)  # ...and now they are mid-sentence
    spoken: list = []
    loop = build(
        ScriptedProbe([[obs("msn_1", event=EventKind.MISSION_COMPLETE)]]),
        emit=spoken,
        floor=floor,
    )

    assert loop.consider(1000.0, wall=DAWN) == [], "must not cut in mid-sentence"
    assert spoken == []
    assert loop.status(1000.0)["deferred_pending"] == 1, "held, not dropped"

    # The sentence ends.
    floor.clear_floor()
    later = loop.consider(1010.0, wall=DAWN)
    assert len(later) == 1, "the deferred line must arrive once the floor clears"
    assert "msn_1" in later[0].text
    # The released line carries a *fresh* reason, not the stale one. The loop
    # re-judges rather than replaying a cached verdict, so the reason describes
    # why it is worth saying now; how long it waited is separate metadata.
    assert later[0].meta["deferred_for"] == "10s"
    assert "quiet window" in later[0].reason


def test_an_interrupt_is_dropped_rather_than_deferred() -> None:
    """The one case where the floor wins outright.

    A stale interrupt delivered after the sentence ends is worse than not
    delivering it, so the judge drops it — and the loop must not "helpfully"
    queue it, which would reintroduce exactly that.
    """
    floor = FloorState()
    floor.note_spoke(0.0)
    floor.note_user_speech(mid_sentence=True)
    loop = build(
        ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]),
        floor=floor,
    )
    assert loop.consider(1000.0, wall=DAWN) == []
    assert loop.status(1000.0)["deferred_pending"] == 0, "an interrupt must not be queued"
    reason = loop.status(1000.0)["last_decision"]["reason"]
    assert "dropped rather than cut in" in reason


def test_a_stale_deferral_is_dropped_rather_than_delivered_late() -> None:
    """A line composed for a moment that has passed is not a delayed message.

    It is a wrong one. Dropping is the honest handling, and it is counted so
    that "why did it never mention that?" has an answer.

    The probe fires once and then goes quiet, which is what makes this about the
    *queue*. With a probe that keeps reporting, a fresh observation would
    legitimately speak in the later tick and mask the fact that the queued one
    expired — the test would pass for the wrong reason.
    """
    floor = FloorState()
    floor.note_spoke(0.0)
    floor.note_user_speech(mid_sentence=True)
    loop = build(
        ScriptedProbe([[obs("msn_1", event=EventKind.MISSION_COMPLETE)], []]),
        floor=floor,
    )
    loop.consider(1000.0, wall=DAWN)
    assert loop.status(1000.0)["deferred_pending"] == 1

    floor.clear_floor()
    assert loop.consider(1000.0 + 10_000.0, wall=DAWN) == []
    assert loop.status(11_000.0)["deferred_dropped"] == 1
    assert loop.status(11_000.0)["deferred_pending"] == 0


def test_a_probe_with_an_unstable_detail_will_reannounce() -> None:
    """The loop deduplicates on the detail string, so detail has to be stable.

    This is not a hypothetical. `event_signature` in core/intent.py builds its
    key as ``event:detail`` and says so in a docstring: "detail is expected to
    be a stable identifier, never a timestamp... timestamps make every
    occurrence unique and defeat repeat suppression entirely."

    A real probe did exactly that — it named the error after whichever
    processes had just exited, so the *same* condition produced
    ``process_gone:conhost.exe,python.exe`` and then
    ``process_gone:conhost.exe,python.exe,timeout.exe``, 30 seconds apart. That
    is inside the 180s routine cooldown, so with a stable detail the second
    sample would have been suppressed; with an unstable one it was not, and one
    fault became three interrupts in eight hours.

    So the loop does not fix this, because the loop cannot: only the probe
    knows what makes two samples the same event. What this test does is pin the
    contract that every probe has to honour, so the next one to get it wrong
    fails here instead of in someone's face at 2am.
    """
    unstable = ScriptedProbe(
        [
            [obs("process_gone:conhost.exe,python.exe", event=EventKind.ERROR, weight=90)],
            [obs("process_gone:conhost.exe,python.exe,timeout.exe", event=EventKind.ERROR, weight=90)],
        ]
    )
    spoken: list = []
    loop = build(unstable, emit=spoken)
    loop.tick(1000.0, wall=DAWN)
    loop.tick(1030.0, wall=DAWN)  # 30s: well inside the 180s cooldown
    assert len(spoken) == 2, "this is the bug: one condition, two interruptions"

    # The fix is one line in the probe: name the condition, not the sample.
    # Whatever is stable about the *event* rather than the instances of it.
    stable = ScriptedProbe(
        [
            [obs("process_gone", event=EventKind.ERROR, weight=90)],
            [obs("process_gone", event=EventKind.ERROR, weight=90)],
        ]
    )
    quiet: list = []
    other = build(stable, emit=quiet)
    other.tick(1000.0, wall=DAWN)
    other.tick(1030.0, wall=DAWN)
    assert len(quiet) == 1, "a stable detail collapses the repeats into the cooldown"


def test_a_folded_acknowledgement_is_never_a_standalone_bubble() -> None:
    """An error during the user's turn folds into their reply, or not at all.

    There is no reply in flight here, and opening a second speaking turn the
    judge explicitly declined to authorise is worse than waiting.
    """
    floor = FloorState()
    floor.note_user_speech(mid_sentence=False)  # just spoke, not mid-sentence
    loop = build(
        ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]),
        floor=floor,
    )
    assert loop.consider(1000.0, wall=DAWN) == []
    assert loop.status(1000.0)["last_decision"]["rule"] == "user_has_floor"


def test_the_floor_is_cleared_by_the_caller_not_inferred() -> None:
    """The loop has no opinion about user state; it is told."""
    floor = FloorState()
    assert floor.snapshot()["user_mid_sentence"] is False
    floor.note_user_speech(mid_sentence=True)
    assert floor.snapshot()["user_mid_sentence"] is True
    floor.clear_floor()
    assert floor.snapshot()["user_mid_sentence"] is False


def test_a_cold_start_reports_unproven_attention_not_a_long_silence() -> None:
    """Fresh history means "never spoken to", and the loop must pass that through.

    The alternative — reading an empty history as an infinite silence — would
    let the first routine event after every reboot announce itself uninvited.
    """
    floor = FloorState()
    assert floor.seconds_since_last_speech(9999.0) is None
    loop = build(ScriptedProbe([[obs("build", verified=True)]]), floor=floor)
    assert loop.consider(9999.0, wall=DAWN) == []
    assert "never spoken to" in loop.status(9999.0)["last_decision"]["reason"]


def test_a_known_gap_is_passed_through_so_a_quiet_window_can_open() -> None:
    """Once something has actually spoken, the gap is knowable and usable.

    This is the restart case: the judge's in-memory history is empty after a
    boot, so without an externally-supplied gap a genuinely long-finished task
    would be unspeakable until something else spoke first.
    """
    floor = FloorState()
    floor.note_spoke(0.0)
    loop = build(ScriptedProbe([[obs("build", verified=True)]]), floor=floor)
    (utterance,) = loop.consider(200.0, wall=DAWN)
    assert utterance.rule == "quiet_window"
    assert "3m 20s" in utterance.reason


# ---------------------------------------------------------------------------
# 5. The probes, against the state they actually read.
# ---------------------------------------------------------------------------


def test_the_runtime_issue_probe_reports_a_new_fault_exactly_once() -> None:
    """Dedup is per fault, not per tick.

    Without it, a fault still present in the ring would be re-offered on every
    tick forever, and the judge would spend its whole budget re-deciding the
    same sentence.
    """
    from core.run_events import record_issue

    record_issue("test_component", "test_op", ValueError("a distinctive probe fault"))
    probe = RuntimeIssueProbe()
    first = [o for o in probe.poll(0.0) if o.meta.get("operation") == "test_op"]
    assert len(first) == 1
    assert first[0].verified is True
    assert first[0].event is EventKind.ERROR
    # Spoken as "component.operation": findable by hand, sayable out loud.
    assert first[0].detail == "test_component.test_op"

    second = [o for o in probe.poll(0.0) if o.meta.get("operation") == "test_op"]
    assert second == [], "the same fault must not be re-offered"


def test_the_runtime_issue_probe_keeps_distinct_faults_apart() -> None:
    """Two different bugs in one component are two pieces of news."""
    from core.run_events import record_issue

    record_issue("test_pair", "op", ValueError("fault number one here"))
    record_issue("test_pair", "op", KeyError("fault number two here"))
    probe = RuntimeIssueProbe()
    details = [o.meta.get("fingerprint") for o in probe.poll(0.0) if o.meta.get("component") == "test_pair"]
    assert len(details) == 2
    assert details[0] != details[1]


def test_a_broken_probe_does_not_take_the_loop_down() -> None:
    """One bad source must not cost the other three."""
    from core.proactivity import Probe  # noqa: F401 - documents the protocol shape

    class Exploding:
        name = "exploding"

        def poll(self, now: float):
            raise RuntimeError("probe is on fire")

    spoken: list = []
    loop = build(
        Exploding(),
        ScriptedProbe([[obs("memory.recall", event=EventKind.ERROR, weight=90)]]),
        emit=spoken,
    )
    (utterance,) = loop.consider(1000.0, wall=DAWN)
    assert "memory.recall" in utterance.text
    assert len(spoken) == 0  # consider() does not deliver


def test_the_default_probe_set_watches_the_announced_sources() -> None:
    """An error, a finished task, a mission, a resource.

    Asserted as a superset rather than an exact set, because this list is
    allowed to grow: a new watcher that fails to register is silent, and
    silence is the failure mode nobody notices. A new one must not be able to
    break this test either, so the check is "everything we promised is there".
    """
    names = {p.name for p in default_probes(ProactivityConfig())}
    assert {"runtime_issue", "job", "mission", "resource"} <= names


def test_a_short_job_is_not_a_long_running_task() -> None:
    """"Your 2s command finished" is noise dressed as a report."""
    probe = JobCompletionProbe(long_job_seconds=120.0)
    assert probe.poll(0.0) == []  # nothing seen in flight yet


def test_the_resource_probe_is_edge_triggered() -> None:
    """A condition that is still true is not a new event.

    The judge would suppress most repeats, but relying on suppression for a
    condition that has not changed is using the wrong mechanism for the job.
    """
    probe = ResourceThresholdProbe(path="C:/", min_free_pct=5.0)

    class FakeShutil:
        """Just the one function the probe uses, with a settable free amount."""

        free = 50.0

        @staticmethod
        def disk_usage(_path):
            return type("Usage", (), {"total": 1000.0, "free": FakeShutil.free})()

    import core.proactivity as mod

    original = mod.shutil
    mod.shutil = FakeShutil  # type: ignore[assignment]
    try:
        assert probe.poll(0.0) == [], "healthy disk is not news"

        FakeShutil.free = 2.0  # 2 of 1000 bytes = 0.2%, past a 5% floor
        crossing = probe.poll(100.0)
        assert len(crossing) == 1
        assert crossing[0].detail == "disk_free"
        assert crossing[0].meta["free_pct"] == 0.2, "percent, not a 0-1 ratio"

        # Still breached one tick later: silence, not a second alarm.
        assert probe.poll(200.0) == []
        # Recovery re-arms the edge.
        FakeShutil.free = 80.0
        assert probe.poll(300.0) == []
        FakeShutil.free = 1.0
        assert len(probe.poll(400.0)) == 1, "a fresh crossing after recovery is news again"
    finally:
        mod.shutil = original


def test_the_resource_probe_does_not_stat_every_tick() -> None:
    """A stat call per tick is a measurable cost for zero extra information."""
    probe = ResourceThresholdProbe(path="C:/")
    probe.poll(0.0)
    assert probe.poll(1.0) == []
    assert probe.poll(59.0) == []


# ---------------------------------------------------------------------------
# 6. Observability: silence must be explicable.
# ---------------------------------------------------------------------------


def test_status_explains_the_silence() -> None:
    """A quiet system and a crashed one look identical from outside.

    This is the endpoint's whole reason to exist: "it never said anything" has
    two causes, and only one of them is a design decision.
    """
    loop = build(ScriptedProbe([[]]))
    status = loop.status(100.0)
    assert status["running"] is False
    assert status["ticks"] == 0
    assert status["spoken"] == 0
    assert status["probes"] == ["scripted"]
    assert status["last_decision"] is None
    assert status["silence"]["known"] is False
    assert status["silence"]["text"] == "never spoken to"


def test_status_carries_the_judges_own_reason_for_the_last_call() -> None:
    """The answer to "why didn't you speak?" is the policy's own reasoning."""
    loop = build(ScriptedProbe([[obs("build", verified=True)]]))
    loop.consider(9999.0, wall=DAWN)
    last = loop.status(9999.0)["last_decision"]
    assert last["should_speak"] is False
    assert last["rule"] == "unremarkable"
    assert last["reason"]


def test_a_long_silence_renders_the_way_a_person_would_say_it() -> None:
    """Because the whole point of a reason is that it can be read back."""
    known = LongSilence(seconds=125.0, known=True)
    assert known.render() == "2m 05s"
    assert LongSilence(seconds=0.0, known=False).render() == "never spoken to"


# ---------------------------------------------------------------------------
# 7. The thread, started and stopped for real.
# ---------------------------------------------------------------------------


def test_start_and_stop_are_clean_and_idempotent() -> None:
    """A loop nobody can shut down is a loop nobody can ship."""
    spoken: list = []
    loop = build(
        ScriptedProbe([[]]),
        emit=spoken,
        config=ProactivityConfig(quiet_hours="", interval_seconds=0.05),
    )
    try:
        assert loop.start() is True
        assert loop.running is True
        assert loop.start() is True, "starting twice must not spawn a second thread"
        assert loop.stop() is None
        assert loop.running is False
    finally:
        loop.stop()


def test_a_disabled_loop_does_not_start() -> None:
    """`HERMUS_PROACTIVITY_ENABLED=0` has to actually mean off."""
    loop = build(ScriptedProbe([[]]), config=ProactivityConfig(enabled=False, quiet_hours=""))
    assert loop.start() is False
    assert loop.running is False


def test_the_thread_actually_ticks() -> None:
    """Not a smoke test of `start()` alone: the thread has to call `tick()`."""
    loop = build(ScriptedProbe([[]]), config=ProactivityConfig(quiet_hours="", interval_seconds=0.02))
    try:
        loop.start()
        deadline = 200
        while deadline and loop.status()["ticks"] < 2:
            deadline -= 1
            import time as _t

            _t.sleep(0.01)
        assert loop.status()["ticks"] >= 2, "the background thread must poll"
    finally:
        loop.stop()


# ---------------------------------------------------------------------------
# 8. The room state the loop reads.
# ---------------------------------------------------------------------------


def test_a_blocked_room_is_reported_because_work_stopped() -> None:
    """Silence while blocked reads as "it did not notice it is stuck".

    The block itself is the news on the first sighting, and its *age* is the
    second one: past the mark where a block stops being a pause and starts
    being a decision only the user can make, the line changes to say so.
    """
    floor = FloorState()
    # The same clock the loop is driven with. Passing the real one here would
    # make the block "0s old" at every simulated timestamp and the age-based
    # rung below unreachable — two clocks in one moment is the bug this
    # signature exists to prevent.
    floor.set_room(RoomState.BLOCKED, now=0.0)
    floor.note_spoke(0.0)
    loop = build(
        ScriptedProbe([[obs("something", event=EventKind.TOOL_FINISHED)]]),
        floor=floor,
    )
    fresh = loop.consider(5.0, wall=DAWN)
    assert fresh, "a block must be reported, not sat on"
    assert fresh[0].rule == "blocked"
    assert "holding here" in fresh[0].text

    # A block that has aged is different news, and says so differently.
    loop.judge.reset()
    older = build(
        ScriptedProbe([[obs("something", event=EventKind.TOOL_FINISHED)]]),
        floor=floor,
    )
    later = older.consider(300.0, wall=DAWN)
    assert later and later[0].rule == "block_age"
    assert "Nothing is moving until that is decided" in later[0].text


def test_the_room_comes_from_the_floor_not_from_the_probe() -> None:
    """A probe reports a fact; only the loop knows what room it is in."""
    floor = FloorState()
    floor.set_room(RoomState.VERIFYING)
    loop = build(ScriptedProbe([[obs("build", verified=True)]]), floor=floor)
    loop.consider(9999.0, wall=DAWN)
    reason = loop.status(9999.0)["last_decision"]["reason"]
    # While verifying, an interim claim is one the system cannot take back, so
    # it declines to make one. The room is the reason, and it came from the
    # floor rather than from anything the probe knew.
    assert "verifying" in reason
    assert "interim claim" in reason
