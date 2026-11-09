"""Round 5 — the human walkthrough fixes.

A person walked every tab again, ran the command bar, and read every pill.
What they hit this round, and what these tests pin:

* presence latched at "working / another task is still active" forever: the
  queue published the job lifecycle event BEFORE finishing the run on the
  RunBus, so the kernel's terminal check always saw the very run that just
  finished as "running" — and the inline /command path finished runs on a
  stream the kernel never listened to at all. The order is fixed, the kernel
  now also watches the RunBus, and idle clears the stale goal/run;
* the no-model reply bullet-listed only its first "Detected:" line and ended
  with developer jargon ("Fallback mock for: …");
* the Overview capability rail claimed providers/tools UNAVAILABLE while
  "tool count" read READY two rows below — the probe misread the summary
  shapes (lists and dicts are not {available: bool});
* the hero said the same sentence twice (chip detail + second line), because
  two pollers both wrote the second line;
* the Memory tab promised "skill memory" but actually hosted the capability
  stats — it now shows real memory totals, a live recall box and the skill
  forge registry, while the capability summary moved to the Capabilities tab;
* status chips ("blocke / d") and ghost buttons ("resum / e") wrapped
  mid-word inside narrow table columns, and stat chips stacked one
  full-width card per number instead of forming a strip.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEXUS_JS = ROOT / "gateway/static/nexus.js"
ROOM_JS = ROOT / "gateway/static/control-room.js"
CLIENT_JS = ROOT / "gateway/static/control-client.js"
NEXUS_CSS = ROOT / "gateway/static/nexus.css"

from core.contracts import EventEnvelope
from core.events.bus import EventBus
from core.presence import PresenceManager
from core.presence_kernel import PresenceKernel
from core.run_events import RunBus
from core.world_model import WorldModel


class _Executive:
    def active_goals(self, *, limit=10):
        return []

    def recent_events(self, *, limit=10):
        return []


class _Awareness:
    def status(self, *, max_age_seconds=60.0):
        return {"fresh": True, "age_seconds": 1, "fact_count": 0, "event_count": 0}


# ---------------------------------------------------------------------------
# Node harness — boots the real scripts with a stub DOM and runs a body
# ---------------------------------------------------------------------------
HEADER = r"""
// Minimal DOM stub sufficient to boot the control-room scripts headlessly.
function makeEl(tag){
  const el = {
    tagName: tag, children: [], attrs: {}, dataset: {}, _q: {},
    style: { setProperty(){}, getPropertyValue(){ return ""; } },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    setAttribute(k, v){ this.attrs[k] = String(v); },
    getAttribute(k){ return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k){ return k in this.attrs; },
    addEventListener(){}, removeEventListener(){}, focus(){}, blur(){}, click(){},
    closest(){ return null; }, contains(){ return false; },
    querySelector(sel){ if(!this._q[sel]) this._q[sel] = makeEl("div"); return this._q[sel]; },
    querySelectorAll(){ return []; },
    getElementsByTagName(){ return []; },
    getBoundingClientRect(){ return { top: 0, left: 0, width: 0, height: 0 }; },
    insertAdjacentHTML(){}, scrollIntoView(){},
    get firstElementChild(){ return this.children[0] || null; },
    get lastElementChild(){ return this.children[this.children.length - 1] || null; },
    prepend(el){ this.children.unshift(el); el.parentElement = this; },
    appendChild(el){ this.children.push(el); el.parentElement = this; return el; },
    remove(){ const p = this.parentElement; if(p){ const i = p.children.indexOf(this); if(i >= 0) p.children.splice(i, 1); } },
    scrollTop: 0, scrollHeight: 0, checked: false, disabled: false, value: "",
  };
  return el;
}
const REGISTRY = {};
global.document = {
  createElement: (t) => makeEl(t),
  addEventListener(){}, removeEventListener(){},
  querySelector: (sel) => (REGISTRY[sel] = REGISTRY[sel] || makeEl("div")),
  querySelectorAll: () => [],
  getElementById: (id) => (REGISTRY["#" + id] = REGISTRY["#" + id] || makeEl("div")),
  body: makeEl("body"),
  documentElement: makeEl("html"),
};
global.REGISTRY = REGISTRY;
global.window = global;
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.dispatchEvent = () => true;
global.location = { search: "", pathname: "/control", hash: "", protocol: "http:", host: "localhost:8000" };
global.localStorage = { _s: {}, getItem(k){ return this._s[k] ?? null; }, setItem(k, v){ this._s[k] = String(v); }, removeItem(k){ delete this._s[k]; } };
global.history = { replaceState(){}, pushState(){} };
global.WebSocket = function(){ this.close = () => {}; this.send = () => {}; this.addEventListener = () => {}; };
global.EventSource = function(){ this.close = () => {}; this.addEventListener = () => {}; };
global.setInterval = () => 0;   // never hold the node process open
global.clearInterval = () => {};
global.navigator = { mediaDevices: null, clipboard: null, getUserMedia: null };
global.matchMedia = () => ({ matches: false, addEventListener(){}, addListener(){} });
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);
global.cancelAnimationFrame = () => {};
global.Audio = function(){ this.play = () => Promise.resolve(); this.pause = () => {}; };
global.speechSynthesis = { speak(){}, cancel(){} };
global.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, c => "\\" + c) };
global.fetch = () => Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve(JSON.stringify({ detail: "stub" })) });
"""

FOOTER = r"""
process.on("unhandledRejection", (e) => { console.log("UNHANDLED " + (e && e.message || e)); process.exit(1); });
"""


def run_node(scripts: list[Path], body: str) -> dict:
    """Boot the given scripts with the stub DOM, run `body`, return its JSON."""
    harness = HEADER
    for s in scripts:
        harness += "\n" + s.read_text(encoding="utf-8") + "\n"
    harness += FOOTER + "\n(async () => {\n" + body + "\n})().then(v => { console.log(typeof v === 'string' ? v : JSON.stringify(v)); }).catch(e => { console.error('BODY-ERR ' + (e && e.message || e)); process.exit(1); });\n"
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise AssertionError(f"node failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    lines = [ln for ln in proc.stdout.strip().split("\n") if ln.strip()]
    return json.loads(lines[-1])


@pytest.fixture(scope="module")
def node_ready():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    return True


# ---------------------------------------------------------------------------
# The forever-working badge: lifecycle order + RunBus sink + idle hygiene
# ---------------------------------------------------------------------------
def _kernel(tmp_path):
    bus = EventBus()
    presence = PresenceManager(tmp_path / "presence.json")
    runs = RunBus()
    kernel = PresenceKernel(
        presence=presence,
        executive=_Executive(),
        world=WorldModel(),
        awareness=_Awareness(),
        bus=bus,
        runs=runs,
    )
    kernel.start()
    return bus, presence, runs, kernel


def test_lifecycle_event_after_finish_reports_idle_immediately(tmp_path):
    """The queue's order: run terminal on the RunBus BEFORE the lifecycle
    event reaches the EventBus — presence is idle the moment it fires."""
    bus, presence, runs, _k = _kernel(tmp_path)
    runs.start("r_queue", label="command:web:default")
    bus.publish(EventEnvelope(type="command.started", command="build"))
    assert presence.current()["state"] == "working"

    runs.finish("r_queue", "finished", {"response": "done"})  # queue._finalize order
    bus.publish(EventEnvelope(type="job.completed", command="build", status="done"))
    cur = presence.current()
    assert cur["state"] == "idle", "a finished run must not keep presence working"
    assert cur["detail"] == "ready"
    assert not cur.get("active_goal"), "idle must clear the stale goal"


def test_lifecycle_event_before_finish_no_longer_latches_working(tmp_path):
    """Even the old order (lifecycle first, finish later) recovers: the kernel
    watches the RunBus, so the run's own finish re-derives the state."""
    bus, presence, runs, _k = _kernel(tmp_path)
    runs.start("r_late", label="command:web:default")
    bus.publish(EventEnvelope(type="command.started", command="build"))
    bus.publish(EventEnvelope(type="job.completed", command="build", status="done"))
    # the historical latch: the run is still registered, so the event-derived
    # terminal check honestly says something else is still going
    assert presence.current()["state"] == "working"
    runs.finish("r_late", "finished", {"response": "done"})
    cur = presence.current()
    assert cur["state"] == "idle", "the RunBus sink must end the stale working state"
    assert not cur.get("active_goal")
    assert not cur.get("run_id"), "the finished run must not stay pinned on presence"


