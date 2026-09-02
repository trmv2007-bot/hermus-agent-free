"""Regression tests for crawl strategy escalation + rate-limit propagation.

Audit findings #6 and #7:

* the crawl worker used to force ``strategy="static", allow_fallback=False`` for
  every page, defeating the intelligent router — a JS-heavy page discovered
  mid-crawl could never escalate. It now honors the crawl's ``strategy`` policy;
* the configured per-domain delay (``web_crawl_per_domain_delay_ms``) never
  reached the plan/worker — ``CrawlPlan`` always used its hard-coded default. It
  is now threaded Config → WebGateway → plan_crawl → CrawlPlan → CrawlWorker.
"""
from __future__ import annotations

import threading
import time

from core.web.crawl import CrawlWorker, plan_crawl
from core.web.models import FetchStrategy, WebResult
from core.web.security import WebSecurityPolicy


class RecordingRouter:
    """Router stand that records the strategy/fallback each page was fetched with
    and returns a canned successful WebResult (no network)."""

    def __init__(self, *, returned_strategy: str = "static"):
        self.calls: list[dict] = []
        self._returned = returned_strategy

    def fetch(self, url, *, strategy="auto", want_markdown=False,
              allow_fallback=True, **kw):
        self.calls.append({"url": url, "strategy": strategy,
                           "allow_fallback": allow_fallback})
        return WebResult(ok=True, url=url, final_url=url, status_code=200,
                         title="t", text="plenty of body text here " * 5,
                         strategy=self._returned, size_bytes=200, sha256="x")


def _policy():
    return WebSecurityPolicy(allow_private_addresses=True)


class TestCrawlStrategyPolicy:
    def test_auto_crawl_uses_auto_router_not_forced_static(self):
        plan = plan_crawl(["http://x.example/"], requested_pages=1, requested_depth=0,
                          strategy="auto")
        router = RecordingRouter()
        worker = CrawlWorker(plan, router=router, policy=_policy())
        worker.run()
        assert router.calls, "worker must fetch at least the start URL"
        assert router.calls[0]["strategy"] == "auto"
        assert router.calls[0]["allow_fallback"] is True

    def test_static_only_crawl_stays_static(self):
        plan = plan_crawl(["http://x.example/"], requested_pages=1, requested_depth=0,
                          strategy="static")
        router = RecordingRouter()
        worker = CrawlWorker(plan, router=router, policy=_policy())
        worker.run()
        assert router.calls[0]["strategy"] == "static"

    def test_dynamic_crawl_requests_dynamic(self):
        plan = plan_crawl(["http://x.example/"], requested_pages=1, requested_depth=0,
                          strategy="dynamic")
        router = RecordingRouter(returned_strategy="dynamic")
        worker = CrawlWorker(plan, router=router, policy=_policy())
        worker.run()
        assert router.calls[0]["strategy"] == "dynamic"

    def test_unknown_strategy_falls_back_to_auto(self):
        plan = plan_crawl(["http://x.example/"], strategy="banana")
        assert plan.strategy == "auto"

    def test_max_dynamic_pages_budget_caps_browser_escalation(self):
        # 3 start URLs, dynamic strategy, but only 1 browser escalation permitted.
        urls = ["http://x.example/a", "http://x.example/b", "http://x.example/c"]
        plan = plan_crawl(urls, requested_pages=3, requested_depth=0,
                          requested_concurrency=1, strategy="dynamic",
                          max_dynamic_pages=1, max_pages_ceiling=5)
        router = RecordingRouter(returned_strategy="dynamic")
        worker = CrawlWorker(plan, router=router, policy=_policy())
        worker.run()
        strategies = [c["strategy"] for c in router.calls]
        # first page escalates (dynamic), subsequent pages are forced back to static
        assert strategies[0] == "dynamic"
        assert strategies[1:] == ["static", "static"]


class TestPerDomainDelayPropagation:
    def test_plan_carries_configured_delay(self):
        plan = plan_crawl(["http://x.example/"], per_domain_delay_ms=750)
        assert plan.per_domain_delay_ms == 750

    def test_zero_delay_is_honored(self):
        plan = plan_crawl(["http://x.example/"], per_domain_delay_ms=0)
        assert plan.per_domain_delay_ms == 0

    def test_worker_honors_delay_between_same_domain_hits(self):
        # Two same-domain pages, concurrency 1, 300ms delay → total ≥ ~300ms.
        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=300,
                          max_pages_ceiling=5)
        router = RecordingRouter()
        worker = CrawlWorker(plan, router=router, policy=_policy())
        started = time.monotonic()
        worker.run()
        elapsed_ms = (time.monotonic() - started) * 1000
        assert elapsed_ms >= 250, f"delay not honored (elapsed {elapsed_ms:.0f}ms)"

    def test_zero_delay_is_fast(self):
        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=1, per_domain_delay_ms=0,
                          max_pages_ceiling=5)
        router = RecordingRouter()
        worker = CrawlWorker(plan, router=router, policy=_policy())
        started = time.monotonic()
        worker.run()
        elapsed_ms = (time.monotonic() - started) * 1000
        assert elapsed_ms < 250, f"zero delay should be fast (elapsed {elapsed_ms:.0f}ms)"


