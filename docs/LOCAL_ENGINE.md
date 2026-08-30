# Local engine: hardware-aware routing, NoLlama and the Hermus doctor

Hermus runs whatever local model the machine can actually drive. This document
describes the three pieces that make that work, and the boundaries each of them
has.

| Piece | File | Job |
|---|---|---|
| Hardware router | [`core/accelerators.py`](../core/accelerators.py) | Detect NPU/GPU, decide *which engine* serves *which role* |
| NoLlama manager | [`core/nollama.py`](../core/nollama.py) | Install the [NoLlama](https://github.com/aweussom/NoLlama) server, download OpenVINO models on demand, start/stop, health |
| Hermus doctor | [`core/doctor.py`](../core/doctor.py) | Use the small local model to diagnose *Hermus itself*, then report to the user |
| HTTP surface | [`gateway/routes_engine.py`](../gateway/routes_engine.py) | `/engine/*`, `/events/recent`, `/doctor/*` |
| UI | `gateway/dashboard.html` | System Overview engine card, "Local AI Engine" pane, "Hermus Doctor" pane |

---

## 1. Why two engines

Ollama cannot target an Intel NPU at all, and it has no Intel path for local
vision models. NoLlama is Intel-only (NPU, Arc iGPU, Arc dGPU, CPU) and speaks
both the OpenAI and the Ollama API. So neither engine covers every machine:

| Hardware found | Mode | Reasoning (heavy generative) | Background (continuous, low-intensity) |
|---|---|---|---|
| NPU **+** GPU | `pipelined` | GPU (Ollama for NVIDIA/AMD, NoLlama for Intel) | **NPU** via NoLlama |
| NPU only | `npu_only` | NoLlama / NPU | NoLlama / NPU |
| GPU only | `gpu_only` | Intel → NoLlama, else Ollama | same engine |
| CPU only | `cpu_only` | NoLlama if a downloaded OpenVINO model exists, else Ollama | same |
| `HERMUS_LOCAL_ENGINE=off` | `disabled` | — | — |

That split is the whole point of `pipelined`: the NPU runs cool and silent while
it does the work that never stops — Whisper voice transcription and indexing —
and the GPU is left free for the agent's token-heavy turns, where speed is what
the user feels.

Roles are a fixed vocabulary, so callers never guess:

* `reasoning` — agent turns, code generation, query answering (max token speed).
* `background` — voice transcription, indexing passes, summaries.
* `doctor` — the small model that diagnoses Hermus (see §4).
* `vision` — image understanding.

Statuses are fixed too: `ready`, `needs_model`, `needs_install`, `unavailable`,
`not_applicable`. Nothing in this subsystem reports a "processing" state — every
long job (download, install) has terminal states, so the UI never polls forever.

NPU assignments carry two documented limits, taken from NoLlama's own docs: no
tool-calling and no vision on the NPU, and a 4096-token prompt cap. That is why
the NPU never gets `reasoning` on a machine that also has a GPU.

---

## 2. Installing and downloading

`setup.sh` installs **runtime dependencies only**. It never pulls model weights,
and it never installs NoLlama. A fresh clone stays small; the multi-GB
downloads happen when a human clicks for them.

```bash
hermus engine status                 # hardware, plan, what is missing
hermus engine install                # git clone + venv, no weights
hermus engine models                 # catalog + what is already on disk
hermus engine download minicpm       # dashboard does the same thing
hermus engine start                  # then: dashboard → Local AI Engine
hermus engine stop
```

The same actions are buttons in the dashboard (**Local AI Engine** pane), and
the **System Overview** shows a one-line card while the engine is missing or
unreachable, with the single action that fixes it. Once the engine reports
`ready`, the banner disappears — that card is a to-do list, not a decoration.

Model catalog ids: `minicpm`, `npu-chat`, `npu-fast`, `smollm3`, `canary`,
`vision`, `minicpm-vision`, `whisper-base`. Sizes and target devices come from
`MODEL_CATALOG` in `core/nollama.py`; `recommended_model()` picks the one model
this machine is missing for its routed plan. A directory is only counted as
installed when `openvino_model.xml` **and** a `openvino_model.bin` at least as
large as the XML declares are present — a half-finished download is reported as
incomplete rather than silently served.

`MiniCPM5-1B-int4-g128-ov` is **not** in NoLlama's own model registry, so Hermus
resolves the OpenVINO export itself (`HarmenWessels/MiniCPM5-1B-int4-g128-ov`,
Apache-2.0, ~755 MB of weights). On an NPU-only machine `recommended_model()`
returns `npu-fast` instead: the NPU compiler rejects group-quantized INT4 and
needs a channel-wise (`-int4-cw-ov`) build.

`engine start` and the dashboard's **Start engine** button now auto-resolve the
download directory from Hermus's own catalog, so a custom export such as
MiniCPM does not need to be added to NoLlama's internal `models.json` first.
That is why the flow is *download → start* (not *download → edit config*), and
why a CPU-only box with MiniCPM downloaded routes the Hermus doctor to
`nollama/MiniCPM5-1B-int4-g128-ov` instead of `ollama/llama3.1:8b`.

Port plan: the gateway owns `8000`, so NoLlama runs on `8010`
(`HERMUS_NOLLAMA_PORT`) with `--ollama-port 0`, so it never shadows the gateway
or a real Ollama. `start()` refuses to bind the gateway port.

---

## 3. Voice transcription on the NPU

`tools/voice.py` sends microphone audio to the engine that owns the
`background` role. On an Intel NPU box with `whisper-base` downloaded, voice
commands transcribe on the NPU while the GPU stays free for the agent. Every
refusal — CPU-only machine, engine down, no Whisper download, or a NoLlama build
without an audio route (it answers 404/405) — falls back to faster-whisper on
the CPU and reports why in `local_engine_error`. `HERMUS_STT_BACKEND=cpu` pins
the CPU path; `=nollama` requires the engine instead of falling back.

`GET /speech/transcription/status` reports which backend is live.

**One honest caveat:** the request goes to the OpenAI-standard audio route
(`POST /v1/audio/transcriptions`). NoLlama documents Whisper transcription
support and ships pre-quantized Whisper models, but the exact route was not
verified against a live server here — hence the 404/405 fallback, which is
covered by `tests/test_voice_routing.py`.

**Embeddings are not routed to NoLlama.** It serves chat, vision and Whisper; it
has no embeddings endpoint. `core/embeddings.py` therefore keeps using Ollama's
`/api/embeddings` when reachable and a deterministic hash fallback otherwise.
Indexing *work* is treated as background work, but the vectors are not produced
on the NPU today.

---

## 4. The Hermus doctor

The small local model is not a user-facing assistant. It is Hermus's own doctor:
it inspects Hermus's failures, reaps work that is stuck, and then tells the user
what went wrong and how to manage it.

`HermusDoctor.run()` collects from `core/diagnostics`, `core/watchdog`,
`core/run_events`, `gateway/queue`, engine state and the lesson store; finds work
stuck longer than `doctor_stuck_minutes`; matches known error signatures; and —
when a local model is reachable — asks it for triage. Because the model is small
(1–3 B), it may look things up online when it does not recognise a failure
(`doctor_ask_internet`, via `tools/web_search`). Auto-runs are rate-limited by
`doctor_cooldown_minutes` and `doctor_daily_cap`.

If the routing plan says (or a downloaded OpenVINO model implies) the doctor
should use NoLlama, Hermus will start the engine and wait briefly for it if a
download is already on disk — so a box that has MiniCPM but has not been
explicitly *started* still uses it instead of silently dropping to
`ollama/llama3.1:8b`. If the local engine truly cannot run (not installed,
model missing, server won't start), the doctor uses any configured API provider
instead, and only if there is none does it fall back to a deterministic triage.
When no OpenVINO model is on disk, it falls back to Ollama on CPU.

The report is Markdown: overall status, the engine line, a numbered "what went
wrong and how to manage it" plan, severity counts, then one block per finding
with category, evidence and fixes. Severity vocabulary is deliberate — `critical`
is reserved for critical, so users do not learn to ignore it.

```bash
hermus doctor                          # inspect only
hermus doctor --self-repair            # reap stuck work, then report
hermus doctor --self-repair --no-llm   # deterministic triage, no model call
```

Or in the dashboard's **Hermus Doctor** pane, which shows findings, stuck work
and past reports. `GET /doctor/reports/{id}?fmt=md` returns the Markdown.

---

## 5. HTTP surface

| Route | Purpose |
|---|---|
| `GET /engine/status` | plan, per-role assignment, engine health, recommended model |
| `POST /engine/refresh` | re-probe hardware now |
| `POST /engine/nollama/{install,start,stop}` | manage the server |
| `GET /engine/models` | catalog + installed models |
| `POST /engine/models/download` | start a download |
| `GET /engine/downloads`, `GET /engine/downloads/{job_id}`, `POST …/cancel` | progress with terminal states |
| `GET /events/recent?limit=` | Live Telemetry feed |
| `GET /doctor/status`, `POST /doctor/run`, `POST /doctor/reap` | doctor |
| `GET /doctor/reports`, `GET /doctor/reports/{id}` | past reports |

Blocking work runs through `asyncio.to_thread` so a slow install or download
never stalls the event loop.

---

## 6. Configuration

| Key | Default | Meaning |
|---|---|---|
| `HERMUS_LOCAL_ENGINE` | `auto` | `auto` / `pipelined` / `npu_only` / `gpu_only` / `cpu_only` / `off` |
| `HERMUS_NOLLAMA_PORT` | `8010` | NoLlama port (never the gateway's `8000`) |
| `HERMUS_NOLLAMA_DIR` | `~/.hermus/nollama` | server checkout + venv |
| `HERMUS_NOLLAMA_MODELS` | `~/models` | OpenVINO model directories |
| `HERMUS_NOLLAMA_STATE` / `_LOG` | `data/nollama_state.json`, `data/nollama.log` | runtime state and log |
| `HERMUS_NOLLAMA_AUTOSTART` | `0` | start the engine with the gateway |
| `HERMUS_NOLLAMA_NPU_MODEL` / `_GPU_MODEL` / `_VISION_MODEL` | see `core/config.py` | per-role model refs |
| `HERMUS_DOCTOR_ENABLED` / `_AUTO` | `1` / `0` | doctor on / scheduled self-checks |
| `HERMUS_DOCTOR_INTERNET` | `1` | let the small model look failures up online |
| `HERMUS_DOCTOR_MODEL` | empty → routed model | override the doctor model |
| `HERMUS_DOCTOR_COOLDOWN_MIN` / `_DAILY_CAP` | `15` / `12` | auto-run limits |
| `HERMUS_DOCTOR_STUCK_MIN` | `20` | work older than this is "stuck" |
| `HERMUS_DOCTOR_REPORTS` | `data/doctor` | report storage |
| `HERMUS_STT_BACKEND` | `auto` | `auto` / `nollama` / `cpu` for transcription |

---

## 7. Tests

```bash
python -m pytest tests/test_local_engine_routing.py tests/test_nollama_manager.py \
  tests/test_hermus_doctor.py tests/test_engine_routes.py tests/test_voice_routing.py \
  tests/test_db_lifecycle.py -q
```

The routing table, the download state machine, the doctor's severity rules, the
HTTP surface and the transcription fallback are all covered. What the tests
cannot cover here is a real NPU: the probes are injectable
(`core/accelerators.DEFAULT_PROBES`), so the NPU and Arc paths are exercised with
synthetic hardware snapshots rather than with the devices themselves.
