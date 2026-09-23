// The one place a user talks to HERMUS from inside the workspace. It posts the
// same typed command the product UI posts — no separate backend path, no
// browser-local illusion of a conversation.

import { useState } from "react";
import { api, GatewayError } from "../api/client";
import { planForRequest } from "../realtime/task-surfaces";
import { useWorkspace } from "../state/workspace-store";

export function CommandBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const openSurface = useWorkspace((state) => state.openSurface);
  const setAdvanced = useWorkspace((state) => state.setAdvanced);

  const send = async () => {
    const command = text.trim();
    if (!command || busy) return;
    setBusy(true);
    setResult(null);

    // Arrange the room for what was asked before the answer arrives, and say why,
    // so an inferred layout is correctable rather than mysterious.
    const plan = planForRequest(command);
    for (const surface of plan.surfaces) openSurface(surface);
    if (plan.intent !== "general") setAdvanced(true);

    try {
      const response = await api.command(command);
      setResult({ ok: Boolean(response.success), message: response.response ?? plan.reason });
    } catch (error) {
      const detail = error instanceof GatewayError ? `${error.status} from ${error.path}` : String(error);
      setResult({ ok: false, message: detail });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="command"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="tell HERMUS what to do"
        aria-label="command"
        disabled={busy}
      />
      <button type="submit" disabled={busy || !text.trim()}>
        {busy ? "working…" : "run"}
      </button>
      {result ? <span className={`result ${result.ok ? "ok" : "bad"}`}>{result.message}</span> : null}
    </form>
  );
}
