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

---

## 🚀 Quick Start

HERMUS now has a **fresh-machine installer by default**. It detects the host, checks required tooling, creates an isolated project `.venv`, installs dependencies, and runs the canonical Doctor/bootstrap verification.

### Windows — easiest path

After installing Git (or with Git available through your normal Windows setup):

```powershell
 git clone https://github.com/trmv2007-bot/hermus-agent-free.git
 cd hermus-agent-free
 .\setup.cmd
```

You can also run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The Windows installer uses `winget` when available to install missing Git, Python 3.12 and Node.js LTS. Node/npm are only required by the installer when a JavaScript package manifest exists. Bun is optional and is not installed just because it is available elsewhere.

### Linux / WSL / macOS / Termux

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
./setup.sh
```

On Linux, the installer can install missing host Python/venv/Git/curl/build tooling using the detected package manager. HERMUS then uses a project-local `.venv` rather than system `pip`.

### Verify or repair

```bash
# Windows
.\setup.cmd -VerifyOnly
.\setup.cmd -Repair

# Linux/macOS/Termux
./setup.sh --verify-only
./setup.sh --repair
```

### Start the Gateway

```bash
./hermus-gateway
```

On Windows, use the corresponding project launcher/PowerShell entry point provided by the repository after setup.

### Open Control Room

**http://localhost:8000/control**

---

## 🧠 What is HERMUS?

**HERMUS** is a general-purpose personal AI agent: a persistent system that can understand objectives, plan work, delegate to specialist capabilities, operate tools, verify results, recover from failures, remember what it learns, and safely become more useful over time.

The architecture is inspired by the idea of a persistent personal assistant—a **Jarvis/Fairy-style operating layer** rather than a simple chatbot.

> **Autonomy should mean more useful execution, not fewer safety controls.**

---

## 🚧 Build Status

| Capability | Status |
|---|:---:|
| Verified computer-control execution | ✅ Complete |
| Voice presence + executive integration | ✅ Complete |
| Control Room / system observability | ✅ Complete |
| Learning, memory + reusable skills | ✅ Complete |
| End-to-end autonomy + safety hardening | ✅ Complete |
| Runtime reliability + observability | ✅ Complete |
| Proactive event-driven automation | ✅ Complete |
| Scheduling + time awareness | ✅ Complete |
| Persistent personal context | ✅ Complete |
| Live world awareness | ✅ Complete |
| Advanced long-horizon planning | ✅ Complete |
| Specialist agent ecosystem | ✅ Complete |
| Multimodal intelligence | ✅ Complete |
| Natural conversation + interruption | ✅ Complete |
| Personal operating system | ✅ Complete |
| Self-improving agent | ✅ Complete |
| Distributed HERMUS | ✅ Complete |
| Reliability & recovery | ✅ Complete |
| Fresh-machine installation | ✅ Complete |
| Real-world provider/device deployment | 🔧 Deployment-dependent |

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
hermus doctor
```

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
