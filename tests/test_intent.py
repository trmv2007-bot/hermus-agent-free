"""Tests for the intent judge.

These assert on **reason strings**, not on truthiness of the boolean. A judge
that returns the right answer for an unexplained reason is a haunted object: it
works until the first case nobody anticipated, and then it is unexplainable in
production. The guideline this encodes is Amershi et al. CHI 2019 number 11,
"make clear why the system did what it did", which is the reason
``Decision.reason`` exists and why every test here pins one.

No model, no network, no gateway, no sleeping. Time is a number the test
supplies, so placing two events three minutes apart costs nothing.
"""

from __future__ import annotations

import pytest

from core.intent import (
    SAY_NOTHING,
    VERIFIED,
    EventKind,
    IntentJudge,
    JudgeConfig,
    Moment,
    RoomState,
    SpeechHistory,
    Urgency,
    decide,
    event_signature,
    format_duration,
)


def idle(now: float = 0.0) -> Moment:
    return Moment(now=now, room=RoomState.IDLE)


def seeded(now: float = 0.0) -> IntentJudge:
    """A judge that already spoke ``now`` seconds ago.

    An idle no-event moment produces silence and therefore records nothing, so
    tests that need a non-empty history have to seed it with a real utterance.
    Every other silence test uses a bare ``IntentJudge()``.
    """
    judge = IntentJudge()
    judge.observe(
        Moment(
            now=now,
            room=RoomState.IDLE,
            event=EventKind.MISSION_COMPLETE,
            event_detail="__seed__",
            event_verified=True,
            seconds_since_last_speech=1_000.0,
        )
    )
    assert judge.history.marks, "the seed must actually have spoken"
    return judge


# ---------------------------------------------------------------------------
# 1. Idle and no event means silence.
# ---------------------------------------------------------------------------


def test_idle_with_no_event_is_silence() -> None:
    d = decide(idle(), SpeechHistory(), JudgeConfig())

    assert d.should_speak is False
    assert d.urgency is Urgency.SILENT
    assert d.line == SAY_NOTHING


def test_idle_no_event_reason_is_specific_and_quantified() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(idle(90.0))

    assert d.rule == "nothing_to_say"
    # Not "event occurred", and not empty. It names the room, the absence of an
    # event, and how long since the last utterance.
    assert d.reason == "no event and room is idle; last utterance was 1m 30s"
    assert "room is idle" in d.reason
    assert "1m 30s" in d.reason
    assert d.reason != "event occurred"


def test_never_spoken_reports_that_honestly_rather_than_as_long_silence() -> None:
    # A fresh session is not "infinitely quiet", it is unproven attention. If
    # this read as a long silence, the very first routine event after a restart
    # would qualify for the quiet window and announce itself uninvited.
    d = decide(Moment(now=9999.0, room=RoomState.IDLE), SpeechHistory(), JudgeConfig())

    assert d.should_speak is False
    assert d.reason.endswith("last utterance was never spoken to")


# ---------------------------------------------------------------------------
# 2. An error interrupts.
# ---------------------------------------------------------------------------


def test_error_in_a_working_room_interrupts() -> None:
    judge = seeded(0.0)  # a real utterance, so the rate gate is clear
    d = judge.observe(
        Moment(
            now=30.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="pdf_extract",
            event_verified=True,
        )
    )

    assert d.should_speak is True
    assert d.urgency is Urgency.INTERRUPT
    assert d.confidence == VERIFIED
    assert "pdf_extract" in d.line
    assert "pdf_extract" in d.reason
    assert "would read as not noticing" in d.reason


def test_blocked_room_interrupts() -> None:
    judge = seeded(0.0)
    d = judge.observe(
        Moment(
            now=45.0,
            room=RoomState.BLOCKED,
            blocked_for_seconds=45.0,
            event=EventKind.MISSION_COMPLETE,
            event_detail="build-report",
            event_verified=True,
        )
    )

    assert d.should_speak is True
    assert d.urgency is Urgency.INTERRUPT
    assert d.rule == "blocked"
    assert "45s" in d.reason


