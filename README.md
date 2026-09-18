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
<a href="https://discord.gg/example"><img src="https://img.shields.io/discord/123456789.svg?style=for-the-badge&logo=discord&label=Community" alt="Discord"></a>
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

- **CLI** - Full command-line interface
- **Web Dashboard** - Live task progress, agents, telemetry, reasoning
- **Telegram** - Mobile and desktop integration
- **Discord** - Server and bot integration
- **Slack** - Workspace webhook support
- **Voice** - Local speech-to-text and text-to-speech
- **Computer Control** - Browser automation and system interaction

### 🛡️ Safety & Trust

- **Red Line Policy** - Clear boundaries for autonomous actions
- **Approval System** - Scoped grants for yellow-zone actions
- **Emergency Brake** - Immediate stop capability
- **Audit Logs** - Complete action tracking and review
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
