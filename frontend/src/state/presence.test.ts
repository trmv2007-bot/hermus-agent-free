import { describe, expect, it } from "vitest";
import {
  advance,
  advanceAudio,
  approach,
  breath,
  EVENT_ARC,
  EVENT_SLOTS,
  IDLE_POOL,
  initialAudio,
  initialEvents,
  initialPresence,
  isStill,
  LEVEL_FLOOR,
  makeRandom,
  motionFor,
  moveDuration,
  nextBlink,
  pickIdleMove,
  type AudioReading,
  type Presence,
  type PresenceState,
} from "./presence";
import { clearLevelSources, eventCountFor, hasLevelSource, orbStateFor, readLevel, setLevelSource, STATE_GLOW } from "./orb";

// What makes a presence read as alive is statistical, not visual: a pure
// spinner is one loop with a period you can count, and the eye finds it in a
// second. These tests assert the statistics. A test that asserted "the canvas
// called fillRect" would pass on a loading spinner.

/** Run the presence forward from a seed, sampling every step. */
function run(seed: number, states: PresenceState[], steps = 4000, dt = 16.667) {
  const rand = makeRandom(seed);
  let p = initialPresence(seed);
  const kinds: string[] = [];
  const activations: number[] = [];
  for (let i = 0; i < steps; i += 1) {
    const target = states[i % states.length];
    p = advance(p, dt, target, rand);
    if (p.move) kinds.push(p.move.kind);
    activations.push(p.activation);
  }
  return { kinds, activations, final: p };
}

describe("idle behaviour", () => {
  it("uses more than one move, so the eye cannot count a loop", () => {
    const { kinds } = run(7, ["idle"]);
    const distinct = new Set(kinds);
    expect(distinct.size).toBeGreaterThanOrEqual(4);
  });

  it("honours the weights · glancing dominates, yawning is rare", () => {
    const rand = makeRandom(11);
    const counts = new Map<string, number>();
    for (let i = 0; i < 20000; i += 1) {
      const move = pickIdleMove(rand);
      counts.set(move.kind, (counts.get(move.kind) ?? 0) + 1);
    }
    const share = (kind: string) => (counts.get(kind) ?? 0) / 20000;
    // The pool weights glance 33 and yawn 3, an 11x spread. Sampling noise is
    // far tighter than that, so these bounds are loose on purpose but still
    // fail if the weighting is dropped entirely.
    expect(share("glance")).toBeGreaterThan(0.25);
    expect(share("glance")).toBeLessThan(0.42);
    expect(share("yawn")).toBeLessThan(0.08);
  });

  it("keeps every move inside its declared duration", () => {
    const rand = makeRandom(3);
    for (const move of IDLE_POOL) {
      for (let i = 0; i < 200; i += 1) {
        const d = moveDuration(move, rand);
        expect(d).toBeGreaterThanOrEqual(move.minMs);
        expect(d).toBeLessThanOrEqual(move.maxMs);
      }
    }
  });

  it("blinks irregularly, never on a metronome", () => {
    const rand = makeRandom(5);
    const gaps = Array.from({ length: 200 }, () => nextBlink(rand));
    // If blinks were on a timer these would all be equal. They are not.
    expect(new Set(gaps.map((g) => Math.round(g))).size).toBeGreaterThan(100);
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(2000);
    expect(Math.max(...gaps)).toBeLessThanOrEqual(5000);
  });
});

describe("the breath", () => {
  it("is continuous, bounded, and scales with activation", () => {
    for (let t = 0; t < 20000; t += 137) {
      const awake = breath(t, 1);
      const asleep = breath(t, 0);
      expect(Math.abs(awake)).toBeLessThanOrEqual(0.016);
      // Asleep is not just "shallower", it is perfectly still.
      expect(asleep).toBe(0);
    }
  });

  it("returns to where it started, so it never drifts", () => {
    // An integral that does not close is a slow drift the eye catches.
    expect(breath(4200, 1)).toBeCloseTo(breath(0, 1), 6);
  });
});