def test_error_and_block_are_the_only_paths_to_an_interrupt() -> None:
    """Outside a blocked room, no event may reach INTERRUPT.

    BLOCKED is exempt on purpose: it is a room state rather than an event, and
    a blocked room interrupts whatever happened because the block itself is the
    news. That is the behaviour asserted separately below.
    """
    for event in (
        EventKind.NONE,
        EventKind.TOOL_FINISHED,
        EventKind.MEMORY_WRITTEN,
        EventKind.MISSION_COMPLETE,
    ):
        for room in (r for r in RoomState if r is not RoomState.BLOCKED):
            judge = seeded(0.0)
            d = judge.evaluate(
                Moment(
                    now=400.0,
                    room=room,
                    event=event,
                    event_detail="thing",
                    event_verified=True,
                )
            )
            assert d.urgency is not Urgency.INTERRUPT, f"{event} in {room}"


# ---------------------------------------------------------------------------
# 3. Repeats are suppressed after the first, but not forever.
# ---------------------------------------------------------------------------


def test_repeating_the_same_event_is_suppressed_after_the_first() -> None:
    judge = seeded(0.0)  # opens the rate gate
    first = judge.observe(
        Moment(
            now=30.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="network",
            event_verified=True,
        )
    )
    second = judge.observe(
        Moment(
            now=60.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="network",
            event_verified=True,
        )
    )

    assert first.should_speak is True
    assert second.should_speak is False
    assert second.urgency is Urgency.SILENT
    assert second.rule == "repeat"
    # The reason must distinguish "already told you" from "not worth telling",
    # and must carry the actual elapsed time and the repeat ordinal.
    assert "already reported 30s ago" in second.reason
    assert "occurrence 2 of the same fault" in second.reason
    assert "same fault, not new news" in second.reason


def test_a_third_repeat_escalates_so_a_persistent_fault_does_not_look_resolved() -> None:
    judge = IntentJudge()
    for now in (30.0, 60.0):
        judge.observe(
            Moment(
                now=now,
                room=RoomState.WORKING,
                event=EventKind.ERROR,
                event_detail="network",
                event_verified=True,
            )
        )
    third = judge.observe(
        Moment(
            now=90.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="network",
            event_verified=True,
        )
    )

    assert third.should_speak is True
    assert third.urgency is Urgency.INFORM
    assert "occurrence 3" in third.reason
    assert "Same fault as before" in third.line


def test_a_fourth_repeat_goes_quiet_again() -> None:
    judge = IntentJudge()
    for now in (30.0, 60.0, 90.0):
        judge.observe(
            Moment(
                now=now,
                room=RoomState.WORKING,
                event=EventKind.ERROR,
                event_detail="network",
                event_verified=True,
            )
        )
    fourth = judge.observe(
        Moment(
            now=120.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="network",
            event_verified=True,
        )
    )

    assert fourth.should_speak is False
    assert fourth.rule == "repeat"


def test_routine_event_is_suppressed_by_its_own_cooldown() -> None:
    judge = seeded(0.0)
    first = judge.observe(
        Moment(
            now=200.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
        )
    )
    # observe, not evaluate: suppression state only moves when the judge is
    # allowed to record that it spoke. At 350s the gap is 150s, inside the 3m
    # cooldown for routine events, even though the room has gone quiet.
    second = judge.observe(
        Moment(
            now=350.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
        )
    )

    assert first.rule == "quiet_window"
    assert second.should_speak is False
    assert second.rule == "cooldown"
    assert "cooldown" in second.reason
    assert "search" in second.reason


def test_a_quiet_room_does_not_make_the_same_event_interesting_twice() -> None:
    """Cooldown is checked before the quiet window, deliberately."""
    judge = seeded(0.0)
    judge.observe(
        Moment(
            now=200.0,
            room=RoomState.WORKING,
            event=EventKind.MISSION_COMPLETE,
            event_detail="deploy",
            event_verified=True,
        )
    )
    d = judge.evaluate(
        Moment(
            now=230.0,
            room=RoomState.WORKING,
            event=EventKind.MISSION_COMPLETE,
            event_detail="deploy",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert d.rule == "cooldown"


# ---------------------------------------------------------------------------
# 4. Mid sentence means never interrupt, for any event, in any room.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", list(EventKind))
@pytest.mark.parametrize("room", list(RoomState))
def test_user_mid_sentence_never_interrupts(event: EventKind, room: RoomState) -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=30.0,
            room=room,
            event=event,
            event_detail="thing",
            event_verified=True,
            user_mid_sentence=True,
            blocked_for_seconds=300.0,
        )
    )

    assert d.urgency is not Urgency.INTERRUPT
    assert d.should_speak is False or d.defers is True


