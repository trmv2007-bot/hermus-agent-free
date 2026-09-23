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

export interface AgentView {
  agent_id: string;
  name: string;
  state: string;
  provider: string;
  model: string;
  current_task?: string | null;
  stats?: Record<string, number>;
  last_activity?: string;
}

export interface KeyHealthView {
  provider: string;
  alias: string;
  healthy: boolean;
  cooldown_until?: number | null;
  rpm_remaining?: number | null;
  last_error?: string | null;
}

export interface ModelView {
  ref: string;
  provider: string;
  capabilities?: string[];
  healthy?: boolean;
  latency_ms?: number | null;
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

export const api = {
  missions: () => get<{ missions: MissionView[] }>("/missions").then((r) => r.missions ?? []),
  mission: (id: string) => get<MissionView>(`/missions/${encodeURIComponent(id)}`),
  missionEvidence: (id: string) =>
    get<{ evidence: EvidenceView[]; outcome_state: string; digest: string }>(`/missions/${encodeURIComponent(id)}/evidence`),
  evidence: (id: string, ref: string) => get<EvidenceView>(`/missions/${encodeURIComponent(id)}/evidence/${encodeURIComponent(ref)}`),
  agents: () => get<{ agents: AgentView[] }>("/api/fleet/agents").then((r) => r.agents ?? []),
  keys: () => get<{ keys: KeyHealthView[] }>("/api/v1/keys/health").then((r) => r.keys ?? []),
  models: () => get<{ models: ModelView[] }>("/engine/models").then((r) => r.models ?? []),
  command: (text: string) => post<{ success: boolean; response?: string }>("/api/v1/commands", { command: text }),
};
