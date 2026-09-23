// Surface panels. Each one reads a real endpoint and shows the real failure when
// there is one — an empty panel that looks healthy is the thing this whole
// rebuild is meant to remove.

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, GatewayError, type MissionView } from "../api/client";
import { useWorkspace } from "../state/workspace-store";

const OUTCOME_LABEL: Record<string, string> = {
  verified: "verified",
  partially_verified: "partially verified",
  claimed: "claimed only",
  observed: "observed",
  executed: "executed",
  failed: "failed",
  unknown: "unknown",
};

function sourceRef(surfaceId: string): string | undefined {
  const surface = useWorkspace.getState().surfaces[surfaceId];
  return surface?.source.ref;
}

/** A query failure, stated plainly, with what to do about it. */
export function Probe({ error, path }: { error: unknown; path: string }) {
  const detail = error instanceof GatewayError ? `${error.status} ${error.message}` : String(error);
  return (
    <p className="probe" data-state="error">
      <b>could not read {path}</b>
      <span>{detail}</span>
      <em>The gateway may not be running, or this build is being viewed without it.</em>
    </p>
  );
}

function Loading({ what }: { what: string }) {
  return <p className="probe loading">reading {what}…</p>;
}

export function MissionPanel({ surfaceId }: { surfaceId: string }) {
  const id = sourceRef(surfaceId);
  const [selected, setSelected] = useState<string | null>(id ?? null);
  const list = useQuery({ queryKey: ["missions"], queryFn: () => api.missions(), enabled: !selected, refetchInterval: 4000 });
  const detail = useQuery({ queryKey: ["mission", selected], queryFn: () => api.mission(selected as string), enabled: Boolean(selected) });
  const mission: MissionView | undefined = selected ? detail.data : list.data?.[0];

  if (selected && detail.isError) return <Probe error={detail.error} path={`/missions/${selected}`} />;
  if (!selected && list.isError) return <Probe error={list.error} path="/missions" />;
  if (!mission) return <Loading what={selected ? "mission" : "the mission list"} />;

  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h3>{mission.goal || mission.mission_id}</h3>
          <p className="muted">
            {mission.state} · <span className={`outcome outcome-${mission.outcome_state}`}>{OUTCOME_LABEL[mission.outcome_state] ?? mission.outcome_state}</span>
          </p>
        </div>
        <button className="ghost" onClick={() => setSelected(null)} type="button">
          all missions
        </button>
      </header>

      {!selected && list.data && list.data.length > 1 && (
        <ul className="rows">
          {list.data.slice(0, 12).map((row) => (
            <li key={row.mission_id}>
              <button type="button" className="row-link" onClick={() => setSelected(row.mission_id)}>
                <span className={row.state}>{row.state}</span> {row.goal.slice(0, 60)}
              </button>
            </li>
          ))}
        </ul>
      )}

      <section>
        <h4>Requirements</h4>
        <ul className="requirements">
          {(mission.requirements ?? []).map((requirement) => (
            <li key={requirement.id} className={requirement.satisfied ? "met" : requirement.status === "breached" ? "breach" : "unmet"}>
              <span className="badge">{requirement.status}</span>
              <span>{requirement.description}</span>
              {requirement.detail ? <em className="muted"> {requirement.detail}</em> : null}
            </li>
          ))}
        </ul>
      </section>

      <section className="claim-verified">
        <div>
          <h4>Claimed by workers</h4>
          <p className="mono">{JSON.stringify(mission.agent_claim)}</p>
        </div>
        <div>
          <h4>Verified by the system</h4>
          <p className="mono">{JSON.stringify(mission.verified_result)}</p>
        </div>
      </section>

      {mission.disagreements?.length ? (
        <section>
          <h4>Where they disagree</h4>
          <ul className="disagreements">
            {mission.disagreements.map((item, index) => (
              <li key={`${item.kind}-${index}`} className={item.severity}>
                <b>{item.kind}</b>
                <span>{item.fact ?? item.claim ?? ""}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <footer className="panel-foot">
        <button
          type="button"
          className="ghost"
          onClick={() =>
            useWorkspace.getState().openSurface({ kind: "evidence", title: `Evidence · ${mission.mission_id.slice(0, 12)}`, source: { kind: "api", ref: mission.mission_id } })
          }
        >
          open evidence
        </button>
        <span className="muted">{mission.artifacts?.length ?? 0} artifact(s)</span>
      </footer>
    </div>
  );
}

export function EvidencePanel({ surfaceId }: { surfaceId: string }) {
  const id = sourceRef(surfaceId);
  const query = useQuery({ queryKey: ["evidence", id], queryFn: () => api.missionEvidence(id as string), enabled: Boolean(id) });
  if (!id) return <p className="probe">No mission is selected. Open one from a mission surface.</p>;
  if (query.isError) return <Probe error={query.error} path={`/missions/${id}/evidence`} />;
  if (!query.data) return <Loading what="evidence" />;

  return (
    <div className="panel">
      <p className="digest">{query.data.digest}</p>
      <ul className="evidence">
        {query.data.evidence.map((entry) => (
          <li key={entry.id}>
            <span className={`source source-${entry.source}`}>{entry.source}</span>
            <button
              type="button"
              className="row-link"
              onClick={async () => {
                const fresh = await api.evidence(id, entry.id);
                useWorkspace.getState().openSurface({
                  kind: "evidence",
                  title: `${entry.id} · ${fresh.recheck?.state ?? "?"}`,
                  temporary: true,
                  source: { kind: "api", ref: `${id}/${entry.id}` },
                });
              }}
            >
              {entry.check || entry.kind} — {entry.summary}
            </button>
          </li>
        ))}
      </ul>
      <p className="muted tiny">references only; the payload stays server-side until a surface asks for it</p>
    </div>
  );
}

export function WorkerPanel({ surfaceId }: { surfaceId: string }) {
  const query = useQuery({ queryKey: ["agents"], queryFn: () => api.agents(), refetchInterval: 5000 });
  if (query.isError) return <Probe error={query.error} path="/api/fleet/agents" />;
  if (!query.data) return <Loading what="the worker roster" />;
  const name = useWorkspace.getState().surfaces[surfaceId]?.title.replace("Worker · ", "");

  return (
    <ul className="panel rows">
      {query.data
        .filter((agent) => !name || agent.name === name || agent.agent_id === name)
        .map((agent) => (
          <li key={agent.agent_id}>
            <span className={agent.state.toLowerCase()}>{agent.state}</span>
            <b>{agent.name}</b>
            <span className="muted">
              {agent.provider}/{agent.model || "unassigned"}
            </span>
            <em className="muted">{agent.current_task ? "busy" : "idle"}</em>
          </li>
        ))}
    </ul>
  );
}

export function ModelPanel() {
  const models = useQuery({ queryKey: ["model-catalogue"], queryFn: () => api.catalogue() });
  const keys = useQuery({ queryKey: ["key-health"], queryFn: () => api.keys() });

  return (
    <div className="panel two-col">
      <section>
        <h4>Local model catalogue</h4>
        {models.isError ? <Probe error={models.error} path="/engine/models" /> : null}
        {!models.isError && !models.data ? <Loading what="the model catalogue" /> : null}
        <ul className="rows">
          {(models.data ?? []).slice(0, 40).map((model) => (
            <li key={model.id}>
              <b>{model.name || model.id}</b>
              <span className="muted">{(model.roles ?? []).join(", ") || "no roles declared"}</span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h4>Provider health</h4>
        {keys.isError ? <Probe error={keys.error} path="/keys/health" /> : null}
        {!keys.isError && !keys.data ? <Loading what="provider health (this probes each provider now)" /> : null}
        <ul className="rows">
          {(keys.data ?? []).map((probe) => (
            <li key={`${probe.provider}:${probe.base_url ?? ""}`}>
              <span className={probe.healthy ? "ok" : "down"}>{probe.healthy ? "reachable" : "unreachable"}</span>
              <b>{probe.provider}</b>
              <span className="muted">{probe.model_tested ?? ""}</span>
              {probe.error ? <em className="muted">{String(probe.error).slice(0, 120)}</em> : null}
            </li>
          ))}
        </ul>
        <p className="muted tiny">
          this panel triggers a real probe of every configured provider when it opens, and shows what came back — including
          the refusals. Key material never reaches this app.
        </p>
      </section>
    </div>
  );
}

export function ChatPanel({ surfaceId }: { surfaceId: string }) {
  const ref = sourceRef(surfaceId) ?? "local";
  return (
    <div className="panel">
      <p className="muted">
        Conversation surface <code>{ref}</code>. The product chat lives at <code>/control</code>; this panel is the advanced
        workspace&rsquo;s view of the same run and posts through the same command endpoint.
      </p>
    </div>
  );
}

export function PendingPanel({ label }: { label: string }) {
  return (
    <div className="panel">
      <p className="probe" data-state="pending">
        <b>{label} is not built yet</b>
        <span>The surface exists so the layout is real, but it will not show invented content.</span>
      </p>
    </div>
  );
}
