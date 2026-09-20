"""Vault economics — roadmap step 0a (docs/design/VAULT_ACCOUNTS.md).

Account-aware ``MultiKeyManager`` coverage: account grouping, account-level
rate windows, persisted UTC-day (RPD) budgets, terminal auth failures with
account quarantine, and dispatch gating. Everything here is offline and
deterministic: rate windows are filled directly (no sleeps, no fake HTTP) and
the JSON store lives in ``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core.multi_key import MultiKeyManager
from core.providers import get_provider, list_providers


def _mgr(tmp_path, name: str = "keys.json") -> MultiKeyManager:
    return MultiKeyManager(db_path=str(tmp_path / name))


def _entry(mgr: MultiKeyManager, provider: str, key: str) -> dict:
    entry = mgr.get_entry(provider, key)
    assert entry is not None, f"{key} not stored for {provider}"
    return entry


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# §1 — new fields, presets, add_key round-trip
# ---------------------------------------------------------------------------
def test_presets_seed_daily_budget():
    """OpenRouter :free 50 RPD and Codestral 2000 RPD become preset defaults."""
    assert get_provider("openrouter")["default_rpd"] == 50
    assert get_provider("codestral")["default_rpd"] == 2000
    # Groq stays header-driven: its x-ratelimit-limit-requests is a daily value
    # adopted from the response, not a preset guess.
    assert get_provider("groq").get("default_rpd") is None

    by_id = {p["id"]: p for p in list_providers()}
    assert by_id["openrouter"]["default_rpd"] == 50
    assert by_id["codestral"]["default_rpd"] == 2000
    assert by_id["groq"]["default_rpd"] is None


def test_normalize_backfills_new_fields_and_add_key_round_trips(tmp_path):
    db = tmp_path / "keys.json"
    # A store written by an older build: none of the step-0a fields exist.
    db.write_text(json.dumps({"openrouter": [{"key": "sk-or-legacy-0001", "name": "old"}]}))

    mgr = MultiKeyManager(db_path=str(db))
    entry = _entry(mgr, "openrouter", "sk-or-legacy-0001")
    assert entry["account_id"] is None  # conservative shared default account
    assert entry["rpd_limit"] == 50  # seeded :free floor
    assert entry["auth_failed_at"] is None
    assert entry["quarantined"] is False
    assert entry["rpd_used"] == 0

    result = mgr.add_key(
        "groq",
        "gsk-account-0001",
        name="g1",
        account_id="groq:alice",
        rpd_limit=100,
        rpm_limit=7,
        auto_discover=False,
    )
    assert result["success"]
    added = _entry(mgr, "groq", "gsk-account-0001")
    assert added["account_id"] == "groq:alice"
    assert added["rpd_limit"] == 100
    # An explicit --rpd joins --rpm/--tpm in marking the budget manual, so
    # provider headers can never overwrite it.
    assert added["rate_limit_source"] == "manual"


def test_groq_daily_header_adopts_into_rpd_not_rpm(tmp_path):
    """Groq's daily x-ratelimit-limit-requests lands in rpd_limit (spec §4.1)."""
    mgr = _mgr(tmp_path)
    mgr.add_key("groq", "gsk-daily-000000001", name="g", auto_discover=False)

    mgr.mark_key_success(
        "groq",
        "gsk-daily-000000001",
        rate_limit={"limit_requests": 14400, "limit_tokens": 12000},
    )
    entry = _entry(mgr, "groq", "gsk-daily-000000001")
    assert entry["rpd_limit"] == 14400
    assert entry["rpm_limit"] == 30  # preset RPM untouched
    assert entry["tpm_limit"] == 12000  # genuine per-minute value adopted


