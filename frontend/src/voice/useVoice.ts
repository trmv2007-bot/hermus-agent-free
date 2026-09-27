/**
 * The microphone, and the wiring from a transcript to an answer.
 *
 * This used to own the whole voice loop, which meant speaking and thinking
 * were two separate paths that only happened to sit in the same panel. The
 * conversation now lives in `loop.ts`, and this file is left with what is
 * genuinely a microphone concern: capability, capture, transcription, and the
 * decision about whether a fresh transcript should be answered out loud.
 *
 * The order of operations is the whole design, and it is not the obvious one.
 * Listen -> transcribe -> answer -> speak. The answer is obtained first and
 * spoken second, because the spoken part is the only part the user waits on
 * at the end, and answering through the same chat route the text panel uses
 * means a spoken question and a typed one are one conversation rather than
 * two.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { createVoiceEar, type VoiceEar } from "./mic";
import { shouldAutoAnswer, speakTo, stopLoop } from "./loop";
import { getVoiceState, setVoiceState, subscribeVoice, type VoiceState } from "./store";

interface StatusPayload {
  enabled?: boolean;
  speakable?: boolean;
  ears?: { available?: boolean; reason?: string };
  tts?: { available?: boolean; backends?: Record<string, { available?: boolean }> };
}

interface HearPayload {
  ok?: boolean;
  text?: string;
  wake?: { keyword?: string }[];
  error?: string;
}

export interface VoiceApi {
  state: VoiceState;
  toggle: () => void;
  send: (text: string) => Promise<void>;
  level: () => number;
  refresh: () => Promise<void>;
  stop: () => void;
}

async function readStatus(): Promise<StatusPayload> {
  const res = await fetch("/voice/status");
  if (!res.ok) throw new Error(`/voice/status returned ${res.status}`);
  return (await res.json()) as StatusPayload;
}

export function useVoice(): VoiceApi {
  const [state, setState] = useState<VoiceState>(getVoiceState());
  const earRef = useRef<VoiceEar | null>(null);
  /** A transcription already in flight, so two utterances never race. */
  const hearingRef = useRef(false);

  useEffect(() => subscribeVoice(setState), []);

  const refresh = useCallback(async () => {
    try {
      const payload = await readStatus();
      // Speakable and hearable are different questions and are answered by
      // different subsystems. Reporting one as a proxy for the other is how a
      // room ends up with a dead microphone behind a button that works.
      const ttsBackends = payload.tts?.backends ?? {};
      const speakable =
        payload.speakable === true || Object.values(ttsBackends).some((b) => b?.available === true);
      const hearable = payload.ears?.available === true;
      setVoiceState({
        speakable,
        hearable,
        reason: payload.ears?.reason ?? "",
      });
    } catch (err) {
      setVoiceState({
        speakable: false,
        hearable: false,
        reason: `could not reach the gateway: ${(err as Error).message}`,
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const stopListening = useCallback(() => {
    // A running turn has to be stopped too, or the room goes quiet with the
    // microphone off and then says an answer to nobody.
    stopLoop();
    earRef.current?.stop();
    earRef.current = null;
    hearingRef.current = false;
    const current = getVoiceState();
    if (current.phase === "listening" || current.phase === "hearing" || current.phase === "starting") {
      setVoiceState({ phase: "off" });
    }
  }, []);

  const startListening = useCallback(async () => {
    if (earRef.current?.active) return;
    setVoiceState({ phase: "starting", error: "" });

    const ear = createVoiceEar();
    if (ear.unsupported) {
      setVoiceState({ phase: "error", error: ear.unsupported });
      return;
    }
    earRef.current = ear;

    ear.onError = (message) => {
      earRef.current = null;
      hearingRef.current = false;
      setVoiceState({ phase: "error", error: message });
    };

    // A whole utterance arrived, already endpointed by the mic. One at a
    // time: two overlapping POSTs would interleave transcripts and the
    // second would silently win.
    ear.onChunk = async (audio, sampleRate) => {
      if (hearingRef.current) return;
      hearingRef.current = true;
      setVoiceState({ phase: "hearing" });
      try {
        const res = await fetch("/voice/hear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audio, sample_rate: sampleRate }),
        });
        const data = (await res.json()) as HearPayload;
        if (!res.ok || !data.ok) {
          setVoiceState({
            phase: "error",
            error: data.error ?? `the gateway could not hear that (${res.status})`,
          });
          return;
        }
        const text = (data.text ?? "").trim();
        const wake = (data.wake ?? []).map((w) => w.keyword ?? "").filter(Boolean);
        setVoiceState({ transcript: text, wake, error: "" });

        if (!text) return;
        // A wake-gated room shows the words and waits for the keyword. An open
        // one answers straight away.
        if (!shouldAutoAnswer(text, wake)) {
          setVoiceState({ phase: "listening" });
          return;
        }
        await speakTo(text, "voice");
      } catch (err) {
        setVoiceState({ phase: "error", error: `transcription failed: ${(err as Error).message}` });
      } finally {
        hearingRef.current = false;
        // The loop owns the phase from here: it is thinking or speaking, and
        // writing "listening" over that would make the orb appear to stop
        // listening in the middle of an answer.
        if (getVoiceState().phase === "hearing") setVoiceState({ phase: "listening" });
      }
    };

    await ear.start();
    if (getVoiceState().phase === "starting") setVoiceState({ phase: "listening" });
  }, []);

  const toggle = useCallback(() => {
    const current = getVoiceState();
    if (current.phase === "off" || current.phase === "error") void startListening();
    else stopListening();
  }, [startListening, stopListening]);

  // Typed input goes through the same loop as spoken input, so the history
  // the model sees is one conversation rather than two half-conversations.
  const send = useCallback(async (text: string) => {
    await speakTo(text, "typed");
  }, []);

  useEffect(
    () => () => {
      earRef.current?.stop();
      stopLoop();
    },
    [],
  );

  return { state, toggle, send, level: () => earRef.current?.level() ?? 0, refresh, stop: stopLoop };
}
