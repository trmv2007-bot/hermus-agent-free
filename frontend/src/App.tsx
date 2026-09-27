// The workspace shell: what is on screen, how the HUD collapses, and what the
// live link is doing.
//
// There is no separate landing view behind a gesture — this room is the
// workspace. The only mode change is whether the chrome is on screen, because
// surfaces cost queries against the gateway: quieting the room means closing
// them, not hiding the door.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { connectStream, STREAMS, type LinkState } from "./realtime/connection";
import { stowedSurfaces, useWorkspace, visibleSurfaces } from "./state/workspace-store";
import { SurfaceFrame } from "./shell/SurfaceFrame";
import { CommandBar } from "./shell/CommandBar";
import { Backdrop } from "./shell/Backdrop";
import { Orb } from "./shell/Orb";
import { Launcher } from "./shell/Launcher";

const ACTIVATION_WINDOW_MS = 1500;
const CLICKS_TO_ENTER = 3;

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
      const box = stage.current?.getBoundingClientRect();
      if (!box || !box.width) return;
      const rail = dock.current?.getBoundingClientRect().height ?? 0;
      setViewport({ w: Math.round(box.width), h: Math.round(Math.max(200, box.height - rail)) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (stage.current) observer.observe(stage.current);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [setViewport]);

  // Alt+W is the keyboard path; it does not need the wordmark to be reachable.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey && !event.shiftKey && !event.metaKey && event.key.toLowerCase() === "w") {
        event.preventDefault();
        useWorkspace.getState().toggleImmersive();
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
          title="open the control room in this room — you do not leave the workspace to inspect it"
        >
          diagnostics
        </button>
        <CommandBar />
        <div className="link-state" title={linkLabel.join(" · ")}>
          {Object.entries(link).map(([name, state]) => (
            <span key={name} className={`dot dot-${state}`}>
              {name}
            </span>
          ))}
        </div>
      </header>

      <main className="stage" ref={stage}>
        {shown.map((surface) => (
          <SurfaceFrame key={surface.id} surface={surface} />
        ))}

        {/* No "no surface open" notice. When the room is empty the core takes the
            middle of it at hero size, and a two-line caption sitting on top of a
            300px orb competed with the thing it was describing. The empty state
            is the core; it needs no label to say so. */}

        <Orb />
        <Launcher />

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
        </footer>

        {immersive ? (
          <button type="button" className="hud-exit" onClick={toggleImmersive} title="Alt+W brings the chrome back">
            exit hud
          </button>
        ) : null}
      </main>
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
