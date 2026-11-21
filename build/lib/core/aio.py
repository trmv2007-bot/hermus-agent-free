"""Async foundation for the Hermus core.

The gateway event loop must never be stalled by sync blocking work, and
parallel LLM fan-out should use structured concurrency instead of ad-hoc
threads. This module is the single choke point for both:

- :func:`run_sync` — run a sync callable in a worker thread (named wrapper
  over :func:`asyncio.to_thread` so call sites read uniformly).
- :func:`gather_limit` — ``asyncio.gather`` with bounded concurrency.
- :func:`get_async_client` / :func:`set_async_client` /
  :func:`aclose_async_client` — one shared ``httpx.AsyncClient`` for all
  core HTTP so connections pool across requests; tests inject a
  ``MockTransport`` client via :func:`set_async_client`.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

T = TypeVar("T")

_client: httpx.AsyncClient | None = None


def get_async_client(timeout: float = 120.0) -> httpx.AsyncClient:
    """Shared async HTTP client (connection pooling across calls)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=timeout)
    return _client


def set_async_client(client: httpx.AsyncClient | None) -> None:
    """Override the shared client (tests inject a ``MockTransport`` client)."""
    global _client
    _client = client


async def aclose_async_client() -> None:
    """Close and drop the shared client (gateway lifespan shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


async def run_sync(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run sync ``func`` in a worker thread; the loop stays responsive."""
    if kwargs:
        func = functools.partial(func, **kwargs)
        return await asyncio.to_thread(func, *args)
    return await asyncio.to_thread(func, *args)


async def gather_limit(n: int, *coros: Awaitable[T]) -> list[T]:
    """Gather ``coros`` with at most ``n`` running concurrently (order kept)."""
    sem = asyncio.Semaphore(max(1, int(n)))

    async def _one(coro: Awaitable[T]) -> T:
        async with sem:
            return await coro

    return list(await asyncio.gather(*(_one(c) for c in coros)))
