"""Capability detection tests (spec §20/§21): the four honest states, Termux
detection, and verification only via REAL fetches — never import success."""
from __future__ import annotations

import pytest

from core.web import capabilities


@pytest.fixture(autouse=True)
def _reset_cache():
    capabilities._cache.clear()
    before = dict(capabilities._verified)
    capabilities._verified.update({"static": False, "dynamic": False, "stealth": False})
    yield
    capabilities._cache.clear()
    capabilities._verified.update(before)


class TestProbeStates:
    def test_states_vocabulary_is_exact(self):
        assert capabilities.probe(force=True)["static"]["status"] in (
            capabilities.AVAILABLE, capabilities.NOT_VERIFIED, capabilities.UNAVAILABLE,
            capabilities.NOT_INSTALLED,
        )

    def test_scrapling_absent_reports_not_installed(self, monkeypatch):
        """Simulate a machine WITHOUT scrapling: parser + fetchers not installed,
        no crash, and every strategy honestly not_installed."""
        monkeypatch.setattr(capabilities, "_importable", lambda name: False)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: None)
        caps = capabilities.probe(force=True)
        assert caps["parser"]["status"] == capabilities.NOT_INSTALLED
        assert caps["static"]["status"] == capabilities.NOT_INSTALLED
        assert caps["dynamic"]["status"] == capabilities.NOT_INSTALLED
        assert caps["stealth"]["status"] == capabilities.NOT_INSTALLED

    def test_browser_missing_reports_unavailable_not_available(self, monkeypatch):
        """The critical honesty case: scrapling present, browser binaries absent.
        dynamic/stealth must NOT be reported as usable."""
        monkeypatch.setattr(capabilities, "_importable", lambda name: True)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: "0.0.0")
        monkeypatch.setattr(capabilities, "_playwright_chromium_path", lambda: None)
        caps = capabilities.probe(force=True)
        assert caps["dynamic"]["status"] == capabilities.UNAVAILABLE
        assert caps["stealth"]["status"] == capabilities.UNAVAILABLE
        assert "chromium" in caps["dynamic"]["detail"].lower()
        assert "scrapling install" in caps["dynamic"]["detail"]

    def test_browser_binary_present_is_not_verified_not_available(self, monkeypatch):
        """A binary on disk is NOT proof of a working browser — must read
        not_verified until a real fetch succeeds."""
        from pathlib import Path

        monkeypatch.setattr(capabilities, "_importable", lambda name: True)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: "0.0.0")
        monkeypatch.setattr(capabilities, "_playwright_chromium_path",
                            lambda: Path("/tmp/fake-chrome"))
        caps = capabilities.probe(force=True)
        assert caps["dynamic"]["status"] == capabilities.NOT_VERIFIED
        assert caps["dynamic"]["status"] != capabilities.AVAILABLE

    def test_strategy_ready_refuses_unavailable(self, monkeypatch):
        monkeypatch.setattr(capabilities, "probe", lambda force=False: {
            "static": {"status": capabilities.NOT_VERIFIED, "detail": ""},
            "dynamic": {"status": capabilities.UNAVAILABLE, "detail": ""},
            "stealth": {"status": capabilities.NOT_INSTALLED, "detail": ""},
        })
        assert capabilities.strategy_ready("static") is True
        assert capabilities.strategy_ready("dynamic") is False
        assert capabilities.strategy_ready("stealth") is False


class TestVerification:
    def test_mark_verified_flips_status_to_available(self, monkeypatch):
        monkeypatch.setattr(capabilities, "_importable", lambda name: True)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: "0.0.0")
        monkeypatch.setattr(capabilities, "_playwright_chromium_path", lambda: None)
        capabilities.mark_verified("dynamic")
        caps = capabilities.probe(force=True)
        assert caps["dynamic"]["status"] == capabilities.AVAILABLE
        assert "live fetch" in caps["dynamic"]["detail"]

    def test_verification_only_comes_from_real_fetch(self, monkeypatch):
        """The router calls mark_verified ONLY after a strategy actually returned
        content — importing/being installed never flips the flag."""
        monkeypatch.setattr(capabilities, "_importable", lambda name: True)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: "0.0.0")
        monkeypatch.setattr(capabilities, "_playwright_chromium_path", lambda: None)
        assert capabilities.probe(force=True)["static"]["status"] == capabilities.NOT_VERIFIED
        capabilities.mark_verified("static")  # this is the router's real-fetch hook
        assert capabilities.probe(force=True)["static"]["status"] == capabilities.AVAILABLE


class TestTermux:
    def test_termux_detected_by_env(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118")
        assert capabilities.is_termux() is True

    def test_not_termux_on_plain_linux(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setattr(capabilities.platform, "system", lambda: "Linux")
        monkeypatch.setattr(capabilities.os.environ, "get",
                            lambda k, d=None: "" if k == "PATH" else d)
        assert capabilities.is_termux() is False

    def test_probe_reports_termux_flag(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118")
        caps = capabilities.probe(force=True)
        assert caps["termux"] is True


class TestStatusSummary:
    def test_summary_shape(self, monkeypatch):
        monkeypatch.setattr(capabilities, "_importable", lambda name: True)
        monkeypatch.setattr(capabilities, "scrapling_version", lambda: "9.9.9")
        monkeypatch.setattr(capabilities, "_playwright_chromium_path", lambda: None)
        summary = capabilities.status_summary()
        assert set(summary) == {"parser", "static", "dynamic", "stealth", "markdown"}
        assert summary["parser"] == capabilities.AVAILABLE
        assert summary["dynamic"] == capabilities.UNAVAILABLE
