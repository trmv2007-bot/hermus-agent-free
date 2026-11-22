"""HRS-6 quick wins — the five small fixes a new person feels in the first hour.

Each test boots the real frontend file in Node against a minimal DOM stub (the
technique ``tests/test_control_room_ux.py`` and ``tests/test_command_answer_loop.py``
already use) or pins a stylesheet contract, so the claim in the spec
(``docs/PRODUCT_V2.md``) is backed by behaviour rather than a comment.

What is proven here:

* switching tabs keeps the panel's own ``hidden`` attribute in step with what
  the screen shows, instead of leaving every panel marked hidden forever;
* a request that never settles ends in an honest, retryable failure instead of
  an endless "loading…" placeholder;
* a run stream that delivers no events stops the core from claiming WORKING
  and tells the person where the answer actually is;
* phone-width cards let their tables wrap and scroll instead of clipping the
  right-hand columns;
* the header latency readout is a round trip to /readyz, not the duration of
  the full health probe it used to print as "rtt".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOM_JS = ROOT / "gateway" / "static" / "control-room.js"
NEXUS_JS = ROOT / "gateway" / "static" / "nexus.js"
NEXUS_CSS = ROOT / "gateway" / "static" / "nexus.css"

node_available = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

#: Same shape as the control-room stub: just enough DOM to boot the real file.
DOM_STUB = r"""
function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [], attrs: {}, dataset: {}, style: {}, textContent: "",
    className: "", innerHTML: "", tabIndex: 0, title: "", value: "",
    childElementCount: 0, parentNode: null, hidden: false,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); c.parentNode = this; this.childElementCount = this.children.length; return c; },
    prepend(c) { this.children.unshift(c); c.parentNode = this; this.childElementCount = this.children.length; return c; },
    append(c) { this.children.push(c); c.parentNode = this; this.childElementCount = this.children.length; return c; },
    insertBefore(c) { this.children.unshift(c); c.parentNode = this; this.childElementCount = this.children.length; return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); this.childElementCount = this.children.length; return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    hasAttribute(k) { return k in this.attrs; },
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, click() {},
    closest() { return null; }, contains() { return false; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementsByTagName() { return []; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    insertAdjacentHTML() {}, scrollIntoView() {},
    scrollTop: 0, scrollHeight: 0, checked: false, disabled: false,
  };
  return el;
}
const REGISTRY = {};
global.REGISTRY = REGISTRY;
global.document = {
  createElement: (t) => makeEl(t),
  addEventListener: () => {},
  readyState: "complete",
  body: makeEl("body"),
  documentElement: makeEl("html"),
  querySelector: (sel) => (REGISTRY[sel] = REGISTRY[sel] || makeEl("div")),
  querySelectorAll: () => [],
  getElementById: (id) => (REGISTRY["#" + id] = REGISTRY["#" + id] || makeEl("div")),
};
global.window = global;
global.location = { search: "", pathname: "/control", hash: "", protocol: "http:", host: "localhost:8000" };
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.history = { replaceState: () => {} };
global.addEventListener = () => {};
global.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
global.EventSource = function () { this.close = () => {}; this.addEventListener = () => {}; };
global.fetch = () => Promise.reject(new Error("offline (stub)"));
global.setInterval = () => 0;
global.clearInterval = () => {};
global.navigator = { mediaDevices: null, clipboard: null, getUserMedia: null, language: "en" };
global.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
global.requestAnimationFrame = () => 0;
global.cancelAnimationFrame = () => {};
global.devicePixelRatio = 1;
global.Audio = function () { this.play = () => Promise.resolve(); this.pause = () => {}; };
global.speechSynthesis = { speak: () => {}, cancel: () => {} };
global.Headers = class { constructor() {} set() {} has() { return false; } get() { return null; } };
global.toast = () => {};
"""


def run(source: Path, body: str) -> dict:
    """Boot one real frontend file with the stub and evaluate ``body``.

    The harness goes to a temp file rather than ``node -e``: Windows caps a
    command line at 32767 characters and these sources are larger than that.
    """
    wrapped = (
        "(async () => {\n"
        + textwrap.dedent(body)
        + "\n})().then((r) => { console.log(JSON.stringify(r)); process.exit(0); },"
        + " (e) => { console.error(e && e.stack || e); process.exit(1); });"
    )
    harness = DOM_STUB + "\n" + source.read_text(encoding="utf-8") + "\n" + wrapped
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node failed for {source.name}:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.strip().split("\n") if ln.strip()]
    assert lines, f"no output from node for {source.name}"
    return json.loads(lines[-1])


# --------------------------------------------------------------------------
# 1. Tab panels tell the truth about being hidden
# --------------------------------------------------------------------------
@node_available
def test_selected_tab_panel_is_not_marked_hidden():
    out = run(
        ROOM_JS,
        """
        const btn = makeEl("button"); btn.dataset.tab = "jobs";
        const other = makeEl("button"); other.dataset.tab = "overview";
        const panelJobs = makeEl("div"); panelJobs.id = "tab-jobs"; panelJobs.hidden = true;
        const panelOverview = makeEl("div"); panelOverview.id = "tab-overview";
        const realAll = document.querySelectorAll;
        document.querySelectorAll = (sel) => {
          if (sel === "nav [role=tab]") return [btn, other];
          if (sel === ".tab") return [panelJobs, panelOverview];
          return [];
        };
        global.refreshTab = () => {};
        selectTab("jobs");
        document.querySelectorAll = realAll;
        return {
          selectedHidden: panelJobs.hidden,
          selectedAriaHidden: panelJobs.getAttribute("aria-hidden"),
          otherHidden: panelOverview.hidden,
          otherAriaHidden: panelOverview.getAttribute("aria-hidden"),
        };
        """,
    )
    assert out["selectedHidden"] is False, "the visible panel must not still claim to be hidden"
    assert out["selectedAriaHidden"] is None
    assert out["otherHidden"] is True
    assert out["otherAriaHidden"] == "true"


# --------------------------------------------------------------------------
# 2. A request that never settles fails honestly
# --------------------------------------------------------------------------
@node_available
def test_request_that_never_settles_becomes_a_retryable_failure():
    out = run(
        ROOM_JS,
        """
        // The stub never resolves; the client's own bound has to end the wait.
        global.fetch = () => new Promise(() => {});
        const realSetTimeout = global.setTimeout;
        global.setTimeout = (fn) => { fn(); return 0; };
        const res = await requestJSON("/permissions/pending");
        global.setTimeout = realSetTimeout;
        return { ok: res.ok, code: res.code, retryable: res.retryable, message: res.message };
        """,
    )
    assert out["ok"] is False
    assert out["code"] == "client_timeout"
    assert out["retryable"] is True, "a timeout is worth retrying, and the panel must offer Retry"
    assert "no answer within" in out["message"]


# --------------------------------------------------------------------------
# 3. A silent run stream stops claiming to be working
# --------------------------------------------------------------------------
@node_available
def test_silent_run_stream_says_where_the_answer_is():
    out = run(
        NEXUS_JS,
        """
        const nx = window.HermusNexus;
        nx.state.busy = true;
        const realSetTimeout = global.setTimeout;
        global.setTimeout = (fn) => { fn(); return 0; };
        nx.__test.connectRun("run_silent");
        global.setTimeout = realSetTimeout;
        const body = REGISTRY["#transcriptBody"];
        const transcript = body.children.map((c) => c.innerHTML || "").join(" | ");
        return {
          busy: nx.state.busy,
          missionLive: nx.state.missionLive,
          rendered: transcript.includes("Jobs"),
          transcript: transcript.slice(-320),
        };
        """,
    )
    assert out["busy"] is False, "a stream that never delivered an event must not leave the core busy"
    assert out["missionLive"] is False
    assert out["rendered"] is True, "the person must be told where the answer actually is"


# --------------------------------------------------------------------------
# 4. Phone-width cards scroll their tables instead of clipping them
# --------------------------------------------------------------------------
def test_narrow_width_tables_wrap_and_scroll():
    css = NEXUS_CSS.read_text(encoding="utf-8")
    narrow = css.split("@media (max-width: 760px)")[-1]
    assert ".card { overflow-x: auto; }" in narrow, "a wide table must be scrollable, not clipped"
    assert "white-space: normal" in narrow, "long ids have to wrap before anything scrolls"
    assert ".card td code" in narrow


# --------------------------------------------------------------------------
# 5. The latency readout is a /readyz round trip
# --------------------------------------------------------------------------
@node_available
def test_latency_readout_measures_readyz():
    out = run(
        ROOM_JS,
        """
        global.__called = [];
        global.fetch = (url) => {
          global.__called.push(String(url));
          return Promise.resolve({
            ok: true, status: 200,
            headers: { get: () => "req_test" },
            text: () => Promise.resolve('{"status":"ready","ready":true}'),
          });
        };
        await refreshReadiness();
        const rtt = REGISTRY["#rtt"];
        const conn = REGISTRY["#conn"];
        return { called: global.__called, rtt: rtt.textContent, title: rtt.title, conn: conn.textContent };
        """,
    )
    assert out["called"][0] == "/readyz", "the readout must come from the cheap ready probe"
    assert out["conn"] == "live"
    assert out["rtt"].endswith(" ms")
    assert "/readyz" in out["title"], "the readout must name what it measured"


def test_overview_no_longer_labels_health_duration_as_rtt():
    src = ROOM_JS.read_text(encoding="utf-8")
    assert 'rtt.textContent = ms + "ms"' not in src, (
        "the header latency readout must not print the duration of the full health probe"
    )
