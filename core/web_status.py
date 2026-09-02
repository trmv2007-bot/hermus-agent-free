"""Doctor-facing web acquisition status checks.

Thin adapter between :mod:`core.web.capabilities` (the honest capability
probes) and the diagnostics/doctor check format. Lives in ``core/`` (not
``core/web``) because it serves the doctor's *report*, while importing the
canonical capability probe — no duplicated probing logic.
"""
from __future__ import annotations

from typing import Any

from core.web import capabilities

_HINTS = {
    "parser": "pip install 'scrapling[fetchers]'",
    "static": "pip install 'scrapling[fetchers]' then run any web_fetch to verify",
    "dynamic": "scrapling install   # downloads the Playwright Chromium browser",
    "stealth": "scrapling install + set HERMUS_WEB_STEALTH=1",
    "markdown": "pip install 'scrapling[ai]' (text extraction works without it)",
}

# Statuses that mean "the capability can run right now".
_OK_STATUSES = {capabilities.AVAILABLE, capabilities.NOT_VERIFIED}


def web_status_checks() -> list[dict[str, Any]]:
    """One diagnostics check per capability + config sanity, honest levels."""
    checks: list[dict[str, Any]] = []
    try:
        caps = capabilities.probe()
    except Exception as exc:  # noqa: BLE001 - diagnostics never raise
        return [{
            "name": "web_acquisition", "ok": False, "level": "recommended",
            "detail": f"probe failed: {type(exc).__name__}: {exc}",
            "hint": "pip install 'scrapling[fetchers]'",
        }]

    for name in ("parser", "static", "dynamic", "stealth", "markdown"):
        info = caps.get(name, {"status": capabilities.NOT_INSTALLED, "detail": "unknown"})
        status = info.get("status", capabilities.NOT_INSTALLED)
        ok = status in _OK_STATUSES
        checks.append({
            "name": f"web_{name}",
            "ok": ok,
            "level": "recommended",
            "detail": f"{status}: {info.get('detail', '')}",
            "hint": "" if ok else _HINTS.get(name, ""),
        })

    # Configuration sanity (security posture surfaced, not guessed).
    try:
        from .config import config

        checks.append({
            "name": "web_config",
            "ok": True,
            "level": "recommended",
            "detail": (
                f"enabled={config.web_enabled} strategy={config.web_default_strategy} "
                f"dynamic={config.web_dynamic_enabled} stealth={config.web_stealth_enabled} "
                f"private_addrs={'ALLOWED' if config.web_allow_private_addresses else 'blocked'} "
                f"crawl<={config.web_crawl_max_pages}p x{config.web_crawl_max_depth}d "
                f"resp<={config.web_max_response_bytes // (1024 * 1024)}MB"
            ),
            "hint": "" if not config.web_allow_private_addresses else
            "HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES=1 disables SSRF protection — keep off unless "
            "this machine is an isolated test/intranet box.",
        })
        if caps.get("termux") and config.web_dynamic_enabled:
            checks.append({
                "name": "web_termux",
                "ok": True,
                "level": "recommended",
                "detail": ("Android/Termux detected: browser strategies are restricted "
                           "(HERMUS_WEB_TERMUX_RESTRICT=1); fast HTTP fetching is used"),
                "hint": "Set HERMUS_WEB_TERMUX_RESTRICT=0 to allow browser strategies "
                        "(only if you have verified a browser actually runs here).",
            })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "name": "web_config", "ok": False, "level": "recommended",
            "detail": f"config unavailable: {type(exc).__name__}", "hint": "",
        })
    return checks
