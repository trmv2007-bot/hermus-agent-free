"""Regression tests for post-redirect SSRF re-validation and response-size caps.

These target two HIGH-priority audit findings:

* The final URL a server redirects us to must be re-validated through the SAME
  security battery as the requested URL — a public host that 30x-redirects to a
  loopback / private / blocked / disallowed-scheme target must be REFUSED, and
  the tool surface must see a typed failed WebResult, never a raw exception.
* The configured response-size limit must be enforced as early as the backend
  permits (immediately after acquisition, before Hermus retains/parses the body)
  and must abort the plan (never escalate to a heavier strategy).

The redirect tests drive the security policy directly (deterministic, no live
redirect server needed) plus an end-to-end path through the backend/router using
a fake Scrapling response whose ``final_url`` / ``history`` point at forbidden
targets.
"""
from __future__ import annotations

import pytest

from core.web.errors import ResponseTooLargeError, SecurityBlockedError
from core.web.scrapling_backend import ScraplingBackend
from core.web.security import WebSecurityPolicy


# --------------------------------------------------------------------------- fakes
class FakeResponse:
    """Minimal Scrapling-Response stand-in for backend._to_raw()."""

    def __init__(self, *, url: str, body: bytes = b"<html><body>hi</body></html>",
                 status: int = 200, history=None, content_type: str = "text/html"):
        self.url = url
        self.body = body
        self.status = status
        self.reason = "OK"
        self.headers = {"content-type": content_type}
        self.history = [type("H", (), {"url": h})() for h in (history or [])]


