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
* the tab strip is a real ARIA tablist with roving tabindex and keyboard nav;
* the Settings tab renders the key pool without ever putting a secret in the
  DOM, reads the pool limits before saving them, and refuses to call a check
  that failed "up to date".

Node is optional (``shutil.which("node")``) — like the existing JS syntax test,
these skip when Node is absent rather than failing an unrelated environment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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
    """Boot control-room.js in Node with the DOM stub, then run `body`.

    The harness is written to a temp file and passed as a path instead of
    ``node -e``: the concatenated stub + control-room.js + body exceeds
    Windows' ~32k command-line limit once control-room.js grew past ~30KB,
    which made every harness test fail with EINVAL regardless of content.
    """
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
    harness_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", encoding="utf-8", delete=False, dir=str(ROOT)
        ) as fh:
            fh.write(harness)
            harness_path = fh.name
        proc = subprocess.run(
            ["node", harness_path],
            capture_output=True,
            text=True,
            # Node writes UTF-8; the pane's rendered HTML contains em dashes and
            # curly quotes, which the Windows locale codec cannot decode.
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            timeout=60,
        )
    finally:
        if harness_path:
            try:
                os.unlink(harness_path)
            except OSError:
                pass
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


def test_settings_does_not_duplicate_the_update_pull_action():
    """Installing an update is destructive and the Systems console already gates
    it behind a confirm. A second button for the same POST would be a second
    policy to keep in sync, so Settings only reports the state."""
    room = ROOM_JS.read_text(encoding="utf-8")
    start = room.index("// ---------- SETTINGS")
    settings_block = room[start : room.index("// ---------- wiring", start)]
    assert "/update/pull" not in settings_block
    assert "/update/check" in settings_block


def test_every_endpoint_settings_calls_is_routable():
    """The pool config path is the trap here: the router is mounted under a
    prefix, so ``/pool/config`` 404s while the real path resolves. Checked
    against the OpenAPI schema, which is built from what actually resolves."""
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    room = ROOM_JS.read_text(encoding="utf-8")
    start = room.index("// ---------- SETTINGS")
    settings_block = room[start : room.index("// ---------- wiring", start)]
    called = set(re.findall(r'"(/[a-z0-9\-_/]+)"', settings_block))
    assert {"/keys/list", "/keys/add", "/api/v1/agents/pool/config", "/update/check"} <= called, called

    routes = set(TestClient(app).get("/openapi.json").json().get("paths", {}))
    missing = sorted(path for path in called if path not in routes)
    assert not missing, f"called by the Settings tab but not routable: {missing}"


def test_settings_key_table_never_renders_a_secret(node_available):
    """/keys/list is redacted server-side. The panel must render the preview and
    nothing else — a raw ``key`` field in the payload is the leak to prove out."""
    out = run_in_node(
        """
        (async () => {
          const payload = {
            llm_keys: {
              groq: [
                { name: "prod", preview: "gsk_ab...9z", healthy: true, health_status: "ok",
                  models_count: 3, rpm_limit: 30, key: "gsk_SECRET_MUST_NOT_RENDER" },
                { name: "quiet", preview: "gsk_xy...11", healthy: false, models_count: 0,
                  key: "gsk_SECOND_SECRET_MUST_NOT_RENDER" },
              ],
              hf: [],
            },
            custom_apis: [{ name: "weather", preview: "no-token", id: "custom_weather_1" }],
            total_llm_keys: 2,
            total_custom_apis: 1,
          };
          global.fetch = async () => ({
            ok: true, status: 200, headers: { get: () => "req_1" },
            text: async () => JSON.stringify(payload),
          });
          await refreshSettingsKeys();
          console.log(JSON.stringify({
            rows: REGISTRY["#settingsKeys tbody"].innerHTML,
            cells: REGISTRY["#settingsKeysSummary"].innerHTML,
          }));
        })();
        """
    )
    assert "gsk_ab...9z" in out["rows"] and "gsk_xy...11" in out["rows"]
    assert "MUST_NOT_RENDER" not in out["rows"]
    # An untested key says so; a custom API has no health record to show.
    assert "untested" in out["rows"] and "no health record" in out["rows"]
    assert "ok" in out["rows"]
    assert "failing" in out["cells"] and "custom APIs" in out["cells"]


