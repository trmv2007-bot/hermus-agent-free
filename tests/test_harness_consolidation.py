"""Every model turn and every agent action goes through the harness.

Consolidation contract, enforced three ways:

1. **Static gate** — no file builds a prompt and hands it to a model unless the
   same function governs it (``governed_turn`` / ``governed_call`` /
   ``prepare_turn`` / ``_govern``), or it is the transport that governance calls,
   or it calls ``HermusAgent.chat`` (which governs its own turn). A new
   ungoverned call site fails this test.
2. **Fake-work gate** — :mod:`core.agents` may not simulate work: no canned
   ``"Completed: ..."``, no ``asyncio.sleep`` standing in for a task, no
   ``AgentState.<member that does not exist>``.
3. **Behaviour** — a pooled agent's task really goes to the canonical JobQueue,
   a failed job is reported as a failure, and swarm mail lands on the durable
   harness bus.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: ``.chat(`` sites that are exempt because of what they are, with the reason.
#: The gate asserts every key here still exists, so the list cannot rot silently.
EXEMPT = {
    "core/llm.py": "the transport itself — governance calls into it",
    "core/models/gateway.py": "the ModelGateway transport — governance calls into it",
    "core/doctor.py": "health probe must measure the raw model, with no context block appended",
}

_GOVERNANCE_RE = re.compile(r"governed_turn\(|governed_call\(|prepare_turn\(|_govern\(|_budget\(")


def _code_lines(source: str, label: str = "") -> list[tuple[int, str]]:
    """``(lineno, text)`` with multi-line string interiors and comments dropped.

    Both gates read code, not prose: docstrings deliberately quote the old
    simulated work and the old ungoverned call sites, and a prompt written as a
    multi-line string has interior lines at column 0 that would otherwise be taken
    for code and cut a function's scope short. Quote positions come from the
    tokenizer, not from counting ``\"\"\"`` occurrences — these prompts nest quotes,
    a heuristic desyncs, and the gate would silently become a no-op.
    """
    import io
    import tokenize

    string_types = {tokenize.STRING}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        part = getattr(tokenize, name, None)
        if part is not None:
            string_types.add(part)

    lines = source.splitlines()
    skip: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in string_types and tok.end[0] > tok.start[0]:
                # f-strings are tokenised into several pieces (3.12+), so every
                # string-family token that spans rows hides its interior rows.
                skip.update(range(tok.start[0] + 1, tok.end[0] + 1))
            elif tok.type == tokenize.COMMENT and lines[tok.start[0] - 1].strip() == tok.string:
                skip.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError, IndentationError) as exc:
        raise AssertionError(f"harness gate cannot parse {label or '<source>'}: {exc}") from exc
    return [(n, line) for n, line in enumerate(lines, start=1) if n not in skip]


def _py_files():
    for base in ("core", "gateway", "tools", "hermus_cli"):
        for path in (ROOT / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def _functions(source: str, label: str = "") -> list[tuple[int, int, str]]:
    """``(start, end, body)`` for every def block, 1-based, scoped over code lines."""
    lines = _code_lines(source, label)
    out: list[tuple[int, int, str]] = []
    for i, (n, line) in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("def ") or stripped.startswith("async def ")):
            continue
        indent = len(line) - len(line.lstrip())
        j = i + 1
        body = [line]
        while j < len(lines):
            nxt = lines[j][1]
            # A blank line belongs to the body; only real dedented code ends it.
            if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                break
            body.append(nxt)
            j += 1
        end = lines[j][0] - 1 if j < len(lines) else lines[-1][0]
        out.append((n, end, "\n".join(body)))
    return out


def _ungoverned_calls(source: str, label: str) -> tuple[list[tuple[int, str]], int]:
    """``(offenders, checked)`` for model calls that their own function never governs.

    Scope comes from the AST, not indentation: these modules build prompts as
    multi-line f-strings whose interpolation rows sit at column 0, and an
    indent-based reader cuts a function short and reports governed turns as
    ungoverned (or, worse, hides a real one).

    ``chat`` is the only verb that sends a prompt in this tree — ``embed`` is a
    vector call, ``model.generate`` is TTS, ``task_store.complete`` is bookkeeping,
    and ``llm.complete`` lives in the exempt transport.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AssertionError(f"harness gate cannot parse {label}: {exc}") from exc

    funcs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno
    ]
    offenders: list[tuple[int, str]] = []
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "chat"):
            continue
        text = (ast.get_source_segment(source, node) or "").lstrip()
        if text.startswith(("agent.chat(", "self.chat(")):
            continue  # HermusAgent.chat governs its own turn inside the engine
        checked += 1
        inner = [f for f in funcs if f.lineno <= node.lineno <= f.end_lineno]
        inner.sort(key=lambda f: f.end_lineno - f.lineno)
        scope = (ast.get_source_segment(source, inner[0]) or "") if inner else ""
        if not _GOVERNANCE_RE.search(scope):
            offenders.append((node.lineno, text.splitlines()[0][:90]))
    return offenders, checked


