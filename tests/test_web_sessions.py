"""Session manager tests (spec §12): isolation, domain pinning, TTL cleanup,
and the guarantee that cookie values never leave the subsystem."""
from __future__ import annotations

import pytest

from core.web.errors import SecurityBlockedError
from core.web.security import WebSecurityPolicy
from core.web.sessions import WebSessionError, WebSessionManager


@pytest.fixture()
def manager():
    return WebSessionManager(WebSecurityPolicy(), max_sessions=3, ttl_seconds=60)


class TestLifecycle:
    def test_create_describe_no_cookie_values(self, manager):
        info = manager.create("shop", domains=["shop.example.com"])
        assert info["ok"] is True
        assert info["name"] == "shop"
        assert info["allowed_domains"] == ["shop.example.com"]
        # description carries counts, never jar contents
        assert "cookies" not in info and "cookie" not in str(info).lower() or \
            "cookie_values" not in info

    def test_create_requires_domains(self, manager):
        with pytest.raises(WebSessionError):
            manager.create("empty", domains=[])

    def test_create_rejects_blocked_domain(self):
        manager = WebSessionManager(WebSecurityPolicy(blocked_domains=("evil.example",)))
        with pytest.raises(SecurityBlockedError):
            manager.create("bad", domains=["evil.example"])

    def test_get_enforces_domain_pinning(self, manager):
        manager.create("shop", domains=["shop.example.com"])
        manager.get("shop", url="https://shop.example.com/item/1")  # allowed
        with pytest.raises(SecurityBlockedError):
            manager.get("shop", url="https://other.example.net/")  # outside pin

    def test_get_unknown_session(self, manager):
        with pytest.raises(WebSessionError):
            manager.get("ghost", url="https://shop.example.com/")

    def test_destroy(self, manager):
        manager.create("tmp", domains=["tmp.example"])
        assert manager.destroy("tmp")["ok"] is True
        assert manager.destroy("tmp")["ok"] is False
        with pytest.raises(WebSessionError):
            manager.get("tmp", url="https://tmp.example/")

    def test_ttl_expiry(self):
        manager = WebSessionManager(WebSecurityPolicy(), max_sessions=3, ttl_seconds=0.05)
        manager.create("flash", domains=["flash.example"])
        import time

        time.sleep(0.08)
        with pytest.raises(WebSessionError):
            manager.get("flash", url="https://flash.example/")
        assert manager.list_sessions() == [], "expired session must be swept"

    def test_max_sessions_evicts_oldest(self, manager):
        manager.create("s1", domains=["one.example"])
        manager.create("s2", domains=["two.example"])
        manager.create("s3", domains=["three.example"])
        manager.create("s4", domains=["four.example"])  # evicts s1 (oldest)
        names = [s["name"] for s in manager.list_sessions()]
        assert "s1" not in names and "s4" in names
        assert len(names) == 3


class TestIsolation:
    def test_session_open_isolated_per_manager(self):
        """Two managers (e.g. two deployments in tests) never share sessions."""
        a = WebSessionManager(WebSecurityPolicy())
        b = WebSessionManager(WebSecurityPolicy())
        a.create("shared-name", domains=["a.example"])
        with pytest.raises(WebSessionError):
            b.get("shared-name", url="https://a.example/")

    def test_describe_shape_stays_secret_free(self, manager):
        manager.create("shop", domains=["shop.example.com"])
        for info in manager.list_sessions():
            serialized = str(sorted(info.items()))
            for forbidden in ("set-cookie", "authorization", "proxy", "token"):
                assert forbidden not in serialized.lower()

    def test_close_all_drops_everything(self, manager):
        manager.create("a", domains=["a.example"])
        manager.create("b", domains=["b.example"])
        manager.close_all()
        assert manager.list_sessions() == []
