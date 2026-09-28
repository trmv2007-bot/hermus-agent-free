// The workspace shell: what is on screen, how the HUD collapses, and what the
// live link is doing.
//
// There is no separate landing view behind a gesture — this room is the
// workspace. The only mode change is whether the chrome is on screen, because
// surfaces cost queries against the gateway: quieting the room means closing
// them, not hiding the door.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSound } from "./audio/useSound";
import { isMuted, setMuted, stopAllLoops, play } from "./audio/sounds";
import { connectStream, STREAMS, type LinkState } from "./realtime/connection";
import { stowedSurfaces, useWorkspace, visibleSurfaces } from "./state/workspace-store";
import { GRID_STEP } from "./state/surfaces";
import { SurfaceFrame } from "./shell/SurfaceFrame";
import { CommandBar } from "./shell/CommandBar";
import { Backdrop } from "./shell/Backdrop";
import { Orb } from "./shell/Orb";
import { Launcher } from "./shell/Launcher";
import { StageCanvas } from "./shell/StageCanvas";

const ACTIVATION_WINDOW_MS = 1500;
const CLICKS_TO_ENTER = 3;

/** Shift multiplies the step, so you can cross the room without 30 presses. */
const GRID_STEP_FAST = GRID_STEP * 4;

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/** Elements that own the arrow keys when they have focus. */
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

function useLiveLink() {
  const [states, setStates] = useState<Record<string, LinkState>>({ fleet: "connecting" });

  useEffect(() => {
    const links = [
      connectStream(STREAMS.fleet, (state) => setStates((current) => ({ ...current, fleet: state }))),
      connectStream(STREAMS.dashboard, (state) => setStates((current) => ({ ...current, dashboard: state }))),
    ];
    return () => links.forEach((link) => link.close());
  }, []);

  return states;
}

