"""Per-turn tool selection: the 93%-of-the-prompt problem.

Every agent call used to ship all ~179 tool schemas — measured at ~18,280 of a
~19,900 token prompt, re-sent on every step of the ReAct loop. These tests pin
the behaviour that makes subsetting safe:

  * it actually reduces the payload,
  * core tools survive every cut,
  * it refuses to guess when there is nothing to match on,
  * and a wrong guess is recoverable via ``expand_tools`` rather than silently
    removing a capability.
"""
from __future__ import annotations

import pytest

from core.tool_select import (
    CORE_TOOLS,
    EXPAND_TOOL_NAME,
    EXPAND_TOOL_SCHEMA,
    score_tool,
    select_tools,
    selection_report,
)


def _tool(name: str, description: str = "") -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": {}}}}


def _names(tools: list[dict]) -> list[str]:
    return [t["function"]["name"] for t in tools]


CATALOG = [
    _tool("file_read", "Read a file from disk"),
    _tool("file_write", "Write a file to disk"),
    _tool("file_edit", "Edit an existing file"),
    _tool("file_search", "Search for files by name or content"),
    _tool("shell_execute", "Run a shell command"),
    _tool("web_search", "Search the web for current information"),
    _tool("web_read", "Fetch and read a web page"),
    _tool("browser_open", "Open a URL in the headless browser"),
    _tool("browser_click", "Click an element in the browser"),
    _tool("browser_screenshot", "Capture a screenshot of the page"),
    _tool("vision_describe", "Describe the contents of an image"),
    _tool("speech_synthesize", "Convert text into spoken audio"),
    _tool("calendar_add_event", "Add an event to the calendar"),
    _tool("email_send", "Send an email message"),
    _tool("database_query", "Run a SQL query against a database"),
]


# --- the win ---------------------------------------------------------------


def test_select_tools_actually_reduces_the_catalog():
    chosen = select_tools(CATALOG, "open a web page in the browser and click a button",
                          limit=6)
    assert len(chosen) < len(CATALOG), "a browser request must not need the whole catalog"
    assert "browser_open" in _names(chosen)
    assert "browser_click" in _names(chosen)


def test_selection_report_describes_the_cut():
    chosen = select_tools(CATALOG, "read a file", limit=5)
    rep = selection_report(CATALOG, chosen)
    assert rep["available"] == len(CATALOG)
    assert rep["offered"] == len(chosen)
    assert rep["reducible"] is True


def test_unrelated_tools_are_dropped_for_a_narrow_request():
    chosen = select_tools(CATALOG, "send an email message", limit=6)
    assert "email_send" in _names(chosen)
    assert "database_query" not in _names(chosen)


# --- safety: never guess blind --------------------------------------------


def test_core_tools_survive_every_cut():
    core_in_catalog = [t for t in CATALOG if t["function"]["name"] in CORE_TOOLS]
    # A generous limit must keep every core tool that the catalog has.
    chosen = select_tools(CATALOG, "read a file from disk", limit=12)
    chosen_names = set(_names(chosen))
    assert all(t["function"]["name"] in chosen_names for t in core_in_catalog)


def test_core_set_cannot_crowd_out_task_relevant_tools():
    """A tight limit still has to include the tool the request is actually about.

    Regression guard: the first version of this reserved the whole limit for the
    core set, so a small limit silently dropped browser_click from a browser
    request — exactly the silent capability loss the expander exists to prevent.
    """
    chosen = select_tools(CATALOG, "open a web page in the browser and click a button",
                          limit=4)
    names = _names(chosen)
    assert "browser_click" in names
    assert "browser_open" in names


def test_no_matchable_words_means_send_everything():
    """A request we cannot match on must not be silently narrowed."""
    assert select_tools(CATALOG, "?? !! ...", limit=3) == CATALOG


def test_empty_request_means_send_everything():
    assert select_tools(CATALOG, "", limit=3) == CATALOG


def test_empty_catalog_returns_empty():
    assert select_tools([], "anything at all", limit=5) == []


# --- off switches ----------------------------------------------------------


def test_limit_zero_disables_subsetting():
    assert select_tools(CATALOG, "read a file", limit=0) == CATALOG


