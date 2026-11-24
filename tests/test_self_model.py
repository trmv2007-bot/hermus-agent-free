"""The self-model has to be true, and it has to fit.

An agent's description of itself is a prompt, and a prompt that overstates what
it can do is how a turn ends in an apology for a capability it actually had.
These tests pin the three properties the section exists to guarantee:

* it is capped and deduplicated — it rides on every chat turn;
* a capability the probe says is broken never appears as usable, and the fix is
  named instead;
* it carries the brand, the real tool names, the red lines and the
  try-everything rule, because those are what make it a self-model rather than
  a tool list.
"""

from __future__ import annotations

import json
import pathlib

from core.self_model import (
    FAMILIES,
    MAX_ITEMS,
    MAX_SECTION_CHARS,
    SelfModel,
    attention_view,
    identity_sentence,
    red_line_titles,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Every tool the fixture registry holds, once.
ALL_TOOLS = [name for family in FAMILIES for name in family.tools]


def _probe(**failures: dict) -> dict:
    """A probe report where the named checks fail and every other check passes."""
    checks = {
        name: {"name": name, "ok": name not in failures, "detail": "import OK", "hint": "", "level": "recommended"}
        for name in ("playwright", "desktop_control", "faster_whisper")
    }
    for name, detail in failures.items():
        checks[name] = detail
    return {"checks": checks, "at": "2026-11-24T00:00:00+00:00", "error": ""}


def _model(**failures: dict) -> SelfModel:
    return SelfModel(probe=_probe(**failures), tools=list(ALL_TOOLS))


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------
def test_section_is_capped_and_carries_the_essentials():
    text = _model().section()
    assert len(text) <= MAX_SECTION_CHARS, f"section is {len(text)} chars, cap is {MAX_SECTION_CHARS}"
    assert "Hermus" in text
    assert "file_read" in text and "shell_execute" in text, "the real tool names are the point"
    assert "Never break" in text
    assert "check your tool set" in text


def test_section_is_deduplicated():
    text = _model().section()
    for name in ALL_TOOLS:
        assert text.count(name) <= 1, f"{name} appears twice"


def test_a_tiny_limit_still_returns_something_capped():
    text = SelfModel(probe=_probe(), tools=list(ALL_TOOLS), limit=200).section()
    assert len(text) <= 200


def test_identity_comes_from_the_onboarding_record(tmp_path, monkeypatch):
    state = tmp_path / "onboarding.json"
    state.write_text(
        json.dumps({"version": 1, "name": "Nut", "permissions": "act", "brain": None, "check": {}, "completed_at": None}),
        encoding="utf-8",
    )
    import core.onboarding as onboarding

    monkeypatch.setattr(onboarding, "state_path", lambda: state)
    sentence = identity_sentence()
    assert "Nut" in sentence
    assert "file work in its own folders" in sentence, "the chosen permission profile must be stated"
    assert "watch only" not in sentence.lower()
    assert sentence.endswith(".")


def test_red_lines_are_read_from_the_policy_file():
    titles = red_line_titles()
    policy = json.loads((ROOT / "policies" / "red_lines.json").read_text(encoding="utf-8"))
    assert titles == [rule["title"] for rule in policy["rules"]]
    assert len(titles) == 11


# ---------------------------------------------------------------------------
# Truthfulness
# ---------------------------------------------------------------------------
def test_a_probe_failure_moves_the_capability_out_of_what_it_can_do():
    model = _model(playwright={"name": "playwright", "ok": False, "detail": "not installed", "level": "recommended"})
    capabilities = model.capabilities()
    available = {entry["key"] for entry in capabilities["available"]}
    unavailable = {entry["key"]: entry for entry in capabilities["unavailable"]}
    assert "browser" not in available
    assert "browser" in unavailable
    assert "pip install playwright" in unavailable["browser"]["enable"]
    assert "not installed" in unavailable["browser"]["reason"]

    text = model.section()
    # The browser tools may only be mentioned under the "cannot do" line.
    can_do_line = next(line for line in text.splitlines() if line.startswith("Can do now"))
    assert "browser_navigate" not in can_do_line
    assert "Cannot do right now" in text
    assert "pip install playwright" in text


def test_a_broken_probe_claims_nothing_gated():
    model = SelfModel(
        probe={"checks": {}, "at": "2026-11-24T00:00:00+00:00", "error": "RuntimeError: boom"}, tools=list(ALL_TOOLS)
    )
    capabilities = model.capabilities()
    available = {entry["key"] for entry in capabilities["available"]}
    assert {"browser", "desktop", "voice"}.isdisjoint(available)
    assert "boom" in model.section()


def test_a_tool_missing_from_the_registry_is_not_claimed():
    model = SelfModel(probe=_probe(), tools=[name for name in ALL_TOOLS if not name.startswith("browser_")])
    available = {entry["key"] for entry in model.capabilities()["available"]}
    assert "browser" not in available
    assert "browser_navigate" not in model.section()


# ---------------------------------------------------------------------------
# Diagnosis the person can read
# ---------------------------------------------------------------------------
def test_attention_view_is_one_row_of_bounded_items():
    broken = [{"kind": "broken", "item": f"b{i}", "evidence": "probe", "fix": "do it"} for i in range(MAX_ITEMS)]
    worth = [{"kind": "worth_adding", "item": f"w{i}", "evidence": "probe", "fix": "do it"} for i in range(MAX_ITEMS)]
    view = attention_view(broken, worth)
    assert view["broken_now"] == MAX_ITEMS
    assert view["worth_adding"] == MAX_ITEMS
    assert len(view["items"]) == MAX_ITEMS * 2
    assert {"what", "evidence", "next"} == set(view["items"][0])
    assert view["items"][0]["what"].startswith("broken: ")


def test_diagnosis_items_each_carry_evidence(monkeypatch):
    import core.self_model as self_model

    monkeypatch.setattr(
        self_model,
        "run_probe",
        lambda **_: _probe(playwright={"name": "playwright", "ok": False, "detail": "no chromium", "level": "recommended"}),
    )
    monkeypatch.setattr(self_model, "_ledger_items", lambda limit: [])
    self_model._diagnosis_cache.clear()
    diagnosis = self_model.diagnose(force=True)
    assert len(diagnosis["broken"]) <= MAX_ITEMS
    assert len(diagnosis["worth"]) <= MAX_ITEMS
    for item in diagnosis["broken"] + diagnosis["worth"]:
        assert item["evidence"].strip(), f"{item['item']} has no evidence"
        assert item["fix"].strip(), f"{item['item']} has no next step"
    assert any(item["kind"] == "worth_adding" and "playwright" in item["evidence"] for item in diagnosis["worth"])


def test_console_row_reads_the_same_diagnosis(monkeypatch):
    from core import console

    panel = console.get("self_attention")
    assert panel is not None
    assert panel.source == "core/self_model.py"
    assert (ROOT / panel.source).exists()
    view = console.probe("self_attention")
    assert view["status"] == "ready"
    assert {"broken_now", "worth_adding", "items"} <= set(view["raw"])
    assert view["columns"] == ["what", "evidence", "next"] or not view["rows"]


# ---------------------------------------------------------------------------
# "Try everything it has" as behaviour, driven by a scripted model
# ---------------------------------------------------------------------------
def test_a_scripted_model_uses_a_tool_it_has_and_hears_what_is_missing(monkeypatch):
    """One turn: a tool the registry holds, then one it does not.

    The scripted model asks for a real tool first and for an unregistered one
    second. What this proves is that the prompt it reads names the tool it can
    use, names the missing capability *and* the command that would enable it,
    and that a request for something that does not exist comes back as an
    explicit error rather than a silent nothing.
    """
    import core.self_model as self_model
    from core.agent import HermusAgent

    rendered = SelfModel(
        probe=_probe(faster_whisper={"name": "faster_whisper", "ok": False, "detail": "not installed", "level": "recommended"}),
        tools=["file_read", "file_write", "transcribe_audio"],
    ).section()
    monkeypatch.setattr(self_model, "section", lambda: rendered)

    agent = HermusAgent(model="mock/mock", max_steps=4)
    prompts: list[str] = []
    results: list[tuple[str, dict]] = []
    state = {"n": 0}

    class _Resp:
        def __init__(self, content, tool_calls):
            self.content = content
            self.tool_calls = tool_calls
            self.usage = {}

    def fake_chat(messages, tools=None, **_kw):
        prompts.append(str(messages[0].get("content") or ""))
        state["n"] += 1
        if state["n"] == 1:
            return _Resp("", [{"name": "file_read", "arguments": {"path": "pyproject.toml"}}])
        if state["n"] == 2:
            return _Resp("", [{"name": "ocr_screenshot", "arguments": {"path": "made-up.png"}}])
        return _Resp("done", [])

    real_execute = agent._execute_tool

    def record(name, args):
        out = real_execute(name, args)
        results.append((name, out))
        return out

    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    monkeypatch.setattr(agent, "_execute_tool", record)
    agent.chat("read pyproject.toml, then screenshot the whole screen", stream=False)

    assert prompts, "the agent never called the model"
    prompt = prompts[0]
    assert "Can do now" in prompt and "file_read" in prompt
    assert "Cannot do right now" in prompt and "pip install faster_whisper" in prompt
    assert "check your tool set" in prompt

    assert [name for name, _ in results] == ["file_read", "ocr_screenshot"]
    held, missing = results[0][1], results[1][1]
    assert "error" not in held, f"a registered tool must run: {held}"
    assert "hermus" in json.dumps(held).lower(), "file_read should have returned the real file"
    # The unregistered tool fails loudly and names itself as unknown — never a
    # silent empty answer — while the prompt already held the fix for what it
    # asked for and could not have.
    assert missing.get("error_code") == "TOOL_NOT_FOUND", missing
    assert "not registered" in str(missing.get("error", "")).lower(), missing


def test_the_digest_carries_one_self_diagnosis_line_a_day(monkeypatch):
    """The diagnosis reaches the person through the existing digest, once a day."""
    from datetime import datetime

    import core.self_model as self_model
    from core.attention import AttentionGate
    from core.awareness.settings import AwarenessSettings

    monkeypatch.setattr(
        self_model,
        "attention_summary",
        lambda: {"broken": 2, "worth": 1, "headline": "doctor found two things", "additions": "browser"},
    )
    now = {"at": datetime(2026, 11, 24, 14, 0).timestamp()}
    gate = AttentionGate(
        settings_provider=lambda: AwarenessSettings(quiet_hours=None, typing_seconds=3.0),
        snapshot_provider=lambda: {},
        clock=lambda: now["at"],
    )
    fired = gate.tick(snapshot={})
    patterns = [item["suggestion"]["pattern"] for item in fired]
    assert patterns.count("self.diagnosis") == 1, patterns
    body = next(item["suggestion"]["body"] for item in fired if item["suggestion"]["pattern"] == "self.diagnosis")
    assert "doctor found two things" in body and "Systems surface" in body

    assert gate.tick(snapshot={}) == [], "it must not repeat itself on the same day"
    now["at"] += 86400
    again = gate.tick(snapshot={})
    assert [item["suggestion"]["pattern"] for item in again] == ["self.diagnosis"]


def test_the_attention_route_publishes_the_same_diagnosis():
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    body = TestClient(app).get("/api/v1/self/attention").json()
    assert body["success"] is True
    assert {"broken_now", "worth_adding", "items"} <= set(body)
    assert isinstance(body["items"], list)


def test_capabilities_reads_the_probe_once(monkeypatch):
    """One answer must describe one machine: no second read inside the section."""
    reads = {"probe": 0, "tools": 0}

    class Counting(SelfModel):
        def probe(self):
            reads["probe"] += 1
            return _probe()

        def tools(self):
            reads["tools"] += 1
            return list(ALL_TOOLS)

    model = Counting()
    model.capabilities()
    model.section()
    assert reads["probe"] == 2, f"capabilities and section read the probe {reads['probe']} times"
    assert reads["tools"] == 2, f"the registry was read {reads['tools']} times for two answers"
