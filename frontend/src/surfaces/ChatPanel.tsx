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

interface Turn {
  id: string;
  role: "you" | "hermus";
  text: string;
  /** Set while a turn is still arriving. */
  streaming?: boolean;
  /** A failure, rendered inside the bubble rather than swallowed. */
  error?: string;
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

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy) return;

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
          <div className={`chat-turn chat-${turn.role}`} key={turn.id}>
            <span className="chat-who">{turn.role === "you" ? "you" : "hermus"}</span>
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
          onChange={(event) => setDraft(event.target.value)}
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
