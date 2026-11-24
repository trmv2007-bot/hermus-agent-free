"""The thinking layer, where a person can see it: the plan, the receipt, the reason.

Four things this file pins, at 1440 and 420:

* a multi-step turn shows the plan it is following, a step at a time, beside the
  conversation — and after a reload the same plan comes back from the server;
* an answer that changed something carries its receipt: a file and its hash, a
  command and its exit status, a page and what came back;
* a turn that could not prove itself says ``Not verified`` in that same place,
  in the same weight, and never twice;
* a turn with no plan, no receipt and nothing to cite adds no markup at all, and
  no surface here ever shows a probe state or a count of machinery.

The multi-step turn is real: the local model stub calls ``file_write`` and
``file_read``, the agent loop runs them, and the receipt is built from the file
that is actually on disk. The receipt shapes are built by the engine's own
``build_receipt``, so a change in what the engine emits breaks this file rather
than silently greying out the surface.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from _real_home_guard import temp_home

try:  # pytest imports this file as `browser.test_room_thinking`
    from browser import _room_model
except ImportError:  # pragma: no cover - direct module import
    import _room_model  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.browser

BOOT_TIMEOUT_S = 180

#: Two clauses and a file path: a plan with a step per clause, and an edit.
MULTI_STEP = "write notes.md with three points about the moon and then check it"

#: What the scripted model says once the tools have run.
TOOL_ANSWER = "Notes written and read back — the three points about the moon are in notes.md."

#: Words that would turn the thinking layer into a status board.
PROBE_WORDS = ("pending", "not probed", "needs_install", "endpoints verified")
PANEL_COUNTER = re.compile(r"\b\d+\s+(?:internal\s+)?(?:panels?|endpoints?|actions?)\b", re.IGNORECASE)

NOTES = "- the moon is 384,400 km away\n- it is tidally locked to the earth\n- it has no real atmosphere\n"


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
    server = _room_model.serve()
    try:
        yield _room_model
    finally:
        _room_model.SLOW_MS = 0
        _room_model.SCRIPT = {}
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

    scratch = tmp_path_factory.mktemp("hermus-thinking")
    home = temp_home(tmp_path_factory, scratch / "home")
    data = temp_home(tmp_path_factory, scratch / "data")
    home.mkdir(parents=True, exist_ok=True)
    (home / "onboarding.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Hermus",
                # ``act`` so a scripted tool call runs instead of waiting on an
                # approval card: this file is about the thinking surface, and
                # the approval surface has its own tests.
                "permissions": "act",
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
            "HERMUS_DATA_DIR": str(data),
            "HERMUS_MODEL": f"ollama/{_room_model.MODEL_NAME}",
            "HERMUS_OLLAMA_BASE_URL": _room_model.BASE,
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
        yield {"base": base, "home": home, "data": data}
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
    """Screenshots land beside the test run, or in ROOM_THINKING_SHOTS for the deliverable."""
    target = Path(os.environ.get("ROOM_THINKING_SHOTS") or tmp_path_factory.mktemp("thinking-shots"))
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture(autouse=True)
def quiet_model():
    """No test inherits the previous one's script, and no gate stays shut."""
    _room_model.SCRIPT = {}
    _room_model.SLOW_MS = 0
    _room_model.GATE = None
    yield
    _room_model.GATE = None
    _room_model.SCRIPT = {}
    _room_model.SLOW_MS = 0


class Room:
    """One page in the room, with the measurements these assertions need."""

    def __init__(self, browser, base: str, width: int):
        self.context = browser.new_context(viewport={"width": width, "height": 900})
        self.page = self.context.new_page()
        self.width = width
        self.page.goto(base + "/", wait_until="load", timeout=60000)
        self.page.wait_for_timeout(1200)

    def close(self):
        self.context.close()

    def ask(self, text: str) -> None:
        self.page.fill("#say", text)
        self.page.press("#say", "Enter")

    def wait_for_answer(self, needle: str, timeout_ms: int = 40000) -> str:
        deadline = time.time() + timeout_ms / 1000.0
        text = ""
        while time.time() < deadline:
            text = self.page.evaluate(
                "() => { const r = [...document.querySelectorAll('#thread .reply')]; return r.length ? r[r.length-1].innerText : ''; }"
            )
            if needle in text:
                return text
            self.page.wait_for_timeout(120)
        return text

    def plan_states(self) -> list[str]:
        return self.page.evaluate("() => [...document.querySelectorAll('#planSteps .plan-step')].map((el) => el.dataset.state)")

    def receipts(self) -> list[dict]:
        return self.page.evaluate(
            """() => [...document.querySelectorAll('#thread .receipt')].map((el) => ({
                text: el.innerText.trim(),
                ok: el.dataset.ok,
            }))"""
        )

    def surface(self) -> dict:
        """What is on the page, and which of it is machinery rather than product."""
        return self.page.evaluate(
            """() => ({
                text: document.body.innerText || '',
                planVisible: !!document.querySelector('#plan') && !document.querySelector('#plan').hidden,
                steps: document.querySelectorAll('#planSteps .plan-step').length,
                receipts: document.querySelectorAll('#thread .receipt').length,
                exchanges: document.querySelectorAll('#thread .exchange').length,
                receiptsInReplies: [...document.querySelectorAll('#thread .reply')]
                    .filter((el) => /Verified:|Not verified:/.test(el.innerText)).length,
                under: (document.querySelector('#under') || {}).innerText || '',
                underTitle: (document.querySelector('#under') || {}).title || '',
                columns: (() => {
                    const a = document.querySelector('#thread');
                    const b = document.querySelector('#plan');
                    if (!a || !b || b.hidden) return null;
                    const ra = a.getBoundingClientRect();
                    const rb = b.getBoundingClientRect();
                    return {
                        sameRow: Math.abs(ra.top - rb.top) < 40,
                        beside: rb.left >= ra.right && ra.right - ra.left > 0,
                        above: rb.bottom <= ra.top + 1,
                        scrollW: document.documentElement.scrollWidth,
                        innerW: window.innerWidth,
                    };
                })(),
            })"""
        )


