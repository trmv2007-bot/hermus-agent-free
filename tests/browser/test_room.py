"""The room: one screen for a person, proved in a browser at 1440 and 420.

Every assertion here is something a person would notice on the first screen:

* exactly one input line, the character, and one quiet control — and no
  telemetry panel of any kind;
* the Systems control still reaches every surface the product had before the
  room existed (the eleven tab panels named below, plus the undo journal), and
  a deep link lands on the panel it names;
* a typed message streams an answer into the conversation;
* a stream that dies still shows the stored answer (the job result outlives
  the socket);
* Stop is reachable while a turn is running;
* an approval arrives as one card in the conversation, not a modal;
* phone width holds without sideways scrolling, with the input above the fold;
* Ctrl+P opens the palette, Esc closes it, focus order follows the screen.

The model is a local OpenAI-compatible stub (``_room_model``): the gateway, the
run bus, the job result and the page are all real, and nothing is billed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:  # pytest imports this file as `browser.test_room`
    from browser import _room_model
except ImportError:  # pragma: no cover - direct module import
    import _room_model  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.browser

#: Every surface that existed before the room. The room's Systems sheet and its
#: palette are built from the same list, so this enumeration is the contract:
#: if a panel is added or renamed, this test says so instead of the surface
#: quietly disappearing.
SURFACES = [
    ("overview", "Overview"),
    ("jobs", "Jobs"),
    ("missions", "Missions"),
    ("approvals", "Approvals"),
    ("safety", "Safety"),
    ("capabilities", "Capabilities"),
    ("memory", "Memory"),
    ("presence", "Presence"),
    ("computer", "Computer"),
    ("agents", "Agents"),
    ("systems", "Diagnostics"),
]

#: The Systems sheet holds one extra destination with no tab of its own: the
#: undo journal, which lives inside Safety and answers on ``/api/v1/undo``.
EXTRA = [("safety", "Undo journal", "/control?tab=safety#undoJournal")]

TELEMETRY_SELECTORS = [
    "#transcriptBody",
    ".kpi",
    ".kpi-row",
    "table",
    ".tabrail",
    '[role="tablist"]',
    ".feed",
    ".console-panels",
    "#consolePanels",
    ".jarvis-workspace",
    "canvas",
    ".dock-bar",
    "[class*='telemetry']",
]

BOOT_TIMEOUT_S = 180


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(base: str) -> dict:
    import requests

    deadline = time.time() + BOOT_TIMEOUT_S
    last: dict = {}
    while time.time() < deadline:
        try:
            last = requests.get(base + "/readyz", timeout=5).json()
            warmup = last.get("warmup") or (last.get("details") or {}).get("warmup") or {}
            if last.get("status") == "ready" and warmup.get("state") == "warm":
                return last
        except Exception:  # noqa: BLE001 - still booting
            pass
        time.sleep(0.5)
    raise AssertionError(f"gateway never became ready: {last}")


@pytest.fixture(scope="module")
def local_model():
    """A local model on Ollama's port, so the room is talking to a real brain."""
    if _room_model.port_busy(11434):
        pytest.skip("port 11434 is already serving a model on this machine; the brain probe is pinned to it")
    server = _room_model.serve(11434)
    try:
        yield _room_model
    finally:
        _room_model.SLOW_MS = 0
        server.shutdown()


