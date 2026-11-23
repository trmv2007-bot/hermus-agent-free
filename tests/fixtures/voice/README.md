# Voice fixtures

Checked-in speech so the voice tests never touch a real microphone.

Each file is 16 kHz mono PCM16, generated once with Piper
(`en_US-lessac-medium`, `piper-tts` 1.8.0) and resampled from 22.05 kHz:

| file | words |
| --- | --- |
| `fixture_ask.wav` | "what can you do" |
| `fixture_point.wav` | "where do I change the audio device" |
| `fixture_act.wav` | "rename the screenshots on my desktop by date" |
| `fixture_long.wav` | "I checked the machine and it has nineteen gigabytes free on the system drive and a model download still queued" |
| `fixture_silence.wav` | one second of silence |

Regenerate with `scripts/voice_fixtures.py` (needs `piper-tts` and a voice).
