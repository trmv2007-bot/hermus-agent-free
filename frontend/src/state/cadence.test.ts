import { describe, expect, it } from "vitest";

import {
  advance,
  DEFAULT_CHARS_PER_SEC,
  initialCadence,
  isDrained,
  maybeTick,
  push,
  resetCadence,
  wordsShown,
} from "./cadence";

describe("queueing", () => {
  it("releases nothing on push, so a fast provider still reads at a human rate", () => {
    const c = push(initialCadence(), "the whole answer arrived at once");
    expect(c.release).toBe(0);
    expect(c.shown).toBe("");
    expect(c.pending).toBe(true);
  });

  it("accumulates chunks rather than replacing them", () => {
    let c = initialCadence();
    c = push(c, "one ");
    c = push(c, "two ");
    expect(c.queue).toBe("one two ");
  });

  it("ignores an empty chunk instead of flushing", () => {
    const c = push(initialCadence(), "");
    expect(c.queue).toBe("");
  });
});

describe("release", () => {
  it("reveals at roughly the configured rate", () => {
    let c = push(initialCadence(), "x".repeat(1000));
    // one second of a stream that only had a fraction of a second to arrive
    c = advance(c, 1000);
    expect(c.release).toBe(DEFAULT_CHARS_PER_SEC);
    expect(c.shown.length).toBe(DEFAULT_CHARS_PER_SEC);
  });

  it("never releases more than is queued", () => {
    const c = advance(push(initialCadence(), "short"), 5000);
    expect(c.release).toBe(5);
    expect(isDrained(c)).toBe(true);
  });

  it("emits nothing when there is nothing queued", () => {
    expect(advance(initialCadence(), 100).release).toBe(0);
  });

  it("drains fully given enough time, and the text is unchanged", () => {
    const text = "the quick brown fox jumps over the lazy dog, twice, at length";
    let c = push(initialCadence(), text);
    for (let t = 0; t < 4000; t += 16) c = advance(c, 16);
    expect(isDrained(c)).toBe(true);
    expect(c.shown).toBe(text);
  });

  it("is frame-rate independent -- a slow frame and a fast one deliver the same text", () => {
    const text = "y".repeat(400);
    let slow = push(initialCadence(), text);
    slow = advance(slow, 400); // one 400ms frame
    let fast = push(initialCadence(), text);
    for (let t = 0; t < 400; t += 8) fast = advance(fast, 8); // fifty 8ms frames
    expect(fast.shown).toBe(slow.shown);
  });

  it("never drops or reorders characters, at any rate", () => {
    const text = "mixed 12 content: ünïcode, symbols — and *emphasis*.";
    let c = push(initialCadence(), text);
    let guard = 0;
    while (!isDrained(c) && guard++ < 2000) c = advance(c, 16);
    expect(c.shown).toBe(text);
  });
});

describe("the tick", () => {
  it("does not fire on an empty stream", () => {
    expect(maybeTick(initialCadence()).tick).toBe(false);
  });

  it("fires once every few words, not once per word", () => {
    // A tick per token is a Geiger counter, and nobody keeps it switched on.
    const c = advance(push(initialCadence(), "one two three four five six"), 5000);
    expect(wordsShown(c)).toBe(6);
    let fired = 0;
    for (let i = 0; i < 6; i++) {
      // one word revealed at a time
      const one = advance(c, 0);
      void one;
    }
    // Simulate word-by-word release instead of one lump.
    let step = initialCadence();
    let ticks = 0;
    for (const w of ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]) {
      step = push(step, w + " ");
      step = advance(step, 1000);
      if (maybeTick(step).tick) ticks++;
    }
    expect(fired).toBe(0);
    expect(ticks).toBe(2);
  });

  it("counts words, not characters, so a tick never lands mid-word", () => {
    const c = advance(push(initialCadence(), "alpha bravo charlie"), 5000);
    expect(wordsShown(c)).toBe(3);
  });

  it("resets for the next turn", () => {
    expect(resetCadence()).toEqual(initialCadence());
  });
});