def test_no_ungoverned_model_turn_exists():
    """A hand-built prompt must be governed in the same function that sends it."""
    offenders: list[str] = []
    checked = 0
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        if ".chat(" not in source:
            continue
        found, seen = _ungoverned_calls(source, rel)
        checked += seen
        offenders.extend(f"{rel}:{n}: {line}" for n, line in found)

    assert checked > 20, f"the gate stopped seeing model-call sites (checked={checked})"
    assert not offenders, "ungoverned model turn(s) — route them through governed_turn/governed_call:\n" + "\n".join(offenders)


def test_documented_exemptions_are_still_real_files():
    for rel in EXEMPT:
        assert (ROOT / rel).exists(), f"stale exemption in the harness gate: {rel}"


def _offenders_of(source: str) -> list[int]:
    """Run the gate's own rule against a snippet."""
    found, _seen = _ungoverned_calls(source, "<snippet>")
    return [n for n, _line in found]


def test_the_gate_actually_fails_on_an_ungoverned_turn():
    """A gate that cannot fail is decoration.

    The snippet is the planner shape: a def holding a multi-line f-string prompt
    (which used to break scope detection and let the whole file pass unnoticed),
    then a model call with and without governance.
    """
    ungoverned = (
        'class P:\n'
        '    def plan(self, task):\n'
        '        prompt = f"""line one\n'
        'INTERPOLATED: {task}\n'
        'line three"""\n'
        '        return self.llm.chat([{"role": "user", "content": prompt}])\n'
    )
    governed = ungoverned.replace(
        'self.llm.chat([',
        'self.llm.chat(governed_call([',
    ).replace('content": prompt}])', 'content": prompt}]))')

    assert _offenders_of(ungoverned) == [6], "the gate missed a bare model turn"
    assert _offenders_of(governed) == [], "the gate flags a governed turn"
    assert _offenders_of('def run(a):\n    return agent.chat(a)\n') == [], "HermusAgent.chat is governed by the engine"


def test_pooled_agents_never_fake_work():
    """core.agents executes through the canonical queue or says it failed.

    Checked on the function bodies, not the whole file: the docstrings quote the
    old simulated code on purpose, and a file-wide grep would hit my own prose.
    """

    def bodies(rel: str, names: tuple[str, ...]) -> str:
        src = (ROOT / rel).read_text(encoding="utf-8")
        found = ""
        for _start, _end, body in _functions(src):
            head = body.splitlines()[0].strip()
            if any(head.startswith(f"{kw} {name}(") for kw in ("def", "async def") for name in names):
                found += body
        assert found, f"no {names} found in {rel} — the gate is checking nothing"
        return found

    agent_bodies = bodies(
        "core/agents/agent.py",
        ("run_task", "_run_through_queue", "_handle_task_message", "_handle_collaboration"),
    )
    for lie in ("asyncio.sleep", "Completed:", "Task completed", "simulate", "TODO: Actually"):
        assert lie not in agent_bodies, f"simulated work still in core/agents/agent.py: {lie!r}"
    assert "submit_and_wait" in agent_bodies, "a pooled agent must use the canonical JobQueue"

    spec_bodies = bodies("core/agents/specialization.py", ("execute_tool",))
    assert "get_tool_gateway" in spec_bodies, "ToolRunner must execute through the canonical ToolGateway"
    assert "run_task" not in spec_bodies.replace("async def run_task", ""), "execute_tool must not fake a tool run as a prompt"


def test_no_reference_to_a_nonexistent_agent_state():
    """``AgentState.active`` raised AttributeError, swallowed per-agent, and made
    /api/v1/agents/ping report the whole fleet dead."""
    src = (ROOT / "gateway/routes_agents.py").read_text(encoding="utf-8")
    assert "AgentState.active" not in src
    from core.agents.agent import AgentState

    assert not hasattr(AgentState, "active")


