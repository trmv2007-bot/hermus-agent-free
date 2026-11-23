"""The probe cache behind the Control Room's status endpoints.

`/doctor/status`, `/api/jarvis/status`, `/api/v1/console/panels` and the VRAM
sample each walk a subsystem and cost 8-12 s cold. They blocked the gateway while
they ran — with a Control Room open, `/healthz` took 6.6 s — because the page asks
for all of them at once. These pin the three properties the fix relies on: a
repeated read is served from cache, concurrent misses share one computation, and
a failing probe never poisons the value that came before it.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.probe_cache import ProbeCache, fresh_requested


def test_a_second_read_is_served_from_cache():
    cache: ProbeCache[int] = ProbeCache("test", ttl=60)
    calls = []

    async def compute() -> int:
        calls.append(1)
        return 42

    async def run() -> tuple[int, int]:
        first = await cache.get(compute)
        second = await cache.get(compute)
        return first, second

    assert asyncio.run(run()) == (42, 42)
    assert len(calls) == 1, "the probe must run once, not once per caller"


def test_concurrent_misses_share_one_computation():
    cache: ProbeCache[int] = ProbeCache("test", ttl=60)
    calls = []

    async def compute() -> int:
        calls.append(1)
        await asyncio.sleep(0.05)
        return len(calls)

    async def run() -> list[int]:
        return await asyncio.gather(*(cache.get(compute) for _ in range(8)))

    results = asyncio.run(run())
    assert len(calls) == 1, "eight callers must not start eight probes"
    assert results == [1] * 8


def test_fresh_bypasses_the_cached_value():
    cache: ProbeCache[int] = ProbeCache("test", ttl=600)
    counter = {"n": 0}

    async def compute() -> int:
        counter["n"] += 1
        return counter["n"]

    async def run() -> list[int]:
        return [await cache.get(compute), await cache.get(compute), await cache.get(compute, fresh=True)]

    assert asyncio.run(run()) == [1, 1, 2]


def test_expired_value_is_recomputed():
    cache: ProbeCache[int] = ProbeCache("test", ttl=0.01)
    counter = {"n": 0}

    async def compute() -> int:
        counter["n"] += 1
        return counter["n"]

    async def run() -> list[int]:
        first = await cache.get(compute)
        await asyncio.sleep(0.05)
        return [first, await cache.get(compute)]

    assert asyncio.run(run()) == [1, 2]


def test_a_failing_probe_raises_but_keeps_the_last_good_value():
    cache: ProbeCache[int] = ProbeCache("test", ttl=0.01)
    state = {"fail": False}

    async def compute() -> int:
        if state["fail"]:
            raise RuntimeError("probe exploded")
        return 7

    async def run() -> tuple[int, int | None]:
        good = await cache.get(compute)
        state["fail"] = True
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError):
            await cache.get(compute)
        return good, cache.peek()

    good, last = asyncio.run(run())
    assert good == 7
    assert last == 7, "a failed refresh must not wipe the last good answer"


def test_warm_reports_failure_instead_of_raising():
    cache: ProbeCache[int] = ProbeCache("test", ttl=60)

    async def boom() -> int:
        raise RuntimeError("no backend")

    assert asyncio.run(cache.warm(boom)) is False
    assert cache.peek() is None


def test_fresh_requested_parses_the_query_flag():
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        assert fresh_requested(truthy) is True
    for falsy in ("", None, "0", "false", "no"):
        assert fresh_requested(falsy) is False
