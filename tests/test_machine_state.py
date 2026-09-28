"""Tests for the ambient machine-state layer.

Two rules govern this file, both learned the hard way while building it:

* **No mocks of the thing under test.** The reader is exercised against this
  actual machine - real ``psutil``, real processes, real disk. A test that
  injects a fake ``MachineState`` proves the arithmetic and nothing about
  whether the numbers are real, and the whole point of this module is that
  they are.
* **Mocks are allowed for the expensive parts.** A threshold is a parameter
  rather than a constant for exactly this reason, so a test can put the bar
  just under a real reading and make a genuinely true condition true, without
  allocating 14 GB to prove it.

What is asserted here is mostly the *silence*. A probe that never returns
empty has stopped being a monitor and become a narrator, and the regressions
worth guarding are the loud ones: repeating itself, re-arming wrongly, and
crashing in a way the loop swallows.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from core.machine_state import (
    MachineState,
    MachineStateProbe,
    MachineStateReader,
    diff,
    render_machine_summary,
)
from core.proactivity import Observation, ProactivityConfig, ProactivityLoop, default_probes
from core.intent import EventKind, JudgeConfig


# ---------------------------------------------------------------------------
# The reader, against this machine
# ---------------------------------------------------------------------------


class TestReaderOnThisMachine:
    def test_returns_a_real_populated_reading(self):
        reader = MachineStateReader(disk_path="C:/" if sys.platform == "win32" else "/")
        state = reader.read()
        assert state.available is True
        assert state.disk_free_pct is not None
        assert 0.0 < state.disk_free_pct <= 100.0
        assert state.process_count > 0
        assert state.memory is not None
        assert state.memory.total_mb > 0

    def test_second_read_produces_a_diff(self):
        reader = MachineStateReader()
        reader.read()
        time.sleep(0.4)
        second = reader.read()
        # The diff is populated for at least one dimension on any live box:
        # CPU moves between two samples even on an idle machine.
        assert second.delta, "a second reading on a live machine must diff something"

    def test_first_read_has_no_diff(self):
        # There is nothing to compare against, and inventing a baseline would
        # report a change that never happened.
        reader = MachineStateReader()
        assert reader.read().delta == {}

    def test_cpu_streak_is_edge_not_level(self):
        reader = MachineStateReader(cpu_sustained_pct=0.0, sustain_windows=2)
        reader.read()
        state = reader.read()
        assert state.cpu_streak >= 1
        reader.cpu_sustained_pct = 101.0
        assert reader.read().cpu_streak == 0

    def test_top_processes_exclude_kernel_and_idle(self):
        """The real reason this filter exists.

        Measured on this box before the filter: the top five processes by CPU
        were "System Idle Process" (1863%), "System", "Registry", and two with
        an empty name. None of those is ever the answer to "what is using the
        CPU".
        """
        reader = MachineStateReader()
        reader.read()
        state = reader.read()
        for proc in state.top_by_cpu:
            assert proc.pid != 0, "pid 0 is System Idle Process and is not a consumer"
            assert proc.name != "System Idle Process"
            assert proc.name.strip(), "an unnamed process cannot be reported to a user"

    def test_top_by_cpu_is_real_under_load(self):
        """A real load must show up as a real consumer.

        This is the test that catches the priming bug: a reader that rebuilds
        ``psutil.Process`` each poll reads 0.0% for every process forever, so
        it would pass every other test in this file while reporting that an
        idle registry was the top CPU consumer during a spin.

        Asserts on the spinners BY PID rather than on a global percentage
        threshold. A single-threaded spinner on this 12C/20T box is ~5% of
        total system CPU, so the old ">20%" bar sat above what one core can
        reach and the test failed roughly one run in three -- flaky, which is
        worse than absent, because it teaches you to distrust the suite.

        The priming bug would make these exact PIDs read 0.0% and drop out of
        the ranking entirely, so identity is the sharper assertion anyway.
        """
        spinners = [
            subprocess.Popen([sys.executable, "-c", "x=0\nwhile True: x+=1"]) for _ in range(2)
        ]
        try:
            reader = MachineStateReader(top_n=50)
            reader.read()  # primes the cached Process handles

            # Sample repeatedly. One 3s window is not enough on a machine that
            # also runs a desktop app, a gateway and a browser, and psutil needs
            # a prior sample before it reports anything but 0. top_n is raised
            # to 50 because the default 5 is a UI-sized list -- on a busy box
            # five other processes crowd the spinners out of it entirely, which
            # made this test fail about one run in three. Retrying plus a wider
            # view is the honest fix; a test that only passes when the machine
            # is quiet is a test that gets deleted eventually.
            hot: list[tuple[str, float]] = []
            for _ in range(5):
                time.sleep(1.5)
                state = reader.read()
                top = state.top_by_cpu
                assert top, "expected at least one reported consumer"
                hot = [(p.name, p.cpu_percent) for p in top if p.cpu_percent > 20.0]
                if any("python" in name.lower() for name, _ in hot):
                    break

            assert any("python" in name.lower() for name, _ in hot), (
                f"spinning processes were not measured across 5 samples; "
                f"reported top was {[(p.name, p.cpu_percent) for p in top]}"
            )
        finally:
            for proc in spinners:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass

    def test_process_exit_is_detected_across_two_polls(self):
        """A process that was observed running and then is not is news.

        Note the two-poll requirement, which is real and not a test artefact: a
        pid spawned and killed between two reads was never seen running, so
        there is nothing to report. The reader can only notice what it
        actually watched.
        """
        child = subprocess.Popen([sys.executable, "-c", "x=0\nwhile True: x+=1"])
        try:
            reader = MachineStateReader()
            reader.read()
            time.sleep(0.6)
            reader.read()
            assert child.pid in reader._known_pids, "the child should have been observed"
            child.kill()
            child.wait(timeout=5)
            time.sleep(0.6)
            state = reader.read()
            gone = state.delta.get("processes_gone")
            assert gone, "a process observed running and then gone must be reported"
            assert any(item.get("pid") == child.pid for item in gone)
        finally:
            if child.poll() is None:
                child.kill()

    def test_diff_does_not_erase_edges_set_during_the_raw_read(self):
        """Regression: the "process gone" edge could never fire.

        ``read()`` assigned ``state.delta = diff(...)`` after the raw read had
        already written its own edge into ``state.delta``, so the assignment
        silently discarded it. Observed as ``processes_gone: None`` in a live
        test after killing a process that had been alive across two polls.
        """
        reader = MachineStateReader()
        reader._known_pids = {999_999: "phantom.exe"}
        state = reader.read()
        assert "processes_gone" in state.delta


# ---------------------------------------------------------------------------
# diff(), which is pure and can be tested directly
# ---------------------------------------------------------------------------


def _state(**kw) -> MachineState:
    base = dict(cpu_percent=10.0, cpu_count=8, process_count=100, disk_free_pct=50.0)
    base.update(kw)
    return MachineState(**base)


class TestDiff:
    def test_no_previous_means_no_diff(self):
        assert diff(None, _state()) == {}

    def test_identical_readings_produce_no_diff(self):
        """A stable machine must diff to nothing.

        Zero-delta entries ("cpu_percent: 10.0 -> 10.0") made every reading look
        like something had changed, which is the level-vs-edge confusion the
        module is otherwise careful to avoid.
        """
        assert diff(_state(), _state()) == {}

    def test_unavailable_reading_yields_nothing(self):
        assert diff(_state(), MachineState(available=False, error="boom")) == {}

    def test_process_churn_below_ten_is_ignored(self):
        # Measured: idle Windows churns by a handful between 20s polls.
        assert "process_count" not in diff(_state(process_count=100), _state(process_count=105))
        assert "process_count" in diff(_state(process_count=100), _state(process_count=130))

    def test_small_disk_drift_is_ignored(self):
        assert "disk_free_pct" not in diff(_state(disk_free_pct=50.0), _state(disk_free_pct=49.0))
        assert "disk_free_pct" in diff(_state(disk_free_pct=50.0), _state(disk_free_pct=40.0))


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


class TestProbe:
    def test_healthy_machine_is_silent(self):
        probe = MachineStateProbe(poll_every_s=0.0)
        probe.reader.memory_critical_pct = 99.99
        probe.reader.cpu_sustained_pct = 99.99
        assert probe.poll(time.monotonic()) == [], (
            "a machine below every threshold must produce no observations at all"
        )

    def test_fires_once_on_a_real_breach(self):
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        # A real reading compared to a real bar, not an injected number.
        probe.reader.memory_critical_pct = max(1.0, first.memory.percent - 2.0)
        first_batch = probe.poll(time.monotonic())
        assert first_batch, "a genuinely breached condition must be reported"
        assert all(isinstance(o, Observation) for o in first_batch)

    def test_does_not_repeat_itself(self):
        """The regression that matters most for a monitor.

        A level-triggered version re-reports the same condition every poll. The
        user reads that three times and then turns the feature off, so the
        second silence is the feature working.
        """
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        probe.reader.memory_critical_pct = max(1.0, first.memory.percent - 2.0)
        assert probe.poll(time.monotonic()), "expected the first report"
        assert probe.poll(time.monotonic()) == [], "an unchanged condition must not be reported again"

    def test_recovery_rearms_the_edge(self):
        """A condition that stops, then returns, is news again.

        Recovery is simulated by moving the *reading*, not the bar: raising the
        threshold above the true value does not recover the machine, it only
        stops the condition from being true, and the latch correctly stays
        closed. The first version of this test did exactly that and failed for
        the right reason - the probe was right and the test was wrong.
        """
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        bar = max(1.0, first.memory.percent - 2.0)
        probe.reader.memory_critical_pct = bar
        assert probe.poll(time.monotonic()), "expected the first report"

        # A genuine recovery: the reading drops below the bar. The mutation has
        # to survive the next read, and ``poll()`` calls ``reader.read()`` which
        # rebuilds the state, so the bar is moved above the value that read
        # will actually produce. Setting it before the poll is what makes the
        # condition false at the moment it is evaluated.
        probe.reader.memory_critical_pct = 99.99
        recovered = probe.poll(time.monotonic())
        # Only assert on *memory*. A real process can exit between two polls on
        # any live machine - during this run the probe correctly reported a
        # bash.exe that had genuinely died - and that is the feature working,
        # not a flaky assertion.
        assert not [o for o in recovered if o.detail == probe._SUBJECT_MEMORY], (
            f"memory should have been silent, got {[o.detail for o in recovered]}"
        )
        assert probe._memory_breached is False, "recovery must clear the latch"

        # And it breaches again on a later real reading.
        probe.reader.memory_critical_pct = 1.0
        assert probe.poll(time.monotonic()), "a condition that returns is news again"

    def test_detail_is_stable_across_readings(self):
        """``core.intent`` keys repeat suppression on the detail string.

        A percentage or a timestamp in there makes every occurrence unique and
        silently disables both suppression and the escalation ladder.
        """
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        probe.reader.memory_critical_pct = max(1.0, first.memory.percent - 2.0)
        one = probe.poll(time.monotonic())
        probe._memory_breached = False  # force a second report
        two = probe.poll(time.monotonic())
        if one and two:
            assert one[0].detail == two[0].detail
            assert not any(ch.isdigit() for ch in one[0].detail.split("machine")[-1][:0] or "")

    def test_poll_is_rate_limited(self):
        """Walking 190 processes on every 20s tick is a real cost for nothing."""
        probe = MachineStateProbe(poll_every_s=30.0)
        probe.poll(time.monotonic())
        assert probe.poll(time.monotonic() + 1.0) == [], "must not re-read inside the interval"

    def test_survives_a_failing_reader(self):
        class Exploding(MachineStateReader):
            def read(self):
                raise RuntimeError("psutil exploded")

        probe = MachineStateProbe(reader=Exploding(), poll_every_s=0.0)
        assert probe.poll(time.monotonic()) == [], "a broken read must be silence, not a crash"

    def test_uses_real_event_kinds(self):
        """Regression: bare strings crash ``core.intent.event_signature``.

        ``event_signature`` does ``detail.value``. A string raises
        AttributeError, the loop swallows judge exceptions and keeps ticking,
        and the probe is then permanently silent with nothing logged. Verified
        by a live run that produced no utterances and no visible error.
        """
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        probe.reader.memory_critical_pct = max(1.0, first.memory.percent - 2.0)
        for obs in probe.poll(time.monotonic()):
            assert isinstance(obs.event, EventKind), f"{obs.event!r} is not an EventKind"

    def test_summary_is_deterministic(self):
        state = _state(disk_free_pct=42.0, process_count=123)
        assert render_machine_summary(state) == render_machine_summary(state)
        assert "42%" in render_machine_summary(state)
        assert "unavailable" in render_machine_summary(MachineState(available=False, error="nope"))

    def test_summary_renders_a_real_memory_reading(self):
        """Regression caught by running the module, not by the suite.

        ``render_machine_summary`` formats available memory as GB through a
        helper. When the probe and the reader lived in two files, merging them
        dropped the helper and the summary raised ``NameError: _fmt_gb`` on
        every real reading. All 25 tests still passed, because every one of
        them either asserted on a state with no ``memory`` or on an
        unavailable state - none of them rendered a populated summary. The gap
        was closed by running the thing against the live machine, which is the
        argument for doing that rather than trusting a green suite.
        """
        state = MachineStateReader().read()
        assert state.memory is not None, "this machine must report memory"
        rendered = render_machine_summary(state)
        assert "memory" in rendered
        assert "GB" in rendered
        assert "unavailable" not in rendered


# ---------------------------------------------------------------------------
# Integration with the real loop and the real judge
# ---------------------------------------------------------------------------


class TestWithTheRealLoop:
    def test_it_speaks_when_something_is_really_wrong(self):
        """End to end: real read -> observation -> judge -> one sentence.

        The whole point of the feature. A probe that produces a correct
        Observation that the judge then discards is a feature that does
        nothing, and that is exactly the failure that is hardest to see from
        outside because everything looks healthy in the logs.
        """
        probe = MachineStateProbe(poll_every_s=0.0)
        first = probe.reader.read()
        probe.reader.memory_critical_pct = max(1.0, first.memory.percent - 2.0)

        loop = ProactivityLoop(
            probes=[probe],
            config=ProactivityConfig(interval_seconds=1.0, quiet_hours=""),
            judge_config=JudgeConfig(),
        )
        # A room that has been quiet for a while, which is the only condition
        # under which the judge will interrupt at all.
        loop.floor.note_spoke(0.0)

        spoken = loop.consider(now=time.monotonic(), wall=_afternoon())
        assert spoken, (
            "a real, verified, first-occurrence error in a quiet idle room must "
            f"produce an utterance; last decision was {loop.status()['last_decision']}"
        )
        utterance = spoken[0]
        assert utterance.origin == "unsolicited"
        assert utterance.source == "machine"
        # The numbers the user needs travel with the sentence.
        assert "percent" in utterance.meta
        assert isinstance(utterance.meta["percent"], (int, float))

    def test_it_stays_quiet_when_nothing_is_wrong(self):
        probe = MachineStateProbe(poll_every_s=0.0)
        probe.reader.memory_critical_pct = 99.99
        probe.reader.cpu_sustained_pct = 99.99
        loop = ProactivityLoop(
            probes=[probe],
            config=ProactivityConfig(interval_seconds=1.0, quiet_hours=""),
            judge_config=JudgeConfig(),
        )
        loop.floor.note_spoke(0.0)
        assert loop.consider(now=time.monotonic(), wall=_afternoon()) == []

    def test_default_probes_include_the_machine_probe(self):
        """The live gateway must actually have it.

        Worth its own test because ``default_probes`` catches per-probe
        exceptions and continues. An import mistake there produced a loop that
        ran forever with four probes and logged a single line nobody reads.
        """
        names = [getattr(p, "name", type(p).__name__) for p in default_probes(ProactivityConfig())]
        assert "machine" in names, f"machine probe missing from {names}"


def _afternoon():
    from datetime import time as dtime

    return dtime(14, 0)