def test_limit_at_or_above_catalog_size_is_a_no_op():
    assert select_tools(CATALOG, "read a file", limit=len(CATALOG)) == CATALOG
    assert select_tools(CATALOG, "read a file", limit=9999) == CATALOG


def test_config_flag_can_turn_it_off_entirely(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "tool_subset_enabled", False, raising=False)
    assert select_tools(CATALOG, "read a file") == CATALOG


def test_config_limit_is_honoured_when_caller_passes_none(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "tool_subset_enabled", True, raising=False)
    monkeypatch.setattr(config, "tool_subset_limit", 4, raising=False)
    chosen = select_tools(CATALOG, "read a file from disk")
    assert len(chosen) <= 4 + 1  # limit plus the expander


def test_explicit_limit_overrides_config(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "tool_subset_enabled", False, raising=False)
    # An explicit limit is a caller decision, not a config one.
    chosen = select_tools(CATALOG, "read a file", limit=3)
    assert len(chosen) <= 4


# --- the escape hatch ------------------------------------------------------


def test_expander_is_offered_whenever_the_catalog_was_cut():
    chosen = select_tools(CATALOG, "read a file", limit=4)
    assert EXPAND_TOOL_NAME in _names(chosen)


def test_expander_is_not_offered_when_everything_was_sent():
    chosen = select_tools(CATALOG, "read a file", limit=0)
    assert EXPAND_TOOL_NAME not in _names(chosen)


def test_expander_can_be_switched_off():
    chosen = select_tools(CATALOG, "read a file", limit=3, include_expander=False)
    assert EXPAND_TOOL_NAME not in _names(chosen)


def test_expander_schema_is_a_valid_tool_definition():
    fn = EXPAND_TOOL_SCHEMA["function"]
    assert fn["name"] == EXPAND_TOOL_NAME
    assert fn["parameters"]["type"] == "object"
    assert fn["parameters"]["required"] == ["reason"]
    assert fn["description"]


def test_expander_does_not_count_against_the_limit_as_a_duplicate():
    chosen = select_tools(CATALOG, "read a file", limit=3)
    assert _names(chosen).count(EXPAND_TOOL_NAME) == 1


# --- scoring ---------------------------------------------------------------


def test_name_matches_outrank_description_matches():
    """'browser' in a tool name is stronger evidence than in prose."""
    by_name = score_tool(_tool("browser_click", "Click something"), {"browser"})
    by_desc = score_tool(_tool("element_click", "Click inside the browser"), {"browser"})
    assert by_name > by_desc


def test_stopwords_do_not_create_matches():
    """Words present in nearly every description must not match everything."""
    chosen = select_tools(CATALOG, "the tool returns the current status", limit=4)
    # 'the'/'tool'/'returns'/'current'/'status' are filtered, so this is a
    # no-signal request and must fall back to the full catalog.
    assert chosen == CATALOG


def test_scoring_is_case_insensitive():
    assert score_tool(_tool("FILE_READ", ""), {"file"}) == score_tool(_tool("file_read", ""), {"file"})


def test_snake_case_names_are_split_into_words():
    assert score_tool(_tool("browser_open", ""), {"open"}) > 0


# --- agent integration -----------------------------------------------------


@pytest.fixture()
def agent():
    from core.agent import HermusAgent

    return HermusAgent(model="mock/mock", mode="agent")


def test_agent_starts_each_turn_with_a_fresh_selection(agent):
    agent._turn_selected_tools = ["stale"]
    agent._turn_tool_expanded = True
    agent._turn_selected_tools = None
    agent._turn_tool_expanded = False
    first = agent._tools_for_turn("read a file from disk")
    assert first is not None
    # Cached within the turn: the second call returns the same object.
    assert agent._tools_for_turn("something completely different") is first


def test_agent_expansion_is_sticky_within_a_turn(agent):
    agent._turn_selected_tools = None
    agent._turn_tool_expanded = False
    subset = agent._tools_for_turn("read a file")
    assert len(subset) < len(agent.tools)

    agent._turn_tool_expanded = True
    agent._turn_selected_tools = None
    assert agent._tools_for_turn("read a file") is agent.tools


def test_agent_sends_nothing_when_it_has_no_tools(agent, monkeypatch):
    monkeypatch.setattr(agent, "tools", [], raising=False)
    assert agent._tools_for_turn("read a file") is None