def test_actively_typing_also_protects_the_floor() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=30.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
            user_typing=True,
        )
    )

    assert d.should_speak is False
    assert d.urgency is Urgency.SILENT
    assert "mid sentence" in d.reason
    assert "dropped rather than cut in" in d.reason


def test_a_mid_sentence_interrupt_is_dropped_not_deferred() -> None:
    """A stale interrupt delivered after the sentence ends is worse than none."""
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=30.0,
            room=RoomState.BLOCKED,
            event=EventKind.MISSION_COMPLETE,
            event_detail="x",
            event_verified=True,
            user_mid_sentence=True,
        )
    )

    assert d.should_speak is False
    assert d.defers is False


def test_non_interrupt_news_during_a_sentence_is_queued_not_dropped() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=400.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
            user_mid_sentence=True,
        )
    )

    assert d.should_speak is True
    assert d.urgency is Urgency.INFORM
    assert d.defers is True
    assert "queued for the turn boundary" in d.reason


# ---------------------------------------------------------------------------
# 5. Long silence plus a notable event means speak.
# ---------------------------------------------------------------------------


def test_long_silence_plus_notable_event_speaks() -> None:
    judge = seeded(0.0)
    d = judge.observe(
        Moment(
            now=300.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
        )
    )

    assert d.should_speak is True
    assert d.urgency is Urgency.INFORM
    assert d.rule == "quiet_window"
    assert "verified" in d.reason
    assert "quiet for 5m" in d.reason
    assert "First result in 5m" in d.line


def test_a_silence_shorter_than_the_quiet_window_does_not_qualify() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=100.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert d.rule == "unremarkable"
    assert "nothing pending" in d.reason


def test_a_quiet_room_does_not_promote_a_memory_write() -> None:
    """Bookkeeping the user did not ask about is not 'worth mentioning'."""
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=900.0,
            room=RoomState.WORKING,
            event=EventKind.MEMORY_WRITTEN,
            event_detail="user-prefers-dark",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert "bookkeeping" in d.reason
    assert "quiet is not a reason to volunteer it" in d.reason


# ---------------------------------------------------------------------------
# 6. Honesty: never announce what cannot be verified.
# ---------------------------------------------------------------------------


def test_unverified_tool_return_is_never_announced_as_a_result() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=900.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=False,
        )
    )

    assert d.should_speak is False
    assert d.rule == "cannot_vouch"
    assert d.confidence == "unverified"
    assert "there is no outcome to report" in d.reason


def test_unverified_mission_completion_is_silence_because_it_would_be_a_promise() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=900.0,
            room=RoomState.WORKING,
            event=EventKind.MISSION_COMPLETE,
            event_detail="migrate-db",
            event_verified=False,
        )
    )

    assert d.should_speak is False
    assert d.rule == "cannot_vouch"
    assert "would be a promise" in d.reason


def test_the_honesty_gate_outranks_urgency() -> None:
    """Even a quiet room cannot promote something unverified."""
    d = decide(
        Moment(
            now=10_000.0,
            room=RoomState.IDLE,
            event=EventKind.MISSION_COMPLETE,
            event_detail="migrate-db",
            event_verified=False,
            seconds_since_last_speech=10_000.0,
        ),
        SpeechHistory(),
        JudgeConfig(),
    )
    assert d.should_speak is False
    assert d.rule == "cannot_vouch"


# ---------------------------------------------------------------------------
# 7. Room-specific behaviour.
# ---------------------------------------------------------------------------


