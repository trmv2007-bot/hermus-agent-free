import { describe, expect, it, beforeEach, vi } from "vitest";

import {
  clearLoop,
  getLoopState,
  loopHistory,
  resetLoopState,
  setLoopState,
  setWakeRequired,
  shouldAutoAnswer,
  speakTo,
  stopLoop,
  subscribeLoop,
} from "./loop";

/**
 * These tests exist because the endpointing bug was invisible to the suite.
 *
 * A 256x over-counting frame loop produced a transcript of "IS TOO PLUS TOO"
 * from a clip that said "Hermes, what is two plus two", and 95 green tests
 * did not notice, because nothing asserted on the audio. The store tests below
 * are the same class of thing: cheap, and they pin the two invariants that
 * make the loop a conversation rather than a queue of requests.
 */

const quietFrame = (n: number) => new Float32Array(n); // all zeros
const loudFrame = (n: number, amp = 0.3) => {
  const f = new Float32Array(n);
  for (let i = 0; i < n; i++) f[i] = amp;
  return f;
};

function fetchStub(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  return vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => handler(String(url), init)));
}

function sseResponse(content: string): Response {
  const body =
    `event: status\ndata: {"phase": "thinking", "messages": 1}\n\n` +
    `event: final\ndata: ${JSON.stringify({ ok: true, content, elapsed_s: 1 })}\n\n`;
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

beforeEach(() => {
  resetLoopState();
  vi.unstubAllGlobals();
});

describe("loop store", () => {
  it("starts empty and idle", () => {
    expect(getLoopState().turns).toEqual([]);
    expect(getLoopState().phase).toBe("idle");
  });

  it("notifies subscribers on a real change and stays quiet on a no-op", () => {
    const seen: number[] = [];
    const off = subscribeLoop((s) => seen.push(s.turns.length));
    setLoopState({ activity: "thinking…" });
    setLoopState({ activity: "thinking…" }); // identical: must not notify
    expect(seen).toEqual([0]);
    off();
    setLoopState({ activity: "speaking…" });
    expect(seen).toEqual([0]);
  });

  it("clears turns but keeps the wake choice", () => {
    setWakeRequired(true);
    setLoopState({ turns: [{ id: 1, role: "you", text: "hi", source: "voice" }] });
    clearLoop();
    expect(getLoopState().turns).toEqual([]);
    expect(getLoopState().wakeRequired).toBe(true);
  });

  it("reset drops the wake choice too", () => {
    setWakeRequired(true);
    resetLoopState();
    expect(getLoopState().wakeRequired).toBe(false);
  });
});

describe("loopHistory", () => {
  it("keeps only completed turns with text", () => {
    setLoopState({
      turns: [
        { id: 1, role: "you", text: "first", source: "voice" },
        { id: 2, role: "hermus", text: "answer one", source: "voice" },
        { id: 3, role: "hermus", text: "", pending: true, source: "voice" },
        { id: 4, role: "hermus", text: "boom", error: "HTTP 500", source: "voice" },
        { id: 5, role: "you", text: "   ", source: "voice" },
      ],
    });
    expect(loopHistory()).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "answer one" },
    ]);
  });

  it("excludes a failed turn so the model never sees its own error as a message", () => {
    setLoopState({
      turns: [
        { id: 1, role: "you", text: "q", source: "typed" },
        { id: 2, role: "hermus", text: "the gateway returned HTTP 503", error: "boom", source: "typed" },
      ],
    });
    expect(loopHistory().map((h) => h.role)).toEqual(["user"]);
  });
});

describe("shouldAutoAnswer", () => {
  it("answers anything in the open mode", () => {
    expect(shouldAutoAnswer("what time is it", [])).toBe(true);
  });

  it("never answers an empty transcript, in either mode", () => {
    expect(shouldAutoAnswer("", [])).toBe(false);
    setWakeRequired(true);
    expect(shouldAutoAnswer("   ", ["HERMES"])).toBe(false);
  });

  it("requires a wake hit in the gated mode", () => {
    setWakeRequired(true);
    expect(shouldAutoAnswer("turn on the lights", [])).toBe(false);
    expect(shouldAutoAnswer("hermes turn on the lights", ["HERMES"])).toBe(true);
  });
});

