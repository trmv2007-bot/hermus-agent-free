/**
 * Conversation — a real chat that gets a real answer.
 *
 * This was a stub for the entire life of the room, and the reason was
 * concrete: `POST /api/v1/commands` looks like a chat endpoint and is not. It
 * publishes an event and answers in about three milliseconds with an
 * `event_id`. There was no route anywhere in the gateway that took a prompt
 * and returned a reply, so the panel had nothing to call. `POST /api/v1/chat`
 * exists now and streams Server-Sent Events.
 *
 * Three decisions that come from how the assistant actually feels to use:
 *
 *  - The user's message appears the instant it is sent, before the server has
 *    confirmed anything. A chat that leaves you staring at an empty box while
 *    a request is in flight reads as broken even when it is working.
 *  - There is a live activity line, not a spinner, and it names what is
 *    actually happening. A spinner during a fifteen-second tool call is
 *    precisely the decorative filler the persona research identifies as the
 *    thing that does not improve perceived response time; a line that says
 *    "running a command" does.
 *  - Failure is said plainly, with the server's own words. A turn that times
 *    out or 503s must not render as an empty assistant bubble, which is
 *    indistinguishable from a model that chose to say nothing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { noteInput, releaseAttention } from "../state/attention";
import { onRoomEvent } from "../realtime/room-events";

interface Turn {
  id: string;
  role: "you" | "hermus";
  text: string;
  /** Set while a turn is still arriving. */
  streaming?: boolean;
  /** A failure, rendered inside the bubble rather than swallowed. */
  error?: string;
  /**
   * An unsolicited line from the ambient loop, rather than a reply to
   * something the user asked.
   *
   * It is carried on the turn rather than kept in a separate list because a
   * separate list is a second transcript with its own ordering problems: a
   * proactive note and a reply are the same kind of thing (the assistant said
   * it) and belong in the same sequence, in the order they actually happened.
   *
   * The reason is kept for the tooltip. The whole argument for letting the
   * assistant speak first is that it can say *why* it thought this was worth
   * interrupting for, and a distinction the user cannot inspect is a
   * distinction the user has to take on faith.
   */
  unsolicited?: { rule: string; reason: string };
}

