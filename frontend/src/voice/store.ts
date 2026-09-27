/**
 * Voice state, kept outside React for the same reason pan is.
 *
 * A live microphone produces a level value several times a second. Routing
 * that through a store the room reads would re-render the orb, the backdrop
 * and every open panel just to move a ring — which is the exact failure the
 * pan extraction already fixed once. The level goes straight to a canvas and
 * everything else subscribes to a state that changes maybe a dozen times per
 * conversation.
 */

export type VoicePhase = "off" | "starting" | "listening" | "hearing" | "thinking" | "speaking" | "error";

export interface VoiceState {
  phase: VoicePhase;
  /** What the server said it can actually do. Null until /voice/status answers. */
  speakable: boolean;
  hearable: boolean;
  /** Why not, when it cannot. Written for a person, not for a log. */
  reason: string;
  /** Latest transcript, and the wake words found alongside it. */
  transcript: string;
  wake: string[];
  error: string;
  /** How many utterances have gone out. Cheap, and the only honest measure
   * of whether this thing is used at all. */
  turns: number;
}

const INITIAL: VoiceState = {
  phase: "off",
  speakable: false,
  hearable: false,
  reason: "",
  transcript: "",
  wake: [],
  error: "",
  turns: 0,
};

let state: VoiceState = INITIAL;
const subscribers = new Set<(s: VoiceState) => void>();

export function getVoiceState(): VoiceState {
  return state;
}

export function setVoiceState(patch: Partial<VoiceState>): void {
  const next = { ...state, ...patch };
  // A no-op write would still notify, and the level updates several times a
  // second — so only publish on a real change.
  let changed = false;
  for (const key of Object.keys(next) as Array<keyof VoiceState>) {
    if (next[key] !== state[key]) {
      changed = true;
      break;
    }
  }
  if (!changed) return;
  state = next;
  subscribers.forEach((fn) => fn(state));
}

export function subscribeVoice(fn: (s: VoiceState) => void): () => void {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function resetVoiceState(): void {
  state = INITIAL;
  subscribers.forEach((fn) => fn(state));
}

/**
 * Which state the orb should wear.
 *
 * Voice outranks the event tray. A room that is working on a task and is also
 * being spoken to should look like it is being spoken to — the mouth is the
 * more immediate fact, and a user watching an orb mid-task while a voice
 * answers them cannot tell which one it is attending to.
 */
export function voiceOverridesOrb(s: VoiceState): string | null {
  switch (s.phase) {
    case "listening":
      return "listening";
    case "hearing":
    case "thinking":
    case "starting":
      return "thinking";
    case "speaking":
      return "speaking";
    default:
      return null;
  }
}
