from pathlib import Path

from core.distributed_transport import EnvelopeSigner, create_envelope
from core.integrations import ExternalIntegrationRegistry
from core.personal_profile import PersonalProfile
from core.voice_stream import VoiceStreamManager


def test_envelope_is_authenticated_and_detects_tampering():
    signer = EnvelopeSigner("test-secret")
    env = signer.sign(create_envelope("a", "b", "job", "runtime.turn", {"text": "x"}, ttl_s=30))
    assert signer.verify(env)
    env.payload["text"] = "tampered"
    assert not signer.verify(env)


def test_integration_registry_filters_secret_metadata():
    reg = ExternalIntegrationRegistry()
    row = reg.configure("email", metadata={"api_key": "secret", "label": "mail"})
    assert row["metadata"] == {"label": "mail"}


def test_personal_profile_persists_explicit_values(tmp_path: Path):
    p = PersonalProfile(tmp_path / "profile.json")
    p.update(name="User", style="direct")
    q = PersonalProfile(tmp_path / "profile.json")
    assert q.snapshot()["name"] == "User"
    assert q.snapshot()["style"] == "direct"


def test_voice_stream_interruption():
    mgr = VoiceStreamManager()
    stream_id = mgr.start("session")["streams"][0]["id"]
    assert mgr.push(stream_id)["streams"][0]["chunks"] == 1
    assert mgr.interrupt(stream_id)["streams"][0]["state"] == "interrupted"
