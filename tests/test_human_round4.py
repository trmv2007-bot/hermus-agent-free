"""Round 4 — the human walkthrough fixes.

A person clicked through every tab, expanded every row and ran every action.
These tests pin what that walk found:

* the health badge read the payload as a flat map of subsystems, so every
  install — including fully healthy ones — was "DEGRADED. Not ready: ok,
  python, venv, capabilities" forever; the badge now reads the payload the
  way the backend means it and names the actual gaps;
* a degraded install now carries the exact pip repair line (backend hint);
* the activity feed stacked a "Model catalog · 0 deployments" row every 30s
  because dedup only matched consecutive rows; it now collapses any repeat;
* a job detail row slammed shut mid-read every poll refresh; expansion now
  survives re-renders;
* console tables printed Python reprs ("['doctor', 'background']", "false");
* the doctor action printed "see raw return" eight times over a JSON dump;
  findings with their fixes are the product and now render as such.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEXUS_JS = ROOT / "gateway/static/nexus.js"
CONSOLE_JS = ROOT / "gateway/static/console.js"
ROOM_JS = ROOT / "gateway/static/control-room.js"
CLIENT_JS = ROOT / "gateway/static/control-client.js"
NEXUS_CSS = ROOT / "gateway/static/nexus.css"


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
    prepend(el){
      // DOM semantics: prepend MOVES a node that is already a child, it never
      // duplicates it.
      const at = this.children.indexOf(el);
      if (at >= 0) this.children.splice(at, 1);
      el.parentElement = this;
      this.children.unshift(el);
    },
    appendChild(el){
      const at = this.children.indexOf(el);
      if (at >= 0) this.children.splice(at, 1);
      el.parentElement = this;
      this.children.push(el);
      return el;
    },
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
    harness += (
        FOOTER
        + "\n(async () => {\n"
        + body
        + "\n})().then(v => { console.log(typeof v === 'string' ? v : JSON.stringify(v)); }).catch(e => { console.error('BODY-ERR ' + (e && e.message || e)); process.exit(1); });\n"
    )
    # the combined scripts blow past the OS argv limit; go through a temp file
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
# Backend: a degraded install must carry the exact repair command
# ---------------------------------------------------------------------------
def test_required_pip_map_covers_every_required_import():
    import bootstrap as boot

    assert set(boot.REQUIRED_PIP_BY_IMPORT) == set(boot.REQUIRED_IMPORTS)
    # spot-check the historical mispairings (a parallel list once drifted)
    assert boot.REQUIRED_PIP_BY_IMPORT["faster_whisper"] == "faster-whisper"
    assert boot.REQUIRED_PIP_BY_IMPORT["feedparser"] == "feedparser"
    assert boot.REQUIRED_PIP_BY_IMPORT["imageio_ffmpeg"] == "imageio-ffmpeg"
    assert boot.REQUIRED_PIP_BY_IMPORT["dotenv"] == "python-dotenv"
    assert boot.REQUIRED_PIP_BY_IMPORT["PIL"] == "Pillow"


def test_missing_capabilities_carry_install_hint():
    import bootstrap as boot

    cap = boot.probe_capabilities()
    for key, entry in cap.items():
        if entry["present"]:
            assert "install" not in entry, key
            continue
        if key.startswith("required."):
            mod = key.split(".", 1)[1]
            assert entry["install"] == f"pip install {boot.REQUIRED_PIP_BY_IMPORT.get(mod, mod)}", key
        elif key.startswith("system."):
            # venv/python/platform gaps are environment facts, not pip fixes —
            # the UI describes them in prose instead of a fake install line.
            assert "install" not in entry, key
        else:
            assert entry.get("install", "").startswith("pip install "), key


def test_omnivoice_never_gets_a_fake_pip_hint():
    """speech.omnivoice is backed by torch (or the omnivoice module, which has
    no public pip package) — the hint must never say "pip install omnivoice"."""
    import bootstrap as boot

    entry = boot.probe_capabilities()["speech.omnivoice"]
    if not entry["present"]:
        assert entry.get("install") in (None, "pip install torch")


def test_console_shaper_keeps_row_lists_as_real_data():
    """A shaped panel row sits exactly where the old depth-3 valve closed: the
    model catalog's roles/devices arrived in the browser as Python reprs."""
    from core.console import _jsonable

    data = {
        "downloads": [{"id": "minicpm", "roles": ["doctor", "background"], "devices": ["GPU", "CPU"], "trust_remote_code": False}]
    }
    out = _jsonable(data)
    row = out["downloads"][0]
    assert row["roles"] == ["doctor", "background"]
    assert row["devices"] == ["GPU", "CPU"]
    assert row["trust_remote_code"] is False
    # the valve still closes on pathological nesting: the innermost levels
    # come out as a repr string, not an infinite recursion
    deep = current = []
    for _ in range(10):
        nxt = []
        current.append(nxt)
        current = nxt
    current.append("leaf")
    assert "'leaf'" in str(_jsonable(deep))


