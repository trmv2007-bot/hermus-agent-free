from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_nexus_visible_interactions_have_wiring():
    html = (ROOT / "gateway/control.html").read_text(encoding="utf-8")
    js = (ROOT / "gateway/static/nexus.js").read_text(encoding="utf-8")
    css = (ROOT / "gateway/static/nexus.css").read_text(encoding="utf-8")

    required_ids = [
        "send", "voiceButton", "modelRefresh", "modelSave",
        "hermusLogo", "workshopClose", "workshopRefresh", "workshopSave",
        "workshopAsk", "workshopMissionOpen", "workshopSend",
        "paletteClose", "paletteInput", "personalSpaceOpen", "personalSpaceOpenDock",
    ]
    for control_id in required_ids:
        assert f'id="{control_id}"' in html, control_id

    required_wiring = [
        "startVoice",
        "modelRefresh",
        "modelSave",
        "setWorkshop",
        "workshopClose",
        "workshopRefresh",
        "workshopSave",
        "workshopAsk",
        "workshopMissionOpen",
        "workshopSend",
        "paletteClose",
    ]
    for token in required_wiring:
        assert token in js, token

    assert "nexus-mock.js" in html
    assert "visual interaction system" in css


def test_nexus_mock_is_opt_in_and_covers_core_surfaces():
    mock = (ROOT / "gateway/static/nexus-mock.js").read_text(encoding="utf-8")
    assert "params.get('mock') !== '1'" in mock
    for endpoint in [
        "/api/v1/system/health",
        "/models/catalog",
        "/models/selected",
        "/models/health",
        "/presence/kernel",
        "/personal-space",
        "/workshop/snapshot",
        "/workshop/file",
        "/context",
        "/api/v1/console/manifest",
        "/api/v1/commands",
        "/voice/command",
    ]:
        assert endpoint in mock
