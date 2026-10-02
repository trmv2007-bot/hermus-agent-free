from pathlib import Path

from core.multimodal import MultimodalIntelligence
from core.world_model import WorldModel


def test_multimodal_image_uses_existing_vision_tool_and_records_evidence(tmp_path, monkeypatch):
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-real-image")
    world = WorldModel()
    layer = MultimodalIntelligence(world=world, workspace_root=tmp_path)

    monkeypatch.setattr(
        "tools.vision.vision_analyze",
        lambda path, prompt, model: {
            "success": True,
            "description": "A login screen with a sign-in button",
        },
    )
    result = layer.analyze_image(image, prompt="read the UI")
    assert result["success"] is True
    assert result["confidence"] > 0
    assert world.recent_events(1)[0].event_type == "multimodal_observation"


def test_multimodal_rejects_paths_outside_workspace(tmp_path):
    layer = MultimodalIntelligence(workspace_root=tmp_path)
    outside = Path(tmp_path).parent / "outside.txt"
    outside.write_text("secret")
    try:
        layer.analyze_document(outside)
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("outside-workspace path was accepted")


def test_multimodal_document_uses_canonical_document_ingest(tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("Phase 13 multimodal evidence")
    layer = MultimodalIntelligence(world=WorldModel(), workspace_root=tmp_path)
    result = layer.analyze_document(doc)
    assert result["success"] is True
    assert "Phase 13" in result["observation"]
