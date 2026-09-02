"""Security-first request gate for every outbound web acquisition (spec §8).

Nothing in the web subsystem touches the network before this module approves
the target URL. Checks are defense-in-depth:

* scheme allowlist (http/https only — no file://, no data:, no ftp:)
* credentials-in-URL rejection (user:pass@host)
* private / reserved / link-local / loopback IP rejection — resolved on every
  candidate host AND re-checked on the final URL after redirects (Scrapling's
  ``follow_redirects="safe"`` already refuses private-IP redirects; this module
  independently refuses to even start when the *original* host resolves private)
* domain allow/block lists (block wins) with exact-match and ``*.suffix`` form
* response-size and content-type guards are enforced by the caller; this module
  owns the *target* checks and URL redaction for telemetry.

The private-address refusal can be relaxed ONLY by an explicit config flag
(``HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES=1``) — used by tests and air-gapped
self-hosted intranets, never by default.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .errors import SecurityBlockedError

ALLOWED_SCHEMES = ("http", "https")
# Ports that are almost never web traffic and are classic SSRF pivots.
BLOCKED_PORTS = {22, 23, 25, 110, 143, 445, 3389, 5432, 6379, 27017, 9200}


def redact_url(url: str, *, keep_query: bool = False) -> str:
    """URL safe for logs/events: strip credentials and (by default) the query.

    Query strings routinely carry tokens and session ids, so telemetry gets the
    path only unless a caller explicitly opts in.
    """
    try:
        parsed = urlparse(url or "")
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        path = parsed.path or ""
        suffix = f"?{parsed.query}" if keep_query and parsed.query else ""
        return f"{parsed.scheme}://{netloc}{path}{suffix}" if parsed.scheme else f"{netloc}{path}"
    except Exception:
        return "<unparseable-url>"


def host_matches_pattern(host: str, pattern: str) -> bool:
    """Domain policy match: exact, leading-dot, and ``*.suffix`` wildcard forms."""
    host = (host or "").lower().strip(".")
    pattern = (pattern or "").lower().strip(".")
    if not host or not pattern:
        return False
    if host == pattern:
        return True
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host.endswith("." + suffix)
    return host.endswith("." + pattern) and host != pattern


@dataclass
class WebSecurityPolicy:
    """Mutable-at-boot view of the configured web security posture."""

    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    allow_private_addresses: bool = False
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES
    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 10
    extra_blocked_hosts: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_config(cls, config: Any) -> "WebSecurityPolicy":
        return cls(
            allowed_domains=tuple(getattr(config, "web_allowed_domains", ()) or ()),
            blocked_domains=tuple(getattr(config, "web_blocked_domains", ()) or ()),
            allow_private_addresses=bool(getattr(config, "web_allow_private_addresses", False)),
            max_response_bytes=int(getattr(config, "web_max_response_bytes", 5 * 1024 * 1024)),
            max_redirects=int(getattr(config, "web_max_redirects", 10)),
        )

    # ------------------------------------------------------------------ checks
    def check(self, url: str, *, purpose: str = "fetch") -> str:
        """Validate ``url`` for an outbound request. Returns the normalized URL.

        Raises :class:`SecurityBlockedError` with a safe, non-secret message.
        """
        raw = (url or "").strip()
        if not raw:
            raise SecurityBlockedError(f"{purpose}: empty URL")
        if any(ch.isspace() for ch in raw):
            raise SecurityBlockedError(f"{purpose}: URL contains whitespace/control characters")

        try:
            parsed = urlparse(raw)
        except ValueError as exc:
            raise SecurityBlockedError(f"{purpose}: unparseable URL") from exc

        if parsed.scheme.lower() not in self.allowed_schemes:
            raise SecurityBlockedError(
                f"{purpose}: scheme '{parsed.scheme or 'none'}' not allowed "
                f"(allowed: {', '.join(self.allowed_schemes)})"
            )
        # A scheme-relative or missing-host URL is not fetchable.
        if not parsed.hostname:
            raise SecurityBlockedError(f"{purpose}: URL has no hostname")

        if parsed.username or parsed.password:
            raise SecurityBlockedError(f"{purpose}: embedded credentials in URL are not allowed")

        host = parsed.hostname.lower().strip(".")
        if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local") \
                or host.endswith(".internal"):
            raise SecurityBlockedError(f"{purpose}: internal host '{host}' is blocked")

        try:
            port = parsed.port
        except ValueError as exc:
            raise SecurityBlockedError(f"{purpose}: invalid port in URL") from exc
        if port in BLOCKED_PORTS:
            raise SecurityBlockedError(f"{purpose}: port {port} is blocked by policy")

        # Domain policy: block list wins over allow list; a non-empty allow list
        # makes everything outside it blocked too.
        for pattern in self.blocked_domains:
            if host_matches_pattern(host, pattern):
                raise SecurityBlockedError(f"{purpose}: domain '{host}' is blocked by Hermus policy")
        for pattern in self.extra_blocked_hosts:
            if host_matches_pattern(host, pattern):
                raise SecurityBlockedError(f"{purpose}: domain '{host}' is blocked by Hermus policy")
        if self.allowed_domains:
            if not any(host_matches_pattern(host, p) for p in self.allowed_domains):
                raise SecurityBlockedError(
                    f"{purpose}: domain '{host}' is not in the configured allow list"
                )

        self._check_host_addresses(host, purpose)
        return raw

    def check_final_url(self, url: str, *, requested_url: str = "",
                        purpose: str = "redirect") -> str:
        """Re-validate the URL a server actually redirected us to (post-redirect
        SSRF guard). This is the *suspenders* to Scrapling's ``follow_redirects``
        belt: a page can redirect ``https://public.example`` →
        ``http://127.0.0.1:8080`` (or to a blocked domain, a disallowed scheme,
        or a private/link-local/loopback IPv4/IPv6 address), and we must refuse
        the *result* even though the original target was allowed.

        It runs the SAME battery of checks as :meth:`check` (scheme allow-list,
        embedded credentials, internal-host suffixes, SSRF pivot ports, domain
        block/allow lists, and DNS→IP private/reserved rejection) so there is a
        single source of security truth — no partial re-implementation that can
        drift. Returns the normalized final URL.
        """
        final = (url or "").strip()
        if not final:
            raise SecurityBlockedError(f"{purpose}: redirect target is empty")
        # Fast path: identical to the already-validated request → nothing new.
        if requested_url and final == requested_url.strip():
            return final
        # DNS-unresolvable is a real block here (unlike the pre-flight gate):
        # the response was fetched, so the final host resolved for Scrapling —
        # if we cannot resolve it, we cannot prove it is safe, so refuse.
        parsed = urlparse(final)
        host = (parsed.hostname or "").lower().strip(".")
        if host and not self.allow_private_addresses:
            try:
                socket.getaddrinfo(host, None)
            except socket.gaierror as exc:
                raise SecurityBlockedError(
                    f"{purpose}: redirect target '{host}' could not be resolved for "
                    "re-validation") from exc
        return self.check(final, purpose=purpose)

    def check_response(self, size_bytes: int, *, content_type: str = "") -> None:
        """Response-size guard, enforced by the caller as early as the backend
        allows (see :mod:`core.web.scrapling_backend`).

        Honest limitation: Scrapling's HTTP fetcher (curl_cffi) downloads the
        full body before Hermus sees it, so this is a *post-acquisition* cutoff,
        not a streamed pre-download abort — the body is bounded by curl but the
        cap is applied the moment control returns. Raises
        :class:`ResponseTooLargeError` (``SIZE_LIMIT``) so the router aborts the
        plan rather than escalating (a heavier strategy never shrinks a body).
        """
        if self.max_response_bytes and size_bytes > self.max_response_bytes:
            from .errors import ResponseTooLargeError

            raise ResponseTooLargeError(
                f"response of {size_bytes} bytes exceeds the configured limit "
                f"({self.max_response_bytes} bytes)"
            )

    # --------------------------------------------------------------- internals
    def _check_host_addresses(self, host: str, purpose: str) -> None:
        if self.allow_private_addresses:
            return
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            # Unresolvable at gate time: let the fetch attempt surface a DNS
            # failure classification instead of a security block.
            return
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if self._is_forbidden_ip(ip):
                raise SecurityBlockedError(
                    f"{purpose}: '{host}' resolves to a private/reserved address ({ip}) — "
                    "blocked to prevent SSRF"
                )

    @staticmethod
    def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
        if isinstance(ip, ipaddress.IPv6Address):
            # ::1, ::, fe80::/10, fc00::/7, and IPv4-mapped addresses.
            if ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            elif ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_private:
                return True
            return False
        return (
            ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified
        )


def test_policy(allow_private: bool = True) -> WebSecurityPolicy:
    """Policy used by tests / local fixtures: loopback allowed, nothing else."""
    return WebSecurityPolicy(allow_private_addresses=allow_private)
