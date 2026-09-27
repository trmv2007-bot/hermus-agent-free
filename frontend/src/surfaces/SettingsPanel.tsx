/**
 * Settings — the writable configuration surface.
 *
 * This is what now sits behind the diagnostics launcher entry. That used to be
 * an iframe of /control: a read-only dump of numbers behind a label that
 * implied you could change them. This one can.
 *
 * Three rules, matching the backend, and the reasoning is in
 * gateway/routes_settings.py:
 *
 *  - A secret arrives masked and is submitted only if you type a new one. The
 *    placeholder is "unchanged" rather than empty, because an empty field
 *    means "leave this alone" and clearing a working API key by saving a form
 *    is not a recoverable mistake.
 *  - Every value carries a `source`. "file" means you wrote it and it needs a
 *    restart; "process" means the running gateway is already using it.
 *    Showing those the same way would claim a change took effect when it has
 *    not, which is the class of lie this page exists to end.
 *  - Nothing is autosaved. An explicit save, because these are API keys and a
 *    keystroke-by-keystroke write is a race with your own typing.
 *
 * The live "what is this build running on" read is kept at the bottom rather
 * than removed: it answers "did my change work", which is the question you
 * have immediately after saving.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

interface SettingItem {
  key: string;
  label: string;
  group: string;
  secret: boolean;
  help: string;
  source: "process" | "file" | "unset";
  set: boolean;
  value: string;
}

interface SettingsPayload {
  settings: SettingItem[];
  groups: string[];
  env_path: string;
  env_exists: boolean;
  requires_restart: boolean;
}

interface EngineStatus {
  status?: string;
  plan?: { mode?: string };
}

const GROUP_TITLES: Record<string, string> = {
  models: "models",
  keys: "api keys",
  voice: "voice",
  room: "this install",
};

const SOURCE_TEXT: Record<SettingItem["source"], string> = {
  process: "live",
  file: "restart to apply",
  unset: "not set",
};

export function SettingsPanel() {
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [problem, setProblem] = useState("");
  const [engine, setEngine] = useState<EngineStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/settings");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as SettingsPayload;
      setPayload(body);
      setDraft({});
      setProblem("");
    } catch (err) {
      setProblem(`could not read settings: ${(err as Error).message}`);
    }
    try {
      const res = await fetch("/engine/status");
      if (res.ok) setEngine((await res.json()) as EngineStatus);
    } catch {
      setEngine(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(
    () =>
      Object.entries(draft).filter(([key, value]) => {
        const item = payload?.settings.find((s) => s.key === key);
        if (!item) return false;
        // A masked secret is never compared against what was typed — the mask
        // would make every entry "dirty" and every save look like a change.
        // Blank is never dirty either, because blank means "leave it alone".
        return value.trim() !== "" && value !== item.value;
      }),
    [draft, payload],
  );

  const save = useCallback(async () => {
    if (!dirty.length) return;
    setSaving(true);
    setNotice(null);
    try {
      const res = await fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ set: Object.fromEntries(dirty) }),
      });
      const body = (await res.json()) as { ok?: boolean; error?: string; message?: string; skipped?: string[] };
      if (!res.ok || !body.ok) {
        setNotice({ tone: "error", text: body.error ?? `save failed (HTTP ${res.status})` });
        return;
      }
      setNotice({
        tone: "ok",
        text:
          body.message ??
          (body.skipped?.length
            ? `${body.skipped.length} field left unchanged — an empty field never clears a saved value`
            : "saved"),
      });
      await load();
    } catch (err) {
      setNotice({ tone: "error", text: `save failed: ${(err as Error).message}` });
    } finally {
      setSaving(false);
    }
  }, [dirty, load]);

  if (problem) {
    return (
      <div className="panel">
        <p className="probe" data-state="error">
          <b>settings did not load</b>
          <span>{problem}</span>
          <em>The gateway may not be running. This stays open so you can retry without leaving the room.</em>
        </p>
        <footer className="panel-foot">
          <button type="button" className="ghost" onClick={() => void load()}>
            try again
          </button>
        </footer>
      </div>
    );
  }

  if (!payload) return <div className="settings"><p className="set-empty">loading…</p></div>;

  return (
    <div className="settings">
      {notice && (
        <p className="set-notice" data-tone={notice.tone}>
          {notice.text}
        </p>
      )}

      {payload.groups.map((group) => (
        <section className="set-section" key={group}>
          <h3 className="set-title">{GROUP_TITLES[group] ?? group}</h3>
          {payload.settings
            .filter((item) => item.group === group)
            .map((item) => {
              const value = draft[item.key] ?? "";
              return (
                <label className="set-field" key={item.key}>
                  <span className="set-field-head">
                    <span className="set-field-label">{item.label}</span>
                    <span className="set-source" data-source={item.source}>
                      {SOURCE_TEXT[item.source]}
                    </span>
                  </span>
                  <span className="set-field-row">
                    <input
                      className="set-input"
                      type={item.secret ? "password" : "text"}
                      value={value}
                      placeholder={
                        item.secret
                          ? item.set
                            ? "unchanged — type to replace"
                            : "not set"
                          : item.value || "not set"
                      }
                      spellCheck={false}
                      autoComplete="off"
                      onChange={(event) => setDraft((prev) => ({ ...prev, [item.key]: event.target.value }))}
                    />
                    {!item.set && <span className="set-unset">empty</span>}
                  </span>
                  {item.help && <span className="set-help">{item.help}</span>}
                </label>
              );
            })}
        </section>
      ))}

      <footer className="set-actions">
        <button type="button" className="set-save" onClick={() => void save()} disabled={!dirty.length || saving}>
          {saving ? "saving…" : `save${dirty.length ? ` ${dirty.length}` : ""}`}
        </button>
        <button type="button" className="ghost" onClick={() => void load()} disabled={saving}>
          discard
        </button>
        {dirty.length > 0 && <span className="set-dirty">{dirty.map(([k]) => k).join(", ")}</span>}
        {payload.requires_restart && <span className="set-restart">changes apply on restart</span>}
      </footer>

      <section className="set-section set-section-last">
        <h3 className="set-title">running now</h3>
        <p className="set-path">
          {payload.env_path}
          {!payload.env_exists && <em> — does not exist yet; saving will create it</em>}
        </p>
        {engine ? (
          <div className="set-row">
            <span className="set-label">engine</span>
            <span className="set-value">
              {engine.plan?.mode ?? "—"} · {engine.status ?? "—"}
            </span>
          </div>
        ) : (
          <p className="set-empty">engine status unavailable</p>
        )}
      </section>
    </div>
  );
}
