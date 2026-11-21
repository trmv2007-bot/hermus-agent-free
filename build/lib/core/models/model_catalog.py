"""Dynamic model catalog for the HERMUS control room.

The catalog is discovery-first: providers expose the model IDs they actually
serve, HERMUS attaches capability evidence to those deployments, and the
dashboard consumes this runtime catalog. Provider presets remain fallbacks for
bootstrapping, but they are never the source of selectable UI model choices.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..model_capabilities import negotiate
from ..openai_compat import list_models
from ..provider_resolver import discover_runtime_bundles
from ..providers import get_provider


class ModelCatalog:
    """One cached, secret-free inventory of selectable model deployments."""

    def __init__(self, ttl_seconds: float = 45.0) -> None:
        self.ttl_seconds = max(5.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0

    def _cache_valid(self) -> bool:
        return self._cache is not None and (time.time() - self._cached_at) < self.ttl_seconds

    @staticmethod
    def _normalize_model(provider: str, raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, str):
            model_id = raw.strip()
            meta: dict[str, Any] = {}
        elif isinstance(raw, dict):
            model_id = str(raw.get("id") or raw.get("name") or "").strip()
            meta = dict(raw)
        else:
            return None
        if not model_id:
            return None
        return {
            "id": model_id,
            "ref": f"{provider}/{model_id}",
            "provider": provider,
            "owned_by": meta.get("owned_by") or meta.get("owner") or "",
            "created": meta.get("created"),
            "source": meta.get("source") or "",
        }

    def _probe_bundle(self, bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        provider = str(bundle.get("provider") or "").lower()
        key = str(bundle.get("key") or "")
        base_url = str(bundle.get("base_url") or "")
        if not provider:
            return [], {"success": False, "error": "provider missing"}

        try:
            result = list_models(provider, api_key=key, base_url=base_url or None, timeout=12)
        except Exception as exc:
            return [], {"success": False, "error": f"{type(exc).__name__}: {exc}"}

        raw_models = result.get("models") if isinstance(result, dict) else []
        models = [item for raw in (raw_models or []) if (item := self._normalize_model(provider, raw)) is not None]
        return models, {
            "success": bool(result.get("success")) if isinstance(result, dict) else bool(models),
            "reachable": bool(result.get("success")) if isinstance(result, dict) else bool(models),
            "status_code": result.get("status_code") if isinstance(result, dict) else None,
            "latency_ms": result.get("latency_ms") if isinstance(result, dict) else None,
            "error": result.get("error") if isinstance(result, dict) else None,
        }

    def _bundles(self) -> list[dict[str, Any]]:
        try:
            return discover_runtime_bundles(include_local=True) or []
        except Exception:
            return []

    def _build(self, probe: bool) -> dict[str, Any]:
        bundles = self._bundles()
        deployments: dict[str, dict[str, Any]] = {}
        provider_status: dict[str, dict[str, Any]] = {}

        # One probe per unique provider/base/key tuple. The secret itself never
        # leaves this module and is never returned in the catalog response.
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for bundle in bundles:
            provider = str(bundle.get("provider") or "").lower()
            if not provider or bool(bundle.get("retired")):
                continue
            key = str(bundle.get("key") or "")
            base = str(bundle.get("base_url") or "")
            unique.setdefault((provider, key, base), bundle)

        for (provider, _key, _base), bundle in unique.items():
            known = [self._normalize_model(provider, m) for m in (bundle.get("models") or [])]
            known = [m for m in known if m]
            discovered = list(known)
            probe_info = {"success": None, "reachable": None, "source": "stored"}
            if probe:
                live, info = self._probe_bundle(bundle)
                if live:
                    discovered = live
                    probe_info = {**info, "source": "live"}
                else:
                    probe_info = {**info, "source": "stored" if known else "unavailable"}

            try:
                provider_name = get_provider(provider).get("name") or provider
            except Exception:
                provider_name = provider
            pstatus = provider_status.setdefault(
                provider,
                {
                    "provider": provider,
                    "name": provider_name,
                    "configured": True,
                    "sources": [],
                    "reachable": None,
                    "model_count": 0,
                },
            )
            source = str(probe_info.get("source") or "stored")
            if source not in pstatus["sources"]:
                pstatus["sources"].append(source)
            if probe_info.get("reachable") is not None:
                pstatus["reachable"] = bool(probe_info["reachable"])

            for item in discovered:
                deployments.setdefault(
                    item["ref"],
                    {
                        **item,
                        "source": item.get("source") or source,
                        "available": True,
                        "reachable": probe_info.get("reachable"),
                        "provider_name": pstatus["name"],
                    },
                )

        rows: list[dict[str, Any]] = []
        for ref in sorted(deployments):
            row = deployments[ref]
            try:
                report = negotiate(ref, probe=False)
                row["capabilities"] = dict(report.capabilities)
                row["context_tokens"] = report.context_tokens
                row["capability_notes"] = list(report.notes)
            except Exception as exc:
                row["capabilities"] = {}
                row["context_tokens"] = None
                row["capability_notes"] = [f"capability probe unavailable: {type(exc).__name__}"]

            rows.append(row)

        for provider, status in provider_status.items():
            status["model_count"] = sum(1 for row in rows if row["provider"] == provider)

        return {
            "generated_at": time.time(),
            "ttl_seconds": self.ttl_seconds,
            "probe": probe,
            "providers": sorted(provider_status.values(), key=lambda item: item["provider"]),
            "models": rows,
            "count": len(rows),
        }

    def list(self, *, probe: bool = False, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if refresh or not self._cache_valid() or probe:
                self._cache = self._build(probe=probe)
                self._cached_at = time.time()
            if self._cache is None:
                self._cache = self._build(probe=False)
                self._cached_at = time.time()
            # Return a shallow JSON-safe copy so callers cannot mutate cache state.
            return {
                **self._cache,
                "stale_after": self._cached_at + self.ttl_seconds,
                "age_seconds": max(0.0, time.time() - self._cached_at),
            }

    def selectable(self, model_ref: str, *, probe: bool = False) -> dict[str, Any] | None:
        ref = str(model_ref or "").strip()
        for row in self.list(probe=probe).get("models", []):
            if row.get("ref") == ref:
                return row
        return None

    def clear(self) -> None:
        with self._lock:
            self._cache = None
            self._cached_at = 0.0


model_catalog = ModelCatalog()