# ---------------------------------------------------------------------------
# Frontend text contracts — the riddles are gone, the seams exist
# ---------------------------------------------------------------------------
def test_nexus_badge_no_longer_lists_scalars_as_subsystems():
    js = NEXUS_JS.read_text(encoding="utf-8")
    assert "healthFacts" in js
    assert "Not ready: " not in js, "the 'ok, python, venv, capabilities' riddle must be gone"
    assert "No subsystem reports ready" not in js, "replaced by the actionable fallback"


def test_nexus_activity_dedup_scans_all_rows():
    js = NEXUS_JS.read_text(encoding="utf-8")
    # dedup must match a repeat anywhere in the feed, not only the first row
    assert "[...host.children].find" in js


def test_job_detail_survives_poll_refresh():
    js = ROOM_JS.read_text(encoding="utf-8")
    assert "EXPANDED_JOBS" in js
    assert "toggleJobDetail(tr, true)" in js, "poll refresh must re-open expanded rows"


def test_console_human_cells_and_doctor_renderer_exist():
    js = CONSOLE_JS.read_text(encoding="utf-8")
    assert "function cellText" in js
    assert "function doctorReportHtml" in js
    css = NEXUS_CSS.read_text(encoding="utf-8")
    assert ".doc-finding" in css


# ---------------------------------------------------------------------------
# Frontend behavior: addEvent collapses interleaved repeats
# ---------------------------------------------------------------------------
def test_add_event_collapses_interleaved_duplicates(node_ready):
    out = run_node(
        [NEXUS_JS],
        r"""
      const H = document.querySelector('#activity');
      const N = window.HermusNexus.__test;
      // pollers interleave: catalog, kernel, catalog again
      N.addEvent('Model catalog · 0 deployment(s) discovered');
      N.addEvent('kernel heartbeat ok');
      N.addEvent('Model catalog · 0 deployment(s) discovered');
      return JSON.stringify({
        rows: H.children.length,
        first: H.children[0].dataset.msg,
        repeats: H.children.map(r => r.dataset.repeat),
      });
    """,
    )
    assert out["rows"] == 2, "the repeated catalog line must collapse into one row"
    assert out["first"] == "Model catalog · 0 deployment(s) discovered"
    assert out["repeats"] == ["2", "1"]


def test_add_event_keeps_feed_bounded(node_ready):
    out = run_node(
        [NEXUS_JS],
        r"""
      const H = document.querySelector('#activity');
      const N = window.HermusNexus.__test;
      for (let i = 0; i < 9; i++) N.addEvent('event ' + i);
      return JSON.stringify({ rows: H.children.length });
    """,
    )
    assert out["rows"] == 7


# ---------------------------------------------------------------------------
# Frontend behavior: the badge explains the real gaps
# ---------------------------------------------------------------------------
HEALTH = {
    "ok": False,
    "python": "3.12.16",
    "venv": False,
    "capabilities": {
        "required.tiktoken": {
            "present": False,
            "status": "missing",
            "reason": "required module 'tiktoken' is not installed",
            "install": "pip install tiktoken",
        },
        "required.pydantic": {"present": True, "status": "ready", "reason": ""},
        "computer.mouse": {
            "present": False,
            "status": "unavailable",
            "reason": "optional module 'pyautogui' is not installed",
            "install": "pip install pyautogui",
        },
        "gateway.websocket": {"present": True, "status": "capable", "reason": ""},
    },
}


