# HERMUS

**A self-hosted agent harness.** Give it a task, watch it work, read the diff, see whether it
actually verified the result. Runs on your machine with local models (Ollama, NoLlama, LM Studio)
or any OpenAI-compatible provider you have a key for. MIT licensed, no account, no paywall.

<p align="center">
<a href="https://github.com/trmv2007-bot/hermus-agent-free/actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/trmv2007-bot/hermus-agent-free/test.yml?branch=main&label=CI"></a>
</p>

---

## Quick start

### One-shot full install

HERMUS ships one canonical installer path per platform. The installer obtains the repository, creates the project-local Python environment, installs the required runtime dependencies, installs supported optional integrations, installs the browser runtime used by the web subsystem where supported, initializes the runtime layout, and runs the Doctor/live verification.

It is safe to re-run. It does not delete HERMUS memory, projects, workspace data, logs, credentials or model files.

**It will offer you a brain.** At the end of the install one question asks whether to install Ollama and download `llama3.2:1b` (about 0.6 GB, once, on your machine, no account). Say yes and the first answer comes from a real model. Say no and it offers a free-tier key instead. Decline both and it says plainly that it will answer with the offline stub, and gives you the one command that fixes it. Nothing is downloaded without that answer.

#### Windows 10/11

Run this from PowerShell:

    $p = Join-Path $env:TEMP "hermus-install.ps1"
    Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.ps1" -OutFile $p
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p

The Windows bootstrap uses winget when available to install missing Git, Python and Node.js LTS. The Node runtime is only required when a JavaScript package manifest is present.

#### Linux / WSL

Run:

    curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh -o /tmp/hermus-install.sh
    bash /tmp/hermus-install.sh

The Linux installer supports the package-manager families already handled by setup.sh (apt, dnf, yum, pacman and apk).

#### macOS

Run:

    curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh -o /tmp/hermus-install.sh
    bash /tmp/hermus-install.sh

macOS uses the canonical setup.sh flow and Homebrew when required host tooling is missing.

#### Android / Termux

Run:

    curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh -o /tmp/hermus-install.sh
    bash /tmp/hermus-install.sh

Termux uses pkg for required host packages. Desktop Chromium/Playwright support is intentionally not forced on Termux.

### Already cloned the repository?

Windows:

    .\setup.cmd
    .\.venv\Scripts\python.exe hermus.py gateway start

**Linux / macOS / Termux:**

    ./setup.sh
    ./.venv/bin/python hermus.py gateway start

**Or install it as a package:**

    pip install .
    hermus gateway start

Then open **http://localhost:8000** — that is the harness.

### Verify or repair

Windows:

    .\setup.cmd -VerifyOnly
    .\setup.cmd -Repair

Linux / macOS / Termux:

    ./setup.sh --verify-only
    ./setup.sh --repair

### Start HERMUS

    ./hermus gateway start

Open **http://localhost:8000/** — the first screen asks three questions (what answers you, what it may do, its name), then proves the model works by answering you once, streaming.
When setup is done, `/` opens the harness: the task view described below. The operator surfaces — companion, approvals, the subsystem console — live at **http://localhost:8000/control**, one mode switch away.
## 🧠 What is HERMUS?

**HERMUS** is a general-purpose personal AI agent: a persistent system that can understand objectives, plan work, delegate to specialist capabilities, operate tools, verify results, recover from failures, remember what it learns, and safely become more useful over time.

The architecture is inspired by the idea of a persistent personal assistant—a **Jarvis/Fairy-style operating layer** rather than a simple chatbot.

> **Autonomy should mean more useful execution, not fewer safety controls.**

---

## Build status

Every row names the test that backs it. If a claim has no test, it is not here.
The same suite runs with `make test` on Linux and macOS and with
`py -3 -m pytest tests -q --ignore=tests/eval` on Windows, and on every pull
request. The suite is platform-neutral by construction: browser code is proven by
running the real frontend files in Node from a temporary file, never through a
command line that Windows would refuse to build.

