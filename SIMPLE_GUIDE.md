# Hermus Agent Free — Simple Guide

**One sentence:** Free, autonomous multi-model AI agent that lives on your machine/server, remembers your work, auto-creates skills, writes & verifies code, executes browser automation, and connects to your phone over Tailscale — 100% free with zero paywalls.

> **GitHub:** https://github.com/trmv2007-bot/hermus-agent-free

---

## ⚡ Fast 1-Step Installation (Linux / WSL / macOS)

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
bash setup.sh
```

Then start the live gateway:
```bash
./hermus-gateway
```
*(Or `./bin/hermus-gateway` or `source activate.sh && hermus-gateway`)*

Open **`http://localhost:8000/control`** in your browser!

---

## 🎛️ The Control Room (single production UI)

The canonical control room is at **`http://localhost:8000/control`** (root `/` redirects
here). It is a single self-contained surface streamed from backend state + event
replay — it is a *projection*, not a separate store. It covers chat, the multi-model
fleet, key vault, semantic RAG memory, tools registry, channel webhooks, computer/agent
autonomy and remote-pocket control, reconstructed from durable backend state. The
older `/dashboard`, `/jarvis`, `/computer/dashboard` and `/remote` surfaces and their
assets were removed and folded into `/control`.

---

## 🧙‍♂️ The 4-Step Quickstart Setup Wizard

On your first visit to the dashboard, Hermus walks you through:
1. **AI Brain:** Choose **⚡ Groq (Llama 3.3 70B)**, **🌪️ Mistral (`devstral-latest` Free Tier)**, **OpenAI**, or **Local Ollama**. Or click **`✨ Auto-Provision Free Fleet`** for 12+ free community models.
2. **Mobile Remote:** Scan the vector QR Code with your phone camera to pair with Tailscale Mesh VPN (`100.x.y.z`) or local Wi-Fi.
3. **Workspace Memory:** 1-click vector indexing of your workspace files into local SQLite (`data/memory2.db`).
4. **Autonomy Policy:** Select Supervised, Autonomous, or Full Auto mode.

---

## 🤖 What Can Hermus Do?

### 💬 Chat, Memory & Skill Forge
* **Persistent Memory:** SQLite FTS5 + semantic vector embeddings store project context, code snippets, and user preferences with exponential decay.
* **Skill Forge:** Automatically packages multi-step trajectories into reusable skills (`skills/name/`) so repetitive tasks cost zero tokens in the future.

### 🌪️ Free Model Fleet & Consensus
* **Groq LPU:** 300+ tokens/sec lightning-fast responses for daily conversation.
* **Mistral Devstral:** Free developer tier model (`devstral-latest`) with a 256k context window for code analysis and deep refactoring.
* **DeepThink Council:** Fan-out prompts to multiple models simultaneously and synthesize final consensus.

### 🖥️ Computer Agent Automation
* Uses Playwright and Chromium to navigate web apps, click UI elements, fill forms, and take screenshot trajectories autonomously.

### 📡 Remote Mesh Access via Tailscale
* Pair your phone and PC over an encrypted WireGuard mesh without opening router ports or exposing your IP to the public internet.

---

## ⌨️ Command Line (CLI) Cheatsheet

```bash
# Chat in terminal
./hermus

# API keys in .env are auto-discovered (OPENROUTER_API_KEY, GEMINI_API_KEY,
# NVIDIA_API_KEY, ...) — you do NOT have to add them again with `multikey add`.
# Optional: show which providers Hermus sees as configured/usable.
./hermus multikey providers

# Add API keys
./hermus multikey add --provider mistral --key YOUR_KEY --model devstral-latest
./hermus multikey add --provider groq --key YOUR_KEY

# Check fleet status
./hermus multikey health

# Run autonomous mission
./hermus mission start "Build a Python CLI for file search"
```
