"""
Multi-API Keys — any AI API key works.
- Unlimited providers (OpenAI-compatible + presets)
- Per-key: base_url, health, models, rate limits, RPM/TPM budgets
- Round-robin + cooldown + parallel task dispatch across keys/models
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.log import get_logger

from .config import config
from .providers import get_provider, list_providers

logger = get_logger(__name__)


def key_fingerprint(key_val: str | None) -> str:
    """Stable, non-reversible identifier for an API key.

    Use this wherever a key has to be named - logs, per-key counters, doctor
    output. Never print a prefix or suffix of the key itself.
    """
    if not key_val:
        return "none"
    return hashlib.sha256(key_val.encode("utf-8", "replace")).hexdigest()[:12]


def _today_utc() -> str:
    """UTC date (``YYYY-MM-DD``) the persisted daily request counter belongs to."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iso_epoch(value) -> float:
    """Epoch seconds for an ISO timestamp; 0.0 when missing or unparseable."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return 0.0


def _key_preview(key_val: str) -> str:
    """Redacted key preview shared by the list/overview surfaces."""
    if not key_val:
        return "(no-key)"
    if len(key_val) > 10:
        return key_fingerprint(key_val)
    return "****"


def _guess_account_id(provider: str, entry: dict) -> str:
    """Conservative same-account heuristic used by :meth:`propose_account_groups`.

    Most providers mint keys with a per-account or per-project prefix
    (``sk-proj-...``, ``gsk_...``, ``hf_...``). Two keys sharing a base_url
    AND the first 8 characters of the key are treated as one account;
    otherwise each key is its own account — never over-merge (spec §6).
    """
    key = entry.get("key") or entry.get("token") or ""
    base = (entry.get("base_url") or get_provider(provider).get("base_url") or "").rstrip("/")
    prefix = key[:8] if len(key) >= 8 else key
    return f"{provider}:{base}#{prefix}" if key else f"{provider}:default"


class MultiKeyManager:
    """Manage many API keys across any providers — load balance, health, limits."""

    MAX_KEYS_PER_PROVIDER = 50
    MAX_KEYS_PER_CUSTOM_API = 10

    #: Distinct keys of one account that may sit at ``auth_failed`` before the
    #: whole account is presumed banned/dead and parked (spec §3). The account
    #: stays quarantined until an operator calls :meth:`re_enable_key`.
    ACCOUNT_QUARANTINE_THRESHOLD = 3

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path("data/api_keys.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._save({"groq": [], "hf": [], "openai": [], "custom": []})

        self.key_queues: dict[str, deque] = defaultdict(deque)
        self.key_failures: dict[str, dict[str, int]] = defaultdict(dict)
        self.key_last_used: dict[str, dict[str, datetime]] = defaultdict(dict)
        # Live rate windows: provider -> key -> list of timestamps
        self._rpm_hits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._tpm_hits: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
        # Account-grouped windows (spec §2): provider -> account_id -> timestamps.
        # Provider quotas are per account/org, so a set of keys sharing one
        # account must not each spend the full budget independently (OG-1).
        self._account_rpm_hits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._account_tpm_hits: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
        self._lock = threading.Lock()
        # Serializes load→mutate→save cycles over the JSON key store. Fleet
        # workers report success/failure from several threads at once; without
        # this, interleaved writes could hand a reader a half-written file,
        # whose parse failure fell back to the empty template and was then
        # saved back — silently wiping every stored API key.
        self._persist_lock = threading.RLock()
        self._load_queues()

    # ---------- persistence ----------

    def _load(self) -> dict:
        try:
            return json.loads(self.db_path.read_text())
        except FileNotFoundError:
            return {"groq": [], "hf": [], "openai": [], "custom": []}
        except Exception:
            # Corrupt/unreadable store: preserve the bad file for manual
            # recovery instead of letting the next _save() overwrite it.
            try:
                backup = self.db_path.with_name(self.db_path.name + f".corrupt-{int(time.time())}")
                if self.db_path.exists() and not backup.exists():
                    shutil.copy2(self.db_path, backup)
            except Exception:
                pass
            return {"groq": [], "hf": [], "openai": [], "custom": []}

    def _save(self, data: dict):
        """Atomically replace the key store.

        Writes go to a sibling temp file followed by ``os.replace`` so a
        concurrent reader can never observe a truncated/partial JSON file.
        """
        tmp = self.db_path.parent / (self.db_path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.db_path)

    def _update(self, mutator: Callable[[dict], None]) -> dict:
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

    def _normalize_entry(self, entry, provider: str, idx: int = 0) -> dict:
        if isinstance(entry, str):
            preset = get_provider(provider)
            return {
                "key": entry,
                "name": f"{provider}_key_{idx + 1}",
                "provider": provider,
                "base_url": preset.get("base_url") or "",
                "added": datetime.now().isoformat(),
                "usage_count": 0,
                "healthy": None,
                "models": [],
                "default_model": preset.get("default_model"),
                "rpm_limit": preset.get("default_rpm"),
                "tpm_limit": preset.get("default_tpm"),
                "rate_limit_source": "preset",
                "account_id": None,
                "rpd_limit": preset.get("default_rpd"),
                "auth_failed_at": None,
                "quarantined": False,
                "rpd_day": None,
                "rpd_used": 0,
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
        # Backfill budgets for keys registered before their provider had a
        # preset (or before presets existed at all), so an upgrade applies the
        # recommended free-tier limits to keys already in the vault instead of
        # leaving them unthrottled.
        preset = get_provider(provider)
        if e.get("rpm_limit") is None and preset.get("default_rpm") is not None:
            e["rpm_limit"] = preset["default_rpm"]
            e.setdefault("rate_limit_source", "preset")
        if e.get("tpm_limit") is None and preset.get("default_tpm") is not None:
            e["tpm_limit"] = preset["default_tpm"]
            e.setdefault("rate_limit_source", "preset")
        # Vault economics (spec §1): account grouping + persisted daily budget.
        # account_id stays None for legacy keys on purpose — the grouping
        # heuristic is a *proposal* (propose_account_groups), because merging
        # distinct accounts under-shoots (safe) while splitting one account
        # re-creates OG-1 (dangerous). None means "one shared default account".
        e.setdefault("account_id", None)
        if e.get("rpd_limit") is None:
            e["rpd_limit"] = preset.get("default_rpd")
        e.setdefault("auth_failed_at", None)
        e.setdefault("quarantined", False)
        # Persisted UTC-day counter (account-shared; see _record_use / _under_rpd).
        e.setdefault("rpd_day", None)
        e.setdefault("rpd_used", 0)
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

    def list_keys(self, provider: str = None, redact: bool = False) -> dict:
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
                out[p].append(
                    {
                        "name": e.get("name"),
                        "preview": _key_preview(key_val),
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
                        # Vault economics (spec §1/§2): account grouping + daily
                        # budget state, so the dashboard/CLI never has to guess.
                        "account_id": e.get("account_id"),
                        "rpd_limit": e.get("rpd_limit"),
                        "rpd_used": (e.get("rpd_used") or 0) if e.get("rpd_day") == _today_utc() else 0,
                        "auth_failed_at": e.get("auth_failed_at"),
                        "quarantined": bool(e.get("quarantined")),
                    }
                )
        return out

    def propose_account_groups(self, provider: str = None) -> dict:
        """Propose conservative account groupings for keys lacking ``account_id``.

        Keys added before this change (or bulk-imported from ``.env``) carry no
        grouping. Guessing wrong in *either* direction is costly: merging
        distinct accounts under-shoots (safe), splitting one account re-creates
        OG-1 (dangerous). So this is a **proposal for operator confirmation**
        (spec §6) — it is never applied silently. Keys with an explicit
        ``account_id`` are listed under it with ``"explicit": True``.

        Returns ``{provider: {proposed_account_id: [{name, preview, explicit}]}}``.
        """
        data = self._load()
        providers = [provider] if provider else list(data.keys())
        out: dict[str, dict[str, list[dict]]] = {}
        for p in providers:
            groups: dict[str, list[dict]] = defaultdict(list)
            for i, k in enumerate(data.get(p, [])):
                e = self._normalize_entry(k, p, i)
                key_val = e.get("key") or ""
                if not key_val:
                    continue
                acct = e.get("account_id") or _guess_account_id(p, e)
                groups[acct].append(
                    {
                        "name": e.get("name"),
                        "preview": _key_preview(key_val),
                        "explicit": bool(e.get("account_id")),
                    }
                )
            out[p] = dict(groups)
        return out

    def list_accounts(self, provider: str = None) -> dict:
        """Account-grouped overview: member keys + effective account-level budgets.

        The dashboard shows *effective account-level RPM, not raw key count*
        (spec §4): the account budget is the tightest member-key budget,
        falling back to the preset default. ``rpd_used`` is the persisted
        UTC-day counter shared by all keys of the account.
        """
        today = _today_utc()
        now = time.time()
        accounts: dict[str, dict] = {}
        for e in self.get_all_entries(provider):
            p = e.get("provider")
            acct = self._account_id(p, e)
            acc = accounts.setdefault(
                acct,
                {
                    "account_id": acct,
                    "provider": p,
                    "keys": [],
                    "key_count": 0,
                    "quarantined": False,
                    "auth_failed": [],
                    "rpd_limit": None,
                    "rpd_used": 0,
                    "rpd_day": today,
                },
            )
            acc["keys"].append(
                {
                    "name": e.get("name"),
                    "preview": _key_preview(e.get("key") or ""),
                    "healthy": e.get("healthy"),
                    "health_status": e.get("health_status"),
                    "auth_failed_at": e.get("auth_failed_at"),
                    "quarantined": bool(e.get("quarantined")),
                    "rpm_limit": e.get("rpm_limit"),
                    "tpm_limit": e.get("tpm_limit"),
                    "rpd_limit": e.get("rpd_limit"),
                }
            )
            acc["key_count"] += 1
            acc["quarantined"] = acc["quarantined"] or bool(e.get("quarantined"))
            if e.get("auth_failed_at"):
                acc["auth_failed"].append(e.get("name"))
            if e.get("rpd_limit"):
                acc["rpd_limit"] = e["rpd_limit"] if acc["rpd_limit"] is None else min(acc["rpd_limit"], e["rpd_limit"])
            if e.get("rpd_day") == today:
                acc["rpd_used"] = max(acc["rpd_used"], int(e.get("rpd_used") or 0))
        for acct, acc in accounts.items():
            rpm, tpm = self._account_budget(acc["provider"], {"account_id": acct})
            acc["rpm_limit_effective"] = rpm
            acc["tpm_limit_effective"] = tpm
            hits = self._account_rpm_hits[acc["provider"]][acct]
            hits[:] = [t for t in hits if now - t < 60.0]
            acc["rpm_used"] = len(hits)
        return {"accounts": list(accounts.values()), "count": len(accounts)}

    def add_key(
        self,
        provider: str,
        api_key: str,
        name: str = None,
        base_url: str = None,
        default_model: str = None,
        rpm_limit: int = None,
        tpm_limit: int = None,
        rpd_limit: int = None,
        account_id: str = None,
        auto_discover: bool = True,
    ) -> dict:
        """Add any API key. Works with openai/groq/openrouter/custom/etc.

        ``account_id`` groups keys that share one provider account/org so the
        rate budgets apply to the account, not each key (spec §1). When
        omitted the key joins the conservative ``<provider>:default`` pool —
        use :meth:`propose_account_groups` before splitting it out.
        """
        provider = (provider or "custom").lower().strip()
        with self._persist_lock:
            data = self._load()
            if provider not in data:
                data[provider] = []

            max_keys = self.MAX_KEYS_PER_CUSTOM_API if provider.startswith("custom") else self.MAX_KEYS_PER_PROVIDER
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
                "name": name or f"{provider}_key_{len(data[provider]) + 1}",
                "provider": provider,
                "base_url": base_url or preset.get("base_url") or "",
                "default_model": default_model or preset.get("default_model"),
                "added": datetime.now().isoformat(),
                "usage_count": 0,
                "healthy": None,
                "models": [],
                "rpm_limit": rpm_limit if rpm_limit is not None else preset.get("default_rpm"),
                "tpm_limit": tpm_limit if tpm_limit is not None else preset.get("default_tpm"),
                # Records where the budget came from so provider-reported
                # limits can later refine a preset default without clobbering
                # a number the user chose deliberately.
                "rate_limit_source": (
                    "manual" if (rpm_limit is not None or tpm_limit is not None or rpd_limit is not None) else "preset"
                ),
                # Vault economics (spec §1): account grouping + UTC-day budget.
                "account_id": account_id or None,
                "rpd_limit": rpd_limit if rpd_limit is not None else preset.get("default_rpd"),
                "auth_failed_at": None,
                "quarantined": False,
                "rpd_day": None,
                "rpd_used": 0,
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

    def remove_key(self, provider: str, key_or_name: str) -> dict:
        with self._persist_lock:
            data = self._load()
            if provider not in data:
                return {"success": False, "error": f"Provider {provider} not found"}
            original_len = len(data[provider])
            data[provider] = [
                k
                for k in data[provider]
                if self._entry_key(k) != key_or_name and (k if isinstance(k, dict) else {}).get("name", "") != key_or_name
            ]
            if len(data[provider]) == original_len:
                return {"success": False, "error": f"Key {key_or_name} not found for {provider}"}
            self._save(data)
        self._load_queues()
        return {"success": True, "provider": provider, "remaining": len(data[provider])}

    def re_enable_key(self, provider: str, key_or_name: str, account: bool = False) -> dict:
        """Clear terminal ``auth_failed`` state and/or an account quarantine.

        Deliberately manual: the operator confirms the credential was fixed
        (rotated, re-billed) before it re-enters rotation, because a 401/403
        key auto-retrying every 5 minutes is the ban-risk pattern from OG-2
        (spec §3). Pass ``account=True`` with the account id to lift a whole
        quarantine in one call.
        """
        matched: list[str] = []

        def _mutate(data: dict) -> None:
            for k in data.get(provider, []):
                if not isinstance(k, dict):
                    continue
                is_key = self._entry_key(k) == key_or_name or k.get("name") == key_or_name
                is_account = bool(account) and (k.get("account_id") or f"{provider}:default") == key_or_name
                if not (is_key or is_account):
                    continue
                k.pop("auth_failed_at", None)
                k["quarantined"] = False
                k["healthy"] = None
                k["health_status"] = "reenabled"
                matched.append(self._entry_key(k))

        self._update(_mutate)
        if not matched:
            return {"success": False, "error": f"No key or account '{key_or_name}' found for {provider}"}
        self._load_queues()
        for key in matched:
            self.key_failures[provider].pop(key, None)
        return {"success": True, "provider": provider, "reenabled": len(matched)}

    def reenable_key(self, provider: str, key_or_name: str, account: bool = False) -> dict:
        """Alias for :meth:`re_enable_key` (the spelling used in the design note)."""
        return self.re_enable_key(provider, key_or_name, account=account)

    def get_entry(self, provider: str, api_key: str = None) -> dict | None:
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

    def get_all_entries(self, provider: str = None) -> list[dict]:
        data = self._load()
        out = []
        providers = [provider] if provider else list(data.keys())
        for p in providers:
            for i, k in enumerate(data.get(p, [])):
                out.append(self._normalize_entry(k, p, i))
        return out

    # ---------- selection / rate limits ----------

    def _account_id(self, provider: str, entry: dict | None = None) -> str:
        """Account an entry belongs to; ``<provider>:default`` when ungrouped.

        Legacy keys without ``account_id`` deliberately share one conservative
        default account rather than each getting a full budget (spec §6).
        """
        return (entry or {}).get("account_id") or f"{provider}:default"

    def _grouped_entries(self, provider: str, entries: list[dict] | None = None) -> dict[str, list[dict]]:
        """``account_id -> member entries`` for one provider."""
        grouped: dict[str, list[dict]] = defaultdict(list)
        for e in entries if entries is not None else self.get_all_entries(provider):
            grouped[self._account_id(provider, e)].append(e)
        return dict(grouped)

    def _budget_from_members(self, provider: str, members: list[dict]) -> tuple[int | None, int | None, int | None]:
        """(rpm, tpm, rpd) budget shared by every key on one account (spec §2).

        Provider quotas are per account/org, so the account budget is the
        tightest member-key budget, falling back to the preset default.
        """
        rpms = [e["rpm_limit"] for e in members if e.get("rpm_limit")]
        tpms = [e["tpm_limit"] for e in members if e.get("tpm_limit")]
        rpds = [e["rpd_limit"] for e in members if e.get("rpd_limit")]
        preset = get_provider(provider)
        rpm = min(rpms) if rpms else preset.get("default_rpm")
        tpm = min(tpms) if tpms else preset.get("default_tpm")
        rpd = min(rpds) if rpds else None
        return rpm, tpm, rpd

    def _account_budget(self, provider: str, entry: dict) -> tuple[int | None, int | None]:
        """(rpm, tpm) budget shared by every key on ``entry['account_id']``."""
        acct = self._account_id(provider, entry)
        members = self._grouped_entries(provider).get(acct, [])
        rpm, tpm, _ = self._budget_from_members(provider, members)
        return rpm, tpm

    @staticmethod
    def _freshest_remaining_requests(members: list[dict]) -> float | None:
        """Newest ``x-ratelimit-remaining-requests`` any member key reported.

        The header is account-scoped on every provider that sends it, so one
        key's response protects its siblings; the freshest report wins, which
        lets a later success on another key un-skip the account naturally
        (spec §4).
        """
        best_ts, best_rem = -1.0, None
        for e in members:
            rem = (e.get("last_rate_limit") or {}).get("remaining_requests")
            if not isinstance(rem, (int, float)) or isinstance(rem, bool):
                continue
            ts = _iso_epoch(e.get("last_rate_limit_at"))
            if ts >= best_ts:
                best_ts, best_rem = ts, float(rem)
        return best_rem

    def _under_rpm(self, provider: str, key: str, rpm_limit: int = None) -> bool:
        if not rpm_limit:
            return True
        now = time.time()
        hits = self._rpm_hits[provider][key]
        # prune > 60s
        hits[:] = [t for t in hits if now - t < 60.0]
        return len(hits) < rpm_limit

    def _account_under_rpm(self, provider: str, account_id: str, rpm_limit: int = None, now: float = None) -> bool:
        """Same 60s sliding window as :meth:`_under_rpm`, scoped to an account."""
        if not rpm_limit:
            return True
        now = now if now is not None else time.time()
        hits = self._account_rpm_hits[provider][account_id]
        hits[:] = [t for t in hits if now - t < 60.0]
        return len(hits) < rpm_limit

    def _under_rpd(self, entry: dict, limit: int | None = None) -> bool:
        """Daily cap check with lazy UTC-midnight rollover (spec §2).

        ``limit`` overrides the entry's own ``rpd_limit``; callers pass the
        account's tightest member limit so a shared counter cannot overshoot
        through a sibling with a higher per-entry value.
        """
        if limit is None:
            limit = entry.get("rpd_limit")
        if not limit:
            return True
        if entry.get("rpd_day") != _today_utc():
            return True  # new UTC day; counter resets on next _record_use
        return (entry.get("rpd_used") or 0) < limit

    def _record_use(self, provider: str, key: str, tokens: int = 0, account_id: str = None):
        now = time.time()
        hits = self._rpm_hits[provider][key]
        hits.append(now)
        hits[:] = [t for t in hits if now - t < 60.0]
        if tokens:
            self._tpm_hits[provider][key].append((now, tokens))
            self._tpm_hits[provider][key][:] = [(t, n) for t, n in self._tpm_hits[provider][key] if now - t < 60.0]
        if account_id:
            acct_hits = self._account_rpm_hits[provider][account_id]
            acct_hits.append(now)
            acct_hits[:] = [t for t in acct_hits if now - t < 60.0]
            if tokens:
                acct_tpm = self._account_tpm_hits[provider][account_id]
                acct_tpm.append((now, tokens))
                acct_tpm[:] = [(t, n) for t, n in acct_tpm if now - t < 60.0]

    def _account_tpm_used(self, provider: str, account_id: str, now: float = None) -> int:
        now = now if now is not None else time.time()
        hits = self._account_tpm_hits[provider][account_id]
        hits[:] = [(t, n) for t, n in hits if now - t < 60.0]
        return sum(n for _, n in hits)

    def _tpm_used(self, provider: str, key: str) -> int:
        now = time.time()
        hits = self._tpm_hits[provider][key]
        hits[:] = [(t, n) for t, n in hits if now - t < 60.0]
        return sum(n for _, n in hits)

    def _can_dispatch(
        self,
        provider: str,
        entry: dict,
        acct_rpm: int | None = None,
        acct_tpm: int | None = None,
        acct_rpd: int | None = None,
        header_remaining: float | None = None,
        now: float = None,
    ) -> bool:
        """Single selection gate shared by every dispatch path (spec §3/§4).

        Enforces min(per-key-remaining, per-account-remaining, daily-remaining)
        plus terminal states, and prefers adopted ``x-ratelimit-remaining``
        header data when deciding.
        """
        if entry.get("auth_failed_at") or entry.get("quarantined"):
            return False
        now = now if now is not None else time.time()
        acct = self._account_id(provider, entry)
        if acct_rpm and not self._account_under_rpm(provider, acct, acct_rpm, now):
            return False
        if acct_tpm and self._account_tpm_used(provider, acct, now) >= acct_tpm:
            return False
        if not self._under_rpd(entry, acct_rpd):
            return False
        if header_remaining is not None and header_remaining <= 1:
            return False  # provider says this account is done for the window
        key = entry.get("key") or ""
        if key and not self._under_rpm(provider, key, entry.get("rpm_limit")):
            return False
        if key and entry.get("tpm_limit") and self._tpm_used(provider, key) >= entry["tpm_limit"]:
            return False
        return True

    def get_dispatchable_entries(self, provider: str) -> list[dict]:
        """Entries eligible to dispatch right now.

        Parallel executors bypass :meth:`get_key`, so they must apply the same
        gates: no terminal auth failure, no account quarantine, and none of the
        per-key / per-account / daily / header-reported budgets exhausted.
        """
        entries = self.get_all_entries(provider)
        groups = self._grouped_entries(provider, entries)
        budgets = {acct: self._budget_from_members(provider, members) for acct, members in groups.items()}
        header_rem = {acct: self._freshest_remaining_requests(members) for acct, members in groups.items()}
        now = time.time()
        out = []
        for e in entries:
            acct = self._account_id(provider, e)
            acct_rpm, acct_tpm, acct_rpd = budgets.get(acct, (None, None, None))
            if self._can_dispatch(provider, e, acct_rpm, acct_tpm, acct_rpd, header_rem.get(acct), now):
                out.append(e)
        return out

    def get_key(self, provider: str = "groq") -> str | None:
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
        entries = self.get_all_entries(provider)
        entry_meta = {self._entry_key(e): e for e in entries}
        groups = self._grouped_entries(provider, entries)
        budgets = {acct: self._budget_from_members(provider, members) for acct, members in groups.items()}
        header_rem = {acct: self._freshest_remaining_requests(members) for acct, members in groups.items()}
        now = time.time()

        with self._lock:
            for _ in range(max(attempts, 1)):
                if not queue:
                    break
                key = queue[0]
                meta = entry_meta.get(key) or {}
                # Terminal states — auth_failed / account quarantine. No
                # 5-minute resurrection: only re_enable_key() brings these back
                # (spec §3), so a deleted/banned credential stops being probed.
                if meta.get("auth_failed_at") or meta.get("quarantined"):
                    queue.rotate(-1)
                    continue
                fails = self.key_failures[provider].get(key, 0)
                if fails >= 3:
                    last_used = self.key_last_used[provider].get(key, datetime.min)
                    if datetime.now() - last_used < timedelta(minutes=5):
                        queue.rotate(-1)
                        continue
                    # Transient failures keep the existing 5-minute reset.
                    self.key_failures[provider][key] = 0

                acct = self._account_id(provider, meta)
                acct_rpm, acct_tpm, acct_rpd = budgets.get(acct, (None, None, None))
                if not self._can_dispatch(
                    provider,
                    meta,
                    acct_rpm,
                    acct_tpm,
                    acct_rpd,
                    header_rem.get(acct),
                    now,
                ):
                    queue.rotate(-1)
                    continue

                queue.rotate(-1)
                self.key_last_used[provider][key] = datetime.now()
                if key.startswith("noauth:"):
                    return ""
                return key

        # Nothing may dispatch: every candidate is terminal, quarantined or
        # budget-exhausted. Callers fall back to another provider instead of
        # hammering an account that has no quota left.
        return None

    def get_key_bundle(self, provider: str) -> dict | None:
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

    def first_available_bundle(
        self,
        prefer: list[str] | None = None,
        require_tools: bool = False,
    ) -> dict | None:
        """
        First usable key bundle across all configured providers (used as a
        fallback when the requested provider has no key, e.g. Ollama not
        running but a Groq/OpenRouter/... key was added).

        Unlike the older store-only implementation this discovers credentials
        from ``.env`` as well as ``data/api_keys.json``, so a provider that is
        configured only through an environment variable is a valid fallback.

        ``require_tools`` filters out providers whose preset rejects tool
        calls (HuggingFace/router, mock) when the caller needs tools.
        """
        try:
            from .provider_resolver import select_usable_bundle

            return select_usable_bundle(
                require_tools=require_tools,
                prefer=prefer,
                exclude_local=True,
            )
        except Exception:
            # Fallback to the legacy store-only behavior if the resolver
            # module is unavailable for some reason.
            data = self._load()
            providers = [p for p, keys in data.items() if keys]
            if not providers:
                return None
            prefer = [p.lower() for p in (prefer or ["custom"])]

            def rank(p: str) -> tuple:
                base_set = any((isinstance(k, dict) and k.get("base_url")) for k in data.get(p, []))
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
                    if require_tools and not self._provider_tools_enabled(provider):
                        continue
                    return bundle
            return None

    def _provider_tools_enabled(self, provider: str) -> bool:
        try:
            return get_provider(provider).get("supports_tools") is not False
        except Exception:
            return True

    def _adopt_reported_limits(self, provider: str, entry: dict, rate_limit: dict) -> None:
        """
        Replace a key's budget with the limits the provider reports in its
        response headers.

        Headers beat presets: the preset is a guess at the free tier, while
        the header is this key's actual quota (higher on a paid plan, lower
        on a throttled one). A budget the user set by hand is left alone, and
        so is a value already adopted from a header — this only overwrites a
        preset-seeded default.

        Groq's ``x-ratelimit-limit-requests`` is a *daily* figure, so it is
        ignored for the per-minute request budget rather than being adopted
        as a wildly permissive RPM.
        """
        from .providers import requests_header_window

        origin = entry.get("rate_limit_source")
        # "manual" = explicitly set by the user; "reported" = already taken
        # from a header. Only overwrite defaults or a stale reported value.
        if origin == "manual":
            return

        adopted = False
        window = requests_header_window(provider)
        if window == "minute" and rate_limit.get("limit_requests"):
            try:
                entry["rpm_limit"] = int(rate_limit["limit_requests"])
                adopted = True
            except (TypeError, ValueError):
                pass
        elif window == "day" and rate_limit.get("limit_requests"):
            # Groq reuses the header name for a *daily* quota (spec §4.1): the
            # number is exactly the RPD cap modelled here, never an RPM budget.
            try:
                entry["rpd_limit"] = int(rate_limit["limit_requests"])
                adopted = True
            except (TypeError, ValueError):
                pass
        if rate_limit.get("limit_tokens"):
            try:
                entry["tpm_limit"] = int(rate_limit["limit_tokens"])
                adopted = True
            except (TypeError, ValueError):
                pass
        if adopted:
            entry["rate_limit_source"] = "reported"

    def mark_key_success(self, provider: str, key: str, tokens: int = 0, latency_ms: int = None, rate_limit: dict = None):
        if not key:
            return
        if provider in self.key_failures and key in self.key_failures[provider]:
            self.key_failures[provider][key] = max(0, self.key_failures[provider][key] - 1)
        known = self.get_entry(provider, key)
        account_id = self._account_id(provider, known)
        self._record_use(provider, key, tokens=tokens or 0, account_id=account_id)

        def _mutate(data: dict) -> None:
            today = _today_utc()
            acct = account_id
            found = False
            for k in data.get(provider, []):
                if isinstance(k, dict) and k.get("key") == key:
                    found = True
                    k["usage_count"] = k.get("usage_count", 0) + 1
                    k["last_used"] = datetime.now().isoformat()
                    k["healthy"] = True
                    k["health_status"] = "ok"
                    acct = k.get("account_id") or f"{provider}:default"
                    if latency_ms is not None:
                        times = k.get("response_times") or []
                        times.append(latency_ms / 1000.0)
                        k["response_times"] = times[-10:]
                        k["avg_response_time"] = sum(k["response_times"]) / len(k["response_times"])
                        k["last_response_time"] = latency_ms / 1000.0
                    if rate_limit:
                        k["last_rate_limit"] = rate_limit
                        # Timestamp lets dispatch treat the freshest account-wide
                        # header report as authoritative (spec §4).
                        k["last_rate_limit_at"] = datetime.now().isoformat()
                        self._adopt_reported_limits(provider, k, rate_limit)
            # Account-shared daily counter (spec §2): increment *every* entry of
            # the account inside this one transaction so siblings read the same
            # rpd_used and the atomic save covers the whole rollover.
            if found:
                for k in data.get(provider, []):
                    if not isinstance(k, dict):
                        continue
                    if (k.get("account_id") or f"{provider}:default") != acct:
                        continue
                    if k.get("rpd_day") != today:
                        k["rpd_day"] = today
                        k["rpd_used"] = 0
                    k["rpd_used"] = int(k.get("rpd_used") or 0) + 1

        try:
            self._update(_mutate)
        except Exception:
            pass

    def mark_key_failed(self, provider: str, key: str, error: str = "", rate_limit: dict = None):
        if not key:
            return
        if provider in self.key_failures:
            self.key_failures[provider][key] = self.key_failures[provider].get(key, 0) + 1
        logger.error(
            f"[MultiKey] key {key_fingerprint(key)} for {provider} failed ({error}), failures: {self.key_failures[provider].get(key, 0)}"
        )
        quarantined: list[str] = []

        def _mutate(data: dict) -> None:
            for k in data.get(provider, []):
                if isinstance(k, dict) and k.get("key") == key:
                    k["last_error"] = str(error)[:300]
                    k["last_failed"] = datetime.now().isoformat()
                    if rate_limit:
                        k["last_rate_limit"] = rate_limit
                        k["last_rate_limit_at"] = datetime.now().isoformat()
                    err_l = (error or "").lower()
                    if "429" in err_l or "rate" in err_l:
                        k["health_status"] = "rate_limited"
                    elif "401" in err_l or "403" in err_l or "auth" in err_l:
                        k["healthy"] = False
                        k["health_status"] = "auth_failed"
                        # Terminal: never auto-returns to rotation, because
                        # re-probing a dead credential from every worker is the
                        # ban-risk pattern from OG-2 (spec §3).
                        k["auth_failed_at"] = k.get("auth_failed_at") or datetime.now().isoformat()
                        acct = k.get("account_id") or f"{provider}:default"
                        failed = {
                            e.get("key") or e.get("token") or ""
                            for e in data.get(provider, [])
                            if isinstance(e, dict)
                            and e.get("auth_failed_at")
                            and (e.get("account_id") or f"{provider}:default") == acct
                        }
                        if len(failed) >= self.ACCOUNT_QUARANTINE_THRESHOLD:
                            # The account is presumed banned/dead: parking one
                            # key while its siblings hammer the same account is
                            # exactly the pattern we must stop. (Bus `alert`
                            # event lands with roadmap step 0b; log until then.)
                            for e in data.get(provider, []):
                                if not isinstance(e, dict):
                                    continue
                                if (e.get("account_id") or f"{provider}:default") == acct:
                                    e["quarantined"] = True
                            if acct not in quarantined:
                                quarantined.append(acct)

        try:
            self._update(_mutate)
        except Exception:
            pass
        for acct in quarantined:
            logger.error(
                f"[MultiKey] Account {acct} quarantined: "
                f"{self.ACCOUNT_QUARANTINE_THRESHOLD}+ keys terminally auth-failed — "
                f"all its keys parked until re_enable_key() clears it"
            )

    # ---------- health + models ----------

    def discover_models(self, provider: str, api_key: str = None, base_url: str = None) -> dict:
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
            def _mutate(data: dict) -> None:
                ids = [m.get("id") for m in result.get("models") or [] if m.get("id")]
                for k in data.get(provider, []):
                    if isinstance(k, dict) and (not api_key or k.get("key") == api_key):
                        k["models"] = ids
                        k["models_updated"] = datetime.now().isoformat()
                        if ids and not k.get("default_model"):
                            k["default_model"] = ids[0]
                        if result.get("rate_limit"):
                            k["last_rate_limit"] = result["rate_limit"]
                            k["last_rate_limit_at"] = datetime.now().isoformat()

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
    ) -> dict:
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
        def _mutate(data: dict) -> None:
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
                    k["last_rate_limit_at"] = datetime.now().isoformat()
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

    def check_all_health(self, provider: str = None) -> list[dict]:
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
            r["key_preview"] = f"{e['key'][:6]}...{e['key'][-4:]}" if e.get("key") and len(e["key"]) > 10 else "****"
            results.append(r)
        return results

    def rate_status(self, provider: str = None) -> dict:
        """Snapshot of RPM/TPM/RPD usage vs limits, per key and per account."""
        entries = self.get_all_entries(provider)
        today = _today_utc()
        out = []
        for e in entries:
            p = e.get("provider")
            key = e.get("key") or ""
            rpm_used = len([t for t in self._rpm_hits[p][key] if time.time() - t < 60]) if key else 0
            tpm_used = self._tpm_used(p, key) if key else 0
            out.append(
                {
                    "provider": p,
                    "name": e.get("name"),
                    "preview": _key_preview(key),
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
                    # Vault economics (spec §1/§2)
                    "account_id": e.get("account_id"),
                    "rpd_limit": e.get("rpd_limit"),
                    "rpd_used": int(e.get("rpd_used") or 0) if e.get("rpd_day") == today else 0,
                    "auth_failed_at": e.get("auth_failed_at"),
                    "quarantined": bool(e.get("quarantined")),
                }
            )
        return {
            "keys": out,
            "count": len(out),
            "accounts": self.list_accounts(provider)["accounts"],
            "providers_known": [p["id"] for p in list_providers()],
        }

    # ---------- parallel execution ----------

    def execute_parallel_with_keys(self, provider: str, tasks: list[dict]) -> list[dict]:
        """Execute tasks in parallel using different API keys (thread pool)."""
        entries = self.get_dispatchable_entries(provider)
        if not entries:
            bundle = self.get_key_bundle(provider)
            if not bundle:
                return [{"success": False, "error": f"No keys for {provider}"}]
            entries = [bundle]

        logger.info(f"[MultiKey] Parallel execution with {len(entries)} keys for {len(tasks)} tasks")

        def run_one(task_id: int, task_data: dict, entry: dict) -> dict:
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

    async def aexecute_parallel_with_keys(self, provider: str, tasks: list[dict], client=None) -> list[dict]:
        """Async mirror of :meth:`execute_parallel_with_keys` (no threads).

        Same task/entry rotation and result shape; concurrency comes from
        ``asyncio`` + pooled HTTP instead of a thread pool. ``client`` is an
        optional ``httpx.AsyncClient`` (tests inject ``MockTransport``).
        """
        from .aio import gather_limit
        from .llm import FreeLLM

        entries = self.get_dispatchable_entries(provider)
        if not entries:
            bundle = self.get_key_bundle(provider)
            if not bundle:
                return [{"success": False, "error": f"No keys for {provider}"}]
            entries = [bundle]

        logger.info(f"[MultiKey] Async parallel execution with {len(entries)} keys for {len(tasks)} tasks")

        async def run_one(task_id: int, task_data: dict, entry: dict) -> dict:
            try:
                model = task_data.get("model") or entry.get("default_model") or get_provider(provider).get("default_model")
                llm = FreeLLM(
                    f"{provider}/{model}",
                    api_key=entry.get("key"),
                    base_url=entry.get("base_url"),
                )
                messages = task_data.get(
                    "messages",
                    [{"role": "user", "content": task_data.get("prompt", "")}],
                )
                resp = await llm.achat(messages, tools=task_data.get("tools"), client=client)
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

        coros = [run_one(idx, task, entries[idx % len(entries)]) for idx, task in enumerate(tasks)]
        results = await gather_limit(min(8, max(1, len(tasks))), *coros)
        results.sort(key=lambda x: x.get("task_id", 0))
        return results


# Global manager
multi_key_manager = MultiKeyManager()
