"""The ears, over HTTP, with real audio.

The unit test in test_voice_live.py proves core/voice.py transcribes. This
proves the route does, because the gap between those two is where the bugs
actually lived: base64 handling, sample-rate validation, and the fact that
audio too short for one recogniser frame crashes out of C++ with no Python
traceback and surfaces as an opaque 500.

Real synthesized speech, not fixtures. A negative control is included because
"the route returns 200" and "the route heard the right words" are different
claims, and a transcription endpoint that transcribes nothing also returns 200.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

AUDIO = Path(r"C:\Users\rishi\AppData\Local\Temp\voicebench")
BASE = "http://127.0.0.1:8000"

pytestmark = pytest.mark.skipif(
    not AUDIO.exists(), reason="voice fixtures are not present on this machine"
)


def _post(path: str, payload: dict, timeout: int = 180) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _pcm(name: str) -> tuple[str, int]:
    with wave.open(str(AUDIO / f"{name}.wav"), "rb") as w:
        return base64.b64encode(w.readframes(w.getnframes())).decode(), w.getframerate()


def test_hears_real_speech_and_finds_the_wake_word():
    audio, rate = _pcm("wake_jarvis")
    status, body = _post("/voice/hear", {"audio": audio, "sample_rate": rate})

    assert status == 200, body
    assert body["ok"] is True
    assert body["hearable"] is True
    # The recogniser mishears "hey" as "hay". Asserting an exact transcript
    # would make this test fail on a spelling it does not care about; the
    # words that carry the meaning are "jarvis" and "time".
    assert "jarvis" in body["text"].lower()
    assert "time" in body["text"].lower()
    assert [hit["keyword"] for hit in body["wake"]] == ["HEY JARVIS"]


def test_ordinary_speech_is_transcribed_but_does_not_wake():
    """The negative control. A spotter that fires on everything looks perfect."""
    audio, rate = _pcm("negative")
    status, body = _post("/voice/hear", {"audio": audio, "sample_rate": rate})

    assert status == 200, body
    assert "light" in body["text"].lower()
    assert body["wake"] == []


@pytest.mark.parametrize(
    ("label", "payload", "expected"),
    [
        ("missing audio", {}, 400),
        ("not base64", {"audio": "!!!definitely not base64!!!"}, 400),
        # Decodes to three bytes. Used to be an opaque 500 raised from inside
        # the recogniser, which is what this case is here to prevent.
        ("truncated data url", {"audio": "data:audio/wav;base64,AAAA"}, 400),
        ("impossible sample rate", {"audio": "AAAA" * 400, "sample_rate": 99999999}, 400),
    ],
)
def test_bad_requests_are_refused_with_a_message_not_a_crash(label, payload, expected):
    status, body = _post("/voice/hear", payload, timeout=30)

    assert status == expected, f"{label}: got {status} {body}"
    assert body.get("error"), f"{label}: refused without saying why"


def test_status_reports_the_ears_separately_from_the_mouth():
    """Speakable and hearable come from different subsystems.

    Collapsing them into one flag is how a room ends up with a working
    microphone button behind a dead speech model, or vice versa.
    """
    with urllib.request.urlopen(BASE + "/voice/status", timeout=60) as response:
        body = json.loads(response.read().decode())

    assert "ears" in body, "status does not report the ears at all"
    assert isinstance(body["ears"]["available"], bool)
    if not body["ears"]["available"]:
        # Unavailable must come with a reason, or a person cannot act on it.
        assert body["ears"]["reason"]
