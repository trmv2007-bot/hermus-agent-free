"""StrategyRouter tests: plan selection, escalation, honest attempt tracking.

Escalation is exercised against a FAKE backend (monkeypatched at the backend
seam) — proving router logic, not browser behavior. Real acquisition is
covered by tests/test_web_gateway.py (loopback) and tests/test_web_live.py.
"""
from __future__ import annotations

from typing import Optional

import pytest

from core.web import capabilities
from core.web.errors import WebAcquisitionError
from core.web.models import FailureClass
from core.web.router import StrategyRouter
from core.web.scrapling_backend import RawFetch
from core.web.security import WebSecurityPolicy


class StubConfig:
    web_dynamic_enabled = True
    web_stealth_enabled = False
    web_stealth_solve_cloudflare = False
    web_termux_restrict = True
    web_request_timeout = 5.0
    web_browser_timeout = 5.0


def make_raw(url: str, *, html: str = "", status: int = 200, text: Optional[str] = None):
    """A RawFetch carrying a minimal fake scrapling response."""

    class FakeResponse:
        def __init__(self):
            self.status = status
            self.reason = "OK"
            self.url = url
            self.headers = {"content-type": "text/html"}
            self.body = html.encode()
            self._html = html
            self._text = text if text is not None else "meaningful content " * 20

        def get_all_text(self):
            return self._text

        @property
        def html_content(self):
            return self._html

        def css(self, selector):
            class M:
                def getall(self):
                    return []

                def get(self):
                    return None

                def __getitem__(self, i):
                    raise IndexError

                def __bool__(self):
                    return False

                def __iter__(self):
                    return iter([])

            return M()

    raw = RawFetch(url=url, final_url=url, status=status, size_bytes=len(html))
    raw.response = FakeResponse()
    return raw


@pytest.fixture()
def router():
    return StrategyRouter(StubConfig(), WebSecurityPolicy())


@pytest.fixture(autouse=True)
def _reset_capability_verification():
    """capability.mark_verified mutates process-global state; reset it around
    every test so escalation behavior never depends on execution order."""
    before = dict(capabilities._verified)
    capabilities._cache.clear()
    capabilities._verified.update({"static": False, "dynamic": False, "stealth": False})
    yield
    capabilities._verified.update(before)
    capabilities._cache.clear()


# --------------------------------------------------------------------------- planning
class TestPlan:
    def test_auto_plan_is_static_first(self, router):
        plan = router.plan("auto")
        assert plan, "static fetchers are installed in this venv"
        assert plan[0].value == "static"

    def test_stealth_not_in_plan_unless_enabled(self, router):
        plan = router.plan("auto")
        assert all(s.value != "stealth" for s in plan)

    def test_forced_stealth_without_permission_is_empty(self, router):
        assert router.plan("stealth") == []

    def test_forced_stealth_with_permission(self, monkeypatch):
        cfg = StubConfig()
        cfg.web_stealth_enabled = True
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
            "stealth": {"status": capabilities.NOT_VERIFIED, "detail": ""},
        })
        router = StrategyRouter(cfg, WebSecurityPolicy())
        plan = router.plan("stealth")
        assert [s.value for s in plan] == ["stealth"]

    def test_unavailable_dynamic_is_dropped(self, router, monkeypatch):
        # In this venv browsers are NOT installed, so dynamic is 'unavailable'
        # and must be dropped from the plan honestly (no blind attempts).
        monkeypatch.setattr(capabilities, "is_termux", lambda: False)
        plan = router.plan("auto")
        statuses = {s.value: capabilities.probe()[s.value]["status"] for s in plan}
        assert all(statuses[s.value] in (capabilities.AVAILABLE, capabilities.NOT_VERIFIED)
                   for s in plan)

    def test_termux_restriction_removes_browser_strategies(self, monkeypatch):
        monkeypatch.setattr(capabilities, "is_termux", lambda: True)
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
            "stealth": {"status": capabilities.AVAILABLE, "detail": ""},
        })
        router = StrategyRouter(StubConfig(), WebSecurityPolicy())
        plan = router.plan("auto")
        assert [s.value for s in plan] == ["static"], \
            "Termux default must restrict to lightweight HTTP (untested browsers)"

    def test_termux_explicit_opt_in_allows_dynamic(self, monkeypatch):
        cfg = StubConfig()
        cfg.web_termux_restrict = False
        monkeypatch.setattr(capabilities, "is_termux", lambda: True)
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
            "stealth": {"status": capabilities.NOT_INSTALLED, "detail": ""},
        })
        router = StrategyRouter(cfg, WebSecurityPolicy())
        plan = router.plan("auto")
        assert [s.value for s in plan] == ["static", "dynamic"]

    def test_require_js_prefers_dynamic(self, monkeypatch):
        monkeypatch.setattr(capabilities, "is_termux", lambda: True)
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
        })
        cfg = StubConfig()
        cfg.web_termux_restrict = False
        router = StrategyRouter(cfg, WebSecurityPolicy())
        assert router.plan("auto", require_js=True)[0].value == "dynamic"


