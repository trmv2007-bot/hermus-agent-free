// Surface panels. Each one reads a real endpoint and shows the real failure when
// there is one — an empty panel that looks healthy is the thing this whole
// rebuild is meant to remove.

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, GatewayError, type MissionView } from "../api/client";
import { digest } from "../realtime/events";

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

function elapsedSince(iso?: string): string {
  if (!iso) return "never";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function WorkerPanel({ surfaceId }: { surfaceId: string }) {
  const query = useQuery({ queryKey: ["agents"], queryFn: () => api.agents(), refetchInterval: 5000 });
  const title = useWorkspace.getState().surfaces[surfaceId]?.title ?? "";
  const wanted = title.startsWith("Worker · ") ? title.slice("Worker · ".length) : "";

  if (query.isError) return <Probe error={query.error} path="/api/fleet/agents" />;
  if (!query.data) return <Loading what="the durable roster" />;

  const agents = query.data.filter((agent) => !wanted || agent.name === wanted || agent.id === wanted);
  const byState = query.data.reduce<Record<string, number>>((acc, agent) => {
    acc[agent.state] = (acc[agent.state] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="panel">
      <p className="muted tiny">
        {query.data.length} worker(s)
        {Object.entries(byState).map(([state, count]) => ` · ${state.toLowerCase()} ${count}`)}
      </p>
      <ul className="workers">
        {agents.map((agent) => (
          <li key={agent.id}>
            <span className={`pill state-${agent.state.toLowerCase()}`}>{agent.state.toLowerCase()}</span>
            <div className="worker-main">
              <b>{agent.name}</b>
              <span className="muted">
                {agent.provider}/{agent.model || "no model assigned"}
                {agent.key_name ? ` · key ${agent.key_name}` : " · no key alias"}
              </span>
              {agent.current_task ? <em className="task">on: {String(agent.current_task).slice(0, 90)}</em> : <em className="muted">nothing assigned</em>}
            </div>
            <div className="worker-meta">
              <span>
                {agent.stats?.tasks_done ?? 0} done / {agent.stats?.tasks_failed ?? 0} failed
              </span>
              <span className="muted">{elapsedSince(agent.last_activity)}</span>
            </div>
          </li>
        ))}
        {!agents.length ? <li className="muted">no worker matches this surface</li> : null}
      </ul>
      <p className="muted tiny">
        a sleeping worker means HERMUS kept its session, task history and checkpoint so it can be resumed without being
        rebuilt. It does not mean a remote provider is still holding a model loaded — nothing here can know that.
      </p>
    </div>
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

export function TelemetryPanel() {
  const queue = useQuery({ queryKey: ["queue"], queryFn: () => api.queue(), refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.jobs(), refetchInterval: 5000 });

  if (queue.isError) return <Probe error={queue.error} path="/queue/status" />;
  if (!queue.data) return <Loading what="the queue" />;

  const q = queue.data;
  const failures = (jobs.data ?? []).filter((job) => job.status === "failed" || job.error);

  return (
    <div className="panel">
      <p className="mono">
        backend {q.backend ?? "?"} · workers {q.workers ?? "?"} · capacity {q.maxsize ?? "?"} · enabled{" "}
        {q.enabled ? "yes" : "no"} · started {q.started ? "yes" : "no"}
      </p>
      {q.by_status && Object.keys(q.by_status).length ? (
        <p className="muted tiny">
          {Object.entries(q.by_status)
            .map(([status, count]) => `${status} ${count}`)
            .join(" · ")}
        </p>
      ) : null}

      <h4>Recent work</h4>
      {jobs.isError ? <Probe error={jobs.error} path="/jobs" /> : null}
      <ul className="rows">
        {(jobs.data ?? []).slice(0, 12).map((job) => (
          <li key={job.id}>
            <span className={`pill state-${job.status ?? "unknown"}`}>{job.status ?? "?"}</span>
            <b>{job.kind}</b>
            <span className="muted">
              {job.duration_ms != null ? `${job.duration_ms}ms` : "no duration"} · attempt {job.attempts ?? 1}/{job.max_attempts ?? 1}
            </span>
            {job.error ? <em className="bad">{String(job.error).slice(0, 90)}</em> : null}
          </li>
        ))}
        {!jobs.data?.length ? <li className="muted">nothing has run through the queue yet</li> : null}
      </ul>
      {failures.length ? (
        <p className="muted tiny">
          {failures.length} job(s) carry an error — open one from the mission surface to see what was attempted.
        </p>
      ) : null}
    </div>
  );
}

export function LogsPanel() {
  // The live streams are the real feed. ``/events/recent`` is a separate
  // in-process mirror that is empty until something publishes to it, so polling
  // it alone would show a healthy-looking page with nothing on it.
  const tray = useWorkspace((state) => state.tray);
  const [showDurable, setShowDurable] = useState(false);
  const durable = useQuery({
    queryKey: ["events-recent"],
    queryFn: () => api.events(80),
    enabled: showDurable,
    refetchInterval: showDurable ? 5000 : false,
  });

  return (
    <div className="panel">
      <ul className="logs">
        {tray.map((entry) => (
          <li key={`${entry.label}-${entry.at}`}>
            <span className="muted">{new Date(entry.at).toISOString().slice(11, 19)}</span>
            <b>{entry.label}</b>
            <span className="mono">{entry.detail}</span>
          </li>
        ))}
        {!tray.length ? <li className="muted">nothing has arrived on the live streams since this tab opened</li> : null}
      </ul>

      <footer className="panel-foot">
        <button type="button" className="ghost" onClick={() => setShowDurable(true)}>
          also read /events/recent
        </button>
      </footer>
      {showDurable ? (
        durable.isError ? (
          <Probe error={durable.error} path="/events/recent" />
        ) : (
          <ul className="logs">
            {(durable.data ?? []).slice(0, 40).map((event, index) => (
              <li key={String(event.id ?? `durable-${index}`)}>
                <span className="muted" />
                <b>{event.type ?? "event"}</b>
                <span className="mono">{digest(event.data)}</span>
              </li>
            ))}
            {!durable.data?.length ? <li className="muted">the durable mirror has nothing recorded</li> : null}
          </ul>
        )
      ) : null}
      <p className="muted tiny">
        this is the runtime trace, not the model&rsquo;s context — nothing shown here is sent to a worker because it appears
        here.
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