def test_settings_update_failure_is_unknown_not_current(node_available):
    """The management routes answer {"error": ...} with a 200, so a check that
    failed has to read as unknown — never as an up-to-date install."""
    out = run_in_node(
        """
        (async () => {
          const results = {};
          const serve = (body) => { global.fetch = async () => ({
            ok: true, status: 200, headers: { get: () => "req_2" },
            text: async () => JSON.stringify(body),
          }); };
          serve({ error: "git ls-remote failed" });
          await refreshSettingsUpdate();
          results.failed = [REGISTRY["#settingsUpdate"].innerHTML, REGISTRY["#settingsUpdateFeed"].innerHTML];
          serve({ update_available: false, up_to_date: true, local: { short: "aaaa111" }, remote: { short: "aaaa111" } });
          await refreshSettingsUpdate();
          results.current = [REGISTRY["#settingsUpdate"].innerHTML, REGISTRY["#settingsUpdateFeed"].innerHTML];
          console.log(JSON.stringify(results));
        })();
        """
    )
    assert "unknown" in out["failed"][0] and "git ls-remote failed" in out["failed"][1]
    assert "current" in out["current"][0] and "unknown" not in out["current"][0]


def test_settings_pool_save_refuses_a_non_number_and_sends_only_filled_fields(node_available):
    out = run_in_node(
        """
        (async () => {
          // Boot-time probes also go through fetch, so only the pool writes count.
          const calls = [];
          global.fetch = async (url, opts) => {
            calls.push([url, (opts || {}).body]);
            return { ok: true, status: 200, headers: { get: () => "req_3" }, text: async () => '{"status":"ok"}' };
          };
          const posted = () => calls.filter((c) => c[0] === "/api/v1/agents/pool/config");
          document.querySelector("#settingsAddOut");
          document.querySelector("#poolMaxAgents").value = "12";
          document.querySelector("#poolMaxConcurrent").value = "ten";
          document.querySelector("#poolIdleTimeout").value = "";
          document.querySelector("#poolCleanupInterval").value = "";
          saveSettingsPool();
          await new Promise((r) => setTimeout(r, 0));
          const rejected = [posted().length, REGISTRY["#settingsAddOut"].innerHTML];
          document.querySelector("#poolMaxConcurrent").value = "4";
          saveSettingsPool();
          await new Promise((r) => setTimeout(r, 0));
          console.log(JSON.stringify({ rejected, accepted: posted()[0] }));
        })();
        """
    )
    assert out["rejected"][0] == 0, "a non-numeric limit must not reach the gateway"
    assert "max_concurrent" in out["rejected"][1]
    sent = json.loads(out["accepted"][1])
    assert sent == {"max_agents": 12, "max_concurrent": 4}


def test_settings_provider_picker_comes_from_the_registry(node_available):
    """The dropdown is the gateway's own provider list — 22 entries a person
    cannot mistype — and a retired one stays out of it."""
    out = run_in_node(
        """
        (async () => {
          const payload = { providers: [
            { id: "groq", name: "Groq", no_auth: false, default_model: "llama-3.3-70b", notes: "Free tier 30 RPM" },
            { id: "ollama", name: "Ollama (local)", no_auth: true },
            { id: "github", name: "GitHub Models", no_auth: false, retired: true },
          ] };
          global.fetch = async () => ({ ok: true, status: 200, headers: { get: () => "req_p" },
            text: async () => JSON.stringify(payload) });
          await refreshSettingsProviders();
          const select = REGISTRY["#addKeyProvider"];
          const ids = (select.innerHTML.match(/value="([^"]+)"/g) || []);
          const groqHint = REGISTRY["#addKeyHint"].textContent;
          select.value = "ollama";
          describeProviderChoice();
          console.log(JSON.stringify({ ids, groqHint, localHint: REGISTRY["#addKeyHint"].textContent }));
        })();
        """
    )
    assert 'value="groq"' in out["ids"] and 'value="ollama"' in out["ids"]
    assert "github" not in out["ids"]
    assert "llama-3.3-70b" in out["groqHint"] and "runs without a key" not in out["groqHint"]
    assert "runs without a key" in out["localHint"]


def test_settings_add_key_accepts_a_keyless_local_provider(node_available):
    """A keyless provider must not be blocked by the form, and one that needs a
    key must be caught before a request that would only be refused."""
    out = run_in_node(
        """
        (async () => {
          const posts = [];
          global.fetch = async (url, opts) => {
            if ((opts || {}).method === "POST") posts.push([url, opts.body]);
            return { ok: true, status: 200, headers: { get: () => "req_k" }, text: async () => '{"status":"ok"}' };
          };
          PROVIDER_PRESETS.groq = { id: "groq", no_auth: false };
          PROVIDER_PRESETS.ollama = { id: "ollama", no_auth: true };
          const provider = document.querySelector("#addKeyProvider");
          const key = document.querySelector("#addKeyValue");
          const outEl = document.querySelector("#settingsAddOut");
          provider.value = "groq"; key.value = "";
          addSettingKey();
          const blocked = [posts.slice(), outEl.innerHTML];
          provider.value = "ollama";
          addSettingKey();
          await new Promise((r) => setTimeout(r, 0));
          console.log(JSON.stringify({ blocked, posts }));
        })();
        """
    )
    assert out["blocked"][0] == [], "a provider that needs a key must not be posted without one"
    assert "needs a key value" in out["blocked"][1]
    assert [p[0] for p in out["posts"]] == ["/keys/add"]
    assert json.loads(out["posts"][0][1]) == {"provider": "ollama", "key": ""}