def _assert_no_status_board(text: str, where: str) -> None:
    low = text.lower()
    for word in PROBE_WORDS:
        assert word not in low, f"{where} shows {word!r}: {text[:400]!r}"
    counter = PANEL_COUNTER.search(text)
    assert counter is None, f"{where} counts machinery: {counter.group(0)!r}"


def _sse(*events: tuple[str, dict]) -> str:
    """A run stream as the gateway frames it: one named event per block."""
    return "".join(f"event: {name}\ndata: {json.dumps({'type': name, 'data': data})}\n\n" for name, data in events)


def _receipts(tmp_path: Path) -> dict[str, dict]:
    """Receipts built by the engine's own composer, one per evidence kind."""
    from core.thinking.receipt import build_receipt

    notes = tmp_path / "notes.md"
    notes.write_text(NOTES, encoding="utf-8")

    filed = build_receipt(
        "write notes.md",
        [{"tool": "file_write", "args": {"path": str(notes)}, "result": {"success": True, "path": str(notes)}}],
        "I wrote notes.md with three points.",
        base_dir=tmp_path,
        expects_change=True,
    )
    ran = build_receipt(
        "run the tests",
        [
            {
                "tool": "shell_execute",
                "args": {"command": "pytest -q"},
                "result": {"success": True, "command": "pytest -q", "returncode": 0, "stdout": "6 passed"},
            }
        ],
        "The suite passes.",
        base_dir=tmp_path,
        expects_change=True,
    )
    fetched = build_receipt(
        "what does the page say",
        [
            {
                "tool": "web_fetch",
                "args": {"url": "https://example.com/moon"},
                "result": {"url": "https://example.com/moon", "content": "m" * 4120},
            }
        ],
        "The page puts the moon 384,400 km away.",
        base_dir=tmp_path,
        expects_change=False,
    )
    unproven = build_receipt("write notes.md", [], "I wrote notes.md with three points.", base_dir=tmp_path, expects_change=True)
    assert filed.verified and ran.verified and fetched.verified, "the engine refused a receipt these tests need"
    assert not unproven.verified
    return {
        "file": filed.to_dict(),
        "command": ran.to_dict(),
        "web": fetched.to_dict(),
        "unverified": unproven.to_dict(),
    }


