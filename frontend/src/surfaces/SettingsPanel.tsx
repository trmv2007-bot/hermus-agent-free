/**
 * Settings — what this build is actually running on, and what it can do.
 *
 * This is a read of live state, not a form. Nothing here is editable, and
 * that is deliberate: the values come from the gateway's own resolution
 * (which provider won, which device a role landed on, which voice models are
 * actually on disk) and a settings page that showed a *stale copy* of those
 * would be worse than no settings page. To change one, edit the env var the
 * gateway reads and restart — and each row says which.
 *
 * It replaced the label "diagnostics" on the launcher, which was pointing at
 * an embedded iframe of the control room. That drawer still exists and is
 * still reachable as Diagnostics; it answers "what is broken", this answers
 * "what is this".
 */

import { useCallback, useEffect, useState } from "react";

interface EngineRole {
  role: string;
  engine: string;
  device: string;
  provider: string;
  model: string;
  base_url?: string;
  reason?: string;
  supports_tools?: boolean;
}

interface EngineStatus {
  status?: string;
  action?: string;
  model_needed?: boolean;
  plan?: { mode?: string; reason?: string; roles?: unknown };
}

/**
 * Roles arrive as an object keyed by role name, not as a list.
 *
 * That is not a detail: declaring them as an array type makes `.map()` compile
 * cleanly and then throw at runtime on the first render, because TypeScript
 * checks the type you asserted rather than the shape the gateway sends. The
 * only thing that catches it is reading an actual response.
 */
function toRoles(roles: unknown): EngineRole[] {
  if (Array.isArray(roles)) return roles as EngineRole[];
  if (roles && typeof roles === "object") {
    return Object.entries(roles as Record<string, EngineRole>).map(([name, role]) => ({
      ...role,
      role: role?.role ?? name,
    }));
  }
  return [];
}

interface VoiceStatus {
  enabled?: boolean;
  speak_answer?: boolean;
  ack_mode?: string;
  ack_phrases?: string[];
  stt_model?: string;
  ears?: { available?: boolean; reason?: string; wake_words?: string[]; engine?: string };
  tts?: { available?: boolean; backends?: Record<string, { available?: boolean; detail?: unknown }> };
}

interface DoctorStatus {
  status?: string;
  checks?: Array<{ name?: string; status?: string; detail?: string }>;
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="set-row">
      <span className="set-label">{label}</span>
      <span className="set-value">{value}</span>
      {hint && <span className="set-hint">{hint}</span>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="set-section">
      <h3 className="set-title">{title}</h3>
      {children}
    </section>
  );
}

export function SettingsPanel() {
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [voice, setVoice] = useState<VoiceStatus | null>(null);
  const [doctor, setDoctor] = useState<DoctorStatus | null>(null);
  const [problem, setProblem] = useState("");

  const load = useCallback(async () => {
    // One failure must not blank the page: each fetch settles on its own and
    // an unreachable section says so in place.
    const get = async <T,>(path: string): Promise<T | null> => {
      try {
        const res = await fetch(path);
        if (!res.ok) return null;
        return (await res.json()) as T;
      } catch {
        return null;
      }
    };
    const [e, v, d] = await Promise.all([get<EngineStatus>("/engine/status"), get<VoiceStatus>("/voice/status"), get<DoctorStatus>("/doctor/status")]);
    setEngine(e);
    setVoice(v);
    setDoctor(d);
    if (!e && !v && !d) setProblem("the gateway did not answer /engine/status, /voice/status or /doctor/status");
    else setProblem("");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const roles = toRoles(engine?.plan?.roles);
  const ttsBackends = voice?.tts?.backends ?? {};

  return (
    <div className="settings">
      {problem && (
        <p className="set-problem" data-state="error">
          <b>nothing answered</b>
          <span>{problem}</span>
        </p>
      )}

      <Section title="models">
        {engine ? (
          <>
            <Row label="plan" value={engine.plan?.mode ?? "—"} />
            <Row
              label="state"
              value={engine.status ?? "—"}
              hint={engine.action ? `action: ${engine.action}` : undefined}
            />
            {roles.map((r) => (
              <div className="set-role" key={r.role}>
                <div className="set-role-head">
                  <span className="set-role-name">{r.role}</span>
                  <span className="set-role-model">{r.model}</span>
                </div>
                <div className="set-role-meta">
                  {r.engine} · {r.device} · {r.provider}
                  {r.supports_tools === false && <em className="set-warn"> · no tools</em>}
                </div>
                {r.reason && <div className="set-role-reason">{r.reason}</div>}
              </div>
            ))}
            {roles.length === 0 && <p className="set-empty">no roles resolved</p>}
          </>
        ) : (
          <p className="set-empty">loading…</p>
        )}
      </Section>

      <Section title="voice">
        {voice ? (
          <>
            <Row
              label="speaks"
              value={voice.tts?.available ? "yes" : "no"}
              hint={Object.entries(ttsBackends)
                .map(([name, b]) => `${name} ${b.available ? "✓" : "✗"}`)
                .join("  ") || undefined}
            />
            <Row
              label="hears"
              value={voice.ears?.available ? "yes" : "no"}
              hint={voice.ears?.available ? (voice.ears?.wake_words ?? []).join(", ") : voice.ears?.reason}
            />
            <Row label="acknowledge" value={voice.ack_mode ?? "—"} hint="spoken before the slow work starts" />
            <Row label="speaks answers" value={voice.speak_answer ? "yes" : "no"} />
            <Row label="engine" value={voice.ears?.engine ?? "—"} />
            <p className="set-note">
              Set <code>HERMUS_PIPER_MODEL</code> to a .onnx voice and put the models under
              <code> models/hermus-voice-models/</code>, then restart. Nothing here downloads on its own.
            </p>
          </>
        ) : (
          <p className="set-empty">loading…</p>
        )}
      </Section>

      <Section title="health">
        {doctor ? (
          <>
            <Row label="doctor" value={doctor.status ?? "—"} />
            {(doctor.checks ?? []).slice(0, 6).map((c, i) => (
              <Row key={c.name ?? i} label={c.name ?? `check ${i + 1}`} value={c.status ?? "—"} hint={c.detail} />
            ))}
            {(doctor.checks ?? []).length === 0 && <p className="set-empty">no checks reported</p>}
          </>
        ) : (
          <p className="set-empty">loading…</p>
        )}
      </Section>

      <footer className="set-foot">
        <button type="button" className="ghost" onClick={() => void load()}>
          refresh
        </button>
        <span className="set-foot-note">read-only — these come from the gateway, not from this page</span>
      </footer>
    </div>
  );
}
