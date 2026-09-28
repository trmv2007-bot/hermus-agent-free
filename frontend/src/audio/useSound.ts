/**
 * Wires the synthesiser to the things that actually happen.
 *
 * One hook, installed once at the shell, rather than sound calls scattered
 * through every component. Two reasons: a sound that has to be remembered at
 * each call site is a sound that gets forgotten, and a delegated listener
 * catches buttons added later without anyone having to know this file exists.
 *
 * Click sounds are attached by delegation and filtered to genuinely
 * interactive elements, because a click on empty space should be silent --
 * otherwise the interface comments on itself.
 */

import { useEffect, useRef } from "react";

import { init, play, loop, stopAllLoops, unlock, setMuted, isMuted } from "./sounds";
import { useWorkspace } from "../state/workspace-store";

const INTERACTIVE =
  'button, a[href], [role="button"], [role="tab"], [role="switch"], [role="slider"], summary, input[type="checkbox"], input[type="radio"]';

export function useSound(): void {
  const surfaces = useWorkspace((s) => s.surfaces);
  const immersive = useWorkspace((s) => s.immersive);
  const count = Object.keys(surfaces).length;
  const prevCount = useRef(count);
  const prevImmersive = useRef(immersive);

  // Boot the context. Browsers refuse audio until a gesture, so this only
  // arms the synth; the first real interaction calls unlock().
  useEffect(() => {
    init();
  }, []);

  useEffect(() => {
    const onFirstGesture = () => {
      unlock();
      window.removeEventListener("pointerdown", onFirstGesture);
      window.removeEventListener("keydown", onFirstGesture);
    };
    window.addEventListener("pointerdown", onFirstGesture, { once: true });
    window.addEventListener("keydown", onFirstGesture, { once: true });
    return () => {
      window.removeEventListener("pointerdown", onFirstGesture);
      window.removeEventListener("keydown", onFirstGesture);
    };
  }, []);

  // Delegated feedback for clicks and hovers. Capture phase so it fires before
  // a handler can stop propagation.
  useEffect(() => {
    const onPointerDown = (ev: PointerEvent) => {
      const t = ev.target as Element | null;
      if (!t?.closest?.(INTERACTIVE)) return;
      play("click");
    };
    const onPointerOver = (ev: PointerEvent) => {
      const t = ev.target as Element | null;
      if (!t?.closest?.(INTERACTIVE)) return;
      const related = ev.relatedTarget as Element | null;
      // Only when actually moving onto a new control, not when moving within
      // one -- otherwise a click ticks once per element the pointer crosses.
      if (related && t.closest(INTERACTIVE)?.contains(related)) return;
      play("hover");
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("pointerover", onPointerOver, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("pointerover", onPointerOver, true);
    };
  }, []);

  // Surfaces opening and closing are the two most noticeable state changes in
  // the room, and they are the ones a person can see. A sound that matches a
  // visible movement is confirmation; a sound with no visible cause is noise.
  useEffect(() => {
    if (count !== prevCount.current) {
      play(count > prevCount.current ? "open" : "close");
      prevCount.current = count;
    }
  }, [count]);

  useEffect(() => {
    if (immersive !== prevImmersive.current) {
      play("toggle");
      prevImmersive.current = immersive;
    }
  }, [immersive]);

  // Voice is a continuous state rather than an event, so it loops rather than
  // firing per frame. Leaving the room should not leave a hum behind.
  useEffect(() => {
    const onVoice = (ev: Event) => {
      const s = (ev as CustomEvent).detail as { listening?: boolean };
      loop("listen", Boolean(s?.listening));
    };
    window.addEventListener("hermes:voice-state", onVoice);
    return () => {
      window.removeEventListener("hermes:voice-state", onVoice);
      stopAllLoops();
    };
  }, []);

  // Never leave audio running when the tab is hidden. A backgrounded tab that
  // hums is the fastest way to make someone never enable sound again.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) stopAllLoops();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => () => stopAllLoops(), []);
}

export { isMuted, setMuted };
export default useSound;
