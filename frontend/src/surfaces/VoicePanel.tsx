/**
 * The voice surface: a real microphone, a real transcript, and an honest
 * statement of what this machine can and cannot do.
 *
 * The rule this panel exists to honour: nothing here is decorative. The level
 * meter is measured RMS, the transcript is whatever the recogniser returned
 * including the empty string, and the capability block is a direct read of
 * what the gateway said it loaded. When a model is missing it says so and
 * disables the button, because a mic button that produces nothing is worse
 * than no mic button.
 */

import { useEffect, useRef, useState } from "react";

import { useVoice } from "../voice/useVoice";
import type { VoiceState } from "../voice/store";

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
  const { state, toggle, send, level, refresh } = useVoice();
  const [draft, setDraft] = useState("");

  const live = state.phase !== "off" && state.phase !== "error";
  const canListen = state.hearable && state.phase !== "error";

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

      {state.error && <p className="voice-error">{state.error}</p>}

      <form
        className="voice-say"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim() || !state.speakable) return;
          void send(draft.trim());
          setDraft("");
        }}
      >
        <input
          className="voice-input"
          value={draft}
          placeholder={state.speakable ? "say something to HERMUS" : "no speech backend on this machine"}
          onChange={(event) => setDraft(event.target.value)}
          disabled={!state.speakable}
        />
        <button type="submit" className="voice-send" disabled={!state.speakable || !draft.trim()}>
          speak
        </button>
      </form>

      <div className="voice-transcript">
        <div className="voice-transcript-head">
          <span>heard</span>
          {state.wake.length > 0 && <span className="voice-wake">wake: {state.wake.join(", ")}</span>}
        </div>
        <p className={state.transcript ? "voice-text" : "voice-text voice-text-empty"}>
          {state.transcript || (live ? "nothing yet" : "not listening")}
        </p>
      </div>

      {state.turns > 0 && <p className="voice-turns">{state.turns} spoken</p>}
    </div>
  );
}
