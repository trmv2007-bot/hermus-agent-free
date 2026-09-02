"""Honest capability detection for the web acquisition subsystem (spec §20/§21).

The rest of Hermus (router, doctor, tools) must know what can actually run on
this machine — not what imports cleanly. Four states, deliberately distinct:

* ``available``      — import + backing runtime both present (browser binaries
                       verified on disk, or a live fetch already succeeded).
* ``unavailable``    — importable but the backing runtime is broken/missing.
* ``not_installed``  — the optional dependency itself is absent.
* ``not_verified``   — everything looks present, but nothing has proven it works
                       in this process (e.g. a browser binary exists but no real
                       page was fetched yet).

Android/Termux is detected explicitly: the full browser stack is reported as
``not_verified`` there until a real fetch succeeds, and config keeps it
disabled by default — we do not claim Android browser support without testing.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Statuses (exposed verbatim to Doctor — see spec §21 vocabulary).
AVAILABLE = "available"
UNAVAILABLE = "unavailable"
NOT_INSTALLED = "not_installed"
NOT_VERIFIED = "not_verified"

_CACHE_TTL = 30.0
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_at = 0.0
# Flips to True per-strategy after one real in-process fetch succeeds.
_verified: dict[str, bool] = {"static": False, "dynamic": False, "stealth": False}


def is_termux() -> bool:
    """True when running under Android/Termux."""
    if os.environ.get("TERMUX_VERSION"):
        return True
    if "android" in platform.system().lower():
        return True
    try:
        if "com.termux" in os.environ.get("PATH", ""):
            return True
    except Exception:
        pass
    return False


def scrapling_version() -> Optional[str]:
    try:
        return importlib.metadata.version("scrapling")
    except Exception:
        return None


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _playwright_chromium_path() -> Optional[Path]:
    """Locate the Playwright chromium binary WITHOUT launching the driver.

    Playwright installs browsers under a browsers root (``$PLAYWRIGHT_BROWSERS_PATH``
    or the per-OS default cache dir) as ``chromium-<build>/<bin-dir>/chrome``.
    Scanning keeps the probe cheap, side-effect-free and safe to call from the
    doctor — launching a browser to prove it exists would defeat the point.
    """
    roots: list[Path] = []
    env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_root:
        roots.append(Path(env_root))
    home = Path.home()
    if platform.system() == "Linux":
        roots.append(home / ".cache" / "ms-playwright")
    elif platform.system() == "Darwin":
        roots.append(home / "Library" / "Caches" / "ms-playwright")
    elif platform.system() == "Windows":
        roots.append(home / "AppData" / "Local" / "ms-playwright")

    exe_names = ("chrome",) if platform.system() != "Windows" else ("chrome.exe",)
    subdirs = ("chrome-linux64", "chrome-linux", "chrome-win64", "chrome-win",
               "chrome-mac", "chrome-mac-arm64") if platform.system() != "Windows" \
        else ("chrome-win64", "chrome-win")
    for root in roots:
        if not root.is_dir():
            continue
        for build in sorted(root.glob("chromium-*"), reverse=True):
            for sub in subdirs:
                for name in exe_names:
                    candidate = build / sub / name
                    if candidate.exists():
                        return candidate
    return None


def _browser_status() -> tuple[str, str]:
    """(status, detail) for the shared browser runtime behind dynamic/stealth."""
    if not _importable("playwright"):
        return NOT_INSTALLED, "playwright package not installed (pip install playwright)"
    exe = _playwright_chromium_path()
    if exe is None:
        return UNAVAILABLE, (
            "playwright installed but the chromium browser binary is missing — "
            "run: scrapling install  (or: playwright install chromium)"
        )
    return NOT_VERIFIED, f"chromium binary present ({exe.name}) but not exercised in this process"


def probe(*, force: bool = False) -> dict[str, Any]:
    """Probe the acquisition stack. Cached briefly; ``force=True`` re-probes."""
    global _cache, _cache_at
    with _lock:
        if not force and _cache and (time.time() - _cache_at) < _CACHE_TTL:
            return dict(_cache)

        caps: dict[str, Any] = {}
        caps["platform"] = platform.system()
        caps["termux"] = is_termux()

        version = scrapling_version()
        caps["scrapling_version"] = version
        parser_ok = _importable("scrapling") and _importable("scrapling.parser")
        caps["parser"] = (
            {"status": AVAILABLE, "detail": f"scrapling {version}"} if parser_ok and version
            else {"status": NOT_INSTALLED, "detail": "scrapling not installed — "
                   "pip install 'scrapling[fetchers]'"}
        )

        fetchers_ok = parser_ok and _importable("scrapling.fetchers") and _importable("curl_cffi")
        if not fetchers_ok:
            caps["static"] = {
                "status": NOT_INSTALLED if not parser_ok else UNAVAILABLE,
                "detail": "scrapling fetchers extra missing — pip install 'scrapling[fetchers]'",
            }
        elif _verified.get("static"):
            caps["static"] = {"status": AVAILABLE, "detail": "verified by a live fetch this process"}
        else:
            caps["static"] = {"status": NOT_VERIFIED,
                              "detail": "scrapling fetchers importable; no live fetch yet this process"}

        browser_status, browser_detail = _browser_status()
        for strat, label in (("dynamic", "DynamicFetcher (Playwright Chromium)"),
                             ("stealth", "StealthyFetcher (hardened Chromium)")):
            if not fetchers_ok:
                caps[strat] = dict(caps["static"])
            elif _verified.get(strat):
                caps[strat] = {"status": AVAILABLE,
                               "detail": f"{label}: verified by a live fetch this process"}
            else:
                caps[strat] = {"status": browser_status,
                               "detail": f"{label}: {browser_detail}"}

        # Markdown conversion needs the optional `markdownify` dependency.
        caps["markdown"] = (
            {"status": AVAILABLE, "detail": "markdownify present"}
            if _importable("markdownify")
            else {"status": NOT_INSTALLED,
                  "detail": "pip install 'scrapling[ai]' for markdown extraction (text still works)"}
        )

        # Session persistence — reported honestly per fetcher (spec §12).
        # Static: Scrapling's FetcherSession keeps a live curl_cffi client whose
        #   cookie jar survives across fetches → Hermus reuses it (persistent).
        # Dynamic/stealth: Hermus does NOT hold a live browser context across
        #   calls (each browser fetch uses a one-off fetcher — see
        #   scrapling_backend._scrapling_session), so cross-fetch persistence is
        #   NOT claimed; the underlying Scrapling DynamicSession/StealthySession
        #   exist but are not wired for reuse here. Do not fake persistence.
        static_persist = caps["static"]["status"] in (AVAILABLE, NOT_VERIFIED)
        caps["static_session_persistence"] = {
            "status": AVAILABLE if static_persist else caps["static"]["status"],
            "detail": ("FetcherSession keeps a live client + cookie jar across fetches"
                       if static_persist else "static fetcher unavailable"),
        }
        caps["dynamic_session_persistence"] = {
            "status": UNAVAILABLE,
            "detail": ("browser sessions use one-off fetchers per call — persistent "
                       "cross-fetch browser contexts are not implemented (no fake "
                       "persistence claimed)"),
        }

        caps["verified"] = dict(_verified)
        _cache = caps
        _cache_at = time.time()
        return dict(_cache)


def mark_verified(strategy: str) -> None:
    """Record that ``strategy`` completed one REAL fetch in this process."""
    if strategy in _verified:
        with _lock:
            _verified[strategy] = True
            _cache.clear()


def strategy_ready(strategy: str) -> bool:
    """True only when the strategy's status is ``available`` or ``not_verified``
    (i.e. installed enough to attempt). ``unavailable``/``not_installed`` refuse."""
    status = probe().get(strategy, {}).get("status")
    return status in (AVAILABLE, NOT_VERIFIED)


def status_summary() -> dict[str, str]:
    """Flat strategy → status map for tools / doctor / the agent."""
    caps = probe()
    return {
        "parser": caps["parser"]["status"],
        "static": caps["static"]["status"],
        "dynamic": caps["dynamic"]["status"],
        "stealth": caps["stealth"]["status"],
        "markdown": caps["markdown"]["status"],
    }
