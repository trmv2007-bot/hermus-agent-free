/**
 * The conversation, in one place, outside React.
 *
 * The room is a voice conversation, which means one thing above all else:
 * a microphone is running. It produces an energy value every 21ms. Every
 * component that read that value through a store the room subscribes to
 * would re-render the orb, the backdrop and every open panel, several times
 * a second, forever. That mistake has already been made and undone twice in
 * this repository - the pan store and then the voice store were both
 * extracted to kill it - so this file follows the rule that both of them
 * established: rates that a human can perceive go straight to a canvas or a
 * callback, and only state that changes once per conversational turn enters a
 * store.
 *
 * So the split is this. `loop.ts` holds the turns, the phase, and the last
 * error: things that change a few times per conversation. The mic level does
 * not live here and must never be added, because the panel renders a
 * conversation, not a waveform.
 *
 * The other job here is the one the old `useVoice` could not do. It spoke
 * text and separately handed the text to a callback, which meant the voice
 * surface and the chat surface were two conversations that happened to be in
 * the same window. Here there is exactly one: a message goes in, an answer
 * comes back, the answer is spoken, and the same transcript is what the panel
 * renders. One loop, one history, one answer.
 */

import { speak as playAudio } from "./mic";
import { setVoiceState, type VoicePhase } from "./store";

// --------------------------------------------------------------------------
// shape
// --------------------------------------------------------------------------

export type LoopPhase = "idle" | "thinking" | "speaking";

export interface Turn {
  id: number;
  role: "you" | "hermus";
  text: string;
  /** True while the answer is still being read off the stream. */
  pending?: boolean;
  /** A failure, rendered in the bubble rather than swallowed. */
  error?: string;
  /** How this turn got here. Spoken turns are worth seeing apart from typed
   * ones: they are the ones the room chose to answer out loud. */
  source: "voice" | "typed";
}

export interface LoopState {
  turns: Turn[];
  phase: LoopPhase;
  /** One line, in words, naming what is happening. Never a spinner. */
  activity: string;
  error: string;
  /** Set when the user asked the loop to stop. */
  aborted: boolean;
  /**
   * Whether a transcript needs a wake word before it is answered.
   *
   * Off by default. A wake-gated room is correct for a device across the
   * room and wrong for someone sitting at a desk with a microphone two
   * inches from their mouth, and the panel exposes both rather than picking
   * for the user.
   */
  wakeRequired: boolean;
}

const INITIAL: LoopState = {
  turns: [],
  phase: "idle",
  activity: "",
  error: "",
  aborted: false,
  wakeRequired: false,
};

let state: LoopState = INITIAL;
const subscribers = new Set<(s: LoopState) => void>();

let counter = 0;
const nextId = () => ++counter;

/** Only a turn in flight aborts. */
let inFlight: AbortController | null = null;

// --------------------------------------------------------------------------
// the store
// --------------------------------------------------------------------------

export function getLoopState(): LoopState {
  return state;
}

export function setLoopState(patch: Partial<LoopState>): void {
  const next = { ...state, ...patch };
  // Same guard as the voice store: a no-op write must not notify, because
  // anything that subscribes here re-renders.
  let changed = false;
  for (const key of Object.keys(next) as Array<keyof LoopState>) {
    if (next[key] !== state[key]) {
      changed = true;
      break;
    }
  }
  if (!changed) return;
  state = next;
  subscribers.forEach((fn) => fn(state));
}

