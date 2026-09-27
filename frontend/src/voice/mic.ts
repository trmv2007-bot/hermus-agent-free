/**
 * The ears and the mouth, in the browser.
 *
 * Capture is done with raw Web Audio and a ScriptProcessor rather than
 * MediaRecorder, for one reason that matters: the recogniser wants 16-bit
 * mono PCM at 16kHz, and MediaRecorder's only portable output is a compressed
 * container that the browser will not let you reshape. Converting Opus to PCM
 * to re-encode to PCM costs more than the round trip it was meant to save, and
 * a ScriptProcessor gives exactly the samples the model wants with no
 * conversion at all.
 *
 * ScriptProcessor is deprecated in favour of AudioWorklet. That is real, and
 * it is not why this is here: an AudioWorklet needs a separate module file
 * served over HTTP, which does not survive being dropped into a page as a
 * single bundle, and the whole point of this file is that it is one file with
 * no build step of its own. Revisit when the surface has somewhere to put a
 * worklet.
 *
 * Everything is a plain callback rather than a framework store, because the
 * only consumer is the orb and the orb already has a state machine. Routing
 * microphone audio through the render tree would re-render the whole room
 * several times a second, which is the same mistake the pan store was
 * extracted to fix.
 */

export interface VoiceEar {
  /** RMS of the current buffer, 0..1. Drives the orb's listening ring. */
  level(): number;
  /** Start listening. Resolves once the mic is actually live, not when asked. */
  start(): Promise<void>;
  stop(): void;
  /** True while audio is arriving. */
  readonly active: boolean;
  /** Called with each captured chunk as base64 16-bit mono PCM at 16kHz. */
  onChunk: ((base64: string, sampleRate: number) => void) | null;
  /** Mic refused or died. The message is safe to show. */
  onError: ((message: string) => void) | null;
  /** Browser cannot do this at all. Set once, before start(). */
  readonly unsupported: string | null;
}

const TARGET_RATE = 16000;
const CHUNK_SECONDS = 0.5; // long enough for the KWS to confirm, short enough
// to feel like a turn rather than a pause