# --------------------------------------------------------------------------- escalation
class TestEscalation:
    def _patch_router_backend(self, monkeypatch, router, behaviors: dict):
        """behaviors: strategy -> callable(url) -> RawFetch or raises."""
        import core.web.router as router_mod

        def make_run(strat):
            def run(strategy, url, **kw):
                if strategy not in behaviors:
                    raise WebAcquisitionError(
                        f"no fake behavior for {strategy}",
                        failure_class=FailureClass.DEPENDENCY_MISSING,
                        error_code="WEB_STRATEGY_UNAVAILABLE")
                return behaviors[strategy](url)
            return run

        monkeypatch.setattr(router, "_run_strategy",
                            lambda strat, url, **kw: make_run(strat.value)(strat.value, url, **kw))

    def test_static_success_no_escalation(self, router, monkeypatch):
        calls: list[str] = []

        def static_ok(url):
            calls.append("static")
            return make_raw(url, html="<html><body>plenty of text</body></html>")

        self._patch_router_backend(monkeypatch, router, {"static": static_ok})
        result = router.fetch("https://ok.example/")
        assert result.ok is True
        assert result.strategy == "static"
        assert calls == ["static"]
        assert [a.outcome for a in result.attempts] == ["success"]

    def test_insufficient_static_escalates_to_dynamic(self, monkeypatch):
        cfg = StubConfig()
        cfg.web_termux_restrict = False
        router = StrategyRouter(cfg, WebSecurityPolicy())
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
        })
        calls: list[str] = []
        behaviors = {
            "static": lambda url: make_raw(
                url, html="<html><head><title>t</title></head><body>"
                          "<div id='root'></div></body></html>", text=""),
            "dynamic": lambda url: make_raw(
                url, html="<html><body>rendered content that is long "
                          "enough to be sufficient here</body></html>"),
        }

        def fake_run(strat, url, **kw):
            calls.append(strat.value)
            return behaviors[strat.value](url)

        monkeypatch.setattr(router, "_run_strategy", fake_run)
        result = router.fetch("https://spa.example/")
        assert result.ok is True
        assert result.strategy == "dynamic"
        assert calls == ["static", "dynamic"], "must escalate past the JS shell"
        outcomes = [(a.strategy, a.outcome) for a in result.attempts]
        assert outcomes == [("static", "insufficient"), ("dynamic", "success")]
        assert result.attempts[0].error_class == FailureClass.JS_REQUIRED.value

    def test_failure_does_not_retry_same_strategy(self, router, monkeypatch):
        calls: list[str] = []

        def static_down(url):
            calls.append("static")
            raise WebAcquisitionError("connection refused",
                                      failure_class=FailureClass.CONNECTION,
                                      error_code="WEB_CONNECTION_ERROR", retryable=True)

        self._patch_router_backend(monkeypatch, router, {"static": static_down})
        result = router.fetch("https://down.example/")
        assert result.ok is False
        assert calls.count("static") == 1, "must never blindly retry the same strategy"
        assert result.failure_class == FailureClass.CONNECTION.value

    def test_security_block_aborts_plan(self, router, monkeypatch):
        from core.web.errors import SecurityBlockedError

        calls: list[str] = []

        def static_blocked(url):
            calls.append("static")
            raise SecurityBlockedError("redirect resolves to a private address")

        self._patch_router_backend(monkeypatch, router, {"static": static_blocked})
        result = router.fetch("https://redirects-evil.example/")
        assert result.ok is False
        assert result.error_code == "WEB_SECURITY_BLOCKED"
        assert calls == ["static"], "security refusals are never escalated around"

    def test_no_strategies_available_is_typed(self, monkeypatch):
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_INSTALLED, "detail": "missing"},
            "dynamic": {"status": capabilities.NOT_INSTALLED, "detail": "missing"},
            "stealth": {"status": capabilities.NOT_INSTALLED, "detail": "missing"},
        })
        router = StrategyRouter(StubConfig(), WebSecurityPolicy())
        result = router.fetch("https://anything.example/")
        assert result.ok is False
        assert result.error_code == "WEB_NO_STRATEGY"
        assert result.failure_class == FailureClass.DEPENDENCY_MISSING.value

    def test_forced_strategy_without_fallback_runs_alone(self, monkeypatch):
        cfg = StubConfig()
        cfg.web_termux_restrict = False
        router = StrategyRouter(cfg, WebSecurityPolicy())
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
        })
        calls: list[str] = []

        def fake_run(strat, url, **kw):
            calls.append(strat.value)
            if strat.value == "dynamic":
                return make_raw(url, html="<html><body>fine</body></html>")
            raise WebAcquisitionError("down", failure_class=FailureClass.CONNECTION,
                                      error_code="WEB_CONNECTION_ERROR")

        monkeypatch.setattr(router, "_run_strategy", fake_run)
        result = router.fetch("https://x.example/", strategy="dynamic", allow_fallback=False)
        assert result.ok is True
        assert calls == ["dynamic"]
