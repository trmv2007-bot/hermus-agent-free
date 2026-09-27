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
  /**
   * Called from the audio callback with the current RMS.
   *
   * This exists so the meter can be fed at 48Hz without the panel polling at
   * 48Hz. The meter reads a value the mic hands it, rather than the mic
   * pushing a value into a store the room subscribes to.
   */
  setLevel: ((rms: number) => void) | null;
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
const PROCESSOR_FRAMES = 2048;
/** Analysis hop, in 16kHz samples. 256 is 16ms, short enough to place an
 * endpoint precisely and long enough that the RMS is not noise. */
const FRAME_SAMPLES = 256;
const EMPTY = new Float32Array(0);

// --------------------------------------------------------------------------
// endpointing
//
// These constants are the ones that shipped, and they were measured against
// this machine's real room tone and real piper speech rather than guessed.
// A quiet room sits at RMS 0.004; speech on this box runs 0.15 to 0.30.
//
// The design point is that a chunk has to contain a whole sentence. The
// recogniser builds a fresh sherpa stream per request, so a sequence of
// requests is N independent utterances, not one: feeding "Hey Hermes, what
// is two plus two" in 0.5s slices returned "PAY WHAT IS PL" with no wake
// hit. The same audio sent as one 2.5s utterance returned "KAY HERMES WHAT
// IS TOO PLUS TOO" and the wake word with it. Fixed-rate chunking does not
// degrade gracefully here, it destroys the request.
// --------------------------------------------------------------------------

/** Enter speech above this. Above room tone by ~9x. */
const SPEECH_ON = 0.035;
/** Stay in speech until below this. Hysteresis, so a dip between two words
 * does not endpoint a sentence in half. */
const SPEECH_OFF = 0.022;
/** Shorter than this is a cough or a desk bump, not a request. */
const MIN_SPEECH_SECONDS = 0.3;
/** This much quiet ends the turn. Long enough to survive a thinking pause
 * mid-sentence, short enough that the room does not feel stuck. */
const MIN_SILENCE_SECONDS = 0.45;
/** Hard cap, because a continuous noise source must not hold the buffer
 * forever. */
const MAX_UTTERANCE_SECONDS = 12;
/** Keep this much of the trailing quiet. The last phoneme decays over tens of
 * milliseconds and cutting it costs the final word every time. */
const TAIL_SECONDS = 0.25;
/** Bounded pre-roll, so a minute of silence is not posted as audio. */
const MAX_PREROLL_SECONDS = 1;

