"""Awareness: the read-only desktop picture, and the gate that keeps it quiet.

Everything here runs against a fake provider, so these tests say what the layer
does on any machine — including the headless Linux box CI runs on, where the real
provider degrades instead of throwing.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.attention import AttentionGate
from core.awareness.journal import AwarenessJournal
from core.awareness.render import MAX_BLOCK_CHARS, DesktopContextRenderer, render_snapshot
from core.awareness.service import AwarenessService
from core.awareness.settings import AwarenessSettings, SettingsStore
from core.connectors.desktop import DesktopConnector, DesktopProvider, PrivacyPolicy, read_desktop


class FakeDesktop(DesktopProvider):
    """A desktop that answers as told, and fails where told to fail."""

    name = "fake"

    def __init__(self) -> None:
        self.focused_value = {
            "title": "main.py — hermus",
            "process": "Code.exe",
            "pid": 42,
            "rect": None,
            "fullscreen": False,
        }
        self.windows_value = [
            {"title": "main.py — hermus", "process": "Code.exe", "pid": 42},
            {"title": "Inbox (3) - Chrome", "process": "chrome.exe", "pid": 7},
        ]
        self.power_value = {"on_ac": False, "percent": 14, "seconds_left": 1800}
        self.idle_value = 8.0
        self.displays_value = [
            {"width": 2560, "height": 1440, "work_width": 2560, "work_height": 1400, "primary": True, "scale": 1.5}
        ]
        self.clipboard_value = {"kind": "text", "hash": "0123456789abcdef", "length": 12}
        self.failures: set[str] = set()

    def _maybe_fail(self, name: str) -> None:
        if name in self.failures:
            raise RuntimeError(f"{name} denied by policy")

    def focused_window(self):
        self._maybe_fail("focused")
        return self.focused_value, None

    def visible_windows(self, limit: int = 40):
        self._maybe_fail("windows")
        return self.windows_value[:limit], None

    def power(self):
        self._maybe_fail("power")
        return self.power_value, None

    def idle_seconds(self):
        self._maybe_fail("idle")
        return self.idle_value, None

    def displays(self):
        self._maybe_fail("displays")
        return self.displays_value, None

    def clipboard(self):
        self._maybe_fail("clipboard")
        return self.clipboard_value, None


SNAPSHOT_FIELDS = ("focused", "windows", "power", "idle", "displays", "clipboard")


def _service(tmp_path, provider=None, **settings) -> AwarenessService:
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AwarenessSettings(**settings))
    return AwarenessService(
        provider=provider or FakeDesktop(),
        store=store,
        journal=AwarenessJournal(tmp_path / "journal.jsonl"),
        poll_interval=0.0,
    )


# -- snapshot shape and degradation ------------------------------------------------


def test_snapshot_has_every_field_with_a_value_or_a_reason():
    snapshot = read_desktop(FakeDesktop())

    for name in SNAPSHOT_FIELDS:
        assert name in snapshot, f"{name} is missing from the snapshot"
        assert snapshot[name]["ok"] is True, snapshot[name]
        assert snapshot[name]["value"] is not None
    assert snapshot["fullscreen"] == {"ok": True, "value": False}
    assert snapshot["focused"]["value"]["process"] == "Code.exe"
    assert snapshot["displays"]["value"][0]["scale"] == 1.5
    assert snapshot["idle"]["value"]["seconds"] == 8.0


def test_a_denied_field_degrades_to_unavailable_and_never_raises():
    provider = FakeDesktop()
    provider.failures = {"focused", "clipboard"}

    snapshot = read_desktop(provider)

    assert snapshot["focused"]["ok"] is False
    assert "denied by policy" in snapshot["focused"]["reason"]
    assert snapshot["clipboard"]["ok"] is False
    assert snapshot["windows"]["ok"] is True
    assert snapshot["fullscreen"]["ok"] is False


def test_a_platform_without_desktop_apis_reports_why():
    from core.connectors.desktop import NullDesktopProvider

    snapshot = read_desktop(NullDesktopProvider("this machine is Linux; no window manager is readable"))

    assert all(snapshot[name]["ok"] is False for name in SNAPSHOT_FIELDS)
    assert all("Linux" in snapshot[name]["reason"] for name in SNAPSHOT_FIELDS)


# -- privacy -----------------------------------------------------------------------


def test_excluded_app_never_appears_in_any_field():
    provider = FakeDesktop()
    provider.focused_value = {"title": "Secret Notes", "process": "notepad.exe", "pid": 9, "rect": None, "fullscreen": False}
    provider.windows_value = [
        {"title": "Secret Notes", "process": "notepad.exe", "pid": 9},
        {"title": "Inbox", "process": "chrome.exe", "pid": 7},
    ]

    snapshot = read_desktop(provider, policy=PrivacyPolicy(excluded_apps=("notepad",)))
    rendered = json.dumps(snapshot)

    assert "Secret Notes" not in rendered
    assert "notepad" not in rendered.lower()
    assert snapshot["focused"]["value"] == {"excluded": True, "fullscreen": False}
    assert [entry["process"] for entry in snapshot["windows"]["value"]] == ["chrome.exe"]


def test_private_window_keeps_the_process_and_blanks_the_title():
    provider = FakeDesktop()
    provider.focused_value = {
        "title": "HDFC Bank - NetBanking",
        "process": "chrome.exe",
        "pid": 7,
        "rect": None,
        "fullscreen": False,
    }

    snapshot = read_desktop(provider)

    assert snapshot["focused"]["value"]["title"] == "[private]"
    assert snapshot["focused"]["value"]["process"] == "chrome.exe"
    assert "NetBanking" not in json.dumps(snapshot)


def test_private_mode_heuristic_covers_incognito_and_password_managers():
    provider = FakeDesktop()
    provider.windows_value = [
        {"title": "Inbox - Incognito", "process": "chrome.exe", "pid": 7},
        {"title": "Vault", "process": "1Password.exe", "pid": 8},
        {"title": "notes.md", "process": "Code.exe", "pid": 9},
    ]

    snapshot = read_desktop(provider)
    titles = {entry["process"]: entry["title"] for entry in snapshot["windows"]["value"]}

    assert titles["chrome.exe"] == "[private]"
    assert titles["1Password.exe"] == "[private]"
    assert titles["Code.exe"] == "notes.md"


def test_secret_shaped_strings_are_redacted_from_titles():
    provider = FakeDesktop()
    provider.focused_value = {
        "title": "terminal — export OPENAI_API_KEY=sk-liveabcd1234EFGH5678 and gh token ghp_abcdefghijkl1234",
        "process": "WindowsTerminal.exe",
        "pid": 11,
        "rect": None,
        "fullscreen": False,
    }

    snapshot = read_desktop(provider)
    title = snapshot["focused"]["value"]["title"]

    assert "sk-liveabcd1234EFGH5678" not in title
    assert "ghp_abcdefghijkl1234" not in title
    assert "redacted" in title


def test_connector_facts_carry_no_window_titles():
    connector = DesktopConnector(provider=FakeDesktop())
    connector.enable()

    facts = connector.observe()
    rendered = json.dumps(facts)

    assert any(fact["predicate"] == "focused_process" for fact in facts)
    assert "Code.exe" in rendered
    assert "main.py" not in rendered
    assert "Inbox" not in rendered


# -- the prompt block --------------------------------------------------------------


def test_context_block_is_capped_and_costs_nothing_when_unchanged():
    provider = FakeDesktop()
    provider.windows_value = [
        {
            "title": f"document {index} — a very long window title meant to push the block past its cap",
            "process": f"app{index}.exe",
            "pid": index,
        }
        for index in range(40)
    ]
    snapshot = read_desktop(provider)

    block = render_snapshot(snapshot)

    assert 0 < len(block) <= MAX_BLOCK_CHARS
    renderer = DesktopContextRenderer()
    first = renderer.block(snapshot)
    second = renderer.block(snapshot)
    assert first and len(first) <= MAX_BLOCK_CHARS
    assert second == "", "an unchanged desktop must cost no tokens"


def test_a_changed_focus_renders_again():
    provider = FakeDesktop()
    renderer = DesktopContextRenderer()
    assert renderer.block(read_desktop(provider))

    provider.focused_value = {"title": "Inbox", "process": "chrome.exe", "pid": 7, "rect": None, "fullscreen": False}
    block = renderer.block(read_desktop(provider))

    assert "chrome.exe" in block


def test_paused_awareness_injects_nothing(tmp_path):
    service = _service(tmp_path)
    assert service.context_block(mode="chat")

    service.pause(True)

    assert service.context_block(mode="chat") == ""
    assert render_snapshot(service.snapshot()) == ""
    assert service.public_snapshot()["paused"] is True


def test_context_is_chat_only_and_honours_the_env_flag(tmp_path, monkeypatch):
    service = _service(tmp_path)

    assert service.context_block(mode="agent") == ""
    assert service.context_block(mode="chat")

    service.renderer.reset()
    monkeypatch.setenv("HERMUS_AWARENESS_CONTEXT", "0")
    assert service.context_block(mode="chat") == ""


# -- the gate ----------------------------------------------------------------------


def _gate(settings=None, snapshot=None, clock=None, enqueue=None):
    state = {"now": (clock or datetime(2026, 11, 23, 14, 0).timestamp())}
    config = settings or AwarenessSettings(quiet_hours=None, typing_seconds=3.0)
    snap = snapshot if snapshot is not None else read_desktop(FakeDesktop())
    gate = AttentionGate(
        settings_provider=lambda: config,
        snapshot_provider=lambda: snap,
        clock=lambda: state["now"],
        enqueue=enqueue,
    )
    return gate, config, state, snap


def _offer(gate, snap, pattern="focus.long_session", now=None):
    return gate.offer(pattern=pattern, title="t", body="b", task="", snapshot=snap, now=now)


def test_typing_suppresses_and_batches():
    gate, _, _, snap = _gate()
    snap["idle"] = {"ok": True, "value": {"seconds": 1.2}}

    result = _offer(gate, snap)

    assert result["delivered"] is False
    assert "typing" in result["reasons"]
    assert result["batched"] is True


def test_fullscreen_suppresses():
    gate, _, _, snap = _gate()
    snap["fullscreen"] = {"ok": True, "value": True}

    result = _offer(gate, snap)

    assert result["delivered"] is False
    assert "fullscreen" in result["reasons"]


def test_a_call_suppresses():
    gate, _, _, snap = _gate()
    snap["in_call"] = True

    result = _offer(gate, snap)

    assert result["delivered"] is False
    assert "in a call" in result["reasons"]


def test_quiet_hours_suppress():
    late = datetime(2026, 11, 23, 23, 30).timestamp()
    gate, config, state, snap = _gate(settings=AwarenessSettings(quiet_hours={"start": "22:00", "end": "07:00"}))
    state["now"] = late

    result = _offer(gate, snap, now=late)

    assert result["delivered"] is False
    assert "quiet hours" in result["reasons"]


def test_rate_limit_two_an_hour_twenty_minutes_apart():
    gate, _, state, snap = _gate()

    first = _offer(gate, snap, pattern="power.low_battery")
    state["now"] += 21 * 60
    second = _offer(gate, snap, pattern="focus.long_session")
    state["now"] += 60
    third = _offer(gate, snap, pattern="other.pattern")

    assert first["delivered"] is True
    assert second["delivered"] is True
    assert third["delivered"] is False
    assert "rate limited" in third["reasons"]

    state["now"] += 19 * 60  # 41 minutes after the first: two fired, still inside the hour
    assert _offer(gate, snap, pattern="other.pattern")["delivered"] is False

    state["now"] += 22 * 60
    assert _offer(gate, snap, pattern="other.pattern")["delivered"] is True


def test_five_events_during_a_call_produce_exactly_one_digest():
    gate, _, _, snap = _gate()
    snap["in_call"] = True
    for index in range(5):
        _offer(gate, snap, pattern=f"pattern.{index}")

    during = gate.suggestions()
    assert during["items"] == []
    assert during["digest"] is None
    assert during["gate"]["allowed"] is False

    snap["in_call"] = False
    after = gate.suggestions()

    assert after["digest"]["count"] == 5
    assert after["digest"]["kind"] == "digest"
    assert after["count"] == 1

    opened = gate.respond(after["digest"]["id"], "accept")

    assert opened["status"] == "opened"
    assert len(gate.suggestions()["items"]) == 5


def test_digest_dismiss_clears_the_batch():
    gate, _, _, snap = _gate()
    snap["fullscreen"] = {"ok": True, "value": True}
    _offer(gate, snap)
    _offer(gate, snap)
    snap["fullscreen"] = {"ok": True, "value": False}

    digest = gate.suggestions()["digest"]
    assert gate.respond(digest["id"], "not_now")["status"] == "dismissed"
    assert gate.suggestions()["digest"] is None


def test_not_now_mutes_that_pattern_for_seven_days():
    gate, settings, state, snap = _gate()

    fired = _offer(gate, snap, pattern="power.low_battery")
    assert fired["delivered"] is True
    muted = gate.respond(fired["suggestion"]["id"], "not_now")

    assert muted["status"] == "muted"
    until = datetime.fromisoformat(muted["muted_until"])
    assert 6 <= (until.timestamp() - state["now"]) / 86400 <= 7.01
    assert settings.muted_patterns["power.low_battery"]

    state["now"] += 120
    again = _offer(gate, snap, pattern="power.low_battery")
    assert again["delivered"] is False
    assert "muted" in again["reason"]

    state["now"] += 7 * 86400 + 60
    assert _offer(gate, snap, pattern="power.low_battery")["delivered"] is True


def test_accept_hands_the_task_to_the_queue():
    queued: list[str] = []
    gate, _, _, snap = _gate(enqueue=lambda task: queued.append(task) or "job_1")

    fired = gate.offer(pattern="power.low_battery", title="Battery", body="b", task="check jobs", snapshot=snap)
    result = gate.respond(fired["suggestion"]["id"], "accept")

    assert result["queued"] is True
    assert queued == ["check jobs"]


def test_accept_without_a_queue_is_reported_honestly():
    gate, _, _, snap = _gate()

    fired = gate.offer(pattern="power.low_battery", title="Battery", body="b", task="check jobs", snapshot=snap)
    result = gate.respond(fired["suggestion"]["id"], "accept")

    assert result["queued"] is False
    assert "queue" in result["reason"]


def test_low_battery_source_fires_only_on_battery_below_threshold():
    gate, _, _, snap = _gate()
    snap["power"] = {"ok": True, "value": {"on_ac": False, "percent": 12, "seconds_left": 900}}

    fired = gate.tick(snapshot=snap)
    assert [item["suggestion"]["pattern"] for item in fired] == ["power.low_battery"]

    gate2, _, _, snap2 = _gate()
    snap2["power"] = {"ok": True, "value": {"on_ac": True, "percent": 12, "seconds_left": None}}
    assert gate2.tick(snapshot=snap2) == []


def test_long_session_source_needs_a_full_sitting():
    gate, _, state, snap = _gate(settings=AwarenessSettings(quiet_hours=None, long_session_minutes=90))
    snap["idle"] = {"ok": True, "value": {"seconds": 5.0}}
    snap["power"] = {"ok": True, "value": {"on_ac": True, "percent": 100, "seconds_left": None}}

    gate.tick(snapshot=snap, now=state["now"])
    state["now"] += 60 * 30
    assert gate.tick(snapshot=snap, now=state["now"]) == []
    state["now"] += 61 * 60
    fired = gate.tick(snapshot=snap, now=state["now"])

    assert [item["suggestion"]["pattern"] for item in fired] == ["focus.long_session"]
    state["now"] += 60
    assert gate.tick(snapshot=snap, now=state["now"]) == [], "one nudge per sitting"


# -- settings and journal -----------------------------------------------------------


def test_unknown_setting_is_refused(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")

    with pytest.raises(ValueError):
        store.update({"clipbord": False})


def test_settings_round_trip_and_clamp(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")

    updated = store.update({"max_per_hour": 99, "quiet_hours": {"start": "22:00", "end": "07:00"}, "excluded_apps": ["Notepad"]})

    assert updated.max_per_hour == 20
    assert store.load().excluded_apps == ["notepad"]
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["quiet_hours"] == {"start": "22:00", "end": "07:00"}


def test_journal_summarises_then_clears(tmp_path):
    journal = AwarenessJournal(tmp_path / "journal.jsonl")
    snapshot = read_desktop(FakeDesktop())
    journal.sample(snapshot, force=True)
    journal.record("suggestion_offered", {"pattern": "power.low_battery"})

    summary = journal.summary()
    assert summary["records"] == 2
    assert summary["suggestions"]["by_pattern"] == {"power.low_battery": 1}
    assert "main.py" not in json.dumps(summary)

    cleared = journal.clear()
    assert cleared["cleared"] is True
    assert journal.summary()["records"] == 0


# -- routes ------------------------------------------------------------------------


def test_routes_expose_snapshot_settings_and_suggestions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import core.awareness as awareness_pkg
    from gateway.gateway import app

    monkeypatch.delenv("HERMUS_GATEWAY_TOKEN", raising=False)
    service = _service(tmp_path)
    monkeypatch.setattr(awareness_pkg, "get_awareness", lambda: service)

    with TestClient(app) as client:
        snapshot = client.get("/api/v1/awareness/snapshot")
        settings = client.get("/api/v1/awareness/settings")
        updated = client.put("/api/v1/awareness/settings", json={"paused": True})
        refused = client.put("/api/v1/awareness/settings", json={"clipbord": True})
        suggestions = client.get("/api/v1/awareness/suggestions")

    assert snapshot.status_code == 200
    assert snapshot.json()["focused"]["value"]["process"] == "Code.exe"
    assert settings.status_code == 200 and settings.json()["settings"]["enabled"] is True
    assert updated.status_code == 200 and updated.json()["settings"]["paused"] is True
    assert refused.status_code == 400
    assert suggestions.status_code == 200 and "digest" in suggestions.json()
