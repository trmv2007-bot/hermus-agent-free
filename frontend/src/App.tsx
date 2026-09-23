// The workspace shell: how the advanced space is entered, what is on screen, and
// what the live link is doing.
//
// The triple-click entry is deliberately fiddly to trigger by accident and the
// keyboard path is the documented one, because this layer is real state —
// surfaces cost queries against the gateway.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { connectStream, STREAMS, type LinkState } from "./realtime/connection";
import { minimizedSurfaces, useWorkspace, visibleSurfaces } from "./state/workspace-store";
import type { SurfaceKind } from "./state/surfaces";
import { SurfaceFrame } from "./shell/SurfaceFrame";
import { CommandBar } from "./shell/CommandBar";

const ACTIVATION_WINDOW_MS = 1500;
const CLICKS_TO_ENTER = 3;
const QUICK_ADD: SurfaceKind[] = ["mission", "model", "worker", "evidence"];

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
  const advanced = useWorkspace((state) => state.advanced);
  const setAdvanced = useWorkspace((state) => state.setAdvanced);
  const surfaces = useWorkspace((state) => state.surfaces);
  const order = useWorkspace((state) => state.order);
  const rejected = useWorkspace((state) => state.rejected);
  const tray = useWorkspace((state) => state.tray);
  const resetLayout = useWorkspace((state) => state.resetLayout);
  const openSurface = useWorkspace((state) => state.openSurface);
  const restoreSurface = useWorkspace((state) => state.restoreSurface);
  const setViewport = useWorkspace((state) => state.setViewport);
  const link = useLiveLink();
  const clicks = useRef<number[]>([]);
  const linkLabel = useMemo(() => Object.entries(link).map(([name, state]) => `${name}: ${state}`), [link]);
  const shown = useMemo(() => visibleSurfaces({ surfaces, order }), [surfaces, order]);
  const hidden = useMemo(() => minimizedSurfaces({ surfaces, order }), [surfaces, order]);

  useEffect(() => {
    const measure = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [setViewport]);

  // Alt+W is the keyboard equivalent; it does not need the logo to be reachable.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey && !event.shiftKey && !event.metaKey && event.key.toLowerCase() === "w") {
        event.preventDefault();
        useWorkspace.getState().setAdvanced(!useWorkspace.getState().advanced);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onLogo = useCallback(() => {
    const now = Date.now();
    clicks.current = [...clicks.current, now].filter((at) => now - at <= ACTIVATION_WINDOW_MS);
    if (clicks.current.length >= CLICKS_TO_ENTER) {
      clicks.current = [];
      useWorkspace.getState().setAdvanced(true);
    }
  }, []);

  return (
    <div className="app" data-advanced={advanced ? "on" : "off"}>
      <header className="topbar">
        <button type="button" className="logo" onClick={onLogo} title={`${CLICKS_TO_ENTER} clicks opens the workspace · Alt+W toggles`}>
          HERMUS
        </button>
        <a className="back" href="/control" title="Back to the product UI">
          ← control room
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

      {!advanced ? (
        <main className="calm">
          <p>
            HERMUS is running. The clean product chat lives at <code>/control</code>.
          </p>
          <p className="muted">
            The advanced workspace holds surfaces — missions, evidence, workers, models — and arranges itself when something
            needs attention. Open it with <b>Alt+W</b>, or click the wordmark three times.
          </p>
          <div className="calm-actions">
            <button type="button" onClick={() => useWorkspace.getState().setAdvanced(true)}>
              open workspace
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => openSurface({ kind: "mission", title: "Missions", source: { kind: "api", ref: "" } })}
            >
              show missions
            </button>
          </div>
          {tray.length ? (
            <details className="tray">
              <summary>{tray.length} runtime readout(s)</summary>
              <ul>
                {tray.slice(0, 8).map((entry, index) => (
                  <li key={`${entry.label}-${index}`}>
                    <b>{entry.label}</b> <span className="muted">{entry.detail}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </main>
      ) : (
        <main className="stage">
          {shown.map((surface) => (
            <SurfaceFrame key={surface.id} surface={surface} />
          ))}
          {!shown.length ? (
            <p className="empty">
              Nothing is open. HERMUS opens surfaces when a task makes one worth seeing, or you can add one:
              {[...QUICK_ADD].map((kind) => (
                <button key={kind} type="button" className="chip" onClick={() => openSurface({ kind, source: { kind: "user" } })}>
                  {kind}
                </button>
              ))}
            </p>
          ) : null}

          <footer className="dock">
            {hidden.map((surface) => (
              <button key={surface.id} type="button" className="chip" onClick={() => restoreSurface(surface.id)}>
                {surface.title}
              </button>
            ))}
            <span className="spacer" />
            {rejected.length ? <span className="warn">{rejected.length} operation(s) refused — the agent can ask, not force</span> : null}
            <button type="button" className="ghost" onClick={resetLayout}>
              reset layout
            </button>
            <button type="button" className="ghost" onClick={() => setAdvanced(false)}>
              close workspace
            </button>
          </footer>
        </main>
      )}
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