def test_settings_remove_needs_the_click_twice(node_available):
    """Deleting a credential is not something a stray click should do, and a
    modal confirm is something nobody reads."""
    out = run_in_node(
        """
        (async () => {
          const posts = [];
          global.fetch = async (url, opts) => {
            if ((opts || {}).method === "POST") posts.push([url, opts.body]);
            return { ok: true, status: 200, headers: { get: () => "req_r" }, text: async () => '{"success":true}' };
          };
          const btn = makeEl("button");
          btn.dataset.credentialKind = "key";
          btn.dataset.credentialProvider = "groq";
          btn.dataset.credentialRef = "work laptop";
          armOrRemove(btn);
          const first = [posts.slice(), btn.textContent, btn.dataset.armed];
          armOrRemove(btn);
          await new Promise((r) => setTimeout(r, 0));
          console.log(JSON.stringify({ first, posts }));
        })();
        """
    )
    assert out["first"][0] == [], "the first click must only arm the button"
    assert "confirm" in out["first"][1] and out["first"][2] == "1"
    assert [p[0] for p in out["posts"]] == ["/keys/remove"]
    assert json.loads(out["posts"][0][1]) == {"provider": "groq", "name": "work laptop", "key": "work laptop"}


def test_settings_filter_narrows_the_rendered_rows_without_refetching(node_available):
    out = run_in_node(
        """
        (async () => {
          let fetches = 0;
          const payload = { llm_keys: { groq: [
            { name: "work", preview: "gsk_a...1", healthy: true, health_status: "ok" },
            { name: "spare", preview: "gsk_b...2", healthy: false, health_status: "auth_failed" },
          ] }, custom_apis: [] };
          // Only the pool probe counts: boot-time readiness calls land here too.
          global.fetch = async (url) => { if (url === "/keys/list") fetches += 1; return { ok: true, status: 200,
            headers: { get: () => "req_f" }, text: async () => JSON.stringify(payload) }; };
          await refreshSettingsKeys();
          const all = REGISTRY["#settingsKeys tbody"].innerHTML;
          document.querySelector("#credentialFilter").value = "auth_failed";
          renderCredentialRows();
          const filtered = REGISTRY["#settingsKeys tbody"].innerHTML;
          document.querySelector("#credentialFilter").value = "nothing-matches-this";
          renderCredentialRows();
          const empty = REGISTRY["#settingsKeys tbody"].innerHTML;
          console.log(JSON.stringify({ fetches, all, filtered, empty }));
        })();
        """
    )
    assert out["fetches"] == 1, "filtering is local; it must not re-probe the gateway"
    assert "work" in out["all"] and "spare" in out["all"]
    assert "spare" in out["filtered"] and ">work<" not in out["filtered"]
    assert "nothing matches" in out["empty"]


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
    for expected in ("jobs", "missions", "computer", "remote", "doctor", "telemetry", "chat"):
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
    assert html.count('role="tab"') == 11
    assert html.count('role="tabpanel"') == 11
    assert html.count('aria-selected="true"') == 1  # exactly one selected
    assert html.count('tabindex="0"') == 1  # roving tabindex: one tabbable
    assert 'aria-controls="tab-chat"' in html
    assert 'aria-labelledby="tabbtn-chat"' in html
    # The chat face replaced both of them; neither may come back as a tab.
    assert 'data-tab="overview"' not in html
    assert 'data-tab="telemetry"' not in html


def test_settings_tab_is_wired_to_a_panel_and_a_refresher():
    """A tab whose panel or refresher is missing renders as an empty room."""
    html = CONTROL_HTML.read_text(encoding="utf-8")
    room = ROOM_JS.read_text(encoding="utf-8")
    assert 'data-tab="settings"' in html
    assert 'id="tab-settings" role="tabpanel" aria-labelledby="tabbtn-settings"' in html
    assert 'name === "settings") refreshSettings()' in room
    assert "RETRY_ACTIONS.settings = refreshSettings" in room


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
