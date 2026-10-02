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
<a href="https://github.com/trmv2007-bot/hermus-agent-free/stargazers"><img src="https://img.shields.io/github/stars/trmv2007-bot/hermus-agent-free?style=for-the-badge&color=58a6ff" alt="Stars"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT"></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg?style=for-the-badge&logo=python" alt="Python"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/actions"><img src="https://img.shields.io/github/actions/workflow/status/trmv2007-bot/hermus-agent-free/test.yml?branch=main&style=for-the-badge" alt="CI"></a>

</p>

---

## 🚀 Quick Start

Get Hermus running in 3 simple steps:

### 1️⃣ Clone & Install
```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
./setup.sh
```

### 2️⃣ Start the Gateway
```bash
./hermus-gateway
```

### 3️⃣ Open Control Room
👉 **[http://localhost:8000/control](http://localhost:8000/control)**

---

## 🧠 What is HERMUS?
## 🚧 Build Status

| Phase | Capability | Status |
|---:|---|:---:|
| 1 | Verified Computer-Control Execution | ✅ Complete |
| 2 | Voice Presence + Executive Integration | ✅ Complete |
| 3 | Control Room / System Observability | ✅ Complete |
| 4 | Learning, Memory + Reusable Skills | ✅ Complete |
| 5 | End-to-End Autonomy + Safety Hardening | ✅ Complete |
| 6 | Runtime Reliability + Observability | ✅ Complete |
| 7 | Proactive Event-Driven Automation | ✅ Complete |
| 8 | Scheduling + Time Awareness | ✅ Complete |
| 9 | Persistent Personal Context | ✅ Complete |
| 10 | Live World Awareness | ✅ Complete |
| 11 | Advanced Long-Horizon Planning | ✅ Complete |
| 12 | Specialist Agent Ecosystem | ✅ Complete |
| 13 | Multimodal Intelligence | ✅ Complete |
| 14 | Natural Conversation + Interruption | ✅ Complete |
| 15 | Personal Operating System | ✅ Complete |
| 16 | Self-Improving Agent | ✅ Complete |
| 17 | Distributed HERMUS | ✅ Complete |


**HERMUS** is being built as a general-purpose personal AI agent: a persistent system that can understand objectives, plan work, delegate to specialist capabilities, operate tools, verify results, recover from failures, remember what it learns, and safely become more useful over time.

The architecture is inspired by the idea of a persistent personal assistant—a **Jarvis/Fairy-style operating layer** rather than a simple chatbot.

> **Autonomy should mean more useful execution, not fewer safety controls.**

---

## 🚀 Current Build Status

The roadmap has progressed through **Phase 17 — Distributed HERMUS**.

| Phase | Capability | Status |
|---|---|---|
| **1** | Verified computer-control execution loop | ✅ Complete |
| **2** | Voice presence + executive integration | ✅ Complete |
| **3** | Control Room / system observability | ✅ Complete |
| **4** | Learning, memory, reusable skills | ✅ Complete |
| **5** | End-to-end autonomy + safety hardening | ✅ Complete |
| **6** | Runtime reliability + observability | ✅ Complete |
| **7** | Proactive, event-driven automation | ✅ Complete |
| **8** | Scheduling + time awareness | ✅ Complete |
| **9** | Persistent personal context | ✅ Complete |
| **10** | World awareness / live World Model | ✅ Complete |
| **11** | Advanced long-horizon planning | ✅ Complete |
| **12** | Specialist agent ecosystem | ✅ Complete |
| **13** | Multimodal intelligence | ✅ Complete |

### Phase 7 highlights

HERMUS can now react to canonical system events using **explicit, persistent automation rules**.

**Event → Rule → Eligibility → Job Queue → Mission Runtime → Safety/Approval → Execute → Verify → Learn**

- Event-driven triggers
- Persistent automation rules
- Explicit enable/disable state
- Event payload filtering
- Cooldowns and optional fire limits
- Safe action allowlisting
- Queue-based execution
- Canonical EventBus integration
- Automation management API
- Runtime correlation and auditability

Proactive rules submit work to the existing runtime; they do not create a privileged execution path.

### Phase 13 — Multimodal Intelligence

HERMUS can now turn visual and document observations into structured evidence:

- Image analysis through the existing local vision/ModelGateway path
- PDF and office-document understanding through canonical document ingestion
- Browser screenshot + visual state analysis
- Confidence, provenance, modality and artifact metadata
- Multimodal evidence persisted in the World Model
- Workspace-scoped multimodal file access
- Multimodal status exposed to the Control Room/gateway

Visual evidence can inform planning and verification without bypassing existing safety, approval or permission controls.

### Phase 14 — Natural Conversation + Interruption

HERMUS now supports conversational control over long-running work:
- Bounded conversation sessions and follow-up context
- Session-to-run correlation for queued and WebSocket work
- Mid-run steering and redirect instructions
- Cooperative interruption/cancellation through the canonical RunBus/runtime
- Background completion/error/cancellation notifications
- WebSocket steer / redirect controls
- Voice output interruption generations and /voice/interrupt
- Existing SSE/WS progress events remain the source of live run state

See docs/PHASE_14_NATURAL_CONVERSATION.md and the master roadmap.
### Phase 15 — Personal Operating System

HERMUS now has a unified personal control plane:
- Durable tasks with priorities, areas, projects, due dates and notes
- Personal OS snapshots and generated briefings
- Goals, projects and focus from persistent Personal Context
- Schedules and proactive automations visible in one operating view
- Live World Model state included in the operating snapshot
- Task execution routed through JobQueue and the canonical runtime
- Gateway endpoints for task and briefing management

See `docs/PHASE_15_PERSONAL_OS.md` and the master roadmap.

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

- **CLI** - Full command-line interface
- **Web Dashboard** - Live task progress, agents, telemetry, reasoning
- **Telegram** - Mobile and desktop integration
- **Discord** - Server and bot integration
- **Slack** - Workspace webhook support
- **Voice** - Local speech-to-text and text-to-speech
- **Computer Control** - Browser automation and system interaction

### 🎤 Presence & Interaction

- **Voice integration** — speech input/output connected to the executive lifecycle
- **Computer control** — observe → act → verify → recover
- **CLI** — direct terminal interaction
- **Web Control Room** — live operational visibility
- **Gateway APIs** — programmatic access to HERMUS capabilities

### ⚙️ Proactive Intelligence

- Event-driven automation
- Persistent rules
- Cooldowns and fire limits
- Context/payload filters
- Safe queue submission
- Automation lifecycle management
- Stable run correlation
- Structured runtime failure classification

### 🛡️ Safety & Trust

- **Red Line Policy** - Clear boundaries for autonomous actions
- **Approval System** - Scoped grants for yellow-zone actions
- **Emergency Brake** - Immediate stop capability
- **Audit Logs** - Complete action tracking and review
- **Verification** - completion is not treated as success without appropriate verification
- **Capability Boundaries** - self-improvement cannot silently disable protected safety controls
- **Proactive Safety** - automations use the existing queue/runtime instead of directly executing tools
- **Sandboxing** - Multiple isolation backends (Docker, Podman, bubblewrap)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        HERMUS AGENT                             │
├─────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   Gateway   │    │   Mission    │    │   Memory    │      │
│  │   & API     │◄──►│   Engine     │◄──►│   System     │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
│          ▲                  ▲                  ▲                │
│          │                  │                  │                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   CLI       │    │   SWE Mode   │    │   Counsel    │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
│          ▲                  ▲                  ▲                │
│          │                  │                  │                │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                Tool System & Sandbox                   │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────┘
```

## 🤖 Proactive Automation

Automation rules are explicit and **disabled by default**.

```
Canonical Event
      ↓
Automation Rule
      ↓
Filter + Cooldown + Limits
      ↓
Job Queue
      ↓
Mission Runtime
      ↓
Approval + Red Lines
      ↓
Execute → Verify → Learn
```

The realtime gateway supports listing, creating, enabling/disabling and deleting automation rules.

---

## 📡 Runtime Observability

Canonical autonomy runs can carry a stable `run_id` and structured health information including state, elapsed time, event count, completion, failure classification and retryability.

---

### 🎨 Control Room Dashboard

The **Control Room** at `http://localhost:8000/control` is your command center:

- **📊 Overview** - System health, capabilities, and event log
- **🎭 Presence** - Agent identity, state, goals, and continuity
- **🎤 Voice** - Speech-to-text and text-to-speech
- **📋 Jobs** - Queue management and execution tracking
- **🚀 Missions** - Autonomous task management
- **📈 Telemetry** - Live event streaming
- **💻 Computer** - System automation and control
- **🔗 Remote** - External integrations
- **🛡️ Safety** - Red lines, approvals, and emergency controls
- **⚙️ Systems** - All subsystems at a glance

---

## 🚀 Usage Examples

### Start an Autonomous Mission
```bash
hermus mission start "Build and test a web application that does X"
```

### Run Software Engineering Workflow
```bash
hermus swe run "Fix the failing tests and package the project"
```

### Use AI Counsel for Complex Decisions
```bash
hermus counsel run "Compare three architectures and recommend the best one"
```

### Interactive Terminal Agent
```bash
hermus
```

### Check System Health
```bash
hermus doctor
```

---

## 🔧 Configuration

Hermus is configured through environment variables. See [`.env.example`](.env.example) for all options.

### Key Configuration Variables

```bash
# Model Providers
HERMUS_MODEL_PROVIDER=ollama
HERMUS_MODEL_NAME=llama3.2

# Gateway
HERMUS_GATEWAY_PORT=8000
HERMUS_GATEWAY_TOKEN=your-secret-token

# Safety
HERMUS_SAFETY_ENABLED=1
HERMUS_SANDBOX_BACKEND=docker

# Memory
HERMUS_MEMORY_ENABLED=1
HERMUS_MEMORY_SWEEP_MINUTES=60

# Multi-Agent
HERMUS_COUNSEL_ENABLED=1
HERMUS_COUNSEL_MAX_MEMBERS=5
```

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [QUICKSTART.md](QUICKSTART.md) | Installation, onboarding, CLI cheatsheet |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Canonical architecture reference |
| [LIVING_CONTROL_ROOM.md](LIVING_CONTROL_ROOM.md) | Control room design and features |
| [RED_LINES.md](RED_LINES.md) | Safety boundaries and red-line policy |
| [AUTONOMY_BOUNDARIES.md](AUTONOMY_BOUNDARIES.md) | Autonomy and capability boundaries |
| [CAPABILITY_LEDGER.md](CAPABILITY_LEDGER.md) | Visible ledger of powers and capabilities |
| [docs/PHASE_6_RUNTIME_RELIABILITY.md](docs/PHASE_6_RUNTIME_RELIABILITY.md) | Runtime reliability and observability |
| [docs/PHASE_7_PROACTIVE_AUTOMATION.md](docs/PHASE_7_PROACTIVE_AUTOMATION.md) | Proactive automation architecture |
| [docs/PHASE_8_SCHEDULING.md](docs/PHASE_8_SCHEDULING.md) | Scheduling and time awareness |
| [docs/PHASE_9_PERSONAL_CONTEXT.md](docs/PHASE_9_PERSONAL_CONTEXT.md) | Persistent personal context |
| [docs/PHASE_10_WORLD_AWARENESS.md](docs/PHASE_10_WORLD_AWARENESS.md) | World awareness and live World Model |
| [docs/PHASE_11_LONG_HORIZON_PLANNING.md](docs/PHASE_11_LONG_HORIZON_PLANNING.md) | Long-horizon planning |
| [docs/PHASE_12_SPECIALIST_ECOSYSTEM.md](docs/PHASE_12_SPECIALIST_ECOSYSTEM.md) | Specialist agent ecosystem |
| [docs/PHASE_13_MULTIMODAL_INTELLIGENCE.md](docs/PHASE_13_MULTIMODAL_INTELLIGENCE.md) | Multimodal intelligence |
| [docs/JARVIS_FAIRY_ROADMAP.md](docs/JARVIS_FAIRY_ROADMAP.md) | Long-term HERMUS roadmap |

---

## 🛠️ Model Providers

Hermus supports multiple model providers:

### Local Models (Recommended)
- **Ollama** - Primary local model path
- **NoLlama** - Intel NPU and GPU support
- **Any local OpenAI-compatible endpoint**

### Hosted Providers
- Compatible with any OpenAI-compatible API
- Free-tier providers can be configured
- No vendor lock-in

### Model Families Supported
- Llama 2/3
- Mistral
- Phi
- And any other compatible models

---

## 🤝 Contributing

We welcome contributions! Please:

1. ✨ **Star** the repository
2. 🐛 **Report** bugs and issues
3. 💬 **Join** the Discord community
4. 📝 **Read** the [Contributing Guide](CONTRIBUTING.md)
5. 🔧 **Submit** pull requests

### Development Setup
```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

---

## 📜 License

Hermus Agent Free is released under the **MIT License**. See [LICENSE](LICENSE).

---

## 🙏 Acknowledgments

- Built with ❤️ for the open-source community
- Inspired by the best AI agent frameworks
- Powered by Python 3.10+
- Designed for autonomy and safety

---

<p align="center">
  <strong>⚡ HERMUS Agent Free — Build, Research, Automate, Remember, Verify, Improve.</strong>
</p>

<p align="center">
  Made with ❤️ by <a href="https://github.com/trmv2007-bot">trmv2007-bot</a> and contributors
</p>

---

<p align="center">
  <a href="https://github.com/trmv2007-bot/hermus-agent-free">
    <img src="https://img.shields.io/github/forks/trmv2007-bot/hermus-agent-free?style=social" alt="Forks">
  </a>
  <a href="https://github.com/trmv2007-bot/hermus-agent-free">
    <img src="https://img.shields.io/github/issues/trmv2007-bot/hermus-agent-free?style=social" alt="Issues">
  </a>
  <a href="https://github.com/trmv2007-bot/hermus-agent-free">
    <img src="https://img.shields.io/github/contributors/trmv2007-bot/hermus-agent-free?style=social" alt="Contributors">
  </a>
</p>
