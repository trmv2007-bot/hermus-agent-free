/**
 * One hook that owns the whole voice loop: capability, microphone, transcript,
 * and speaking. The panel is a view over this; the orb reads the store.
 *
 * The order of operations in `talk` is the whole design, and it is not the
 * obvious one. Listen -> transcribe -> acknowledge -> act. The acknowledgement
 * is what makes the assistant feel fast, and the persona research is
 * unambiguous that a *voice* filler plus a gesture moves perceived response
 * time while an animated spinner does not. So the filler is spoken the moment
 * the words are known, and the slow part happens while it is still talking.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { createVoiceEar, speak as playAudio, type VoiceEar } from "./mic";
import { getVoiceState, setVoiceState, subscribeVoice, type VoiceState } from "./store";

interface StatusPayload {
  enabled?: boolean;
  speakable?: boolean;
  ears?: { available?: boolean; reason?: string };
  tts?: { available?: boolean; backends?: Record<string, { available?: boolean }> };
}

export interface VoiceApi {
  state: VoiceState;
  toggle: () => void;
  send: (text: string) => Promise<void>;
  level: () => number;
  refresh: () => Promise<void>;
}

async function readStatus(): Promise<StatusPayload> {
  const res = await fetch("/voice/status");
  if (!res.ok) throw new Error(`/voice/status returned ${res.status}`);
  return (await res.json()) as StatusPayload;
}

export function useVoice(onCommand?: (text: string) => void): VoiceApi {
  const [state, setState] = useState<VoiceState>(getVoiceState());
  const earRef = useRef<VoiceEar | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cmdRef = useRef(onCommand);
  cmdRef.current = onCommand;

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
    earRef.current?.stop();
    earRef.current = null;
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
      setVoiceState({ phase: "error", error: message });
    };

    ear.onChunk = async (audio, sampleRate) => {
      // One utterance at a time. Two overlapping POSTs would interleave
      // transcripts and the second would silently win.
      if (getVoiceState().phase === "hearing" || getVoiceState().phase === "thinking") return;
      setVoiceState({ phase: "hearing" });
      try {
        const res = await fetch("/voice/hear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audio, sample_rate: sampleRate }),
        });
        const data = (await res.json()) as { ok?: boolean; text?: string; wake?: { keyword: string }[]; error?: string };
        if (!res.ok || !data.ok) {
          setVoiceState({ phase: "error", error: data.error ?? `the gateway could not hear that (${res.status})` });
          return;
        }
        const text = (data.text ?? "").trim();
        const wake = (data.wake ?? []).map((w) => w.keyword);
        setVoiceState({ transcript: text, wake, phase: "listening" });
        if (text) cmdRef.current?.(text);
      } catch (err) {
        setVoiceState({ phase: "error", error: `transcription failed: ${(err as Error).message}` });
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

  const say = useCallback(async (text: string) => {
    if (!text.trim()) return;
    setVoiceState({ phase: "speaking", error: "" });
    try {
      // The ack route, not a bare TTS call: it speaks immediately and queues
      // the real work, which is what makes the reply land in well under a
      // second instead of after a full agent turn.
      const res = await fetch("/voice/ack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        setVoiceState({ phase: "error", error: `could not speak: HTTP ${res.status}` });
        return;
      }
      const data = (await res.json()) as { audio_url?: string; spoken?: boolean; error?: string };
      if (!data.spoken || !data.audio_url) {
        setVoiceState({
          phase: "error",
          error: data.error ?? "this machine has no speech backend, so there was nothing to play",
        });
        return;
      }
      const audio = playAudio(
        data.audio_url,
        () => setVoiceState({ phase: earRef.current?.active ? "listening" : "off" }),
        (message) => setVoiceState({ phase: "error", error: message }),
      );
      audioRef.current = audio;
      setVoiceState({ turns: getVoiceState().turns + 1 });
    } catch (err) {
      setVoiceState({ phase: "error", error: `speech failed: ${(err as Error).message}` });
    }
  }, []);

  const send = useCallback(
    async (text: string) => {
      await say(text);
      cmdRef.current?.(text);
    },
    [say],
  );

  useEffect(
    () => () => {
      earRef.current?.stop();
      audioRef.current?.pause();
    },
    [],
  );

  return { state, toggle, send, level: () => earRef.current?.level() ?? 0, refresh };
}
