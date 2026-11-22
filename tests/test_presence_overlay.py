"""Presence overlay: geometry, bridge TTLs, router payloads, and the seam."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import presence_pointer
from gateway import routes_presence_overlay
from presence_app import geometry
from presence_app.bridge import get_presence_bridge, reset_presence_bridge
from presence_app.states import resolve_transition, transition_duration_ms

# A 1080p primary at 100 % beside a 2560x1440 panel at 150 %. Physical
# coordinates start where the previous panel ended, so the second monitor's
# physical origin (1920) is not its DIP origin (1920) × its scale.
FULL_HD = [
    {"index": 0, "bounds": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "scale": 1.0, "physical": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
    {
        "index": 1,
        "bounds": {"x": 1920, "y": 0, "w": 2560, "h": 1440},
        "scale": 1.5,
        "physical": {"x": 1920, "y": 0, "w": 3840, "h": 2160},
    },
]


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(routes_presence_overlay.router)
    reset_presence_bridge()
    with TestClient(app) as test_client:
        yield test_client
    reset_presence_bridge()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clocked_bridge():
    clock = FakeClock()
    bridge = reset_presence_bridge(clock=clock)
    yield bridge, clock
    reset_presence_bridge()


# ------------------------------------------------------------------ geometry
def test_parse_rect_accepts_numbers_and_normalizes_shape():
    assert geometry.parse_rect({"x": 10, "y": 20, "w": 30, "h": 40}) == {"x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0}
    assert geometry.parse_rect({"x": "12", "y": 4, "width": 8, "height": 9}) == {"x": 12.0, "y": 4.0, "w": 8.0, "h": 9.0}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "rect",
        {},
        {"x": 1, "y": 2, "w": 0, "h": 10},
        {"x": 1, "y": 2, "w": -5, "h": 10},
        {"x": float("nan"), "y": 2, "w": 10, "h": 10},
        {"x": float("inf"), "y": 2, "w": 10, "h": 10},
        {"x": True, "y": 2, "w": 10, "h": 10},
        {"x": 1, "y": 2, "w": 10, "h": None},
        {"x": 1, "y": 2, "w": 1e9, "h": 10},
        {"x": 1e9, "y": 2, "w": 10, "h": 10},
        {"x": 1, "y": 2},
    ],
)
def test_parse_rect_rejects_anything_undrawable(payload):
    with pytest.raises(geometry.PresencePayloadError):
        geometry.parse_rect(payload)


def test_point_rect_centres_a_click_target():
    rect = geometry.point_rect(200, 100, 80, 40)
    assert rect == {"x": 160.0, "y": 80.0, "w": 80.0, "h": 40.0}


def test_ring_and_label_never_cover_the_target():
    target = {"x": 500, "y": 400, "w": 120, "h": 40}
    ring = geometry.ring_rect(target)
    # The stroke is drawn on this box with width <= the gap, so its inner edge
    # is still outside the target: the thing being pointed at stays uncovered.
    stroke = 1.6
    assert ring["x"] + stroke <= target["x"]
    assert ring["y"] + stroke <= target["y"]
    assert ring["x"] + ring["w"] - stroke >= target["x"] + target["w"]
    box = geometry.label_box(target, (140, 26), (1920, 1080))
    assert not geometry.overlaps(box, target)
    assert box["placement"] == "above"


def test_label_flips_below_at_the_top_edge_and_stays_on_screen():
    target = {"x": 40, "y": 2, "w": 60, "h": 20}
    box = geometry.label_box(target, (160, 26), (1920, 1080))
    assert box["placement"] == "below"
    assert box["y"] > 2 and box["y"] + box["h"] <= 1080
    assert not geometry.overlaps(box, target)


def test_physical_rects_map_per_display_with_dpi():
    layout = geometry.parse_displays(FULL_HD)
    assert geometry.virtual_bounds(layout) == {"x": 0.0, "y": 0.0, "w": 4480.0, "h": 1440.0}

    primary = {"x": 100, "y": 200, "w": 100, "h": 50}
    mapped, ok = geometry.physical_to_dip(primary, layout)
    assert ok and mapped == primary

    # A 150 % second monitor: physical 1920 + 150 = DIP 1920 + 100.
    second = {"x": 1920 + 150, "y": 300, "w": 150, "h": 60}
    mapped, ok = geometry.physical_to_dip(second, layout)
    assert ok
    assert mapped == {"x": 2020.0, "y": 200.0, "w": 100.0, "h": 40.0}


def test_physical_rect_outside_every_display_is_reported_unmapped():
    layout = geometry.parse_displays(FULL_HD)
    mapped, ok = geometry.physical_to_dip({"x": 90_000, "y": 90_000, "w": 10, "h": 10}, layout)
    assert ok is False
    assert mapped["x"] == 90_000


def test_parse_displays_rejects_junk():
    with pytest.raises(geometry.PresencePayloadError):
        geometry.parse_displays("monitors")
    with pytest.raises(geometry.PresencePayloadError):
        geometry.parse_displays([{"x": 0, "y": 0, "w": 100, "h": 100, "scale": 0}])


# -------------------------------------------------------------------- states
def test_state_cross_fade_is_never_instant_and_error_reads_slowest():
    assert transition_duration_ms("idle", "idle") == 0
    assert transition_duration_ms("idle", "listening") < transition_duration_ms("idle", "thinking")
    assert transition_duration_ms("error", "idle") > transition_duration_ms("thinking", "acting")


def test_acting_to_thinking_is_folded_while_inside_the_dwell_window():
    assert resolve_transition("acting", "thinking", 40)[0] == "acting"
    assert resolve_transition("acting", "thinking", 5_000)[0] == "thinking"


# -------------------------------------------------------------------- bridge
def test_bridge_reports_unknown_states_without_changing_anything(clocked_bridge):
    bridge, _clock = clocked_bridge
    snapshot = bridge.set_state("daydreaming")
    assert snapshot["ok"] is False
    assert bridge.snapshot()["state"] == "idle"


def test_bridge_transitions_and_records_history(clocked_bridge):
    bridge, clock = clocked_bridge
    bridge.set_state("thinking", rationale="planning")
    assert bridge.snapshot()["state"] == "thinking"
    clock.advance(1.0)
    bridge.set_state("acting", ttl_ms=900)
    snapshot = bridge.snapshot()
    assert snapshot["state"] == "acting"
    assert snapshot["previous"] == "thinking"
    clock.advance(2.0)
    assert bridge.snapshot()["state"] == "idle"
    assert [entry["state"] for entry in bridge.snapshot()["history"]][-2:] == ["acting", "idle"]


def test_point_and_say_expire_on_their_own_ttl(clocked_bridge):
    bridge, clock = clocked_bridge
    bridge.point({"x": 10, "y": 10, "w": 20, "h": 20}, label="Save", ttl_ms=600)
    bridge.say("Writing the file now", ttl_ms=600)
    assert bridge.snapshot()["point"]["label"] == "Save"
    clock.advance(0.7)
    snapshot = bridge.snapshot()
    assert snapshot["point"] is None
    assert snapshot["say"] is None


def test_pointing_wakes_a_sleeping_character(clocked_bridge):
    bridge, _clock = clocked_bridge
    bridge.hide()
    assert bridge.snapshot()["state"] == "sleeping"
    bridge.point({"x": 0, "y": 0, "w": 40, "h": 40})
    assert bridge.snapshot()["state"] == "acting"


def test_say_is_clamped_to_the_two_line_contract(clocked_bridge):
    bridge, _clock = clocked_bridge
    snapshot = bridge.say("  spaced   words \n\n" + "x" * 400)
    text = snapshot["say"]["text"]
    assert len(text) == geometry.MAX_SAY_CHARS
    assert "\n" not in text


def test_bridge_converts_physical_points_through_the_registered_layout(clocked_bridge):
    bridge, _clock = clocked_bridge
    bridge.set_displays(FULL_HD)
    snapshot = bridge.point({"x": 2070, "y": 300, "w": 150, "h": 60}, space="physical")
    assert snapshot["point"]["x"] == 2020.0
    assert snapshot["point"]["mapped"] is True


def test_hide_clears_everything_and_show_revives(clocked_bridge):
    bridge, _clock = clocked_bridge
    bridge.point({"x": 1, "y": 1, "w": 9, "h": 9}, label="x")
    bridge.say("hello")
    hidden = bridge.hide()
    assert hidden["visible"] is False and hidden["point"] is None and hidden["say"] is None
    revived = bridge.show()
    assert revived["visible"] is True and revived["state"] == "idle"


def test_note_input_rejects_junk_and_clamps(clocked_bridge):
    bridge, _clock = clocked_bridge
    assert bridge.note_input("1200")["input_idle_ms"] == 1200
    assert bridge.note_input(-5)["input_idle_ms"] == 0
    with pytest.raises(geometry.PresencePayloadError):
        bridge.note_input("soon")


# -------------------------------------------------------------------- router
def test_router_serves_the_renderer_page(client):
    page = client.get("/presence/overlay")
    assert page.status_code == 200
    assert "HERMUS" in page.text
    assert "/presence/overlay/app.js" in page.text
    for asset in ("/presence/overlay/app.css", "/presence/overlay/app.js"):
        assert client.get(asset).status_code == 200


def test_router_state_transitions(client):
    initial = client.get("/api/presence/state").json()
    assert initial["state"] == "idle"
    for state in ("listening", "thinking", "acting", "speaking", "error", "sleeping"):
        response = client.post("/api/presence/state", json={"state": state})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == state
        assert client.get("/api/presence/state").json()["state"] == state


def test_router_point_valid_and_labelled(client):
    response = client.post("/api/presence/point", json={"x": 120, "y": 240, "w": 80, "h": 32, "label": "Save"})
    assert response.status_code == 200
    point = response.json()["point"]
    assert point["label"] == "Save" and point["w"] == 80
    assert response.json()["state"] == "acting"


def test_router_rejects_a_bad_rect_and_keeps_serving(client):
    for body in (
        {"x": "left", "y": 2, "w": 10, "h": 10},
        {"x": 1, "y": 2, "w": -10, "h": 10},
        {"y": 2, "w": 10, "h": 10},
        {"x": 1, "y": 2, "w": 10, "h": 10, "space": "inches"},
        {"x": 1, "y": 2, "w": 10, "h": 10, "ttl_ms": "soon"},
        {"x": 1, "y": 2, "w": 10, "h": 10, "label": 42},
    ):
        response = client.post("/api/presence/point", json=body)
        assert response.status_code == 400, body
        assert response.json()["ok"] is False
    assert client.get("/api/presence/state").status_code == 200


def test_router_rejects_an_unknown_state(client):
    response = client.post("/api/presence/state", json={"state": "giggling"})
    assert response.status_code == 400
    assert client.post("/api/presence/state", json={}).status_code == 400


def test_router_say_hide_and_input(client):
    said = client.post("/api/presence/say", json={"text": "Reading the page"})
    assert said.status_code == 200 and said.json()["say"]["text"] == "Reading the page"
    assert client.post("/api/presence/say", json={"text": 3}).status_code == 400
    typed = client.post("/api/presence/input", json={"idle_ms": 900})
    assert typed.status_code == 200 and typed.json()["input_idle_ms"] == 900
    assert client.post("/api/presence/input", json={"idle_ms": "later"}).status_code == 400
    hidden = client.post("/api/presence/hide").json()
    assert hidden["visible"] is False and hidden["point"] is None


def test_router_point_ttl_expires(client, monkeypatch):
    from presence_app import bridge as bridge_module

    clock = FakeClock()
    monkeypatch.setattr(bridge_module.get_presence_bridge(), "_clock", clock)
    client.post("/api/presence/point", json={"x": 10, "y": 10, "w": 40, "h": 40, "ttl_ms": 500})
    assert client.get("/api/presence/state").json()["point"] is not None
    clock.advance(0.6)
    assert client.get("/api/presence/state").json()["point"] is None


def test_router_registers_displays(client):
    response = client.post("/api/presence/displays", json=FULL_HD)
    assert response.status_code == 200
    assert response.json()["canvas"]["w"] == 4480.0
    assert client.post("/api/presence/displays", json=[{"x": 0, "y": 0, "w": 10, "h": 10, "scale": 99}]).status_code == 400


def test_stream_emits_snapshots_then_releases_its_client_slot():
    bridge = reset_presence_bridge()

    async def consume_two_frames():
        response = await routes_presence_overlay.presence_stream()
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-store"
        generator = response.body_iterator
        first = await generator.__anext__()
        second = await generator.__anext__()
        await generator.aclose()
        return first, second

    first, second = asyncio.run(consume_two_frames())
    assert first.startswith("retry:")
    assert second.startswith("data: ")
    snapshot = json.loads(second[len("data: "):])
    assert snapshot["state"] in ("idle", "sleeping")
    assert "states" in snapshot and "point" in snapshot
    assert bridge.snapshot()["clients"] == 0


def test_stream_only_sends_when_the_version_moves():
    bridge = reset_presence_bridge()

    async def frames():
        response = await routes_presence_overlay.presence_stream()
        generator = response.body_iterator
        await generator.__anext__()  # retry hint
        initial = await generator.__anext__()
        bridge.say("only one frame for this", ttl_ms=5000)
        changed = await generator.__anext__()
        await generator.aclose()
        return initial, changed

    initial, changed = asyncio.run(frames())
    assert json.loads(initial[len("data: "):])["version"] < json.loads(changed[len("data: "):])["version"]


# ---------------------------------------------------------------------- seam
def test_seam_maps_a_click_to_a_point_rect():
    target = presence_pointer.target_for("click", {"x": 400, "y": 300})
    assert target["label"] == "Click"
    rect = target["rect"]
    assert rect["x"] + rect["w"] / 2 == 400 and rect["y"] + rect["h"] / 2 == 300


def test_seam_ignores_actions_without_a_location(monkeypatch):
    monkeypatch.setattr(presence_pointer, "focused_window_rect", lambda: None)
    assert presence_pointer.target_for("click", {}) is None
    assert presence_pointer.target_for("type_text", {}) is None
    assert presence_pointer.target_for("nonsense", {"x": 1, "y": 2}) is None


def test_announce_points_in_process_and_never_raises(monkeypatch):
    reset_presence_bridge()
    monkeypatch.setattr(presence_pointer, "focused_window_rect", lambda: None)
    point = presence_pointer.announce("click", {"x": 640, "y": 480, "button": "left"})
    assert point is not None
    snapshot = get_presence_bridge().snapshot()
    assert snapshot["point"]["source"] == "computer"
    assert snapshot["state"] == "acting"
    for junk in ({}, {"x": "nope", "y": None}, {"x": 1e9, "y": 1e9}):
        assert presence_pointer.announce("click", junk) is None or isinstance(presence_pointer.announce("click", junk), dict)


def test_announce_honours_the_kill_switch(monkeypatch):
    reset_presence_bridge()
    monkeypatch.setenv("HERMUS_PRESENCE_POINTER", "0")
    assert presence_pointer.announce("click", {"x": 10, "y": 10}) is None
    assert get_presence_bridge().snapshot()["point"] is None


def test_the_real_controller_emits_a_point_before_it_acts(monkeypatch):
    """End to end through the shipped seam: a gated controller action points first."""
    from core.computer.controller import ComputerActionController
    from core.computer.keyboard import DryRunKeyboard
    from core.computer.mouse import DryRunMouse
    from core.computer.window_manager import DryRunWindowBackend

    reset_presence_bridge()
    monkeypatch.setenv("HERMUS_PRESENCE_POINTER", "1")
    controller = ComputerActionController(
        mouse=DryRunMouse(),
        keyboard=DryRunKeyboard(),
        window_manager=DryRunWindowBackend(),
        approval=None,
    )
    result = controller.click(320, 240)
    assert result["action"] == "click"
    point = get_presence_bridge().snapshot()["point"]
    assert point is not None
    assert point["label"] == "Click"
    assert point["x"] + point["w"] / 2 == 320 and point["y"] + point["h"] / 2 == 240
    assert point["source"] == "computer"


def test_announce_result_marks_a_failed_action_as_error():
    reset_presence_bridge()
    presence_pointer.announce("click", {"x": 50, "y": 50})
    presence_pointer.announce_result("click", {"ok": False, "detail": "denied"})
    assert get_presence_bridge().snapshot()["state"] == "error"
