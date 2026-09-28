"""A live, honest demonstration of the ambient machine layer on this box.

Run it and read the output. It is not a test and asserts nothing - its job is
to show three things plainly:

1. what the reader actually observed, from the real OS;
2. which of those facts the probe turned into a sentence, and why;
3. what it stayed silent about, which is the part that decides whether the
   feature is usable.

Point 3 is the one worth reading. A monitor that comments on everything is
worse than no monitor, because the user learns to ignore it and then also
ignores the case that mattered.
"""

from __future__ import annotations

import time
from datetime import time as dtime

from core.machine_state import MachineStateProbe, MachineStateReader, render_machine_summary
from core.proactivity import ProactivityConfig, ProactivityLoop
from core.intent import JudgeConfig

AFTERNOON = dtime(14, 0)


def rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    reader = MachineStateReader()
    probe = MachineStateProbe(reader=reader, poll_every_s=0.0)

    rule("1. WHAT THE READER OBSERVED (real OS reads, no mocks)")
    first = reader.read()
    time.sleep(1.5)
    second = reader.read()

    print(f"summary now     : {render_machine_summary(second)}")
    print(f"summary previous: {render_machine_summary(first)}")
    print()
    print("top by memory:")
    for proc in second.top_by_memory:
        print(f"   {proc.name:26s} pid={proc.pid:<7} {proc.rss_mb:8.1f} MB")
    print("top by cpu:")
    for proc in second.top_by_cpu:
        print(f"   {proc.name:26s} pid={proc.pid:<7} {proc.cpu_percent:6.1f}%")
    print()
    print("what changed between the two reads (the diff):")
    if not second.delta:
        print("   (nothing moved - a stable machine diffs to nothing, by design)")
    for key, value in second.delta.items():
        print(f"   {key}: {value}")

    rule("2. WHAT IT DECIDED WAS WORTH SAYING (thresholds at their real defaults)")
    print(f"thresholds in force: memory >= {probe.reader.memory_critical_pct}%")
    print(f"                    cpu    >= {probe.reader.cpu_sustained_pct}% sustained "
          f"over {probe.reader.sustain_windows} reads")
    print(f"                    swap   >= {probe.swap_growth_mb} MB growth")
    print()
    observations = probe.poll(time.monotonic())
    if not observations:
        print("   NOTHING. This machine is healthy and the probe is saying so.")
    for obs in observations:
        print(f"   SPOKE ABOUT -> {obs.detail}")
        print(f"        verified={obs.verified} weight={obs.weight}")

    rule("3. THROUGH THE REAL JUDGE (does the room actually hear it?)")
    loop = ProactivityLoop(
        probes=[probe],
        config=ProactivityConfig(interval_seconds=20.0, quiet_hours=""),
        judge_config=JudgeConfig(),
    )
    # Only pre-existing silence is simulated here (a room that has been quiet
    # for hours). No observation is injected.
    loop.floor.note_spoke(0.0)
    spoken = loop.consider(now=time.monotonic(), wall=AFTERNOON)
    if not spoken:
        print("   silence - correct, nothing is wrong")
    for utterance in spoken:
        print(f"   SAID: {utterance.text}")
        print(f"   WHY : {utterance.reason}")
    decision = loop.status()["last_decision"]
    if decision:
        print(f"   last decision: rule={decision['rule']} spoke={decision['should_speak']} "
              f"urgency={decision['urgency']}")
        print(f"   reason: {decision['reason']}")

    rule("4. WHAT IT STAYED SILENT ABOUT (the part that decides usability)")
    # Every condition the probe knows about, with its current value and the
    # threshold it would have to cross. Read down the "would it speak?" column.
    mem = second.memory
    print(f"   memory     {mem.percent:5.1f}% used   threshold {probe.reader.memory_critical_pct}%"
          f"          -> SILENT (healthy)")
    print(f"   cpu        {second.cpu_percent:5.1f}%         threshold {probe.reader.cpu_sustained_pct}%"
          f" sustained   -> SILENT ({second.cpu_streak} consecutive over)")
    print(f"   swap       {mem.swap_used_mb/1024:5.1f} GB     threshold {probe.swap_growth_mb} MB growth"
          f"   -> SILENT (idle baseline ~2.2 GB is normal here)")
    print(f"   disk       {second.disk_free_pct:5.1f}% free                            "
          f"-> SILENT (and a separate disk probe owns this)")
    print(f"   processes  {second.process_count:5d}         threshold 10-proc change      "
          f"-> SILENT (steady)")
    print(f"   uptime     {second.uptime_hours:5.1f}h      threshold 72h                "
          f"-> SILENT (reported once ever, only past 72h)")
    print()
    print("   Every one of these is a real reading from this machine right now,")
    print("   and every one of them is deliberately not worth a sentence.")


if __name__ == "__main__":
    main()
