"""A tiny stdlib HTTP client for the presence overlay API.

Used by ``hermus presence …`` and by the demo. ``urllib`` rather than
``requests``/``httpx`` on purpose: the overlay must work on a bare install where
the optional web clients were never added, and this surface is five localhost
JSON calls.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PresenceUnavailableError(RuntimeError):
    """The gateway is not answering — reported, never swallowed."""


class PresenceClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", token: str | None = None, timeout: float = 4.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or None
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing
    def _url(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        if self.token:
            join = "&" if "?" in url else "?"
            url = f"{url}{join}token={urllib.parse.quote(self.token)}"
        return url

    def _request(self, method: str, path: str, payload: Any = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - localhost only
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(detail)
            except ValueError:
                parsed = {"error": detail}
            return {"ok": False, "status": exc.code, **parsed}
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise PresenceUnavailableError(f"gateway not reachable at {self.base_url}: {exc}") from exc
        try:
            return json.loads(body or "{}")
        except ValueError:
            return {"ok": False, "error": "gateway returned non-JSON", "body": body[:200]}

    # ---------------------------------------------------------------- endpoints
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/presence/health")

    def snapshot(self) -> dict[str, Any]:
        return self._request("GET", "/api/presence/state")

    def set_state(self, state: str, ttl_ms: int | None = None, rationale: str = "") -> dict[str, Any]:
        return self._request("POST", "/api/presence/state", {"state": state, "ttl_ms": ttl_ms or 0, "rationale": rationale})

    def point(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        label: str = "",
        ttl_ms: int | None = None,
        space: str = "physical",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/presence/point",
            {"x": x, "y": y, "w": w, "h": h, "label": label, "ttl_ms": ttl_ms or 0, "space": space, "source": "cli"},
        )

    def say(self, text: str, ttl_ms: int | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/presence/say", {"text": text, "ttl_ms": ttl_ms or 0, "source": "cli"})

    def hide(self) -> dict[str, Any]:
        return self._request("POST", "/api/presence/hide")

    def show(self) -> dict[str, Any]:
        return self._request("POST", "/api/presence/show")

    def retract(self) -> dict[str, Any]:
        return self._request("POST", "/api/presence/retract")

    def displays(self, layout: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/presence/displays", layout)

    def reachable(self) -> bool:
        try:
            return bool(self.health().get("ok"))
        except PresenceUnavailableError:
            return False


def page_url(base_url: str = "http://127.0.0.1:8000", token: str | None = None) -> str:
    """The renderer URL the shell loads (token attached as a query string)."""
    url = f"{base_url.rstrip('/')}/presence/overlay"
    if token:
        url = f"{url}?token={urllib.parse.quote(token)}"
    return url


__all__ = ["PresenceClient", "PresenceUnavailableError", "page_url"]
