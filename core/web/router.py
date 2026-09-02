"""Web acquisition strategy router — cheap-first with autonomous escalation.

Decision policy (spec §5/§6), cheapest-sufficient-first:

1. STATIC  — Scrapling Fetcher: fast HTTP with a browser TLS fingerprint.
2. DYNAMIC — Scrapling DynamicFetcher: only when static output is insufficient
   (JS-only shell, challenge marker, tiny text) or the caller requires JS.
3. STEALTH — Scrapling StealthyFetcher: only when (a) enabled in config,
   (b) lower strategies genuinely failed, and (c) the target passed security
   review. Never attempted blindly against every site.

``AUTO`` builds a plan, executes it in order, records every attempt (strategy,
status, latency, failure class, escalation reason) on the resulting WebResult,
and never retries the exact same failing strategy with the same arguments.
Forced strategies run alone unless ``allow_fallback`` is set. Security refusals
are never escalated around — they abort the whole plan.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from . import capabilities
from .errors import (
    SecurityBlockedError,
    StrategyUnavailableError,
)
from .models import (
    FailureClass,
    FetchStrategy,
    StrategyAttempt,
    WebResult,
)
from .normalizer import build_web_result, error_result
from .scrapling_backend import RawFetch, backend
from .security import WebSecurityPolicy

# Markers that a static response is a JS shell / anti-bot challenge.
_JS_SHELL_MARKERS = (
    re.compile(r"<div[^>]+id=[\"'](?:root|app|__next|q-app)[\"'][^>]*>\s*</div>", re.I),
    re.compile(r"<noscript>[^<]*enable\s+javascript", re.I),
)
_CHALLENGE_MARKERS = (
    re.compile(r"just a moment", re.I),
    re.compile(r"cf-browser-verification|cf_chl_|cloudflare", re.I),
    re.compile(r"captcha|are you a robot|verify you are human", re.I),
    re.compile(r"attention required", re.I),
)
_MIN_MEANINGFUL_TEXT = 120
_BIG_HTML_WITHOUT_TEXT = 4000


def _page_html(response: Any) -> str:
    try:
        return response.html_content or ""
    except Exception:
        return ""


def _page_text(response: Any) -> str:
    try:
        return (response.get_all_text() or "").strip()
    except Exception:
        return ""


def _looks_like_js_shell(response: Any) -> bool:
    html = _page_html(response)
    if any(p.search(html) for p in _JS_SHELL_MARKERS):
        return True
    # A big HTML with ~no text is a strong signal the content is JS-rendered.
    return len(html) > _BIG_HTML_WITHOUT_TEXT


def _looks_challenged(response: Any) -> bool:
    html = _page_html(response)[:8000]
    text = _page_text(response)[:4000]
    return any(p.search(html) or p.search(text) for p in _CHALLENGE_MARKERS)


def _is_insufficient(response: Any, status: Optional[int]) -> bool:
    """Heuristic: does this response carry meaningful content?"""
    if status and status >= 400:
        return True
    if response is None:
        return True
    text = _page_text(response)
    if len(text) >= _MIN_MEANINGFUL_TEXT:
        return False
    return _looks_like_js_shell(response)


class StrategyRouter:
    """Selects and executes acquisition strategies with escalation tracking."""

    def __init__(self, config: Any, policy: WebSecurityPolicy):
        self._config = config
        self._policy = policy

    # ------------------------------------------------------------------ plan
    def plan(self, requested: str = FetchStrategy.AUTO.value, *,
             require_js: bool = False) -> list[FetchStrategy]:
        """Ordered strategy plan honoring config + capability, cheapest first."""
        cfg = self._config
        requested = (requested or FetchStrategy.AUTO.value).lower()
        on_termux = capabilities.is_termux()
        dynamic_allowed = bool(getattr(cfg, "web_dynamic_enabled", True)) and not (
            bool(getattr(cfg, "web_termux_restrict", True)) and on_termux
        )
        stealth_allowed = bool(getattr(cfg, "web_stealth_enabled", False)) and not (
            bool(getattr(cfg, "web_termux_restrict", True)) and on_termux
        )

        if requested == FetchStrategy.STEALTH.value:
            plan = [FetchStrategy.STEALTH] if stealth_allowed else []
        elif requested == FetchStrategy.DYNAMIC.value:
            plan = [FetchStrategy.DYNAMIC] if dynamic_allowed else []
        elif requested == FetchStrategy.STATIC.value:
            plan = [FetchStrategy.STATIC]
        else:  # AUTO
            plan = [FetchStrategy.DYNAMIC] if (require_js and dynamic_allowed) \
                else [FetchStrategy.STATIC]
            if dynamic_allowed and FetchStrategy.DYNAMIC not in plan:
                plan.append(FetchStrategy.DYNAMIC)
            if stealth_allowed:
                plan.append(FetchStrategy.STEALTH)

        # Keep only strategies whose backing stack is installed/usable here.
        usable: list[FetchStrategy] = []
        for strat in plan:
            status = capabilities.probe().get(strat.value, {}).get("status")
            if status in (capabilities.AVAILABLE, capabilities.NOT_VERIFIED):
                usable.append(strat)
        return usable

    # --------------------------------------------------------------- execute
    def fetch(self, url: str, *, strategy: str = FetchStrategy.AUTO.value,
              require_js: bool = False, want_markdown: bool = True,
              include_html: bool = False, session: Optional[Any] = None,
              session_name: str = "", allow_fallback: bool = True,
              capture_xhr: Optional[str] = None,
              wait_selector: Optional[str] = None,
              solve_cloudflare: bool = False) -> WebResult:
        """Run the plan with escalation; returns a WebResult on every path."""
        started = time.monotonic()
        attempts: list[StrategyAttempt] = []
        plan = self.plan(strategy, require_js=require_js)
        if not plan:
            return error_result(
                url, strategy=strategy,
                error="no acquisition strategy is available on this machine "
                      "(install: pip install 'scrapling[fetchers]')",
                error_code="WEB_NO_STRATEGY",
                failure_class=FailureClass.DEPENDENCY_MISSING.value,
            )

        forced = requested_strategy(strategy) is not None
        insufficient = False
        for index, strat in enumerate(plan):
            try:
                raw = self._run_strategy(
                    strat, url, session=session,
                    capture_xhr=capture_xhr if strat == FetchStrategy.DYNAMIC else None,
                    wait_selector=wait_selector, solve_cloudflare=solve_cloudflare,
                )
                result = self._evaluate(strat, url, raw, want_markdown=want_markdown,
                                        include_html=include_html, session_name=session_name)
                attempts.extend(result.attempts)
                if result.ok and not _is_insufficient(raw.response, raw.status):
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    result.attempts = attempts
                    capabilities.mark_verified(strat.value)
                    return result
                insufficient = True
                last_reason = attempts[-1].reason if attempts else "content insufficient"
            except SecurityBlockedError as exc:
                attempts.append(StrategyAttempt(
                    strategy=strat.value, outcome="error",
                    error_class=exc.failure_class.value, error=str(exc), reason="blocked",
                ))
                return error_result(
                    url, strategy=strategy, error=str(exc),
                    error_code=exc.error_code,
                    failure_class=exc.failure_class.value, attempts=attempts,
                )
            except StrategyUnavailableError as exc:
                attempts.append(StrategyAttempt(
                    strategy=strat.value, outcome="error",
                    error_class=exc.failure_class.value, error=str(exc), reason="unavailable",
                ))
                last_reason = f"{strat.value} unavailable"
                continue
            except Exception as exc:  # typed WebAcquisitionError from the backend
                failure_class = getattr(exc, "failure_class", FailureClass.UNKNOWN)
                attempts.append(StrategyAttempt(
                    strategy=strat.value, outcome="error",
                    error_class=failure_class.value, error=str(exc),
                    reason="transport failure",
                ))
                last_reason = "transport failure"

            # Decide whether to escalate to the next strategy at all.
            next_index = index + 1
            if next_index >= len(plan):
                break
            if forced and not allow_fallback:
                break
        else:
            pass  # plan exhausted

        last = attempts[-1] if attempts else StrategyAttempt(
            strategy=strategy, outcome="error", error_class=FailureClass.UNKNOWN.value)
        if last.error:
            message = last.error
        elif last.reason:
            message = f"acquisition failed: {last.reason}"
        else:
            message = ("last strategy returned insufficient content" if insufficient
                       else "all strategies failed")
        failed = error_result(
            url, strategy=strategy,
            error=message,
            error_code="WEB_ALL_STRATEGIES_FAILED",
            failure_class=last.error_class or FailureClass.UNKNOWN.value,
            attempts=attempts,
            status_code=last.status_code,
        )
        if insufficient and last.status_code and last.status_code < 400:
            failed.warnings.append(
                f"content fetched but considered insufficient ({last_reason}); "
                "no further strategy was permitted or available"
            )
        failed.duration_ms = int((time.monotonic() - started) * 1000)
        return failed

    # -------------------------------------------------------------- internals
    def _run_strategy(self, strat: FetchStrategy, url: str, *, session: Optional[Any],
                      capture_xhr: Optional[str], wait_selector: Optional[str],
                      solve_cloudflare: bool) -> RawFetch:
        cfg = self._config
        common = {"policy": self._policy, "session": session}
        if strat == FetchStrategy.STATIC:
            return backend.fetch_static(
                url, timeout=float(getattr(cfg, "web_request_timeout", 20.0)),
                stealthy_headers=True, **common,
            )
        if strat == FetchStrategy.DYNAMIC:
            return backend.fetch_dynamic(
                url, timeout=float(getattr(cfg, "web_browser_timeout", 45.0)),
                capture_xhr=capture_xhr, wait_selector=wait_selector,
                network_idle=True, **common,
            )
        return backend.fetch_stealth(
            url, timeout=float(getattr(cfg, "web_browser_timeout", 60.0)),
            solve_cloudflare=solve_cloudflare and bool(
                getattr(cfg, "web_stealth_solve_cloudflare", False)),
            **common,
        )

    def _evaluate(self, strat: FetchStrategy, url: str, raw: RawFetch, **kw: Any) -> WebResult:
        """Normalize a transport-level success into a WebResult + attempt record."""
        result = build_web_result(raw, url=url, strategy=strat.value, policy=self._policy, **kw)
        status = raw.status or 0
        insufficient_reason = ""
        if status >= 400:
            challenged = _looks_challenged(raw.response)
            result.failure_class = (FailureClass.CHALLENGE if challenged
                                    else FailureClass.HTTP_STATUS).value
            insufficient_reason = "anti-bot challenge page" if challenged else f"HTTP {status}"
        elif _is_insufficient(raw.response, raw.status):
            shell = _looks_like_js_shell(raw.response)
            result.failure_class = (FailureClass.JS_REQUIRED if shell
                                    else FailureClass.EMPTY_CONTENT).value
            insufficient_reason = ("page is a JS shell; content renders client-side" if shell
                                   else "no meaningful text in response")
        result.attempts = [StrategyAttempt(
            strategy=strat.value,
            outcome="insufficient" if insufficient_reason else "success",
            status_code=status or None, duration_ms=raw.duration_ms,
            error_class=result.failure_class, reason=insufficient_reason,
        )]
        return result


def requested_strategy(strategy: str) -> Optional[FetchStrategy]:
    """The explicit strategy a caller demanded, or None for AUTO."""
    try:
        strat = FetchStrategy((strategy or FetchStrategy.AUTO.value).lower())
    except ValueError:
        return None
    return None if strat == FetchStrategy.AUTO else strat


__all__ = ["StrategyRouter", "requested_strategy"]