function WorkspaceShell() {
  const immersive = useWorkspace((state) => state.immersive);
  const toggleImmersive = useWorkspace((state) => state.toggleImmersive);
  const surfaces = useWorkspace((state) => state.surfaces);
  const order = useWorkspace((state) => state.order);
  const rejected = useWorkspace((state) => state.rejected);
  const resetLayout = useWorkspace((state) => state.resetLayout);
  const showSurface = useWorkspace((state) => state.showSurface);
  const openSurface = useWorkspace((state) => state.openSurface);
  const setViewport = useWorkspace((state) => state.setViewport);
  const stage = useRef<HTMLElement>(null);
  const dock = useRef<HTMLElement>(null);
  useSound();
  const [soundOn, setSoundOn] = useState(() => !isMuted());
  const toggleSound = useCallback(() => {
    // Stop whatever is sustaining before flipping the switch, so muting does
    // not leave a hum running under a muted master.
    const next = !isMuted();
    setMuted(next);
    if (next) stopAllLoops();
    setSoundOn(!next);
    if (!next) play("toggle");
  }, []);
  const link = useLiveLink();
  const clicks = useRef<number[]>([]);
  const linkLabel = useMemo(() => Object.entries(link).map(([name, state]) => `${name}: ${state}`), [link]);
  const shown = useMemo(() => visibleSurfaces({ surfaces, order }), [surfaces, order]);
  const stowed = useMemo(() => stowedSurfaces({ surfaces, order }), [surfaces, order]);

  // The room is measured, not assumed: surfaces, the core and the launch fan all
  // position against the stage minus whatever the dock actually occupies, so a
  // taller font or a wrapped rail cannot drop a surface behind it.
  useEffect(() => {
    const measure = () => {
      // The stage element is owned by StageCanvas now, so it is found by class
      // rather than held as a ref here. There is exactly one of them.
      const node = stage.current ?? document.querySelector<HTMLElement>(".stage");
      const box = node?.getBoundingClientRect();
      if (!box || !box.width) return;
      const rail = dock.current?.getBoundingClientRect().height ?? 0;
      setViewport({ w: Math.round(box.width), h: Math.round(Math.max(200, box.height - rail)) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    const node = stage.current ?? document.querySelector<HTMLElement>(".stage");
    if (node) observer.observe(node);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [setViewport]);

  // Alt+W is the keyboard path; it does not need the wordmark to be reachable.
  // The arrow keys are the grid path: with a panel focused they nudge it one
  // cell, and with nothing focused they pan the room. Both snap, so a keypress
  // always produces a visible, repeatable change.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey && !event.shiftKey && !event.metaKey && event.key.toLowerCase() === "w") {
        event.preventDefault();
        useWorkspace.getState().toggleImmersive();
        return;
      }
      const direction = ARROWS[event.key];
      if (!direction) return;
      // A text field owns its own arrow keys — history recall in the terminal,
      // caret movement in a search box. Stealing them there is the single most
      // annoying thing a global shortcut can do.
      if (isTyping(event.target) || event.altKey || event.metaKey || event.ctrlKey) return;

      const store = useWorkspace.getState();
      const step = event.shiftKey ? GRID_STEP_FAST : GRID_STEP;
      const target = store.focusedId;
      if (target && store.surfaces[target]) {
        event.preventDefault();
        store.nudgeSurface(target, direction[0], direction[1]);
      } else {
        event.preventDefault();
        store.panBy(-direction[0] * step, -direction[1] * step);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The wordmark is the mouse path into full-HUD mode. Three clicks keeps it out
  // of the way of anyone who only wanted to look at where they were.
  const onLogo = useCallback(() => {
    const now = Date.now();
    clicks.current = [...clicks.current, now].filter((at) => now - at <= ACTIVATION_WINDOW_MS);
    if (clicks.current.length >= CLICKS_TO_ENTER) {
      clicks.current = [];
      useWorkspace.getState().toggleImmersive();
    }
  }, []);

  return (
    <div className="app" data-hud={immersive ? "recessed" : "full"}>
      <Backdrop />

      <header className="topbar">
        <button
          type="button"
          className="logo"
          onClick={onLogo}
          title={`${CLICKS_TO_ENTER} clicks or Alt+W folds the chrome away`}
        >
          HERMUS
        </button>
        <button
          type="button"
          className="ghost diagnostics-toggle"
          onClick={() => openSurface({ kind: "diagnostics", source: { kind: "user" } })}
          title="change models, api keys, voice and this install"
        >
          settings
        </button>
        {/* /control is still a real route, and PRODUCT.md section 2 keeps it
            as the diagnostics drawer rather than deleting it. The old entry
            for it was a topbar <a> that made the operator leave the room
            mid-task, which is why the settings surface above took its place.
            This is deliberately the quiet way in: a target=_blank link at the
            far end of the header, so the workspace still has a route back to
            the raw diagnostics without it being the thing you click first. */}
        <a
          className="ghost legacy-diagnostics-link"
          href="/control"
          target="_blank"
          rel="noreferrer"
          title="raw diagnostics — the legacy control room"
        >
          diagnostics
        </a>
        <CommandBar />
        <div className="link-state" title={linkLabel.join(" · ")}>
          {Object.entries(link).map(([name, state]) => (
            <span key={name} className={`dot dot-${state}`}>
              {name}
            </span>
          ))}
        </div>
      </header>

      <StageCanvas>
        {shown.map((surface) => (
          <SurfaceFrame key={surface.id} surface={surface} />
        ))}

        {/* No "no surface open" notice. When the room is empty the core takes the
            middle of it at hero size, and a two-line caption sitting on top of a
            300px orb competed with the thing it was describing. The empty state
            is the core; it needs no label to say so. */}

        <Orb />
        <Launcher />
      </StageCanvas>

      <footer className="dock" ref={dock}>
          {stowed.map((surface) => (
            <button key={surface.id} type="button" className="chip" onClick={() => showSurface(surface.id)}>
              {surface.title}
            </button>
          ))}
          <span className="spacer" />
          {rejected.length ? <span className="warn">{rejected.length} operation(s) refused — the agent can ask, not force</span> : null}
          <button type="button" className="ghost" onClick={resetLayout}>
            reset layout
          </button>
          <button type="button" className="ghost" onClick={toggleImmersive}>
            full hud
          </button>
          <button
            type="button"
            className="ghost"
            onClick={toggleSound}
            aria-pressed={soundOn}
            title={soundOn ? "Sound on — click to mute" : "Sound muted — click to unmute"}
          >
            {soundOn ? "sound on" : "muted"}
          </button>
        </footer>

        {immersive ? (
          <button type="button" className="hud-exit" onClick={toggleImmersive} title="Alt+W brings the chrome back">
            exit hud
          </button>
        ) : null}
    </div>
  );
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 2000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceShell />
    </QueryClientProvider>
  );
}