@pytest.fixture(scope="module")
def gateway(local_model, tmp_path_factory):
    """A real gateway on a free port, an isolated home, and a finished onboarding."""
    playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    try:
        with playwright.sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - no browser binary
        pytest.skip(f"chromium unavailable: {str(exc).splitlines()[0][:160]}")

    scratch = tmp_path_factory.mktemp("hermus-room")
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "onboarding.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Hermus",
                "permissions": "ask",
                "brain": {
                    "kind": "local",
                    "provider": "ollama",
                    "name": _room_model.MODEL_NAME,
                    "model": f"ollama/{_room_model.MODEL_NAME}",
                    "source": "test",
                },
                "check": {"ok": True, "reason": "", "at": None, "model": _room_model.MODEL_NAME},
                "completed_at": None,
            }
        ),
        encoding="utf-8",
    )
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "HERMUS_HOME": str(home),
            "HERMUS_DATA_DIR": str(scratch / "data"),
            "HERMUS_MODEL": f"ollama/{_room_model.MODEL_NAME}",
            "HERMUS_PROFILE": "fairy",
            "HERMUS_NO_DOTENV": "1",
            "PYTHONPATH": str(ROOT),
        }
    )
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "HERMUS_GATEWAY_TOKEN"):
        env.pop(key, None)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "hermus.py"), "gateway", "start", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    """Screenshots land beside the test run, or in ROOM_SHOTS for the deliverable."""
    wanted = os.environ.get("ROOM_SHOTS")
    target = Path(wanted) if wanted else tmp_path_factory.mktemp("room-shots")
    target.mkdir(parents=True, exist_ok=True)
    return target


class Room:
    """One page in the room, with the measurements the assertions need."""

    def __init__(self, browser, base: str, width: int):
        self.context = browser.new_context(viewport={"width": width, "height": 900})
        self.page = self.context.new_page()
        self.base = base
        self.width = width
        self.page.goto(base + "/", wait_until="load", timeout=60000)
        self.page.wait_for_timeout(1200)

    def close(self):
        self.context.close()

    def layout(self) -> dict:
        return self.page.evaluate(
            """() => {
                const vis = (el) => {
                    const r = el.getBoundingClientRect();
                    const cs = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
                };
                const box = (el) => { const r = el.getBoundingClientRect();
                    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
                const inputs = [...document.querySelectorAll('input:not([type=hidden]), textarea')].filter(vis);
                const telemetry = [];
                for (const sel of __SELECTORS__) {
                    const n = document.querySelectorAll(sel).length;
                    if (n) telemetry.push(sel + ' x' + n);
                }
                return {
                    title: document.title,
                    url: location.pathname,
                    inputs: inputs.map((i) => ({ id: i.id, box: box(i), label: i.getAttribute('aria-label') })),
                    telemetry,
                    scrollW: document.documentElement.scrollWidth,
                    innerW: window.innerWidth,
                    innerH: window.innerHeight,
                    character: (() => { const c = document.querySelector('.being'); return c && vis(c) ? box(c) : null; })(),
                    systems: (() => { const s = document.querySelector('#systemsBtn'); return s && vis(s) ? { box: box(s), text: s.innerText.trim() } : null; })(),
                    visibleText: (document.body.innerText || '').trim(),
                };
            }""".replace("__SELECTORS__", json.dumps(TELEMETRY_SELECTORS))
        )

    def ask(self, text: str) -> None:
        self.page.fill("#say", text)
        self.page.press("#say", "Enter")

    def last_reply(self) -> str:
        return self.page.evaluate(
            "() => { const r = [...document.querySelectorAll('#thread .reply')]; return r.length ? r[r.length-1].innerText.trim() : ''; }"
        )

    def wait_for_answer(self, needle: str, timeout_ms: int = 20000) -> str:
        deadline = time.time() + timeout_ms / 1000.0
        text = ""
        while time.time() < deadline:
            text = self.last_reply()
            if needle in text:
                return text
            self.page.wait_for_timeout(120)
        return text


@pytest.fixture(scope="module")
def browser(gateway):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        instance = p.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


def room_at(browser, gateway, width: int) -> Room:
    return Room(browser, gateway, width)


