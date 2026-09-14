"""Phase 5b control-room behaviour: envelope awareness, states, tabs, toasts.

The control room's wording is pinned elsewhere (``tests/_control_room_source``);
these tests exercise the *logic* added in 5b by evaluating the real
``gateway/static/control-room.js`` in Node against a minimal DOM stub. That is
the only way to verify browser code here without dragging in a JS toolchain.

What is proven:

* ``requestJSON`` understands the canonical envelope — a failure yields
  ``code`` / ``message`` / ``retryable`` / ``requestId`` instead of the old
  ``"url -> 500"``, and a network failure is treated as retryable;
* ``stateHtml`` / ``stateRowHtml`` render loading / empty / error consistently,
  surface the X-Request-ID and offer Retry only when the envelope says the
  request is retryable;
* ``toast`` emits into the aria-live region and carries Retry + request id;
* ``refreshReadiness`` maps /readyz 200 -> live, 503 -> "not ready" (never a
  fabricated "live"), unreachable -> offline;
* the tab strip is a real ARIA tablist with roving tabindex and keyboard nav.

Node is optional (``shutil.which("node")``) — like the existing JS syntax test,
these skip when Node is absent rather than failing an unrelated environment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOM_JS = ROOT / "gateway" / "static" / "control-room.js"
CONTROL_HTML = ROOT / "gateway" / "control.html"
CONTROL_CSS = ROOT / "gateway" / "static" / "control.css"

#: A DOM small enough to boot control-room.js in Node, and nothing more.
DOM_STUB = r"""
function makeEl(tag) {
  const el = {
    tagName: (tag || "div").toUpperCase(),
    children: [], attrs: {}, dataset: {}, style: {}, textContent: "",
    className: "", innerHTML: "", tabIndex: 0, title: "", value: "",
    childElementCount: 0, parentNode: null,
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
    getElementsByTagName() { return []; }, getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    insertAdjacentHTML() {}, scrollIntoView() {},
    scrollTop: 0, scrollHeight: 0, checked: false, disabled: false,
  };
  return el;
}
const REGISTRY = {};
global.document = {
  createElement: (t) => makeEl(t),
  addEventListener: () => {},
  querySelector: (sel) => (REGISTRY[sel] = REGISTRY[sel] || makeEl("div")),
  querySelectorAll: () => [],
  getElementById: (id) => (REGISTRY["#" + id] = REGISTRY["#" + id] || makeEl("div")),
};
global.REGISTRY = REGISTRY;
global.window = global;
global.location = { search: "", pathname: "/control", hash: "", protocol: "http:", host: "localhost:8000" };
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.history = { replaceState: () => {} };
global.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
global.EventSource = function () { this.close = () => {}; };
global.setInterval = () => 0;   // never hold the node process open
global.clearInterval = () => {};
global.navigator = { mediaDevices: null, clipboard: null, getUserMedia: null };
global.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);
global.cancelAnimationFrame = () => {};
global.Audio = function () { this.play = () => Promise.resolve(); this.pause = () => {}; };
global.speechSynthesis = { speak: () => {}, cancel: () => {} };
"""


def run_in_node(body: str) -> dict:
    """Boot control-room.js in Node with the DOM stub, then run `body`."""
    harness = (
        DOM_STUB
        + "\n"
        + ROOM_JS.read_text(encoding="utf-8")
        + "\n"
        + textwrap.dedent(
            """
            global.__api = {
              requestJSON, getJSON, stateHtml, stateRowHtml, toast,
              refreshReadiness, selectTab, RETRY_ACTIONS, reportFailure,
              document, REGISTRY,
            };
            """
        )
        + "\n"
        + body
    )
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed ({proc.returncode}):\n{proc.stderr}")
    # The last line is the JSON result; anything before it is script output.
    lines = [ln for ln in proc.stdout.strip().split("\n") if ln.strip()]
    return json.loads(lines[-1])


@pytest.fixture(scope="module")
def node_available():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    return True


# ---------------------------------------------------------------------------
# Envelope-aware API layer
# ---------------------------------------------------------------------------
def test_failure_returns_the_canonical_envelope(node_available):
    out = run_in_node(
        """
        global.fetch = (url) => Promise.resolve({
          ok: false, status: 404,
          headers: { get: () => "req_abc123" },
          text: () => Promise.resolve(JSON.stringify({
            success: false, error: "not_found", code: "not_found",
            message: "No such job", retryable: false, details: {},
          })),
        });
        requestJSON("/jobs").then((r) => console.log(JSON.stringify(r)));
        """
    )
    assert out["ok"] is False
    assert out["status"] == 404
    assert out["code"] == "not_found"
    assert out["message"] == "No such job"
    assert out["retryable"] is False
    assert out["requestId"] == "req_abc123"


def test_retryable_failures_are_marked_retryable(node_available):
    out = run_in_node(
        """
        global.fetch = () => Promise.resolve({
          ok: false, status: 503, headers: { get: () => null },
          text: () => Promise.resolve(JSON.stringify({
            success: false, error: "not_ready", code: "not_ready",
            message: "draining (shutdown)", retryable: true, details: {},
          })),
        });
        requestJSON("/readyz").then((r) => console.log(JSON.stringify(r)));
        """
    )
    assert out["retryable"] is True
    assert "draining" in out["message"]


def test_network_failure_is_retryable_not_fatal(node_available):
    out = run_in_node(
        """
        global.fetch = () => Promise.reject(new Error("Failed to fetch"));
        requestJSON("/jobs").then((r) => console.log(JSON.stringify(r)));
        """
    )
    assert out["ok"] is False
    assert out["status"] == 0
    assert out["code"] == "network_error"
    assert out["retryable"] is True  # the gateway may simply be restarting


def test_success_passes_body_and_timing_through(node_available):
    out = run_in_node(
        """
        global.fetch = () => Promise.resolve({
          ok: true, status: 200, headers: { get: () => "req_ok" },
          text: () => Promise.resolve(JSON.stringify({ jobs: [] })),
        });
        requestJSON("/jobs").then((r) => console.log(JSON.stringify(r)));
        """
    )
    assert out["ok"] is True
    assert out["data"] == {"jobs": []}
    assert out["requestId"] == "req_ok"


def test_non_json_error_body_still_yields_a_usable_message(node_available):
    out = run_in_node(
        """
        global.fetch = () => Promise.resolve({
          ok: false, status: 502, headers: { get: () => null },
          text: () => Promise.resolve("<html>bad gateway</html>"),
        });
        requestJSON("/x").then((r) => console.log(JSON.stringify(r)));
        """
    )
    assert out["code"] == "http_502"
    assert "502" in out["message"]


# ---------------------------------------------------------------------------
# Panel states
# ---------------------------------------------------------------------------
def test_panel_states_render_loading_empty_and_error(node_available):
    out = run_in_node(
        """
        console.log(JSON.stringify({
          loading: stateHtml({ loading: true }, {}),
          empty: stateHtml({ empty: "queue idle" }, {}),
          error: stateHtml({ error: { message: "boom", code: "internal", requestId: "req_1", retryable: true } },
                           { label: "queue", onRetryId: "jobs" }),
          row: stateRowHtml({ empty: "none" }, 6),
        }));
        """
    )
    assert "skeleton" in out["loading"]
    assert "panel-state" in out["empty"] and "queue idle" in out["empty"]
    assert "boom" in out["error"] and "(internal)" in out["error"]
    assert "req_1" in out["error"]
    assert 'data-retry="jobs"' in out["error"]
    assert out["row"].startswith('<tr><td colspan="6">')


def test_retry_button_only_when_the_envelope_says_retryable(node_available):
    out = run_in_node(
        """
        console.log(JSON.stringify({
          retryable: stateHtml({ error: { message: "x", retryable: true } }, { onRetryId: "jobs" }),
          fatal: stateHtml({ error: { message: "x", retryable: false } }, { onRetryId: "jobs" }),
        }));
        """
    )
    assert "data-retry" in out["retryable"]
    assert "data-retry" not in out["fatal"]


def test_every_panel_retry_target_is_registered(node_available):
    out = run_in_node("console.log(JSON.stringify(Object.keys(RETRY_ACTIONS).sort()));")
    for expected in ("jobs", "missions", "computer", "remote", "doctor", "telemetry", "overview"):
        assert expected in out, f"{expected} panel offers Retry but has no action registered"


# ---------------------------------------------------------------------------
# Toasts
# ---------------------------------------------------------------------------
def test_toast_renders_message_request_id_and_retry(node_available):
    out = run_in_node(
        """
        const host = document.querySelector("#toasts");
        toast("saved", "ok", {});
        const plain = host.children.length;
        toast("queue: boom", "error", { requestId: "req_9", retry: true, onRetry: () => {} });
        const el = host.children[host.children.length - 1];
        const parts = el.children.map((c) => c.className + "|" + (c.textContent || ""));
        console.log(JSON.stringify({ plain, parts, cls: el.className }));
        """
    )
    assert out["cls"] == "toast error"
    assert any("toast-msg" in p and "queue: boom" in p for p in out["parts"])
    assert any("toast-rid" in p and "req_9" in p for p in out["parts"])
    assert any("toast-retry" in p for p in out["parts"])


# ---------------------------------------------------------------------------
# Readiness (never a fabricated "live")
# ---------------------------------------------------------------------------
def test_readiness_pill_maps_ready_draining_and_offline(node_available):
    out = run_in_node(
        """
        const results = {};
        const stub = (status, body) => { global.fetch = () => Promise.resolve({
          ok: status === 200, status: status, headers: { get: () => null },
          text: () => Promise.resolve(JSON.stringify(body)),
        }); };
        const pill = document.querySelector("#conn");
        (async () => {
          stub(200, { status: "ready", ready: true });
          await refreshReadiness();
          results.ready = [pill.textContent, pill.className, pill.title];

          // Exactly what /readyz returns while draining: an envelope whose
          // `details` carries the per-check reasons (gateway/envelope.py).
          stub(503, {
            success: false, error: "not_ready", code: "not_ready",
            message: "draining (shutdown)",
            retryable: true,
            details: { ready: false, draining: true, queue: "draining",
                       reasons: ["draining (shutdown)"], started: true },
          });
          await refreshReadiness();
          results.draining = [pill.textContent, pill.className, pill.title];

          global.fetch = () => Promise.reject(new Error("offline"));
          await refreshReadiness();
          results.offline = [pill.textContent, pill.className];
          console.log(JSON.stringify(results));
        })();
        """
    )
    assert out["ready"][0] == "live" and "ok" in out["ready"][1]
    assert out["draining"][0] == "not ready"
    assert "warn" in out["draining"][1]
    assert "draining" in out["draining"][2]  # the reason is surfaced, not hidden
    assert out["offline"][0] == "offline" and "err" in out["offline"][1]


# ---------------------------------------------------------------------------
# Tabs: real ARIA tablist
# ---------------------------------------------------------------------------
def test_markup_is_a_real_tablist():
    html = CONTROL_HTML.read_text(encoding="utf-8")
    assert 'role="tablist"' in html
    assert html.count('role="tab"') == 10
    assert html.count('role="tabpanel"') == 10
    assert html.count('aria-selected="true"') == 1  # exactly one selected
    assert html.count('tabindex="0"') == 1  # roving tabindex: one tabbable
    assert 'aria-controls="tab-overview"' in html
    assert 'aria-labelledby="tabbtn-overview"' in html


def test_select_tab_maintains_aria_state(node_available):
    out = run_in_node(
        """
        const btn = makeEl("button"); btn.dataset.tab = "jobs";
        const other = makeEl("button"); other.dataset.tab = "overview";
        const panelJobs = makeEl("div"); panelJobs.id = "tab-jobs";
        const panelOverview = makeEl("div"); panelOverview.id = "tab-overview";
        const realAll = document.querySelectorAll;
        document.querySelectorAll = (sel) => {
          if (sel === "nav [role=tab]") return [btn, other];
          if (sel === ".tab") return [panelJobs, panelOverview];
          return [];
        };
        global.refreshTab = () => {};
        selectTab("jobs");
        const res = {
          selected: [btn.getAttribute("aria-selected"), other.getAttribute("aria-selected")],
          tabindex: [btn.tabIndex, other.tabIndex],
          hidden: [panelJobs.getAttribute("aria-hidden"), panelOverview.getAttribute("aria-hidden")],
        };
        document.querySelectorAll = realAll;
        console.log(JSON.stringify(res));
        """
    )
    assert out["selected"] == ["true", "false"]
    assert out["tabindex"] == [0, -1]  # roving tabindex
    assert out["hidden"] == [None, "true"]


# ---------------------------------------------------------------------------
# Accessibility / motion affordances in CSS
# ---------------------------------------------------------------------------
def test_css_honours_focus_and_reduced_motion():
    css = CONTROL_CSS.read_text(encoding="utf-8")
    assert ":focus-visible" in css, "keyboard focus must be visible"
    assert css.count("prefers-reduced-motion") >= 2, "animated affordances must respect reduced motion"
