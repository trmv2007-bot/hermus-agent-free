# ⚡ HERMUS Agent Free

<p align="center">
  <img alt="HERMUS Logo" src="docs/assets/hermus-dark.png" width="200">
</p>

<p align="center">
<strong>🌟 A free, open-source, self-hosted AI agent for coding, research, automation and multi-agent work.</strong><br>
<strong>Build with local models or free-tier providers. Keep your data, tools and runtime under your control.</strong>
</p>

<p align="center">
<a href="https://github.com/trmv2007-bot/hermus-agent-free/stargazers"><img src="https://img.shields.io/github/stars/trmv2007-bot/hermus-agent-free?style=for-the-badge&color=58a6ff" alt="Stars"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT"></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg?style=for-the-badge&logo=python" alt="Python"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/graphs/contributors"><img src="https://img.shields.io/github/contributors/trmv2007-bot/hermus-agent-free?style=for-the-badge" alt="Contributors"></a>
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

- **CLI** — Full command-line interface (`./hermus`)
- **Web Dashboard** — Live task progress, agents, telemetry, reasoning
- **Telegram** — Mobile and desktop integration
- **Discord** — Server and bot integration
- **Voice** — Local speech-to-text and text-to-speech
- **Computer Control** — Browser automation and system interaction
- **Android Companion** — Consent-gated on-device agent bridge

### 🛡️ Safety & Trust

- **Red Line Policy** — Clear boundaries for autonomous actions
- **Approval System** — Scoped grants for yellow-zone actions
- **Emergency Brake** — Immediate stop capability
- **Audit Logs** — Complete action tracking and review
- **Sandboxing** — Multiple isolation backends (Docker, Podman, gVisor)

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       HERMUS AGENT                           │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
│  │   Gateway   │◄──►│   Mission   │◄──►│   Memory    │       │
│  │   & API     │    │   Engine    │    │   System    │       │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘       │
│         │                  │                  │              │
│  ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐       │
│  │    CLI      │    │  SWE Mode   │    │   Counsel   │       │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘       │
│         │                  │                  │              │
│  ┌──────▼──────────────────▼──────────────────▼──────┐       │
│  │           Tool System & Sandbox                   │       │
│  └───────────────────────────────────────────────────┘       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

The canonical, detailed reference lives in [ARCHITECTURE.md](ARCHITECTURE.md).

### 🎨 Control Room Dashboard

The **Control Room** at `http://localhost:8000/control` is your command center:

| Tab | Purpose |
|-----|---------|
| **Chat** | Talk to HERMUS (streamed replies) beside the live event rail |
| **Agents** | Fleet agents, live roster states, and assignments |
| **Presence** | Agent identity, state, goals, and continuity |
| **Voice** | Speech-to-text and text-to-speech |
| **Jobs** | Queue management and execution tracking |
| **Missions** | Autonomous task management |
| **Computer** | System automation and control |
| **Remote** | External integrations |
| **Safety** | Red lines, approvals, and emergency controls |
| **Systems** | Health, capabilities, VRAM, replay, typed commands, and every subsystem at a glance |

---

## 🚀 Usage Examples

### Start an Autonomous Mission
```bash
./hermus mission start "Build and test a web application that does X"
```

### Run Software Engineering Workflow
```bash
./hermus swe run "Fix the failing tests and package the project"
```

### Use AI Counsel for Complex Decisions
```bash
./hermus counsel run "Compare three architectures and recommend the best one"
```

### Interactive Terminal Agent
```bash
./hermus
```

### Check System Health
```bash
./hermus doctor
```

More commands live in the [CLI cheatsheet](QUICKSTART.md).

---

## 🔧 Configuration

Hermus is configured through environment variables (prefix `HERMUS_`). The
typed settings model in `core/config.py` is the source of truth — key examples:

```bash
# Model (provider-prefixed: ollama/..., groq/..., hf/..., mock/...)
HERMUS_MODEL=ollama/llama3.1:8b

# Gateway
HERMUS_GATEWAY_TOKEN=your-secret-token   # optional; required when exposing beyond localhost

# Safety
HERMUS_PERMISSIONS_ENFORCE=1
HERMUS_SANDBOX=auto                      # auto | docker | podman | gvisor | local | off

# Memory
HERMUS_MEMORY2_ENABLED=1
HERMUS_MEMORY_SWEEP_MINUTES=60

# Multi-agent
HERMUS_COUNSEL_ENABLED=1
HERMUS_COUNSEL_MAX_MEMBERS=6

# Mission runtime
HERMUS_MISSION_RUNTIME=1
HERMUS_MISSION_BUDGET_STEPS=48
```

Provider API keys (`GROQ_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, …)
are auto-discovered from `.env` — add them once with `./hermus multikey add`.

---

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| [QUICKSTART.md](QUICKSTART.md) | Installation, onboarding, CLI cheatsheet |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Canonical architecture reference |
| [LIVING_CONTROL_ROOM.md](LIVING_CONTROL_ROOM.md) | Control room design and features |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and PR process |
| [RED_LINES.md](RED_LINES.md) | Safety boundaries and red-line policy |
| [AUTONOMY_BOUNDARIES.md](AUTONOMY_BOUNDARIES.md) | Autonomy and capability boundaries |
| [CAPABILITY_LEDGER.md](CAPABILITY_LEDGER.md) | Visible ledger of powers and capabilities |
| [SPEC_PERSISTENT_FLEET.md](SPEC_PERSISTENT_FLEET.md) | Persistent Fleet master spec (v2) + roadmap |
| [docs/design/](docs/design/README.md) | Fleet v2 design docs: gap registers, Vault accounts, dashboard UX |

---

## 🛠️ Model Providers

Hermus supports multiple model providers behind one gateway:

### Local Models (Recommended)
- **Ollama** — primary local model path
- **NoLlama** — Intel NPU and Arc GPU support
- **Any local OpenAI-compatible endpoint**

### Hosted Providers
- Compatible with any OpenAI-compatible API (Groq, OpenRouter, Gemini, …)
- Free-tier providers can be configured
- No vendor lock-in

The router auto-selects a compatible model per task (tool calling, vision,
context size) instead of trusting name keywords — see `HERMUS_AUTO_SELECT_MODEL`.

---

## 🤝 Contributing

We welcome contributions! Please:

1. 🐛 **Report** bugs and issues
2. 📝 **Read** the [Contributing Guide](CONTRIBUTING.md)
3. 🔧 **Submit** pull requests

### Development Setup
```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
make setup          # creates .venv, installs runtime + dev dependencies
make test           # fast test suite
./hermus doctor     # health/diagnostics report
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