class TestOneScreen:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_one_input_one_character_one_door(self, browser, gateway, width, shots):
        room = room_at(browser, gateway, width)
        try:
            layout = room.layout()
            assert layout["url"] == "/companion", f"/ sent a finished install to {layout['url']}"
            assert len(layout["inputs"]) == 1, f"{len(layout['inputs'])} input lines on first paint"
            assert layout["inputs"][0]["id"] == "say"
            assert layout["telemetry"] == [], f"telemetry on the first screen: {layout['telemetry']}"
            assert layout["character"], "the character is not on the first screen"
            assert layout["systems"] and layout["systems"]["text"].lower() == "systems"
            room.page.screenshot(path=str(shots / f"room-empty-{width}.png"))
        finally:
            room.close()

    @pytest.mark.parametrize("width", [1440, 420])
    def test_it_fits_the_width_without_scrolling_sideways(self, browser, gateway, width, shots):
        room = room_at(browser, gateway, width)
        try:
            layout = room.layout()
            assert layout["scrollW"] <= layout["innerW"] + 1, (
                f"the page is {layout['scrollW']}px wide in a {layout['innerW']}px window"
            )
            field = layout["inputs"][0]["box"]
            assert field["y"] + field["h"] <= layout["innerH"], "the input line is below the fold"
            assert layout["systems"]["box"]["y"] >= 0, "the Systems control is off-screen"
            room.ask("what can you do?")
            answer = room.wait_for_answer(_room_model.ANSWER_TEXT, timeout_ms=25000)
            assert _room_model.ANSWER_TEXT in answer, f"no answer at {width}px: {answer[:200]!r}"
            after = room.layout()
            assert after["scrollW"] <= after["innerW"] + 1, "a full exchange pushed the page sideways"
            room.page.screenshot(path=str(shots / f"room-answer-{width}.png"))
        finally:
            room.close()


class TestTheConversationIsThePage:
    def test_a_typed_message_streams_an_answer(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.ask("what can you do?")
            text = room.wait_for_answer(_room_model.ANSWER_TEXT)
            assert _room_model.ANSWER_TEXT in text, f"the answer never arrived: {text[:200]!r}"
            assert room.page.evaluate("() => document.querySelectorAll('#thread .exchange').length") == 1
            assert room.page.evaluate("() => document.querySelector('#thread .said').innerText").strip()
        finally:
            room.close()

    def test_stop_is_reachable_while_a_turn_runs(self, browser, gateway):
        _room_model.SLOW_MS = 4000
        room = room_at(browser, gateway, 1440)
        try:
            room.ask("say something slow")
            room.page.wait_for_timeout(700)
            mode = room.page.evaluate(
                """() => { const b = document.querySelector('#go');
                    return { mode: b.dataset.mode, text: b.innerText.trim(), visible: b.getBoundingClientRect().height > 0 }; }"""
            )
            assert mode["visible"], "the stop button is not reachable mid-turn"
            assert mode["mode"] == "stop" and mode["text"].lower() == "stop", mode
            room.page.click("#go")
            room.page.wait_for_timeout(1200)
            state = room.page.evaluate(
                "() => ({ mode: document.querySelector('#go').dataset.mode, reply: (document.querySelector('#thread .reply') || {}).innerText })"
            )
            assert state["mode"] == "send", "the line did not come back after Stop"
            assert "Stopped" in (state["reply"] or "") or "took" in (state["reply"] or ""), state
        finally:
            _room_model.SLOW_MS = 0
            room.close()

    def test_a_failed_stream_falls_back_to_the_stored_result(self, browser, gateway):
        """G7's lesson: the run stream is a live channel, not the record."""
        room = room_at(browser, gateway, 1440)

        def trim_to_nothing(route):
            response = route.fetch()
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            route.fulfill(status=response.status, headers=headers, body='event: run_finished\ndata: {"status":"finished"}\n\n')

        room.page.route("**/stream/command", trim_to_nothing)
        try:
            room.ask("what can you do?")
            text = room.wait_for_answer(_room_model.ANSWER_TEXT, timeout_ms=30000)
            assert _room_model.ANSWER_TEXT in text, f"the stored answer was not recovered: {text[:200]!r}"
        finally:
            room.close()

    def test_an_approval_is_one_inline_card_not_a_modal(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        posted: list[str] = []
        room.page.on("request", lambda req: posted.append(req.url) if "/permissions/pending/resolve" in req.url else None)
        room.page.route(
            "**/permissions/pending/resolve",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "success": True,
                        "id": "approval_test",
                        "retry": {"success": True, "result": {"ok": True, "output": "renamed 14 files"}},
                    }
                ),
            ),
        )

        def stream_an_approval(route):
            body = (
                'event: approval_required\ndata: {"data":{"tool":"write_file","approval_request":'
                '{"id":"approval_test","title":"Approve write_file","tool":"write_file",'
                '"suggested_purpose":"It wants to rename 14 screenshots on your Desktop.",'
                '"suggested_resources":["C:\\\\Users\\\\rishi\\\\Desktop"]}}}\n\n'
                'event: run_finished\ndata: {"status":"finished"}\n\n'
            )
            route.fulfill(status=200, content_type="text/event-stream", body=body)

        room.page.route("**/stream/command", stream_an_approval)
        try:
            room.ask("rename my screenshots")
            room.page.wait_for_selector("#thread .card-inline", timeout=15000)
            in_thread = room.page.evaluate("() => !!document.querySelector('#thread .card-inline')")
            assert in_thread, "the approval is not inside the conversation"
            assert (
                room.page.evaluate("() => document.querySelectorAll('dialog[open], [role=dialog][aria-hidden=false]').length")
                == 0
            ), "the approval opened a modal"
            assert "rename 14 screenshots" in room.page.inner_text("#thread .card-inline")
            room.page.click("#thread .card-inline button:has-text('Approve')")
            room.page.wait_for_timeout(600)
            assert any("/permissions/pending/resolve" in u for u in posted), "Approve never reached the approval endpoint"
            assert room.page.evaluate("() => document.querySelector('#thread .card-inline').dataset.resolved") == "true"
        finally:
            room.close()


