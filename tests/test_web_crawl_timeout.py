"""Hard wall-clock deadline tests for the crawl worker.

The crawl deadline must be hard at every scheduling boundary:

* no new page fetch starts once the deadline is reached;
* per-domain politeness waits are bounded by the deadline (and by
  cancellation) — never a full-length sleep past either;
* in-flight fetches are waited on only up to the deadline plus a short join
  grace, then abandoned honestly (daemon threads — never killed unsafely);
* the summary reports the timeout accurately (status="timeout",
  timed_out=True, a "timeout"-class failure entry).

These tests use router stands with REAL threads and REAL clock timing (no
network) — they prove the worker's scheduling/deadline logic, not fetching.
Real acquisition is covered by tests/test_web_crawl.py (loopback fixture site)
and tests/test_web_live.py.
"""
from __future__ import annotations

import threading
import time

import core.web.crawl as crawl_mod
from core.web.crawl import CrawlWorker, plan_crawl
from core.web.models import WebResult
from core.web.security import WebSecurityPolicy


def _policy():
    return WebSecurityPolicy(allow_private_addresses=True)


def _ok_result(url: str) -> WebResult:
    return WebResult(ok=True, url=url, final_url=url, status_code=200,
                     title="t", text="plenty of body text here " * 5,
                     strategy="static", size_bytes=200, sha256="x")


class SlowRouter:
    """Router stand where every fetch takes ``delay`` seconds of real time."""

    def __init__(self, delay: float):
        self.delay = delay
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, url, **kw):
        with self._lock:
            self.calls.append(url)
        time.sleep(self.delay)
        return _ok_result(url)


class TestWallClockDeadline:
    def test_timeout_is_reported_accurately(self):
        urls = [f"http://x.example/{i}" for i in range(4)]
        plan = plan_crawl(urls, requested_pages=4, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=0,
                          max_pages_ceiling=10)
        router = SlowRouter(0.25)
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=0.4)
        summary = worker.run()
        assert summary["timed_out"] is True
        assert summary["status"] == "timeout"
        assert summary["cancelled"] is False
        assert any(f.get("class") == "timeout" for f in summary["failures"])

    def test_no_new_work_scheduled_after_deadline(self):
        """With a 0.3s budget and 0.25s pages at concurrency 1, only the pages
        that started before the deadline may ever be fetched."""
        urls = [f"http://x.example/{i}" for i in range(8)]
        plan = plan_crawl(urls, requested_pages=8, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=0,
                          max_pages_ceiling=10)
        router = SlowRouter(0.25)
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=0.3)
        started = time.monotonic()
        summary = worker.run()
        elapsed = time.monotonic() - started
        assert len(router.calls) <= 3, \
            f"work kept being scheduled after the deadline: {router.calls}"
        assert summary["timed_out"] is True
        assert elapsed < 2.0, f"crawl overshot the hard deadline ({elapsed:.2f}s)"

    def test_rate_limit_wait_is_deadline_bounded(self):
        """A 10s politeness delay must not hold the crawl past a 0.3s deadline."""
        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=10_000,
                          max_pages_ceiling=5)
        router = SlowRouter(0.0)
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=0.3)
        started = time.monotonic()
        summary = worker.run()
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"politeness wait ignored the deadline ({elapsed:.2f}s)"
        assert len(router.calls) == 1, "second same-domain page must not be fetched"
        assert summary["timed_out"] is True
        assert summary["status"] == "timeout"

    def test_cancellation_interrupts_rate_limit_wait(self):
        """Cooperative cancel must cut a long politeness wait short (bounded
        poll interval, not a full-length sleep)."""
        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=10_000,
                          max_pages_ceiling=5)
        router = SlowRouter(0.0)
        flip_at = time.monotonic() + 0.2
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             should_cancel=lambda: time.monotonic() >= flip_at,
                             wall_clock_seconds=60.0)
        started = time.monotonic()
        summary = worker.run()
        elapsed = time.monotonic() - started
        assert summary["cancelled"] is True
        assert summary["status"] == "cancelled"
        assert elapsed < 5.0, f"cancel did not interrupt the wait ({elapsed:.2f}s)"

    def test_inflight_fetch_is_abandoned_not_killed(self, monkeypatch):
        """A fetch that outlives deadline + grace is abandoned (daemon thread
        keeps running harmlessly) and reported honestly."""
        monkeypatch.setattr(crawl_mod, "_JOIN_GRACE_SECONDS", 0.2)
        urls = ["http://x.example/a"]
        plan = plan_crawl(urls, requested_pages=1, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=0,
                          max_pages_ceiling=5)
        router = SlowRouter(5.0)  # far longer than deadline + grace
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=0.2)
        started = time.monotonic()
        summary = worker.run()
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"join was not bounded ({elapsed:.2f}s)"
        assert summary["timed_out"] is True
        assert summary["status"] == "timeout"
        assert any("abandoned" in (f.get("error") or "") for f in summary["failures"])
        assert summary["pages_processed"] == 0

    def test_generous_deadline_changes_nothing(self):
        """Fast crawls under the deadline behave exactly as before."""
        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=2, per_domain_delay_ms=0,
                          max_pages_ceiling=5)
        router = SlowRouter(0.0)
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=30.0)
        summary = worker.run()
        assert summary["status"] == "completed"
        assert summary["timed_out"] is False
        assert summary["pages_processed"] == 2


