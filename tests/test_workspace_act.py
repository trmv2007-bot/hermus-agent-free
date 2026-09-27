"""Acting through a surface.

The point of these is the boundary: which actions exist, what they call, and
what happens when one of them is asked for that does not. A generic dispatcher
would pass every one of them and still let the agent run something the room has
no business running.
"""

from __future__ import annotations

import pytest

from core import workspace_act as act


class TestVocabulary:
    def test_the_surface_area_is_a_literal_table(self):
        # No dynamic dispatch: every action is a name in ACTIONS and a literal
        # in HANDLERS. If these drift apart, an action exists that cannot run.
        assert act.ACTIONS == set(act.HANDLERS)

    def test_every_action_names_the_panel_it_shows_in(self):
        assert set(act.ACTION_SURFACE) == act.ACTIONS

    def test_the_expected_actions_are_present(self):
        assert {"terminal.run", "memory.remember", "memory.recall", "computer.frame"} <= act.ACTIONS

    def test_computer_frame_is_read_only(self):
        # Looking at the screen and driving it are different capabilities.
        # /computer/run is the deliberate one; this action must not be it.
        source = (act._computer_frame.__doc__ or "").lower()
        assert "read-only" in source
        assert "/computer/run" in source


class TestRefusals:
    @pytest.mark.asyncio
    async def test_refuses_an_unknown_action_by_name(self):
        with pytest.raises(act.ActionRefusal) as caught:
            await act.act("terminal.rm_rf", {})
        assert "terminal.rm_rf" in caught.value.reason

    @pytest.mark.asyncio
    async def test_refuses_a_non_string_action(self):
        with pytest.raises(act.ActionRefusal):
            await act.act(42, {})

    @pytest.mark.asyncio
    async def test_refuses_non_object_args(self):
        with pytest.raises(act.ActionRefusal) as caught:
            await act.act("terminal.run", ["echo hi"])
        assert "args" in caught.value.reason

    @pytest.mark.asyncio
    async def test_refuses_a_terminal_run_with_no_command(self):
        with pytest.raises(act.ActionRefusal) as caught:
            await act.act("terminal.run", {})
        assert "command" in caught.value.reason

    @pytest.mark.asyncio
    async def test_refuses_an_absurdly_long_command(self):
        with pytest.raises(act.ActionRefusal) as caught:
            await act.act("terminal.run", {"command": "x" * (act.MAX_TERMINAL_COMMAND + 1)})
        assert "longer" in caught.value.reason

    @pytest.mark.asyncio
    async def test_refuses_remember_with_no_content(self):
        with pytest.raises(act.ActionRefusal) as caught:
            await act.act("memory.remember", {"content": "   "})
        assert "content" in caught.value.reason

    @pytest.mark.asyncio
    async def test_refuses_an_absurdly_long_memory(self):
        with pytest.raises(act.ActionRefusal):
            await act.act("memory.remember", {"content": "x" * (act.MAX_MEMORY_CONTENT + 1)})


class TestPublishing:
    @pytest.mark.asyncio
    async def test_publishes_the_result_so_the_panel_can_show_it(self, monkeypatch):
        seen: list[tuple[str, dict]] = []
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append((kind, data or {})) or {"id": "e1"})
        monkeypatch.setitem(act.HANDLERS, "memory.recall", act._async_wrap(lambda args: {"hits": [], "count": 0}))

        await act.act("memory.recall", {"query": "x"})

        assert seen, "nothing was published — the panel would never learn"
        kind, payload = seen[0]
        assert kind == "workspace_action"
        assert payload["surface"] == "memory"
        assert payload["ok"] is True

    @pytest.mark.asyncio
    async def test_a_refused_action_never_reaches_the_bus(self, monkeypatch):
        seen: list = []
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append(kind) or {"id": "e"})
        with pytest.raises(act.ActionRefusal):
            await act.act("nope", {})
        assert not seen

    @pytest.mark.asyncio
    async def test_a_failing_backend_is_reported_not_raised(self, monkeypatch):
        """The command failed, the request was fine. Those are different, and
        conflating them tells the agent to retry something that will never work."""
        seen: list[dict] = []
        import core.dashboard_events as dashboard

        monkeypatch.setattr(dashboard, "publish", lambda kind, data=None: seen.append(data or {}) or {"id": "e"})

        def boom(args):
            raise RuntimeError("sandbox unavailable")

        monkeypatch.setitem(act.HANDLERS, "terminal.run", act._async_wrap(boom))

        result = await act.act("terminal.run", {"command": "echo hi"})
        assert result["ok"] is False
        assert "sandbox unavailable" in result["error"]
        # It still reaches the room, so the panel can show the failure.
        assert seen and seen[0]["ok"] is False
