"""No model may be named in the code that decides which model to use.

The local tier is discovered, not declared. Every hardcoded model name that
ever sat in this repo was a liability: `llava:7b` was the default for the
video analyzer and is not installed on this machine, so screen_watch,
screen_verify, screen_analyze and screen_understand were all wired to something
that could not run -- and failed with "model not found", which points the
reader at installing that model rather than at the fact that the decision
should not have been hardcoded at all.

These tests enforce the rule mechanically, so the next person who adds
`model = "qwen-something"` to a routing function gets a failing test instead
of a machine that only works on one setup.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Files whose job is to pick a model. Comments and docstrings are allowed to
# name models -- recording why a decision was made is the point of
# core/vision_routing.py's header -- but code may not.
ROUTING_FILES = [
    "core/vision_routing.py",
    "core/computer/video_analyzer.py",
    "core/config.py",
    "core/llm.py",
    "core/confidence.py",
]

# A model reference in code: a quoted string that looks like a model tag.
# Deliberately narrow so ordinary strings are not flagged.
MODEL_TAG = re.compile(
    r"""["'][^"']*(?:qwen|llava|llama|mistral|phi|gemma|spark|deepseek|moondream|minicpm|internvl|nemotron)"""
    r"""[^"']*:\s*[\w.-]+["']""",
    re.I,
)


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Every string constant in the file, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("rel", ROUTING_FILES)
def test_no_model_tag_is_hardcoded_in_routing_code(rel: str) -> None:
    path = ROOT / rel
    offenders = [
        f"{rel}:{line}: {value!r}"
        for line, value in _string_literals(path)
        if MODEL_TAG.search(value)
    ]
    assert not offenders, (
        "a model name is hardcoded in routing logic -- resolve it from the "
        "runtime capability list instead:\n  " + "\n  ".join(offenders)
    )


def test_the_local_vision_decision_defaults_to_the_capability() -> None:
    """Installing a multimodal local model must need no code change.

    `trust_local` used to default to False, which meant the capability check
    ran, found a vision model, and then the answer was discarded. A machine
    with a perfectly good local VLM would still have been sent to a hosted
    tier. The default is now None, meaning "ask the machine".
    """
    import inspect

    from core.vision_routing import plan_vision_route

    default = inspect.signature(plan_vision_route).parameters["trust_local"].default
    assert default is None, f"trust_local defaults to {default!r}, so local vision is opted out by default"


def test_a_multimodal_local_model_wins_without_being_named(monkeypatch) -> None:
    """The behaviour the whole design rests on, asserted directly.

    A local runtime reporting one vision-capable model must route to it, with
    no name in this test that the product could be reading.
    """
    from core import vision_routing as vr

    monkeypatch.setattr(
        vr,
        "local_vision_support",
        lambda *_a, **_k: {
            "reachable": True,
            "models": [{"name": "whatever-the-user-installed", "vision": True, "capabilities": ["vision", "completion"]}],
            "vision_models": ["whatever-the-user-installed"],
            "error": "",
        },
    )

    route = vr.plan_vision_route("what does this screenshot show?", tools=["screen_analyze"])
    assert route.needs_vision is True
    assert route.plan == "local", route.plan
    assert route.tiers and route.tiers[0].model.endswith("whatever-the-user-installed")


def test_no_vision_capable_local_model_still_falls_through(monkeypatch) -> None:
    """Capability-driven means capability-gated: no vision model, no local plan."""
    from core import vision_routing as vr

    monkeypatch.setattr(
        vr,
        "local_vision_support",
        lambda *_a, **_k: {
            "reachable": True,
            "models": [{"name": "text-only-4b", "vision": False, "capabilities": ["completion"]}],
            "vision_models": [],
            "error": "",
        },
    )

    route = vr.plan_vision_route("what does this screenshot show?", tools=["screen_analyze"])
    assert route.plan != "local", "routed to a local tier that cannot see"


def test_the_video_analyzer_names_no_default_model() -> None:
    """`llava:7b` was the default here, and it is not installed on this box."""
    import inspect

    from core.computer.video_analyzer import OllamaVisionModel

    src = inspect.getsource(OllamaVisionModel)
    assert "llava:7b" not in src.split('"""')[-1], "llava:7b is back as a default"

    adapter = OllamaVisionModel()
    assert adapter.model is None, "a model was baked in at construction"
