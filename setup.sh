#!/usr/bin/env bash
# =============================================================================
# Hermus Agent Free — SYSTEM INSTALLER (WSL / Linux)
# Installs OS packages (APTs, Node, Bun, Playwright system deps).
#
# NOTE (Rebuild §6, §18): the canonical ONE-COMMAND bootstrap is `./hermus bootstrap`,
# which creates the venv, installs the pinned Python dependencies, initializes the
# data layout, migrates legacy state and runs health probes. This script handles the
# OS-level packages only and then delegates to that canonical bootstrap at the end.
# It does NOT duplicate launchers or Python dependency logic.
# =============================================================================

# Auto-re-exec in bash if invoked via sh/dash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e

# Color & Format helpers
if [ -t 1 ]; then
  C_CYAN='\033[0;36m'
  C_GREEN='\033[0;32m'
  C_YELLOW='\033[1;33m'
  C_BLUE='\033[0;34m'
  C_MAGENTA='\033[0;35m'
  C_RED='\033[0;31m'
  C_BOLD='\033[1m'
  C_DIM='\033[2m'
  C_MUTED='\033[2m'
  C_RESET='\033[0m'
else
  C_CYAN=''
  C_GREEN=''
  C_YELLOW=''
  C_BLUE=''
  C_MAGENTA=''
  C_RED=''
  C_BOLD=''
  C_DIM=''
  C_MUTED=''
  C_RESET=''
fi

step_start() {
  local num="$1"
  local title="$2"
  echo -e "\n${C_CYAN}${C_BOLD}[$num/9]${C_RESET} ${C_BOLD}$title${C_RESET}"
}

sub_step() {
  echo -e "      ${C_BLUE}➜${C_RESET} $*"
}

step_ok() {
  echo -e "      ${C_GREEN}✓ $1${C_RESET}"
}

step_warn() {
  echo -e "      ${C_YELLOW}⚠ $1${C_RESET}"
}

echo -e "${C_CYAN}${C_BOLD}"
echo "  ╔═════════════════════════════════════════════════════════════════╗"
echo "  ║         ☤ HERMUS AGENT FREE — MASTER DEVELOPER INSTALLER        ║"
echo "  ║        Complete Stack: Linux/WSL, Node, Bun, Python & AI       ║"
echo "  ╚═════════════════════════════════════════════════════════════════╝"
echo -e "${C_RESET}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------------------------
# STEP 1: System Packages & Linux/WSL Tooling
# -----------------------------------------------------------------------------
step_start "1" "System Packages & Build Essentials (APT / Linux / WSL)"

if command -v apt-get >/dev/null 2>&1; then
  SUDO_CMD=""
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
  fi
  
  sub_step "Updating package lists..."
  $SUDO_CMD apt-get update -y -qq 2>/dev/null || true
  
  sub_step "Installing git, curl, wget, jq, ffmpeg, build-essential..."
  $SUDO_CMD DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git curl wget jq ca-certificates build-essential ffmpeg \
    python3 python3-venv python3-pip python3-dev python3-virtualenv virtualenv 2>/dev/null || true
  
  step_ok "System developer tooling ready (git, curl, jq, ffmpeg)"
else
  step_ok "Non-Debian/WSL environment detected — using existing system packages"
fi

# -----------------------------------------------------------------------------
# STEP 2: Node.js & npm (JavaScript / TypeScript Ecosystem)
# -----------------------------------------------------------------------------
step_start "2" "Node.js & npm (Frontend, MCP Bridges & Web Tools)"

if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  step_ok "Node.js $(node -v) & npm $(npm -v) already installed"
else
  sub_step "Installing Node.js 20 LTS & npm..."
  if command -v apt-get >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO_CMD bash - >/dev/null 2>&1 || true
    $SUDO_CMD apt-get install -y -qq nodejs >/dev/null 2>&1 || true
  fi
  if command -v node >/dev/null 2>&1; then
    step_ok "Node.js $(node -v) installed successfully"
  else
    step_warn "Node.js optional installation skipped (install manually if using MCP JS tools)"
  fi
fi

# -----------------------------------------------------------------------------
# STEP 3: Bun Runtime (Fast TypeScript / JavaScript Execution)
# -----------------------------------------------------------------------------
step_start "3" "Bun Runtime (Ultra-fast JS/TS package executor)"

if command -v bun >/dev/null 2>&1; then
  step_ok "Bun $(bun --version) already installed"