class TestThePlan:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_a_multi_step_turn_shows_its_plan_and_keeps_it_across_a_reload(self, browser, gateway, width, shots, tmp_path):
        notes = gateway["home"] / f"notes-{width}.md"
        _room_model.SCRIPT = {
            "tools": [
                {"name": "file_write", "arguments": {"path": str(notes), "content": NOTES}},
                {"name": "file_read", "arguments": {"path": str(notes)}},
            ],
            "answer": TOOL_ANSWER,
        }
        # The answer is held open until this test has looked at the turn mid-flight.
        _room_model.GATE = threading.Event()
        room = Room(browser, gateway["base"], width)
        try:
            room.ask(MULTI_STEP)
            room.page.wait_for_function("() => document.querySelectorAll('#planSteps .plan-step').length >= 2", timeout=60000)
            room.page.wait_for_function(
                "() => document.querySelectorAll('#planSteps .plan-step[data-state=\"verified\"]').length >= 2",
                timeout=60000,
            )

            midflight = room.plan_states()
            assert "verified" in midflight, f"no step reached verified mid-flight: {midflight}"
            assert "running" in midflight, f"nothing is running mid-flight: {midflight}"
            surface = room.surface()
            assert surface["planVisible"], "the plan is not beside the conversation"
            assert surface["exchanges"] == 1
            _assert_no_status_board(surface["text"], f"a running plan at {width}px")
            if width >= 1120:
                # Beside the conversation, not stacked on it.
                assert surface["columns"] and surface["columns"]["beside"], "the plan is not beside the conversation"
            else:
                assert surface["columns"] and surface["columns"]["above"], "the plan did not move above the conversation"
            assert surface["columns"]["scrollW"] <= surface["columns"]["innerW"] + 1
            room.page.screenshot(path=str(shots / f"plan-midflight-{width}.png"))

            _room_model.GATE.set()
            answer = room.wait_for_answer("Notes written", timeout_ms=60000)
            assert TOOL_ANSWER in answer, f"the answer never landed: {answer[:200]!r}"
            # The answer's last token can land a beat before the turn closes.
            room.page.wait_for_function("() => document.body.dataset.busy === 'false'", timeout=60000)
            settled = room.plan_states()
            assert settled[:2] == ["verified", "verified"], f"the work steps did not verify: {settled}"
            # A step no tool result backed is reported as not reached, never as done.
            assert settled[-1] in ("skipped", "planned"), settled

            digest = hashlib.sha256(notes.read_bytes()).hexdigest()[:8]
            receipts = room.receipts()
            assert len(receipts) == 1, f"{len(receipts)} receipt lines for one change"
            assert notes.name in receipts[0]["text"], receipts
            assert digest in receipts[0]["text"], f"the receipt does not cite the hash: {receipts}"
            assert receipts[0]["ok"] == "true"

            finished = room.surface()
            assert finished["receiptsInReplies"] == 0, "the evidence sentence is still inside the answer"
            assert "ollama/" in finished["under"] and "·" in finished["under"], (
                f"the routing reason is not beside the model badge: {finished['under']!r}"
            )
            assert finished["under"].split(" · ")[0].strip() == f"ollama/{_room_model.MODEL_NAME}"
            _assert_no_status_board(finished["text"], f"a verified turn at {width}px")
            room.page.screenshot(path=str(shots / f"plan-verified-{width}.png"))

            # A reload throws the page away: everything below is read from the server.
            room.page.reload(wait_until="load", timeout=60000)
            room.page.wait_for_function("() => document.querySelectorAll('#planSteps .plan-step').length >= 2", timeout=60000)
            restored = room.surface()
            assert restored["planVisible"], "the plan did not survive the reload"
            assert room.plan_states()[:2] == ["verified", "verified"], room.plan_states()
            assert restored["receipts"] == 1, "the receipt did not survive the reload"
            assert notes.name in room.receipts()[0]["text"]
            room.page.screenshot(path=str(shots / f"plan-reloaded-{width}.png"))
        finally:
            room.close()


class TestTheReceipt:
    @pytest.mark.parametrize(
        "kind,expected",
        [
            ("file", "notes.md · "),
            ("command", "pytest -q · exit 0"),
            ("web", "example.com/moon · 4120 characters came back"),
            ("unverified", "Not verified — "),
        ],
    )
    @pytest.mark.parametrize("width", [1440, 420])
    def test_every_kind_of_evidence_reads_as_one_line(self, browser, gateway, kind, expected, width, shots, tmp_path):
        receipt = _receipts(tmp_path)[kind]
        # The runtime appends this sentence to the answer text for every other
        # channel; the room takes it off and draws it as the receipt instead.
        note = "\n\nNot verified: the request asked for a change" if kind == "unverified" else "\n\nVerified: see above"
        body = _sse(
            ("agent_response", {"text": "I wrote notes.md with three points." + note}),
            ("receipt", receipt),
        )
        room = Room(browser, gateway["base"], width)
        try:
            room.page.route("**/stream/command", lambda route: route.fulfill(status=200, body=body))
            room.ask(MULTI_STEP)
            room.page.wait_for_selector("#thread .receipt", timeout=30000)
            receipt_lines = room.receipts()
            assert len(receipt_lines) == 1, f"{kind}: {receipt_lines}"
            assert expected in receipt_lines[0]["text"], receipt_lines
            assert receipt_lines[0]["ok"] == ("false" if kind == "unverified" else "true")

            surface = room.surface()
            # The engine's own sentence is not left inside the answer as well.
            assert surface["receiptsInReplies"] == 0
            assert surface["steps"] == 0 and not surface["planVisible"], "a receipt drew a plan"
            _assert_no_status_board(surface["text"], f"a {kind} receipt")
            room.page.screenshot(path=str(shots / f"receipt-{kind}-{width}.png"))
        finally:
            room.close()


class TestNothingToShow:
    @pytest.mark.parametrize("width", [1440, 420])
    def test_a_single_step_turn_adds_no_plan_and_no_receipt(self, browser, gateway, width, shots):
        room = Room(browser, gateway["base"], width)
        try:
            room.ask("what can you do?")
            answer = room.wait_for_answer(_room_model.ANSWER_TEXT, timeout_ms=40000)
            assert _room_model.ANSWER_TEXT in answer
            surface = room.surface()
            assert surface["exchanges"] == 1
            assert surface["steps"] == 0, "a turn with no plan drew steps"
            assert not surface["planVisible"], "an empty plan is on the page"
            assert surface["receipts"] == 0, "a turn that changed nothing claims a receipt"
            assert surface["receiptsInReplies"] == 0
            assert room.page.evaluate("() => document.querySelector('#planSteps').children.length") == 0
            _assert_no_status_board(surface["text"], f"a single-step turn at {width}px")
            room.page.screenshot(path=str(shots / f"no-thinking-{width}.png"))
        finally:
            room.close()
