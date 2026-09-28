"""The persona must not lie about what HERMUS can do.

These tests exist because a persona that overstates itself is a real failure
mode, not a style preference: a model told it is a fully capable assistant
will claim abilities it does not have, and the user cannot tell the difference
without trying. So the load-bearing test here is the one that checks every
claim against the live registry.
"""

from __future__ import annotations

import pytest

from core.persona import (
    LIMITS,
    Persona,
    build_persona,
    group_capabilities,
)


class _FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return {"tools": list(self._tools)}


class TestGrounding:
    def test_every_claimed_ability_exists_in_the_registry(self):
        """The claim must be checkable, not decorative.

        Anything the persona names as a group has to map back to real tool
        names, so a prompt can never advertise a capability that was removed.
        """
        names = ["computer_screenshot", "voice_speak", "file_read", "shell_exec", "web_fetch", "memory_store"]
        groups = group_capabilities(names)
        claimed = {tool for _, tools in groups for tool in tools}
        assert claimed <= set(names), f"persona claims tools that are not registered: {claimed - set(names)}"

    def test_a_smaller_registry_shrinks_the_claim(self):
        big = group_capabilities(["computer_screenshot", "android_launch_app", "web_fetch"])
        small = group_capabilities(["web_fetch"])
        assert len(small) < len(big)
        assert small[0][0] == "reach the network"

    def test_no_groups_claimed_from_an_empty_registry(self):
        assert group_capabilities([]) == ()

    def test_a_broken_registry_yields_no_claim_rather_than_an_error(self):
        class Broken:
            def list_tools(self):
                raise RuntimeError("registry is down")

        persona = build_persona(registry=Broken())
        assert persona.tool_count == 0
        assert persona.groups == ()
        # It still has to produce a usable prompt.
        assert persona.name in persona.describe()

    def test_the_tool_count_is_the_real_count(self):
        persona = build_persona(registry=_FakeRegistry(["file_read", "file_write", "shell_exec"]))
        assert persona.tool_count == 3
        assert str(persona.tool_count) in persona.describe()


class TestIdentity:
    def test_it_knows_its_name_and_its_master(self):
        persona = build_persona(registry=_FakeRegistry(["file_read"]))
        text = persona.describe()
        assert persona.name in text
        assert persona.master in text
        assert f"{persona.master}'s" in text

    def test_the_master_is_configurable(self, monkeypatch):
        monkeypatch.setenv("HERMUS_MASTER", "Sam")
        assert build_persona(registry=_FakeRegistry([])).master == "Sam"

    def test_the_name_is_configurable(self, monkeypatch):
        monkeypatch.setenv("HERMUS_AGENT_NAME", "ARIA")
        assert build_persona(registry=_FakeRegistry([])).name == "ARIA"

    def test_the_prompt_stays_short(self):
        """It is prepended to every turn, so length is paid per turn."""
        text = build_persona(registry=_FakeRegistry(["computer_screenshot", "voice_speak"])).describe()
        assert len(text) < 2000

    def test_it_is_told_not_to_claim_capabilities_it_lacks(self):
        text = build_persona(registry=_FakeRegistry(["file_read"])).describe()
        assert "Never claim a capability" in text

    def test_it_knows_its_own_limits(self):
        text = build_persona(registry=_FakeRegistry(["file_read"])).describe()
        for line in LIMITS:
            assert line in text
