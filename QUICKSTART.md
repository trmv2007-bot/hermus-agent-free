# ☤ Hermus Agent Free — Quickstart & Installation Guide

Get up and running with Hermus on **Linux, macOS, or Windows (WSL2)** in 3 simple steps.

---

## ⚡ 3-Step Fast Installation (Linux / WSL / macOS)

### Step 1: Clone the Repository
```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
```

### Step 2: Bootstrap with One Command
```bash
./hermus bootstrap
```
*(No need to `source` multiple files. If you need OS-level system packages first —
Node, Bun, Playwright Chromium, ffmpeg, graphics libs — run `bash setup.sh`, which
now delegates all Python/dependency/layout/health work to this canonical bootstrap.)*

**What the canonical bootstrap does (idempotent — safe to run again):**
1. Detects OS / Python / permissions.
2. Creates or updates the Python virtual environment (`.venv`).
3. Installs the pinned runtime dependencies (required set gate the exit code).
4. Initializes the canonical data/home layout (`data`, `workspace`, `artifacts`, `skills`, `migrations`, `logs`).
5. Migrates legacy memory/state if present.
6. Runs health probes and prints one capability summary.
7. Exits `0` only when **required** capabilities are ready.
   Optional deps (Playwright, vision, voice, channels, backends) degrade into an
   explicit `unavailable` state with the exact reason — never a fake success.

Recommended commands:
```bash
./hermus bootstrap            # one-command setup + health
./hermus doctor               # health/diagnostics report
./hermus start                # dashboard + gateway
./hermus mission "goal"       # autonomous mission
./hermus status --live        # live capability state
```

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