# --------------------------------------------------------------------------- policy-level
class TestFinalUrlRevalidation:
    """check_final_url must run the full battery, not a partial re-implementation."""

    def test_public_to_loopback_ipv4_refused(self):
        policy = WebSecurityPolicy()  # production posture: private refused
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://127.0.0.1:8080/", purpose="redirect")

    def test_public_to_localhost_refused(self):
        policy = WebSecurityPolicy()
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://localhost/admin", purpose="redirect")

    def test_public_to_ipv6_loopback_refused(self):
        policy = WebSecurityPolicy()
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://[::1]:9000/", purpose="redirect")

    def test_public_to_link_local_metadata_refused(self):
        policy = WebSecurityPolicy()
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://169.254.169.254/latest/meta-data/",
                                   purpose="redirect")

    def test_public_to_private_rfc1918_refused(self):
        policy = WebSecurityPolicy()
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://10.0.0.5/internal", purpose="redirect")

    def test_public_to_blocked_domain_refused(self):
        policy = WebSecurityPolicy(blocked_domains=("evil.example",),
                                   allow_private_addresses=True)
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("https://evil.example/landing", purpose="redirect")

    def test_public_to_disallowed_scheme_refused(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("file:///etc/passwd", purpose="redirect")

    def test_public_to_embedded_credentials_refused(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        with pytest.raises(SecurityBlockedError):
            policy.check_final_url("http://user:pw@example.com/", purpose="redirect")

    def test_public_to_allowlisted_public_ok(self):
        policy = WebSecurityPolicy(allow_private_addresses=True)
        # A benign public redirect returns the normalized URL.
        out = policy.check_final_url("https://example.com/final", purpose="redirect")
        assert out == "https://example.com/final"

    def test_identical_url_fast_path(self):
        policy = WebSecurityPolicy()  # would normally resolve DNS
        out = policy.check_final_url("https://ok.example/x",
                                     requested_url="https://ok.example/x")
        assert out == "https://ok.example/x"


# --------------------------------------------------------------------------- backend end-to-end
class TestBackendRedirectEnforcement:
    """_to_raw must refuse a fetched response whose final/hop URL is forbidden."""

    def test_final_url_to_private_is_blocked(self):
        backend = ScraplingBackend()
        policy = WebSecurityPolicy()  # private refused
        resp = FakeResponse(url="http://127.0.0.1:8080/")
        with pytest.raises(SecurityBlockedError):
            backend._to_raw("https://public.example/start", resp, 0.0, policy=policy)

    def test_multi_hop_redirect_through_private_is_blocked(self):
        backend = ScraplingBackend()
        policy = WebSecurityPolicy()
        # final URL is public but an intermediate hop touched loopback.
        resp = FakeResponse(url="https://public.example/final",
                            history=["https://public.example/start",
                                     "http://127.0.0.1/pivot"])
        with pytest.raises(SecurityBlockedError):
            backend._to_raw("https://public.example/start", resp, 0.0, policy=policy)

    def test_benign_public_redirect_passes(self):
        backend = ScraplingBackend()
        policy = WebSecurityPolicy(allow_private_addresses=True)
        resp = FakeResponse(url="https://example.com/final",
                            history=["https://example.com/start"])
        raw = backend._to_raw("https://example.com/start", resp, 0.0, policy=policy)
        assert raw.final_url == "https://example.com/final"

    def test_no_policy_skips_revalidation(self):
        """Backward-compatible: callers that pass no policy get no extra checks."""
        backend = ScraplingBackend()
        resp = FakeResponse(url="http://127.0.0.1/x")
        raw = backend._to_raw("http://127.0.0.1/x", resp, 0.0)
        assert raw.final_url == "http://127.0.0.1/x"


# --------------------------------------------------------------------------- size limit
class TestResponseSizeEnforcement:
    def test_oversized_body_rejected_after_acquisition(self):
        backend = ScraplingBackend()
        policy = WebSecurityPolicy(allow_private_addresses=True, max_response_bytes=100)
        resp = FakeResponse(url="https://example.com/big", body=b"x" * 500)
        with pytest.raises(ResponseTooLargeError):
            backend._to_raw("https://example.com/big", resp, 0.0, policy=policy)

    def test_body_under_limit_passes(self):
        backend = ScraplingBackend()
        policy = WebSecurityPolicy(allow_private_addresses=True, max_response_bytes=10_000)
        resp = FakeResponse(url="https://example.com/small", body=b"x" * 500)
        raw = backend._to_raw("https://example.com/small", resp, 0.0, policy=policy)
        assert raw.size_bytes == 500

    def test_check_response_zero_limit_disables_cap(self):
        policy = WebSecurityPolicy(max_response_bytes=0)
        policy.check_response(10_000_000)  # no raise when cap is 0

    def test_size_error_carries_size_limit_class(self):
        policy = WebSecurityPolicy(max_response_bytes=10)
        try:
            policy.check_response(100)
        except ResponseTooLargeError as exc:
            assert exc.failure_class.value == "size_limit"
            assert exc.error_code == "WEB_RESPONSE_TOO_LARGE"
        else:  # pragma: no cover
            pytest.fail("expected ResponseTooLargeError")


class TestRouterAbortsOnSize:
    """An oversized response aborts the plan — no escalation to a heavier strategy."""

    def test_router_returns_typed_size_failure_and_does_not_escalate(self, monkeypatch):
        from core.web import capabilities
        from core.web.router import StrategyRouter

        class Cfg:
            web_dynamic_enabled = True
            web_stealth_enabled = False
            web_termux_restrict = False
            web_request_timeout = 5.0
            web_browser_timeout = 5.0

        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.AVAILABLE, "detail": ""},
        })
        router = StrategyRouter(Cfg(), WebSecurityPolicy(allow_private_addresses=True,
                                                         max_response_bytes=10))
        calls: list[str] = []

        def fake_run(strat, url, **kw):
            calls.append(strat.value)
            raise ResponseTooLargeError("response of 500 bytes exceeds the configured limit (10 bytes)")

        monkeypatch.setattr(router, "_run_strategy", fake_run)
        result = router.fetch("https://big.example/")
        assert result.ok is False
        assert result.failure_class == "size_limit"
        assert result.error_code == "WEB_RESPONSE_TOO_LARGE"
        assert calls == ["static"], "size cap is terminal — must not escalate"
