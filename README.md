# ⚡ HERMUS Agent Free

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/docs/assets/hermus-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/docs/assets/hermus-light.png">
    <img alt="HERMUS Logo" src="https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/docs/assets/hermus-dark.png" width="200">
  </picture>
</p>

<p align="center">
<strong>🌟 A free, open-source, self-hosted AI agent for coding, research, automation and multi-agent work.</strong><br>
<strong>Build with local models or free-tier providers. Keep your data, tools and runtime under your control.</strong>
</p>

<p align="center">
<a href="https://github.com/trmv2007-bot/hermus-agent-free/actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/trmv2007-bot/hermus-agent-free/test.yml?branch=main&label=CI"></a>
</p>

---

## 🚀 Quick Start

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

Linux / macOS / Termux:

    ./setup.sh

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
When setup is done, `/` opens the companion: one character and one input line. The eleven operator panels of the Control Room live at **http://localhost:8000/control**, one quiet control away.
## 🧠 What is HERMUS?

**HERMUS** is a general-purpose personal AI agent: a persistent system that can understand objectives, plan work, delegate to specialist capabilities, operate tools, verify results, recover from failures, remember what it learns, and safely become more useful over time.

The architecture is inspired by the idea of a persistent personal assistant—a **Jarvis/Fairy-style operating layer** rather than a simple chatbot.

> **Autonomy should mean more useful execution, not fewer safety controls.**

---

## Build status

Every row names the test that backs it. If a claim has no test, it is not here.
The same suite runs with `make test` (~1 min) and on every pull request.

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

## 🛡️ Reliability & Recovery

HERMUS includes retries, circuit breakers, durable idempotency, crash/resume checkpoints, incident tracking, integrity-checked snapshots, recovery/restore controls, resource degradation signals, and distributed lease/fencing support.

The Control Room exposes reliability telemetry and recovery controls so failures can be investigated instead of silently disappearing.

---

## 🏭 Production & Real-World Integration

The repository provides the common contracts and safety boundaries. Third-party accounts, provider-specific credentials, physical devices, mTLS certificates and off-site infrastructure must be supplied by the deployment; HERMUS never invents those secrets.

Remaining deployment validation includes real worker nodes, provider authentication, physical voice devices, off-site backup drills, mTLS and failure-injection testing on the target hardware/network.

See `docs/PRODUCTION_AND_REAL_WORLD_INTEGRATION.md` and `docs/PHASE_18_RELIABILITY_RECOVERY.md`.

The Nexus Control Room also includes a discovery-driven **JARVIS × FAIRY model environment**: models are discovered from configured runtimes, selected by role or Auto mode, validated through the canonical ModelGateway, and never hardcoded into the dashboard. See `docs/JARVIS_FAIRY_ENVIRONMENT_SPEC.md`.

---

## ✨ Features

### 🎯 Core Capabilities

| Feature | Description |
|---------|-------------|
| 🧠 **Autonomous Missions** | Objective-driven planning, execution, verification, repair and proof |
| 💻 **SWE Mode** | Full software-engineering workflow: inspect, edit, build, test, debug, review, package |
| 👥 **Multi-Agent** | Subagents, DAGs, delegation trees and parallel workstreams |
| 🏛️ **AI Counsel** | Multiple agent roles can propose, critique, deliberate, vote and synthesize |
| 🔄 **Self-Improving** | Successful trajectories become reusable skills |
| 🧠 **Project Memory** | SQLite-backed memory with FTS5 retrieval and optional vector search |
| 📚 **Lessons Loop** | Corrections and failures become reusable lessons |
| ✅ **Verification** | Domain-specific verification plus offline evaluation harness |
| 🧪 **Agent Harness** | Run task suites against **any configured model** (API-key/auth hosted providers or local ollama — `hermus harness models`, `--model auto\|groq\|ollama/...`) in isolated workspaces, verify with checks, JSON/markdown reports + multi-model comparison (`hermus harness run <suite>`) |
| ⚡ **Sentinel (always-alert core)** | Event-driven FAIRY layer: reacts to the event bus **inline/instantly** (no polling), salience-scored decisions, LLM triage with any model, never idles — `hermus sentinel run` + autostart via `hermus sentinel install`; measurable via `hermus harness run sentinel` |
| 🔒 **Sandboxing** | Policy-controlled command execution with isolation backends |

