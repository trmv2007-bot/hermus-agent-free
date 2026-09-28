"""Tests for vision routing and the vision/capture tool classification.

The claims in ``core.vision_routing`` are claims about *this machine*, and the
first thing worth guarding is that they stay true. Two of them are only true
until somebody runs ``ollama pull``, and a test that notices is worth more than
a comment nobody re-reads.
"""

from __future__ import annotations

import pytest

from core.vision_routing import (
    CAPTURE_TOOLS,
    VISION_TOOLS,
    classify_vision_tools,
    local_vision_support,
    needs_vision,
    plan_vision_route,
)


def _registry_names() -> list[str]:
    try:
        from core.tool_registry import tool_registry

        return list(tool_registry.list_tools().get("tools") or [])
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tool registry unavailable: {exc}")


class TestClassification:
    def test_every_vision_ish_tool_is_accounted_for(self):
        """No tool may be silently unclassified.

        This is the trap the module docstring names. A substring classifier
        over 197 tools for "vision|screen|capture|image" matches 17 names, and
        most are capture-only: ``browser_screenshot``, ``screen_record_*``,
        ``android_get_screen``, ``computer_action``. Treating those as
        "vision" would break screen capture on a box with no vision model in
        order to "fix" understanding.
        """
        names = _registry_names()
        buckets = classify_vision_tools(names)
        assert buckets["unclassified"] == [], (
            f"tools that look vision-ish but are neither vision nor capture: {buckets['unclassified']}. "
            "Add each to VISION_TOOLS or CAPTURE_TOOLS in core/vision_routing.py."
        )

    def test_the_two_sets_are_disjoint(self):
        assert not (VISION_TOOLS & CAPTURE_TOOLS)

    def test_every_declared_tool_exists_in_the_registry(self):
        """A declared tool that no longer exists is a routing hole.

        The registry is the source of truth and it changes when a module is
        removed. A name in the allowlist that is not in the registry means
        either the tool was renamed (so the old name will never match) or the
        allowlist is stale.
        """
        names = set(_registry_names())
        missing = (VISION_TOOLS | CAPTURE_TOOLS) - names
        assert not missing, f"declared but not in the live registry: {sorted(missing)}"

    def test_capture_tools_are_not_vision(self):
        """Capture works on a machine with no vision model. Keep it working."""
        for name in ("browser_screenshot", "screen_record_start", "android_get_screen", "computer_action"):
            buckets = classify_vision_tools([name])
            assert buckets["vision"] == []
            assert buckets["capture"] == [name]

    def test_an_unknown_vision_ish_tool_is_surfaced_not_assumed(self):
        buckets = classify_vision_tools(["screen_magically_understands"])
        assert buckets["vision"] == []
        assert buckets["unclassified"] == ["screen_magically_understands"]


class TestNeedsVision:
    @pytest.mark.parametrize(
        "text",
        [
            "what am I looking at",
            "what's on my screen",
            "read my screen please",
            "can you see the error",
            "what does this say",
            "take a screenshot",
        ],
    )
    def test_recognises_the_obvious_phrasings(self, text):
        assert needs_vision(text) is True

    @pytest.mark.parametrize("text", ["hello there", "what time is it", "run the tests", ""])
    def test_ignores_ordinary_turns(self, text):
        assert needs_vision(text) is False

    def test_a_long_paste_containing_the_word_is_not_a_request(self):
        """A 5000-character document is not a screen-reading request.

        Without the length guard, pasting a document that mentions
        "screenshot" once would send a 5K-token turn down the vision cascade
        and spend a hosted call on it.
        """
        long_text = ("the incident report mentions a screenshot. " * 200)[:5000]
        assert needs_vision(long_text) is False

    def test_a_vision_tool_in_the_offer_is_enough(self):
        assert needs_vision("do the thing", tools=["vision_analyze"]) is True


class TestLocalCapabilityProbe:
    def test_reports_the_real_runtime(self):
        """Live read of this machine's Ollama, not a fixture.

        Skips rather than fails when Ollama is not running, because "no local
        runtime" is a legitimate state on a developer's machine and the
        behaviour it should produce - route away from local - is asserted
        separately below.
        """
        support = local_vision_support(ttl_s=0.0)
        assert "reachable" in support
        assert "vision_models" in support
        if support["reachable"]:
            for entry in support["models"]:
                assert isinstance(entry["vision"], bool)


class TestRouting:
    TIERS = [
        "groq/openai/gpt-oss-120b",
        "google/gemini-2.5-flash",
        "ollama/spark-x2.5-4b-q4",
    ]

    def test_a_plain_turn_is_not_routed_anywhere(self):
        route = plan_vision_route("hello there", tiers=self.TIERS)
        assert route.plan == "not_a_vision_request"
        assert route.needs_vision is False

    def test_local_tier_is_refused_when_it_cannot_see(self):
        """The decision the module exists to make.

        On this machine no local model advertises the ``vision`` capability, so
        the local tier must be marked unusable with a reason rather than being
        tried and failing at the provider with an opaque 400.
        """
        route = plan_vision_route("what am I looking at?", tiers=self.TIERS)
        local = [t for t in route.tiers if t.provider == "ollama"]
        assert local, "the local tier should appear in the plan, marked unusable"
        assert local[0].usable is False
        assert "vision" in local[0].reason

    def test_hosted_tiers_stay_usable_and_ordered(self):
        route = plan_vision_route("what am I looking at?", tiers=self.TIERS)
        hosted = [t.model for t in route.tiers if t.usable]
        assert hosted == ["groq/openai/gpt-oss-120b", "google/gemini-2.5-flash"]
        assert route.plan == "cascade"

    def test_no_usable_tier_is_reported_as_unroutable(self):
        route = plan_vision_route(
            "what am I looking at?", tiers=["ollama/spark-x2.5-4b-q4", "ollama/qwen3:4b"]
        )
        assert route.plan == "unroutable"
        assert all(not t.usable for t in route.tiers)

    def test_the_plan_carries_its_own_evidence(self):
        """The numbers behind the decision travel with it.

        Without this, "why did it route to Groq?" is unanswerable except by
        re-running the probe by hand, which is the situation the module was
        written to prevent.
        """
        route = plan_vision_route("what am I looking at?", tiers=self.TIERS)
        assert "local_models" in route.evidence
        assert "local_vision_models" in route.evidence
        payload = route.to_dict()
        assert payload["needs_vision"] is True
        assert payload["tiers"]

    def test_provider_split_does_not_mangle_slashed_model_ids(self):
        """``google/gemma-3-27b-it`` must not become provider ``google/gemma-3-27b-it``.

        Splitting on every slash calls the provider a model name and then
        treats ``google`` as a local runtime, which is the wrong answer to give
        about a hosted model.
        """
        from core.vision_routing import _split_ref

        assert _split_ref("google/gemma-3-27b-it") == ("google", "gemma-3-27b-it")
        assert _split_ref("ollama/spark-x2.5-4b-q4") == ("ollama", "spark-x2.5-4b-q4")
        assert _split_ref("plainname") == ("", "plainname")
