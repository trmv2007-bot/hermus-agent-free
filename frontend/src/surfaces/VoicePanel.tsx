/**
 * The voice surface: a real microphone, a real conversation, and an honest
 * statement of what this machine can and cannot do.
 *
 * The rule this panel exists to honour: nothing here is decorative. The level
 * meter is measured RMS, the transcript is whatever the recogniser returned
 * including the empty string, the turns are the real history, and the
 * capability block is a direct read of what the gateway said it loaded. When
 * a model is missing it says so and disables the button, because a mic button
 * that produces nothing is worse than no mic button.
 *
 * The conversation itself lives in `loop.ts` outside React. The panel is a
 * view over it, which is what keeps a live microphone from re-rendering the
 * room: the meter is read on a frame callback and the turns change a few
 * times per conversation.
 */

import { useEffect, useRef, useState } from "react";

import { useVoice } from "../voice/useVoice";
import { clearLoop, getLoopState, setWakeRequired, subscribeLoop, type LoopState } from "../voice/loop";
import type { VoiceState } from "../voice/store";
import "../styles/voice-loop.css";

const PHASE_TEXT: Record<VoiceState["phase"], string> = {
  off: "off",
  starting: "opening the microphone…",
  listening: "listening",
  hearing: "transcribing…",
  thinking: "thinking",
  speaking: "speaking",
  error: "stopped",
};

function LevelMeter({ getLevel }: { getLevel: () => number }) {
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let raf = 0;
    // Read straight from the mic on every frame instead of through React
    // state. This value changes many times a second and re-rendering the
    // panel to move a bar is the same mistake the pan store was extracted to
    // fix.
    const tick = () => {
      const el = barRef.current;
      if (el) {
        const level = getLevel();
        el.style.transform = `scaleX(${Math.min(1, level * 3.2).toFixed(3)})`;
        el.style.opacity = level > 0 ? "1" : "0.25";
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [getLevel]);

  return (
    <div className="voice-meter" aria-hidden="true">
      <div className="voice-meter-fill" ref={barRef} />
    </div>
  );
}

export function VoicePanel({ surfaceId }: { surfaceId: string }) {
  const { state, toggle, send, level, refresh, stop } = useVoice();
  const [draft, setDraft] = useState("");
  const [loop, setLoop] = useState<LoopState>(getLoopState());
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => subscribeLoop(setLoop), []);

  // Keep the newest turn in view, the way a real transcript does.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [loop.turns]);

  const live = state.phase !== "off" && state.phase !== "error";
  const canListen = state.hearable && state.phase !== "error";
  const busy = loop.phase !== "idle";

  return (
    <div className="voice-panel" data-surface={surfaceId}>
      <div className="voice-status">
        <div className="voice-capability">
          <span className={state.speakable ? "voice-cap voice-cap-yes" : "voice-cap voice-cap-no"}>
            speaks
          </span>
          <span className={state.hearable ? "voice-cap voice-cap-yes" : "voice-cap voice-cap-no"}>hears</span>
          <button type="button" className="voice-refresh" onClick={() => void refresh()} title="re-check what this machine can do">
            re-check
          </button>
        </div>
        {state.reason && <p className="voice-reason">{state.reason}</p>}
      </div>

      <div className="voice-controls">
        <button
          type="button"
          className={live ? "voice-mic voice-mic-live" : "voice-mic"}
          onClick={toggle}
          disabled={!canListen}
          title={canListen ? (live ? "stop listening" : "start listening") : "this machine has no speech recognition models"}
        >
          {live ? "stop listening" : "start listening"}
        </button>
        <LevelMeter getLevel={level} />
        <span className="voice-phase">{PHASE_TEXT[state.phase]}</span>
      </div>

      {/* The phase line, in words. The loop is the authority here rather than
          the voice store, because a turn started by typing has no microphone
          phase to show. */}
      <p className="voice-loopline" data-phase={loop.phase}>
        {loop.activity || PHASE_TEXT[state.phase]}
      </p>

      {busy && (
        <button type="button" className="voice-stop" onClick={stop}>
          stop talking
        </button>
      )}

      {state.error && <p className="voice-error">{state.error}</p>}

      <form
        className="voice-say"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          void send(draft.trim());
          setDraft("");
        }}
      >
        <input
          className="voice-input"
          value={draft}
          placeholder="say or type something to HERMUS"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="voice-send" disabled={busy || !draft.trim()}>
          send
        </button>
      </form>

      <label className="voice-wake-toggle" title="When on, a spoken message is only answered if it contains a wake word like 'Hermes'.">
        <input
          type="checkbox"
          checked={loop.wakeRequired}
          onChange={(event) => setWakeRequired(event.target.checked)}
        />
        <span>only answer when I say the wake word</span>
      </label>

      <div className="voice-convo">
        <div className="voice-convo-scroll" ref={scrollerRef}>
          {loop.turns.length === 0 && (
            <p className="voice-convo-empty">
              Nothing said yet. Start listening, or type below - both go to the same conversation.
            </p>
          )}
          {loop.turns.map((turn) => (
            <div className={`voice-turn voice-turn-${turn.role}`} key={turn.id}>
              <span className="voice-turn-who">
                {turn.role === "you" ? "you" : "hermus"}
                {turn.role === "you" && turn.source === "voice" && (
                  <em className="voice-turn-src" title="heard through the microphone">
                    spoken
                  </em>
                )}
              </span>
              <p className={turn.text ? "voice-turn-text" : "voice-turn-text voice-turn-empty"}>
                {turn.text || (turn.pending ? "…" : "")}
                {turn.error && <em className="voice-turn-error">{turn.error}</em>}
              </p>
            </div>
          ))}
        </div>

        {loop.turns.length > 0 && (
          <div className="voice-heard">
            <div className="voice-transcript-head">
              <span>heard</span>
              {state.wake.length > 0 && <span className="voice-wake">wake: {state.wake.join(", ")}</span>}
            </div>
            <p className={state.transcript ? "voice-text" : "voice-text voice-text-empty"}>
              {state.transcript || (live ? "nothing yet" : "not listening")}
            </p>
            <button type="button" className="voice-clear" onClick={() => clearLoop()}>
              clear conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