export function createVoiceEar(): VoiceEar {
  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let processor: ScriptProcessorNode | null = null;
  let sink: GainNode | null = null;
  let active = false;
  // Holds the un-flushed tail so stop() can push it out.
  let flushRef: { current: (() => void) | null } = { current: null };
  // The latest level, handed over rather than polled from a render tree.
  const levelRef = { value: 0 };

  const unsupported =
    typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia
      ? "this browser exposes no microphone API"
      : typeof AudioContext === "undefined" && typeof (globalThis as { webkitAudioContext?: unknown }).webkitAudioContext === "undefined"
        ? "this browser has no Web Audio"
        : null;

  const ear: VoiceEar = {
    onChunk: null,
    onError: null,
    setLevel: null,
    unsupported,

    get active() {
      return active;
    },

    level() {
      return levelRef.value;
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

      const bufferSize = PROCESSOR_FRAMES;
      processor = ctx.createScriptProcessor(bufferSize, 1, 1);

      // The utterance being assembled, and where the speech in it starts.
      let utterance: Float32Array = EMPTY;
      let inSpeech = false;
      let speechSamples = 0;
      let quietSamples = 0;
      // Trailing quiet, kept so the final phoneme is not clipped.
      let tail: Float32Array = EMPTY;

      const emit = (floats: Float32Array) => {
        if (!floats.length) return;
        const out = new Int16Array(floats.length);
        for (let i = 0; i < floats.length; i++) {
          const s = Math.max(-1, Math.min(1, floats[i]));
          out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        ear.onChunk?.(toBase64(out), TARGET_RATE);
      };

      // Copy on write.
      //
      // Returning `b` when `a` is empty hands back a subarray VIEW of the
      // caller's buffer. That buffer is reused on the next callback, so the
      // stored "utterance" would be silently rewritten underneath the
      // segmenter. Every returned array is therefore a real copy.
      const EMPTY_ARR = new Float32Array(0);
      const concat = (a: Float32Array, b: Float32Array): Float32Array => {
        if (!a.length) return b.length ? b.slice() : EMPTY_ARR;
        if (!b.length) return a;
        const out = new Float32Array(a.length + b.length);
        out.set(a, 0);
        out.set(b, a.length);
        return out;
      };

      const resetUtterance = () => {
        utterance = EMPTY_ARR;
        tail = EMPTY_ARR;
        inSpeech = false;
        speechSamples = 0;
        quietSamples = 0;
      };

      // Post the utterance that has just been assembled, and start a fresh
      // one. The payload is built BEFORE the reset, because the reset is what
      // clears the buffer being sent.
      const endpoint = () => {
        const tailSamples = Math.floor(TAIL_SECONDS * TARGET_RATE);
        const keep = tail.length > tailSamples ? tail.subarray(tail.length - tailSamples) : tail;
        const payload = concat(utterance, keep);
        const worthSending = speechSamples >= MIN_SPEECH_SECONDS * TARGET_RATE;
        resetUtterance();
        if (worthSending) emit(payload);
      };

      // Resample to 16k by linear interpolation.
      const ratio = inputRate / TARGET_RATE;
      const resample = (input: Float32Array): Float32Array => {
        const produced = Math.floor(input.length / ratio);
        const out = new Float32Array(produced);
        for (let i = 0; i < produced; i++) {
          const pos = i * ratio;
          const lo = Math.floor(pos);
          const frac = pos - lo;
          const a = input[lo] ?? 0;
          const b = input[lo + 1] ?? a;
          out[i] = a + (b - a) * frac;
        }
        return out;
      };

      processor.onaudioprocess = (event) => {
        if (!ctx) return;
        const input = event.inputBuffer.getChannelData(0);
        const resampled = resample(input);
        if (!resampled.length) return;

        // Step by the frame width, not by one sample.
        //
        // Overlapping windows here would count the same sample once per
        // overlap, so the buffer would grow ~256x faster than the audio
        // arriving: the 12s cap was reached after roughly 50ms of real sound,
        // and the endpoint fired on a fraction of a sentence. The analysis
        // frame is a real hop.
        for (let i = 0; i < resampled.length; i += FRAME_SAMPLES) {
          const frame = resampled.subarray(i, Math.min(i + FRAME_SAMPLES, resampled.length));
          let sum = 0;
          for (let j = 0; j < frame.length; j++) sum += frame[j] * frame[j];
          const rms = Math.sqrt(sum / frame.length);
          // Room tone removal for the meter only. The endpointing thresholds
          // are absolute, so the ring and the segmenter cannot disagree
          // about what counts as silence.
          const shown = rms < 0.02 ? 0 : rms;
          levelRef.value = shown;
          ear.setLevel?.(shown);

          utterance = concat(utterance, frame);

          if (inSpeech) {
            if (rms >= SPEECH_OFF) {
              quietSamples = 0;
              tail = EMPTY;
            } else {
              quietSamples += frame.length;
              tail = concat(tail, frame);
            }
            if (
              quietSamples >= MIN_SILENCE_SECONDS * TARGET_RATE ||
              utterance.length >= MAX_UTTERANCE_SECONDS * TARGET_RATE
            ) {
              endpoint();
            }
          } else if (rms >= SPEECH_ON) {
            speechSamples += frame.length;
            if (speechSamples >= MIN_SPEECH_SECONDS * TARGET_RATE) {
              inSpeech = true;
              // Everything from the start of the burst is kept, not from
              // here: the first 300ms is the consonant that begins the word.
              quietSamples = 0;
              tail = EMPTY;
            }
          } else {
            speechSamples = 0;
            // Bound the pre-roll so a long silence is never posted as audio.
            const maxPre = Math.floor(MAX_PREROLL_SECONDS * TARGET_RATE);
            if (utterance.length > maxPre) {
              utterance = concat(EMPTY, utterance.subarray(utterance.length - maxPre));
            }
          }
        }
      };

      // Flush the tail on stop, or the last half-second of every utterance is
      // discarded - which is exactly where the final word lives.
      flushRef.current = () => {
        if (inSpeech && speechSamples >= MIN_SPEECH_SECONDS * TARGET_RATE) {
          emit(concat(utterance, tail));
        }
        resetUtterance();
      };

      source.connect(processor);
      processor.connect(sink);
      active = true;
    },

    stop() {
      active = false;
      levelRef.value = 0;
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
