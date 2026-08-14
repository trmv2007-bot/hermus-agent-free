"""Search the public-apis/public-apis catalog from inside Hermus.

The upstream project is a community-maintained directory, not an API proxy:
its URLs usually point to documentation and are not necessarily callable API
endpoints.  This module therefore focuses on discovery.  Once a user has read
the provider's docs and selected an endpoint, Hermus' ``api add`` command can
register it as an executable custom tool.

A bundled snapshot keeps discovery fast and available offline.  Users can
explicitly refresh a runtime cache from GitHub without changing tracked files.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.request import Request, urlopen

from core.config import config

SOURCE_REPO = "https://github.com/public-apis/public-apis"
SOURCE_README = (
    "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
)
SOURCE_CONTENTS_API = (
    "https://api.github.com/repos/public-apis/public-apis/contents/README.md?ref=master"
)
BUNDLED_CATALOG = config.base_dir / "resources" / "public_apis_catalog.json"
RUNTIME_CACHE = config.resolve_path("data/public_apis_catalog_cache.json")

_LINK_RE = re.compile(r"^\[([^\]]+)\]\((.+)\)$")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _clean_cell(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_public_apis_markdown(markdown: str) -> List[Dict[str, str]]:
    """Parse the five-column category tables in the upstream README.

    The parser intentionally ignores promotional tables and malformed rows.
    Some upstream rows contain an extra empty trailing cell; taking the first
    five cells makes those rows harmless while preserving the catalog data.
    """
    category = ""
    entries: List[Dict[str, str]] = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            category = line[4:].strip()
            continue
        if not category or not line.startswith("| ["):
            continue

        cells = [part.strip() for part in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        link_cell, description, auth, https, cors = cells[:5]
        match = _LINK_RE.match(link_cell)
        if not match:
            continue

        name, url = match.groups()
        auth = _clean_cell(auth)
        https = _clean_cell(https).title()
        cors = _clean_cell(cors).title()
        if https not in {"Yes", "No", "Unknown"}:
            continue
        if cors not in {"Yes", "No", "Unknown"}:
            cors = "Unknown"

        entries.append(
            {
                "name": name.strip(),
                "description": description.strip(),
                "auth": auth or "Unknown",
                "https": https,
                "cors": cors,
                "category": category,
                "documentation_url": url.strip(),
            }
        )

    return entries


def build_catalog_payload(
    entries: Iterable[Dict[str, str]],
    *,
    source_commit: str = "",
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    apis = list(entries)
    categories = sorted({item["category"] for item in apis}, key=str.casefold)
    return {
        "schema_version": 1,
        "source": SOURCE_REPO,
        "source_readme": SOURCE_README,
        "source_commit": source_commit,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "license": "MIT; Copyright (c) 2022 public-apis",
        "api_count": len(apis),
        "categories": categories,
        "apis": apis,
    }


def _valid_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and isinstance(payload.get("apis"), list)
        and len(payload["apis"]) > 0
    )


class PublicAPICatalog:
    """Offline-first catalog with optional, explicit GitHub refresh."""

    def __init__(
        self,
        bundled_path: Optional[Path] = None,
        cache_path: Optional[Path] = None,
        source_url: str = SOURCE_README,
    ):
        self.bundled_path = Path(bundled_path or BUNDLED_CATALOG)
        self.cache_path = Path(cache_path or RUNTIME_CACHE)
        self.source_url = source_url
        self._payload: Optional[Dict[str, Any]] = None
        self._loaded_from = ""

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if _valid_payload(payload) else None
        except (OSError, ValueError, TypeError):
            return None

    def load(self, prefer_cache: bool = True) -> Dict[str, Any]:
        if self._payload is not None:
            return self._payload

        candidates = (
            [(self.cache_path, "runtime_cache"), (self.bundled_path, "bundled_snapshot")]
            if prefer_cache
            else [(self.bundled_path, "bundled_snapshot"), (self.cache_path, "runtime_cache")]
        )
        for path, label in candidates:
            payload = self._read_json(path)
            if payload:
                self._payload = payload
                self._loaded_from = label
                return payload

        # A source checkout may not include generated resources.  Return a
        # clear empty payload rather than failing module import/tool loading.
        self._payload = build_catalog_payload([])
        self._loaded_from = "empty"
        return self._payload

    def refresh(self, timeout: int = 20) -> Dict[str, Any]:
        """Fetch and parse upstream README, then atomically update local cache."""
        try:
            sources = [(self.source_url, "text/plain")]
            if self.source_url == SOURCE_README:
                # raw.githubusercontent.com is blocked on some networks.  The
                # GitHub contents API can return the same file as raw text.
                sources.append((SOURCE_CONTENTS_API, "application/vnd.github.raw+json"))

            entries: List[Dict[str, str]] = []
            download_errors = []
            for url, accept in sources:
                try:
                    request = Request(
                        url,
                        headers={
                            "Accept": accept,
                            "User-Agent": "Hermus-Agent-Free public-api-catalog",
                        },
                    )
                    with urlopen(
                        request, timeout=max(1, min(int(timeout), 60))
                    ) as response:
                        # The upstream README is currently well below 1 MB.
                        # Bound the download so a bad endpoint cannot fill memory.
                        raw = response.read(5_000_001)
                        if len(raw) > 5_000_000:
                            raise ValueError(
                                "Catalog response exceeded the 5 MB safety limit"
                            )
                        charset = response.headers.get_content_charset() or "utf-8"
                        markdown = raw.decode(charset, errors="replace")
                    candidate_entries = parse_public_apis_markdown(markdown)
                    # A tiny result usually means GitHub returned an error or
                    # the upstream format changed. Try the alternate source.
                    if len(candidate_entries) < 100:
                        raise ValueError(
                            f"Only parsed {len(candidate_entries)} entries"
                        )
                    entries = candidate_entries
                    break
                except Exception as download_exc:
                    download_errors.append(f"{url}: {download_exc}")
            if not entries:
                raise RuntimeError(
                    "; ".join(download_errors)
                    + "; refusing to replace the existing catalog"
                )

            payload = build_catalog_payload(entries)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{self.cache_path.name}.",
                dir=str(self.cache_path.parent),
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp_name, self.cache_path)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass

            self._payload = payload
            self._loaded_from = "runtime_cache"
            return {
                "success": True,
                "count": len(entries),
                "categories": len(payload["categories"]),
                "cache_path": str(self.cache_path),
                "source": SOURCE_REPO,
                "generated_at": payload["generated_at"],
            }
        except Exception as exc:
            fallback = self.load()
            return {
                "success": False,
                "error": str(exc),
                "using_fallback": self._loaded_from,
                "fallback_count": len(fallback.get("apis", [])),
                "source": SOURCE_REPO,
            }

    @staticmethod
    def _auth_matches(value: str, requested: str) -> bool:
        requested = requested.strip().casefold()
        value = value.strip().casefold()
        if requested in {"", "any", "all"}:
            return True
        if requested in {"none", "no", "no_auth", "no-auth"}:
            return value == "no"
        return requested == value

    @staticmethod
    def _choice_matches(value: str, requested: str) -> bool:
        requested = requested.strip().casefold()
        return requested in {"", "any", "all"} or value.casefold() == requested

    @staticmethod
    def _score(item: Dict[str, str], query: str, terms: List[str]) -> int:
        if not terms:
            return 0
        name = item["name"].casefold()
        description = item["description"].casefold()
        category = item["category"].casefold()
        combined = f"{name} {description} {category}"
        q = query.casefold().strip()
        score = 0
        if q == name:
            score += 200
        elif q in name:
            score += 80
        if q == category:
            score += 70
        elif q and q in category:
            score += 25
        if q and q in description:
            score += 20
        for term in terms:
            if term in name:
                score += 15
            if term in category:
                score += 8
            if term in description:
                score += 4
            if term not in combined:
                score -= 2
        return score

    def search(
        self,
        query: str = "",
        category: str = "",
        auth: str = "any",
        https_only: bool = True,
        cors: str = "any",
        limit: int = 10,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        refresh_result = self.refresh() if refresh else None
        payload = self.load()
        limit = max(1, min(int(limit), 50))
        query = (query or "").strip()
        terms = _WORD_RE.findall(query.casefold())
        requested_category = (category or "").strip().casefold()

        ranked = []
        for index, item in enumerate(payload.get("apis", [])):
            if requested_category and requested_category not in item["category"].casefold():
                continue
            if not self._auth_matches(item.get("auth", "Unknown"), auth or "any"):
                continue
            if https_only and item.get("https") != "Yes":
                continue
            if not self._choice_matches(item.get("cors", "Unknown"), cors or "any"):
                continue
            score = self._score(item, query, terms)
            if terms and score <= 0:
                continue
            ranked.append((score, index, item))

        ranked.sort(key=lambda row: (-row[0], row[2]["name"].casefold(), row[1]))
        results = []
        for score, _index, item in ranked[:limit]:
            result = dict(item)
            result["relevance"] = score
            result["usage_note"] = (
                "Documentation URL only. Verify the provider's terms and endpoint "
                "before registering it with `hermus api add`."
            )
            results.append(result)

        response: Dict[str, Any] = {
            "success": True,
            "query": query,
            "filters": {
                "category": category or "any",
                "auth": auth or "any",
                "https_only": bool(https_only),
                "cors": cors or "any",
            },
            "count": len(results),
            "total_matched": len(ranked),
            "results": results,
            "catalog": {
                "loaded_from": self._loaded_from,
                "total_apis": len(payload.get("apis", [])),
                "generated_at": payload.get("generated_at"),
                "source_commit": payload.get("source_commit", ""),
                "source": SOURCE_REPO,
                "license": payload.get("license"),
            },
        }
        if refresh_result is not None:
            response["refresh"] = refresh_result
        return response

    def categories(self) -> Dict[str, Any]:
        payload = self.load()
        counts: Dict[str, Dict[str, int]] = {}
        for item in payload.get("apis", []):
            stats = counts.setdefault(
                item["category"],
                {"total": 0, "no_auth": 0, "https": 0, "cors_yes": 0},
            )
            stats["total"] += 1
            stats["no_auth"] += int(item.get("auth", "").casefold() == "no")
            stats["https"] += int(item.get("https") == "Yes")
            stats["cors_yes"] += int(item.get("cors") == "Yes")

        categories = [
            {"category": name, **stats}
            for name, stats in sorted(counts.items(), key=lambda pair: pair[0].casefold())
        ]
        return {
            "success": True,
            "count": len(categories),
            "categories": categories,
            "catalog": {
                "loaded_from": self._loaded_from,
                "total_apis": len(payload.get("apis", [])),
                "generated_at": payload.get("generated_at"),
                "source": SOURCE_REPO,
            },
        }


public_api_catalog = PublicAPICatalog()


def public_api_search(
    query: str = "",
    category: str = "",
    auth: str = "any",
    https_only: bool = True,
    cors: str = "any",
    limit: int = 10,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Find APIs by task/name and free-use metadata."""
    return public_api_catalog.search(
        query=query,
        category=category,
        auth=auth,
        https_only=https_only,
        cors=cors,
        limit=limit,
        refresh=refresh,
    )


