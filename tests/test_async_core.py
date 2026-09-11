"""Phase 3: async core — achat parity, structured concurrency, loop hygiene.

All HTTP is faked with ``httpx.MockTransport`` (no network). Sync tests
drive coroutines with :func:`asyncio.run`, matching the repo's existing
async-test convention (see ``tests/test_gateway_realtime.py``).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from core.aio import (
    aclose_async_client,
    gather_limit,
    get_async_client,
    run_sync,
    set_async_client,
)
from core.llm import FreeLLM
from core.multi_key import MultiKeyManager
from core.openai_compat import CompatAPIError, achat_completions

ROOT = Path(__file__).resolve().parent.parent


def _drive(coro):
    return asyncio.run(coro)


def _chat_payload(content: str = "hello-async", tools: list | None = None) -> dict:
    msg: dict = {"content": content}
    if tools is not None:
        msg["tool_calls"] = tools
    return {
        "choices": [{"message": msg}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        "model": "mock-model",
    }


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# achat_completions — async HTTP mirror
# --------------------------------------------------------------------------


def test_achat_completions_success_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=_chat_payload())

    async def main():
        client = _mock_client(handler)
        try:
            return await achat_completions(
                "custom",
                "m",
                [{"role": "user", "content": "hi"}],
                api_key="k",
                base_url="http://mock/v1",
                client=client,
            )
        finally:
            await client.aclose()

    resp = _drive(main())
    assert resp.content == "hello-async"
    assert resp.usage["total_tokens"] == 12
    assert resp.model == "mock-model"


def test_achat_completions_tool_calls_parsed():
    tc = [{"id": "call_1", "function": {"name": "web_search", "arguments": '{"q": "x"}'}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_payload(content="", tools=tc))

    async def main():
        client = _mock_client(handler)
        try:
            return await achat_completions(
                "custom", "m", [{"role": "user", "content": "hi"}], api_key="k", base_url="http://mock/v1", client=client
            )
        finally:
            await client.aclose()

    resp = _drive(main())
    assert resp.tool_calls == [{"name": "web_search", "arguments": {"q": "x"}, "id": "call_1"}]


def test_achat_completions_error_mapping_matches_sync():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limit hit"}})

    async def main():
        client = _mock_client(handler)
        try:
            await achat_completions(
                "custom", "m", [{"role": "user", "content": "hi"}], api_key="k", base_url="http://mock/v1", client=client
            )
        finally:
            await client.aclose()

    with pytest.raises(CompatAPIError) as ei:
        _drive(main())
    assert ei.value.status_code == 429
    assert ei.value.is_rate_limit is True
    assert "rate limit hit" in ei.value.message


def test_achat_completions_timeout_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    async def main():
        client = _mock_client(handler)
        try:
            await achat_completions(
                "custom",
                "m",
                [{"role": "user", "content": "hi"}],
                api_key="k",
                base_url="http://mock/v1",
                timeout=1,
                client=client,
            )
        finally:
            await client.aclose()

    with pytest.raises(CompatAPIError) as ei:
        _drive(main())
    assert ei.value.status_code == 408
    assert ei.value.message == "Request timeout"


# --------------------------------------------------------------------------
# FreeLLM.achat — routing, fallbacks, cache
# --------------------------------------------------------------------------


def test_achat_mock_matches_chat():
    llm = FreeLLM("mock/mock")
    messages = [{"role": "user", "content": "hello there"}]
    sync_resp = llm.chat(messages)
    async_resp = _drive(llm.achat(messages))
    assert async_resp.content == sync_resp.content
    assert "MOCK" in async_resp.content


def test_achat_openai_compat_uses_explicit_key_and_base_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_chat_payload("key-ok"))

    async def main():
        client = _mock_client(handler)
        try:
            llm = FreeLLM("custom/m", api_key="sk-test", base_url="http://mock/v1")
            return await llm.achat([{"role": "user", "content": "key-ok-probe-async-core"}], client=client)
        finally:
            await client.aclose()

    resp = _drive(main())
    assert resp.content == "key-ok"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["url"].startswith("http://mock/v1/chat/completions")


def test_achat_no_provider_message_matches_sync(monkeypatch):
    monkeypatch.setattr(FreeLLM, "_fallback_bundle", lambda self, require_tools=False: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    llm = FreeLLM("groq/llama", api_key=None)
    # Force the no-key branch regardless of ambient .env keys.
    monkeypatch.setattr(FreeLLM, "_resolve_bundle", lambda self: {"key": "", "base_url": "", "default_model": "m"})
    messages = [{"role": "user", "content": "hi"}]
    assert llm.chat(messages).content == _drive(llm.achat(messages)).content
    assert "No usable provider" in llm.chat(messages).content


def test_achat_ollama_native_path():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(404, json={"error": {"message": "not found"}})
        assert request.url.path == "/api/chat"
        return httpx.Response(
            200,
            json={"message": {"content": "native-ok", "tool_calls": []}, "done": True},
        )

    async def main():
        client = _mock_client(handler)
        try:
            llm = FreeLLM("ollama/llama3.1:8b")
            return await llm.achat([{"role": "user", "content": "ollama-native-probe-async-core"}], client=client)
        finally:
            await client.aclose()

    resp = _drive(main())
    assert resp.content == "native-ok"


def test_achat_ollama_down_message_matches_sync(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(FreeLLM, "_fallback_bundle", lambda self, require_tools=False: None)

    async def main():
        client = _mock_client(handler)
        try:
            llm = FreeLLM("ollama/llama3.1:8b")
            return await llm.achat([{"role": "user", "content": "ollama-down-probe-async-core"}], client=client)
        finally:
            await client.aclose()

    resp = _drive(main())
    assert "Ollama not running" in resp.content
    assert "total_tokens" in resp.usage


# --------------------------------------------------------------------------
# Concurrency — overlap proof + bounded gather
# --------------------------------------------------------------------------


def test_parallel_achat_calls_overlap_in_time():
    delay = 0.25
    n = 4

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return httpx.Response(200, json=_chat_payload("p"))

    async def main():
        client = _mock_client(handler)
        try:
            llm = FreeLLM("custom/m", api_key="k", base_url="http://mock/v1")
            # Distinct prompts defeat the LLM cache so all N calls hit HTTP.
            coros = [llm.achat([{"role": "user", "content": f"overlap-probe-async-core-{i}"}], client=client) for i in range(n)]
            return await asyncio.gather(*coros)
        finally:
            await client.aclose()

    # Cache isolation: other tests must not poison these prompts.
    started = time.monotonic()
    resps = _drive(main())
    wall = time.monotonic() - started
    assert [r.content for r in resps] == ["p"] * n
    assert wall < n * delay * 0.95, f"no overlap: {wall:.2f}s for {n}x{delay}s"


def test_gather_limit_bounds_concurrency():
    active = 0
    peak = 0

    async def worker(i: int) -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return i

    async def main():
        return await gather_limit(2, *(worker(i) for i in range(6)))

    assert _drive(main()) == [0, 1, 2, 3, 4, 5]
    assert peak <= 2


def test_run_sync_runs_blocking_work_off_loop():
    async def main():
        return await run_sync(time.sleep, 0.01), await run_sync(lambda a, b=0: a + b, 2, b=3)

    slept, total = _drive(main())
    assert slept is None
    assert total == 5


def test_shared_client_lifecycle():
    async def main():
        first = get_async_client()
        assert get_async_client() is first
        await aclose_async_client()
        second = get_async_client()
        assert second is not first
        await aclose_async_client()
        return True

    assert _drive(main()) is True


def test_set_async_client_injects_test_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_payload("injected"))

    async def main():
        client = _mock_client(handler)
        set_async_client(client)
        try:
            resp = await achat_completions(
                "custom", "m", [{"role": "user", "content": "x"}], api_key="k", base_url="http://mock/v1"
            )
            return resp.content
        finally:
            set_async_client(None)
            await client.aclose()

    assert _drive(main()) == "injected"


# --------------------------------------------------------------------------
# Async multi-key fan-out
# --------------------------------------------------------------------------


def test_aexecute_parallel_with_keys(tmp_path):
    mgr = MultiKeyManager(db_path=str(tmp_path / "mk.json"))
    mgr.add_key("custom", "key-AAA", name="k1", base_url="http://mock/v1", default_model="m", auto_discover=False)
    mgr.add_key("custom", "key-BBB", name="k2", base_url="http://mock/v1", default_model="m", auto_discover=False)

    seen_keys: list = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        seen_keys.append(request.headers.get("authorization"))
        return httpx.Response(200, json=_chat_payload("fanout"))

    async def main():
        client = _mock_client(handler)
        try:
            tasks = [{"prompt": f"fanout-probe-async-core-{i}"} for i in range(3)]
            return await mgr.aexecute_parallel_with_keys("custom", tasks, client=client)
        finally:
            await client.aclose()

    results = _drive(main())
    assert [r["task_id"] for r in results] == [0, 1, 2]
    assert all(r["success"] for r in results)
    assert all(r["response"] == "fanout" for r in results)
    # Round-robin rotation across both keys.
    assert {r["key_name"] for r in results} == {"k1", "k2"}
    assert len(seen_keys) == 3


def test_aexecute_parallel_with_keys_no_keys(tmp_path):
    mgr = MultiKeyManager(db_path=str(tmp_path / "mk.json"))

    async def main():
        return await mgr.aexecute_parallel_with_keys("nosuch", [{"prompt": "x"}])

    results = _drive(main())
    assert results[0]["success"] is False


# --------------------------------------------------------------------------
# Loop hygiene — blocking core calls stay off the gateway event loop
# --------------------------------------------------------------------------


def _src(name: str) -> str:
    return (ROOT / "gateway" / name).read_text(encoding="utf-8")


def test_voice_inline_fallback_runs_off_loop():
    src = _src("routes_voice.py")
    assert src.count("asyncio.to_thread(_run_inline,") == 2


def test_local_defense_scan_runs_off_loop():
    src = _src("routes_subsystems.py")
    assert "asyncio.to_thread(\n        scan_folder," in src
    assert "asyncio.to_thread(run_local_scan_mission, bundle" in src
    assert "asyncio.to_thread(run_local_scan_mission, mission_id)" in src
    assert "asyncio.to_thread(\n                    mission_engine.resume_mission," in src


def test_upload_extract_runs_off_loop():
    src = (ROOT / "gateway" / "gateway.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(\n                extract_document," in src