describe("activation", () => {
  it("eases up when busy and down when idle, instead of flipping", () => {
    const rand = makeRandom(13);
    let p = initialPresence(13);
    const seen: number[] = [];
    for (let i = 0; i < 200; i += 1) {
      p = advance(p, 16.667, "thinking", rand);
      seen.push(p.activation);
    }
    expect(p.activation).toBeGreaterThan(0.9);
    // Monotonic while working · no flicker.
    for (let i = 1; i < seen.length; i += 1) expect(seen[i]).toBeGreaterThanOrEqual(seen[i - 1] - 1e-9);

    for (let i = 0; i < 400; i += 1) p = advance(p, 16.667, "idle", rand);
    expect(p.activation).toBeLessThan(0.1);
  });

  it("stays inside 0..1 under any state churn", () => {
    const states: PresenceState[] = ["idle", "thinking", "speaking", "compacting", "asleep", "listening"];
    const { activations } = run(21, states, 8000);
    for (const a of activations) {
      expect(a).toBeGreaterThanOrEqual(0);
      expect(a).toBeLessThanOrEqual(1);
    }
  });
});

describe("state changes", () => {
  it("injects energy so a change is never ambiguous", () => {
    const rand = makeRandom(17);
    let p = initialPresence(17);
    for (let i = 0; i < 50; i += 1) p = advance(p, 16.667, "thinking", rand);
    const before = p.transitionEnergy;
    p = advance(p, 16.667, "blocked", rand);
    expect(p.state).toBe("blocked");
    expect(p.transitionEnergy).toBeGreaterThan(before);
    // And it decays back, or the orb would sit permanently hot.
    for (let i = 0; i < 200; i += 1) p = advance(p, 16.667, "blocked", rand);
    expect(p.transitionEnergy).toBeLessThan(0.1);
  });

  it("does not idle while it is working", () => {
    const rand = makeRandom(23);
    let p = initialPresence(23);
    for (let i = 0; i < 300; i += 1) {
      p = advance(p, 16.667, "thinking", rand);
      expect(p.move).toBeNull();
    }
  });
});

describe("determinism", () => {
  it("replays exactly from a seed", () => {
    const a = run(99, ["idle", "thinking", "speaking"], 500);
    const b = run(99, ["idle", "thinking", "speaking"], 500);
    expect(a.kinds).toEqual(b.kinds);
    expect(a.activations).toEqual(b.activations);
  });

  it("produces a different timeline for a different seed", () => {
    // If every seed produced the same motion, presence would be a canned
    // animation with extra steps.
    const a = run(1, ["idle"], 600);
    const b = run(2, ["idle"], 600);
    expect(a.kinds).not.toEqual(b.kinds);
  });
});

// ---------------------------------------------------------------------------
// The rule the whole redesign exists to enforce: nothing moves without a real
// reason. These are the assertions that would have caught the free-running
// sweep and the constant 3.3Hz swell, because both of those pass every
// "does it look alive" test and fail every one of these.
// ---------------------------------------------------------------------------

/** Drive a presence forward and collect the motion each frame licensed. */
function drive(seed: number, state: PresenceState, frames: number, eventsAt: Record<number, number> = {}) {
  const rand = makeRandom(seed);
  let p = initialPresence(seed);
  const a = initialAudio();
  const spins: number[] = [];
  const swells: number[] = [];
  const rings: number[] = [];
  let latest = motionFor(p, a);
  for (let i = 0; i < frames; i += 1) {
    p = advance(p, 16.667, state, rand, eventsAt[i] ?? p.events);
    const m = motionFor(p, a);
    spins.push(m.spin);
    swells.push(m.swell);
    rings.push(m.ring);
    latest = m;
  }
  return { spins, swells, rings, latest, presence: p };
}

