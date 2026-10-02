from core.voice.executive_bridge import VoiceExecutiveBridge
from core.voice_presence import VoicePresence


def test_voice_turn_reaches_executive_and_speaker():
    presence = VoicePresence()
    calls = []
    bridge = VoiceExecutiveBridge(
        presence,
        lambda text: {"answer": f"handled: {text}"},
        speaker=lambda text: calls.append(text),
    )
    result = bridge.handle_transcript("s1", "status report")
    assert result.result["answer"] == "handled: status report"
    assert result.spoken is True
    assert calls == ["handled: status report"]
    assert presence.get("s1").turns == 1


def test_muted_session_does_not_execute():
    presence = VoicePresence()
    presence.start("s1")
    presence.set_muted("s1", True)
    bridge = VoiceExecutiveBridge(
        presence,
        lambda _: (_ for _ in ()).throw(AssertionError()),
    )
    result = bridge.handle_transcript("s1", "do it")
    assert result.result["ok"] is False