export function subscribeLoop(fn: (s: LoopState) => void): () => void {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function resetLoopState(): void {
  stopLoop();
  state = { ...INITIAL, turns: [] };
  subscribers.forEach((fn) => fn(state));
}

/** Drop history but keep the user's mode choices. */
export function clearLoop(): void {
  const { wakeRequired } = state;
  stopLoop();
  state = { ...INITIAL, turns: [], wakeRequired };
  subscribers.forEach((fn) => fn(state));
}

/**
 * Mirror the loop's phase into the voice store, which is what the orb reads.
 *
 * Two stores, one room: the loop owns the conversation, the voice store owns
 * how the room looks while it happens. Writing the orb's state from here
 * rather than from the panel is the point - the orb should react to a turn
 * that arrived from the microphone whether or not the voice panel is open.
 */
function toVoicePhase(phase: LoopPhase): VoicePhase | null {
  switch (phase) {
    case "thinking":
      return "thinking";
    case "speaking":
      return "speaking";
    default:
      return null;
  }
}

function setPhase(phase: LoopPhase, activity: string): void {
  setLoopState({ phase, activity, ...(phase === "idle" ? { error: "" } : {}) });
  const voicePhase = toVoicePhase(phase);
  if (voicePhase) setVoiceState({ phase: voicePhase });
}

// --------------------------------------------------------------------------
// history
// --------------------------------------------------------------------------

/**
 * The conversation so far, in the shape the chat route reads.
 *
 * Only completed, non-empty turns. Sending a pending or failed turn back to
 * the model teaches it that its own error messages were things the user said,
 * which is how a gateway ends up apologising for its own 503.
 */
export function loopHistory(): Array<{ role: "user" | "assistant"; content: string }> {
  return state.turns
    .filter((t) => !t.pending && !t.error && t.text.trim())
    .map((t) => ({
      role: t.role === "you" ? ("user" as const) : ("assistant" as const),
      content: t.text,
    }));
}

// --------------------------------------------------------------------------
// the model turn
// --------------------------------------------------------------------------

interface ChatFrame {
  event: string;
  data: Record<string, unknown>;
}

/**
 * Read one SSE stream to its end, reporting the final text.
 *
 * `onActivity` receives the progress frames so the panel can name what the
 * model is doing. The frames this endpoint sends are `status`, `final` and
 * `error`; anything else is ignored rather than guessed at, because an
 * unrecognised frame rendered as a progress line is a lie about progress.
 */
async function streamChat(
  message: string,
  history: Array<{ role: "user" | "assistant"; content: string }>,
  signal: AbortSignal,
  onActivity: (text: string) => void,
): Promise<string> {
  const res = await fetch("/api/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
    signal,
  });

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error ?? `the gateway returned HTTP ${res.status}`);
  }
  if (!res.body) throw new Error("the gateway sent no stream to read");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = "";

  const handle = (frame: ChatFrame) => {
    if (frame.event === "error") {
      throw new Error(String(frame.data.error ?? "the turn failed"));
    }
    if (frame.event === "final") {
      const content = frame.data.content;
      if (typeof content === "string" && content.trim()) final = content;
      return;
    }
    // The status frame carries a phase, not words. Showing the raw object
    // would put `{"phase": "thinking"}` in front of a person.
    const detail = frame.data.data as { label?: string; name?: string; tool?: string } | undefined;
    const what = detail?.label ?? detail?.name ?? detail?.tool;
    if (what) onActivity(String(what));
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // A frame ends at a blank line. Whatever follows the last one is a
    // partial frame and has to wait: parsing it early is how a streamed
    // answer loses its last word.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");

      if (!block.trim() || block.startsWith(":")) continue;
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
      } catch {
        // A frame that will not parse is not worth failing a turn over; the
        // final frame is authoritative and is the one that matters.
        continue;
      }
      handle({ event, data });
    }
  }

  if (!final.trim()) {
    throw new Error("the model finished without saying anything - check the main model in Settings");
  }
  return final;
}

// --------------------------------------------------------------------------
// the mouth
// --------------------------------------------------------------------------

/**
 * Speak text and resolve when the audio really finishes.
 *
 * `/voice/ack` is the route, not `/voice/say`. Both synthesize, but `say`
 * also queues the text as a *new* agent request and answers 202 with a job
 * id, which would run the whole conversation a second time. `ack` speaks one
 * piece of text and returns its audio url, which is exactly the job here.
 *
 * The `ended` event is the only honest finish signal. Resolving on a
 * duration estimate leaves the orb talking over the next question, which is
 * the detail that makes a voice assistant feel like a recording.
 */
export async function speakText(text: string): Promise<void> {
  const body = text.trim();
  if (!body) return;

  const res = await fetch("/voice/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: body }),
  });

  if (!res.ok) throw new Error(`could not synthesize speech: HTTP ${res.status}`);
  const data = (await res.json()) as { spoken?: boolean; audio_url?: string | null; error?: string };
  if (!data.spoken || !data.audio_url) {
    throw new Error(data.error ?? "this machine has no speech backend, so there was nothing to play");
  }

  await new Promise<void>((resolve, reject) => {
    playAudio(data.audio_url as string, resolve, (message) => reject(new Error(message)));
  });
}