class TestSystemsStillReachesEverything:
    """The sheet answers what the room can do; the palette stays the index of
    every surface. Nothing the product had may become unreachable because the
    sheet stopped being a link list."""

    def test_the_palette_still_names_every_surface_the_product_had(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.keyboard.press("Control+p")
            room.page.wait_for_timeout(300)
            hrefs = room.page.evaluate(
                "() => [...document.querySelectorAll('#palette .palette-item')].map(a => a.getAttribute('href'))"
            )
            for tab, name in SURFACES:
                assert any(f"tab={tab}" in h for h in hrefs), f"{name} (/control?tab={tab}) has no door"
            for tab, name, href in EXTRA:
                assert href in hrefs, f"{name} has no door"
        finally:
            room.close()

    def test_the_sheet_answers_instead_of_listing_the_grid(self, browser, gateway, shots):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.click("#systemsBtn")
            room.page.wait_for_timeout(300)
            assert room.page.evaluate("() => document.querySelector('#systems').open === true"), (
                "the Systems control opened nothing"
            )
            room.page.wait_for_selector("#systemsRows:not([hidden])", timeout=30000)
            rows = room.page.evaluate("() => document.querySelectorAll('#systemsRows .sheet-row').length")
            assert rows == 4, f"Systems lists {rows} rows instead of four answers"
            assert (
                room.page.evaluate("() => document.querySelectorAll('#systems #consolePanels, #systems .console-card').length")
                == 0
            ), "the panel grid rendered inside the Systems sheet"
            href = room.page.evaluate("() => document.querySelector('.sheet-telemetry-link').getAttribute('href')")
            assert href == "/control?tab=systems", "Diagnostics no longer reaches the console"
            room.page.screenshot(path=str(shots / "systems-sheet-1440.png"))
        finally:
            room.close()

    def test_the_undo_journal_still_answers(self, gateway):
        import requests

        body = requests.get(gateway + "/api/v1/undo?limit=5", timeout=20).json()
        assert body.get("success") is True
        assert isinstance(body.get("entries"), list)
        page = requests.get(gateway + "/control?tab=safety", timeout=20)
        assert page.status_code == 200
        assert 'id="undoJournal"' in page.text, "the undo journal has no home in the control room"

    def test_every_tab_panel_still_exists_on_the_control_room(self, gateway):
        import requests

        html = requests.get(gateway + "/control", timeout=20).text
        for tab, name in SURFACES:
            assert f'data-tab="{tab}"' in html, f"the {name} tab is gone from /control"
            assert f'id="tab-{tab}"' in html, f"the {name} panel is gone from /control"
        # The door back to the room, so the deep surface is not a trap.
        assert 'id="roomLink"' in html

    def test_a_deep_link_lands_on_the_panel_it_names(self, browser, gateway):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            page.goto(gateway + "/control?tab=safety#undoJournal", wait_until="load", timeout=60000)
            page.wait_for_timeout(1200)
            selected = page.evaluate(
                "() => (document.querySelector('nav [role=tab][aria-selected=true]') || {}).dataset?.tab || ''"
            )
            assert selected == "safety", f"deep link selected {selected!r}"
            assert page.evaluate("() => document.querySelector('#tab-safety').hidden === false")
        finally:
            context.close()


class TestKeyboardFirst:
    def test_ctrl_p_opens_the_palette_and_esc_closes_it(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.keyboard.press("Control+p")
            room.page.wait_for_timeout(300)
            assert room.page.evaluate("() => document.querySelector('#palette').classList.contains('open')")
            items = room.page.evaluate(
                "() => [...document.querySelectorAll('#palette .palette-item')].map(a => a.innerText.trim())"
            )
            assert len(items) == len(SURFACES) + len(EXTRA), f"the palette lists {len(items)} destinations"
            for _tab, name in SURFACES:
                assert any(name in item for item in items), f"{name} is missing from the palette"
            room.page.keyboard.press("Escape")
            room.page.wait_for_timeout(250)
            assert room.page.evaluate("() => !document.querySelector('#palette').classList.contains('open')"), (
                "Esc did not close the palette"
            )
            focused = room.page.evaluate("() => document.activeElement.id")
            assert focused in ("systemsBtn", "say"), f"focus went to {focused!r} after closing the palette"
        finally:
            room.close()

    def test_esc_closes_the_systems_sheet(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.click("#systemsBtn")
            room.page.wait_for_timeout(250)
            assert room.page.evaluate("() => document.querySelector('#systems').open === true")
            room.page.keyboard.press("Escape")
            room.page.wait_for_timeout(250)
            assert room.page.evaluate("() => document.querySelector('#systems').open === false")
        finally:
            room.close()

    def test_tab_order_follows_the_screen(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.evaluate("() => document.activeElement && document.activeElement.blur()")
            order = []
            for _ in range(8):
                room.page.keyboard.press("Tab")
                order.append(
                    room.page.evaluate("() => { const a = document.activeElement; return a ? (a.id || a.tagName) : 'none'; }")
                )
            # Chrome resumes from the last focused element (the input, by
            # autofocus) instead of restarting, so the assertion is the order of
            # the sequence, not its starting index.
            expected = ["skipToInput", "systemsBtn", "say", "go"]
            start = order.index(expected[0]) if expected[0] in order else -1
            assert start >= 0, f"the skip link is not in the tab order: {order}"
            rotated = order[start:] + order[:start]
            assert rotated[: len(expected)] == expected, f"tab order is {order}"
        finally:
            room.close()

    def test_every_control_shows_a_focus_ring(self, browser, gateway):
        room = room_at(browser, gateway, 1440)
        try:
            room.page.evaluate("() => document.activeElement && document.activeElement.blur()")
            for target in ("systemsBtn", "say", "go"):
                reached = False
                for _ in range(8):
                    room.page.keyboard.press("Tab")
                    if room.page.evaluate("() => document.activeElement.id") == target:
                        reached = True
                        break
                assert reached, f"{target} cannot be reached with Tab"
                if target == "say":
                    # The input sits inside the line; the line is what lights up.
                    ring = room.page.evaluate(
                        """() => { const cs = getComputedStyle(document.querySelector('.line'));
                            return { shadow: cs.boxShadow, border: cs.borderTopColor }; }"""
                    )
                    assert ring["shadow"] and ring["shadow"] != "none", "the input line shows no focus indication"
                    continue
                outline = room.page.evaluate(
                    """(sel) => { const cs = getComputedStyle(document.getElementById(sel));
                        return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth) || 0 }; }""",
                    target,
                )
                assert outline["style"] != "none" and outline["width"] > 0, f"{target} has no focus ring: {outline}"
        finally:
            room.close()
