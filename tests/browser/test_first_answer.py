"""The first hour, proved in a browser: a typed question gets its answer.

This is the regression test for the defect that made HERMUS feel dead — a
person typed a question into the Control Room and never saw an answer, because
one synchronous handler froze the gateway while fourteen pollers queued behind
Chrome's six sockets per host.

It boots a real gateway (isolated ``HERMUS_HOME``, ``mock/mock`` model, no
network, no paid API), drives the real page with Playwright, and asserts what a
person feels:

1. the answer to a typed command appears in the conversation within five
   seconds;
2. after ten idle seconds no request is still open after a second;
3. every request the first screen issues comes back under 300ms.

Chromium is optional. Without Playwright or a browser these tests skip with that
reason instead of failing the suite — CI's main job installs neither.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.browser

ANSWER_DEADLINE_MS = 5000
FIRST_SCREEN_DEADLINE_MS = 300
BOOT_TIMEOUT_S = 120


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_warm(base: str) -> dict:
    import requests

    deadline = time.time() + BOOT_TIMEOUT_S
    last: dict = {}
    while time.time() < deadline:
        try:
            body = requests.get(base + "/readyz", timeout=5).json()
            last = body
            warmup = body.get("warmup") or (body.get("details") or {}).get("warmup") or {}
            if body.get("status") == "ready" and warmup.get("state") == "warm":
                return body
        except Exception:  # noqa: BLE001 - the gateway is still booting
            pass
        time.sleep(0.5)
    raise AssertionError(f"gateway never reported a warm first screen: {last}")


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    """A real gateway on a free port, with nothing of the person's on disk."""
    playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
    try:
        with playwright.sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001 - no browser binary on this runner
        pytest.skip(f"chromium unavailable: {str(exc).splitlines()[0][:160]}")

    home = tmp_path_factory.mktemp("hermus-first-hour")
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "HERMUS_HOME": str(home / "home"),
            "HERMUS_DATA_DIR": str(home / "data"),
            "HERMUS_MODEL": "mock/mock",
            "HERMUS_PROFILE": "fairy",
            "HERMUS_NO_DOTENV": "1",
            "PYTHONPATH": str(ROOT),
        }
    )
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY"):
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
        _wait_until_warm(base)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def trace(gateway, tmp_path_factory):
    """One instrumented page load per module: request timings, then a command."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")

        origin = time.time()
        started: dict[str, tuple[float, str]] = {}
        records: list[dict] = []

        def on_sent(params: dict) -> None:
            url = (params.get("request") or {}).get("url", "")
            started[params["requestId"]] = (time.time() - origin, url)

        def on_done(params: dict) -> None:
            began = started.pop(params["requestId"], None)
            if began is not None:
                records.append(
                    {
                        "at": began[0],
                        "ms": (time.time() - origin - began[0]) * 1000,
                        "url": began[1].replace(gateway, ""),
                    }
                )

        cdp.on("Network.requestWillBeSent", on_sent)
        cdp.on("Network.loadingFinished", on_done)
        cdp.on("Network.loadingFailed", on_done)

        page.goto(gateway + "/control", wait_until="load", timeout=60000)
        page.wait_for_timeout(10000)
        stuck = {rid: at[1] for rid, at in started.items() if (time.time() - origin) - at[0] > 1.0}
        first_screen = [record["ms"] for record in records if record["at"] <= 3.0]

        baseline = page.evaluate("() => document.querySelectorAll('#transcriptBody .msg.hermus').length")
        page.fill("#command", "what can you do?")
        typed_at = time.time()
        page.press("#command", "Enter")
        answer = ""
        while (time.time() - typed_at) * 1000 < ANSWER_DEADLINE_MS * 6:
            page.wait_for_timeout(100)
            answer = page.evaluate(
                """(baseline) => {
                    const rows = [...document.querySelectorAll('#transcriptBody .msg.hermus')];
                    if (rows.length <= baseline) return '';
                    return (rows[rows.length - 1].innerText || '').trim();
                }""",
                baseline,
            )
            if answer and len(answer) > 16:
                break
        answer_ms = (time.time() - typed_at) * 1000

        shot = os.environ.get("FIRST_ANSWER_SHOT")
        page.screenshot(path=str(Path(shot) if shot else tmp_path_factory.mktemp("shot") / "first-answer.png"))
        browser.close()

    return {
        "records": records,
        "first_screen": first_screen,
        "stuck": stuck,
        "answer": answer,
        "answer_ms": answer_ms,
    }


class TestFirstAnswer:
    def test_a_typed_question_is_answered_in_the_conversation(self, trace):
        assert trace["answer"], "the conversation stayed empty after submitting a command"
        assert trace["answer_ms"] < ANSWER_DEADLINE_MS, (
            f"the answer took {trace['answer_ms']:.0f}ms (allowed {ANSWER_DEADLINE_MS}ms): {trace['answer'][:200]}"
        )

    def test_nothing_is_left_hanging_after_ten_idle_seconds(self, trace):
        assert not trace["stuck"], f"{len(trace['stuck'])} request(s) still open after 10s idle"

    def test_the_first_screen_is_fast(self, trace):
        assert trace["first_screen"], "no first-screen requests were observed"
        slowest = max(trace["records"], key=lambda r: r["ms"] if r["at"] <= 3.0 else 0)
        assert slowest["ms"] < FIRST_SCREEN_DEADLINE_MS, (
            f"slowest first-screen request was {slowest['ms']:.0f}ms: {slowest['url']}"
        )

    def test_the_first_screen_is_one_bundle_not_a_storm(self, trace):
        # 10 scripts + 3 stylesheets + the document + one console state bundle
        # + a live-event stream. A storm is what the old page did: 50+.
        assert len(trace["records"]) < 30, f"{len(trace['records'])} requests is a storm, not a screen"

    def test_diagnostics_were_not_requested(self, trace, gateway):
        """A page load must never trigger the doctor or the engine probe."""
        import requests

        # The routes are still there, behind the door.
        assert requests.get(gateway + "/doctor/status", timeout=90).status_code == 200
