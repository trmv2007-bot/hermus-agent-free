#!/usr/bin/env bash
# =============================================================================
# Hermus Agent Free — Single Unified Master Installer for Linux / WSL / macOS
# =============================================================================

# Auto-re-exec in bash if invoked via sh/dash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e

REPO_URL="https://github.com/trmv2007-bot/hermus-agent-free.git"
DEFAULT_PORT="8000"

# Color helpers
if [ -t 1 ]; then
  C_CYAN='\033[0;36m'
  C_GREEN='\033[0;32m'
  C_YELLOW='\033[1;33m'
  C_RED='\033[0;31m'
  C_BOLD='\033[1m'
  C_DIM='\033[2m'
  C_RESET='\033[0m'
else
  C_CYAN=''
  C_GREEN=''
  C_YELLOW=''
  C_RED=''
  C_BOLD=''
  C_DIM=''
  C_RESET=''
fi

echo -e "${C_CYAN}${C_BOLD}"
echo "  ☤ HERMUS AGENT FREE — MASTER INSTALLER"
echo "  Universal Autonomous Multi-Model Agent"
echo -e "${C_RESET}${C_DIM}  ======================================================${C_RESET}"
echo ""

# 1. Detect Working Directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${C_CYAN}[1/6]${C_RESET} Checking system dependencies for Linux/WSL..."

# 2. Install System Packages if on Debian/Ubuntu/WSL
if command -v apt-get >/dev/null 2>&1; then
  SUDO_CMD=""
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
  fi
  
  echo -e "      Updating apt packages and installing Python3, venv, git, ffmpeg..."
  $SUDO_CMD apt-get update -y -qq || true
  $SUDO_CMD DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git curl ca-certificates python3 python3-venv python3-pip python3-dev \
    build-essential ffmpeg libasound2 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 || true
fi

# 3. Locate Python 3.10+
PYTHON_BIN=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
      PYTHON_BIN="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo -e "${C_RED}[ERROR] Python 3.10+ is required but not found.${C_RESET}"
  echo "Please install Python 3.10 or newer (sudo apt install python3 python3-venv)"
  exit 1
fi

echo -e "      ${C_GREEN}✓ Found Python: $($PYTHON_BIN --version)${C_RESET}"

# 4. Create & Activate Virtual Environment (.venv)
echo -e "${C_CYAN}[2/6]${C_RESET} Creating virtual environment (.venv)..."
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# Activate venv
# shellcheck disable=SC1091
source .venv/bin/activate
echo -e "      ${C_GREEN}✓ Virtualenv active: $(python --version)${C_RESET}"

# Upgrade pip, setuptools, wheel
python -m pip install -U pip setuptools wheel --quiet

# 5. Install A-to-Z Python Dependencies
echo -e "${C_CYAN}[3/6]${C_RESET} Installing complete A-to-Z Python package stack..."
pip install --quiet \
  "fastapi>=0.104.0" \
  "uvicorn[standard]>=0.24.0" \
  "starlette>=0.46.0" \
  "httpx>=0.24.0" \
  "pydantic>=2.0.0" \
  "python-dotenv>=1.0.0" \
  "requests>=2.31.0" \
  "groq>=0.4.0" \
  "huggingface_hub>=0.23.0" \
  "tiktoken>=0.5.0" \
  "duckduckgo-search>=5.0.0" \
  "ddgs" \
  "APScheduler>=3.10.0" \
  "prompt_toolkit>=3.0.41" \
  "rich>=13.0.0" \
  "Pillow>=10.0.0" \
  "imageio-ffmpeg>=0.5.1" \
  "faster-whisper>=0.9.0" \
  "paramiko>=3.0.0" \
  "python-telegram-bot>=20.0" \
  "discord.py>=2.3.0" \
  "pytest>=8.0.0" \
  "anyio>=4.0.0" \
  "playwright>=1.40.0" || {
    echo -e "${C_YELLOW}[warn] Some optional voice/bot packages had warnings, installing core stack...${C_RESET}"
    pip install \
      "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" "httpx>=0.24.0" \
      "pydantic>=2.0.0" "python-dotenv>=1.0.0" "requests>=2.31.0" \
      "groq>=0.4.0" "tiktoken>=0.5.0" "duckduckgo-search>=5.0.0" \
      "APScheduler>=3.10.0" "prompt_toolkit>=3.0.41" "rich>=13.0.0" \
      "Pillow>=10.0.0" "pytest>=8.0.0" "playwright>=1.40.0"
}
echo -e "      ${C_GREEN}✓ Python packages installed successfully${C_RESET}"

