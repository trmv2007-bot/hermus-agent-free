# VAULT_ACCOUNTS — Implementation Note: Roadmap Step 0a (Vault economics)

> **This is the FIRST implementation task** of the Persistent Fleet roadmap
> (spec §10, step 0a). Everything else — bus contract (0b), mission object
> (0c), Fleet Registry (1) — builds on the assumption that the Vault enforces
> honest, account-level economics. Without it, the free-key pooling premise
> fails on real providers (see [`OPS_GAPS.md`](OPS_GAPS.md) OG-1, OG-2).
>
> Scope per the roadmap: **`core/multi_key.py` only** (+ one flag in
> `hermus_cli/g_models.py`, + presets in `core/providers.py`). ~1–2 days.

Spec cross-ref: `SPEC_PERSISTENT_FLEET.md` §4 (the Vault) and §10 step 0a.

---

## 1. New key-entry fields

Every entry in `data/api_keys.json` gains four fields. All are optional in
storage and backfilled by `_normalize_entry()` (the existing upgrade path,
`core/multi_key.py:97-135`), so old stores load cleanly.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `account_id` | `str` | heuristic (§6) or `f"{provider}:default"` | Groups keys that share one provider account/org. All quotas below are enforced per `account_id`, not per key. |
| `rpd_limit` | `int \| None` | preset `default_rpd` if any | Requests-per-day cap for the **account** (UTC-midnight anchored). Seed OpenRouter `:free` at 50, Codestral at 2000, Groq from its daily header. `None` = no daily cap. |
| `auth_failed_at` | `str \| None` (ISO ts) | `None` | Terminal auth-failure timestamp. When set, the key is **permanently out of rotation** until manual re-enable (§3). |
| `quarantined` | `bool` | `False` | Set on **every key of an account** when that account trips the quarantine threshold (§3). Quarantined keys never dispatch. |

Daily counters are persisted on the entry (see §2) — no separate file:

```jsonc
{
  "key": "gsk_...", "name": "groq_key_1", "provider": "groq",
  "account_id": "groq:alice",
  "rpm_limit": 30, "tpm_limit": 6000, "rpd_limit": 14400,
  "auth_failed_at": null, "quarantined": false,
  "rpd_day": "2026-08-14",   // UTC date the counter belongs to
  "rpd_used": 137             // requests this UTC day, account-shared
}
```

Backfill in `_normalize_entry()` (add after the `tpm_limit` backfill,
`core/multi_key.py:132-134`):

```python
e.setdefault("account_id", _guess_account_id(provider, e))   # §6
if e.get("rpd_limit") is None:
    e["rpd_limit"] = get_provider(provider).get("default_rpd")
e.setdefault("auth_failed_at", None)
e.setdefault("quarantined", False)
```

And a `default_rpd` on presets that publish daily caps
(`core/providers.py`): `"openrouter": 50` (with the existing note that it's
1000 after $10 topped up — keep the floor, headers/user override lift it),
`"codestral": 2000`. Groq stays header-driven (`requests_header_window ==
"day"`, adopted into `rpd_limit` — see §4).

---

## 2. Account-grouped rate windows + persisted daily counters

**In-memory minute windows** get an account dimension alongside the existing
per-key ones (`core/multi_key.py:44-46`):

```python
# Live rate windows: provider -> key -> timestamps  (existing)
self._rpm_hits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
self._tpm_hits: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
# NEW: provider -> account_id -> timestamps (same 60s sliding window)
self._acct_rpm_hits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
self._acct_tpm_hits: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
```

An account's RPM limit = the **minimum of the member keys' explicit
`rpm_limit`s**, falling back to the preset `default_rpm`. Rationale: provider
quotas are per-account, so the account budget is the per-account number the
preset already encodes (Groq 30, OpenRouter 20, ...); per-key limits stay as
the per-credential ceiling. Helper:

```python
def _account_budget(self, provider: str, entry: dict) -> tuple[int | None, int | None]:
    """(rpm, tpm) budget shared by every key on entry['account_id']."""
    acct = entry.get("account_id") or f"{provider}:default"
    rpms, tpms = [], []
    for e in self.get_all_entries(provider):
        if (e.get("account_id") or f"{provider}:default") != acct:
            continue
        if e.get("rpm_limit"): rpms.append(e["rpm_limit"])
        if e.get("tpm_limit"): tpms.append(e["tpm_limit"])
    preset = get_provider(provider)
    rpm = min(rpms) if rpms else preset.get("default_rpm")
    tpm = min(tpms) if tpms else preset.get("default_tpm")
    return rpm, tpm
```

**Persisted daily counter.** RPD lives on the entries (account-shared: every
key of an account reads/writes the same logical day counter, stored per entry
but incremented together inside one `_update()` so the atomic
temp-file + `os.replace` write in `_save()` covers it):

```python
def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _under_rpd(self, entry: dict) -> bool:
    """Daily cap check. Counter is reset lazily when the UTC day rolls over."""
    limit = entry.get("rpd_limit")
    if not limit:
        return True
    if entry.get("rpd_day") != _today_utc():
        return True  # new day; counter resets on next _record_use
    return (entry.get("rpd_used") or 0) < limit
```

`_record_use()` gains the account window + persisted counter (called from
`mark_key_success`, `core/multi_key.py:530`):

```python
def _record_use(self, provider: str, key: str, tokens: int = 0, account_id: str = None):
    now = time.time()
    self._rpm_hits[provider][key].append(now)
    if tokens:
        self._tpm_hits[provider][key].append((now, tokens))
        self._tpm_hits[provider][key][:] = [
            (t, n) for t, n in self._tpm_hits[provider][key] if now - t < 60.0]
    if account_id:                                   # NEW
        self._acct_rpm_hits[provider][account_id].append(now)
        if tokens:
            self._acct_tpm_hits[provider][account_id].append((now, tokens))
```

…and the RPD increment happens inside `mark_key_success`'s existing
`_mutate` (`core/multi_key.py:532-547`) — roll `rpd_day`/`rpd_used` on the
used key **and all same-account entries** in that one transaction.


---

## 3. Terminal `auth_failed` + account quarantine

**Selection gate** — in `get_key()` (`core/multi_key.py:362-391`), before the
failure-count check, skip terminally failed / quarantined keys **with no
5-minute resurrection**:

```python
meta = entry_meta.get(key) or {}
if meta.get("auth_failed_at") or meta.get("quarantined"):
    queue.rotate(-1)
    continue                      # terminal: only reenable_key() brings it back
```

Then the existing checks run, extended with the two new budgets:

```python
acct = meta.get("account_id") or f"{provider}:default"
acct_rpm, acct_tpm = self._account_budget(provider, meta)
if acct_rpm and not self._acct_under_rpm(provider, acct, acct_rpm):
    queue.rotate(-1); continue
if not self._under_rpd(meta):
    queue.rotate(-1); continue    # daily cap hit — try another account
```

(`_acct_under_rpm` is `_under_rpm` re-pointed at `_acct_rpm_hits`; TPM likewise.)

**Classification** — `mark_key_failed()` (`core/multi_key.py:554-580`)
already maps the error string; make `auth_failed` terminal and add quarantine:

```python
ACCOUNT_QUARANTINE_THRESHOLD = 2  # auth-failures across distinct keys of one account

# inside _mutate, in the existing elif branch:
elif "401" in err_l or "403" in err_l or "auth" in err_l:
    k["healthy"] = False
    k["health_status"] = "auth_failed"
    k["auth_failed_at"] = datetime.now().isoformat()   # NEW: terminal

# after the per-key update, quarantine the account if warranted:
def _mutate(data):
    ...
    failed_keys = {e.get("key") for e in data.get(provider, [])
                   if isinstance(e, dict) and e.get("auth_failed_at")
                   and e.get("account_id") == acct}
    if len(failed_keys) >= ACCOUNT_QUARANTINE_THRESHOLD:
        for e in data.get(provider, []):
            if isinstance(e, dict) and e.get("account_id") == acct:
                e["quarantined"] = True
        # emit `alert` bus event (lands with 0b; log + last_error until then)
```