### 🌐 Gateway & Integrations

- **CLI** — Full command-line interface
- **Web Dashboard** — Live task progress, agents, telemetry and system state
- **Telegram / Discord / Slack** — Integration capabilities when configured
- **Voice** — Local speech-to-text and text-to-speech capabilities
- **Computer Control** — Browser automation and system interaction

### 🛡️ Safety & Trust

- **Red Line Policy** — Clear boundaries for autonomous actions
- **Approval System** — Scoped grants for yellow-zone actions
- **Emergency Brake** — Immediate stop capability
- **Audit Logs** — Action tracking and review
- **Verification** — completion is not treated as success without appropriate verification
- **Capability Boundaries** — self-improvement cannot silently disable protected safety controls
- **Sandboxing** — Multiple isolation backends where supported

---

## 🎨 Control Room Dashboard

The **Control Room** at `http://localhost:8000/control` is the operational command center:

- **📊 Overview** — System health and event log
- **🎭 Presence** — Agent identity, state, goals and continuity
- **🎤 Voice** — Speech capabilities
- **📋 Jobs** — Queue management and execution tracking
- **🚀 Missions** — Autonomous task management
- **📈 Telemetry** — Live runtime information
- **💻 Computer** — System automation and control
- **🔗 Remote** — External integrations
- **🛡️ Safety** — Red lines, approvals and emergency controls
- **🧰 Reliability** — Incidents, circuits, checkpoints, backups and recovery
- **🧠 Model Core** — Runtime-discovered models, role selection and capability fit
- **✨ Fairy Attention** — Proactive priorities, incidents, tasks and world signals
- **⚙️ Systems** — Subsystem health

---

## 🚀 Usage Examples

```bash
hermus mission start "Build and test a web application that does X"
hermus swe run "Fix the failing tests and package the project"
hermus counsel run "Compare three architectures and synthesize the tradeoffs"
hermus harness run smoke --model mock/mock   # measure the agent on a task suite (offline)
hermus harness models                        # every model it can see (API keys + local + mock)
hermus harness compare <run_a> <run_b>       # A/B two runs
hermus sentinel run --model auto             # always-alert core: instant event reaction, never idles
hermus sentinel install                      # autostart on boot (systemd / Termux / cron)
hermus doctor
```

Harness: `docs/HARNESS.md` · Sentinel: `docs/SENTINEL.md` · Always-on: `docs/ALWAYS_ON.md`

Easy mode: `hermus check` (offline selfcheck + scoreboard) · `hermus harness run` with no args runs every builtin suite · `hermus doctor --harness` for always-on health.

---

## 🔧 Configuration

HERMUS is configured through environment variables. See `.env.example` for available options.

Third-party credentials are deployment-specific and must be supplied by the operator.

---

## 📚 Documentation

| Document | Purpose |
|---|---|
| `QUICKSTART.md` | Installation and CLI cheatsheet |
| `ARCHITECTURE.md` | Canonical architecture reference |
| `LIVING_CONTROL_ROOM.md` | Control Room design and features |
| `RED_LINES.md` | Safety boundaries |
| `AUTONOMY_BOUNDARIES.md` | Autonomy and capability boundaries |
| `CAPABILITY_LEDGER.md` | Capability ledger |
| `docs/PHASE_18_RELIABILITY_RECOVERY.md` | Reliability and recovery |
| `docs/JARVIS_FAIRY_ROADMAP.md` | Long-term roadmap |
| `docs/JARVIS_FAIRY_ENVIRONMENT.md` | JARVIS/Fairy environment direction and dynamic model policy |

---

## 🛠️ Model Providers

HERMUS supports local models, OpenAI-compatible endpoints and configured hosted providers. No provider credentials are bundled with the repository.

---

## 🤝 Contributing

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
./setup.sh
```

For Windows, use `setup.cmd` or `setup.ps1`.

---

## 📜 License

HERMUS Agent Free is released under the **MIT License**. See `LICENSE`.

---

<p align="center">
  <strong>⚡ HERMUS Agent Free — Build, Research, Automate, Remember, Verify, Improve.</strong>
</p>