def public_api_categories() -> Dict[str, Any]:
    """List catalog categories and useful free/HTTPS/CORS counts."""
    return public_api_catalog.categories()


def public_api_refresh(timeout: int = 20) -> Dict[str, Any]:
    """Refresh the untracked runtime catalog from GitHub."""
    return public_api_catalog.refresh(timeout=timeout)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "public_api_search",
            "description": (
                "Discover public APIs from the public-apis/public-apis catalog. "
                "Filter for no-auth, HTTPS, CORS, or category. Results are documentation "
                "links, not automatically trusted/callable endpoints."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Task, API name, or keywords, e.g. weather or threat intelligence",
                        "default": "",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter, e.g. Security or Weather",
                        "default": "",
                    },
                    "auth": {
                        "type": "string",
                        "description": "any, No/no_auth, apiKey, OAuth, or another exact catalog auth value",
                        "default": "any",
                    },
                    "https_only": {
                        "type": "boolean",
                        "description": "Exclude APIs without confirmed HTTPS",
                        "default": True,
                    },
                    "cors": {
                        "type": "string",
                        "enum": ["any", "Yes", "No", "Unknown"],
                        "default": "any",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Fetch latest upstream README before searching",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "public_api_categories",
            "description": "List public API categories with total, no-auth, HTTPS, and CORS-supported counts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "public_api_refresh",
            "description": "Refresh Hermus' runtime public API catalog from the public-apis GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 20,
                    }
                },
                "required": [],
            },
        },
    },
]

TOOL_MAP = {
    "public_api_search": public_api_search,
    "public_api_categories": public_api_categories,
    "public_api_refresh": public_api_refresh,
}
