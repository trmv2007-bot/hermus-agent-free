#!/usr/bin/env python3
"""Repeatable performance benchmarks for the remodel.

Run:  python scripts/bench.py            (or:  make bench)
      python scripts/bench.py async      (one section)
      python scripts/bench.py --json bench.json

Three sections, one per thing the remodel changed:

async    Does the new async provider path *actually* overlap I/O? Injects a
         fixed per-call latency and compares a sequential sync loop against an
         asyncio gather. The interesting number is the speedup, and the honest
         check is that async wall time stays near one latency unit instead of
         scaling linearly with the number of calls.

gateway  Cost of the Phase 4 cross-cutting middleware (request context, rate
         limit, error envelope). Two otherwise identical apps are driven
         through the same harness so the difference is the middleware, not
         the framework. Reported as p50/p95 and the per-request delta.

cli      Cold-start cost of the CLI after the hermus_cli/ split: real
         subprocess time for `hermus --help` plus in-process import +
         build_parser(). These are budgets, not races — the point is that
         splitting one 2776-line file into 46 modules did not make startup
         measurably slower.

Numbers vary by machine; re-run before/after a change rather than comparing
across machines. Nothing here hits the network.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os_env = {"HERMUS_NO_DOTENV": "1"}


def _env():
    import os

    env = dict(os.environ)
    env["HERMUS_NO_DOTENV"] = "1"
    return env


def _pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


# ---------------------------------------------------------------------------
# 1. Async provider path
# ---------------------------------------------------------------------------
def bench_async(calls: int = 24, latency: float = 0.02) -> dict:
    """Sync loop vs asyncio gather over N provider calls with injected latency."""
    import httpx

    from core import openai_compat as compat
    from core.aio import gather_limit

    payload = {
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "created": 0,
        "model": "mock/model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    # --- async: MockTransport with an async delay -------------------------
    async def _handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(latency)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    async def _async_once(i: int):
        return await compat.achat_completions(
            "custom",
            "mock/model",
            [{"role": "user", "content": f"ping-{i}"}],
            api_key="bench",
            base_url="http://bench/v1",
            client=client,
        )

    # --- sync: stub requests.post with an equivalent blocking delay -------
    class _FakeResponse:
        status_code = 200
        headers: dict = {}
        text = json.dumps(payload)

        def json(self):
            return payload

    real_post = compat.requests.post

    def _fake_post(url, **kwargs):
        time.sleep(latency)
        return _FakeResponse()

    async def _run() -> tuple[float, float, float]:
        t0 = time.perf_counter()
        results = await gather_limit(calls, *(_async_once(i) for i in range(calls)))
        async_full = time.perf_counter() - t0

        # bounded concurrency: limit 4 over `calls` items
        t0 = time.perf_counter()
        await gather_limit(4, *(_async_once(i) for i in range(calls)))
        async_limited = time.perf_counter() - t0
        assert len(results) == calls
        return async_full, async_limited, 0.0

    async_full, async_limited, _ = asyncio.run(_run())

    compat.requests.post = _fake_post
    try:
        t0 = time.perf_counter()
        for i in range(calls):
            compat.chat_completions(
                "custom",
                "mock/model",
                [{"role": "user", "content": f"ping-{i}"}],
                api_key="bench",
                base_url="http://bench/v1",
            )
        sync_total = time.perf_counter() - t0
    finally:
        compat.requests.post = real_post

    asyncio.run(client.aclose())

    floor = latency  # one call is the theoretical minimum
    return {
        "calls": calls,
        "latency_per_call_s": latency,
        "sync_sequential_s": round(sync_total, 4),
        "async_gather_s": round(async_full, 4),
        "async_gather_limit4_s": round(async_limited, 4),
        "speedup": round(sync_total / async_full, 2) if async_full else 0.0,
        "async_floor_s": floor,
        # True when the gather really overlapped: N calls cost far less than N×L.
        "overlaps": async_full < (calls * latency) / 2,
    }


# ---------------------------------------------------------------------------
# 2. Gateway middleware overhead
# ---------------------------------------------------------------------------
def bench_gateway(requests_n: int = 300) -> dict:
    """Same route, with and without the Phase 4 middleware stack."""
    from fastapi import FastAPI
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.testclient import TestClient

    from gateway.middleware import (
        ErrorEnvelopeMiddleware,
        RateLimitMiddleware,
        RequestContextMiddleware,
    )

    def build(*middlewares) -> FastAPI:
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        # Registration order matches gateway/gateway.py (last = outermost),
        # so the stack is built innermost-first exactly as the gateway does.
        for mw in reversed(middlewares):
            app.add_middleware(mw)
        return app

    def measure(app: FastAPI) -> list[float]:
        samples = []
        with TestClient(app) as client:
            for _ in range(20):  # warm up
                client.get("/ping")
            for _ in range(requests_n):
                t0 = time.perf_counter()
                client.get("/ping")
                samples.append((time.perf_counter() - t0) * 1000)
        return samples

    # The access log writes to stderr on every request; that is real production
    # cost but it would otherwise dominate a per-middleware comparison.
    import logging

    hermus_log = logging.getLogger("hermus")
    saved_level = hermus_log.level
    hermus_log.setLevel(logging.ERROR)
    try:
        stacks = {
            "plain": build(),
            "+request_context": build(RequestContextMiddleware),
            "+rate_limit": build(RateLimitMiddleware, RequestContextMiddleware),
            "+gzip": build(GZipMiddleware, RateLimitMiddleware, RequestContextMiddleware),
            "+error_envelope (full stack)": build(
                ErrorEnvelopeMiddleware,
                GZipMiddleware,
                RateLimitMiddleware,
                RequestContextMiddleware,
            ),
        }
        measured = {name: measure(app) for name, app in stacks.items()}
    finally:
        hermus_log.setLevel(saved_level)

    base = _pct(measured["plain"], 50)
    full = _pct(measured["+error_envelope (full stack)"], 50)
    return {
        "requests": requests_n,
        "p50_by_stack_ms": {k: round(_pct(v, 50), 3) for k, v in measured.items()},
        "p95_full_stack_ms": round(_pct(measured["+error_envelope (full stack)"], 95), 3),
        "full_stack_p50_delta_ms": round(full - base, 3),
        "full_stack_overhead_pct": round(((full - base) / base) * 100, 1) if base else 0.0,
        "note": (
            "TestClient harness (single-threaded, absolute ms are harness-bound); "
            "compare stacks against 'plain', and re-run on the same machine. "
            "Access logging is muted here so it does not mask the middleware cost. "
            f"Run-to-run noise at n={requests_n} is ~0.05ms, so smaller deltas "
            "are not signal."
        ),
    }


# ---------------------------------------------------------------------------
# 3. CLI cold start
# ---------------------------------------------------------------------------
def bench_cli(runs: int = 5) -> dict:
    """Real subprocess `hermus --help` plus in-process import + build_parser."""
    import os

    times = []
    env = dict(os.environ)
    env["HERMUS_NO_DOTENV"] = "1"
    for _ in range(runs):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "hermus.py", "--help"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        times.append((time.perf_counter() - t0) * 1000)
    assert proc.returncode == 0, "hermus --help must exit 0"

    # In-process cost of assembling the parser (the part the split touched).
    import importlib

    t0 = time.perf_counter()
    module = importlib.import_module("hermus_cli")
    import_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    module.build_parser()
    parser_s = time.perf_counter() - t0

    return {
        "runs": runs,
        "help_subprocess_p50_ms": round(_pct(times, 50), 1),
        "help_subprocess_min_ms": round(min(times), 1),
        "import_hermus_cli_s": round(import_s, 4),
        "build_parser_s": round(parser_s, 4),
        "command_count": len(module.COMMANDS),
    }


SECTIONS = {"async": bench_async, "gateway": bench_gateway, "cli": bench_cli}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "sections",
        nargs="*",
        default=None,
        metavar="{" + ",".join(sorted(SECTIONS)) + "}",
        help="which sections to run (default: all)",
    )
    ap.add_argument("--json", dest="json_path", default=None, help="write results as JSON")
    args = ap.parse_args()

    results: dict[str, dict] = {}
    sections = args.sections or sorted(SECTIONS)
    unknown = [n for n in sections if n not in SECTIONS]
    if unknown:
        ap.error(f"unknown section(s): {', '.join(unknown)} (choose from {', '.join(sorted(SECTIONS))})")

    for name in sections:
        print(f"\n=== {name} ===")
        started = time.perf_counter()
        data = SECTIONS[name]()
        results[name] = data
        for key, value in data.items():
            print(f"  {key:28} {value}")
        print(f"  ({time.perf_counter() - started:.1f}s to measure)")

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
