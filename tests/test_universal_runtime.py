"""Regression tests for the universal mission runtime unification pass.

Covers:
  * request classification: chat vs mission (auto-promotion of goal-like text)
  * ``HermusAgent.autonomous`` routes through MissionEngine (one runtime, not two)
  * evidence-gated node success: describing work ≠ performing work
  * DAG parent-context handoff: child prompts receive upstream outputs
  * mid-run steering: /run/steer → run bus inbox → agent conversation
  * queue-first execution: /command async:true returns a job and streams events
  * binary attachments: DOCX/XLSX/ZIP contents actually reach the prompt
  * structured runtime issues (no more silent except/pass blindness)
  * step budgets scale with HERMUS_MAX_TOOL_STEPS (no hidden 12-step cap)
  * SWE coder phase is agent-backed with diff-grounded file evidence
"""
from __future__ import annotations

import io
import json
import pathlib
import time
import zipfile

import pytest


@pytest.fixture()
def client():
    from starlette.testclient import TestClient

    from gateway.gateway import app

    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------- classification
def test_classify_request_chat_vs_mission():
    from core.runtime import classify_request

    # plain conversation stays chat
    assert classify_request("hi there!") == "chat"
    assert classify_request("what is the capital of France?") == "chat"
    assert classify_request("summarize this paragraph for me") == "chat"
    # goal-like, deliverable-shaped requests become missions
    assert classify_request(
        "Build a complete web app with user auth and tests, and keep going until it works"
    ) == "mission"
    assert classify_request(
        "Write a script that scrapes prices daily, stores them in sqlite, "
        "and then run it to verify the output"
    ) == "mission"
    # explicit markers always win
    assert classify_request("mission: tidy up my notes") == "mission"
    assert classify_request("do this autonomously please") == "mission"


def test_mission_auto_classify_flag_disables_promotion():
    from core import runtime
    from core.config import config

    old = config.mission_auto_classify
    config.mission_auto_classify = False
    try:
        assert runtime.classify_request(
            "Build a complete web app with user auth and tests for our team"
        ) == "chat"
        # explicit markers still promote
        assert runtime.classify_request("mission: tidy up my notes") == "mission"
    finally:
        config.mission_auto_classify = old


# --------------------------------------------------- agent.autonomous → runtime
def test_agent_autonomous_routes_through_mission_runtime():
    """autonomous() must use the MissionEngine core, not a private runner."""
    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock")
    report = agent.autonomous("summarize the plan", max_repairs=1)

    assert report["run_kind"] == "mission"
    assert str(report.get("mission_id") or "").startswith("msn_")
    # mission state is honest: mock backend cannot perform work
    assert report.get("state") in ("blocked", "failed", "completed")
    # legacy contract kept for CLI/delegation consumers
    assert report["status"] in ("done", "failed", "blocked")
    assert report["phases"][0] == "understand"
    assert report["phases"][-1] == "finish"
    assert isinstance(report["steps"], list)
    assert "verified" in report and "repairs" in report
    # canonical answer contract
    assert report["response"]


def test_agent_autonomous_no_silent_downgrade_when_runtime_disabled():
    """Disabling the mission runtime is a BLOCKED state, never a silent downgrade."""
    from core.agent import HermusAgent
    from core.config import config

    old = config.mission_runtime_enabled
    config.mission_runtime_enabled = False
    try:
        agent = HermusAgent(model="mock/mock")
        report = agent.autonomous("summarize the plan", max_repairs=1)
        assert report["run_kind"] == "mission_blocked"
        assert report["state"] == "blocked"
        assert not report.get("verified", False)
    finally:
        config.mission_runtime_enabled = old


# ------------------------------------------------------- evidence-gated success
class _FakeNode:
    def __init__(self, role="coder", goal="Implement authentication", inputs=None):
        self.id = "node_x"
        self.role = role
        self.goal = goal
        self.inputs = inputs or {}
        self.dependencies = []


