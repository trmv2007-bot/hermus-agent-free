/**
 * Making streamed text land like speech rather than like a paste.
 *
 * Tokens arrive from the provider in clumps. A clump of twelve words followed
 * by a two-second pause, then another clump, reads as a download progress bar
 * with letters in it. The text is identical either way; what changes is
 * whether the room sounds like it is thinking in sentences.
 *
 * Two small corrections, both cheap:
 *
 *   - A queue that releases on a steady clock rather than as fast as chunks
 *     arrive. Bursts are spread out, and the gaps between them stop being
 *     ragged.
 *   - A very faint tick every few tokens. Not a click per token -- that is a
 *     Geiger counter and it becomes unbearable within a paragraph -- but
 *     something every few words, low enough to feel rather than hear.
 *
 * The reason this lives in a pure module is that a cadence is easy to get
 * subtly wrong: a queue that never drains, a tick that fires on an empty
 * stream, or a release rate that is faster than the source and therefore does
 * nothing. Each of those is a test rather than a thing to eyeball.
 */

export interface Cadence {
  /** Text waiting to be revealed. */
  queue: string;
  /** Whole chunks already shown. */
  shown: string;
  /** How much to release this frame, in characters. */
  release: number;
  /** Token count since the last tick. */
  sinceTick: number;
  /**
   * Fractional characters carried to the next frame.
   *
   * Without this, flooring each frame's release makes the reveal rate depend
   * on the frame rate: at 120Hz a frame earns 1.52 characters and gets 1, so
   * the same answer takes twice as long to read on a faster display. Half a
   * character is not a thing that can be shown, but it is a thing that has
   * been earned, and dropping it every frame is a real speed difference.
   */
  carry: number;
  /** True when there is genuinely more to show. */
  pending: boolean;
}

export const DEFAULT_CHARS_PER_SEC = 190;
/** Roughly a syllable every few words: present, but not countable. */
export const TOKENS_PER_TICK = 5;

export function initialCadence(): Cadence {
  return { queue: "", shown: "", release: 0, sinceTick: 0, carry: 0, pending: false };
}

/**
 * Push a chunk in. Never releases anything here -- release is a function of
 * elapsed time, so a provider that dumps the whole answer at once still gets
 * read at a human rate instead of appearing instantly.
 */
export function push(c: Cadence, chunk: string): Cadence {
  if (!chunk) return c;
  return { ...c, queue: c.queue + chunk, pending: true };
}

/**
 * Advance by `dtMs`. `budget` is the same length as `shown`, counted per
 * chunk that was pushed, so the release rate is a property of the model rather
 * than of however fast the socket happened to be.
 */
export function advance(c: Cadence, dtMs: number, charsPerSec = DEFAULT_CHARS_PER_SEC): Cadence {
  if (!c.queue) return { ...c, release: 0, pending: false };
  const earned = Math.max(0, (charsPerSec * dtMs) / 1000) + c.carry;
  const take = Math.min(c.queue.length, Math.floor(earned));
  return {
    ...c,
    shown: c.shown + c.queue.slice(0, take),
    queue: c.queue.slice(take),
    release: take,
    carry: earned - take,
    pending: true,
  };
}

/** True when everything pushed has been shown. */
export function isDrained(c: Cadence): boolean {
  return c.queue.length === 0;
}

/**
 * Words revealed, for the tick counter. Counting on word boundaries rather
 * than characters means the tick does not land mid-word, which is audible in
 * a way that is hard to name.
 */
export function wordsShown(c: Cadence): number {
  const m = c.shown.match(/\S+/g);
  return m ? m.length : 0;
}

export interface TickResult {
  cadence: Cadence;
  tick: boolean;
}

/** Fire at most one tick per call, however many words appeared. */
export function maybeTick(c: Cadence, every = TOKENS_PER_TICK): TickResult {
  const words = wordsShown(c);
  const fired = words > 0 && words % every === 0 && c.sinceTick < every;
  return { cadence: { ...c, sinceTick: fired ? 0 : c.sinceTick }, tick: fired };
}

/** Reset for a new turn. */
export function resetCadence(): Cadence {
  return initialCadence();
}