# ---------------------------------------------------------------------------
# Behaviour: a pooled agent's task is a real job, reported honestly
# ---------------------------------------------------------------------------
@pytest.fixture()
def _isolated_store(monkeypatch, tmp_path):
    from core.harness import bus, sessions

    (tmp_path / "sessions").mkdir(exist_ok=True)
    monkeypatch.setattr(sessions, "_dir", lambda: tmp_path / "sessions")
    monkeypatch.setattr(bus, "_path", lambda: tmp_path / "bus.json")
    return tmp_path


def _make_agent(agent_id: str):
    from core.agents.agent import Agent, AgentConfig

    return Agent(agent_id=agent_id, config=AgentConfig(name=agent_id, model="mock/mock", timeout=5))


def test_run_task_returns_the_real_answer_and_records_the_session(_isolated_store, monkeypatch):
    from core.harness import sessions
    from core.harness.runtime import HarnessRuntime

    def fake_submit(kind, payload, **kwargs):
        assert kind == "runtime.turn", "the pooled agent must use the canonical turn kind"
        assert payload["text"] == "do the thing"
        return {"status": "done", "result": {"response": "the real answer"}}

    monkeypatch.setattr("subagents.subagent.submit_and_wait", fake_submit)
    # No model backend in tests: govern the session bookkeeping only.
    monkeypatch.setattr(HarnessRuntime, "prepare_turn", lambda *a, **k: {"messages": [], "enabled": False})

    async def scenario():
        agent = _make_agent("agent-real")
        try:
            return agent, await agent.run_task("do the thing")
        finally:
            agent._running = False
            if agent._listener_task:
                agent._listener_task.cancel()

    agent, answer = asyncio.run(scenario())

    assert answer == "the real answer"
    assert agent.stats.tasks_completed == 1
    assert agent.stats.tasks_failed == 0
    record = sessions.get("agent-real")
    assert record is not None and record["status"] == "done", "a pooled agent must appear in the harness session store"


def test_run_task_reports_failure_instead_of_inventing_success(_isolated_store, monkeypatch):
    from core.harness import sessions
    from core.harness.runtime import HarnessRuntime

    monkeypatch.setattr(HarnessRuntime, "prepare_turn", lambda *a, **k: {"messages": [], "enabled": False})

    def failing_submit(kind, payload, **kwargs):
        return {"status": "failed", "error": "no model backend reachable", "result": {}}

    monkeypatch.setattr("subagents.subagent.submit_and_wait", failing_submit)

    async def scenario():
        agent = _make_agent("agent-fail")
        try:
            return agent, await agent.run_task("do the thing")
        finally:
            agent._running = False
            if agent._listener_task:
                agent._listener_task.cancel()

    agent, answer = asyncio.run(scenario())

    assert answer.startswith("Error:"), answer
    assert "no model backend reachable" in answer
    assert agent.stats.tasks_completed == 0
    assert agent.stats.tasks_failed == 1
    assert sessions.get("agent-fail")["status"] == "error"


def test_run_task_reports_a_raising_queue_as_failure(_isolated_store, monkeypatch):
    from core.harness.runtime import HarnessRuntime

    monkeypatch.setattr(HarnessRuntime, "prepare_turn", lambda *a, **k: {"messages": [], "enabled": False})

    def boom(kind, payload, **kwargs):
        raise RuntimeError("job queue unavailable")

    monkeypatch.setattr("subagents.subagent.submit_and_wait", boom)

    async def scenario():
        agent = _make_agent("agent-boom")
        try:
            return agent, await agent.run_task("do the thing")
        finally:
            agent._running = False
            if agent._listener_task:
                agent._listener_task.cancel()

    agent, answer = asyncio.run(scenario())

    assert answer.startswith("Error:")
    assert agent.stats.tasks_completed == 0


def test_swarm_mail_is_durable_on_the_harness_bus(_isolated_store):
    from core.harness import bus

    async def scenario():
        a = _make_agent("swarm-a")
        b = _make_agent("swarm-b")
        try:
            assert await a.send(b.agent_id, "check the tests")
            await a.broadcast("all hands")
            return True
        finally:
            for agent in (a, b):
                agent._running = False
                if agent._listener_task:
                    agent._listener_task.cancel()

    asyncio.run(scenario())

    inbox = bus.inbox("swarm-b", unread_only=False)
    kinds = {m["kind"] for m in inbox}
    assert "dm" in kinds, "an agent DM must be recorded on the durable harness bus"
    assert "broadcast" in kinds, "a broadcast must be recorded once, not as N DMs"
    assert sum(1 for m in inbox if m["kind"] == "broadcast") == 1