def test_inline_run_finish_alone_ends_working(tmp_path):
    """The inline /command path finishes runs on the RunBus stream only — the
    sink (not any EventBus event) must carry presence back to idle."""
    _bus, presence, runs, _k = _kernel(tmp_path)
    runs.start("r_inline", label="command:web:default")
    presence.set_state("working", detail="working · tool_call", run_id="r_inline", goal="what models are you running right now")
    assert presence.current()["state"] == "working"

    runs.finish("r_inline", "finished", {"response": "hi"})
    cur = presence.current()
    assert cur["state"] == "idle"
    assert not cur.get("active_goal")
    assert not cur.get("run_id")


def test_run_bus_sink_never_touches_approval_or_error_states(tmp_path):
    """Only a busy state can be wrong about a finished run; waiting_approval
    and error belong to other owners and must survive a run finish."""
    _bus, presence, runs, _k = _kernel(tmp_path)
    presence.set_state("waiting_approval", detail="waiting for your approval", goal="ship it")
    runs.start("r_appr")
    runs.finish("r_appr", "finished")
    assert presence.current()["state"] == "waiting_approval"
    assert presence.current()["active_goal"] == "ship it"


def test_finish_turn_clears_active_goal_and_run_id(tmp_path):
    presence = PresenceManager(tmp_path / "presence.json")
    presence.begin_turn("Build the release checklist", session_id="s1", run_id="r1")
    assert presence.current()["active_goal"] == "Build the release checklist"
    presence.finish_turn(goal="Build the release checklist", response="ready", session_id="s1", run_id="r1")
    cur = presence.current()
    assert cur["state"] == "idle"
    assert cur["detail"] == "ready"
    assert not cur.get("active_goal"), "finished goal must move to last_goal, not stay active"
    assert cur.get("last_goal") == "Build the release checklist"
    assert not cur.get("run_id"), "a finished run id must not stay pinned"