let counter = 0;
const nextId = () => `t${++counter}`;

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState("");
  const scrollerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Keep the newest turn in view, the way a real transcript does.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  // A turn still running when the panel unmounts must not keep streaming into
  // a component that is gone, and must not set state afterwards.
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // An unsolicited line, arriving on the room stream with nobody having asked
  // for it. Subscribing here rather than opening a second socket is the point
  // of `onRoomEvent`: `connectStream` already fans every frame through it.
  //
  // The functional update matters. `send` also appends turns, and by the time
  // an ambient line lands a turn may be mid-flight; a stale-closure append
  // would silently drop it, and a dropped proactive line is the one bug in
  // this feature that a user would experience as "it never actually says
  // anything".
  useEffect(
    () =>
      onRoomEvent("hermus_spoke", (event) => {
        const data = (event.data ?? {}) as {
          text?: string;
          rule?: string;
          reason?: string;
          id?: string;
        };
        const text = typeof data.text === "string" ? data.text.trim() : "";
        // An empty proactive bubble is worse than none: it is a speech the
        // user cannot dismiss and cannot read, and it would defeat the panel's
        // existing rule that a turn with no text means something went wrong.
        if (!text) return;
        setTurns((prev) => [
          ...prev,
          {
            id: data.id ?? nextId(),
            role: "hermus",
            text,
            unsolicited: { rule: data.rule ?? "unknown", reason: data.reason ?? "" },
          },
        ]);
      }),
    [],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy) return;
    // The field is done being typed into, so the room stops looking at it and
    // goes back to the work. The gaze eases out rather than cutting, which is
    // what makes it read as looking away instead of losing interest.
    releaseAttention();

    const youId = nextId();
    const hermusId = nextId();
    // Both bubbles exist before the request goes out. The empty assistant
    // bubble is the visual promise that something is coming.
    setTurns((prev) => [
      ...prev,
      { id: youId, role: "you", text },
      { id: hermusId, role: "hermus", text: "", streaming: true },
    ]);
    setDraft("");
    setBusy(true);
    setActivity("thinking…");

    const controller = new AbortController();
    abortRef.current = controller;
    let received = "";

    const patch = (fn: (turn: Turn) => Turn) =>
      setTurns((prev) => prev.map((t) => (t.id === hermusId ? fn(t) : t)));

    try {
      const res = await fetch("/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { error?: string; message?: string };
        throw new Error(body.error ?? body.message ?? `the gateway returned HTTP ${res.status}`);
      }
      if (!res.body) throw new Error("the gateway sent no stream to read");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line. Anything after the last
        // separator is a partial frame and must wait for more bytes — parsing
        // it early is how a streamed answer ends up with its last word cut in
        // half.
        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          split = buffer.indexOf("\n\n");

          if (!frame.trim() || frame.startsWith(":")) continue;
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;

          let data: { data?: unknown; content?: string; error?: string };
          try {
            data = JSON.parse(dataLines.join("\n"));
          } catch {
            continue;
          }

          if (event === "error") {
            throw new Error(data.error ?? "the turn failed");
          }

          if (event === "final") {
            // The final frame is authoritative. Streamed deltas may be partial
            // or duplicated; the completed text is the answer.
            if (typeof data.content === "string" && data.content.trim()) {
              received = data.content;
              patch((t) => ({ ...t, text: received }));
            } else if (!received.trim()) {
              throw new Error(
                "the model finished without saying anything — check the main model in Settings",
              );
            }
          } else {
            // Progress events. Named where the name is useful, and never
            // rendered as if it were part of the answer.
            const detail = data.data as { label?: string; name?: string; tool?: string } | undefined;
            const what = detail?.label ?? detail?.name ?? detail?.tool;
            if (what) setActivity(String(what));
          }
        }
      }

      patch((t) => ({ ...t, text: received || t.text, streaming: false }));
    } catch (err) {
      const aborted = (err as Error).name === "AbortError";
      patch((t) => ({
        ...t,
        streaming: false,
        error: aborted ? undefined : (err as Error).message,
        text: aborted ? t.text : t.text || "—",
      }));
    } finally {
      setBusy(false);
      setActivity("");
      abortRef.current = null;
    }
  }, [busy, draft]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollerRef}>
        {turns.length === 0 && (
          <p className="chat-empty">
            Ask something. This streams from the model, so the first words arrive before the rest do.
          </p>
        )}
        {turns.map((turn) => (
          <div
            className={`chat-turn chat-${turn.role}${turn.unsolicited ? " chat-unsolicited" : ""}`}
            key={turn.id}
            // The reason is the explanation for speaking without being asked, so
            // it belongs where it can be read without being shouted. A title
            // attribute is the right weight for it: present, inspectable, and
            // invisible to anyone who is not looking for it.
            title={turn.unsolicited ? turn.unsolicited.reason : undefined}
            data-rule={turn.unsolicited?.rule}
          >
            <span className="chat-who">
              {turn.role === "you" ? "you" : "hermus"}
              {/* Says *that* it was unprompted. A different colour alone does
                  not survive a high-contrast mode or a colourblind reader,
                  and "why did it say that with no reason" is the question this
                  label exists to pre-empt. */}
              {turn.unsolicited && <em className="chat-unasked"> · unprompted</em>}
            </span>
            <div className="chat-bubble">
              {turn.text}
              {turn.streaming && !turn.text && <span className="chat-waiting">…</span>}
              {turn.error && <em className="chat-error">{turn.error}</em>}
            </div>
          </div>
        ))}
      </div>

      {activity && <p className="chat-activity">{activity}</p>}

      <form
        className="chat-compose"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          className="chat-input"
          value={draft}
          rows={2}
          placeholder="tell HERMUS what to do"
          spellCheck
          onChange={(event) => {
            setDraft(event.target.value);
            // The room is told where the person is, in viewport fractions, so
            // the core can look at the field being used. Clearing the field
            // releases it -- nothing is watching an empty box.
            const el = event.currentTarget;
            const r = el.getBoundingClientRect();
            if (event.target.value.trim()) {
              noteInput(
                (r.left + r.width / 2) / window.innerWidth,
                (r.top + r.height / 2) / window.innerHeight,
              );
            } else {
              releaseAttention();
            }
          }}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter is a newline. The usual inversion is
            // Enter for newline, which makes a chat feel like a textarea.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        {busy ? (
          <button type="button" className="chat-send chat-stop" onClick={stop}>
            stop
          </button>
        ) : (
          <button type="submit" className="chat-send" disabled={!draft.trim()}>
            send
          </button>
        )}
      </form>
    </div>
  );
}