describe("speakTo", () => {
  it("sends the message with the history that existed BEFORE this turn", async () => {
    // The bug this pins: history read after appending sent the question twice
    // and produced a transcript of [user, user, user] with no assistant.
    let seen: { message: string; history: unknown[] } | null = null;
    fetchStub((url, init) => {
      if (url.includes("/api/v1/chat")) {
        seen = JSON.parse(String(init?.body));
        return sseResponse("four");
      }
      return new Response(JSON.stringify({ spoken: true, audio_url: "/speech/audio/x" }), { status: 200 });
    });
    // No real Audio element in node, so playback resolves via a stub.
    vi.stubGlobal(
      "Audio",
      class {
        onended: (() => void) | null = null;
        onerror: ((m: string) => void) | null = null;
        play() {
          setTimeout(() => this.onended?.(), 0);
          return Promise.resolve();
        }
      },
    );

    setLoopState({
      turns: [
        { id: 1, role: "you", text: "earlier question", source: "voice" },
        { id: 2, role: "hermus", text: "earlier answer", source: "voice" },
      ],
    });

    await speakTo("and now this one", "voice");

    const body = seen as unknown as { message: string; history: { role: string; content: string }[] };
    expect(body.message).toBe("and now this one");
    expect(body.history).toEqual([
      { role: "user", content: "earlier question" },
      { role: "assistant", content: "earlier answer" },
    ]);
    // The new turn is appended exactly once.
    expect(body.history.some((h) => h.content === "and now this one")).toBe(false);
  });

  it("renders the question and the answer as two turns", async () => {
    fetchStub((url) => {
      if (url.includes("/api/v1/chat")) return sseResponse("the answer is four");
      return new Response(JSON.stringify({ spoken: true, audio_url: "/speech/audio/x" }), { status: 200 });
    });
    vi.stubGlobal(
      "Audio",
      class {
        onended: (() => void) | null = null;
        onerror: ((m: string) => void) | null = null;
        play() {
          setTimeout(() => this.onended?.(), 0);
          return Promise.resolve();
        }
      },
    );

    await speakTo("two plus two", "voice");

    const turns = getLoopState().turns;
    expect(turns.map((t) => t.role)).toEqual(["you", "hermus"]);
    expect(turns[0].text).toBe("two plus two");
    expect(turns[0].source).toBe("voice");
    expect(turns[1].text).toBe("the answer is four");
    expect(turns[1].pending).toBeFalsy();
    expect(getLoopState().phase).toBe("idle");
  });

  it("ignores an empty message", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await speakTo("   ");
    expect(f).not.toHaveBeenCalled();
    expect(getLoopState().turns).toEqual([]);
  });

  it("keeps the answer on screen when speech fails, and says why", async () => {
    fetchStub((url) => {
      if (url.includes("/api/v1/chat")) return sseResponse("four");
      return new Response(JSON.stringify({ spoken: false, error: "no speech backend" }), { status: 200 });
    });

    await speakTo("two plus two", "typed");

    const hermus = getLoopState().turns.find((t) => t.role === "hermus");
    expect(hermus?.text).toBe("four");
    expect(hermus?.error).toContain("no speech backend");
  });

  it("puts a failed turn in the bubble rather than an empty one", async () => {
    fetchStub(() => new Response(JSON.stringify({ error: "no model is reachable" }), { status: 503 }));

    await speakTo("hello", "typed");

    const hermus = getLoopState().turns.find((t) => t.role === "hermus");
    expect(hermus?.error).toContain("no model is reachable");
    expect(hermus?.text).toBe("-");
    expect(getLoopState().error).toContain("no model is reachable");
  });

  it("surfaces a model that answers with nothing", async () => {
    const body = 'event: final\ndata: {"ok": true, "content": "   "}\n\n';
    fetchStub(() => new Response(body, { status: 200 }));

    await speakTo("hello", "typed");

    const hermus = getLoopState().turns.find((t) => t.role === "hermus");
    expect(hermus?.error).toContain("without saying anything");
  });

  it("refuses a second turn while one is in flight", async () => {
    let release: (() => void) | null = null;
    fetchStub((url) => {
      if (url.includes("/api/v1/chat")) {
        return new Promise<Response>((resolve) => {
          release = () =>
            resolve(
              new Response(
                'event: final\ndata: {"ok": true, "content": "first"}\n\n',
                { status: 200 },
              ),
            );
        });
      }
      return new Response(JSON.stringify({ spoken: true, audio_url: "/speech/audio/x" }), { status: 200 });
    });
    vi.stubGlobal(
      "Audio",
      class {
        onended: (() => void) | null = null;
        onerror: ((m: string) => void) | null = null;
        play() {
          setTimeout(() => this.onended?.(), 0);
          return Promise.resolve();
        }
      },
    );

    const first = speakTo("one", "typed");
    await speakTo("two", "typed"); // must be dropped, not queued
    // TS narrows `release` to null at this line because the assignment happens
    // inside a callback it cannot see, and that narrowing survives being copied
    // into a typed const. The assertion is the escape hatch: it resets the
    // narrowed type back to what the variable is declared as. (An IIFE that
    // merely *returns* release and calls itself also looks like it works, and
    // releases nothing, so the test hangs instead of failing.)
    (release as (() => void) | null)?.();
    await first;

    expect(getLoopState().turns.filter((t) => t.role === "you").map((t) => t.text)).toEqual(["one"]);
  });

  it("marks an aborted turn without inventing an error", async () => {
    fetchStub((url) => {
      if (url.includes("/api/v1/chat")) {
        return new Promise<Response>((_resolve, reject) => {
          setTimeout(() => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          }, 5);
        });
      }
      return new Response("{}", { status: 200 });
    });

    const run = speakTo("stop me", "typed");
    setTimeout(() => stopLoop(), 1);
    await run;

    const hermus = getLoopState().turns.find((t) => t.role === "hermus");
    expect(hermus?.error).toBeUndefined();
    expect(getLoopState().aborted).toBe(true);
  });
});

describe("audio primitives used by the tests above", () => {
  it("quiet and loud frames differ in energy", () => {
    const quiet = quietFrame(256);
    const loud = loudFrame(256, 0.3);
    const rms = (f: Float32Array) => Math.sqrt(f.reduce((a, v) => a + v * v, 0) / f.length);
    expect(rms(quiet)).toBe(0);
    expect(rms(loud)).toBeCloseTo(0.3, 5);
  });
});
