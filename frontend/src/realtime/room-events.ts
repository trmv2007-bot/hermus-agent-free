// A tap on the room's own event stream, for panels that need to react to the
// agent rather than only to their own requests.
//
// The alternative — every panel opening its own WebSocket — means one socket per
// panel, four copies of the same replay buffer, and a panel that still shows a
// stale answer when the agent acts through it. This is the same stream the
// workspace already listens on; panels just subscribe to what arrives.
//
// The result of an agent action is published by the gateway precisely so it
// lands here: a panel that ran a command itself knows the answer, but a panel
// that the agent acted through would otherwise have no idea anything happened.

type Listener = (event: { type?: string; data?: Record<string, unknown> }) => void;

const listeners = new Set<Listener>();

/** Called by the stream connection for every frame. Not for panels to call. */
export function emitRoomEvent(event: { type?: string; data?: Record<string, unknown> }): void {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch (error) {
      // One broken panel must not stop the others from hearing the event.
      console.error("room event listener failed", error);
    }
  }
}

/**
 * Subscribe to room events, optionally filtered by type.
 *
 * Returns an unsubscribe function, and the listener is called only for events
 * that arrive after subscribing — there is no replay, because a panel showing
 * the answer to a command from five minutes ago is a lie about the present.
 */
export function onRoomEvent(type: string | null, listener: Listener): () => void {
  const wrapped: Listener = (event) => {
    if (type && event.type !== type) return;
    listener(event);
  };
  listeners.add(wrapped);
  return () => listeners.delete(wrapped);
}
