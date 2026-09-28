// @vitest-environment jsdom
//
// An unsolicited line, and the rule that an ambient note is a *different kind
// of speech* from a reply.
//
// The backend already decides when to speak (core/intent.py, via the loop in
// core/proactivity.py). Nothing here re-decides that. These tests cover the
// one thing only this side owns: that a line which arrives uninvited is
// visibly marked as such, and that a malformed one is not rendered as a
// bubble the user cannot read.
//
// jsdom is declared per-file because the project default is `node`
// (vite.config.ts); the pure-logic suites do not need a DOM.

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { emitRoomEvent } from "./room-events";
import { ChatPanel } from "../surfaces/ChatPanel";

// `emitRoomEvent` dispatches synchronously, but the listener it reaches calls
// `setState` from a `useEffect`. React will not flush that outside `act`, so
// without the wrapper the bubble exists in state and never reaches the DOM —
// which looks exactly like a broken feature rather than a broken test.
function speak(data: Record<string, unknown>) {
  act(() => {
    emitRoomEvent({ type: "hermus_spoke", data });
  });
}

// Without this, every `render` stays mounted and each test sees the previous
// test's bubbles too. `emitRoomEvent` is a module-level bus, so that leak
// reaches across tests as well as across the DOM.
afterEach(cleanup);

describe("an unsolicited line in the transcript", () => {
  it("appears as a bubble without the user having asked for one", () => {
    render(<ChatPanel />);
    expect(screen.queryByText(/build is done/i)).toBeNull();

    speak({ id: "s1", text: "memory.recall failed. I stopped rather than continue on a bad result.", rule: "urgent", reason: "because" });

    expect(screen.getByText(/memory\.recall failed/)).toBeTruthy();
  });

  it("is marked as unprompted rather than looking like a reply", () => {
    render(<ChatPanel />);
    speak({ id: "s1", text: "the build is done", rule: "quiet_window", reason: "3m quiet" });

    // The word is load-bearing on its own. A distinct colour or a dashed edge
    // does not survive a high-contrast mode or a colourblind reader, and
    // "why did it just say that" is the question the label pre-empts.
    expect(screen.getByText(/unprompted/i)).toBeTruthy();
  });

  it("carries the reason, so the judgement behind it can be inspected", () => {
    render(<ChatPanel />);
    speak({ id: "s1", text: "the build is done", rule: "quiet_window", reason: "the room has been quiet 4m" });

    const bubble = screen.getByText(/the build is done/).closest(".chat-turn");
    expect(bubble?.getAttribute("title")).toContain("quiet 4m");
    expect(bubble?.getAttribute("data-rule")).toBe("quiet_window");
  });

  it("renders nothing for a line with no text", () => {
    render(<ChatPanel />);
    speak({ id: "s1", text: "   ", rule: "urgent", reason: "because" });
    speak({ id: "s2", rule: "urgent", reason: "because" });

    // An empty bubble is a speech the user cannot read and cannot dismiss, and
    // it would collide with the panel's existing rule that an empty assistant
    // bubble means a turn went wrong.
    expect(screen.queryByText(/unprompted/i)).toBeNull();
    expect(screen.getByText(/Ask something/)).toBeTruthy();
  });

  it("does not blow up on a frame with no data at all", () => {
    render(<ChatPanel />);
    expect(() => emitRoomEvent({ type: "hermus_spoke" })).not.toThrow();
  });
});
