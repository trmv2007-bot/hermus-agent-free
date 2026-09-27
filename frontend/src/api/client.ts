// Gateway access for the workspace: typed reads over the same endpoints the
// product UI uses, plus the token bootstrap the browser has to do itself.
//
// Secrets never come back from these endpoints — the gateway returns metadata
// only. If a call fails, the surface shows the failure rather than a placeholder.

export interface RequirementView {
  id: string;
  description: string;
  satisfied: boolean;
  status: string;
  oracle?: string;
  detail?: string;
  evidence: string[];
}

export interface MissionView {
  mission_id: string;
  goal: string;
  state: string;
  outcome_state: string;
  progress_pct: number;
  confidence_score: number;
  requirements: RequirementView[];
  artifacts: string[];
  evidence_refs: string[];
  agent_claim: Record<string, unknown>;
  verified_result: Record<string, unknown>;
  disagreements: { kind: string; severity?: string; claim?: string; fact?: string }[];
  started_at: string;
  finished_at?: string | null;
}

export interface EvidenceView {
  id: string;
  mission_id: string;
  kind: string;
  check: string;
  source: string;
  summary: string;
  artifact_path?: string | null;
  sha256?: string | null;
  created_at: string;
  recheck?: { state: string; found: boolean };
}

/** The fleet card shape — see ``_agent_card`` in gateway/routes_fleet.py. */
export interface AgentView {
  id: string;
  name: string;
  state: string;
  provider: string;
  model: string;
  key_name?: string | null;
  skills?: string[];
  last_activity?: string;
  created_at?: string;
  stats?: { tasks_done?: number; tasks_failed?: number; tokens?: number };
  memory_summary?: string;
  current_task?: string | null;
  /** Whether a usable credential was found for this provider. */
  binding_status?: string;
}

export interface KeyProbe {
  provider: string;
  base_url?: string;
  model_tested?: string;
  success: boolean;
  healthy: boolean;
  timestamp?: number;
  error?: string | null;
  models_probe?: { success?: boolean; count?: number; error?: string | null };
}

/** A local model in the engine catalogue — not a live worker, and not health. */
export interface ModelCatalogEntry {
  id: string;
  name: string;
  repo?: string;
  roles?: string[];
  devices?: string[];
  est_size_gb?: number;
  notes?: string;
}

/** ``GET /jobs`` — the live queue and its recent work. */
export interface JobView {
  id: string;
  kind: string;
  status: string;
  attempts?: number;
  max_attempts?: number;
  duration_ms?: number | null;
  elapsed_ms?: number | null;
  error?: string | null;
  created?: string;
  created_at?: string;
}

export interface QueueView {
  found?: boolean;
  enabled?: boolean;
  started?: boolean;
  backend?: string;
  workers?: number;
  maxsize?: number;
  by_status?: Record<string, number>;
  registered_kinds?: string[];
}