class TestGatewayPropagatesConfig:
    def test_gateway_crawl_threads_config_delay_into_plan(self, monkeypatch):
        """Config.web_crawl_per_domain_delay_ms must reach CrawlPlan via the
        gateway (previously silently ignored)."""
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
            "web_crawl_per_domain_delay_ms": 640,
            "web_max_sessions": 4, "web_session_ttl": 300.0,
            "web_cache_enabled": False, "web_cache_size": 8, "web_cache_ttl": 60,
        })()
        gateway = WebGateway(cfg, bus=EventBus())

        captured = {}
        real_plan = gw_mod.plan_crawl

        def spy_plan(*a, **kw):
            captured.update(kw)
            return real_plan(*a, **kw)

        monkeypatch.setattr(gw_mod, "plan_crawl", spy_plan)
        # Fail fast at the policy stage would skip planning; use a resolvable-looking
        # loopback host allowed by the test policy.
        gateway.crawl(["http://127.0.0.1:9/"], max_pages=1, max_depth=0)
        assert captured.get("per_domain_delay_ms") == 640
        assert captured.get("strategy") == "static"


class BlockingRouter:
    """Router stand whose fetches rendezvous on a barrier — every page fetch of
    a batch is genuinely in flight at the same moment (real threads, no
    network). Proves the max_dynamic_pages reservation is atomic: with the old
    check-then-increment logic all concurrent workers passed the budget check
    before any one of them incremented the counter."""

    def __init__(self, parties: int):
        self.barrier = threading.Barrier(parties, timeout=10)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def fetch(self, url, *, strategy="auto", want_markdown=False,
              allow_fallback=True, **kw):
        with self._lock:
            self.calls.append({"url": url, "strategy": strategy,
                               "allow_fallback": allow_fallback})
        try:
            self.barrier.wait()  # hold until ALL batch fetches are in flight
        except threading.BrokenBarrierError:
            pass
        returned = "dynamic" if strategy in ("auto", "dynamic", "stealth") else "static"
        return WebResult(ok=True, url=url, final_url=url, status_code=200,
                         title="t", text="plenty of body text here " * 5,
                         strategy=returned, size_bytes=200, sha256="x")


class TestDynamicBudgetAtomicity:
    def test_budget_never_exceeded_under_real_concurrency(self):
        """3 pages fetched concurrently, budget 1 → exactly ONE browser fetch."""
        urls = [f"http://x.example/{i}" for i in range(3)]
        plan = plan_crawl(urls, requested_pages=3, requested_depth=0,
                          requested_concurrency=3, strategy="dynamic",
                          max_dynamic_pages=1, max_pages_ceiling=5,
                          per_domain_delay_ms=0)
        router = BlockingRouter(3)
        worker = CrawlWorker(plan, router=router, policy=_policy())
        summary = worker.run()
        strategies = sorted(c["strategy"] for c in router.calls)
        assert strategies == ["dynamic", "static", "static"], \
            f"budget of 1 must yield exactly one browser fetch, got {strategies}"
        assert summary["dynamic_pages_used"] == 1
        # over-budget pages are pinned static with no escalation permitted
        static_calls = [c for c in router.calls if c["strategy"] == "static"]
        assert all(c["allow_fallback"] is False for c in static_calls)

    def test_zero_budget_means_unlimited(self):
        urls = [f"http://x.example/{i}" for i in range(3)]
        plan = plan_crawl(urls, requested_pages=3, requested_depth=0,
                          requested_concurrency=3, strategy="dynamic",
                          max_dynamic_pages=0, max_pages_ceiling=5,
                          per_domain_delay_ms=0)
        router = BlockingRouter(3)
        worker = CrawlWorker(plan, router=router, policy=_policy())
        summary = worker.run()
        assert [c["strategy"] for c in router.calls] == ["dynamic"] * 3
        assert summary["dynamic_pages_used"] == 3

    def test_auto_page_that_stays_static_returns_its_slot(self):
        """AUTO reserves the browser slot up front; when the router resolves the
        page statically the slot must be released for later pages."""
        urls = [f"http://x.example/{i}" for i in range(3)]
        plan = plan_crawl(urls, requested_pages=3, requested_depth=0,
                          requested_concurrency=1, strategy="auto",
                          max_dynamic_pages=1, max_pages_ceiling=5,
                          per_domain_delay_ms=0)
        router = RecordingRouter(returned_strategy="static")
        worker = CrawlWorker(plan, router=router, policy=_policy())
        summary = worker.run()
        # every page keeps the auto policy — the unused slot was returned each time
        assert [c["strategy"] for c in router.calls] == ["auto"] * 3
        assert summary["dynamic_pages_used"] == 0

    def test_fetch_exception_releases_reserved_slot(self):
        class FlakyRouter:
            def __init__(self):
                self.calls: list[str] = []

            def fetch(self, url, *, strategy="auto", want_markdown=False,
                      allow_fallback=True, **kw):
                self.calls.append(strategy)
                if len(self.calls) == 1:
                    raise RuntimeError("browser crashed")
                return WebResult(ok=True, url=url, final_url=url, status_code=200,
                                 title="t", text="plenty of body text here " * 5,
                                 strategy="dynamic", size_bytes=200, sha256="x")

        urls = ["http://x.example/a", "http://x.example/b"]
        plan = plan_crawl(urls, requested_pages=2, requested_depth=0,
                          requested_concurrency=1, strategy="dynamic",
                          max_dynamic_pages=1, max_pages_ceiling=5,
                          per_domain_delay_ms=0)
        router = FlakyRouter()
        worker = CrawlWorker(plan, router=router, policy=_policy())
        worker.run()
        # first fetch crashed → its reserved slot was released → the second
        # page may still use the single browser slot
        assert router.calls == ["dynamic", "dynamic"]
