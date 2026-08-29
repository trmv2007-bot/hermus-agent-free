# ☤ Hermus Agent Free — Quickstart & Installation Guide

Get up and running with Hermus on **Linux, macOS, or Windows (WSL2)** in 3 simple steps.

---

## ⚡ 3-Step Fast Installation (Linux / WSL / macOS)

### Step 1: Clone the Repository
```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
```

### Step 2: Run the Single Master Installer
```bash
bash setup.sh
```
*(Or run non-interactively in one shot: `bash setup.sh -y`)*

**What this automatically does:**
1. Installs all required system dependencies (`python3-venv`, `python3-pip`, `git`, `curl`, `ffmpeg`, browser graphics libs).
2. Sets up the Python virtual environment (`.venv`).
3. Installs the complete A-to-Z Python stack (`fastapi`, `uvicorn`, `httpx`, `groq`, `pydantic`, `tiktoken`, `rich`, `pytest`, etc.).
4. Sets up Playwright Chromium binaries for the Computer Agent.
5. Generates ready-to-run executables (`./hermus`, `./bin/hermus-gateway`, `source activate.sh`).

---

### Step 3: Launch the Server & Open Your Dashboard
```bash
./hermus-gateway
```
*(Or `./bin/hermus-gateway` or `source activate.sh && hermus-gateway`)*

Open in your browser:
* **🎛️ Control Center & Setup Wizard (Default):** [`http://localhost:8000/dashboard`](http://localhost:8000/dashboard)
* **🌌 Jarvis 3D Holographic Spatial HUD:** [`http://localhost:8000/jarvis`](http://localhost:8000/jarvis)
* **🖥️ Autonomous Computer Agent Deck:** [`http://localhost:8000/computer/dashboard`](http://localhost:8000/computer/dashboard)
* **📱 Mobile Pocket Remote Deck:** [`http://localhost:8000/remote`](http://localhost:8000/remote)

---

## 🧙‍♂️ 4-Step Interactive Onboarding Wizard

When you open the dashboard for the first time, the interactive wizard walks you through:

1. **AI Brain & Key Connection:**
   * Select **⚡ Groq (Free Tier)**, **🌪️ Mistral (`devstral-latest` Free Tier)**, **🧠 OpenAI**, or **💻 Local Ollama (100% Offline)**.
   * Or click **`✨ 1-Click Activate Free Fleet`** to auto-provision 12+ free models from OpenRouter Free and Mistral Devstral!
2. **Mobile Remote & Tailscale Mesh Pairing:**
   * Instant scannable vector SVG QR Code to pair your iPhone/Android camera.
   * Auto-detects Tailscale WireGuard VPN (`100.x.y.z`) to control Hermus from anywhere outside home Wi-Fi.
3. **Local Workspace & Semantic RAG Indexer:**
   * 1-click vector memory indexing into local SQLite (`data/memory2.db`).
4. **Autonomy & Safety Policy:**
   * Choose between Supervised (confirm shell edits), Autonomous (smart auto-run with pocket push notifications), or Full Auto.

---

## ⌨️ Using Hermus in the Terminal (CLI)

```bash
# Chat with default free AI brain
./hermus

# Distribute hard coding & research tasks across the multi-model fleet
./hermus fleet run "Analyze this repository and refactor routes"
```