export interface EventView {
  id?: string | number;
  type?: string;
  ts?: string | number;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

/** `GET /computer/status` — the live-desktop control state. */
export interface ComputerStatus {
  backends?: { total?: number; running?: number; interrupted?: number; failed?: number } & Record<string, unknown>;
  control?: { active?: boolean; halted?: boolean; halt_reason?: string | null } & Record<string, unknown>;
  recording?: Record<string, unknown>;
  current_task?: Record<string, unknown> | null;
  tasks?: Record<string, unknown>[];
  task_stats?: Record<string, number>;
  skills?: { skills?: unknown[]; stats?: Record<string, number> } & Record<string, unknown>;
  recent_events?: Record<string, unknown>[];
  timestamp?: string | number;
}

/** `POST /memory2/recall` — what Hermus already knows. */
export interface MemoryHit {
  id?: string | number;
  text?: string;
  content?: string;
  kind?: string;
  project?: string;
  score?: number;
  created_at?: string | number;
  [key: string]: unknown;
}

/**
 * `GET /memory2/graph` — the memory store as a constellation.
 *
 * The store keeps NO link table, so `edges` is derived from shared project,
 * shared kind and shared content words. `edgesAreInferred` is the backend saying
 * so out loud, and the UI is expected to show it: a graph that looks stored but
 * is actually inferred is the false-success shape §4 forbids.
 */
export interface MemoryNode {
  id: string | number;
  label: string;
  full: string;
  kind: string;
  project: string;
  importance: number;
  radius: number;
  pinned: boolean;
  ts?: string | null;
  tokens: number;
}

export interface MemoryEdge {
  source: string | number;
  target: string | number;
  /** 0..1 — how strong the inferred relationship is. */
  weight: number;
  reasons: string[];
}

export interface MemoryGraph {
  nodes: MemoryNode[];
  edges: MemoryEdge[];
  kinds: string[];
  edges_are_inferred: boolean;
  note: string;
  truncated_nodes: number;
  truncated_edges: boolean;
}

/** `GET /sandbox/status` — which isolation backend is actually in force. */
export interface SandboxStatus {
  configured?: string;
  backend?: string;
  reason?: string;
  capabilities?: Record<string, unknown>;
  policy?: Record<string, unknown>;
  active?: number;
  root?: string;
  audit_log?: string;
  note?: string;
}

/** `POST /sandbox/run` — mirrors core.sandbox.SandboxResult. */
export interface SandboxRun {
  success: boolean;
  stdout: string;
  stderr: string;
  returncode: number;
  backend: string;
  sandbox_id: string;
  duration_ms: number;
  timeout: boolean;
  error: string;
  workdir: string;
  limits?: Record<string, unknown>;
  audit?: Record<string, unknown>;
}

/** One line of the sandbox audit log. */
export interface SandboxAuditEntry {
  sandbox_id?: string;
  backend?: string;
  purpose?: string;
  command?: string;
  blocked?: boolean;
  matched?: string[];
  at?: string;
  [key: string]: unknown;
}

/**
 * The gateway token is not stored in this app. The serving template injects
 * `window.__HERMUS_GATEWAY_TOKEN`, or the operator passes `?token=` once and it
 * is kept in sessionStorage for the tab only.
 */
export function gatewayToken(): string {
  if (typeof window === "undefined") return "";
  const injected = (window as unknown as { __HERMUS_GATEWAY_TOKEN?: string }).__HERMUS_GATEWAY_TOKEN;
  if (injected) return injected;
  const fromUrl = new URLSearchParams(window.location.search).get("token");
  if (fromUrl) {
    try {
      sessionStorage.setItem("hermus.token", fromUrl);
    } catch {
      /* private mode: the URL still works for this navigation */
    }
    return fromUrl;
  }
  try {
    return sessionStorage.getItem("hermus.token") ?? "";
  } catch {
    return "";
  }
}

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = gatewayToken();
  const sep = path.includes("?") ? "&" : "?";
  return `${proto}//${window.location.host}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

export class GatewayError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly path: string,
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const token = gatewayToken();
  const response = await fetch(path, {
    signal,
    headers: { accept: "application/json", ...(token ? { "X-Hermus-Token": token } : {}) },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new GatewayError(response.status, `${response.status} ${response.statusText}`, path);
  }
  return (await response.json()) as T;
}

export async function post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const token = gatewayToken();
  const response = await fetch(path, {
    method: "POST",
    signal,
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      ...(token ? { "X-Hermus-Token": token } : {}),
    },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new GatewayError(response.status, `${response.status} ${response.statusText}`, path);
  }
  return (await response.json()) as T;
}

/**
 * Fetch a binary body (the live screen frame) as an object URL.
 *
 * `get` would try to `response.json()` a JPEG. The caller owns the returned
 * URL and must `URL.revokeObjectURL` it, or a surface that polls every second
 * leaks a blob per frame for the life of the tab.
 */
export async function getBlobUrl(path: string, signal?: AbortSignal): Promise<string> {
  const token = gatewayToken();
  const response = await fetch(path, {
    signal,
    headers: token ? { "X-Hermus-Token": token } : {},
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new GatewayError(response.status, `${response.status} ${response.statusText}`, path);
  }
  return URL.createObjectURL(await response.blob());
}

export const api = {
  missions: () => get<{ missions: MissionView[] }>("/missions").then((r) => r.missions ?? []),
  mission: (id: string) => get<MissionView>(`/missions/${encodeURIComponent(id)}`),
  missionEvidence: (id: string) =>
    get<{ evidence: EvidenceView[]; outcome_state: string; digest: string }>(`/missions/${encodeURIComponent(id)}/evidence`),
  evidence: (id: string, ref: string) => get<EvidenceView>(`/missions/${encodeURIComponent(id)}/evidence/${encodeURIComponent(ref)}`),
  agents: () => get<{ agents: AgentView[] }>("/api/fleet/agents").then((r) => r.agents ?? []),
  /** A live probe of every configured provider — deliberately not polled. */
  keys: () => get<{ results: KeyProbe[] }>("/keys/health").then((r) => r.results ?? []),
  catalogue: () => get<{ catalog: ModelCatalogEntry[] }>("/engine/models").then((r) => r.catalog ?? []),
  /**
   * `GET /engine/status` — what is actually loaded right now.
   *
   * This is the endpoint that answers "which model is running", and it carries
   * the planner's reasoning per role. `/engine/models` is a download catalogue
   * of things that could be installed, which is a different question.
   */
  engineStatus: () =>
    get<{
      status: string;
      action?: string;
      model_needed?: boolean;
      recommended_model?: string | null;
      plan?: {
        mode?: string;
        roles?: Record<
          string,
          {
            role: string;
            engine: string;
            device?: string;
            provider?: string;
            base_url?: string;
            model?: string;
            supports_tools?: boolean;
            reason?: string;
          }
        >;
      };
    }>("/engine/status"),
  command: (text: string) => post<{ success: boolean; response?: string }>("/api/v1/commands", { command: text }),
  queue: () => get<{ queue: QueueView }>("/queue/status").then((r) => r.queue ?? {}),
  jobs: () => get<{ jobs: JobView[]; queue: QueueView }>("/jobs").then((r) => r.jobs ?? []),
  events: (limit = 60) => get<{ count: number; events: EventView[] }>(`/events/recent?limit=${limit}`).then((r) => r.events ?? []),
  computerStatus: () => get<ComputerStatus>("/computer/status"),
  computerResources: () => get<Record<string, unknown>>("/computer/resources"),
  /** The live screen, as an object URL the caller must revoke. 404 = no frame yet. */
  liveFrame: (signal?: AbortSignal) => getBlobUrl("/computer/live-frame", signal),
  memoryRecall: (query: string, limit = 10) =>
    post<{ results: MemoryHit[] }>("/memory2/recall", { query, limit }).then((r) => r.results ?? []),
  /**
   * The route reads `content` and `kind`, NOT `text` — it calls
   * `memory.remember(kind, content, ...)`. Sending `text` stores an empty
   * string and still reports success, which is the §4 failure mode again, one
   * layer up. Checked against routes_subsystems.py::memory2_remember.
   */
  memoryRemember: (content: string, kind = "semantic", project?: string) =>
    post<{ success?: boolean; error?: string; id?: string | number }>("/memory2/remember", { content, kind, project }),
  memoryGraph: (limit = 400) => get<MemoryGraph>("/memory2/graph?limit=" + limit),
  sandboxStatus: () => get<SandboxStatus>("/sandbox/status"),
  sandboxRun: (command: string, opts?: { timeout?: number; cwd?: string; network?: boolean }) =>
    post<SandboxRun>("/sandbox/run", { command, ...opts }),
  sandboxRecent: (limit = 20) => get<{ entries: SandboxAuditEntry[]; path?: string }>(`/sandbox/recent?limit=${limit}`),
};

/** Fields the workspace panels read, in one place so a Python test can check them. */
export const READS = {
  "/jobs": ["id", "kind", "status", "attempts", "duration_ms", "error"],
  "/queue/status": ["backend", "workers", "maxsize", "enabled", "started", "by_status"],
  "/missions": ["mission_id", "goal", "state", "outcome_state", "evidence_refs"],
  "/api/fleet/agents": ["id", "name", "state", "provider", "model", "key_name", "stats", "current_task", "binding_status"],
} as const;