describe("the ring turns by event, not by clock", () => {
  it("does not move at all when nothing happens, however long you wait", () => {
    // Ten seconds of frames at 60Hz with zero events. The old painter advanced
    // its sweep by `t * motion.speed` here, so the angle changed every frame
    // and the eye could count the loop.
    const { spins } = drive(3, "working", 600);
    expect(new Set(spins.map((s) => s.toFixed(9))).size).toBe(1);
    expect(spins[0]).toBe(0);
  });

  it("is still in every state that has no business moving", () => {
    for (const state of ["idle", "working", "thinking", "attention", "compacting"] as PresenceState[]) {
      const { spins, latest } = drive(9, state, 400);
      expect(new Set(spins.map((s) => s.toFixed(9))).size).toBe(1);
      expect(latest.backing.spin).toBe("event");
      expect(latest.spin).toBe(0);
    }
  });

  it("advances by exactly one segment per real event", () => {
    const rand = makeRandom(5);
    let p = initialPresence(5);
    const angles: number[] = [];
    // One event on frame 10, one on frame 100, one on frame 200.
    const schedule: Record<number, number> = { 10: 1, 100: 2, 200: 3 };
    for (let i = 0; i < 260; i += 1) {
      p = advance(p, 16.667, "working", rand, schedule[i] ?? p.events);
      if (i === 9 || i === 10 || i === 99 || i === 100 || i === 199 || i === 200) {
        angles.push(motionFor(p, initialAudio()).spin);
      }
    }
    // No event between 10 and 99, so the angle must be identical at both.
    expect(angles[1]).toBeCloseTo(angles[2], 9);
    expect(angles[1]).toBeCloseTo(EVENT_ARC, 9);
    expect(angles[3]).toBeCloseTo(2 * EVENT_ARC, 9);
    expect(angles[4]).toBeCloseTo(angles[3], 9);
    expect(angles[5]).toBeCloseTo(3 * EVENT_ARC, 9);
  });

  it("empties the trail as events age out, so a room going quiet looks quiet", () => {
    const rand = makeRandom(7);
    let p = initialPresence(7);
    // One event, then a long silence.
    p = advance(p, 16.667, "working", rand, 1);
    const fresh = motionFor(p, initialAudio());
    expect(fresh.ticks[0]).toBeGreaterThan(0.9);
    for (let i = 0; i < 200; i += 1) p = advance(p, 16.667, "working", rand, p.events);
    const stale = motionFor(p, initialAudio());
    expect(stale.ticks.every((t) => t === 0)).toBe(true);
    // The ring is still drawn, faintly, because activation is still up. That is
    // the body being awake, not a claim that work is arriving.
    expect(stale.ring).toBeLessThan(fresh.ring);
  });

  it("never rewinds, even if the count it is handed goes backwards", () => {
    // The tray is capped and can be cleared, so a lower count arrives for real.
    const rand = makeRandom(11);
    let p = initialPresence(11);
    p = advance(p, 16.667, "working", rand, 40);
    expect(p.events).toBe(40);
    p = advance(p, 16.667, "working", rand, 3);
    expect(p.events).toBe(40);
    expect(motionFor(p, initialAudio()).spin).toBeCloseTo(40 * EVENT_ARC, 9);
  });
});

describe("a still state is still", () => {
  it("names the states that may not move at all", () => {
    expect(isStill("asleep")).toBe(true);
    expect(isStill("blocked")).toBe(true);
    for (const state of ["idle", "working", "listening", "speaking", "compacting"] as PresenceState[]) {
      expect(isStill(state)).toBe(false);
    }
  });

  it("zeroes every licensed channel, including audio and the eye", () => {
    for (const state of ["asleep", "blocked"] as PresenceState[]) {
      const { latest, presence } = drive(13, state, 120, { 5: 3, 20: 9 });
      expect(latest.spin).toBe(0);
      expect(latest.ring).toBe(0);
      expect(latest.swell).toBe(0);
      expect(latest.audio).toBeNull();
      expect(latest.speaking).toBe(false);
      expect(latest.ticks.every((t) => t === 0)).toBe(true);
      expect(latest.backing).toEqual({ spin: "none", ring: "none", swell: "none", audio: "none" });
      // A blocked orb keeps its eye open. It is waiting for something. Asleep
      // shuts it, because asleep is not waiting for anything.
      expect(latest.aperture).toBe(state === "asleep" ? 0 : 1);
      expect(presence.events).toBe(9);
    }
  });

  it("does not blink while asleep, even though asleep counts as idling", () => {
    const rand = makeRandom(17);
    let p = initialPresence(17);
    let blinked = false;
    for (let i = 0; i < 2000; i += 1) {
      p = advance(p, 16.667, "asleep", rand);
      if (p.blinking) blinked = true;
    }
    expect(blinked).toBe(false);
  });
});

