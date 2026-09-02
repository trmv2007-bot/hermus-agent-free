"""Regression coverage for the shared desktop WorldState model.

Each test here pins one defect that was found and fixed in
``core/computer/world_state.py`` / ``core/state/world.py``:

* ``satisfies`` reported false negatives on short but real labels ("OK").
* The compatibility property setters mutated state without bumping ``revision``,
  so diagnose/transition phases were invisible to a change counter.
* The ``modal`` setter destroyed dialog history; ``elements`` did not dedupe.
* ``from_dict`` dropped ``max_observations`` on a save/load round trip.
* ``last_verification`` was overwritten by plain (non-verification) vision reads.
* A caller-supplied timestamp could move the snapshot clock backwards.
* ``dataclasses.asdict`` raised because the RLock was a dataclass field.
* ``migrate_world_state`` silently replaced an unreadable file with an empty
  snapshot and still reported success — real data loss.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta

import pytest

from core.computer.world_state import WorldState
from core.state import (
    create_world_state,
    detect_legacy,
    load_world_state,
    migrate_world_state,
    world_state_from_dict,
)


# --- satisfies(): no false negatives on short-but-real labels ----------------
def test_satisfies_matches_short_real_labels():
    world = WorldState()
    world.update({"active_application": "Chrome", "visible_targets": ["OK button", "Save"]})

    assert world.satisfies("OK")["matched"] is True
    assert world.satisfies("click OK")["matched"] is True
    assert world.satisfies("Chrome")["matched"] is True
    # A genuinely absent target must still not match.
    assert world.satisfies("Firefox address bar")["matched"] is False


def test_satisfies_condition_without_substantive_tokens_is_vacuous():
    world = WorldState()
    world.update({"active_application": "Chrome"})

    result = world.satisfies("the")
    assert result["matched"] is True
    assert result["confidence"] == 1.0
    assert "no substantive tokens" in result["detail"]
    assert world.satisfies("")["matched"] is True


# --- property setters participate in change accounting -----------------------
def test_property_setters_bump_revision_only_when_value_changes():
    world = WorldState()
    world.begin_task("t")
    baseline = world.revision

    world.current_state = "DIAGNOSE:CLICK"
    assert world.revision == baseline + 1
    assert world.task_state == "DIAGNOSE:CLICK"

    # Writing the same value again is a no-op, not a phantom change.
    world.current_state = "DIAGNOSE:CLICK"
    assert world.revision == baseline + 1

    world.application = "Chrome"
    world.window = "YouTube"
    assert world.revision == baseline + 3
    assert (world.active_application, world.active_window) == ("Chrome", "YouTube")


def test_state_machine_style_transition_is_visible_in_the_snapshot():
    """The diagnose/transition phases write through ``current_state``."""
    world = WorldState()
    world.begin_task("t")
    before = world.revision

    world.current_state = "DIAGNOSE:OPEN_BROWSER"
    world.current_state = "BROWSER_READY"

    assert world.revision == before + 2
    assert world.task_state == "BROWSER_READY"
    assert world.to_dict()["current_state"] == "BROWSER_READY"


# --- elements / modal setter semantics ---------------------------------------
def test_elements_setter_dedupes_like_update():
    world = WorldState()
    world.elements = ["a", "b", "a"]
    assert world.elements == ["a", "b"]
    assert world.visible_targets == ["a", "b"]


def test_modal_setter_preserves_dialog_history():
    world = WorldState()
    world.update({"dialogs": ["Permission prompt"]})
    world.dialogs.append("Update popup")

    world.modal = "Save dialog"

    assert world.dialogs == ["Permission prompt", "Update popup", "Save dialog"]
    assert world.modal == "Save dialog"
    # Setting the same modal again must not duplicate it.
    world.modal = "Save dialog"
    assert world.dialogs.count("Save dialog") == 1
    # Clearing empties the stack.
    world.modal = None
    assert world.dialogs == []
    assert world.modal is None


# --- serialization round trip ------------------------------------------------
def test_max_observations_survives_round_trip(tmp_path):
    world = WorldState(max_observations=5)
    for index in range(10):
        world.update({"detail": f"obs {index}"})
    assert len(world.observations) == 5

    assert world.to_dict()["max_observations"] == 5

    path = tmp_path / "world.json"
    world.save(str(path))
    restored = WorldState.load(str(path))

    assert restored.max_observations == 5
    restored.update({"detail": "one more"})
    assert len(restored.observations) == 5

    # from_dict alone must round trip it too.
    assert world_state_from_dict(world.to_dict()).max_observations == 5


def test_invalid_max_observations_falls_back_to_default():
    assert WorldState.from_dict({"max_observations": 0}).max_observations == 100
    assert WorldState.from_dict({"max_observations": "abc"}).max_observations == 100
    assert WorldState.from_dict({"max_observations": -4}).max_observations == 100


def test_asdict_works_now_that_the_lock_is_not_a_field():
    world = WorldState(task="t")
    data = dataclasses.asdict(world)
    assert data["task"] == "t"
    assert "_lock" not in data
    assert "_lock" not in [f.name for f in dataclasses.fields(WorldState)]


# --- last_verification semantics ---------------------------------------------
def test_plain_vision_update_does_not_claim_to_be_a_verification():
    world = WorldState()
    world.update({"active_application": "Firefox"}, source="vision")
    assert world.last_verification is None

    world.update({"ok": True, "detail": "button present"}, source="action_verification")
    assert world.last_verification is not None
    assert world.last_verification["ok"] is True

    # A later non-verification read must not clobber the recorded verification.
    world.update({"active_window": "New tab"}, source="vision")
    assert world.last_verification["ok"] is True


def test_observation_records_verification_outcome():
    world = WorldState()
    world.update({"matched": False, "detail": "missing"}, source="action_verification")
    assert world.observations[-1]["verification_ok"] is False


# --- clock handling ----------------------------------------------------------
def test_supplied_timestamp_cannot_move_the_clock_backwards():
    world = WorldState()
    world.update({"detail": "now"}, source="a")
    current = world.timestamp

    stale = (datetime.now().astimezone() - timedelta(days=3650)).isoformat()
    world.update({"detail": "replayed", "timestamp": stale}, source="b")

    assert world.timestamp >= current
    assert world.observations[-1]["detail"] == "replayed"


def test_newer_supplied_timestamp_is_accepted():
    world = WorldState()
    world.update({"detail": "old"}, source="a")
    future = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
    world.update({"detail": "new", "timestamp": future}, source="b")
    assert world.timestamp == future


# --- corrupt / missing file handling -----------------------------------------
def test_load_is_forgiving_by_default_and_strict_on_demand(tmp_path):
    missing = tmp_path / "nope.json"
    assert WorldState.load(str(missing)).task_state == "UNKNOWN"
    with pytest.raises(FileNotFoundError):
        WorldState.load(str(missing), strict=True)

    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    assert WorldState.load(str(corrupt)).task_state == "UNKNOWN"
    with pytest.raises(ValueError):
        WorldState.load(str(corrupt), strict=True)

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        WorldState.load(str(not_an_object), strict=True)

    assert load_world_state(str(missing)).task_state == "UNKNOWN"


def test_migrate_refuses_to_overwrite_an_unreadable_file(tmp_path):
    corrupt = tmp_path / "world.json"
    original = "{ partially written checkpoint, still valuable"
    corrupt.write_text(original, encoding="utf-8")

    result = migrate_world_state(str(corrupt))

    assert result["success"] is False
    assert result["corrupt"] is True
    assert "unreadable" in result["error"]
    # The whole point: the original bytes are still on disk.
    assert corrupt.read_text(encoding="utf-8") == original


def test_migrate_with_allow_corrupt_backs_up_before_replacing(tmp_path):
    corrupt = tmp_path / "world.json"
    original = "{ partially written checkpoint"
    corrupt.write_text(original, encoding="utf-8")

    result = migrate_world_state(str(corrupt), allow_corrupt=True)

    assert result["success"] is True
    assert result["corrupt"] is True
    backup = tmp_path / result["backup"].split("/")[-1]
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original
    assert json.loads(corrupt.read_text(encoding="utf-8"))["task_state"] == "UNKNOWN"


def test_migrate_valid_file_still_writes_canonical_snapshot(tmp_path):
    source = tmp_path / "world.json"
    source.write_text(json.dumps(
        {"active_application": "chrome", "current_state": "IDLE", "revision": 7}
    ), encoding="utf-8")

    assert detect_legacy(str(source)) is True
    out = tmp_path / "out.json"
    result = migrate_world_state(str(source), out_path=str(out),
                                 marker_path=str(tmp_path / "marker.json"))

    assert result["success"] is True
    assert result["corrupt"] is False
    assert result["revision"] == 7
    snapshot = json.loads(out.read_text(encoding="utf-8"))
    assert snapshot["active_application"] == "chrome"
    assert snapshot["task_state"] == "IDLE"
    assert json.loads((tmp_path / "marker.json").read_text(encoding="utf-8"))["written"] == str(out)
    # The input is untouched when we wrote elsewhere.
    assert json.loads(source.read_text(encoding="utf-8"))["revision"] == 7


def test_migrate_dry_run_does_not_write(tmp_path):
    source = tmp_path / "world.json"
    source.write_text(json.dumps({"active_application": "chrome"}), encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    result = migrate_world_state(str(source), dry_run=True)

    assert result["dry_run"] is True
    assert result["corrupt"] is False
    assert "active_application" in result["keys"]
    assert source.read_text(encoding="utf-8") == before


def test_migrate_missing_file_reports_failure(tmp_path):
    result = migrate_world_state(str(tmp_path / "absent.json"))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_detect_legacy_ignores_directories(tmp_path):
    assert detect_legacy(str(tmp_path)) is False
    f = tmp_path / "world.json"
    f.write_text("{}", encoding="utf-8")
    assert detect_legacy(str(f)) is True


# --- the core.state construction boundary ------------------------------------
def test_core_state_factories_produce_canonical_world_state():
    world = create_world_state(task="browse", task_state="PLANNING")
    assert isinstance(world, WorldState)
    assert (world.task, world.task_state) == ("browse", "PLANNING")

    rebuilt = world_state_from_dict(world.to_dict())
    assert isinstance(rebuilt, WorldState)
    assert rebuilt.task == "browse"


def test_facade_wraps_a_single_state_and_exposes_the_live_surface():
    from core.state import WorldStateFacade, get_world_state

    facade = WorldStateFacade()
    facade.begin_task("open browser", "PLANNING")
    facade.update({"active_application": "chrome", "visible_targets": ["button"]})
    facade.before_action("EXECUTING", {"click": "button"})
    facade.mark_state("clicked", True)
    facade.finish_task(True)

    assert facade.task_state == "SUCCESS"
    assert facade.current_state == "SUCCESS"
    assert facade.active_application == "chrome"
    assert "button" in facade.visible_targets
    assert facade.revision >= 4
    assert facade.state is not None
    assert get_world_state().canonical == "v1"
