"""The agent's route into the room.

These cover the two things that are easy to get wrong and invisible when broken:
a refusal has to name what was wrong, and a request has to reach the bus the
room actually reads. The second one is not a unit test of `request` alone — a
previous version published to the canonical bus, validated cleanly, and never
reached a single surface, because the room listens on the dashboard bus.
"""

from __future__ import annotations

import pytest

from core import workspace_ops as ops


def _open(kind: str = "mission", title: str = "T") -> dict:
    return {"op": "open", "surface": {"kind": kind, "title": title}}


class TestVocabulary:
    def test_every_op_is_named(self):
        assert "open" in ops.OP_NAMES
        assert "move" in ops.OP_NAMES
        assert len(ops.OP_NAMES) == 14


class TestValidation:
    def test_accepts_a_well_formed_open(self):
        clean = ops.validate_op(_open())
        assert clean["op"] == "open"
        assert clean["surface"]["kind"] == "mission"

    def test_fills_in_a_missing_title_rather_than_refusing(self):
        clean = ops.validate_op({"op": "open", "surface": {"kind": "chat"}})
        assert clean["surface"]["title"] == "chat"

    def test_refuses_an_unknown_op_by_name(self):
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_op({"op": "teleport", "id": "x"})
        assert "teleport" in caught.value.reason

    def test_refuses_a_missing_id(self):
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_op({"op": "close"})
        assert "id" in caught.value.reason

    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ({"op": "move", "id": "a"}, "x"),
            ({"op": "move", "id": "a", "x": "left", "y": 1}, "x"),
            ({"op": "resize", "id": "a", "w": 0, "h": 10}, "positive"),
            ({"op": "resize", "id": "a", "w": -5, "h": 10}, "positive"),
            ({"op": "dock", "id": "a", "side": "up"}, "side"),
            ({"op": "rename", "id": "a", "title": "   "}, "title"),
        ],
    )
    def test_refuses_bad_fields_naming_the_field(self, payload, fragment):
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_op(payload)
        assert fragment in caught.value.reason

    def test_refuses_a_bool_where_a_number_belongs(self):
        # bool is a subclass of int in Python, so True would sail through as 1.
        with pytest.raises(ops.OpRefusal):
            ops.validate_op({"op": "move", "id": "a", "x": True, "y": 1})

    def test_refuses_nan_and_infinity(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ops.OpRefusal):
                ops.validate_op({"op": "move", "id": "a", "x": bad, "y": 0})

    def test_refuses_absurd_dimensions_rather_than_accepting_them(self):
        clean = ops.validate_op({"op": "resize", "id": "a", "w": 10**9, "h": 10})
        assert clean["w"] == 20000.0

    def test_open_without_a_surface_is_refused(self):
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_op({"op": "open"})
        assert "surface" in caught.value.reason

    def test_truncates_an_absurd_title_rather_than_refusing(self):
        clean = ops.validate_op(_open(title="x" * 5000))
        assert len(clean["surface"]["title"]) == 120


class TestBatches:
    def test_refuses_an_empty_batch(self):
        with pytest.raises(ops.OpRefusal):
            ops.validate_ops([])

    def test_refuses_a_non_list(self):
        with pytest.raises(ops.OpRefusal):
            ops.validate_ops({"op": "open"})

    def test_refuses_an_oversized_batch(self):
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_ops([_open()] * (ops.MAX_OPS_PER_BATCH + 1))
        assert "at most" in caught.value.reason

    def test_one_bad_op_refuses_the_whole_batch_with_its_index(self):
        # Half-applying a batch is how the room ends up in a state neither the
        # agent nor the operator asked for.
        with pytest.raises(ops.OpRefusal) as caught:
            ops.validate_ops([_open(), {"op": "move", "id": "a"}, _open()])
        assert caught.value.index == 1
        assert "op 1" in caught.value.reason


class TestRequest:
    def test_publishes_on_the_bus_the_room_reads(self, monkeypatch):
        """The room's WebSocket subscribes to the dashboard bus, not the
        canonical one. Publishing only to the canonical bus validates, logs, and
        reaches no surface at all — which is exactly what happened once."""
        seen: list[tuple[str, dict]] = []

        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append((kind, data or {})) or {"id": "evt_1"})

        result = ops.request([_open(kind="worker", title="from jarvis")])

        assert result["ok"] is True
        assert result["count"] == 1
        assert seen, "nothing was published to the dashboard bus"
        kind, payload = seen[0]
        assert kind == "workspace_op"
        assert payload["ops"][0]["surface"]["kind"] == "worker"
        assert payload["actor"] == "agent"

    def test_does_not_publish_a_refused_batch(self, monkeypatch):
        seen: list = []
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append(kind) or {"id": "x"})
        with pytest.raises(ops.OpRefusal):
            ops.request([{"op": "nope"}])
        assert not seen, "a refused batch must not reach the bus"

    def test_returns_the_event_id_so_a_log_can_quote_it(self, monkeypatch):
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: {"id": "abc123"})
        assert ops.request([_open()])["event_id"] == "abc123"

    def test_carries_a_trace_id_when_given_one(self, monkeypatch):
        seen: list[dict] = []
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append(data or {}) or {"id": "x"})
        ops.request([_open()], trace_id="tr_9")
        assert seen[0]["trace_id"] == "tr_9"