# ---------------------------------------------------------------------------
# Behaviour: the governance primitives themselves
# ---------------------------------------------------------------------------
def test_governed_turn_degrades_to_the_callers_messages(monkeypatch):
    from core.harness import runtime as runtime_mod

    def explode(*a, **k):
        raise RuntimeError("harness is down")

    monkeypatch.setattr(runtime_mod.HarnessRuntime, "prepare_turn", explode)
    messages = [{"role": "system", "content": "keep me"}, {"role": "user", "content": "and me"}]

    out = runtime_mod.governed_turn("s1", "hello", messages)

    assert [m["content"] for m in out["messages"]] == ["keep me", "and me"]
    assert out["enabled"] is False, "the caller must be able to see governance did not run"


def test_governed_call_keeps_a_single_prompt_message_intact(monkeypatch, tmp_path):
    """The strict-JSON callers depend on this: budget-only must not rewrite shape."""
    from core.harness import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod.config, "harness_budget_tokens", 200_000)
    prompt = "Return ONLY JSON: {\"steps\": []}\n\nGoal: build a thing"
    messages = [{"role": "system", "content": "You plan."}, {"role": "user", "content": prompt}]

    out = runtime_mod.governed_call(messages, session_id="reasoning.plan")

    assert out == messages, "an under-budget single-shot prompt must pass through byte-identical"


def test_council_member_calls_are_governed(monkeypatch, tmp_path):
    from core.counsel.council import CouncilSession
    from core.harness import sessions
    from core.harness.runtime import HarnessRuntime

    (tmp_path / "sessions").mkdir(exist_ok=True)
    monkeypatch.setattr(sessions, "_dir", lambda: tmp_path / "sessions")
    monkeypatch.setattr(HarnessRuntime, "prepare_turn", lambda *a, **k: {"messages": [], "enabled": False})

    calls: list[str] = []
    # Patch the name the council imports (the package re-export), not the module
    # it is defined in — otherwise the patch silently misses the call site.
    monkeypatch.setattr(
        "core.harness.governed_call",
        lambda messages, **kw: calls.append(kw.get("session_id") or "") or messages,
        raising=True,
    )

    class _Resp:
        content = '{"steps": []}'

    class _LLM:
        def chat(self, messages, **kwargs):
            return _Resp()

    class _Member:
        model = "mock/mock"
        name = "chair"

        def llm(self):
            return _LLM()

    council = CouncilSession.__new__(CouncilSession)
    council.session_id = "council_test"
    council.goal = "build a thing"
    council.members = []

    council._call(_Member(), [{"role": "user", "content": "plan it"}])

    assert calls == ["council_test"], "a council JSON-contract call must be budget-governed under the council session"


def test_harness_subscribe_tool_reaches_the_bus(_isolated_store):
    from core.harness import bus
    from tools.harness_tools import harness_inbox, harness_subscribe

    bus.send("build is red", "worker-1", channel="build")
    assert harness_inbox("listener")["count"] == 0

    out = harness_subscribe("listener", "build")
    assert out["ok"] is True

    seen = harness_inbox("listener")
    assert seen["count"] == 1, "subscribing must make channel traffic reachable for a listener"
    assert harness_inbox("listener", channel="build")["count"] == 1

    harness_subscribe("listener", "build", unsubscribe=True)
    assert harness_inbox("listener")["count"] == 0


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    return TestClient(app)


def test_agents_endpoints_read_and_write_the_harness_bus(client, _isolated_store):
    from core.harness import bus

    sub = client.post("/api/v1/agents/subscribe", json={"session_id": "panel", "channel": "build"})
    assert sub.status_code == 200, sub.text
    assert sub.json()["ok"] is True

    bus.send("nightly failed", "worker-1", channel="build")

    got = client.get("/api/v1/agents/inbox/panel")
    assert got.status_code == 200
    body = got.json()
    assert body["count"] == 1 and body["messages"][0]["body"] == "nightly failed"

    bad = client.post("/api/v1/agents/subscribe", json={"session_id": "panel", "channel": ""})
    assert bad.status_code == 400