elif [ -f "$HOME/.bun/bin/bun" ]; then
  export PATH="$HOME/.bun/bin:$PATH"
  step_ok "Bun $($HOME/.bun/bin/bun --version) detected in ~/.bun/bin"
else
  sub_step "Installing Bun via official installer (bun.sh)..."
  curl -fsSL https://bun.sh/install | bash >/dev/null 2>&1 || true
  if [ -f "$HOME/.bun/bin/bun" ]; then
    export PATH="$HOME/.bun/bin:$PATH"
    step_ok "Bun $($HOME/.bun/bin/bun --version) installed successfully"
  else
    step_warn "Bun install skipped (optional for ultra-fast TS execution)"
  fi
fi

# -----------------------------------------------------------------------------
# STEP 4: Python 3.10+ & Virtual Environment (.venv)
# -----------------------------------------------------------------------------
step_start "4" "Python 3.10+ Environment & Resilient (.venv) Setup"

PYTHON_CANDIDATES=()
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
      PYTHON_CANDIDATES+=("$cmd")
    fi
  fi
done

if [ ${#PYTHON_CANDIDATES[@]} -eq 0 ]; then
  echo -e "${C_RED}[ERROR] Python 3.10+ is required but not found.${C_RESET}"
  echo "Install with: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi

PRIMARY_PY="${PYTHON_CANDIDATES[0]}"
sub_step "Selected Python runtime: $($PRIMARY_PY --version)"

# Clean up broken .venv if needed
if [ -d ".venv" ] && [ ! -f ".venv/bin/activate" ]; then
  sub_step "Cleaning incomplete previous virtualenv..."
  rm -rf .venv
fi

if [ ! -f ".venv/bin/activate" ]; then
  sub_step "Creating isolated virtual environment (.venv)..."
  for py in "${PYTHON_CANDIDATES[@]}"; do
    "$py" -m venv .venv 2>/dev/null || true
    if [ -f ".venv/bin/activate" ]; then
      PRIMARY_PY="$py"
      break
    fi
  done
  
  if [ ! -f ".venv/bin/activate" ] && command -v virtualenv >/dev/null 2>&1; then
    virtualenv -p "$PRIMARY_PY" .venv 2>/dev/null || true
  fi
fi

if [ ! -f ".venv/bin/activate" ]; then
  echo -e "${C_RED}[ERROR] Could not initialize virtual environment.${C_RESET}"
  echo "Run: sudo apt install python3-venv python3-pip virtualenv"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
step_ok "Virtual environment active: $(python --version)"

sub_step "Upgrading pip, setuptools, wheel..."
python -m pip install -U pip setuptools wheel --quiet 2>/dev/null || true

# -----------------------------------------------------------------------------
# STEP 5: Python Core & Web Gateway Stack (Live Progress)
# -----------------------------------------------------------------------------
step_start "5" "Hermus AI Core & FastAPI Web Gateway Stack"

sub_step "Installing FastAPI, Uvicorn, multipart uploads, telemetry, Starlette, HTTPX..."
pip install "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" "starlette>=0.46.0" \
  "httpx>=0.24.0" "python-multipart>=0.0.9" "psutil>=5.9.0" --quiet
step_ok "FastAPI, SSE/WebSockets, JARVIS attachments and live telemetry dependencies ready"

sub_step "Installing Pydantic, Python-Dotenv, Requests..."
pip install "pydantic>=2.0.0" "python-dotenv>=1.0.0" "requests>=2.31.0" --quiet
step_ok "Data validation and configuration loader ready"

sub_step "Installing Groq, HuggingFace, Tiktoken, Search Engine..."
pip install "groq>=0.4.0" "huggingface_hub>=0.23.0" "tiktoken>=0.5.0" "duckduckgo-search>=5.0.0" "ddgs" --quiet
step_ok "LLM connectors (Groq, Mistral, HuggingFace) & Search tools ready"

sub_step "Installing TUI & CLI Studio (Rich, Prompt-Toolkit, APScheduler)..."
pip install "APScheduler>=3.10.0" "prompt_toolkit>=3.0.41" "rich>=13.0.0" "pytest>=8.0.0" "anyio>=4.0.0" --quiet
step_ok "Terminal UI and autonomous scheduling subsystem ready"

sub_step "Installing Media & Vision (Pillow, FFmpeg bindings)..."
if pip install "Pillow>=10.0.0" "imageio-ffmpeg>=0.5.1" --quiet 2>/dev/null; then
  step_ok "Vision, screenshot and image analysis tools ready"
else
  # Optional component: report the real outcome instead of a false "ready".
  sub_step "⚠️  Vision/FFmpeg packages failed to install (optional; screenshots/vision degraded)"
fi

# Optional voice / bot packages
sub_step "Installing Voice & Bot Integrations (faster-whisper, telegram, discord)..."
if pip install "faster-whisper>=0.9.0" "python-telegram-bot>=20.0" "discord.py>=2.3.0" "paramiko>=3.0.0" --quiet 2>/dev/null; then
  step_ok "Voice transcription & messaging integrations ready"
else
  sub_step "⚠️  Voice/bot packages failed to install (optional; voice & Telegram/Discord degraded)"
fi

# -----------------------------------------------------------------------------
# STEP 6: Computer Agent & Browser Automation Runtime
# -----------------------------------------------------------------------------
step_start "6" "Computer Agent Browser Automation Runtime (Playwright)"

sub_step "Installing Playwright library..."
pip install "playwright>=1.40.0" --quiet
sub_step "Setting up Chromium browser binaries (~150MB engine download)..."
if python -m playwright install chromium; then
  step_ok "Playwright Chromium browser automation engine ready"
else
  sub_step "⚠️  Chromium download failed (computer-agent live browser view degraded; the library is installed and you can re-run: python -m playwright install chromium)"
fi

# -----------------------------------------------------------------------------
# STEP 7: Local Storage, Databases & Memory Initialization
# -----------------------------------------------------------------------------
step_start "7" "Local Vector Storage, Memory & Session Directories"

mkdir -p data data/sessions data/tmp data/skins data/counsel data/plans data/recordings missions artifacts checkpoints bin
step_ok "Initialized local SQLite databases and vector storage in data/"

# -----------------------------------------------------------------------------
# STEP 8: Executable Launchers & Environment Paths
# -----------------------------------------------------------------------------
step_start "8" "Generating Executable Launchers & Environment Setup"

cat > bin/hermus << 'EOF'
#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python "$ROOT/hermus.py" "$@"
EOF
chmod +x bin/hermus

cat > bin/hermus-gateway << 'EOF'
#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PORT="${1:-8000}"
echo -e "\033[0;36m☤ Hermus Live Gateway\033[0m"
echo -e "  • Control Center:   \033[1;36mhttp://localhost:${PORT}/control\033[0m"
echo -e "  • Jarvis Spatial:   \033[1;35mhttp://localhost:${PORT}/control#telemetry\033[0m"
echo -e "  • Computer Agent:   \033[1mhttp://localhost:${PORT}/control#computer\033[0m"
echo -e "  • Pocket Remote:    \033[1mhttp://localhost:${PORT}/remote\033[0m"
echo ""
exec python "$ROOT/hermus.py" gateway start --port "$PORT"
EOF
chmod +x bin/hermus-gateway

cat > activate.sh << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

(return 0 2>/dev/null) && IS_SOURCED=1 || IS_SOURCED=0

if [ "$IS_SOURCED" -eq 1 ]; then
  if [ -f "$ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
  fi
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  export PATH="$ROOT/bin:$ROOT:$PATH"
  if [ -d "$HOME/.bun/bin" ]; then
    export PATH="$HOME/.bun/bin:$PATH"
  fi
  echo -e "\033[0;32m✓\033[0m \033[1;36mHermus environment activated!\033[0m"
  echo -e "  Commands now available: \033[1mhermus-gateway\033[0m, \033[1mhermus\033[0m"
else
  echo -e "\033[1;33m⚠️  Notice:\033[0m 'activate.sh' must be \033[1mSOURCE-loaded\033[0m to persist in your current terminal:"
  echo -e "  👉 \033[1;32msource activate.sh\033[0m   (or: \033[1;32m. activate.sh\033[0m)"
  echo ""
  echo -e "\033[1mAlternatively, launch directly without activating:\033[0m"
  echo -e "  • Start Dashboard: \033[1;36m./bin/hermus-gateway\033[0m  (or \033[1;36m./hermus-gateway\033[0m)"
  echo -e "  • Terminal CLI:    \033[1;36m./hermus\033[0m"
  echo ""
fi
EOF
chmod +x activate.sh

cat > hermus << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/hermus" "$@"
EOF
chmod +x hermus

cat > hermus-gateway << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/hermus-gateway" "$@"
EOF
chmod +x hermus-gateway

step_ok "Generated executables: ./hermus, ./hermus-gateway, ./bin/hermus-gateway, source activate.sh"

# -----------------------------------------------------------------------------
# STEP 9: Verify the core actually imports (do NOT declare success if broken).
# -----------------------------------------------------------------------------
step_start "9" "Verifying Hermus and the JARVIS control plane"
sub_step "Checking gateway/runtime imports, multipart uploads, telemetry, routes and dashboard assets..."
if python - <<'PY' 2>/dev/null
import sys
from pathlib import Path
sys.path.insert(0, ".")
import multipart  # required by JARVIS FormData attachments
import psutil     # live process telemetry
from gateway.gateway import app
from gateway import routes_jarvis
from core import agent, mission

routes = list(app.routes)
for included in app.routes:
    original = getattr(included, "original_router", None)
    if original is not None:
        routes.extend(original.routes)
paths = {route.path for route in routes if getattr(route, "path", None)}
required_routes = {
    "/control", "/command", "/api/jarvis/status", "/navigator/fetch",
    "/run/steer", "/run/cancel/{run_id}", "/stream/run/{run_id}",
    "/api/v1/system/health", "/jobs", "/queue/status",
}
missing_routes = required_routes - paths
required_assets = [
    Path("gateway/control.html"),
]
missing_assets = [str(path) for path in required_assets if not path.is_file()]
if missing_routes or missing_assets:
    raise SystemExit(f"missing routes={sorted(missing_routes)} assets={missing_assets}")
PY
then
  step_ok "Hermus runtime and JARVIS command, SSE, control, telemetry and attachment surfaces are installed"
else
  echo ""
  echo -e "${C_RED}${C_BOLD}  ✗ VERIFICATION FAILED: Hermus core did not import cleanly.${C_RESET}"
  echo -e "${C_MUTED}    Re-run setup with: bash setup.sh   (or activate the venv and run: pip install -r requirements.txt)${C_RESET}"
  echo ""
  exit 1
fi

echo ""
echo -e "${C_GREEN}${C_BOLD}═════════════════════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_CYAN}${C_BOLD}  🎉 ALL-IN-ONE DEVELOPER STACK INSTALLED SUCCESSFULLY!${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}═════════════════════════════════════════════════════════════════════════${C_RESET}"
echo ""
echo -e "  ${C_BOLD}1. Start the Server & Dashboards (pick one):${C_RESET}"
echo -e "     ${C_GREEN}source activate.sh && hermus-gateway${C_RESET}"
echo -e "     ${C_MUTED}or directly:${C_RESET} ${C_CYAN}./bin/hermus-gateway${C_RESET}  ${C_MUTED}(or ${C_CYAN}./hermus-gateway${C_MUTED})${C_RESET}"
echo ""
echo -e "  ${C_BOLD}2. Open in your Browser:${C_RESET}"
echo -e "     • Setup Wizard & Chat:  ${C_CYAN}http://localhost:8000/control${C_RESET}"
echo -e "     • Jarvis Spatial HUD:   ${C_CYAN}http://localhost:8000/control#telemetry${C_RESET}"
echo -e "     • Computer Agent Deck:  ${C_CYAN}http://localhost:8000/control#computer${C_RESET}"
echo -e "     • Mobile Pocket Remote: ${C_CYAN}http://localhost:8000/remote${C_RESET}"
echo ""
echo -e "  ${C_BOLD}3. Use in Terminal (CLI):${C_RESET}"
echo -e "     ${C_CYAN}./hermus${C_RESET}"
echo ""
echo -e "  ${C_BOLD}4. Optional local AI engine (no weights were downloaded here):${C_RESET}"
echo -e "     ${C_MUTED}Dashboard → Local AI Engine, or:${C_RESET} ${C_CYAN}./hermus engine status${C_RESET}"
echo -e "     ${C_MUTED}NPU/GPU routing, on-demand model downloads and the Hermus doctor:${C_RESET}"
echo -e "     ${C_MUTED}docs/LOCAL_ENGINE.md${C_RESET}"
echo ""

# -----------------------------------------------------------------------------
# STEP 9: Delegate to the canonical one-command bootstrap (Rebuild §6, §18)
# -----------------------------------------------------------------------------
step_start "9" "Running canonical bootstrap (./hermus bootstrap)"
if [ -x "./bin/hermus" ]; then
  bash "./bin/hermus" bootstrap
else
  echo -e "${C_YELLOW}⚠ launcher not generated yet; running bootstrap directly${C_RESET}"
  ( exec python bootstrap.py )
fi
