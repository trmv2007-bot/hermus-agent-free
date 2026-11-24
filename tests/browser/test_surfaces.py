"""Nothing a person opens first looks like a status board.

Rishi opened the product and saw a grid of 58 panel cards reading "pending",
"not probed yet", and a row of counters. None of that is a product. These tests
pin the surfaces a person actually meets:

* the landing and the Systems sheet never show a probe state, a panel count, or
  a raw endpoint list, at 1440 or 420;
* Systems answers what the room can do here in four sentences, each with a
  state and only a missing one carrying the single command that fixes it;
* the gateway's panel grid stays behind one Diagnostics disclosure that is
  closed by default, and the sheet never renders that grid inline;
* the control room does not open on the grid either.

The gateway is real, the pages are real; nothing here needs a model, so the
suite runs on a machine where Ollama is already serving.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _real_home_guard import temp_home

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.browser

#: Words that turn a surface into a refurbishment site's status board.
PROBE_WORDS = (
    "pending",
    "not probed",
    "needs_install",
    "endpoints verified",
    "declared actions",
)

#: "58 panels", "55 actions" and friends: a count of machinery, never of value.
PANEL_COUNTER = re.compile(r"\b\d+\s+(?:internal\s+)?(?:panels?|endpoints?|actions?)\b", re.IGNORECASE)

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
            if last.get("status") == "ready":
                return last
        except Exception:  # noqa: BLE001 - still booting
            pass
        time.sleep(0.5)
    raise AssertionError(f"gateway never became ready: {last}")


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    """A real gateway on a free port with an isolated home and a finished setup."""
    playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    try:
        with playwright.sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - no browser binary
        pytest.skip(f"chromium unavailable: {str(exc).splitlines()[0][:160]}")

    scratch = tmp_path_factory.mktemp("hermus-surfaces")
    home = temp_home(tmp_path_factory, scratch / "home")
    data = temp_home(tmp_path_factory, scratch / "data")
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
                    "name": "surfaces-test:1b",
                    "model": "ollama/surfaces-test:1b",
                    "source": "test",
                },
                "check": {"ok": True, "reason": "", "at": None, "model": "surfaces-test:1b"},
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
            "HERMUS_DATA_DIR": str(data),
            "HERMUS_MODEL": "ollama/surfaces-test:1b",
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
def browser(gateway):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        instance = p.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    wanted = os.environ.get("SURFACE_SHOTS")
    target = Path(wanted) if wanted else tmp_path_factory.mktemp("surface-shots")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _open(browser, base: str, width: int):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.goto(base + "/", wait_until="load", timeout=60000)
    page.wait_for_timeout(1200)
    return context, page


def _assert_no_status_board(text: str, where: str) -> None:
    low = text.lower()
    for word in PROBE_WORDS:
        assert word not in low, f"{where} shows {word!r}: {text[:400]!r}"
    counter = PANEL_COUNTER.search(text)
    assert counter is None, f"{where} counts machinery: {counter.group(0)!r}"


def _open_sheet(page) -> None:
    page.click("#systemsBtn")
    page.wait_for_selector("#systemsRows:not([hidden])", timeout=30000)


def _sheet_text(page) -> str:
    return page.evaluate("() => document.querySelector('#systems').innerText")


class TestTheLanding:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_the_first_screen_is_a_room_not_a_board(self, browser, gateway, width, shots):
        context, page = _open(browser, gateway, width)
        try:
            text = page.inner_text("body")
            _assert_no_status_board(text, f"the landing at {width}px")
            assert page.evaluate("() => document.querySelectorAll('.console-card, #consolePanels, .kpi').length") == 0
            # The room's own first-screen guard matches classes containing
            # "telemetry": the Diagnostics door must not wear that name.
            assert page.evaluate("() => document.querySelectorAll(\"[class*='telemetry']\").length") == 0
            page.screenshot(path=str(shots / f"room-after-onboarding-{width}.png"))
        finally:
            context.close()


class TestSystemsIsAnAnswer:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_four_sentences_and_no_probe_state(self, browser, gateway, width, shots):
        context, page = _open(browser, gateway, width)
        try:
            _open_sheet(page)
            text = _sheet_text(page)
            _assert_no_status_board(text, f"Systems at {width}px")

            rows = page.evaluate(
                """() => [...document.querySelectorAll('#systemsRows .sheet-row')].map((r) => ({
                    label: r.querySelector('.sheet-label').innerText.trim(),
                    value: r.querySelector('.sheet-value').innerText.trim(),
                    state: r.dataset.state || '',
                }))"""
            )
            assert [r["label"] for r in rows] == ["Model", "Voice", "Computer", "Memory"]
            for row in rows:
                assert row["value"], f"{row['label']} says nothing"
                assert row["state"] in ("on", "off", "unknown"), row

            # The grid is not in the sheet, and not in the DOM behind it either.
            assert page.evaluate("() => document.querySelectorAll('#systems #consolePanels, #systems .console-card').length") == 0
            assert page.evaluate("() => document.querySelector('#systemsDiagnostics').open === false"), (
                "Diagnostics is open by default"
            )
            assert page.evaluate("() => document.querySelectorAll(\"[class*='telemetry']\").length") == 0
            # The door is styled as part of the sheet, not left as raw chrome.
            assert page.evaluate("() => getComputedStyle(document.querySelector('#systemsDiagnostics')).borderTopWidth") == "1px"
            page.screenshot(path=str(shots / f"systems-{width}.png"))
        finally:
            context.close()

    def test_a_missing_capability_carries_one_command(self, browser, gateway):
        context, page = _open(browser, gateway, 1440)
        try:
            # A machine with nothing installed: the sheet still answers, and each
            # missing row names exactly one way back.
            route_json = lambda route, body: route.fulfill(  # noqa: E731
                status=200, content_type="application/json", body=json.dumps(body)
            )
            page.route(
                "**/api/v1/onboarding/state",
                lambda r: route_json(
                    r,
                    {
                        "complete": True,
                        "name": "Hermus",
                        "resolved": {"kind": "stub", "name": "stub", "reachable": False},
                        "fix": "ollama pull llama3.2:1b",
                    },
                ),
            )
            page.route(
                "**/voice/live/status",
                lambda r: route_json(
                    r,
                    {
                        "stt": {
                            "available": False,
                            "reason": "local speech-to-text is not installed",
                            "fix": "pip install faster-whisper",
                        },
                        "tts": {"available": False},
                    },
                ),
            )
            page.route(
                "**/memory/stats",
                lambda r: route_json(r, {"total": 9, "index": {"available": False}}),
            )
            _open_sheet(page)
            rows = {
                r["label"]: r
                for r in page.evaluate(
                    """() => [...document.querySelectorAll('#systemsRows .sheet-row')].map((row) => ({
                        label: row.querySelector('.sheet-label').innerText.trim(),
                        value: row.querySelector('.sheet-value').innerText.trim(),
                        fix: (row.querySelector('.sheet-fix') || {}).innerText || '',
                    }))"""
                )
            }
            assert rows["Model"]["fix"] == "ollama pull llama3.2:1b"
            assert rows["Voice"]["fix"] == "pip install faster-whisper"
            assert rows["Memory"]["fix"] == "hermus mem2 reindex"
            assert "Off:" in rows["Voice"]["value"]
            _assert_no_status_board(_sheet_text(page), "Systems with nothing installed")
        finally:
            context.close()


class TestDiagnosticsIsDeliberate:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_one_closed_door_then_the_console(self, browser, gateway, width, shots):
        context, page = _open(browser, gateway, width)
        try:
            _open_sheet(page)
            assert page.evaluate("() => document.querySelector('#systemsDiagnostics').open === false")

            page.click("#systemsDiagnostics summary")
            page.wait_for_timeout(250)
            opened = page.evaluate("() => document.querySelector('#systemsDiagnostics').open")
            assert opened is True, "the Diagnostics control did not open"

            body = page.evaluate("() => document.querySelector('.sheet-diagnostics-body').innerText").lower()
            assert "developer" in body, f"Diagnostics does not say who it is for: {body!r}"
            assert "nothing here is needed" in body, "Diagnostics does not say it is optional"
            href = page.evaluate("() => document.querySelector('.sheet-diagnostics-link').getAttribute('href')")
            assert href == "/control?tab=systems", href
            marked = page.evaluate("""() => getComputedStyle(document.querySelector('.sheet-diagnostics-mark')).transform""")
            assert marked not in ("", "none"), "the chevron does not turn when the door opens"
            assert (
                page.evaluate("() => document.querySelectorAll('#systems #consolePanels, #systems .console-card').length") == 0
            ), "the panel grid rendered inside the room"
            page.screenshot(path=str(shots / f"systems-diagnostics-{width}.png"))
        finally:
            context.close()

    def test_the_control_room_does_not_open_on_the_grid(self, browser, gateway):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            page.goto(gateway + "/control", wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            selected = page.evaluate(
                "() => (document.querySelector('nav [role=tab][aria-selected=true]') || {}).dataset?.tab || ''"
            )
            assert selected and selected != "systems", f"the control room opened on {selected!r}"
            visible = page.evaluate(
                """() => { const g = document.querySelector('#consolePanels');
                    if (!g) return false; const r = g.getBoundingClientRect();
                    return r.width > 0 && r.height > 0; }"""
            )
            assert visible is False, "the panel grid is on screen on load"
        finally:
            context.close()

    def test_the_grid_never_calls_itself_pending(self, browser, gateway):
        """The deliberate surface may be dense, but it is never unfinished: the
        console chrome carries no probe state, and a card is only drawn once its
        probe answered. (A *value* inside a panel may read "pending" — that is
        the gateway's own data, which is the point of this surface.)"""
        chrome = """() => ({
            summary: (document.querySelector('#consoleSummary').innerText || '').toLowerCase(),
            grid: (document.querySelector('#consolePanels').innerText || '').toLowerCase(),
            tags: [...document.querySelectorAll('#consolePanels .tag')].map((t) => t.innerText.trim().toLowerCase()),
        })"""

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            page.goto(gateway + "/control?tab=systems", wait_until="load", timeout=60000)
            page.wait_for_timeout(120)
            early = page.evaluate(chrome)
            assert "pending" not in early["summary"] + early["grid"], early
            assert "not probed" not in early["grid"], early

            page.wait_for_selector("#consolePanels .console-card", timeout=120000)
            page.wait_for_timeout(500)
            later = page.evaluate(chrome)
            for word in ("pending", "not probed"):
                assert word not in later["summary"], f"the console summary says {word!r}"
                assert word not in later["tags"], f"a card state chip says {word!r}"
            assert later["summary"].startswith(("all ", "")) or "probes did not answer" in later["summary"]
        finally:
            context.close()
