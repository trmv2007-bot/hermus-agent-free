from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nexus_uses_one_persistent_conversation_session():
    src = (ROOT / "gateway/static/nexus.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('hermus_session_id')" in src
    assert "ensureSession()" in src
    assert "session_id:await ensureSession()" in src
    assert "/voice/command" in src