# 6. Install Playwright Browser Binaries for Computer Agent
echo -e "${C_CYAN}[4/6]${C_RESET} Setting up Playwright Chromium browser for Computer Agent..."
python -m playwright install chromium 2>/dev/null || true
echo -e "      ${C_GREEN}✓ Browser automation runtime ready${C_RESET}"

# 7. Initialize Storage Directories & Data Stores
echo -e "${C_CYAN}[5/6]${C_RESET} Initializing local storage & vector databases..."
mkdir -p data data/sessions data/tmp data/skins data/counsel data/plans data/recordings missions artifacts checkpoints bin
echo -e "      ${C_GREEN}✓ Local storage directories initialized${C_RESET}"

# 8. Create Ready-to-Run Launchers
echo -e "${C_CYAN}[6/6]${C_RESET} Generating executable launchers..."

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

cat > bin/hermus-gateway << EOF
#!/usr/bin/env bash
set -e
ROOT="\$(cd "\$(dirname "\$0")/.." && pwd)"
if [ -f "\$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "\$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="\$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
PORT="\${1:-$DEFAULT_PORT}"
echo -e "\033[0;36m☤ Hermus Live Gateway\033[0m"
echo -e "  • Control Center:   \033[1mhttp://localhost:\${PORT}/dashboard/legacy\033[0m"
echo -e "  • Computer Agent:   \033[1mhttp://localhost:\${PORT}/computer/dashboard\033[0m"
echo -e "  • Pocket Remote:    \033[1mhttp://localhost:\${PORT}/remote\033[0m"
echo ""
exec python "\$ROOT/hermus.py" gateway start --port "\$PORT"
EOF
chmod +x bin/hermus-gateway

cat > activate.sh << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$ROOT/bin:$PATH"
echo "☤ Hermus environment active! Run 'hermus --help' or 'hermus-gateway'"
EOF
chmod +x activate.sh

cat > hermus << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/hermus" "$@"
EOF
chmod +x hermus

# Create default .env if missing
if [ ! -f ".env" ]; then
  cat > .env << 'EOF'
# Hermus Configuration
HERMUS_GATEWAY_PORT=8000
HERMUS_AUTONOMY_MODE=balanced
# Add keys anytime via the Quickstart Setup Wizard or dashboard
EOF
fi

echo ""
echo -e "${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_CYAN}${C_BOLD}  🎉 HERMUS INSTALLATION COMPLETE!${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════════════${C_RESET}"
echo ""
echo -e "  ${C_BOLD}1. Start the Live Server & Dashboards:${C_RESET}"
echo -e "     ${C_CYAN}./bin/hermus-gateway${C_RESET}"
echo ""
echo -e "  ${C_BOLD}2. Open in your Browser:${C_RESET}"
echo -e "     • Setup Wizard & Chat:  ${C_CYAN}http://localhost:8000/dashboard/legacy${C_RESET}"
echo -e "     • Computer Agent Deck:  ${C_CYAN}http://localhost:8000/computer/dashboard${C_RESET}"
echo -e "     • Mobile Pocket Remote: ${C_CYAN}http://localhost:8000/remote${C_RESET}"
echo ""
echo -e "  ${C_BOLD}3. Use in Terminal (CLI):${C_RESET}"
echo -e "     ${C_CYAN}./hermus${C_RESET}"
echo ""
