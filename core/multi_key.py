"""
Multi-API Keys — any AI API key works.
- Unlimited providers (OpenAI-compatible + presets)
- Per-key: base_url, health, models, rate limits, RPM/TPM budgets
- Round-robin + cooldown + parallel task dispatch across keys/models
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import config
from .providers import get_provider, list_providers


class MultiKeyManager:
    """Manage many API keys across any providers — load balance, health, limits."""

    MAX_KEYS_PER_PROVIDER = 50
    MAX_KEYS_PER_CUSTOM_API = 10

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path("data/api_keys.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._save({"groq": [], "hf": [], "openai": [], "custom": []})

        self.key_queues: Dict[str, deque] = defaultdict(deque)
        self.key_failures: Dict[str, Dict[str, int]] = defaultdict(dict)
        self.key_last_used: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        # Live rate windows: provider -> key -> list of timestamps
        self._rpm_hits: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._tpm_hits: Dict[str, Dict[str, List[tuple]]] = defaultdict(lambda: defaultdict(list))
        self._lock = threading.Lock()
        # Serializes load→mutate→save cycles over the JSON key store. Fleet
        # workers report success/failure from several threads at once; without
        # this, interleaved writes could hand a reader a half-written file,
        # whose parse failure fell back to the empty template and was then
        # saved back — silently wiping every stored API key.
        self._persist_lock = threading.RLock()
        self._load_queues()

    # ---------- persistence ----------

    def _load(self) -> Dict:
        try:
            return json.loads(self.db_path.read_text())
        except FileNotFoundError:
            return {"groq": [], "hf": [], "openai": [], "custom": []}
        except Exception:
            # Corrupt/unreadable store: preserve the bad file for manual
            # recovery instead of letting the next _save() overwrite it.
            try:
                backup = self.db_path.with_name(
                    self.db_path.name + f".corrupt-{int(time.time())}"
                )
                if self.db_path.exists() and not backup.exists():
                    shutil.copy2(self.db_path, backup)
            except Exception:
                pass
            return {"groq": [], "hf": [], "openai": [], "custom": []}

    def _save(self, data: Dict):
        """Atomically replace the key store.

        Writes go to a sibling temp file followed by ``os.replace`` so a
        concurrent reader can never observe a truncated/partial JSON file.
        """
        tmp = self.db_path.parent / (self.db_path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.db_path)

    def _update(self, mutator: Callable[[Dict], None]) -> Dict:
        """Thread-safe read-modify-write cycle over the persisted key store."""
        with self._persist_lock:
            data = self._load()
            mutator(data)
            self._save(data)
            return data

    def _entry_key(self, entry) -> str:
        if isinstance(entry, str):
            return entry
        return entry.get("key") or entry.get("token") or ""

    def _normalize_entry(self, entry, provider: str, idx: int = 0) -> Dict:
        if isinstance(entry, str):
            preset = get_provider(provider)
            return {
                "key": entry,
                "name": f"{provider}_key_{idx+1}",
                "provider": provider,
                "base_url": preset.get("base_url") or "",
                "added": datetime.now().isoformat(),
                "usage_count": 0,
                "healthy": None,
                "models": [],
                "default_model": preset.get("default_model"),
                "rpm_limit": preset.get("default_rpm"),
                "tpm_limit": preset.get("default_tpm"),
            }
        e = dict(entry)
        e.setdefault("provider", provider)
        e.setdefault("key", e.get("token") or "")
        e.setdefault("name", f"{provider}_key")
        e.setdefault("usage_count", 0)
        e.setdefault("models", e.get("models") or [])
        if not e.get("base_url"):
            e["base_url"] = get_provider(provider).get("base_url") or ""
        if not e.get("default_model"):
            e["default_model"] = get_provider(provider).get("default_model")
        return e

    def _load_queues(self):
        data = self._load()
        self.key_queues = defaultdict(deque)
        for provider, keys in data.items():
            normalized = []
            for i, k in enumerate(keys):
                entry = self._normalize_entry(k, provider, i)
                if entry.get("key") or get_provider(provider).get("no_auth"):
                    # allow empty key for no_auth providers only if explicitly stored
                    if entry.get("key") or provider in ("ollama", "lmstudio"):
                        normalized.append(entry.get("key") or f"noauth:{provider}")
            self.key_queues[provider] = deque([k for k in normalized if k])
            for key in self.key_queues[provider]:
                self.key_failures[provider].setdefault(key, 0)
                self.key_last_used[provider].setdefault(key, datetime.min)

    # ---------- CRUD ----------

    def list_keys(self, provider: str = None, redact: bool = False) -> Dict:
        data = self._load()
        if provider:
            data = {provider: data.get(provider, [])}
        if not redact:
            return data
        out = {}
        for p, keys in data.items():
            out[p] = []
            for k in keys:
                e = self._normalize_entry(k, p)
                key_val = e.get("key") or ""
                preview = (
                    f"{key_val[:6]}...{key_val[-4:]}" if len(key_val) > 10 else ("(no-key)" if not key_val else "****")
                )
                out[p].append(
                    {
                        "name": e.get("name"),
                        "preview": preview,
                        "base_url": e.get("base_url"),
                        "default_model": e.get("default_model"),
                        "healthy": e.get("healthy"),
                        "health_status": e.get("health_status"),
                        "models_count": len(e.get("models") or []),
                        "models_sample": (e.get("models") or [])[:8],
                        "usage_count": e.get("usage_count", 0),
                        "avg_response_time": e.get("avg_response_time"),
                        "last_tested": e.get("last_tested"),
                        "rpm_limit": e.get("rpm_limit"),
                        "tpm_limit": e.get("tpm_limit"),
                        "rate_limit": e.get("last_rate_limit"),
                        "added": e.get("added"),
                    }
                )
        return out

    def add_key(
        self,
        provider: str,
        api_key: str,
        name: str = None,
        base_url: str = None,
        default_model: str = None,
        rpm_limit: int = None,
        tpm_limit: int = None,
        auto_discover: bool = True,
    ) -> Dict:
        """Add any API key. Works with openai/groq/openrouter/custom/etc."""
        provider = (provider or "custom").lower().strip()
        with self._persist_lock:
            data = self._load()
            if provider not in data:
                data[provider] = []

            max_keys = (
                self.MAX_KEYS_PER_CUSTOM_API
                if provider.startswith("custom")
                else self.MAX_KEYS_PER_PROVIDER
            )
            if len(data[provider]) >= max_keys:
                return {
                    "success": False,
                    "error": f"Max {max_keys} keys for {provider}. Remove old keys first.",
                }

            existing = [self._entry_key(k) for k in data[provider]]
            if api_key and api_key in existing:
                return {"success": False, "error": f"Key already exists for {provider}"}

            preset = get_provider(provider)
            key_entry = {
                "key": api_key or "",
                "name": name or f"{provider}_key_{len(data[provider])+1}",
                "provider": provider,
                "base_url": base_url or preset.get("base_url") or "",
                "default_model": default_model or preset.get("default_model"),
                "added": datetime.now().isoformat(),
                "usage_count": 0,
                "healthy": None,
                "models": [],
                "rpm_limit": rpm_limit if rpm_limit is not None else preset.get("default_rpm"),
                "tpm_limit": tpm_limit if tpm_limit is not None else preset.get("default_tpm"),
            }
            if not key_entry["base_url"] and provider not in ("ollama", "lmstudio"):
                # custom without base_url is ok if they set later — warn
                if provider in ("custom", "vllm", "azure"):
                    key_entry["warning"] = "base_url empty — set with --base-url for this provider"

            data[provider].append(key_entry)
            self._save(data)
        self._load_queues()

        result = {
            "success": True,
            "provider": provider,
            "key_name": key_entry["name"],
            "base_url": key_entry["base_url"],
            "default_model": key_entry["default_model"],
            "total_keys": len(data[provider]),
            "preset": preset.get("name"),
        }

        if auto_discover and (api_key or preset.get("no_auth")):
            try:
                health = self.check_key_health(provider, api_key, base_url=key_entry["base_url"])
                result["health"] = {
                    "healthy": health.get("healthy"),
                    "status": health.get("status"),
                    "latency_ms": health.get("latency_ms"),
                    "models_count": (health.get("models_probe") or {}).get("count"),
                    "models_sample": (health.get("models_probe") or {}).get("sample"),
                    "rate_limit": health.get("rate_limit"),
                    "error": health.get("error"),
                }
            except Exception as e:
                result["health_error"] = str(e)

        return result

    def remove_key(self, provider: str, key_or_name: str) -> Dict:
        with self._persist_lock:
            data = self._load()
            if provider not in data:
                return {"success": False, "error": f"Provider {provider} not found"}
            original_len = len(data[provider])
            data[provider] = [
                k
                for k in data[provider]
                if self._entry_key(k) != key_or_name
                and (k if isinstance(k, dict) else {}).get("name", "") != key_or_name
            ]
            if len(data[provider]) == original_len:
                return {"success": False, "error": f"Key {key_or_name} not found for {provider}"}
            self._save(data)
        self._load_queues()
        return {"success": True, "provider": provider, "remaining": len(data[provider])}

    def get_entry(self, provider: str, api_key: str = None) -> Optional[Dict]:
        data = self._load()
        keys = data.get(provider, [])
        if api_key:
            for k in keys:
                e = self._normalize_entry(k, provider)
                if e.get("key") == api_key or e.get("name") == api_key:
                    return e
            return None
        # first healthy or first
        for k in keys:
            e = self._normalize_entry(k, provider)
            if e.get("healthy") is not False:
                return e
        return self._normalize_entry(keys[0], provider) if keys else None

    def get_all_entries(self, provider: str = None) -> List[Dict]:
        data = self._load()
        out = []
        providers = [provider] if provider else list(data.keys())
        for p in providers:
            for i, k in enumerate(data.get(p, [])):
                out.append(self._normalize_entry(k, p, i))
        return out

    # ---------- selection / rate limits ----------

    def _under_rpm(self, provider: str, key: str, rpm_limit: int = None) -> bool:
        if not rpm_limit:
            return True
        now = time.time()
        hits = self._rpm_hits[provider][key]
        # prune > 60s
        hits[:] = [t for t in hits if now - t < 60.0]
        return len(hits) < rpm_limit

    def _record_use(self, provider: str, key: str, tokens: int = 0):
        now = time.time()
        self._rpm_hits[provider][key].append(now)
        if tokens:
            self._tpm_hits[provider][key].append((now, tokens))
            self._tpm_hits[provider][key][:] = [
                (t, n) for t, n in self._tpm_hits[provider][key] if now - t < 60.0
            ]

    def _tpm_used(self, provider: str, key: str) -> int:
        now = time.time()
        hits = self._tpm_hits[provider][key]
        hits[:] = [(t, n) for t, n in hits if now - t < 60.0]
        return sum(n for _, n in hits)

    def get_key(self, provider: str = "groq") -> Optional[str]:
        """Next available key via round-robin, skip failed / rate-limited."""
        provider = (provider or "groq").lower()
        # env fallbacks
        if provider not in self.key_queues or not self.key_queues[provider]:
            env_map = {
                "groq": config.groq_api_key,
                "hf": config.hf_token,
                "huggingface": config.hf_token,
                "openai": getattr(config, "openai_api_key", None),
            }
            # also check os env via preset
            import os

            preset = get_provider(provider)
            env_key_name = preset.get("env_key")
            if env_key_name and os.getenv(env_key_name):
                return os.getenv(env_key_name)
            return env_map.get(provider)

        queue = self.key_queues[provider]
        attempts = len(queue)
        entry_meta = {self._entry_key(e): e for e in self.get_all_entries(provider)}

        with self._lock:
            for _ in range(max(attempts, 1)):
                if not queue:
                    break
                key = queue[0]
                fails = self.key_failures[provider].get(key, 0)
                if fails >= 3:
                    last_used = self.key_last_used[provider].get(key, datetime.min)
                    if datetime.now() - last_used < timedelta(minutes=5):
                        queue.rotate(-1)
                        continue
                    self.key_failures[provider][key] = 0

                meta = entry_meta.get(key) or {}
                rpm = meta.get("rpm_limit")
                if not self._under_rpm(provider, key, rpm):
                    queue.rotate(-1)
                    continue
                tpm_limit = meta.get("tpm_limit")
                if tpm_limit and self._tpm_used(provider, key) >= tpm_limit:
                    queue.rotate(-1)
                    continue

                queue.rotate(-1)
                self.key_last_used[provider][key] = datetime.now()
                if key.startswith("noauth:"):
                    return ""
                return key

        return queue[0] if queue and not str(queue[0]).startswith("noauth:") else (
            "" if queue else None
        )

    def get_key_bundle(self, provider: str) -> Optional[Dict]:
        """Return key + base_url + default_model for LLM calls."""
        key = self.get_key(provider)
        if key is None and not get_provider(provider).get("no_auth"):
            return None
        entry = self.get_entry(provider, key) if key else self.get_entry(provider)
        preset = get_provider(provider)
        if not entry:
            import os

            env_key = preset.get("env_key")
            api_key = os.getenv(env_key) if env_key else None
            if not api_key and not preset.get("no_auth"):
                if key:
                    api_key = key
                else:
                    return None
            return {
                "key": api_key or key or "",
                "base_url": preset.get("base_url") or "",
                "default_model": preset.get("default_model"),
                "provider": provider,
                "name": "env",
            }
        return {
            "key": entry.get("key") or key or "",
            "base_url": entry.get("base_url") or preset.get("base_url") or "",
            "default_model": entry.get("default_model") or preset.get("default_model"),
            "provider": provider,
            "name": entry.get("name"),
            "models": entry.get("models") or [],
            "rpm_limit": entry.get("rpm_limit"),
            "tpm_limit": entry.get("tpm_limit"),
        }

    def first_available_bundle(self, prefer: Optional[List[str]] = None) -> Optional[Dict]:
        """
        First usable key bundle across all providers (used as a fallback when
        the requested provider has no key, e.g. Ollama not running but a
        custom/groq/... key was added). Providers listed in `prefer` win;
        providers with an explicit base_url (custom endpoints) rank next.
        """
        data = self._load()
        providers = [p for p, keys in data.items() if keys]
        if not providers:
            return None
        prefer = [p.lower() for p in (prefer or ["custom"])]
        # Prefer explicitly listed providers, then ones with a base_url set,
        # then everything else. Keep insertion order otherwise.
        def rank(p: str) -> tuple:
            base_set = any(
                (isinstance(k, dict) and k.get("base_url")) for k in data.get(p, [])
            )
            pref = prefer.index(p) if p in prefer else len(prefer) + 1
            return (pref, 0 if base_set else 1)

        for provider in sorted(providers, key=rank):
            if provider in ("ollama", "lmstudio", "mock"):
                continue
            # Skip pseudo-providers created for custom-API tool round-robin
            if provider.startswith("custom_"):
                continue
            bundle = self.get_key_bundle(provider)
            if bundle and bundle.get("key") and bundle.get("base_url"):
                return bundle
        return None

    def mark_key_success(self, provider: str, key: str, tokens: int = 0, latency_ms: int = None, rate_limit: Dict = None):
        if not key:
            return
        if provider in self.key_failures and key in self.key_failures[provider]:
            self.key_failures[provider][key] = max(0, self.key_failures[provider][key] - 1)
        self._record_use(provider, key, tokens=tokens or 0)

        def _mutate(data: Dict) -> None:
            for k in data.get(provider, []):
                if isinstance(k, dict) and k.get("key") == key:
                    k["usage_count"] = k.get("usage_count", 0) + 1
                    k["last_used"] = datetime.now().isoformat()
                    k["healthy"] = True
                    k["health_status"] = "ok"
                    if latency_ms is not None:
                        times = k.get("response_times") or []
                        times.append(latency_ms / 1000.0)
                        k["response_times"] = times[-10:]
                        k["avg_response_time"] = sum(k["response_times"]) / len(k["response_times"])
                        k["last_response_time"] = latency_ms / 1000.0
                    if rate_limit:
                        k["last_rate_limit"] = rate_limit
                        # adopt server-reported limits when present
                        if rate_limit.get("limit_requests") and not k.get("rpm_limit"):
                            try:
                                k["rpm_limit"] = int(rate_limit["limit_requests"])
                            except Exception:
                                pass
                        if rate_limit.get("limit_tokens") and not k.get("tpm_limit"):
                            try:
                                k["tpm_limit"] = int(rate_limit["limit_tokens"])
                            except Exception:
                                pass

        try:
            self._update(_mutate)
        except Exception:
            pass

    def mark_key_failed(self, provider: str, key: str, error: str = "", rate_limit: Dict = None):
        if not key:
            return
        if provider in self.key_failures:
            self.key_failures[provider][key] = self.key_failures[provider].get(key, 0) + 1
        print(
            f"[MultiKey] Key {key[:10]}... for {provider} failed ({error}), "
            f"failures: {self.key_failures[provider].get(key, 0)}"
        )

        def _mutate(data: Dict) -> None:
            for k in data.get(provider, []):
                if isinstance(k, dict) and k.get("key") == key:
                    k["last_error"] = str(error)[:300]
                    k["last_failed"] = datetime.now().isoformat()
                    if rate_limit:
                        k["last_rate_limit"] = rate_limit
                    err_l = (error or "").lower()
                    if "429" in err_l or "rate" in err_l:
                        k["health_status"] = "rate_limited"
                    elif "401" in err_l or "403" in err_l or "auth" in err_l:
                        k["healthy"] = False
                        k["health_status"] = "auth_failed"

        try:
            self._update(_mutate)
        except Exception:
            pass

    # ---------- health + models ----------

    def discover_models(self, provider: str, api_key: str = None, base_url: str = None) -> Dict:
        from .openai_compat import list_models

        if api_key is None:
            bundle = self.get_key_bundle(provider)
            if not bundle:
                return {"success": False, "error": f"No key for {provider}"}
            api_key = bundle.get("key")
            base_url = base_url or bundle.get("base_url")
        result = list_models(provider, api_key=api_key, base_url=base_url)
        if result.get("success") and api_key is not None:
            # persist models onto matching key entry
            def _mutate(data: Dict) -> None:
                ids = [m.get("id") for m in result.get("models") or [] if m.get("id")]
                for k in data.get(provider, []):
                    if isinstance(k, dict) and (not api_key or k.get("key") == api_key):
                        k["models"] = ids
                        k["models_updated"] = datetime.now().isoformat()
                        if ids and not k.get("default_model"):
                            k["default_model"] = ids[0]
                        if result.get("rate_limit"):
                            k["last_rate_limit"] = result["rate_limit"]

            try:
                self._update(_mutate)
            except Exception:
                pass
        return result

    def check_key_health(
        self,
        provider: str,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ) -> Dict:
        from .openai_compat import health_ping

        entry = None
        if api_key:
            entry = self.get_entry(provider, api_key)
        if entry is None:
            entry = self.get_entry(provider)
        if api_key is None and entry:
            api_key = entry.get("key")
        base_url = base_url or (entry or {}).get("base_url")
        model = model or (entry or {}).get("default_model")

        result = health_ping(provider, api_key=api_key, base_url=base_url, model=model)

        # persist
        def _mutate(data: Dict) -> None:
            for k in data.get(provider, []):
                if not isinstance(k, dict):
                    continue
                if api_key and k.get("key") != api_key:
                    continue
                k["healthy"] = result.get("healthy")
                k["health_status"] = result.get("status")
                k["last_tested"] = datetime.now().isoformat()
                k["last_latency_ms"] = result.get("latency_ms")
                if result.get("error"):
                    k["last_error"] = str(result["error"])[:300]
                if result.get("rate_limit"):
                    k["last_rate_limit"] = result["rate_limit"]
                sample = (result.get("models_probe") or {}).get("sample") or []
                if sample:
                    # merge into models list
                    existing = k.get("models") or []
                    merged = list(dict.fromkeys(list(existing) + list(sample)))
                    k["models"] = merged
                if result.get("model_tested"):
                    # Replace placeholder defaults after a successful fallback.
                    # Custom endpoints are commonly added with "default", which
                    # is not a real model ID (e.g. NVIDIA NIM catalogs).
                    current_default = (k.get("default_model") or "").strip().lower()
                    if result.get("healthy") and current_default in ("", "default", "auto", "local-model"):
                        k["default_model"] = result["model_tested"]
                    else:
                        k.setdefault("default_model", result["model_tested"])
                if result.get("latency_ms"):
                    times = k.get("response_times") or []
                    times.append(result["latency_ms"] / 1000.0)
                    k["response_times"] = times[-10:]
                    k["avg_response_time"] = sum(k["response_times"]) / len(k["response_times"])
                    k["last_response_time"] = result["latency_ms"] / 1000.0
                if api_key:
                    break  # only one

        try:
            self._update(_mutate)
        except Exception as e:
            result["persist_error"] = str(e)
        return result

    def check_all_health(self, provider: str = None) -> List[Dict]:
        entries = self.get_all_entries(provider)
        results = []
        # Also probe no-key local providers
        if provider is None:
            for local in ("ollama",):
                if not any(e["provider"] == local for e in entries):
                    results.append(self.check_key_health(local, api_key=""))
        for e in entries:
            r = self.check_key_health(
                e.get("provider"),
                api_key=e.get("key"),
                base_url=e.get("base_url"),
                model=e.get("default_model"),
            )
            r["key_name"] = e.get("name")
            r["key_preview"] = (
                f"{e['key'][:6]}...{e['key'][-4:]}" if e.get("key") and len(e["key"]) > 10 else "****"
            )
            results.append(r)
        return results

    def rate_status(self, provider: str = None) -> Dict:
        """Snapshot of RPM/TPM usage vs limits for all keys."""
        entries = self.get_all_entries(provider)
        out = []
        for e in entries:
            p = e.get("provider")
            key = e.get("key") or ""
            rpm_used = len(
                [t for t in self._rpm_hits[p][key] if time.time() - t < 60]
            ) if key else 0
            tpm_used = self._tpm_used(p, key) if key else 0
            out.append(
                {
                    "provider": p,
                    "name": e.get("name"),
                    "preview": f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "****",
                    "rpm_used": rpm_used,
                    "rpm_limit": e.get("rpm_limit"),
                    "tpm_used": tpm_used,
                    "tpm_limit": e.get("tpm_limit"),
                    "healthy": e.get("healthy"),
                    "health_status": e.get("health_status"),
                    "last_rate_limit": e.get("last_rate_limit"),
                    "avg_response_time": e.get("avg_response_time"),
                    "models_count": len(e.get("models") or []),
                    "default_model": e.get("default_model"),
                    "failures": self.key_failures.get(p, {}).get(key, 0),
                }
            )
        return {"keys": out, "count": len(out), "providers_known": [p["id"] for p in list_providers()]}

    # ---------- parallel execution ----------

    def execute_parallel_with_keys(self, provider: str, tasks: List[Dict]) -> List[Dict]:
        """Execute tasks in parallel using different API keys (thread pool)."""
        entries = self.get_all_entries(provider)
        if not entries:
            bundle = self.get_key_bundle(provider)
            if not bundle:
                return [{"success": False, "error": f"No keys for {provider}"}]
            entries = [bundle]

        print(
            f"[MultiKey] Parallel execution with {len(entries)} keys for {len(tasks)} tasks"
        )

        def run_one(task_id: int, task_data: Dict, entry: Dict) -> Dict:
            try:
                from .llm import FreeLLM

                model = task_data.get("model") or entry.get("default_model") or get_provider(provider).get("default_model")
                # Force this key via FreeLLM kwargs path
                llm = FreeLLM(
                    f"{provider}/{model}",
                    api_key=entry.get("key"),
                    base_url=entry.get("base_url"),
                )
                messages = task_data.get(
                    "messages",
                    [{"role": "user", "content": task_data.get("prompt", "")}],
                )
                resp = llm.chat(messages, tools=task_data.get("tools"))
                return {
                    "task_id": task_id,
                    "task": task_data.get("prompt") or task_data.get("messages", [{}])[-1].get("content", "")[:80],
                    "api_key": (entry.get("key") or "")[:10] + "...",
                    "key_name": entry.get("name"),
                    "model": f"{provider}/{model}",
                    "response": resp.content,
                    "tool_calls": resp.tool_calls,
                    "usage": getattr(resp, "usage", {}),
                    "success": True,
                }
            except Exception as e:
                return {
                    "task_id": task_id,
                    "success": False,
                    "error": str(e),
                    "api_key": (entry.get("key") or "")[:10] + "...",
                    "key_name": entry.get("name"),
                }

        results = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(tasks)))) as ex:
            futs = []
            for idx, task in enumerate(tasks):
                entry = entries[idx % len(entries)]
                futs.append(ex.submit(run_one, idx, task, entry))
            for fut in as_completed(futs):
                results.append(fut.result())
        results.sort(key=lambda x: x.get("task_id", 0))
        return results


# Global manager
multi_key_manager = MultiKeyManager()
