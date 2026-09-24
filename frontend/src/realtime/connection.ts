// The live link between the Python runtime and the workspace.
//
// WebSocket first, with the state surfaced honestly: a closed socket says
// "closed", it does not keep the last snapshot on screen looking current.

import { wsUrl } from "../api/client";
import { useWorkspace } from "../state/workspace-store";
import { digest, planForEvent, type RuntimeEvent } from "./events";

export type LinkState = "connecting" | "open" | "closed" | "error";

export interface Link {
  close: () => void;
}

const MAX_BACKOFF_MS = 15000;

function parse(raw: unknown): RuntimeEvent | null {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const value = JSON.parse(raw);
    return value && typeof value === "object" ? (value as RuntimeEvent) : null;
  } catch {
    return null;
  }
}

/**
 * Subscribes to one stream and turns each event into workspace operations.
 * `path` is a gateway WebSocket route; the token rides the query string because
 * a browser cannot set headers on a WebSocket handshake.
 */
export function connectStream(path: string, onState: (state: LinkState, detail?: string) => void): Link {
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let stopped = false;

  const apply = (event: RuntimeEvent) => {
    const plan = planForEvent(event);
    const workspace = useWorkspace.getState();
    if (plan.ops.length) workspace.applyOps(plan.ops);
    for (const entry of plan.tray) workspace.pushTray({ ...entry, detail: entry.detail || digest(event.data) });
    if (plan.reveal && workspace.immersive) workspace.setImmersive(false);
  };

  const open = () => {
    if (stopped) return;
    onState("connecting");
    try {
      socket = new WebSocket(wsUrl(path));
    } catch (error) {
      onState("error", String(error));
      schedule();
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      onState("open");
    };
    socket.onmessage = (message) => {
      const event = parse(message.data);
      if (event) apply(event);
    };
    socket.onclose = () => {
      onState("closed");
      schedule();
    };
    socket.onerror = () => onState("error", `${path} could not be reached`);
  };

  const schedule = () => {
    if (stopped) return;
    const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
    attempt += 1;
    timer = setTimeout(open, delay);
  };

  open();

  return {
    close: () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (socket) {
        socket.onclose = null;
        socket.onerror = null;
        socket.onmessage = null;
        socket.close();
      }
    },
  };
}

export const STREAMS = {
  fleet: "/api/fleet/ws/fleet",
  dashboard: "/dashboard/events",
} as const;