class TestJoinBudgetIsGlobal:
    """The join grace is spent ONCE per batch, not once per stuck thread.

    Regression guard: joining each thread with its own ``deadline + grace``
    budget multiplied the effective timeout by the number of in-flight
    fetches (N stuck threads → N × grace of overshoot). The batch must share
    a single absolute join deadline.
    """

    @staticmethod
    def _stuck_threads(count: int, release: threading.Event) -> list[threading.Thread]:
        """Start ``count`` daemon threads that stay alive until released."""
        threads = []
        for _ in range(count):
            t = threading.Thread(target=release.wait, args=(30.0,), daemon=True)
            t.start()
            threads.append(t)
        return threads

    def test_many_stuck_threads_share_one_join_budget(self, monkeypatch):
        monkeypatch.setattr(crawl_mod, "_JOIN_GRACE_SECONDS", 0.3)
        plan = plan_crawl(["http://x.example/a"], requested_pages=1,
                          requested_depth=0, requested_concurrency=1,
                          per_domain_delay_ms=0, max_pages_ceiling=5)
        worker = CrawlWorker(plan, router=SlowRouter(0.0), policy=_policy(),
                             wall_clock_seconds=0.0)
        worker._deadline = time.monotonic()  # deadline already reached

        release = threading.Event()
        stuck = self._stuck_threads(6, release)
        try:
            started = time.monotonic()
            abandoned = worker._join_bounded(stuck)
            elapsed = time.monotonic() - started
        finally:
            release.set()

        assert abandoned == 6, "stuck threads must be reported as abandoned"
        # Global bound is grace (0.3s) + scheduling slack — emphatically NOT
        # 6 × 0.3s, which is what the per-thread budget produced.
        assert elapsed < 1.0, f"join budget multiplied per thread ({elapsed:.2f}s)"
        assert all(t.is_alive() for t in stuck), "threads must never be killed"

    def test_join_waits_are_not_cumulative_as_threads_grow(self, monkeypatch):
        """Doubling the number of stuck threads must not double the wait."""
        monkeypatch.setattr(crawl_mod, "_JOIN_GRACE_SECONDS", 0.4)
        plan = plan_crawl(["http://x.example/a"], requested_pages=1,
                          requested_depth=0, requested_concurrency=1,
                          per_domain_delay_ms=0, max_pages_ceiling=5)
        worker = CrawlWorker(plan, router=SlowRouter(0.0), policy=_policy(),
                             wall_clock_seconds=0.0)

        timings = []
        for count in (2, 8):
            worker._deadline = time.monotonic()
            release = threading.Event()
            stuck = self._stuck_threads(count, release)
            try:
                started = time.monotonic()
                assert worker._join_bounded(stuck) == count
                timings.append(time.monotonic() - started)
            finally:
                release.set()

        two, eight = timings
        assert eight < 1.0, f"8 stuck threads overshot the global bound ({eight:.2f}s)"
        assert eight < two + 0.4, \
            f"wait scaled with thread count ({two:.2f}s → {eight:.2f}s)"

    def test_early_finishers_still_join_normally(self, monkeypatch):
        """A shared budget must not cut short threads that finish in time."""
        monkeypatch.setattr(crawl_mod, "_JOIN_GRACE_SECONDS", 2.0)
        plan = plan_crawl(["http://x.example/a"], requested_pages=1,
                          requested_depth=0, requested_concurrency=1,
                          per_domain_delay_ms=0, max_pages_ceiling=5)
        worker = CrawlWorker(plan, router=SlowRouter(0.0), policy=_policy(),
                             wall_clock_seconds=5.0)
        threads = [threading.Thread(target=time.sleep, args=(0.05,), daemon=True)
                   for _ in range(4)]
        for t in threads:
            t.start()
        assert worker._join_bounded(threads) == 0
        assert not any(t.is_alive() for t in threads)

    def test_concurrent_stuck_fetches_stay_within_global_bound(self, monkeypatch):
        """End-to-end: a full batch of hung fetches is abandoned once, and the
        whole crawl stays inside deadline + one grace period."""
        monkeypatch.setattr(crawl_mod, "_JOIN_GRACE_SECONDS", 0.4)
        urls = [f"http://h{i}.example/a" for i in range(4)]
        plan = plan_crawl(urls, requested_pages=4, requested_depth=0,
                          requested_concurrency=4, per_domain_delay_ms=0,
                          max_pages_ceiling=10, max_concurrency_ceiling=8)
        router = SlowRouter(10.0)  # every fetch hangs far past deadline + grace
        worker = CrawlWorker(plan, router=router, policy=_policy(),
                             wall_clock_seconds=0.3)

        started = time.monotonic()
        summary = worker.run()
        elapsed = time.monotonic() - started

        # deadline (0.3) + one shared grace (0.4) + slack — not 4 × grace.
        assert elapsed < 1.5, f"total wait exceeded the global bound ({elapsed:.2f}s)"
        assert summary["timed_out"] is True
        assert summary["status"] == "timeout"
        assert summary["pages_processed"] == 0
        assert any("abandoned" in (f.get("error") or "") for f in summary["failures"])


