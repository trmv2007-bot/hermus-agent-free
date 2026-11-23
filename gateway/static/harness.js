/* HERMUS Harness — task-first view.
 *
 * Reads the same canonical backend the cockpit reads: /runs for the list,
 * /runs/{id} for a run's event history, /runs/{id}/diff for what it changed,
 * POST /command (async) to start one. Plain DOM, no framework, no build step;
 * the repo's other surfaces are the same shape.
 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const state = {
    runs: [],
    selected: null,
    detail: null,
    poll: null,
    lastRunId: null,
  };

  // ------------------------------------------------------------------ helpers

  async function getJSON(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.message || body.detail || `HTTP ${res.status}`);
    return body;
  }

  function ago(ts) {
    if (!ts) return "—";
    const secs = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)));
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) return `${Math.round(secs / 60)}m`;
    if (secs < 86400) return `${Math.round(secs / 3600)}h`;
    return `${Math.round(secs / 86400)}d`;
  }

  function clock(ts) {
    if (!ts) return "";
    try {
      return String(ts).slice(11, 19);
    } catch (e) {
      return "";
    }
  }

  function statusClass(status) {
    if (status === "running" || status === "queued") return "running";
    if (status === "finished" || status === "succeeded") return "ok";
    if (status === "failed" || status === "error" || status === "interrupted") return "bad";
    return "";
  }

  /** Tool-call markup that a model emitted as text, made readable. */
  function humanizeToolMarkup(text) {
    return text
      .replace(/<\/?tool_call>/g, "")
      .replace(/<tool name="([^"]+)"[^>]*arguments=(\{[\s\S]*?\})>[^]*?<\/tool>/g, (m, name, args) => `→ ${name}(${args})`)
      .replace(/<tool name="([^"]+)"[^>]*\/>/g, (_, name) => `→ ${name}`)
      .trim();
  }

  // --------------------------------------------------------------- run list

  async function loadRuns() {
    try {
      const data = await getJSON("/runs?limit=40");
      state.runs = (data.runs || []).slice().reverse();
      renderRuns();
      $("#runsEmpty").hidden = state.runs.length > 0;
    } catch (err) {
      $("#runsEmpty").hidden = false;
      $("#runsEmpty").textContent = `runs unavailable: ${err.message}`;
    }
  }

  function renderRuns() {
    const filter = ($("#runFilter").value || "").toLowerCase().trim();
    const list = $("#runList");
    list.textContent = "";
    state.runs
      .filter((run) => !filter || String(run.label || "").toLowerCase().includes(filter))
      .forEach((run) => {
        const li = el("li", "run-item" + (run.run_id === state.selected ? " on" : ""));
        li.dataset.runId = run.run_id;
        li.append(el("span", `dot ${statusClass(run.status)}`));
        const box = el("div");
        const label = (run.result && run.result.response ? String(run.result.response).slice(0, 60) : run.label) || run.run_id;
        box.append(el("div", "run-label", label.replace(/\s+/g, " ")));
        const steps = run.result && run.result.steps !== undefined ? `${run.result.steps} steps` : `${run.events || 0} events`;
        box.append(el("div", "run-sub", `${run.status} · ${steps} · ${ago(run.finished || run.started)}`));
        li.append(box);
        list.append(li);
      });
  }

  // ---------------------------------------------------------------- run detail

  async function selectRun(runId) {
    state.selected = runId;
    renderRuns();
    await loadDetail(runId);
  }

  async function loadDetail(runId) {
    try {
      const detail = await getJSON(`/runs/${runId}`);
      state.detail = detail;
      renderDetail(detail);
      schedulePoll(detail);
      loadDiff(runId);
    } catch (err) {
      $("#runTitle").textContent = "Run unavailable";
      $("#runMeta").textContent = err.message;
    }
  }

  function schedulePoll(detail) {
    if (state.poll) clearTimeout(state.poll);
    state.poll = null;
    if (detail && (detail.status === "running" || detail.status === "queued")) {
      state.poll = setTimeout(() => {
        if (state.selected) loadDetail(state.selected);
      }, 1200);
    }
  }

  function renderDetail(detail) {
    const result = detail.result || {};
    $("#runTitle").textContent = detail.label || detail.run_id;
    const bits = [
      detail.status,
      result.steps !== undefined ? `${result.steps} steps` : null,
      detail.duration_ms ? `${(detail.duration_ms / 1000).toFixed(1)}s` : null,
      detail.run_id,
    ].filter(Boolean);
    $("#runMeta").textContent = bits.join(" · ");
    $("#interrupt").hidden = !(detail.status === "running" || detail.status === "queued");

    renderTranscript(detail.events || [], result);
    renderVerification(result);
  }

  function renderTranscript(events, result) {
    const feed = $("#transcript");
    feed.textContent = "";
    let streamNode = null;

    events.forEach((event) => {
      const type = String(event.type || "");
      const data = event.data || {};
      if (type === "llm_delta") {
        const text = String(data.text || data.delta || "");
        if (!text) return;
        if (!streamNode) {
          streamNode = makeEvent("turn", event.ts, "");
          const pre = el("pre");
          streamNode.querySelector(".ev-body").append(pre);
          feed.append(streamNode);
        }
        streamNode.querySelector("pre").textContent += text;
        return;
      }
      streamNode = null;
      if (type === "step_observed") {
        feed.append(makeEvent("tool", event.ts, `step ${data.step ?? "?"} · ${data.tool_calls ?? 0} tool calls · ${data.chars ?? 0} chars`));
      } else if (type === "verification") {
        const verdict = data.verified ? "verified" : "not verified";
        const problems = (data.problems || []).join("; ");
        feed.append(makeEvent(data.verified ? "final" : "veto", event.ts, `verification: ${verdict}${problems ? ` — ${problems}` : ""}`));
      } else if (type === "agent_response") {
        const text = humanizeToolMarkup(String(data.text || ""));
        if (text) {
          const node = makeEvent("final", event.ts, "");
          const pre = el("pre", null, text);
          node.querySelector(".ev-body").append(pre);
          feed.append(node);
        }
      } else if (type === "job_finished" || type === "run_finished") {
        feed.append(makeEvent("turn", event.ts, `${type} · ${data.status || detailStatus(result)}`));
      } else if (type === "mission_started" || type === "mission_runtime_started") {
        feed.append(makeEvent("turn", event.ts, `mission · ${data.domain || "general"} · budget ${data.budget_steps ?? "?"} steps — ${data.goal || ""}`));
      } else if (type === "mission_state") {
        feed.append(makeEvent("tool", event.ts, `state ${data.state} · ${data.progress_pct ?? 0}% · ${data.steps_left ?? "?"} steps left`));
      } else if (type === "node_started") {
        feed.append(makeEvent("tool", event.ts, `▶ ${data.stage}: ${String(data.goal || "").slice(0, 120)}`));
      } else if (type === "node_finished") {
        const files = data.files_changed !== undefined ? ` · ${data.files_changed} files` : "";
        const tools = data.tools !== undefined ? ` · ${data.tools} tools` : "";
        const ms = data.ms ? ` · ${(data.ms / 1000).toFixed(1)}s` : "";
        feed.append(makeEvent("tool", event.ts, `◀ ${data.stage}: ${data.status}${tools}${files}${ms}`));
      } else if (type === "mission_verification") {
        const errors = (data.errors || []).join("; ");
        feed.append(
          makeEvent(data.verified ? "final" : "veto", event.ts, `mission verification: ${data.verified ? "verified" : `not verified (score ${data.score ?? 0})`}${errors ? ` — ${errors}` : ""}`)
        );
      } else if (type === "mission_repair") {
        const hints = (data.hints || []).join("; ");
        feed.append(makeEvent("veto", event.ts, `repair round ${data.round ?? "?"}${hints ? `: ${hints}` : ""}`));
      } else if (type === "job_queued" || type === "job_started" || type === "run_started" || type === "turn_started" || type === "step_started") {
        feed.append(makeEvent("tool", event.ts, `${type}${data.label ? ` · ${data.label}` : ""}`));
      } else if (type.startsWith("skill_")) {
        feed.append(makeEvent("turn", event.ts, `${type}${data.reason ? ` · ${data.reason}` : ""}`));
      }
      // everything else is deliberately not shown: the transcript is the work,
      // not the framework's bookkeeping.
    });

    if (!feed.children.length) {
      feed.append(el("li", "empty", "This run has no recorded events."));
    }
  }

  function detailStatus(result) {
    return result && result.success === false ? "failed" : "finished";
  }

  function makeEvent(kind, ts, text) {
    const li = el("li", `ev kind-${kind}`);
    li.append(el("span", "ev-time", clock(ts)));
    li.append(el("span", "ev-body", text));
    return li;
  }

  function renderVerification(result) {
    const pane = $("#pane-verify");
    pane.textContent = "";
    const proof = result.proof || null;
    const skill = result.skill_created || null;
    if (!proof && !skill) {
      pane.append(el("p", "empty", "This run recorded no verification. That is not a pass — it means nobody checked."));
      return;
    }
    const verdict = el("div", `verify-verdict ${proof && proof.verified ? "ok" : "none"}`);
    verdict.textContent = proof ? (proof.verified ? "verified" : "not verified") : "no verdict recorded";
    pane.append(verdict);
    (proof && proof.evidence ? proof.evidence : []).forEach((line) => {
      const ul = el("ul", "evidence");
      ul.append(el("li", null, String(line)));
      pane.append(ul);
    });
    if (skill) {
      pane.append(el("p", "meta", `skill: ${skill.created ? "harvested" : `stage ${skill.stage || "?"}`}`));
      const evaluation = skill.evaluation || null;
      if (evaluation && evaluation.reasons && evaluation.reasons.length) {
        const ul = el("ul", "evidence");
        evaluation.reasons.forEach((reason) => ul.append(el("li", null, String(reason))));
        pane.append(ul);
      }
    }
  }

  // -------------------------------------------------------------------- diff

  async function loadDiff(runId) {
    const pane = $("#pane-diff");
    pane.textContent = "";
    pane.append(el("p", "empty", "reading changes…"));
    let diff;
    try {
      diff = await getJSON(`/runs/${runId}/diff`);
    } catch (err) {
      pane.textContent = "";
      pane.append(el("p", "empty", `changes unavailable: ${err.message}`));
      return;
    }
    pane.textContent = "";
    if (!diff.available) {
      pane.append(el("p", "empty", diff.reason || "This run recorded no file changes."));
      (diff.files || []).forEach((file) => pane.append(el("div", "diff-file", `${file.path}${file.source ? ` · ${file.source}` : ""}`)));
      return;
    }
    (diff.files || []).forEach((file) => {
      const box = el("div", "diff-file");
      const head = el("h3", null, file.path);
      if (file.additions !== undefined) head.append(el("span", "counts", `+${file.additions} −${file.deletions}`));
      box.append(head);
      (file.hunks || []).forEach((hunk) => {
        const wrap = el("div", "hunk");
        wrap.append(el("div", "hunk-head", hunk.header || ""));
        (hunk.lines || []).forEach((line) => {
          const cls = line.startsWith("+") && !line.startsWith("+++") ? "line add" : line.startsWith("-") && !line.startsWith("---") ? "line del" : "line";
          wrap.append(el("div", cls, line));
        });
        box.append(wrap);
      });
      pane.append(box);
    });
    if (!(diff.files || []).length) pane.append(el("p", "empty", "No file changes recorded for this run."));
  }

  // ---------------------------------------------------------------- composer

  async function submit(event) {
    event.preventDefault();
    const text = $("#task").value.trim();
    if (!text) return;
    const target = $("#target").value.trim();
    const model = $("#modelSelect").value;
    const payload = { text: target ? `${text}\n\n(work in: ${target})` : text, async: true, platform: "harness" };
    if (model) payload.model = model;
    $("#run").disabled = true;
    try {
      const started = await fetch("/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (!started || (!started.run_id && !started.job_id)) throw new Error(started && (started.message || started.error) || "no run started");
      $("#task").value = "";
      await loadRuns();
      if (started.run_id) {
        state.lastRunId = started.run_id;
        await selectRun(started.run_id);
      }
    } catch (err) {
      $("#runMeta").textContent = `could not start: ${err.message}`;
    } finally {
      $("#run").disabled = false;
    }
  }

  async function interrupt() {
    if (!state.selected) return;
    try {
      await fetch(`/runs/${state.selected}/interrupt`, { method: "POST" });
      setTimeout(() => state.selected && loadDetail(state.selected), 600);
    } catch (err) {
      $("#runMeta").textContent = `interrupt failed: ${err.message}`;
    }
  }

  // ------------------------------------------------------------- chrome bits

  async function loadModels() {
    try {
      const catalog = await getJSON("/models/catalog?probe=false&refresh=false");
      const models = catalog.models || [];
      const select = $("#modelSelect");
      models.slice(0, 40).forEach((model) => {
        const option = el("option");
        option.value = model.ref || model.id;
        option.textContent = (model.ref || model.id).replace(/^custom\//, "custom/");
        select.append(option);
      });
    } catch (err) {
      /* an empty model list means "use the configured default" */
    }
    try {
      const selected = await getJSON("/models/selected");
      const def = (selected.selections || {}).default;
      if (def) $("#modelPill").textContent = `model ${def}`;
    } catch (err) {
      /* the pill stays as it was */
    }
  }

  async function readiness() {
    try {
      const res = await fetch("/readyz");
      const body = await res.json();
      const pill = $("#readyPill");
      pill.textContent = body.ready ? "ready" : `not ready${body.reasons && body.reasons.length ? `: ${body.reasons[0]}` : ""}`;
      pill.className = `pill ${body.ready ? "ok" : "bad"}`;
    } catch (err) {
      const pill = $("#readyPill");
      pill.textContent = "gateway unreachable";
      pill.className = "pill bad";
    }
  }

  // -------------------------------------------------------------------- wire

  document.addEventListener("click", (event) => {
    const item = event.target.closest(".run-item");
    if (item && item.dataset.runId) selectRun(item.dataset.runId);

    const mode = event.target.closest("[data-mode]");
    if (mode) {
      const wanted = mode.dataset.mode;
      localStorage.setItem("hermus.mode", wanted);
      if (wanted === "agent") window.location.href = "/control";
    }

    const tab = event.target.closest("[data-pane]");
    if (tab) {
      document.querySelectorAll(".tab").forEach((node) => {
        const on = node === tab;
        node.classList.toggle("on", on);
        node.setAttribute("aria-selected", on ? "true" : "false");
      });
      document.querySelectorAll(".pane").forEach((pane) => {
        pane.classList.toggle("on", pane.id === `pane-${tab.dataset.pane}`);
      });
    }
  });

  $("#runFilter").addEventListener("input", renderRuns);
  $("#composer").addEventListener("submit", submit);
  $("#task").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit(event);
  });
  $("#interrupt").addEventListener("click", interrupt);
  $("#refresh").addEventListener("click", async () => {
    await loadRuns();
    if (state.selected) await loadDetail(state.selected);
  });
  $("#newRun").addEventListener("click", () => $("#task").focus());

  localStorage.setItem("hermus.mode", "harness");
  readiness();
  setInterval(readiness, 5000);
  loadModels();
  loadRuns().then(() => {
    if (state.runs.length) selectRun(state.runs[0].run_id);
  });
  setInterval(loadRuns, 8000);
})();