def test_nothing_announces_itself_during_compaction() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=600.0,
            room=RoomState.COMPACTING,
            event=EventKind.TOOL_FINISHED,
            event_detail="trim",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert d.rule == "internal"
    assert "internal machinery" in d.reason


def test_verifying_room_refuses_to_make_an_interim_claim() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=400.0,
            room=RoomState.VERIFYING,
            event=EventKind.MEMORY_WRITTEN,
            event_detail="candidate",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert "not final yet" in d.reason
    assert "cannot take back" in d.reason


def test_attention_room_caps_an_interrupt_because_the_user_already_has_the_floor() -> None:
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=30.0,
            room=RoomState.ATTENTION,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
        )
    )

    assert d.urgency is Urgency.INFORM
    assert d.rule == "urgent"


def test_a_long_block_becomes_news_again_once_it_changes_state() -> None:
    judge = seeded(0.0)
    d = judge.observe(
        Moment(
            now=400.0,
            room=RoomState.BLOCKED,
            event=EventKind.MISSION_COMPLETE,
            event_detail="deploy",
            event_verified=True,
            blocked_for_seconds=600.0,
        )
    )

    assert d.urgency is Urgency.INFORM
    assert d.rule == "block_age"
    assert "10m" in d.reason


# ---------------------------------------------------------------------------
# 8. The user already has the floor.
# ---------------------------------------------------------------------------


def test_non_urgent_news_folds_into_the_reply_already_in_flight() -> None:
    judge = IntentJudge()
    d = judge.evaluate(
        Moment(
            now=400.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
            user_just_spoke=True,
        )
    )

    assert d.should_speak is False
    assert d.defers is True
    assert "queued for the end of the sentence" in d.reason


def test_an_error_during_the_user_turn_acknowledges_instead_of_opening_a_second_turn() -> None:
    judge = IntentJudge()
    d = judge.evaluate(
        Moment(
            now=400.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
            user_just_spoke=True,
        )
    )

    assert d.should_speak is True
    assert d.urgency is Urgency.ACKNOWLEDGE
    assert d.fold_into_reply is True
    assert d.rule == "user_has_floor"
    assert "same breath" in d.reason


def test_a_folded_acknowledgement_does_not_start_a_cooldown() -> None:
    judge = IntentJudge()
    judge.observe(
        Moment(
            now=400.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
            user_just_spoke=True,
        )
    )
    assert judge.history.marks == []

    # The user did hear about the error, so a later occurrence is legitimately
    # a repeat. What matters is that no *announcement* cooldown was started,
    # which is what the empty marks above prove.
    assert judge.history.count_occurrences("error:api", 400.0, 900.0) == 1
    assert judge.history.seconds_since_signature("error:api", 400.0) is None

    # Past the escalation window the fault counts as new again.
    later = judge.evaluate(
        Moment(
            now=400.0 + 901.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
        )
    )
    assert later.urgency is Urgency.INTERRUPT


# ---------------------------------------------------------------------------
# 9. The minimum gap, and why it exists.
# ---------------------------------------------------------------------------


def test_minimum_gap_holds_back_a_different_event_too() -> None:
    """Not nagging means not interrupting, even for a different event."""
    judge = seeded(0.0)
    d = judge.evaluate(
        Moment(
            now=10.0,
            room=RoomState.WORKING,
            event=EventKind.MISSION_COMPLETE,
            event_detail="deploy",
            event_verified=True,
        )
    )

    assert d.should_speak is False
    assert d.rule == "rate"
    assert "minimum gap" in d.reason
    assert "10s" in d.reason


def test_history_derives_the_gap_when_the_moment_does_not_supply_it() -> None:
    judge = seeded(0.0)
    judge.observe(idle(500.0))  # silence, so it records nothing
    assert len(judge.history.marks) == 1, "an idle moment must not record a mark"

    # No seconds_since_last_speech supplied, so the gap comes from the last
    # real mark, which is the seed at t=0. Ten seconds is under the minimum.
    d = judge.evaluate(
        Moment(
            now=10.0,
            room=RoomState.WORKING,
            event=EventKind.TOOL_FINISHED,
            event_detail="search",
            event_verified=True,
        )
    )
    assert d.rule == "rate"
    assert "10s after the last utterance" in d.reason