class TestGatewayWallClock:
    def test_gateway_honors_requested_wall_clock_bounded_by_ceiling(self, monkeypatch):
        """The wall_clock_seconds argument reaches the worker (previously it
        was silently ignored) and can never exceed the configured ceiling."""
        import core.web.gateway as gw_mod
        from core.events import EventBus
        from core.web.gateway import WebGateway

        cfg = type("C", (), {
            "web_enabled": True, "web_default_strategy": "static",
            "web_dynamic_enabled": False, "web_stealth_enabled": False,
            "web_termux_restrict": True, "web_request_timeout": 5.0,
            "web_browser_timeout": 5.0, "web_max_response_bytes": 1_000_000,
            "web_max_redirects": 5, "web_allow_private_addresses": True,
            "web_allowed_domains": [], "web_blocked_domains": [],
            "web_crawl_max_pages": 5, "web_crawl_max_depth": 1,
            "web_crawl_concurrency": 2, "web_crawl_wall_clock": 30.0,
            "web_crawl_per_domain_delay_ms": 0,
            "web_max_sessions": 4, "web_session_ttl": 300.0,
            "web_cache_enabled": False, "web_cache_size": 8, "web_cache_ttl": 60,
        })()
        gateway = WebGateway(cfg, bus=EventBus())

        seen: dict = {}
        real_worker = gw_mod.CrawlWorker

        def spy_worker(plan, **kw):
            seen["wall_clock_seconds"] = kw.get("wall_clock_seconds")
            return real_worker(plan, **kw)

        monkeypatch.setattr(gw_mod, "CrawlWorker", spy_worker)
        gateway.crawl(["http://127.0.0.1:9/"], max_pages=1, max_depth=0,
                      wall_clock_seconds=7.5)
        assert seen["wall_clock_seconds"] == 7.5

        gateway.crawl(["http://127.0.0.1:9/"], max_pages=1, max_depth=0,
                      wall_clock_seconds=99_999.0)
        assert seen["wall_clock_seconds"] == 30.0, \
            "requested wall clock must be clamped to the configured ceiling"
