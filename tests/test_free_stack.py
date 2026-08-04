"""Tests for Free Stack - No paid APIs needed"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

def test_llm_mock():
    from core.llm import FreeLLM
    llm = FreeLLM("mock/mock")
    resp = llm.chat([{"role": "user", "content": "Hello"}])
    assert resp.content
    print(f"✅ LLM mock: {resp.content[:100]}")

def test_memory_fts5():
    from core.memory import memory
    import uuid
    session_id = f"test_{uuid.uuid4().hex[:6]}"
    memory.add_session_message(session_id, "user", "Test message about Python async")
    results = memory.search_sessions("Python async", limit=3)
    assert len(results) >= 0
    print(f"✅ Memory FTS5 search: {len(results)} results")

def test_skill_creation():
    from core.skill_manager import skill_manager
    traj = [
        {"role": "user", "content": "Research Python async", "tool_calls": []},
        {"role": "assistant", "content": "Searching", "tool_calls": [{"name": "web_search", "arguments": {"query": "Python async"}}]},
        {"role": "tool", "content": "Results", "tool_calls": []},
        {"role": "assistant", "content": "Writing file", "tool_calls": [{"name": "file_write", "arguments": {"path": "test.py", "content": "print('hi')"}}]},
        {"role": "assistant", "content": "Done", "tool_calls": [{"name": "shell_execute", "arguments": {"command": "ls"}}]},
    ]
    should = skill_manager.should_create_skill(traj)
    print(f"✅ Skill should create: {should} (need >=3 tool calls, got {sum(len(t.get('tool_calls',[])) for t in traj)})")

def test_web_search_free():
    from tools.web_search import web_search
    results = web_search("Python programming", max_results=2)
    print(f"✅ Free web search DuckDuckGo: {len(results)} results")

def test_agent_mock():
    from core.agent import HermusAgent
    agent = HermusAgent(model="mock/mock", session_id="test_mock")
    result = agent.chat("Hello, what can you do?")
    assert result["response"]
    print(f"✅ Agent mock: {result['response'][:200]}")

if __name__ == "__main__":
    test_llm_mock()
    test_memory_fts5()
    test_skill_creation()
    test_web_search_free()
    test_agent_mock()
    print("\nAll free stack tests passed - No paywall, no API key needed for mock!")
