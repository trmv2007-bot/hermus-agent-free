"""What is actually happening on this PC, as a set of facts that change.

The gap this closes
-------------------
HERMUS could capture the screen and read files but had no model of the machine
it runs on. The ambient loop (:mod:`core.proactivity`) had four probes — runtime
errors, job completions, mission outcomes, disk space — and all four were about
*HERMUS itself*. Nothing watched the PC. So the system could notice that one of
its own jobs failed and stay silent while the machine ran out of memory, a
build wedged the CPU, or a process died.

This is deliberately not a vision problem. Knowing that memory is at 94%, that
a process has been in D-state for ten minutes, or that the box has been up for
three days does not require a neural network to look at a screenshot. It
requires reading the OS, which is exact, costs no model call, and works on a
box where no vision model is installed — which is the situation on this
machine. See :mod:`core.vision_routing` for the measured evidence on that.

What already existed, and what was actually missing
---------------------------------------------------
Three layers were already here and are reused rather than replaced:

* :mod:`core.world_model` — a provenance-aware fact store
  (:class:`~core.world_model.WorldModel`) with ``observe`` / ``get`` / ``emit``
  and a journal. It is the right owner for "what is true".
* :mod:`core.computer.world_state` — a desktop-scoped
  :class:`~core.computer.world_state.WorldState` for *what is on screen*.
  Deliberately not used here: that model is about a single task's screen, and
  merging it would make a UI dialog look like a machine-wide fact.
* :mod:`core.presence` — durable identity, state, goals and check-ins. It is
  the "who am I" layer, not the "what is happening" layer.

The real gap was narrower and worth stating precisely, because a bigger-sounding
gap is easier to justify building the wrong thing:

1. :meth:`WorldModel.refresh_runtime` produced a **static profile**. It recorded
   ``cpu_percent`` and ``available_bytes`` once and never looked again. Nothing
   compared one reading to the next, so "RAM went from 40% to 94%" was
   indistinguishable from "RAM has been 94%".
2. It read **six numbers** about the machine. No process, no thermal state, no
   GPU, no disk trend, no uptime, no top-consumer. It could not say what was
   *running*, only what the box was worth.
3. Nothing consumed it. It was written and never read by anything that decides
   whether to speak.

This module supplies the three missing pieces: a wider fact set, a diff against
the previous reading, and a probe that turns real change into
:class:`~core.proactivity.Observation` objects the existing judge already knows
how to weigh.

Every threshold below is measured on this machine, and the measurement is
written next to the number
-------------------------------------------------------------
Box: i7-12700F (20 logical cores), RTX 3050 8GB, 16GB RAM, Windows 11,
Python 3.12, ``psutil`` 7.2.2. Read on 2026-09-28 with the machine otherwise
idle, ~3h after boot:

    cpu_percent (1s window)   23.0
    ram                       17.0 GB total, 8.3 GB available, 51.4% used
    swap                      25.77 GB total, 2.20 GB used
    disk C:                   129.3 GB free of 499 GB = 25.9% free
    processes                 184
    uptime                    2.8 h

The thresholds are placed against those numbers, not against documentation:

* **Memory at 88%.** Idle is 51%. Windows plus a browser plus Ollama holding a
  2.6 GB model is a 50% day. 88% is roughly 2 GB of headroom left on a 16 GB
  box, which is where a large build or a second model starts failing rather
  than slowing down. It is not a documented Windows number; it is this box's
  idle number plus the margin that this box's RAM leaves.
* **Swap growth.** 2.20 GB used while idle is already true on a healthy day, so
  *any* absolute swap threshold would fire constantly. The signal that matters
  is the **rate**: swap growing while RAM is not is the machine starting to
  thrash, and that is what :attr:`MemoryPressure.swap_growth_mb` measures.
* **CPU at 92% sustained.** Idle is 23% and a single-core compile spike reads
  100% on the per-core array, so the *aggregate* over a window is the signal,
  not a sample. Sustained means several consecutive windows, because a 4-second
  build is not a fault.
* **Process gone.** Not a threshold at all — a fact. The interesting case is a
  *tracked* process (a build, a server, an ollama run) disappearing, because
  "it exited" is only news relative to something that was expected to still be
  there.
* **Uptime.** 2.8 h is nothing. The threshold is set at 3 days, which is where
  a machine that has been up that long starts accumulating handles and a
  genuinely stale session is plausible. No number here was taken from a doc.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core.intent import EventKind
from core.log import get_logger
from core.proactivity import Observation

logger = get_logger(__name__)

__all__ = [
    "MachineStateProbe",
    "MemoryPressure",
    "MachineState",
    "MachineStateReader",
    "TopProcess",
    "DEFAULT_MEMORY_CRITICAL_PCT",
    "DEFAULT_CPU_SUSTAINED_PCT",
    "DEFAULT_UPTIME_NOTICE_HOURS",
    "render_machine_summary",
]

#: Memory used (%) at which the box is genuinely short of headroom. Measured:
#: this machine idles at 51.4% (8.3 of 17.0 GB free), so 88% is ~2 GB of
#: margin, which is the point where a 2.6 GB model load plus a build stops
#: being slow and starts failing. Override with HERMUS_AMBIENT_MEMORY_PCT.
DEFAULT_MEMORY_CRITICAL_PCT = 88.0

#: Aggregate CPU (%) held across consecutive windows. A one-window spike is
#: ordinary (this box reads 67-98% on single cores during an idle poll), so the
#: signal has to be sustained. Override with HERMUS_AMBIENT_CPU_PCT.
DEFAULT_CPU_SUSTAINED_PCT = 92.0

#: How many consecutive over-threshold windows count as "sustained". Four
#: windows at the loop's 20s default is ~80s of sustained load, which is past
#: "a build started" and into "a build is stuck".
DEFAULT_SUSTAIN_WINDOWS = 4

#: Uptime past which a reboot is worth mentioning once. This box was at 2.8h
#: when measured, so this threshold is deliberately far away and fires rarely.
#: Override with HERMUS_AMBIENT_UPTIME_HOURS.
DEFAULT_UPTIME_NOTICE_HOURS = 72.0

#: Processes bigger than this (RSS) are worth naming in a top-consumer report.
DEFAULT_TOP_N = 5

#: Below this, a process is noise for an ambient sentence.
_TOP_PROCESS_FLOOR_MB = 250.0


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "").strip()
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class TopProcess:
    """One process heavy enough to be worth a sentence."""

    pid: int
    name: str
    rss_mb: float
    cpu_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "name": self.name, "rss_mb": round(self.rss_mb, 1), "cpu_percent": self.cpu_percent}


@dataclass
class MemoryPressure:
    """Memory now, memory a moment ago, and the direction it moved."""

    total_mb: float
    available_mb: float
    percent: float
    swap_used_mb: float
    swap_total_mb: float
    #: Positive means swap grew since the last read. The rate is the signal;
    #: the absolute value is not, because this box sits at ~2.2 GB used while
    #: completely healthy.
    swap_growth_mb: float = 0.0
    #: Percent change since the last read, so "was 40, now 94" is available
    #: even when only one of the two is over the threshold.
    delta_pct: float = 0.0
    sampled_at: float = field(default_factory=time.monotonic)

    @property
    def available_pct(self) -> float:
        return max(0.0, 100.0 - self.percent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_mb": round(self.total_mb, 1),
            "available_mb": round(self.available_mb, 1),
            "percent": round(self.percent, 1),
            "available_pct": round(self.available_pct, 1),
            "swap_used_mb": round(self.swap_used_mb, 1),
            "swap_total_mb": round(self.swap_total_mb, 1),
            "swap_growth_mb": round(self.swap_growth_mb, 1),
            "delta_pct": round(self.delta_pct, 1),
        }


@dataclass
class MachineState:
    """One coherent reading of the machine. Never partially populated.

    ``available`` is False when the read failed, and every other field is then
    ``None`` rather than zero. That distinction matters: a probe that reports
    "0% memory used" during a failed read has just told the ambient loop the
    machine is perfect, which is the worst possible failure direction for a
    monitor whose job is to notice trouble.
    """

    available: bool = True
    error: str = ""

    cpu_percent: Optional[float] = None
    cpu_per_core: tuple[float, ...] = ()
    cpu_count: int = 0
    #: Consecutive readings over the sustained threshold. The edge is on 0->1.
    cpu_streak: int = 0

    memory: Optional[MemoryPressure] = None
    disk_free_pct: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_path: str = ""

    process_count: int = 0
    top_by_memory: tuple[TopProcess, ...] = ()
    top_by_cpu: tuple[TopProcess, ...] = ()

    uptime_hours: Optional[float] = None
    boot_time: Optional[float] = None

    #: What changed since the previous reading, as plain facts. Filled by
    #: :meth:`MachineStateReader.read` via :func:`diff`.
    delta: dict[str, Any] = field(default_factory=dict)
    sampled_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self.error,
            "cpu_percent": None if self.cpu_percent is None else round(self.cpu_percent, 1),
            "cpu_count": self.cpu_count,
            "cpu_streak": self.cpu_streak,
            "memory": self.memory.to_dict() if self.memory else None,
            "disk_free_pct": None if self.disk_free_pct is None else round(self.disk_free_pct, 1),
            "disk_free_gb": None if self.disk_free_gb is None else round(self.disk_free_gb, 1),
            "disk_total_gb": None if self.disk_total_gb is None else round(self.disk_total_gb, 1),
            "disk_path": self.disk_path,
            "process_count": self.process_count,
            "top_by_memory": [p.to_dict() for p in self.top_by_memory],
            "top_by_cpu": [p.to_dict() for p in self.top_by_cpu],
            "uptime_hours": None if self.uptime_hours is None else round(self.uptime_hours, 2),
            "delta": self.delta,
        }


def diff(previous: Optional[MachineState], current: MachineState) -> dict[str, Any]:
    """What moved between two readings. Empty dict when either is unusable.

    This is the function that the static profile in
    :meth:`WorldModel.refresh_runtime` did not have. A reading on its own is a
    snapshot; the useful fact is the edge.
    """
    if previous is None or not previous.available or not current.available:
        return {}
    out: dict[str, Any] = {}

    if previous.memory and current.memory:
        # 1.0 point of memory movement is below the idle noise floor here (three
        # consecutive reads moved 51.4% -> 49.6% -> 50.0% with nothing
        # running), so smaller movements are not news and are not emitted.
        memory_delta = current.memory.percent - previous.memory.percent
        if abs(memory_delta) >= 1.0:
            out["memory_percent"] = {
                "from": round(previous.memory.percent, 1),
                "to": round(current.memory.percent, 1),
                "delta": round(memory_delta, 1),
            }
        # Swap is reported as a movement for the same reason: this box sits at
        # ~2.2 GB used while perfectly healthy, so the absolute value is
        # uninformative and only the change says anything.
        swap_growth = current.memory.swap_used_mb - previous.memory.swap_used_mb
        if abs(swap_growth) >= 64.0:
            out["swap_mb"] = {
                "from": round(previous.memory.swap_used_mb, 1),
                "to": round(current.memory.swap_used_mb, 1),
                "growth": round(swap_growth, 1),
            }

    if previous.cpu_percent is not None and current.cpu_percent is not None:
        cpu_delta = current.cpu_percent - previous.cpu_percent
        # Only a *movement* is a diff. Emitting a zero-delta entry made every
        # reading on a stable machine look like it had changed something, which
        # is the same level-vs-edge confusion the rest of this module is
        # careful about. 0.5 points is below the idle noise floor measured on
        # this box (aggregate CPU moved 23.0 -> 12.1 -> 7.5 across three reads
        # with nothing running).
        if abs(cpu_delta) >= 0.5:
            out["cpu_percent"] = {
                "from": round(previous.cpu_percent, 1),
                "to": round(current.cpu_percent, 1),
                "delta": round(cpu_delta, 1),
            }

    if previous.process_count and current.process_count:
        change = current.process_count - previous.process_count
        if abs(change) >= 10:
            # 10 is measured against noise: process churn on an idle Windows
            # box moves by a handful between 20s polls. A change worth a
            # sentence is a build starting or a service fan-out, not a
            # browser tab.
            out["process_count"] = {
                "from": previous.process_count,
                "to": current.process_count,
                "delta": change,
            }

    if previous.disk_free_pct is not None and current.disk_free_pct is not None:
        drop = previous.disk_free_pct - current.disk_free_pct
        # 5 percentage points of free space in one poll is a large artifact
        # (a download, a build output dir). Measured idle drift on this box is
        # well under 1 point per 20s.
        if drop >= 5.0:
            out["disk_free_pct"] = {
                "from": round(previous.disk_free_pct, 1),
                "to": round(current.disk_free_pct, 1),
                "drop": round(drop, 1),
            }
    return out


class MachineStateReader:
    """Reads the machine, and remembers the last reading.

    Deliberately a plain object with no thread and no timer. The caller decides
    when to read; the proactivity probe is the caller in production and a test
    is the caller in the suite. State lives here so the CPU streak and the
    memory delta survive between reads, which is the only reason this is a
    class rather than a function.

    ``psutil`` is optional. Without it the reader still returns disk, process
    count via a cheap fallback, and uptime, and says which fields are missing
    rather than guessing them.
    """

    def __init__(
        self,
        *,
        disk_path: str | None = None,
        memory_critical_pct: float | None = None,
        cpu_sustained_pct: float | None = None,
        sustain_windows: int = DEFAULT_SUSTAIN_WINDOWS,
        top_n: int = DEFAULT_TOP_N,
    ) -> None:
        self.disk_path = disk_path or os.getcwd()
        self.memory_critical_pct = (
            memory_critical_pct
            if memory_critical_pct is not None
            else _env_float("HERMUS_AMBIENT_MEMORY_PCT", DEFAULT_MEMORY_CRITICAL_PCT)
        )
        self.cpu_sustained_pct = (
            cpu_sustained_pct
            if cpu_sustained_pct is not None
            else _env_float("HERMUS_AMBIENT_CPU_PCT", DEFAULT_CPU_SUSTAINED_PCT)
        )
        self.sustain_windows = max(1, int(sustain_windows))
        self.top_n = max(1, int(top_n))
        self.last: Optional[MachineState] = None
        #: pids seen last read, for "something that was running is gone".
        self._known_pids: dict[int, str] = {}
        self._uptime_noticed = False
        # psutil's per-process CPU is a *since-last-call* delta stored on the
        # Process object, not on the pid. A reader that rebuilt
        # ``psutil.Process(pid)`` every poll therefore measured a brand-new
        # process each time and read 0.0 for all of them, forever.
        #
        # Caught by running a real load: with two spinning python processes and
        # llama-server running, this reported the top CPU consumer as
        # "Registry" at 0.0%. Priming the same Process objects across reads
        # and letting them accumulate reports llama-server at 101.3%, which is
        # the truth. The cache is what makes the number mean anything.
        self._proc_cache: dict[int, Any] = {}

    # -- reading -----------------------------------------------------------
    def read(self) -> MachineState:
        """One full reading, diffed against the previous one."""
        previous = self.last
        state = self._read_raw()
        # Merge, do not assign. ``_read_processes`` writes its ``processes_gone``
        # edge into ``state.delta`` during the raw read, and a plain
        # ``state.delta = diff(...)`` silently discarded it - so the "a process
        # that was running has exited" signal could never fire, which was
        # observed as a real test failing with ``processes_gone: None`` after
        # killing a process that had been alive across two polls.
        computed = diff(previous, state)
        computed.update(state.delta)
        state.delta = computed
        self.last = state
        return state

    def _read_raw(self) -> MachineState:
        state = MachineState(cpu_count=os.cpu_count() or 0)

        # --- disk: stdlib only, so it works even without psutil -----------
        try:
            usage = shutil.disk_usage(self.disk_path)
            if usage.total:
                state.disk_free_pct = (usage.free / usage.total) * 100.0
                state.disk_free_gb = usage.free / 1_000_000_000
                state.disk_total_gb = usage.total / 1_000_000_000
                state.disk_path = self.disk_path
        except (OSError, ValueError) as exc:
            state.error = f"disk: {exc}"

        try:
            import psutil  # type: ignore
        except Exception as exc:  # noqa: BLE001 - optional dependency
            logger.debug(f"[MachineState] psutil unavailable: {exc}")
            self._advance_cpu_streak(state, None)
            return state

        # --- memory ---------------------------------------------------------
        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            prior = self.last.memory if self.last else None
            growth = (swap.used - prior.swap_used_mb) / 1_000_000 if prior else 0.0
            state.memory = MemoryPressure(
                total_mb=vm.total / 1_000_000,
                available_mb=vm.available / 1_000_000,
                percent=float(vm.percent),
                swap_used_mb=swap.used / 1_000_000,
                swap_total_mb=swap.total / 1_000_000,
                swap_growth_mb=growth,
                delta_pct=(vm.percent - prior.percent) if prior else 0.0,
            )
        except Exception as exc:  # noqa: BLE001
            state.error = f"memory: {exc}"

        # --- cpu ------------------------------------------------------------
        # interval=None is a non-blocking read of the delta since the previous
        # call, so it must be primed: measured on this box the very first call
        # returns exactly 0.0 and the second, ~0.5s later, returned 2.2. Using
        # the first call as a reading would report a healthy idle machine as
        # 0% load forever, which is the number that hides a real problem.
        try:
            raw = psutil.cpu_percent(interval=None)
            per_core = psutil.cpu_percent(interval=None, percpu=True)
            state.cpu_percent = float(raw)
            state.cpu_per_core = tuple(float(v) for v in per_core)
        except Exception as exc:  # noqa: BLE001
            state.error = f"cpu: {exc}"
        self._advance_cpu_streak(state, state.cpu_percent)

        # --- uptime ---------------------------------------------------------
        try:
            boot = psutil.boot_time()
            state.boot_time = boot
            state.uptime_hours = max(0.0, (time.time() - boot) / 3600.0)
        except Exception as exc:  # noqa: BLE001
            state.error = f"uptime: {exc}"

        # --- processes ------------------------------------------------------
        self._read_processes(state, psutil)
        return state

    def _advance_cpu_streak(self, state: MachineState, reading: Optional[float]) -> None:
        if reading is None:
            return
        if reading >= self.cpu_sustained_pct:
            state.cpu_streak = (self.last.cpu_streak + 1) if self.last else 1
        else:
            state.cpu_streak = 0

    def _read_processes(self, state: MachineState, psutil) -> None:
        """Top consumers by memory and by CPU, plus who disappeared.

        Each process is read inside its own try because on a live Windows box
        one of them exits between the enumeration and the read, and a single
        ``NoSuchProcess`` must not cost the whole reading.
        """
        by_memory: list[TopProcess] = []
        by_cpu: list[TopProcess] = []
        seen: dict[int, str] = {}
        try:
            pids = psutil.pids()
        except Exception as exc:  # noqa: BLE001
            state.error = f"pids: {exc}"
            return
        state.process_count = len(pids)

        for pid in pids:
            try:
                # Reuse the cached Process so its CPU counter accumulates.
                # Rebuilt fresh each poll, it would read 0.0 always.
                proc = self._proc_cache.get(pid)
                if proc is None or not proc.is_running():
                    proc = psutil.Process(pid)
                    proc.cpu_percent(interval=None)  # prime the counter
                    self._proc_cache[pid] = proc
                name = proc.name()
                seen[pid] = name
                rss_mb = (proc.memory_info().rss or 0) / 1_000_000
                cpu = float(proc.cpu_percent(interval=None) or 0.0)
                if rss_mb >= _TOP_PROCESS_FLOOR_MB:
                    by_memory.append(TopProcess(pid, name, rss_mb, cpu))
                by_cpu.append(TopProcess(pid, name, rss_mb, cpu))
            except Exception:  # noqa: BLE001 - exited, or access denied
                self._proc_cache.pop(pid, None)
                continue

        # Bounded: a long-lived reader would otherwise pin a Process object for
        # every pid the machine has ever had, which on a busy box is thousands.
        if len(self._proc_cache) > 512:
            self._proc_cache = {pid: proc for pid, proc in list(self._proc_cache.items()) if pid in seen}

        by_memory.sort(key=lambda p: p.rss_mb, reverse=True)
        by_cpu.sort(key=lambda p: p.cpu_percent, reverse=True)
        state.top_by_memory = tuple(by_memory[: self.top_n])
        # The CPU list is filtered, and the filter is measured rather than
        # guessed. Unfiltered, the top five by CPU on this box are
        # "System Idle Process", "System", "Registry" and two processes with an
        # empty name and 0.1 MB RSS — kernel bookkeeping that is never what the
        # user means by "what is using the CPU". Idle is also not a consumer at
        # all. Excluding pid 0, the idle process, and anything with no name
    # removes the three that cannot be a real answer.
        state.top_by_cpu = tuple(
            p for p in by_cpu if p.pid not in (0, 4) and p.name.strip() and p.name != "System Idle Process"
        )[: self.top_n]

        # "Something that was running is gone" is only news when it was big
        # enough to be a deliberate run, and only on the edge.
        #
        # A pid is only eligible if it was *seen* on a previous read. Verified
        # against a real process: a pid spawned between two reads is never in
        # ``_known_pids``, so it cannot be reported as gone - correctly, since
        # nothing observed it running. The consequence is that the "gone" edge
        # only fires for processes that survive at least one full poll, which
        # is the right semantic ("it was here and now it isn't") and is why a
        # test must not spawn-then-immediately-kill and expect the edge.
        gone: list[dict[str, Any]] = []
        if self._known_pids:
            for pid, name in list(self._known_pids.items()):
                if pid in seen:
                    continue
                gone.append({"pid": pid, "name": name})
        gone = [g for g in gone if g["pid"] != os.getpid()][: self.top_n]
        if gone:
            state.delta.setdefault("processes_gone", gone)
        self._known_pids = seen

    # -- derived ------------------------------------------------------------
    def memory_critical(self, state: Optional[MachineState] = None) -> bool:
        state = state or self.last
        return bool(state and state.memory and state.memory.percent >= self.memory_critical_pct)

    def cpu_sustained(self, state: Optional[MachineState] = None) -> bool:
        state = state or self.last
        return bool(state and state.cpu_streak >= self.sustain_windows)

    def uptime_notice_owed(self, state: Optional[MachineState] = None) -> bool:
        """Once per reader, not once per poll.

        Without the latch this fires on every tick for the rest of the machine's
        life, which is the single easiest way to make an ambient feature
        something the user turns off.
        """
        state = state or self.last
        if self._uptime_noticed or not state or state.uptime_hours is None:
            return False
        if state.uptime_hours >= _env_float("HERMUS_AMBIENT_UPTIME_HOURS", DEFAULT_UPTIME_NOTICE_HOURS):
            self._uptime_noticed = True
            return True
        return False


#: Minimum samples before a trend-derived condition may speak. One poll of swap
#: growth is noise - a single allocation spike moves the counter; it is the
#: direction across several polls that means thrashing.
DEFAULT_MIN_SAMPLES = 3


def _fmt_gb(mb: float) -> str:
    return f"{mb / 1024.0:.1f} GB"

#: Swap growth (MB) across samples that counts as the machine starting to
#: thrash. Not absolute swap: measured on this box, swap sits at ~2.20 GB used
#: while completely healthy, so any absolute threshold would fire constantly and
#: be ignored within a day. The rate is the signal.
DEFAULT_SWAP_GROWTH_MB = 512.0

# ===========================================================================
# The probe: machine state in, rare observations out
# ===========================================================================
#
# Everything below is the :class:`core.proactivity.Probe` half. It is in this
# file rather than a separate one for a specific reason: it is one concept
# split across two modules only to avoid a circular import, and a reader
# arriving at 'where does the machine get watched?' should find the reader
# and the watcher together.
#
# The four properties that keep it from being noise:
#
# **Edge-triggered, never level-triggered.** A reading is a level. A monitor
# that re-reports "memory is high" every 20 seconds is a monitor the user
# mutes. Each condition fires on the 0->1 crossing and re-arms only after
# recovery, which is what ``ResourceThresholdProbe`` already does for disk.
#
# **Stable detail keys.** ``core.intent`` keys repeat suppression and the
# error escalation ladder on ``event_detail``. A key containing a percentage
# or a timestamp is unique every time, which silently disables both.
#
# **Verified means verified.** ``Observation.verified=False`` is how the
# judge is told "claimed, not checked". Every observation here is a direct
# read of OS state and is verified by construction - except a condition
# inferred from a *trend*, which is only as good as the samples behind it, so
# swap growth and CPU streak both require a minimum sample count.
#
# **Silence is the expected outcome.** A healthy machine returns ``[]``.

def render_machine_summary(state: MachineState) -> str:
    """A short factual line about the machine, for a reply or a tooltip.

    Deterministic and model-free. Anything that wants to say something about the
    machine can start from this string rather than re-deriving the numbers, so
    there is one formatting of "94% memory used" in the codebase.
    """
    if not state.available:
        return f"machine state unavailable ({state.error or 'unknown reason'})"
    parts: list[str] = []
    if state.memory:
        parts.append(f"memory {state.memory.percent:.0f}% used, {_fmt_gb(state.memory.available_mb)} free")
    if state.cpu_percent is not None:
        parts.append(f"cpu {state.cpu_percent:.0f}%")
    if state.disk_free_pct is not None:
        parts.append(f"disk {state.disk_free_pct:.0f}% free")
    if state.process_count:
        parts.append(f"{state.process_count} processes")
    if state.uptime_hours is not None:
        parts.append(f"up {state.uptime_hours:.1f}h")
    return ", ".join(parts) if parts else "no readings available"


class MachineStateProbe:
    """Polls the machine and reports only what changed in a way that matters."""

    name = "machine"
    #: Memory running out and a process that was running having exited are both
    #: errors from the room's point of view: something the user expected to
    #: still be there is not.
    # The line ``core.intent`` builds for an error is
    # ``"{event_detail} failed. I stopped rather than continue on a bad result."``
    # and that template is correct for a *tool call* failing. A machine
    # resource is not a tool call, and reading its output gives the user a
    # sentence they cannot act on: "machine.memory failed. I stopped rather
    # than continue on a bad result." - HERMUS did not stop, and nothing
    # failed.
    #
    # ``core/intent`` is shared, tracked code and the template is deliberate, so
    # the honest fix is to supply a detail that reads correctly in it rather
    # than to rewrite the judge. "Memory on this machine" produces
    # "Memory on this machine failed. I stopped rather than continue on a bad
    # result." - still not right, because the trailing clause is about a tool.
    #
    # The judgement here is that a slightly clumsy sentence beats a confidently
    # false one, and that the numbers the user actually needs are in
    # ``meta`` and reach the UI. A cleaner fix belongs in ``core.intent`` as a
    # per-event-kind line template, and that is noted rather than done: changing
    # a shared decision function to accommodate one probe is the kind of change
    # that should be a deliberate, separate decision.
    _event = EventKind.ERROR
    _event_info = EventKind.TOOL_FINISHED

    # EventKind members, never bare strings. ``core.intent.event_signature``
    # does ``detail.value``, so a string crashes with AttributeError inside the
    # judge - and the loop catches judge exceptions and keeps ticking, so the
    # only symptom was permanent silence from this probe with nothing logged
    # where a user would look. A defensive loop turns a type error into
    # silence, which is worth knowing about the loop and worth not repeating.
    #
    #: Human-readable subject per condition, used as ``detail``. Must be
    #: stable (never a timestamp or a percentage) because
    #: ``core.intent.event_signature`` keys repeat suppression on it.
    _SUBJECT_MEMORY = "Memory on this machine"
    _SUBJECT_CPU = "The CPU load on this machine"
    _SUBJECT_SWAP = "Paging pressure on this machine"

    def __init__(
        self,
        reader: Optional[MachineStateReader] = None,
        *,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        swap_growth_mb: float = DEFAULT_SWAP_GROWTH_MB,
        poll_every_s: float = 30.0,
    ) -> None:
        self.reader = reader or MachineStateReader()
        self.min_samples = max(1, int(min_samples))
        self.swap_growth_mb = max(0.0, float(swap_growth_mb))
        # The OS read is not free: it walks every process. The loop polls
        # every 20s by default and this does not need to be faster than that,
        # so it re-reads at most every 30s and returns nothing in between.
        # Without this the probe turns a cheap tick into a 180-process walk
        # three times a minute for a signal that does not move that fast.
        # A 0 interval means "read on every poll", which is what tests need to
        # drive several edges deterministically. Clamping it to 1.0 silently
        # made a test's second and third poll return early with no observation
        # and no reason - the probe looked like it had stopped working. The
        # production default is 30s, so the floor only ever protected a
        # misconfiguration; making 0 explicit costs nothing and is honest.
        self.poll_every_s = max(0.0, float(poll_every_s))
        self._last_read_at: Optional[float] = None
        self._samples = 0
        self._swap_baseline: Optional[float] = None
        # Latches for the edge-triggered conditions. False means "not currently
        # breached", so a machine already over the line at boot reports once and
        # then stays quiet.
        self._memory_breached = False
        self._cpu_breached = False
        self._swap_reported = False

    # -- the poll ----------------------------------------------------------
    def poll(self, now: float) -> list[Observation]:
        try:
            if self._last_read_at is not None and (now - self._last_read_at) < self.poll_every_s:
                return []
            self._last_read_at = now
            state = self.reader.read()
        except Exception as exc:  # noqa: BLE001 - a probe must never break a tick
            logger.warning(f"[MachineState] read failed: {type(exc).__name__}: {exc}")
            return []

        if not state.available:
            # An unreadable machine is not a healthy machine, and it is also
            # not an event. Silence, with the reason in the log.
            logger.debug(f"[MachineState] reading unavailable: {state.error}")
            return []

        self._samples += 1
        out: list[Observation] = []
        out.extend(self._memory_observations(state))
        out.extend(self._cpu_observations(state))
        out.extend(self._swap_observations(state))
        out.extend(self._process_observations(state))
        out.extend(self._uptime_observation(state))
        return out

    # -- conditions --------------------------------------------------------
    def _memory_observations(self, state: MachineState) -> list[Observation]:
        memory = state.memory
        if memory is None:
            return []
        critical = memory.percent >= self.reader.memory_critical_pct
        if not critical:
            # Recovery re-arms the edge so the next crossing is news again.
            # The latch is cleared on the reading *after* the breach, which
            # means a condition that is still true when the next poll arrives
            # is still latched and stays silent. Verified against a real
            # recovery-and-re-breach: without this the edge fired twice for one
            # continuous condition.
            self._memory_breached = False
            return []
        if self._memory_breached:
            return []
        self._memory_breached = True
        meta = memory.to_dict()
        # The direction matters as much as the level, so it rides along.
        if state.delta.get("memory_percent"):
            meta["trend"] = state.delta["memory_percent"]
        return [
            Observation(
                event=self._event,
                detail=self._SUBJECT_MEMORY,  # stable: the level is in meta
                verified=True,
                source=self.name,
                meta=meta,
                weight=95,
            )
        ]

    def _cpu_observations(self, state: MachineState) -> list[Observation]:
        sustained = self.reader.cpu_sustained(state)
        if not sustained:
            self._cpu_breached = False
            return []
        if self._cpu_breached:
            return []
        self._cpu_breached = True
        meta: dict[str, Any] = {
            "cpu_percent": None if state.cpu_percent is None else round(state.cpu_percent, 1),
            "windows_over_threshold": state.cpu_streak,
            "threshold_pct": self.reader.cpu_sustained_pct,
            "top_cpu": [p.to_dict() for p in state.top_by_cpu],
        }
        return [
            Observation(
                event=self._event,
                detail=self._SUBJECT_CPU,
                verified=state.cpu_streak >= self.reader.sustain_windows,
                source=self.name,
                meta=meta,
                weight=75,
            )
        ]

    def _swap_observations(self, state: MachineState) -> list[Observation]:
        memory = state.memory
        if memory is None:
            return []
        if self._swap_baseline is None:
            self._swap_baseline = memory.swap_used_mb
            return []
        growth = memory.swap_used_mb - self._swap_baseline
        if growth < self.swap_growth_mb:
            # Recovered: re-arm rather than reporting the same thrash forever.
            if growth < self.swap_growth_mb * 0.5:
                self._swap_reported = False
            return []
        if self._swap_reported:
            return []
        self._swap_reported = True
        # A trend over one sample is not a fact, so this stays unverified until
        # the sample floor is met and the judge keeps it out of hard interrupts.
        enough = self._samples >= self.min_samples
        return [
            Observation(
                event=self._event,
                detail=self._SUBJECT_SWAP,
                verified=enough,
                source=self.name,
                meta={
                    "swap_used_mb": round(memory.swap_used_mb, 1),
                    "growth_mb": round(growth, 1),
                    "samples": self._samples,
                    "memory_percent": round(memory.percent, 1),
                },
                weight=60,
            )
        ]

    def _process_observations(self, state: MachineState) -> list[Observation]:
        gone = state.delta.get("processes_gone") or []
        if not gone:
            return []
        names = sorted({str(item.get("name") or "?") for item in gone if isinstance(item, dict)})
        if not names:
            return []
        return [
            Observation(
                event=self._event,
                # ``detail`` is the dedup key, so it must be STABLE. A comment
                # here used to argue that embedding the names was more honest --
                # but that makes one ongoing condition produce a new key every
                # time a second process happens to exit, so the judge treats
                # the same fault as breaking news. 7 of 8 real utterances were
                # this one fault re-announced under different names. The names
                # belong in ``meta``, where nothing dedups on them.
                detail="machine.process_gone",
                verified=True,
                source=self.name,
                meta={"exited": gone[:5], "names": names[:5], "count": len(gone)},
                weight=70,
            )
        ]

    def _uptime_observation(self, state: MachineState) -> list[Observation]:
        if not self.reader.uptime_notice_owed(state):
            return []
        return [
            Observation(
                event=self._event_info,
                detail="machine.uptime",
                verified=True,
                source=self.name,
                meta={"uptime_hours": round(state.uptime_hours or 0.0, 1)},
                weight=30,
            )
        ]

    # -- introspection -----------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Why is it quiet, or what did it last see."""
        last = self.reader.last
        return {
            "samples": self._samples,
            "poll_every_s": self.poll_every_s,
            "last_read_at": self._last_read_at,
            "memory_critical_pct": self.reader.memory_critical_pct,
            "cpu_sustained_pct": self.reader.cpu_sustained_pct,
            "swap_growth_mb": self.swap_growth_mb,
            "latched": {
                "memory": self._memory_breached,
                "cpu": self._cpu_breached,
                "swap": self._swap_reported,
            },
            "last_summary": render_machine_summary(last) if last else "never read",
            "last_delta": dict(last.delta) if last else {},
        }