# ---------------------------------------------------------------------------
# The no-model reply: every provider bulleted, no developer jargon
# ---------------------------------------------------------------------------
def test_ollama_down_reply_bullets_every_provider_and_speaks_human(monkeypatch):
    import core.llm as llm_mod
    from core.llm import FreeLLM

    diag = {
        "usable_providers": [],
        "configured": [
            {"provider": "ollama", "reason": "local runtime configured (no credential needed)"},
            {"provider": "lmstudio", "reason": "local runtime configured (no credential needed)"},
            {"provider": "nollama", "reason": "local runtime configured (no credential needed)"},
            {"provider": "vllm", "reason": "local runtime configured (no credential needed)"},
        ],
        "recommended_provider": "none",
        "recommended_model": "ollama/llama3.1:8b",
    }
    monkeypatch.setattr("core.provider_resolver.diagnose", lambda **kw: diag)

    client = FreeLLM(provider="ollama", model="ollama/llama3.1:8b")
    resp = client._ollama_down_response(tools=False, prompt_tokens=10, messages=[{"content": "what models are you running right now"}])
    text = resp.content

    assert "Fallback mock for:" not in text, "developer jargon must not reach the user"
    assert "no model was reachable" in text, "stable marker the dashboard detects"
    assert 'Your message was: "what models are you running right now"' in text
    lines = [ln for ln in text.split("\n") if ln.startswith("- ")]
    assert len(lines) == 4, f"every detected provider gets its own bullet, got {lines}"
    assert all(p in text for p in ("ollama", "lmstudio", "nollama", "vllm"))


def test_dashboard_detects_the_humanized_marker(node_ready):
    """A no-model reply must be reported as a failure, never as an answer —
    now also for the humanized marker (and the legacy one)."""
    out = run_node([CLIENT_JS], r"""
      global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', text: async () =>
        JSON.stringify({ response: 'Ollama not running. This reply came from the built-in helper because no model was reachable. Your message was: "hello"', run_kind: 'chat' }) });
      const r = await window.HermusClient.sendCommand({ text: 'hello', runId: 'r5' });
      return JSON.stringify({ ok: r.ok, error: r.error, response: r.response });
    """)
    assert out["ok"] is False
    assert "Model not configured" in out["error"]
    assert out["response"] == ""


# ---------------------------------------------------------------------------
# The Overview capability rail reads the shapes the backend sends
# ---------------------------------------------------------------------------
CAPS = {
    "providers": [
        {"provider": "ollama", "configured": True, "has_credentials": True},
        {"provider": "openai", "configured": False, "has_credentials": False},
    ],
    "tools": {"a": {}, "b": {}},
    "tool_count": 2,
    "circuit": {"providers": {}, "models": {}},
    "speech": {"available": True, "backend": "espeak"},
    "transcription": {"available_models": ["tiny", "base"], "current_model": "none", "faster_whisper_installed": True},
    "avatar": {"available": False, "configured": True, "probed": False},
}