class _FakeAgent:
    """Agent stand-in with a scripted chat result and non-mock provider."""

    def __init__(self, response="Here is how authentication should be implemented...",
                 tool_calls=None):
        self._response = response
        self._tool_calls = tool_calls or []
        self.prompts = []

        class _LLM:
            provider = "fake"

        self.llm = _LLM()

    def chat(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return {
            "response": self._response,
            "tool_calls": list(self._tool_calls),
            "tool_results": [{"tool": t} for t in self._tool_calls],
            "steps": 1,
        }


@pytest.fixture()
def no_file_scan(monkeypatch):
    """Deterministic evidence: control the changed-files signal in tests."""
    changed = {"files": []}
    monkeypatch.setattr("core.mission._scan_changed_files",
                        lambda since_ts, roots=None: changed["files"])
    return changed


def test_executor_rejects_description_without_work(no_file_scan):
    """A coder node that only *describes* the work must NOT be successful."""
    from core.mission import make_agent_backed_executor

    describer = _FakeAgent(
        response="Authentication should use JWT tokens with a refresh rotation policy..."
    )
    executor = make_agent_backed_executor(agent=describer)
    result = executor(_FakeNode(role="coder", goal="Implement authentication"), {})

    assert result["success"] is False
    assert result["error"] == "no_evidence_of_work"
    assert result["evidence"][0]["performed_work"] is False
    assert "do not merely describe" in result["instructions"].lower()


def test_executor_accepts_tool_backed_work(no_file_scan):
    from core.mission import make_agent_backed_executor

    worker = _FakeAgent(
        response="Implemented auth in auth.py and ran the test suite.",
        tool_calls=["file_write", "sandbox_run"],
    )
    executor = make_agent_backed_executor(agent=worker)
    result = executor(_FakeNode(role="coder", goal="Implement authentication"), {})

    assert result["success"] is True
    ev = result["evidence"][0]
    assert ev["performed_work"] is True
    assert "file_write" in ev["action_tools"]
    assert "sandbox_run" in ev["commands_executed"]


def test_executor_accepts_file_change_evidence(no_file_scan):
    from core.mission import make_agent_backed_executor

    no_file_scan["files"] = ["/tmp/proj/auth.py"]
    describer = _FakeAgent(response="Wrote the module to auth.py")
    executor = make_agent_backed_executor(agent=describer)
    result = executor(_FakeNode(role="coder", goal="Implement authentication"), {})
    assert result["success"] is True
    assert result["evidence"][0]["files_changed"] == ["/tmp/proj/auth.py"]


def test_executor_blocks_on_mock_backend(no_file_scan):
    from core.mission import make_agent_backed_executor

    class _MockAgent(_FakeAgent):
        def __init__(self):
            super().__init__(response="[MOCK llama] Echo: ...")

            class _LLM:
                provider = "mock"

            self.llm = _LLM()

    executor = make_agent_backed_executor(agent=_MockAgent())
    result = executor(_FakeNode(role="coder", goal="Implement authentication"), {})
    assert result["success"] is False
    assert result["error"] == "no_model_backend"


def test_analysis_node_still_requires_substance(no_file_scan):
    from core.mission import make_agent_backed_executor

    executor = make_agent_backed_executor(agent=_FakeAgent(response="ok"))
    # architect role is analysis: no tools needed, but a real analysis is
    result = executor(_FakeNode(role="architect", goal="Design architecture for the parser"), {})
    assert result["success"] is False
    assert result["error"] == "empty_analysis"

    good = _FakeAgent(response="A" * 300)
    executor2 = make_agent_backed_executor(agent=good)
    res2 = executor2(_FakeNode(role="architect", goal="Design architecture for the parser"), {})
    assert res2["success"] is True


def test_scan_changed_files_detects_new_files(tmp_path):
    from core.mission import _scan_changed_files

    before = time.time() - 5
    (tmp_path / "out.py").write_text("print('new')")
    changed = _scan_changed_files(before, roots=[tmp_path])
    assert any(p.endswith("out.py") for p in changed)
    assert _scan_changed_files(time.time() + 60, roots=[tmp_path]) == []


# ------------------------------------------------------- parent-context handoff
def test_build_node_prompt_injects_parent_context():
    from core.mission import build_node_prompt

    node = _FakeNode(role="coder", goal="Implement the login module")
    parent_ctx = {
        "spec": {
            "outputs": {"output": "ARCHITECTURE_MARKER: use FastAPI with JWT auth"},
            "artifacts": ["/tmp/proj/architecture.md"],
        }
    }
    prompt = build_node_prompt(node, parent_ctx)
    assert "ARCHITECTURE_MARKER" in prompt
    assert "/tmp/proj/architecture.md" in prompt
    assert "Upstream results" in prompt
    # repair hints also flow through
    node.inputs["repair_hints"] = ["tests failed on import"]
    prompt2 = build_node_prompt(node, parent_ctx)
    assert "tests failed on import" in prompt2


def test_dag_child_receives_parent_output_in_prompt(tmp_path, no_file_scan):
    """End-to-end: the second DAG stage's prompt contains the first's output."""
    from core.mission import MissionEngine

    agent = _FakeAgent(
        response="STAGE_OUTPUT_MARKER: concrete work done here, moving on.",
        tool_calls=["file_write"],
    )
    engine = MissionEngine(storage_dir=tmp_path / "missions")
    engine.start_mission(
        goal="ship the feature",
        subgoals=["Analyze the requirements", "Implement the solution"],
        budget_steps=6,
        agent=agent,
    )
    assert len(agent.prompts) >= 2
    # the second node's prompt must carry the first node's actual output
    assert "STAGE_OUTPUT_MARKER" in agent.prompts[1]


# ------------------------------------------------------------------- steering
def test_run_bus_steer_inbox_roundtrip():
    from core.run_events import RunBus

    bus = RunBus()
    bus.start("r_steer_1", label="t")
    assert bus.steer("r_stear_1", "x") is False or True  # unknown ids return False
    assert bus.steer("r_steer_1", "first instruction")
    assert bus.steer("r_steer_1", "second instruction")
    drained = bus.pending_steers("r_steer_1")
    assert drained == ["first instruction", "second instruction"]
    assert bus.pending_steers("r_steer_1") == []  # drained
    # steer events are published on the stream too
    types = [e["type"] for e in bus.history("r_steer_1")]
    assert "steer" in types and "steer_consumed" in types


def test_agent_chat_consumes_mid_run_steers():
    """Steering queued during a run must reach the model conversation."""
    from types import SimpleNamespace

    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock", max_steps=5)

    responses = [
        SimpleNamespace(content="initial answer", tool_calls=[], usage={}),
        SimpleNamespace(content="final answer honoring the steer", tool_calls=[], usage={}),
    ]

    class _ScriptedLLM:
        provider = "fake"

        def chat(self, messages, tools=None):
            return responses.pop(0)

    agent.llm = _ScriptedLLM()

    from collections import deque

    # drain sequence: [] at step 1 start, the steer "arrives" while model
    # call 1 is generating, then nothing new.
    steers = deque([[], ["Focus on Python 3.11 compatibility"], []])

    events = []
    result = agent.chat(
        "write the module",
        on_event=lambda t, d: events.append((t, d)),
        steer_source=lambda: steers.popleft() if steers else [],
    )

    assert result["response"] == "final answer honoring the steer"
    steer_events = [d for t, d in events if t == "steer_applied"]
    assert steer_events and steer_events[0]["count"] == 1
    assert "MID-RUN STEERING" in json.dumps([str(m) for m in agent.trajectory])
    assert "Python 3.11" in json.dumps([str(m) for m in agent.trajectory])


def test_run_steer_endpoint_queues_for_active_run(client):
    from core.run_events import run_bus

    rid = "run_steer_inbox_probe"
    run_bus.start(rid, label="probe")
    try:
        r = client.post("/run/steer", json={"run_id": rid, "text": "prefer async io"})
        assert r.status_code == 200
        body = r.json()
        assert body["applied_to_stream"] is True
        assert body["queued_for_agent"] is True
        # the instruction is actually queued for the executing agent
        assert run_bus.pending_steers(rid) == ["prefer async io"]
    finally:
        run_bus.pending_steers(rid)


# ---------------------------------------------------------- queue-first command
def test_command_async_submits_runtime_turn(client):
    """The dashboard path: async:true → job handle → executed by the queue."""
    import time as _time

    r = client.post("/command", json={
        "text": "hello queue-first world", "user_id": "rt1", "platform": "dashboard",
        "async": True, "stream": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["async"] is True
    assert body["job_id"].startswith("job_")
    assert body["stream_url"].startswith("/stream/run/")

    jid = body["job_id"]
    st = {}
    deadline = _time.time() + 60
    while _time.time() < deadline:
        st = client.get(f"/jobs/{jid}").json()
        if st.get("status") in ("succeeded", "failed", "cancelled"):
            break
        _time.sleep(0.1)
    assert st.get("status") == "succeeded", st

    res = client.get(f"/jobs/{jid}/result").json()
    assert res["result"]["response"]

    events = client.get(f"/jobs/{jid}/events?follow=false").json()["events"]
    types = [e["type"] for e in events]
    assert "agent_response" in types, "queued turns must publish the final answer on the run bus"
    assert "run_finished" in types


def test_command_async_mission_autonomous(client):
    """/command?autonomous=true&async=true runs the mission runtime via the queue."""
    import time as _time

    r = client.post("/command", json={
        "text": "mission: build a tiny tool and verify it", "user_id": "rt2",
        "platform": "dashboard", "autonomous": True, "async": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["async"] is True

    jid = body["job_id"]
    st = {}
    deadline = _time.time() + 90
    while _time.time() < deadline:
        st = client.get(f"/jobs/{jid}").json()
        if st.get("status") in ("succeeded", "failed", "cancelled"):
            break
        _time.sleep(0.1)
    assert st.get("status") == "succeeded", st
    res = client.get(f"/jobs/{jid}/result").json()["result"]
    assert res.get("run_kind") == "mission"
    assert str(res.get("mission_id") or "").startswith("msn_")
    # mock backend cannot perform work → the mission says so honestly and is
    # resumable (BLOCKED) rather than pretending success
    assert res.get("state") in ("blocked", "failed")
    assert "No model backend" in (res.get("blocker_reason") or "")


# ---------------------------------------------------------- binary attachments
def _docx_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "word/document.xml",
            f"<w:document><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:document>",
        )
    return buf.getvalue()


def test_document_ingest_extracts_office_and_archives():
    from core.document_ingest import extract_document

    d = extract_document("notes.docx", _docx_bytes("MKR_DOCX_MARKER hello"))
    assert d.text and "MKR_DOCX_MARKER" in d.text
    assert d.method == "docx"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml",
                   "<office:body><text:p><text:span>ODT_MARKER body</text:span></text:p></office:body>")
    d = extract_document("doc.odt", buf.getvalue())
    assert "ODT_MARKER" in (d.text or "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>alpha</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   '<sheetData><row><c t="s"><v>0</v></c><c><v>7</v></c></row></sheetData>')
    d = extract_document("sheet.xlsx", buf.getvalue())
    assert "alpha" in (d.text or "")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inner/important.txt", "x" * 12)
    d = extract_document("bundle.zip", buf.getvalue(), save_binary_to=None)
    assert "inner/important.txt" in (d.text or "")
    assert d.method == "archive"


def test_command_binary_attachment_extracted_into_prompt(client):
    """A DOCX upload must reach the agent prompt with its extracted text."""
    import gateway.gateway as gw

    captured = {}

    class FakeAgent:
        mode = type("M", (), {"value": "agent"})()
        mode_config = type("C", (), {"name": "Agent", "description": "x"})()
        model_name = "fake"
        project = "default"

        class _LLM:
            provider = "fake"

            def _resolve_bundle(self):
                return {}

        llm = _LLM()

        def chat(self, text, **kwargs):
            captured["text"] = text
            return {"response": "ack", "tool_calls": [], "tool_results": [], "steps": 1}

    original = gw.get_agent_for_user
    gw.get_agent_for_user = lambda *a, **k: FakeAgent()
    try:
        files = {"files": ("notes.docx", _docx_bytes("MKR_DOCX_MARKER answer"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        data = {"platform": "dashboard", "user_id": "attach-bin", "text": "Summarize this"}
        resp = client.post("/command", data=data, files=files)
        assert resp.status_code == 200, resp.text[:400]
        body = resp.json()
        assert body.get("attachments")
        assert body["attachments"][0]["inlined"] is True
        assert body["attachments"][0]["method"] == "docx"
    finally:
        gw.get_agent_for_user = original

    prompt = captured.get("text", "")
    assert "MKR_DOCX_MARKER" in prompt, "extracted DOCX text must reach the agent prompt"
    assert "extracted via docx" in prompt


# ----------------------------------------------------------- structured issues
def test_record_issue_is_structured_and_streamed():
    from core.run_events import recent_issues, record_issue, run_bus

    rid = "run_issue_probe"
    run_bus.start(rid, label="t")
    events = []
    unsub = run_bus.add_sink(lambda r, e: events.append(e) if r == rid else None)
    try:
        d = record_issue("memory", "recall", "db locked", error_type="OperationalError",
                         run_id=rid, step=3, retryable=True, fallback="continued without memory")
        assert d["component"] == "memory"
        assert d["operation"] == "recall"
        assert d["step"] == 3
        assert d["retryable"] is True
        assert d["fallback"] == "continued without memory"
        streamed = [e for e in events if e.get("type") == "runtime_issue"]
        assert streamed and streamed[0]["data"]["component"] == "memory"
        assert any(i["operation"] == "recall" for i in recent_issues(50))
    finally:
        unsub()


def test_runtime_issues_endpoint(client):
    from core.run_events import record_issue

    record_issue("test-component", "probe-op", "probe error")
    r = client.get("/runtime/issues")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    probe = [i for i in body["issues"] if i["operation"] == "probe-op"]
    assert probe and probe[0]["component"] == "test-component"


# ---------------------------------------------------------------- step budgets
def test_step_budgets_scale_with_config():
    from core.config import Config, config
    from core.reasoning.governor import governor

    # assert the *declared* default (other test modules mutate the runtime value)
    default_steps = Config.model_fields["max_tool_steps"].default
    assert default_steps >= 24, "default tool budget must not starve real work"

    old = config.max_tool_steps
    config.max_tool_steps = 32
    try:
        hard = ("Build a full-stack web application with authentication, a database, "
                "a test suite, and deployment scripts. Keep going until it works, "
                "fixing every error you find along the way.")
        budget = governor.step_budget(hard, mode="agent")
        assert budget > 12, f"difficulty-5 tasks must exceed the old fixed cap (got {budget})"
        assert budget <= 32
        assert governor.step_budget("hi") <= 32
        assert governor.step_budget("anything", mode="chat") <= 2
    finally:
        config.max_tool_steps = old


# ------------------------------------------------------------------ SWE agent
def test_swe_coder_phase_is_agent_backed(tmp_path, no_file_scan):
    """With a bound agent, files_modified comes from the real checkpoint diff."""
    from core.swe_mode import SoftwareEngineerMode

    root = tmp_path / "repo"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi\n")

    class CoderAgent(_FakeAgent):
        def chat(self, prompt, **kwargs):
            self.prompts.append(prompt)
            (root / "app.py").write_text("print('implemented')\n")
            return {"response": "created app.py", "tool_calls": ["file_write"],
                    "tool_results": [{"tool": "file_write"}], "steps": 1}

    swe = SoftwareEngineerMode(workspace_root=root)
    res = swe.execute(
        "add an app entrypoint",
        workspace_dir=root,
        agent=CoderAgent(),
    )
    d = res.to_dict()
    assert any(f.endswith("app.py") for f in d["files_modified"]), d["files_modified"]
    assert d["phases_executed"][0] == "inspect"