describe("symmetry", () => {
  it("opens and closes a state on the same curve", () => {
    // 200ms of approach from 0.2 toward 0.6 has to equal 200ms from 0.6 back to
    // 0.2. This is the assertion that killed the old pair of one-sided rates.
    const up = approach(0.2, 0.6, 200, 1800);
    const down = approach(0.6, 0.2, 200, 1800);
    expect(up - 0.2).toBeCloseTo(0.6 - down, 12);
  });

  it("does not care how many frames it is given", () => {
    // The same 1000ms as one big step or sixty small ones has to land in the
    // same place, or the orb is a different orb on a 120Hz display.
    const coarse = approach(0, 1, 1000, 400);
    let fine = 0;
    for (let i = 0; i < 60; i += 1) fine = approach(fine, 1, 1000 / 60, 400);
    expect(fine).toBeCloseTo(coarse, 9);
  });

  it("arrives immediately when the tau is zero", () => {
    expect(approach(0.3, 1, 16.667, 0)).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Audio. The distinction under test throughout is `null` versus `0`: no level
// source is not the same as a silent room, and only one of them may draw a
// waveform.
// ---------------------------------------------------------------------------

/** Run the audio envelope through a scripted sequence of readings. */
function play(readings: AudioReading[], dt = 16.667) {
  let a = initialAudio();
  const frames = readings.map((reading) => {
    a = advanceAudio(a, dt, a.time + dt, reading);
    return a;
  });
  return frames;
}

describe("the audio envelope", () => {
  it("reports no data when there is no source, which is not the same as silence", () => {
    const [first] = play([{ mic: null, playback: null, playing: false }]);
    expect(first.source).toBe("none");
    expect(first.measured).toBe(false);
    expect(first.level).toBe(0);

    // A real source reporting a genuinely silent room IS measured, and that
    // difference is the whole point.
    const [quiet] = play([{ mic: 0, playback: null, playing: false }]);
    expect(quiet.source).toBe("mic");
    expect(quiet.measured).toBe(true);
    expect(quiet.level).toBe(0);
  });

  it("follows a real level instead of a clock", () => {
    // Silence, then a loud vowel, then silence again. The envelope has to
    // track those three facts and nothing else.
    const frames = play([
      ...Array(20).fill({ mic: 0, playback: null, playing: false }),
      ...Array(20).fill({ mic: 0.8, playback: null, playing: false }),
      ...Array(40).fill({ mic: 0, playback: null, playing: false }),
    ] as AudioReading[]);
    const quiet = frames[10].level;
    const loud = frames[30].level;
    const after = frames[frames.length - 1].level;
    expect(quiet).toBeLessThan(LEVEL_FLOOR);
    expect(loud).toBeGreaterThan(0.7);
    expect(after).toBeLessThan(0.05);
    // Monotone up into the vowel and monotone back out of it. A ring that
    // flickers inside a steady tone is a ring nobody can read.
    for (let i = 20; i < 40; i += 1) expect(frames[i].level).toBeGreaterThanOrEqual(frames[i - 1].level - 1e-9);
    for (let i = 40; i < frames.length; i += 1) expect(frames[i].level).toBeLessThanOrEqual(frames[i - 1].level + 1e-9);
  });

  it("attacks faster than it releases, so gaps between words read as speech", () => {
    const up = play([{ mic: 1, playback: null, playing: false }], 16.667);
    expect(up[up.length - 1].level).toBeGreaterThan(0.3);
    const upAfter = play([...Array(30).fill({ mic: 1, playback: null, playing: false })] as AudioReading[]);
    const down = play(
      [...Array(30).fill({ mic: 1, playback: null, playing: false }), ...Array(30).fill({ mic: 0, playback: null, playing: false })] as AudioReading[],
    );
    // Same number of frames either side of the change, and the fall is the
    // gentler of the two.
    expect(30 - down[59].level).toBeGreaterThan(upAfter[29].level);
  });

  it("holds a peak so a syllable that already ended is still legible", () => {
    const frames = play([
      ...Array(10).fill({ mic: 0.9, playback: null, playing: false }),
      ...Array(5).fill({ mic: 0, playback: null, playing: false }),
    ] as AudioReading[]);
    // The level has fallen, but the peak is still up near where it was.
    expect(frames[14].peak).toBeGreaterThan(frames[14].level + 0.2);
  });

  it("prefers what it is playing over what it is hearing", () => {
    const [frame] = play([{ mic: 0.9, playback: 0.1, playing: true }]);
    expect(frame.source).toBe("playback");
    expect(frame.level).toBeLessThan(0.5);
  });

  it("reads the speech window off the transport, not off a duration estimate", () => {
    const frames = play([
      { mic: null, playback: null, playing: false },
      { mic: null, playback: null, playing: true },
      { mic: null, playback: null, playing: true },
      { mic: null, playback: null, playing: false },
    ]);
    expect(frames[0].speechStartedAt).toBeNull();
    // The start is stamped on the frame playback actually began.
    expect(frames[1].speechStartedAt).toBeCloseTo(frames[1].time, 9);
    expect(frames[2].speechStartedAt).toBe(frames[1].speechStartedAt);
    // And the end is stamped on the frame it really stopped, not interpolated.
    expect(frames[3].speechEndedAt).toBeCloseTo(frames[3].time, 9);
  });

  it("stops being 'speaking' on the frame playback stops, not after a guess", () => {
    const frames = play([
      { mic: null, playback: null, playing: true },
      { mic: null, playback: null, playing: true },
      { mic: null, playback: null, playing: false },
    ]);
    expect(frames[1].playing).toBe(true);
    expect(frames[2].playing).toBe(false);
  });

  it("holds an open envelope while playing, and says it is unmeasured", () => {
    // With no amplitude tap the orb can honestly claim only that audio is
    // leaving the machine. The shape is transport-derived and the flag says so.
    const frames = play([
      ...Array(30).fill({ mic: null, playback: null, playing: true }),
    ] as AudioReading[]);
    const last = frames[frames.length - 1];
    expect(last.level).toBeGreaterThan(0.9);
    expect(last.measured).toBe(false);
    expect(last.source).toBe("playback");
  });

  it("stays inside 0..1 for any input, including nonsense", () => {
    const frames = play([
      { mic: 5, playback: null, playing: false },
      { mic: -3, playback: null, playing: false },
      { mic: Number.NaN, playback: null, playing: false },
      { mic: Number.POSITIVE_INFINITY, playback: null, playing: false },
    ]);
    for (const f of frames) {
      expect(f.level).toBeGreaterThanOrEqual(0);
      expect(f.level).toBeLessThanOrEqual(1);
      expect(f.peak).toBeGreaterThanOrEqual(0);
      expect(f.peak).toBeLessThanOrEqual(1);
    }
  });
});

describe("the audio ring refuses to invent data", () => {
  const listening = (seed = 31): Presence => {
    const rand = makeRandom(seed);
    let p = initialPresence(seed);
    for (let i = 0; i < 120; i += 1) p = advance(p, 16.667, "listening", rand);
    return p;
  };

  it("draws nothing while listening with no level source", () => {
    const p = listening();
    const m = motionFor(p, initialAudio());
    // This is the honesty assertion. `initialAudio()` means no source, so the
    // orb must not put a ring on screen for a microphone it cannot read.
    expect(m.audio).toBeNull();
    expect(m.backing.audio).toBe("none");
  });

  it("draws a real level the moment one is measured", () => {
    const p = listening();
    let a = initialAudio();
    for (let i = 0; i < 30; i += 1) a = advanceAudio(a, 16.667, a.time + 16.667, { mic: 0.6, playback: null, playing: false });
    const m = motionFor(p, a);
    expect(m.audio).toBeGreaterThan(0.5);
    expect(m.audioMeasured).toBe(true);
    expect(m.backing.audio).toBe("level");
  });

  it("still measures nothing while listening to a genuinely silent room", () => {
    // Measured silence. The ring is drawn, because there IS a source, and it is
    // drawn at zero. That is different from not drawing it, and the flag is
    // what tells them apart.
    const p = listening();
    let a = initialAudio();
    for (let i = 0; i < 30; i += 1) a = advanceAudio(a, 16.667, a.time + 16.667, { mic: 0, playback: null, playing: false });
    const m = motionFor(p, a);
    expect(m.audio).toBe(0);
    expect(m.audioMeasured).toBe(true);
  });

  it("stops claiming to speak the moment the transport does", () => {
    const p = (() => {
      const rand = makeRandom(37);
      let x = initialPresence(37);
      for (let i = 0; i < 60; i += 1) x = advance(x, 16.667, "speaking", rand);
      return x;
    })();
    let playing = initialAudio();
    for (let i = 0; i < 10; i += 1) playing = advanceAudio(playing, 16.667, playing.time + 16.667, { mic: null, playback: null, playing: true });
    expect(motionFor(p, playing).speaking).toBe(true);
    // An orb that keeps its mouth moving after the audio stopped is the exact
    // detail that makes an assistant sound like a recording.
    let stopped = playing;
    stopped = advanceAudio(stopped, 16.667, stopped.time + 16.667, { mic: null, playback: null, playing: false });
    expect(motionFor(p, stopped).speaking).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The level bus. A probe is a function someone else owns, so it has to be
// possible for it to be absent, to be dishonest, and to throw, without taking
// the frame loop down or producing a number that was never measured.
// ---------------------------------------------------------------------------

describe("the level bus", () => {
  it("reports no source honestly", () => {
    clearLevelSources();
    expect(hasLevelSource("mic")).toBe(false);
    expect(readLevel("mic")).toBeNull();
  });

  it("reads a registered probe and clamps it", () => {
    setLevelSource("mic", () => 0.4);
    expect(readLevel("mic")).toBe(0.4);
    setLevelSource("mic", () => 9);
    expect(readLevel("mic")).toBe(1);
    setLevelSource("mic", () => -9);
    expect(readLevel("mic")).toBe(0);
    clearLevelSources();
  });

  it("treats a null, a NaN, and a throw as no data rather than as zero", () => {
    // Zero is a real measurement and would light the ring as a quiet room. None
    // of these are measurements, so none of them may.
    for (const probe of [() => null, () => Number.NaN, () => Number.POSITIVE_INFINITY, () => undefined]) {
      setLevelSource("mic", probe as () => number | null);
      expect(readLevel("mic")).toBeNull();
    }
    setLevelSource("mic", () => {
      throw new Error("the audio graph went away mid-frame");
    });
    expect(readLevel("mic")).toBeNull();
    clearLevelSources();
  });

  it("keeps the two sources apart", () => {
    clearLevelSources();
    setLevelSource("playback", () => 0.2);
    expect(readLevel("playback")).toBe(0.2);
    expect(readLevel("mic")).toBeNull();
    clearLevelSources();
    expect(readLevel("playback")).toBeNull();
  });
});

describe("real event counting", () => {
  it("counts distinct signals and nothing else", () => {
    expect(eventCountFor([])).toBe(0);
    expect(eventCountFor([{ at: 3 }, { at: 2 }, { at: 1 }])).toBe(3);
    // The same timestamp pushed twice is one event, not two, or a re-render
    // would spin the ring.
    expect(eventCountFor([{ at: 7 }, { at: 7 }])).toBe(1);
  });
});

describe("compacting is reachable and is not a task", () => {
  it("is produced by a real compaction event rather than sitting unreachable", () => {
    // The runtime reports compaction as an ordinary event kind, and every
    // unrecognised kind reaches the tray verbatim.
    expect(orbStateFor([{ label: "harness.compaction_started" }])).toBe("compacting");
    expect(orbStateFor([{ label: "context_compacted" }])).toBe("compacting");
    // And it must not be mistaken for work, which is the failure it exists to
    // prevent: a wait that looks like a task reads as a hang.
    expect(orbStateFor([{ label: "compaction_started" }])).not.toBe("working");
    expect(STATE_GLOW.compacting).toBeLessThan(STATE_GLOW.working);
  });

  it("still yields to the states that genuinely need the user", () => {
    // Newest first, so a blocked event above a compaction event wins.
    expect(orbStateFor([{ label: "emergency_stop" }, { label: "compaction_started" }])).toBe("blocked");
    expect(orbStateFor([{ label: "mission_requirement_breach" }, { label: "compaction_started" }])).toBe("attention");
  });
});

describe("the honest palette", () => {
  it("never lets a state that needs nothing be brighter than one that does", () => {
    // blocked is the floor: a thing that is stuck must recede.
    expect(STATE_GLOW.blocked).toBeLessThan(STATE_GLOW.idle);
    // A wait must never look like work.
    expect(STATE_GLOW.compacting).toBeLessThan(STATE_GLOW.working);
    // Something that needs the user is the loudest thing in the room.
    expect(STATE_GLOW.attention).toBeGreaterThanOrEqual(STATE_GLOW.working);
  });

  it("keeps the idle swell inside a range the eye cannot mistake for a pulse", () => {
    // The old painter added a free-running sin(t/300) term on top of the
    // breath. Idle is now allowed to breathe by at most this much.
    const { swells } = drive(41, "idle", 1200);
    const peak = Math.max(...swells.map(Math.abs));
    expect(peak).toBeGreaterThan(0);
    expect(peak).toBeLessThan(0.05);
  });

  it("remembers a bounded number of event windows", () => {
    const rand = makeRandom(43);
    let p = initialPresence(43);
    for (let i = 0; i < 5000; i += 1) p = advance(p, 16.667, "working", rand, Math.floor(i / 20));
    expect(p.history.recent.length).toBe(EVENT_SLOTS);
    expect(p.events).toBeGreaterThan(100);
    expect(initialEvents().recent.every((at) => at === -1)).toBe(true);
  });
});
