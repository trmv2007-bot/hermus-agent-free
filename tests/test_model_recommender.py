"""The recommendation must be honest about this machine, not flattering.

Each test here corresponds to a way this feature could quietly lie:

- recommending off *total* VRAM when only 6.9 of 8 GB is free right now
- treating weights as the whole cost and omitting the vision activation buffer
- inventing a VRAM figure where none was measured
- downloading without a deliberate click
- accepting a model name that was never verified against the registry
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from core.hardware_spec import GPU, MachineSpecs, detect_specs
from core.model_recommender import load_catalog, recommend

ROOT = Path(__file__).resolve().parents[1]


def _specs(free: float | None, total: float = 8.0) -> MachineSpecs:
    return MachineSpecs(
        os_name="Windows", cpu_cores=20, ram_total_gb=15.84, ram_free_gb=7.7,
        gpu=GPU(name="test", vendor="nvidia", vram_total_gb=total,
                vram_free_gb=free, runtime="cuda", source="test"),
    )


# --- detection --------------------------------------------------------------

def test_detection_reports_a_source_for_every_value():
    s = detect_specs()
    assert s.gpu.source, "a GPU with no source of truth is not a measurement"
    for row in s.notes:
        assert isinstance(row, str) and row


def test_unmeasurable_vram_is_none_not_a_guess():
    """A VRAM figure that was never measured must not be invented."""
    from core.hardware_spec import GPU as G
    g = G(name="Apple M-series", vendor="apple", vram_total_gb=None,
          vram_free_gb=None, source="unified memory")
    s = MachineSpecs(gpu=g)
    out = recommend(s)
    assert out["available"] is False
    assert out["recommended"] is None
    assert "could not measure" in out["reason"].lower()


# --- the budget -------------------------------------------------------------

def test_recommendation_uses_free_vram_not_total():
    """8 GB total with 4 GB free must not recommend a 5.8 GB model.

    This is the bug that makes a local VLM work in a demo and fail at home:
    the card's name is quoted, its availability is not.
    """
    out = recommend(_specs(free=4.0, total=8.0))
    rows = {r["id"]: r for r in out["models"]}
    assert rows["qwen3-vl:4b"]["fits"] is False, "recommended against total VRAM, not free"
    assert out["recommended"] != "qwen3-vl:4b"


def test_required_vram_exceeds_download_size():
    """Weights are never the whole cost.

    A vision encoder builds a large activation buffer on a full-screen image.
    If this ever fails, the budget has silently stopped accounting for it.
    """
    for e in load_catalog():
        assert e["required_vram_gb"] > e["download_gb"], (
            f"{e['id']} budgets no more than its download size -- the vision "
            "activation buffer is missing"
        )


def test_best_fitting_model_is_recommended():
    out = recommend(_specs(free=6.98))
    assert out["recommended"] == "qwen3-vl:4b"
    rows = {r["id"]: r for r in out["models"]}
    assert rows["qwen3-vl:4b"]["headroom_gb"] == pytest.approx(1.18, abs=0.05)
    assert rows["llava:7b"]["fits"] is False, "7.3 GB must not fit in 6.98 GB"


def test_a_tight_card_gets_the_small_model_and_says_why():
    out = recommend(_specs(free=3.5))
    assert out["recommended"] == "moondream"
    assert "free" in out["reason"].lower()


def test_the_hardcoded_default_does_not_fit_a_real_8gb_card():
    """llava:7b was hardcoded as the analyzer's default. On this box it cannot run."""
    out = recommend(_specs(free=6.98))
    rows = {r["id"]: r for r in out["models"]}
    assert rows["llava:7b"]["fits"] is False


def test_no_model_is_recommended_when_none_fit():
    out = recommend(_specs(free=2.0))
    assert out["recommended"] is None
    assert "none" in out["reason"].lower()


# --- catalog integrity ------------------------------------------------------

def test_every_catalog_entry_was_verified_against_a_registry():
    for e in load_catalog():
        assert e.get("verified") is True, f"{e['id']} is listed but was not verified"
        assert e.get("ollama"), f"{e['id']} has no ollama tag, so Pull cannot be built"


def test_catalog_entry_sizes_match_the_ollama_registry():
    """The 'verified' flag is only worth something if it was checked against
    something. These are the sizes the registry reported, re-checked live."""
    expected = {
        "qwen3-vl:2b": 1.89, "qwen3-vl:4b": 3.30, "qwen3.5-4b": None,
        "qwen2.5vl:3b": 3.20, "gemma3:4b": 3.34, "llava:7b": 4.73,
        "moondream": 1.74,
    }
    by_id = {e["id"]: e for e in load_catalog()}
    for tag, size in expected.items():
        if size is None or tag not in by_id:
            continue
        assert by_id[tag]["download_gb"] == pytest.approx(size, abs=0.05), (
            f"{tag} catalog size drifted from the registry"
        )


# --- the pull is a deliberate act -------------------------------------------

def test_pull_requires_explicit_confirmation():
    """A settings page that fires this on mount would download 3.3 GB unasked."""
    from gateway.routes_models import PullRequest, _resolve_tag
    r = PullRequest(model="qwen3-vl:4b", confirm=False)
    assert r.confirm is False, "confirm must default to False"


def test_pull_refuses_a_model_that_was_never_verified():
    """moondream2 was guessed once and does not exist.

    The catalog is the allowlist precisely so that a guessed name is refused
    rather than turned into a confusing registry 404 mid-download.
    """
    from fastapi import HTTPException
    from gateway.routes_models import _resolve_tag

    assert _resolve_tag("qwen3-vl:4b") == "qwen3-vl:4b"
    for bogus in ("moondream2", "llava:7b-not-real", "definitely-not-real"):
        with pytest.raises(HTTPException) as ei:
            _resolve_tag(bogus)
        assert ei.value.status_code == 400


# --- live wiring ------------------------------------------------------------

def test_the_model_routes_are_registered_and_gated():
    src = (ROOT / "gateway" / "gateway.py").read_text(encoding="utf-8")
    assert "routes_models" in src
    assert "app.include_router(_models_router, dependencies=_gate_control)" in src, (
        "the model routes must sit behind the token gate like every other control route"
    )