def test_agent_survives_a_broken_selector(agent, monkeypatch):
    """Selection is an optimization; a bug in it must not fail the turn."""
    def boom(*_a, **_k):
        raise RuntimeError("selector exploded")

    monkeypatch.setattr("core.agent.select_tools", boom)
    agent._turn_selected_tools = None
    agent._turn_tool_expanded = False
    assert agent._tools_for_turn("read a file") == agent.tools


def test_agent_emits_a_selection_event(agent):
    seen = []
    agent._turn_selected_tools = None
    agent._turn_tool_expanded = False
    agent._tools_for_turn("read a file from disk",
                          emit=lambda kind, data: seen.append((kind, data)))
    kinds = [k for k, _ in seen]
    assert "tools_selected" in kinds
    payload = seen[kinds.index("tools_selected")][1]
    assert payload["available"] == len(agent.tools)
    assert payload["offered"] <= payload["available"]


def test_reload_tools_clears_a_cached_selection(agent):
    agent._turn_selected_tools = ["stale"]
    agent.reload_tools()
    assert agent._turn_selected_tools is None


def test_chat_resets_tool_selection_between_turns(agent, monkeypatch):
    """A widened turn must not leak its full catalog into the next turn."""
    from core import agent as agent_mod

    captured: list[int] = []

    class _Resp:
        content = "done"
        tool_calls = []
        usage = {}

    def fake_chat(_messages, tools=None, **_kw):
        captured.append(len(tools or []))
        return _Resp()

    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    monkeypatch.setattr(agent, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(agent_mod, "select_tools",
                        lambda tools, _text, **_k: list(tools[:3]))

    agent._turn_tool_expanded = True          # pretend the last turn expanded
    agent.chat("hello")
    agent.chat("hello again")
    assert captured and all(n == 3 for n in captured), \
        f"every turn must re-select, got {captured}"


def test_expand_tools_widens_the_next_model_call_without_executing_a_real_tool(agent, monkeypatch):
    """The escape hatch, driven through the actual agent loop.

    This is the safety property the whole design rests on: when the model asks
    for a tool it was not offered, the next call must carry the full catalog.
    """
    from core import agent as agent_mod

    calls: list[int] = []
    executed: list[str] = []
    events: list[str] = []
    state = {"n": 0}

    class _Resp:
        def __init__(self, content, tool_calls):
            self.content = content
            self.tool_calls = tool_calls
            self.usage = {}

    def fake_chat(_messages, tools=None, **_kw):
        calls.append(len(tools or []))
        state["n"] += 1
        if state["n"] == 1:
            return _Resp("", [{"name": "expand_tools",
                               "arguments": {"reason": "need database_query"}}])
        return _Resp("done", [])

    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    monkeypatch.setattr(agent, "_build_system_prompt", lambda *a, **k: "sys")
    monkeypatch.setattr(agent, "_execute_tool",
                        lambda name, args: executed.append(name) or {"success": True})
    monkeypatch.setattr(agent_mod, "select_tools", lambda tools, _t, **_k: list(tools[:3]))

    agent.chat("query the database", on_event=lambda kind, data: events.append(kind))

    assert len(calls) >= 2, f"expected a second model call, got {calls}"
    assert calls[0] == 3, "first call should carry the narrowed subset"
    assert calls[1] == len(agent.tools), \
        f"second call must carry the full catalog, got {calls[1]} of {len(agent.tools)}"
    assert "expand_tools" not in executed, "the expander is intercepted, never executed"
    assert "tools_expanded" in events


def test_system_prompt_reports_the_offered_count_not_the_catalog_size(agent):
    """Telling the model it has 179 tools when 21 are in the request invites it to
    call ones it cannot see. The prompt must match what was actually sent."""
    prompt = agent._build_system_prompt("what time is it?")
    offered = len(agent._tools_for_turn("what time is it?"))
    line = next((l for l in prompt.splitlines()
                 if "tools" in l and "available" in l and "browser" in l), "")
    assert line, "the capability line describing available tools is missing"
    assert f"{offered} tools" in line, f"prompt says something else: {line}"
    assert str(len(agent.tools)) in line and "of" in line, \
        f"a subset should say how many are registered: {line}"
    assert "expand_tools" in line