# ---------------------------------------------------------------------------
# §6 — account grouping heuristic + §5 overview
# ---------------------------------------------------------------------------
def test_propose_account_groups_and_overview(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.add_key("groq", "gsk_shared11111111", name="a1", auto_discover=False)
    mgr.add_key("groq", "gsk_shared22222222", name="a2", auto_discover=False)
    mgr.add_key("groq", "gsk_other33333333", name="a3", auto_discover=False)
    mgr.add_key("groq", "gsk_shared99999999", name="a4", account_id="groq:alice", auto_discover=False)

    groups = mgr.propose_account_groups("groq")["groq"]
    # Same base_url + same 8-char key prefix ⇒ one proposed account.
    merged = [g for g in groups.values() if {"a1", "a2"} <= {k["name"] for k in g}]
    assert len(merged) == 1
    # A different prefix stays its own proposed account (never over-merge).
    assert any({k["name"] for k in g} == {"a3"} for g in groups.values())
    # Already-grouped keys are listed under their explicit account.
    explicit = [g for g in groups.values() if {k["name"] for k in g} == {"a4"}]
    assert explicit and all(k["explicit"] for k in explicit[0])

    # Overview: account grouping + effective account-level budgets (spec §4).
    overview = mgr.list_accounts("groq")
    by_acct = {a["account_id"]: a for a in overview["accounts"]}
    assert by_acct["groq:alice"]["key_count"] == 1
    default = by_acct["groq:default"]  # ungrouped keys share one default account
    assert default["key_count"] == 3
    assert default["rpm_limit_effective"] == 30  # Groq preset floor
    assert default["rpd_limit"] is None  # header-driven, not seeded
    assert default["auth_failed"] == []

    # Redacted list output carries the same grouping/daily fields.
    listed = mgr.list_keys("groq", redact=True)["groq"]
    assert {k["name"]: k["account_id"] for k in listed}["a4"] == "groq:alice"
    assert all("rpd_limit" in k and "quarantined" in k for k in listed)

    # rate_status exposes accounts too.
    assert "accounts" in mgr.rate_status("groq")


# ---------------------------------------------------------------------------
# §2 — account rate windows, min(per-key, per-account, daily)
# ---------------------------------------------------------------------------
def test_account_rpm_window_blocks_31st_binding(tmp_path):
    """10 keys on one Groq account can never bind a 31st time in 60s (spec §7)."""
    mgr = _mgr(tmp_path)
    keys = [f"gsk_acct-{i:010d}" for i in range(10)]
    for i, key in enumerate(keys):
        mgr.add_key("groq", key, name=f"g{i}", account_id="groq:alice", auto_discover=False)

    for _ in range(30):  # Groq preset: 30 RPM per *account*
        key = mgr.get_key("groq")
        assert key is not None
        mgr.mark_key_success("groq", key)

    assert mgr.get_key("groq") is None  # 31st binding denied
    # The per-key windows are far from spent (10 keys were sharing the 30),
    # so it was the account-level window that blocked it.
    assert all(len(mgr._rpm_hits["groq"][k]) <= 3 for k in keys)

    # Non-member accounts are unaffected: a second account keeps dispatching.
    mgr.add_key("groq", "gsk_other-000000001", name="b1", account_id="groq:bob", auto_discover=False)
    assert mgr.get_key("groq") == "gsk_other-000000001"


def test_per_key_window_still_binds_inside_a_healthy_account(tmp_path):
    mgr = _mgr(tmp_path)
    hot, cool = "gsk_hot-0000000001", "gsk_cool-000000002"
    mgr.add_key("groq", hot, name="hot", account_id="groq:alice", rpm_limit=3, auto_discover=False)
    mgr.add_key("groq", cool, name="cool", account_id="groq:alice", auto_discover=False)

    for _ in range(3):
        mgr._record_use("groq", hot, account_id="groq:alice")
    # Clear the account window so only the per-key budget can block.
    mgr._account_rpm_hits["groq"]["groq:alice"].clear()

    handed = {mgr.get_key("groq") for _ in range(4)}
    assert hot not in handed
    assert handed == {cool}


# ---------------------------------------------------------------------------
# §2/§3 — persisted daily budgets
# ---------------------------------------------------------------------------
def test_daily_budget_and_counter_persist_across_reinstantiation(tmp_path):
    db = tmp_path / "keys.json"
    mgr = MultiKeyManager(db_path=str(db))
    mgr.add_key("openrouter", "sk-or-daily-0001", name="or1", rpd_limit=2, account_id="or:alice", auto_discover=False)

    assert mgr.get_key("openrouter") == "sk-or-daily-0001"
    mgr.mark_key_success("openrouter", "sk-or-daily-0001")
    mgr.mark_key_success("openrouter", "sk-or-daily-0001")
    assert mgr.get_key("openrouter") is None  # 2/2 used in this UTC day

    stored = json.loads(db.read_text())["openrouter"][0]
    assert stored["rpd_used"] == 2
    assert stored["rpd_day"] == _today()

    restarted = MultiKeyManager(db_path=str(db))  # process restart
    assert restarted.get_key("openrouter") is None  # counter survived
    assert _entry(restarted, "openrouter", "sk-or-daily-0001")["rpd_used"] == 2

    # UTC-midnight anchor: a counter from a previous day is lazily reset.
    data = json.loads(db.read_text())
    data["openrouter"][0]["rpd_day"] = "2000-01-01"
    db.write_text(json.dumps(data))
    fresh_day = MultiKeyManager(db_path=str(db))
    assert fresh_day.get_key("openrouter") == "sk-or-daily-0001"


def test_daily_counter_is_account_shared_and_blocks_siblings(tmp_path):
    mgr = _mgr(tmp_path)
    a, b = "sk-or-a-0000000001", "sk-or-b-0000000002"
    mgr.add_key("openrouter", a, name="a", account_id="or:team", rpd_limit=2, auto_discover=False)
    mgr.add_key("openrouter", b, name="b", account_id="or:team", auto_discover=False)

    mgr.mark_key_success("openrouter", a)
    for e in mgr.get_all_entries("openrouter"):
        assert e["rpd_used"] == 1  # account-shared counter
        assert e["rpd_day"] == _today()

    mgr.mark_key_success("openrouter", a)
    # The whole account is out of daily budget — the sibling is skipped too,
    # and the account budget is the tightest member limit (2, not the 50 seed).
    assert mgr.get_key("openrouter") is None
    assert mgr.list_accounts("openrouter")["accounts"][0]["rpd_limit"] == 2


# ---------------------------------------------------------------------------
# §3/§4 — terminal auth failures, quarantine, manual re-enable
# ---------------------------------------------------------------------------
def test_auth_failed_is_terminal_until_manual_reenable(tmp_path):
    mgr = _mgr(tmp_path)
    bad, good = "gsk_bad-0000000001", "gsk_good-000000002"
    mgr.add_key("groq", bad, name="bad", account_id="groq:alice", auto_discover=False)
    mgr.add_key("groq", good, name="good", account_id="groq:bob", auto_discover=False)

    mgr.mark_key_failed("groq", bad, error="401 Unauthorized: invalid api key")
    failed = _entry(mgr, "groq", bad)
    assert failed["health_status"] == "auth_failed"
    assert failed["healthy"] is False
    assert failed["auth_failed_at"]

    # Even after the old 5-minute failure-counter window the key stays parked:
    # only re_enable_key() may return it to rotation (spec §3).
    mgr.key_failures["groq"][bad] = 3
    mgr.key_last_used["groq"][bad] = datetime.now() - timedelta(minutes=30)
    for _ in range(3):
        assert mgr.get_key("groq") == good

    result = mgr.re_enable_key("groq", "bad")
    assert result["success"] and result["reenabled"] == 1
    reenabled = _entry(mgr, "groq", bad)
    assert reenabled["auth_failed_at"] is None
    assert reenabled["quarantined"] is False
    assert reenabled["health_status"] == "reenabled"
    assert mgr.key_failures["groq"].get(bad, 0) == 0
    assert {mgr.get_key("groq") for _ in range(2)} == {bad, good}

    missing = mgr.re_enable_key("groq", "does-not-exist")
    assert missing["success"] is False


def test_three_auth_failures_quarantine_account_not_others(tmp_path):
    mgr = _mgr(tmp_path)
    alice = [f"gsk_alice-{i:09d}" for i in range(4)]
    bob = "gsk_bob-0000000001"
    for i, key in enumerate(alice):
        mgr.add_key("groq", key, name=f"a{i}", account_id="groq:alice", auto_discover=False)
    mgr.add_key("groq", bob, name="bob", account_id="groq:bob", auto_discover=False)

    mgr.mark_key_failed("groq", alice[0], error="401 Unauthorized")
    mgr.mark_key_failed("groq", alice[1], error="403 Forbidden")
    assert not any(e["quarantined"] for e in mgr.get_all_entries("groq"))  # 2 < threshold 3

    mgr.mark_key_failed("groq", alice[2], error="error: authentication failed")
    entries = {e["name"]: e for e in mgr.get_all_entries("groq")}
    assert all(entries[f"a{i}"]["quarantined"] for i in range(4))  # whole account parked
    assert entries["bob"]["quarantined"] is False  # other accounts unaffected
    assert mgr.get_key("groq") == bob
    assert [e["name"] for e in mgr.get_dispatchable_entries("groq")] == ["bob"]

    # Account quarantine is lifted only by an explicit operator call.
    lifted = mgr.re_enable_key("groq", "groq:alice", account=True)
    assert lifted["success"] and lifted["reenabled"] == 4
    assert not any(e["quarantined"] for e in mgr.get_all_entries("groq"))
    assert {mgr.get_key("groq") for _ in range(5)} == set(alice) | {bob}


# ---------------------------------------------------------------------------
# §4 — header-driven dispatch
# ---------------------------------------------------------------------------
def test_dispatch_skips_header_exhausted_account_until_refreshed(tmp_path):
    mgr = _mgr(tmp_path)
    k1, k2 = "sk-or-h1-0000000001", "sk-or-h2-0000000002"
    mgr.add_key("openrouter", k1, name="h1", account_id="or:team", auto_discover=False)
    mgr.add_key("openrouter", k2, name="h2", account_id="or:team", auto_discover=False)

    # Provider says the account spent its window: siblings are protected too.
    mgr.mark_key_success("openrouter", k1, rate_limit={"remaining_requests": 0, "limit_requests": 20})
    assert mgr.get_key("openrouter") is None

    # A later success on the sibling refreshes the account-level report.
    mgr.mark_key_success("openrouter", k2, rate_limit={"remaining_requests": 19, "limit_requests": 20})
    assert mgr.get_key("openrouter") is not None


def test_dispatch_skips_quarantined_and_failed_entries(tmp_path):
    mgr = _mgr(tmp_path)
    dead = "gsk_dead-000000001"
    park = ["gsk_park-00000001", "gsk_park-00000002", "gsk_park-00000003"]
    live = "gsk_live-000000001"
    mgr.add_key("groq", dead, name="dead", account_id="groq:other", auto_discover=False)
    for i, key in enumerate(park):
        mgr.add_key("groq", key, name=f"p{i}", account_id="groq:parked", auto_discover=False)
    mgr.add_key("groq", live, name="live", account_id="groq:alive", auto_discover=False)

    mgr.mark_key_failed("groq", dead, error="401 invalid key")
    for key in park:
        mgr.mark_key_failed("groq", key, error="401 invalid key")

    # Only the healthy key of the untouched account is dispatchable.
    assert [e["name"] for e in mgr.get_dispatchable_entries("groq")] == ["live"]
    for _ in range(3):
        assert mgr.get_key("groq") == live