def test_badge_degraded_names_modules_not_scalars(node_ready):
    out = run_node(
        [NEXUS_JS],
        r"""
      const N = window.HermusNexus.__test;
      const h = JSON.parse(jsonHealth);
      return JSON.stringify({ t: N.badgeTitle(false, h) });
    """.replace("jsonHealth", json.dumps(json.dumps(HEALTH))),
    )
    t = out["t"]
    assert t.startswith("DEGRADED")
    assert "tiktoken" in t, "the missing module must be named"
    assert "capabilities ready (2 of 4)" in t
    assert "distribution module missing: tiktoken" in t
    # the old riddle listed the payload's scalar keys as broken subsystems
    assert "venv," not in t and "capabilities," not in t


def test_badge_healthy_reads_a_score_not_a_riddle(node_ready):
    healthy = json.loads(json.dumps(HEALTH))
    healthy["ok"] = True
    healthy["capabilities"]["required.tiktoken"]["present"] = True
    healthy["capabilities"]["computer.mouse"]["present"] = True
    out = run_node(
        [NEXUS_JS],
        r"""
      const N = window.HermusNexus.__test;
      return JSON.stringify({ t: N.badgeTitle(true, JSON.parse(jsonHealth)) });
    """.replace("jsonHealth", json.dumps(json.dumps(healthy))),
    )
    t = out["t"]
    assert t.startswith("HEALTHY")
    assert "(4 of 4)" in t
    assert "no optional extras missing" in t


def test_badge_empty_payload_points_at_diagnostics(node_ready):
    out = run_node(
        [NEXUS_JS],
        r"""
      const N = window.HermusNexus.__test;
      return JSON.stringify({ t: N.badgeTitle(false, {}) });
    """,
    )
    assert "Run diagnostics" in out["t"]


# ---------------------------------------------------------------------------
# Frontend behavior: console tables and the doctor report
# ---------------------------------------------------------------------------
def test_cell_text_humanizes_lists_and_booleans(node_ready):
    out = run_node(
        [ROOM_JS, CLIENT_JS, CONSOLE_JS],
        r"""
      const C = window.HermusConsole.__test;
      return JSON.stringify({
        roles: C.cellText(['doctor', 'background']),
        devices: C.cellText(['GPU', 'CPU']),
        no: C.cellText(false),
        yes: C.cellText(true),
        obj: C.cellText({ a: 1 }),
        text: C.cellText('MiniCPM5 1B'),
        nil: C.cellText(null),
      });
    """,
    )
    assert out["roles"] == "doctor · background"
    assert out["devices"] == "GPU · CPU"
    assert out["no"] == "no"
    assert out["yes"] == "yes"
    assert out["text"] == "MiniCPM5 1B"
    assert out["nil"] == ""