def test_capability_rail_reports_what_it_found(node_ready):
    out = run_node([NEXUS_JS], r"""
      const N = window.HermusNexus.__test;
      const list = document.querySelector('#capabilities');
      N.renderCapabilities(JSON.parse(jsonCaps));
      const rows = [...list.children].map((r) => r.innerHTML);
      return JSON.stringify({ rows });
    """.replace("jsonCaps", json.dumps(json.dumps(CAPS))))
    html = "|".join(out["rows"])
    assert len(out["rows"]) == 6, "one honest row per capability family"
    assert "UNAVAILABLE" not in html, "misreading a shape must not claim a subsystem is broken"
    assert "1 configured" in html, "providers: count the configured ones"
    assert "2 ready" in html, "tools: name the count"
    assert "espeak" in html, "speech: name the backend"
    assert "none registered" in html, "an empty circuit registry says so, it is not broken"
    assert "models ready · none selected" in html, "transcription: installed but unselected is not broken"
    assert "configured · not probed" in html, "avatar: configured-but-unprobed is honest, not UNAVAILABLE"


def test_capability_rail_empty_payload_stays_honest(node_ready):
    out = run_node([NEXUS_JS], r"""
      const N = window.HermusNexus.__test;
      const list = document.querySelector('#capabilities');
      N.renderCapabilities({});
      return JSON.stringify({ html: list.innerHTML });
    """)
    assert "No capability data reported" in out["html"]


# ---------------------------------------------------------------------------
# The hero's second line has exactly one writer per situation
# ---------------------------------------------------------------------------
def test_hero_second_line_written_only_by_the_live_run(node_ready):
    out = run_node([NEXUS_JS], r"""
      const N = window.HermusNexus.__test;
      const st = window.HermusNexus.state;
      const now = document.querySelector('#coreNow');
      now.textContent = 'Idle. The queue is clear.';
      N.setState('IDLE', 'ready');
      const afterIdle = now.textContent;
      st.busy = true;
      N.setState('WORKING', 'processing your request');
      const afterBusy = now.textContent;
      st.commandInFlight = 'summarize the report';
      N.setState('WORKING', 'run run_abc');
      const afterNamed = now.textContent;
      return JSON.stringify({ afterIdle, afterBusy, afterNamed });
    """)
    assert out["afterIdle"] == "Idle. The queue is clear.", "idle must leave the queue poller's line alone"
    assert out["afterBusy"] == "working on your request…"
    assert out["afterNamed"] == "working on “summarize the report”"


# ---------------------------------------------------------------------------
# The Memory tab: real totals, real recall, real skills
# ---------------------------------------------------------------------------
STATS = {
    "success": True,
    "memory": {
        "total": 118,
        "archived": 0,
        "by_kind": {"episodic": 92, "semantic": 21, "procedural": 5},
        "access_events": 6996,
        "recall_index": {"available": True, "fts5": True, "vector_backend": "brute-force-cosine", "vectors_indexed": 118},
    },
    "skills": {"count": 0, "path": "data/skills/registry.json", "skills": []},
}


def test_memory_tab_renders_totals_index_and_skills(node_ready):
    out = run_node([ROOM_JS], r"""
      global.fetch = async () => ({ ok: true, status: 200, headers: { get(){ return null; } },
        text: async () => JSON.stringify(JSON.parse(jsStats)) });
      await refreshMemory();
      const k = document.querySelector('#memoryKpis');
      const note = document.querySelector('#memoryIndexNote');
      const s = document.querySelector('#skillList');
      return JSON.stringify({
        kpis: k.innerHTML,
        note: note.textContent,
        skills: s.innerHTML,
      });
    """.replace("jsStats", json.dumps(json.dumps(STATS))))
    kpis = out["kpis"]
    assert "118" in kpis and "memories" in kpis
    assert "episodic" in kpis and "92" in kpis
    assert "6996" in kpis
    assert "FTS5 on" in out["note"] and "brute-force-cosine" in out["note"]
    assert "No skills distilled yet" in out["skills"], "an empty forge says so in words"