// --------------------------------------------------------------------------
// the loop
// --------------------------------------------------------------------------

/**
 * One turn: show the words, get an answer, speak it, show the answer.
 *
 * Both bubbles exist before the request leaves. An empty assistant bubble is
 * the visual promise that something is coming, and a panel that shows the
 * question and then a blank space while the provider cold-starts reads as
 * broken even when it is working.
 */
export async function speakTo(text: string, source: "voice" | "typed" = "typed"): Promise<void> {
  const message = text.trim();
  if (!message) return;
  // One turn at a time. Two overlapping streams interleave their frames and
  // the second answer silently overwrites the first.
  if (inFlight) return;

  const youId = nextId();
  const hermusId = nextId();
  // History is read BEFORE the new turns are appended.
  //
  // The order matters more than it looks. `loopHistory` filters out pending
  // turns, so reading it after the append would still be correct for the
  // assistant bubble - but the new "you" turn is already in the list by then,
  // and the route appends the message itself, so the model would receive the
  // question twice: once in history and once as the message. Measured on a
  // real run, the third turn sent
  //   history: [user, user, user]  message: "..."
  // which is a conversation with no assistant in it at all.
  const history = loopHistory();
  setLoopState({
    error: "",
    aborted: false,
    turns: [
      ...state.turns,
      { id: youId, role: "you", text: message, source },
      { id: hermusId, role: "hermus", text: "", pending: true, source },
    ],
  });
  setPhase("thinking", "thinking…");

  const controller = new AbortController();
  inFlight = controller;
  const patchHermus = (patch: Partial<Turn>) =>
    setLoopState({
      turns: getLoopState().turns.map((t) => (t.id === hermusId ? { ...t, ...patch } : t)),
    });

  try {
    const answer = await streamChat(message, history, controller.signal, (activity) =>
      setLoopState({ activity }),
    );
    // `pending` is cleared here, not in the finally block: the answer is
    // complete at this point, and leaving it set made every answered turn
    // render as still-arriving.
    patchHermus({ text: answer, pending: false });

    setPhase("speaking", "speaking…");
    try {
      await speakText(answer);
    } catch (err) {
      // The answer is real and on screen. A machine that cannot speak must
      // not also pretend the answer failed, so this is reported on the turn
      // and the turn keeps its text.
      patchHermus({ error: `the answer is here but could not be spoken: ${(err as Error).message}` });
    }
  } catch (err) {
    const aborted = (err as Error).name === "AbortError";
    patchHermus({
      pending: false,
      error: aborted ? undefined : (err as Error).message,
      // A visible placeholder, so a failed turn is not indistinguishable from
      // a model that chose to say nothing.
      text: aborted ? "" : "-",
    });
    if (!aborted) setLoopState({ error: (err as Error).message });
  } finally {
    inFlight = null;
    const turn = getLoopState().turns.find((t) => t.id === hermusId);
    setPhase("idle", "");
    if (turn?.error) {
      setLoopState({ error: turn.error });
      setVoiceState({ phase: "error", error: turn.error });
    }
  }
}

/** Abort a running turn. The question stays, the answer is marked stopped. */
export function stopLoop(): void {
  inFlight?.abort();
  inFlight = null;
  setLoopState({ aborted: true });
}

export function setWakeRequired(required: boolean): void {
  setLoopState({ wakeRequired: required });
}

/**
 * What a fresh transcript should do.
 *
 * A wake word is required only in the mode the user asked for. The wake
 * keyword itself is left in the text on purpose: the model handles "Hermes,
 * what time is it" perfectly well, and stripping it risks eating a real first
 * word when the keyword and the sentence run together ("hermes and what is
 * the time" is not a phrase anyone says, and the failure mode of being
 * wrong here is a mangled request).
 */
export function shouldAutoAnswer(text: string, wake: string[]): boolean {
  if (!text.trim()) return false;
  if (getLoopState().wakeRequired) return wake.length > 0;
  return true;
}
