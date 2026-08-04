"""Test Token Counting - Free"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

def test_token_counter():
    from core.token_counter import token_counter, count_tokens

    text = "Hello, this is a test message about Python async programming."
    count = count_tokens(text)
    print(f"✅ Token count for '{text[:30]}...': {count} tokens")

    messages = [
        {"role": "user", "content": "What is Python async?"},
        {"role": "assistant", "content": "Python async is..."}
    ]
    count_msgs = token_counter.count_messages(messages)
    print(f"✅ Messages token count: {count_msgs}")

    # Cost estimation
    cost = token_counter.estimate_cost(1000, 500, model="groq/llama-3.1-70b-versatile")
    print(f"✅ Cost estimation Groq 70b: {cost}")

    cost_free = token_counter.estimate_cost(1000, 500, model="ollama/llama3.1:8b")
    assert cost_free["is_free"] == True
    assert cost_free["total_cost"] == 0.0
    print(f"✅ Free model cost 0: {cost_free}")

def test_llm_usage():
    from core.llm import FreeLLM
    llm = FreeLLM("mock/mock")
    resp = llm.chat([{"role": "user", "content": "Hello"}])
    assert hasattr(resp, 'usage')
    assert resp.usage.get("total_tokens", 0) > 0
    print(f"✅ LLM response has usage: {resp.usage}")

def test_memory_token_tracking():
    from core.memory import memory
    import uuid
    session_id = f"test_token_{uuid.uuid4().hex[:6]}"
    usage = {
        "model": "mock/mock",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "total_cost": 0.0,
        "is_free": True
    }
    memory.add_token_usage(session_id, usage)
    data = memory.get_token_usage(session_id=session_id)
    assert data["totals"]["total_tokens"] >= 150
    print(f"✅ Memory token tracking: {data['totals']}")

if __name__ == "__main__":
    test_token_counter()
    test_llm_usage()
    test_memory_token_tracking()
    print("\nAll token counting tests passed - Free, no paywall, tracks tokens for all models!")