def test_memory_tab_reports_an_unavailable_stats_endpoint(node_ready):
    out = run_node([ROOM_JS], r"""
      global.fetch = async () => ({ ok: false, status: 500, headers: { get(){ return null; } },
        text: async () => JSON.stringify({ detail: 'boom' }) });
      await refreshMemory();
      return JSON.stringify({ kpis: document.querySelector('#memoryKpis').innerHTML });
    """)
    assert "memory stats" in out["kpis"], "a failed probe names what failed"


def test_memory_recall_renders_hits_and_honest_miss(node_ready):
    out = run_node([ROOM_JS], r"""
      let payload = { results: [ { kind: 'episodic', created_at: '2026-11-09T10:00:00Z', content: 'ran the downloads scan' } ] };
      global.fetch = async () => ({ ok: true, status: 200, headers: { get(){ return null; } },
        text: async () => JSON.stringify(payload) });
      document.querySelector('#memoryRecallQuery').value = 'downloads scan';
      await recallMemory();
      const hit = document.querySelector('#memoryRecallOut').innerHTML;
      payload = { results: [] };
      document.querySelector('#memoryRecallQuery').value = 'quantum cookies';
      await recallMemory();
      const miss = document.querySelector('#memoryRecallOut').innerHTML;
      return JSON.stringify({ hit, miss });
    """)
    assert "downloads scan" in out["hit"] and "episodic" in out["hit"]
    assert "Nothing matched" in out["miss"] and "quantum cookies" in out["miss"]


def test_memory_stats_route_returns_real_shape():
    from gateway.routes_subsystems import memory2_stats

    out = asyncio.run(memory2_stats())
    assert out.get("success") is True
    mem = out["memory"]
    assert isinstance(mem.get("total", 0), int)
    assert set(mem.get("by_kind") or {}) <= {"episodic", "semantic", "procedural", "skill", "fact", "note"} or isinstance(mem.get("by_kind"), dict)
    idx = mem.get("recall_index") or {}
    assert "available" in idx and "fts5" in idx
    skills = out["skills"]
    assert isinstance(skills.get("skills"), list)
    assert isinstance(skills.get("count"), int)
    for sk in skills["skills"]:
        assert set(sk) == {"name", "summary", "created_at"}


# ---------------------------------------------------------------------------
# CSS: no mid-word wraps, stat chips form a strip
# ---------------------------------------------------------------------------
def test_css_status_chip_and_ghost_button_never_wrap():
    css = NEXUS_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.status \{[^}]*white-space:\s*nowrap", css, re.S), "status words must not break mid-word in narrow cells"
    assert re.search(r"button\.ghost \{[^}]*white-space:\s*nowrap", css, re.S), "button labels must not break mid-word"


def test_css_kpi_forms_a_strip_outside_flex_rows():
    css = NEXUS_CSS.read_text(encoding="utf-8")
    m = re.search(r"\.kpi \{[^}]*\}", css, re.S)
    assert m, ".kpi must exist"
    block = m.group(0)
    assert "inline-flex" in block, "block-level KPIs stacked one full-width card per number"
    assert "flex-direction: column" in block


def test_capability_summary_lives_on_the_capabilities_tab():
    html = (ROOT / "gateway/control.html").read_text(encoding="utf-8")
    cap_section = html.split('id="tab-capabilities"', 1)[1].split('<!-- ============ MEMORY', 1)[0]
    assert 'id="caps"' in cap_section, "the capability summary moved to the Capabilities tab"
    mem_section = html.split('id="tab-memory"', 1)[1].split("</section>", 1)[0]
    assert 'id="caps"' not in mem_section, "the Memory tab must not host capability stats anymore"
    assert 'id="memoryKpis"' in mem_section
    assert 'id="memoryRecallQuery"' in mem_section
    assert 'id="skillList"' in mem_section
    room = ROOM_JS.read_text(encoding="utf-8")
    assert '"/memory2/stats"' in room
    assert "refreshMemory" in room


def test_queue_finishes_the_run_before_the_lifecycle_event():
    """Source-level pin of the ordering fix: bus.finish must precede
    _emit_lifecycle_event inside JobWorker._finalize."""
    src = (ROOT / "gateway/queue.py").read_text(encoding="utf-8")
    m = re.search(r"def _finalize\(.*?(?=\n    # ---|\n    def )", src, re.S)
    assert m, "_finalize not found"
    body = m.group(0)
    finish_at = body.index("self.bus.finish(")
    lifecycle_at = body.index("_emit_lifecycle_event(")
    assert finish_at < lifecycle_at, "the RunBus finish must come before the EventBus lifecycle event"
