"""Performance budgets — loose, machine-independent guards.

These are *not* benchmarks. ``scripts/bench.py`` (``make bench``) is the
measurement tool and prints real numbers; these tests exist so a change that
silently blows up a cost we measured gets caught in CI. Every threshold is
deliberately 5-20x above the observed value, so they fail on regressions, not
on a slow CI box.

Budgets:
* CLI parser assembly — the hermus_cli/ split imports 46 modules; a module
  that starts importing something heavy at import time shows up here.
* Gateway middleware — the Phase 4 stack is cross-cutting; it must stay cheap
  per request.
* Async fan-out — the async path must still overlap I/O (a stricter version of
  this lives in tests/test_async_core.py).
"""

from __future__ import annotations

import time

import pytest


def test_cli_parser_assembly_is_within_budget():
    """Observed ~0.03s; budget 1.0s catches a heavy import creeping in."""
    from hermus_cli import build_parser

    start = time.perf_counter()
    parser = build_parser()
    elapsed = time.perf_counter() - start
    assert parser is not None
    assert elapsed < 1.0, f"build_parser() took {elapsed:.3f}s (budget 1.0s)"


def test_cli_dispatch_table_is_complete():
    """A missing command is a silent CLI regression, not a perf issue."""
    from hermus_cli import COMMANDS

    assert len(COMMANDS) >= 43, f"expected >=43 command groups, got {len(COMMANDS)}"


def test_gateway_middleware_stays_cheap():
    """Observed full-stack p50 ~0.83ms vs ~0.70ms bare; budget 10ms/request."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gateway.middleware import (
        ErrorEnvelopeMiddleware,
        RateLimitMiddleware,
        RequestContextMiddleware,
    )

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    for mw in reversed((ErrorEnvelopeMiddleware, RateLimitMiddleware, RequestContextMiddleware)):
        app.add_middleware(mw)

    samples = []
    with TestClient(app) as client:
        client.get("/ping")  # warm up
        for _ in range(50):
            start = time.perf_counter()
            client.get("/ping")
            samples.append((time.perf_counter() - start) * 1000)

    p50 = sorted(samples)[len(samples) // 2]
    assert p50 < 10.0, f"gateway middleware p50 {p50:.2f}ms exceeds the 10ms budget"


@pytest.mark.asyncio
async def test_async_fanout_still_overlaps():
    """24 calls with a 20ms per-call delay must not take 24 x 20ms."""
    import httpx

    from core import openai_compat as compat
    from core.aio import gather_limit

    payload = {
        "id": "chatcmpl-budget",
        "object": "chat.completion",
        "created": 0,
        "model": "mock/model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(0.02)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        start = time.perf_counter()
        results = await gather_limit(
            12,
            *(
                compat.achat_completions(
                    "custom",
                    "mock/model",
                    [{"role": "user", "content": f"p{i}"}],
                    api_key="budget",
                    base_url="http://budget/v1",
                    client=client,
                )
                for i in range(12)
            ),
        )
        elapsed = time.perf_counter() - start
    finally:
        await client.aclose()

    assert len(results) == 12
    # Sequential would be ~0.24s. Budget is deliberately loose (0.15s).
    assert elapsed < 0.15, f"12 overlapping calls took {elapsed:.3f}s; they are not overlapping"