| Capability | Evidence |
|---|---|
| Verified computer-control execution | `tests/test_computer_agent_v2.py` |
| Voice presence + executive integration | `tests/test_voice_executive_bridge.py` |
| Control Room / system observability | `tests/test_control_room_e2e.py` |
| Learning, memory + reusable skills | `tests/test_learning_loop.py` |
| End-to-end autonomy + safety hardening | `tests/test_autonomy_safety_contract.py` |
| Runtime reliability + observability | `tests/test_runtime_health.py` |
| Proactive event-driven automation | `tests/test_proactive.py` |
| Scheduling + time awareness | `tests/test_phase8_scheduler.py` |
| Persistent personal context | `tests/test_phase9_personal_context.py` |
| Live world awareness | `tests/test_world_awareness.py` |
| Long-horizon autonomous loop | `tests/test_autonomous_loop.py` |
| Specialist agent delegation | `tests/test_agent_delegation.py` |
| Multimodal intelligence | `tests/test_phase13_multimodal.py` |
| Natural conversation + interruption | `tests/test_phase14_conversation.py` |
| Personal operating system | `tests/test_phase15_personal_os.py` |
| Self-improving agent | `tests/test_phase16_self_improvement.py` |
| Distributed HERMUS | `tests/test_phase17_distributed.py` |
| Reliability & recovery | `tests/test_phase18_reliability.py` |
| Fresh-machine installation | `tests/test_bootstrap_runtime.py` |
| First run: model resolution, `/welcome`, no fake boot claims | `tests/test_first_run_onboarding.py` |
| Real-world provider/device deployment | deployment-dependent; `tests/test_model_gateway_fallback.py` covers the fallback path only |

---

## Running a task

The home screen is a task view, not a dashboard:

- **Left** — your runs: status, task, steps, age.
- **Middle** — the transcript, one line per event: model turns streaming, `▶ architect: …` /
  `◀ architect: executed · 1 tools · 23.8s` for mission nodes, tool calls, verification and repair
  rounds.
- **Right** — what the run changed (unified diff), and how it was verified.
- **Bottom** — the composer: task, target, model, Run. Ctrl+Enter starts it.

A run either produces a diff you can read and a verdict you can trust, or it says plainly that it
changed nothing and that nobody verified it. Those are different sentences on purpose.

**The agent cockpit is still there**, one click away in the header (or at `/control`): presence,
approvals and red-line policy, the capability ledger, memory, and the 59-panel subsystem console.
That is the ops room; the harness is where work happens.

---

## Choosing a model

Three ways, in order of convenience:

1. **The Model Core panel** in the cockpit — pick a model per role (`default`, `reasoning`,
   `vision`, `coding`, `background`, `doctor`, `voice`), or leave a role on `auto`.
2. **The Settings panel** in Systems — writes `HERMUS_MODEL` (and port, sandbox limits, log level)
   into `.env` for you. Restart the gateway to apply.
3. **The CLI** — `hermus multikey add --provider groq --key …`, or a local engine with
   `hermus engine`.

Local models need nothing but Ollama running. Provider keys in the environment (`OPENAI_API_KEY`,
`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, …) are discovered automatically. Nothing is
bundled with the repository.

---


### What the suite cannot cover

- **Provider end-to-end.** CI has no API keys. A turn against your provider is the first thing to
  try after setup; `hermus multikey health` shows key and model health.
- **Host computer control.** The controller is tested against a simulated display; the host test
  skips without `pyautogui` and a real display.
- **Android control.** The transports and the on-device loop are proven against a simulated
  device; a physical handset is untested.
- **Off-site operations.** mTLS between nodes, backup/restore drills and failure injection on real
  hardware are deployment work.

---

## Safety

Autonomy means more execution, not fewer brakes. `RED_LINES.md` and `policies/red_lines.json` define
green / yellow / red actions; yellow actions need a scoped grant you can see and revoke in the
cockpit's Approvals and Safety tabs; an emergency stop is always one click away. Commands run
through a sandbox with a policy-controlled backend (`auto`, `local`, `docker`, `podman`, `off`).
Memory and the sandbox limits are configurable from the dashboard.

See [`RED_LINES.md`](RED_LINES.md) and [`AUTONOMY_BOUNDARIES.md`](AUTONOMY_BOUNDARIES.md).

---

## CLI cheatsheet

```bash
hermus doctor                          # health and installation check
hermus check --no-ping                 # offline selfcheck with a scoreboard
hermus harness run smoke --model mock/mock   # measure the agent on a task suite
hermus multikey health                 # keys, rate limits, discovered models
hermus mission start "ship the thing"  # objective-driven run with verification
hermus sentinel run --model auto       # always-alert event reactor
hermus tools | hermus powers | hermus safety report
```

Windows: prefix with `.\.venv\Scripts\python.exe ` or use `.\setup.cmd` wrappers.

---

## Documentation

| Document | Purpose |
|---|---|
| `QUICKSTART.md` | install, verify, repair |
| `ARCHITECTURE.md` | what owns what, and the gates that enforce it |
| `docs/HARNESS.md` | using Hermus as a measurable agent harness |
| `RED_LINES.md` / `AUTONOMY_BOUNDARIES.md` | what it may do without asking |
| `CAPABILITY_LEDGER.md` | powers it has, lacks, and what each would need |
| `docs/archive/README.md` | the old design and phase notes, kept for the record |

---

## License

MIT. See [`LICENSE`](LICENSE).