Quarantine means: the *account* is presumed banned/dead — parking one key
while its siblings keep hammering the same account is exactly the ban-risk
pattern from OG-2. `429` stays non-terminal but gains exponential cooldown
(`cooldown_until = now + min(300, 2 ** fails)` seconds) instead of the flat
5-minute reset; `5xx` stays transient.

**Manual re-enable** (the *only* way back):

```python
def reenable_key(self, provider: str, key_or_name: str, account: bool = False) -> dict:
    """Clear terminal auth_failed (and optionally the account quarantine).

    Deliberately manual — the operator confirms the credential was fixed
    (rotated, re-billed) before it re-enters rotation."""
    def _mutate(data):
        for k in data.get(provider, []):
            if not isinstance(k, dict):
                continue
            if self._entry_key(k) == key_or_name or k.get("name") == key_or_name \
               or (account and k.get("account_id") == key_or_name):
                k.pop("auth_failed_at", None)
                k["quarantined"] = False
                k["healthy"] = None
                k["health_status"] = "reenabled"
    self._update(_mutate)
    self._load_queues()
    self.key_failures[provider].pop(key_or_name, None)
    return {"success": True}
```

Wire CLI: `hermus multikey reenable --provider groq --key groq_key_2`
(and `--account groq:alice` to lift a whole quarantine).


---

## 4. Header-driven dispatch (`x-ratelimit-remaining-*`)

Response headers are already parsed into `rate_limit` dicts by
`_extract_rate_headers()` in `core/openai_compat.py:93-144` and threaded into
`mark_key_success(..., rate_limit=...)` / `mark_key_failed(...,
rate_limit=...)`; `_adopt_reported_limits()`
(`core/multi_key.py:486-523`) already lets headers overwrite preset-seeded
budgets. Three extensions:

1. **Adopt the daily figure.** When `requests_header_window(provider) ==
   "day"` (Groq), adopt `limit_requests` into `rpd_limit` instead of ignoring
   it (today it is deliberately dropped from the RPM budget — correct for
   RPM, but the number is exactly the daily cap we now model):

   ```python
   # in _adopt_reported_limits, after the minute-window branch:
   elif requests_header_window(provider) == "day" and rate_limit.get("limit_requests"):
       try:
           entry["rpd_limit"] = int(rate_limit["limit_requests"])
           adopted = True
       except (TypeError, ValueError):
           pass
   ```

2. **Skip near-exhausted accounts even when local windows look clean.**
   `last_rate_limit` is already persisted per key. In `get_key()`, before
   binding:

   ```python
   rl = meta.get("last_rate_limit") or {}
   rem = rl.get("remaining_requests")
   if isinstance(rem, (int, float)) and rem <= 1:
       queue.rotate(-1); continue   # provider says this account is done for the window
   ```

   `remaining_requests` is account-scoped on every provider that sends it, so
   one key's response protects its siblings. A fresh success on another key of
   the same account overwrites `last_rate_limit` and un-skips naturally.
3. **Precedence stays as documented** in `core/providers.py`: explicit
   `--rpm/--tpm/--rpd` ("manual") > header ("reported") > preset. The
   `rate_limit_source == "manual"` early-return in `_adopt_reported_limits`
   already enforces this; extend the same guard to `rpd_limit`.

---

## 5. `add_key` CLI/API changes + import heuristic

**API** — `MultiKeyManager.add_key()` (`core/multi_key.py:189-235`) gains two
keyword params, both optional:

```python
def add_key(self, provider, api_key, name=None, base_url=None, default_model=None,
            rpm_limit=None, tpm_limit=None, rpd_limit=None,        # NEW
            account_id=None,                                        # NEW
            auto_discover=True) -> dict:
    ...
    key_entry = {
        ...,
        "account_id": account_id or _guess_account_id(provider, {...provisional entry...}),
        "rpd_limit": rpd_limit if rpd_limit is not None else preset.get("default_rpd"),
        "auth_failed_at": None,
        "quarantined": False,
    }
    # rpd_limit passed explicitly joins rpm/tpm in marking the source manual:
    "rate_limit_source": ("manual" if (rpm_limit is not None or tpm_limit is not None
                                       or rpd_limit is not None) else "preset"),
```

**CLI** — `hermus_cli/g_models.py`, `_configure_multikey()`
(`hermus_cli/g_models.py:22-34`):

```python
multikey_add.add_argument("--account", help="Account id this key belongs to "
                          "(keys on one account share rate limits)")
multikey_add.add_argument("--rpd", type=int, help="Requests-per-day budget (account-shared)")
```

…and `_run_multikey` passes `account_id=args.account, rpd_limit=args.rpd`
into `add_key`. New subcommands: `multikey reenable --provider P --key K`,
`multikey accounts [--provider P]` (lists `account_id` → keys, effective
account RPM/RPD — the dashboard shows **effective account-level RPM, not raw
key count**, spec §4).


---

## 6. Account-grouping heuristic for import

Keys added before this change (or bulk-imported from `.env` via
`provider_resolver`) have no `account_id`. Guessing wrong in *either*
direction is costly: merging distinct accounts under-shoots (safe), splitting
one account re-creates OG-1 (dangerous). So the heuristic is conservative and
**proposes groupings for operator confirmation** (spec §4, §13 migration):

```python
def _guess_account_id(provider: str, entry: dict) -> str:
    """Conservative default: same provider+base_url+key-prefix ⇒ same account.

    Most providers mint keys with a per-account or per-project prefix
    (sk-proj-..., gsk_..., hf_...). Two keys sharing base_url AND the first
    8 chars of the key are treated as one account; otherwise each key is its
    own account (never over-merge).
    """
    key = entry.get("key") or ""
    base = (entry.get("base_url") or get_provider(provider).get("base_url") or "").rstrip("/")
    prefix = key[:8] if len(key) >= 8 else key
    return f"{provider}:{base}#{prefix}" if key else f"{provider}:default"
```

Import flow (`hermus multikey list --accounts` / migration `--dry-run`):
group all stored keys by guessed `account_id`, print the proposed groups
(name, preview via the existing `list_keys(redact=True)` format,
`core/multi_key.py:155-187`), and let the operator merge groups with
`hermus multikey add ... --account <chosen-id>` or a `multikey accounts merge
<id1> <id2>` helper that rewrites `account_id` on the second group's entries
inside one `_update()`. Unconfirmed keys keep the conservative per-prefix
grouping — safe by construction.

---

## 7. Acceptance checklist (step 0a done = all true)

- [ ] 10 keys, one `account_id`, Groq preset: `get_key()` never hands out a
      31st binding in a 60s window (unit test with fake clock).
- [ ] OpenRouter entry with `rpd_limit=50`: 51st dispatch in one UTC day
      returns no key; counter survives process restart (persisted).
- [ ] 401 on key A → `auth_failed_at` set, key skipped for >5 min, only
      `reenable_key()` restores it.
- [ ] 401 on 2 keys of one account → all keys of that account
      `quarantined`, other accounts unaffected.
- [ ] Fake server (precedent: `tests/test_universal_keys.py`) returning
      `x-ratelimit-remaining-requests: 0` → sibling keys of that account are
      skipped until a success refreshes `last_rate_limit`.
- [ ] Groq header `x-ratelimit-limit-requests: 14400` adopts into
      `rpd_limit`, never into `rpm_limit`.
- [ ] Old `data/api_keys.json` (no new fields) loads; `_normalize_entry`
      backfills; `hermus multikey add --provider groq --key ... --account
      alice --rpd 100` round-trips.
- [ ] No behaviour change for single-key users with default groupings.