export function createVoiceEar(): VoiceEar {
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;
  let sink: GainNode | null = null;
  let active = false;
  let rms = 0;
  // Holds the un-flushed tail so stop() can push it out.
  let flushRef: { current: (() => void) | null } = { current: null };

  const unsupported =
    typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia
      ? "this browser exposes no microphone API"
      : typeof AudioContext === "undefined" && typeof (globalThis as { webkitAudioContext?: unknown }).webkitAudioContext === "undefined"
        ? "this browser has no Web Audio"
        : null;

  const ear: VoiceEar = {
    onChunk: null,
    onError: null,
    unsupported,

    get active() {
      return active;
    },

    level() {
      return rms;
    },

    async start() {
      if (active) return;
      if (unsupported) {
        ear.onError?.(unsupported);
        return;
      }
      try {
        // 16kHz mono is requested directly. Browsers are honest about echo
        // cancellation being wanted, and it is the one constraint worth
        // keeping: without it a voice assistant hears itself speak.
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (err) {
        // These are the two failures a user will actually hit, and they mean
        // different things: NotAllowed is a decision, NotFound is a fact.
        const name = (err as { name?: string })?.name ?? "Error";
        const message =
          name === "NotAllowedError"
            ? "microphone permission was denied"
            : name === "NotFoundError"
              ? "no microphone is attached to this machine"
              : `microphone failed to open: ${name}`;
        ear.onError?.(message);
        return;
      }

      const Ctor =
        (globalThis as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext })
          .AudioContext ??
        (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) {
        ear.onError?.("this browser has no Web Audio");
        return;
      }
      ctx = new Ctor();
      // A context created before a user gesture starts suspended and never
      // produces samples. Opening the mic is that gesture, so resume now.
      if (ctx.state === "suspended") await ctx.resume();

      const inputRate = ctx.sampleRate;
      source = ctx.createMediaStreamSource(stream);

      // Gain of zero: a ScriptProcessor is only pulled when it is connected to
      // the graph, but connecting it to the speakers would be a feedback loop
      // through a room full of speakers. This keeps it running and silent.
      sink = ctx.createGain();
      sink.gain.value = 0;
      sink.connect(ctx.destination);

      const bufferSize = 2048;
      processor = ctx.createScriptProcessor(bufferSize, 1, 1);

      let pending: Float32Array = new Float32Array(0);
      const wantSamples = Math.round(TARGET_RATE * CHUNK_SECONDS);

      const emit = (floats: Float32Array) => {
        if (!floats.length) return;
        const out = new Int16Array(floats.length);
        for (let i = 0; i < floats.length; i++) {
          const s = Math.max(-1, Math.min(1, floats[i]));
          out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        ear.onChunk?.(toBase64(out), TARGET_RATE);
      };

      processor.onaudioprocess = (event) => {
        if (!ctx) return;
        const input = event.inputBuffer.getChannelData(0);

        // Level, from the source rate. Cheap and monotonic; it is a visual,
        // not a measurement, and averaging squares is enough.
        let sum = 0;
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
        rms = Math.sqrt(sum / input.length);
        // Noise floor removal. A silent room sits around 0.005 and would
        // otherwise light the ring permanently.
        if (rms < 0.02) rms = 0;

        // Resample to 16k by linear interpolation.
        const ratio = inputRate / TARGET_RATE;
        const produced = Math.floor(input.length / ratio);
        const resampled = new Float32Array(produced);
        for (let i = 0; i < produced; i++) {
          const pos = i * ratio;
          const lo = Math.floor(pos);
          const frac = pos - lo;
          const a = input[lo] ?? 0;
          const b = input[lo + 1] ?? a;
          resampled[i] = a + (b - a) * frac;
        }

        const merged = new Float32Array(pending.length + resampled.length);
        merged.set(pending, 0);
        merged.set(resampled, pending.length);
        pending = merged;

        // Accumulate to an utterance-sized chunk before sending.
        //
        // This is not an optimisation. A ScriptProcessor fires onaudioprocess
        // once per buffer — at 48kHz with 2048 frames that is every 43ms, so
        // emitting per callback would POST to /voice/hear twenty-three times
        // a second, each request carrying a fragment too short to contain a
        // word. The recogniser accumulates internally; the transport has to
        // as well, or the model never sees a whole sound.
        while (pending.length >= wantSamples) {
          emit(pending.subarray(0, wantSamples));
          pending = pending.slice(wantSamples);
        }
      };

      // Flush the tail on stop, or the last half-second of every utterance is
      // discarded — which is exactly where the final word lives.
      flushRef.current = () => {
        if (pending.length) {
          emit(pending);
          pending = new Float32Array(0);
        }
      };

      source.connect(processor);
      processor.connect(sink);
      active = true;
    },

    stop() {
      active = false;
      rms = 0;
      // Best-effort: a tail that cannot be flushed shortens one transcript,
      // it does not wedge teardown.
      try {
        flushRef.current?.();
      } catch {
        // Intentionally ignored.
      }
      flushRef.current = null;
      try {
        processor?.disconnect();
        source?.disconnect();
        sink?.disconnect();
      } catch {
        // Disconnecting an already-torn-down node is not interesting.
      }
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close().catch(() => undefined);
      processor = null;
      source = null;
      sink = null;
      stream = null;
      ctx = null;
    },
  };

  return ear;
}

/** Base64 without pulling in a Buffer, which does not exist in a browser. */
function toBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  let binary = "";
  const CHUNK = 0x8000; // avoid blowing the argument limit on a long buffer
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/**
 * Play synthesized audio and report when it really finishes.
 *
 * `ended` is the only trustworthy signal. A duration estimate is a guess, and
 * an orb that keeps its mouth moving after the audio stopped is the kind of
 * detail that makes an assistant feel like a recording.
 */
export function speak(
  url: string,
  onEnd?: () => void,
  onError?: (message: string) => void,
): HTMLAudioElement {
  const audio = new Audio(url);
  audio.onended = () => onEnd?.();
  audio.onerror = () => onError?.("synthesized audio could not be played");
  audio.play().catch((err: unknown) => {
    onError?.(`playback was blocked: ${(err as Error)?.message ?? "unknown"}`);
  });
  return audio;
}