def test_doctor_report_renders_findings_and_fixes(node_ready):
    report = {
        "status": "attention",
        "worst_severity": "high",
        "counts": {"critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0},
        "engine": {"mode": "cpu_only", "status": "unavailable", "doctor_model": "ollama/llama3.1:8b"},
        "findings": [
            {
                "id": "engine_down-abc",
                "severity": "high",
                "category": "engine_down",
                "title": "Ollama is not reachable but the plan routes work to it",
                "evidence": "GET http://localhost:11434/v1/models failed",
                "fixes": ["Start Ollama: ollama serve", "Pull a model: ollama pull llama3.1:8b"],
                "component": "ollama",
                "auto_fixable": False,
            }
        ],
    }
    out = run_node(
        [ROOM_JS, CLIENT_JS, CONSOLE_JS],
        r"""
      const C = window.HermusConsole.__test;
      const h = C.doctorReportHtml(JSON.parse(jsReport));
      return JSON.stringify({ h });
    """.replace("jsReport", json.dumps(json.dumps(report))),
    )
    h = out["h"]
    assert "Ollama is not reachable" in h, "findings are the product"
    assert "ollama serve" in h, "the doctor's fixes must be visible, not buried"
    assert "high: 1" in h
    assert "engine: cpu_only · unavailable" in h
    assert "16 item(s)" not in h


def test_result_record_routes_doctor_reports_to_the_renderer(node_ready):
    report = {
        "status": "attention",
        "counts": {"high": 1},
        "findings": [
            {"id": "x", "severity": "high", "title": "t", "evidence": "", "fixes": [], "component": "c", "auto_fixable": False}
        ],
    }
    generic = {"status": "ok", "items": [1, 2, 3]}
    out = run_node(
        [ROOM_JS, CLIENT_JS, CONSOLE_JS],
        r"""
      const C = window.HermusConsole.__test;
      const doc = C.resultRecord('POST /doctor/run', { ok: true, data: JSON.parse(jsReport) });
      const gen = C.resultRecord('POST /x', { ok: true, data: JSON.parse(jsGeneric) });
      return JSON.stringify({ docIsRendered: !!doc.doctor, docHasNoFlat: !doc.text, genIsFlat: !!gen.text, genHasNoDoctor: !gen.doctor });
    """.replace("jsReport", json.dumps(json.dumps(report))).replace("jsGeneric", json.dumps(json.dumps(generic))),
    )
    assert out["docIsRendered"] and out["docHasNoFlat"]
    assert out["genIsFlat"] and out["genHasNoDoctor"]


# ---------------------------------------------------------------------------
# Frontend behavior: a job detail row survives poll refreshes
# ---------------------------------------------------------------------------
def test_job_detail_stays_open_across_refresh(node_ready):
    out = run_node(
        [ROOM_JS],
        r"""
      // Intercept at the fetch layer: control-room.js declares its own
      // requestJSON/getJSON, so a global getJSON stub would be shadowed.
      global.fetch = async (url) => {
        const u = String(url);
        // /jobs returns a bare array of job rows (or {jobs: [...]}), exactly
        // like the gateway.
        const payload =
          u === '/jobs' ? [{ id: 'job_a', kind: 'runtime.turn', status: 'succeeded', attempts: 1, max_attempts: 1, payload: { text: 'hello' } }]
          : u === '/queue/status' ? { queue: { backend: 'sqlite', workers: 1, by_status: { succeeded: 1 } } }
          : u === '/jobs/job_a/result' ? { result: { response: 'the answer' } }
          : {};
        return { ok: true, status: 200, headers: { get(){ return null; } }, text: async () => JSON.stringify(payload) };
      };
      // Hand-built row/detail pair (the stub does not parse HTML strings):
      // the same objects refreshJobs() will see from querySelectorAll.
      const row = document.createElement('tr');
      row.className = 'job-row'; row.dataset.job = 'job_a';
      const detail = document.createElement('tr'); detail.hidden = true;
      row.nextElementSibling = detail;
      const tbody = document.querySelector('#jobs tbody');
      tbody.querySelectorAll = (sel) => sel === 'tr.job-row' ? [row] : [];
      await toggleJobDetail(row);
      const openAfterClick = row.getAttribute('aria-expanded');
      const openHidden = detail.hidden;
      await toggleJobDetail(row);           // user closes it again
      const closedAfterClick = row.getAttribute('aria-expanded');
      // poller rebuilds: the open row must come back open, the closed one must stay closed
      await toggleJobDetail(row);           // re-open
      await refreshJobs();
      await new Promise((r) => setTimeout(r, 20));   // the re-open refetch is fire-and-forget
      const reopened = row.getAttribute('aria-expanded');
      const reopenedHidden = detail.hidden;
      const detailHasPayload = String(detail.innerHTML).includes('hello');
      await toggleJobDetail(row);           // close again
      await refreshJobs();
      const staysClosed = row.getAttribute('aria-expanded');
      return JSON.stringify({ openAfterClick, openHidden, closedAfterClick, reopened, reopenedHidden, detailHasPayload, staysClosed });
    """,
    )
    assert out["openAfterClick"] == "true"
    assert out["openHidden"] is False
    assert out["closedAfterClick"] == "false"
    assert out["reopened"] == "true", "a poll refresh must not slam the detail shut"
    assert out["reopenedHidden"] is False
    assert out["detailHasPayload"], "the re-opened detail must show the payload again"
    assert out["staysClosed"] == "false", "a row the user closed must not be forced open"
