from core.voice_presence import VoicePresence


def test_voice_presence_lifecycle():
    presence = VoicePresence()
    session = presence.start("s1", source="browser")
    assert session.active is True
    presence.record_input("s1")
    presence.record_output("s1")
    presence.set_muted("s1", True)
    snapshot = presence.snapshot("s1")
    row = snapshot["sessions"][0]
    assert row["turns"] == 1
    assert row["muted"] is True
    assert presence.stop("s1") is True
    assert presence.get("s1").active is False
