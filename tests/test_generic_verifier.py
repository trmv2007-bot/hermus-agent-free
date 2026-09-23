"""The generic verifier must be able to look at something other than prose.

Before this it ran exactly one check — scanning the worker's own output for error
markers — so a generic mission's outcome could never rise above "the worker said
so, and it read clean".
"""

from __future__ import annotations

from core.verifier_registry import SOURCE_OBSERVED, GenericVerifier


def _verify(tmp_path, **context):
    ctx = {"task": "do the thing", "workspace_dir": str(tmp_path)}
    ctx.update(context)
    return GenericVerifier().verify(ctx)


def test_a_reported_deliverable_that_exists_is_observed_not_quoted(tmp_path):
    (tmp_path / "service.py").write_text("print('real')\n", encoding="utf-8")
    result = _verify(tmp_path, output="created service.py", artifacts=["service.py"])

    assert result.verified is True
    checks = {e["check"] for e in result.evidence}
    assert {"file_exists", "marker_scan"} <= checks
    provenance = result.provenance
    assert provenance["grounded"] is True
    assert provenance["certifiable"] is True
    assert provenance["counts"][SOURCE_OBSERVED] == 1
    assert result.details["deliverables_present"] == 1


def test_a_reported_deliverable_that_is_not_there_fails_the_mission(tmp_path):
    (tmp_path / "service.py").write_text("print('real')\n", encoding="utf-8")
    result = _verify(tmp_path, output="created service.py and app.py", artifacts=["service.py", "ghost.py"])

    assert result.verified is False
    assert any("ghost.py" in e for e in result.errors)
    # the one that exists is still observed; a partial delivery is not a fiction
    assert result.details["deliverables_present"] == 1
    assert result.details["deliverables_missing"] == ["ghost.py"]
    assert result.provenance["grounded"] is True


def test_an_empty_file_is_not_a_deliverable(tmp_path):
    (tmp_path / "notes.md").write_text("", encoding="utf-8")
    result = _verify(tmp_path, output="wrote notes.md", artifacts=["notes.md"])

    assert result.verified is False
    assert result.details["deliverables_empty"] == ["notes.md"]


def test_text_only_success_is_still_ungrounded(tmp_path):
    """Answering a question leaves no artifact; that must read honestly."""
    result = _verify(tmp_path, output="The capital is Python.", artifacts=[])

    assert result.verified is True
    assert result.provenance["grounded"] is False
    assert result.provenance["certifiable"] is False
    assert any("worker's own output text" in w for w in result.warnings)


def test_an_error_marker_fails_even_with_files_on_disk(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    result = _verify(tmp_path, output="done\nTraceback (most recent call last):\n  boom", artifacts=["ok.py"])

    assert result.verified is False
    assert result.details["deliverables_present"] == 1
    assert any("traceback" in e.lower() for e in result.errors)


def test_paths_resolve_against_the_mission_workspace(tmp_path):
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "mod.py").write_text("y = 2\n", encoding="utf-8")

    relative = _verify(tmp_path, output="wrote src/mod.py", artifacts=["src/mod.py"])
    absolute = _verify(tmp_path, output="wrote src/mod.py", artifacts=[str(nested / "mod.py")])

    assert relative.verified is True and absolute.verified is True
    assert relative.details["deliverables_present"] == absolute.details["deliverables_present"] == 1