# ---------------------------------------------------------------------------
# 10. Purity, determinism, and the shape of the contract.
# ---------------------------------------------------------------------------


def test_decide_is_pure_and_deterministic() -> None:
    moment = Moment(
        now=30.0,
        room=RoomState.WORKING,
        event=EventKind.ERROR,
        event_detail="api",
        event_verified=True,
    )
    history = SpeechHistory()
    history.record(decide(moment, history, JudgeConfig()), moment)

    first = decide(moment, history, JudgeConfig())
    second = decide(moment, history, JudgeConfig())
    assert first == second


def test_decide_does_not_mutate_the_history_it_is_given() -> None:
    history = SpeechHistory()
    moment = Moment(
        now=30.0,
        room=RoomState.WORKING,
        event=EventKind.ERROR,
        event_detail="api",
        event_verified=True,
    )
    decide(moment, history, JudgeConfig())
    assert history.marks == []


def test_every_decision_carries_a_reason_long_enough_to_be_specific() -> None:
    """Guard against a future branch returning a terse or empty reason."""
    events = list(EventKind)
    rooms = list(RoomState)
    for event in events:
        for room in rooms:
            for mid in (False, True):
                d = decide(
                    Moment(
                        now=30.0,
                        room=room,
                        event=event,
                        event_detail="thing",
                        event_verified=True,
                        user_mid_sentence=mid,
                        blocked_for_seconds=300.0,
                    ),
                    SpeechHistory(),
                    JudgeConfig(),
                )
                assert len(d.reason) > 30, f"{event}/{room}/mid={mid}: {d.reason!r}"
                assert d.reason.lower() != "event occurred"
                assert d.rule != ""


def test_silence_always_uses_the_explicit_say_nothing_token() -> None:
    for event in EventKind:
        d = decide(
            Moment(now=0.0, event=event, event_detail="x", event_verified=True),
            SpeechHistory(),
            JudgeConfig(),
        )
        if not d.should_speak:
            assert d.line == SAY_NOTHING


def test_a_speaking_decision_never_carries_the_silence_token() -> None:
    judge = IntentJudge()
    d = judge.observe(
        Moment(
            now=30.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
        )
    )
    assert d.should_speak is True
    assert d.line != SAY_NOTHING
    assert len(d.line.split()) >= 3


def test_no_module_level_import_of_anything_heavy_or_side_effecting() -> None:
    """This module is a decision function. It must stay cheap and inert."""
    import ast
    import pathlib

    source = pathlib.Path("core/intent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
    }, f"unexpected imports: {imported}"


# ---------------------------------------------------------------------------
# 11. Helpers.
# ---------------------------------------------------------------------------


def test_event_signature_ignores_whitespace_but_not_identity() -> None:
    assert event_signature(EventKind.ERROR, "api") == "error:api"
    assert event_signature(EventKind.ERROR, "  api  ") == "error:api"
    assert event_signature(EventKind.ERROR, "db") != "error:api"
    assert event_signature(EventKind.ERROR) == "error"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0s"),
        (0.4, "0.4s"),
        (41.0, "41s"),
        (59.0, "59s"),
        (60.0, "1m"),
        (61.0, "1m 01s"),
        (252.0, "4m 12s"),
        (3600.0, "1h 00m"),
        (-5.0, "0s"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_judge_reset_clears_suppression_state() -> None:
    judge = IntentJudge()
    judge.observe(
        Moment(
            now=30.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
        )
    )
    assert judge.history.marks

    judge.reset()
    d = judge.observe(
        Moment(
            now=60.0,
            room=RoomState.WORKING,
            event=EventKind.ERROR,
            event_detail="api",
            event_verified=True,
        )
    )
    assert d.urgency is Urgency.INTERRUPT


def test_with_config_returns_a_new_judge_and_leaves_the_original_alone() -> None:
    judge = IntentJudge()
    tight = judge.with_config(JudgeConfig(min_gap_seconds=0.0))

    assert tight is not judge
    assert tight.config.min_gap_seconds == 0.0
    assert judge.config.min_gap_seconds == 20.0
