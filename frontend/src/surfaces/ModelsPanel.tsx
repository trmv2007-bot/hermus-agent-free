/**
 * Models — what this machine can do, and which model to put on it.
 *
 * The shape of this page is the argument against a "recommended" badge that
 * is really a guess. Every number that produced the recommendation is shown:
 * the GPU, its total VRAM, the VRAM that is *free right now*, and the
 * arithmetic for each model. You can disagree with the ranking and see
 * exactly why you disagree.
 *
 * That matters more than usual here, because free VRAM moves. A card quoted
 * at 8 GB with 1.1 GB in use is a 6.9 GB card, and a list that ranks against
 * the 8 will hand you a model that does not load.
 *
 * Nothing on this page downloads anything until you click Pull. These are
 * multi-gigabyte pulls, and a page that fetched one on mount would be making a
 * decision for you that you never agreed to. The button says the size, so the
 * click is an informed one.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface Specs {
  os_name: string;
  os_version: string;
  arch: string;
  cpu_cores: number | null;
  ram_total_gb: number | null;
  ram_free_gb: number | null;
  disk_free_gb: number | null;
  gpu: {
    name: string | null;
    vendor: string | null;
    vram_total_gb: number | null;
    vram_free_gb: number | null;
    runtime: string | null;
    source: string;
  };
  notes: string[];
}

interface ModelRow {
  id: string;
  display: string;
  download_gb: number;
  required_vram_gb: number;
  fits: boolean;
  headroom_gb: number | null;
  quality: number | null;
  speed: number | null;
  strengths: string;
  tradeoffs: string;
  installed: boolean;
}

interface Recommendation {
  available: boolean;
  specs: Specs;
  models: ModelRow[];
  recommended: string | null;
  reason: string;
  fits_count: number;
  total_count: number;
}

type PullState =
  | { kind: "idle" }
  | { kind: "working"; model: string; lines: string[] }
  | { kind: "done"; model: string }
  | { kind: "error"; model: string; message: string };

const gb = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(1)} GB`);

export function ModelsPanel() {
  const [data, setData] = useState<Recommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pull, setPull] = useState<PullState>({ kind: "idle" });
  const abort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/models/recommend");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Re-detect when the panel comes back into view: VRAM freed by whatever was
  // using it is the input most likely to have changed.
  useEffect(() => {
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  const startPull = useCallback(async (row: ModelRow) => {
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    setPull({ kind: "working", model: row.id, lines: [] });

    try {
      const res = await fetch("/models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: row.id, confirm: true }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        const detail = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        setPull({ kind: "error", model: row.id, message: detail.detail ?? `HTTP ${res.status}` });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawError: string | null = null;

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        // Streamed chunks split mid-character, so decode incrementally and
        // keep the remainder rather than decoding each chunk in isolation.
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          const frame = JSON.parse(line.slice(5).trim());
          if (frame.type === "progress") {
            setPull((p) =>
              p.kind === "working" ? { ...p, lines: [...p.lines, frame.line].slice(-6) } : p,
            );
          } else if (frame.type === "error") {
            sawError = frame.message ?? frame.line ?? "pull failed";
          }
        }
      }

      if (sawError) setPull({ kind: "error", model: row.id, message: sawError });
      else {
        setPull({ kind: "done", model: row.id });
        await load();
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setPull({ kind: "error", model: row.id, message: (e as Error).message });
      }
    }
  }, [load]);

  if (error) {
    return <div className="panel-error">Could not read this machine&apos;s specs: {error}</div>;
  }
  if (!data) {
    return <div className="panel-loading">Detecting hardware…</div>;
  }

  const { specs, models } = data;
  const g = specs.gpu;

  return (
    <div className="models-panel">
      <header className="models-specs">
        <h2>This machine</h2>
        <dl>
          <div><dt>OS</dt><dd>{specs.os_name} {specs.os_version} ({specs.arch})</dd></div>
          <div><dt>GPU</dt><dd>{g.name ?? "none detected"}</dd></div>
          <div><dt>VRAM</dt>
            <dd>
              {gb(g.vram_total_gb)} total ·{" "}
              <strong className={g.vram_free_gb != null && g.vram_free_gb < 5 ? "warn" : "ok"}>
                {gb(g.vram_free_gb)} free now
              </strong>
            </dd>
          </div>
          <div><dt>Runtime</dt><dd>{g.runtime ?? "—"}</dd></div>
          <div><dt>RAM</dt><dd>{gb(specs.ram_free_gb)} free of {gb(specs.ram_total_gb)}</dd></div>
          <div><dt>Disk free</dt><dd>{gb(specs.disk_free_gb)}</dd></div>
        </dl>
        <p className="models-source">measured via {g.source}</p>
        {specs.notes.map((n) => <p key={n} className="models-note">{n}</p>)}
      </header>

      <div className="models-reason">{data.reason}</div>

      <table className="models-table">
        <thead>
          <tr>
            <th>Model</th><th>Download</th><th>Needs VRAM</th><th>Fit</th>
            <th>Quality</th><th></th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const busy = pull.kind === "working" && pull.model === m.id;
            return (
              <tr key={m.id} className={`${m.fits ? "" : "no-fit"}${m.id === data.recommended ? " recommended" : ""}`}>
                <td>
                  <div className="models-name">
                    {m.display}
                    {m.id === data.recommended && <span className="badge">best fit</span>}
                    {m.installed && <span className="badge ok">installed</span>}
                  </div>
                  <div className="models-trade">{m.strengths}</div>
                  {!m.fits && <div className="models-trade">Too big right now — {m.tradeoffs}</div>}
                </td>
                <td>{m.download_gb.toFixed(1)} GB</td>
                <td>{m.required_vram_gb.toFixed(1)} GB</td>
                <td className={m.fits ? "ok" : "no-fit"}>
                  {m.fits ? `fits, ${m.headroom_gb?.toFixed(1)} GB spare` : "does not fit"}
                </td>
                <td>{"●".repeat(m.quality ?? 0)}<span className="dim">{"●".repeat(5 - (m.quality ?? 0))}</span></td>
                <td>
                  {!m.installed && (
                    <button
                      className="pull"
                      disabled={busy || pull.kind === "working"}
                      onClick={() => void startPull(m)}
                    >
                      {busy ? "pulling…" : `Pull ${m.download_gb.toFixed(1)} GB`}
                    </button>
                  )}
                  {m.installed && <span className="dim">ready</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {pull.kind === "working" && (
        <pre className="models-progress" aria-live="polite">{pull.lines.join("\n") || "starting…"}</pre>
      )}
      {pull.kind === "done" && (
        <div className="models-done">Pulled {pull.model}. HERMUS will use it automatically for vision — no config change needed.</div>
      )}
      {pull.kind === "error" && (
        <div className="panel-error">Pull of {pull.model} failed: {pull.message}</div>
      )}
    </div>
  );
}

export default ModelsPanel;
