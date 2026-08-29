#!/usr/bin/env bash
# =============================================================================
# Hermus Agent Free — All-in-One Automated Setup for Linux / WSL / macOS
# =============================================================================

# If executed with 'sh' (which is dash on Ubuntu/WSL), safely re-exec with bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e
set -u
# Safely enable pipefail only if supported
(set -o pipefail 2>/dev/null) && set -o pipefail

HERMUS_REPO_URL="${HERMUS_REPO_URL:-https://github.com/trmv2007-bot/hermus-agent-free.git}"
HERMUS_DIR_NAME="${HERMUS_DIR_NAME:-hermus-agent-free}"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

# defaults
MINIMAL=0
WITH_OLLAMA=0
WITH_BROWSER=1
WITH_VOICE=1
START_GATEWAY=0
ASSUME_YES=0
GROQ_KEY=""
MISTRAL_KEY=""
OPENROUTER_KEY=""
OPENAI_KEY=""
CUSTOM_PROVIDER=""
CUSTOM_KEY=""
CUSTOM_BASE_URL=""
CUSTOM_MODEL=""
INSTALL_DIR=""
SKIP_CLONE=0
PULL_MODEL="${HERMUS_OLLAMA_MODEL:-llama3.2}"
GATEWAY_PORT="${HERMUS_GATEWAY_PORT:-8000}"

# colors
if [[ -t 1 ]]; then
  C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'
  C_BLUE='\033[0;34m'; C_CYAN='\033[0;36m'; C_BOLD='\033[1m'; C_DIM='\033[2m'; C_NC='\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_BLUE=''; C_CYAN=''; C_BOLD=''; C_DIM=''; C_NC=''
fi

log()  { echo -e "${C_BLUE}[hermus]${C_NC} $*"; }
ok()   { echo -e "${C_GREEN}[  ok  ]${C_NC} $*"; }
warn() { echo -e "${C_YELLOW}[ warn ]${C_NC} $*"; }
err()  { echo -e "${C_RED}[error ]${C_NC} $*" >&2; }
die()  { err "$*"; exit 1; }

header() {
  echo ""
  echo -e "${C_CYAN}${C_BOLD}☤ Hermus Agent Free — Setup${C_NC}"
  echo -e "${C_DIM}   Multi-Model Autonomous Agent · Free Stack · Self-Hosted${C_NC}"
  echo ""
}

usage() {
  echo "Usage: bash setup.sh [options]"
  echo ""
  echo "Options:"
  echo "  --minimal         Install core dependencies only (fastest)"
  echo "  --with-ollama     Install and start local Ollama"
  echo "  --with-browser    Install Playwright Chromium (default: on)"
  echo "  --no-browser      Skip browser installation"
  echo "  --groq-key KEY    Pre-configure Groq API key"
  echo "  --mistral-key KEY Pre-configure Mistral/Devstral API key"
  echo "  --start           Start gateway immediately after install"
  echo "  -y, --yes         Assume yes to all prompts"
  exit 0
}

# ---------- args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --minimal) MINIMAL=1; WITH_BROWSER=0; shift ;;
    --with-ollama) WITH_OLLAMA=1; shift ;;
    --with-browser) WITH_BROWSER=1; shift ;;
    --no-browser) WITH_BROWSER=0; shift ;;
    --with-voice) WITH_VOICE=1; shift ;;
    --no-voice) WITH_VOICE=0; shift ;;
    --start) START_GATEWAY=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --groq-key) GROQ_KEY="${2:-}"; shift 2 ;;
    --mistral-key) MISTRAL_KEY="${2:-}"; shift 2 ;;
    --openrouter-key) OPENROUTER_KEY="${2:-}"; shift 2 ;;
    --openai-key) OPENAI_KEY="${2:-}"; shift 2 ;;
    --custom-provider) CUSTOM_PROVIDER="${2:-}"; shift 2 ;;
    --custom-key) CUSTOM_KEY="${2:-}"; shift 2 ;;
    --custom-base-url) CUSTOM_BASE_URL="${2:-}"; shift 2 ;;
    --custom-model) CUSTOM_MODEL="${2:-}"; shift 2 ;;
    --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --model) PULL_MODEL="${2:-}"; shift 2 ;;
    --port) GATEWAY_PORT="${2:-}"; shift 2 ;;
    *)
      warn "Unknown option: $1"
      shift
      ;;
  esac
done

header

# ---------- helpers ----------
have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  local msg="$1"
  if [[ "$ASSUME_YES" == "1" ]]; then return 0; fi
  if [[ ! -t 0 ]]; then return 0; fi
  read -r -p "$(echo -e "${C_YELLOW}?${C_NC} $msg [Y/n] ")" ans || true
  case "${ans:-Y}" in
    n|N|no|NO) return 1 ;;
    *) return 0 ;;
  esac
}

detect_os() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Linux*)  echo linux ;;
    Darwin*) echo macos ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) echo unknown ;;
  esac
}

OS="$(detect_os)"

find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if have "$c"; then
      if "$c" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

install_system_deps() {
  log "Checking system packages…"
  case "$OS" in
    linux)
      if have apt-get; then
        local SUDO=""
        if [[ "$(id -u)" -ne 0 ]] && have sudo; then
          SUDO="sudo"
        fi
        if confirm "Install system dependencies via apt (git, python3-venv, pip, curl, ffmpeg)?"; then
          $SUDO apt-get update -y || true
          $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
            git curl ca-certificates \
            python3 python3-venv python3-pip python3-dev \
            build-essential ffmpeg \
            || warn "Some apt packages had warnings — continuing"
        fi
      fi
      ;;
    macos)
      if have brew; then
        if confirm "Install ffmpeg via Homebrew?"; then
          brew install ffmpeg || true
        fi
      fi
      ;;
  esac
}

resolve_repo_dir() {
  local here
  here="$(pwd)"
  if [[ -f "$here/hermus.py" && -f "$here/requirements.txt" ]]; then
    SKIP_CLONE=1
    echo "$here"
    return 0
  fi
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$script_dir/hermus.py" && -f "$script_dir/requirements.txt" ]]; then
      SKIP_CLONE=1
      echo "$script_dir"
      return 0
    fi
  fi
  if [[ -n "$INSTALL_DIR" ]]; then
    echo "$INSTALL_DIR"
  else
    echo "$HOME/$HERMUS_DIR_NAME"
  fi
}

clone_or_update() {
  local dir="$1"
  if [[ "$SKIP_CLONE" == "1" ]]; then
    ok "Using existing directory: $dir"
    return 0
  fi

  if [[ -d "$dir/.git" ]]; then
    ok "Repo already at $dir"
    if confirm "Pull latest changes from GitHub?"; then
      git -C "$dir" pull --ff-only || warn "git pull failed (continuing with local copy)"
    fi
    return 0
  fi

  have git || die "git is required. Install git and re-run."
  log "Cloning $HERMUS_REPO_URL → $dir"
  mkdir -p "$(dirname "$dir")"
  git clone --depth 1 "$HERMUS_REPO_URL" "$dir"
  ok "Cloned repository successfully"
}

setup_venv() {
  local dir="$1"
  local py="$2"
  cd "$dir"
  log "Creating virtual environment (.venv) using $py …"
  if [[ ! -d .venv ]]; then
    "$py" -m venv .venv
  else
    ok "Virtual environment already exists"
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip setuptools wheel --quiet
  ok "venv ready: $(python --version 2>&1)"
}

install_python_deps() {
  local dir="$1"
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate

  log "Installing Python dependencies…"
  pip install \
    "pydantic>=2.0.0" "python-dotenv>=1.0.0" "requests>=2.31.0" \
    "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" "httpx>=0.24.0" \
    "tiktoken>=0.5.0" "duckduckgo-search>=5.0.0" \
    "APScheduler>=3.10.0" "prompt_toolkit>=3.0.41" "rich>=13.0.0" \
    "pytest>=8.0.0" "Pillow>=10.0.0" "groq>=0.4.0" \
    || die "pip install core failed"
  ok "Core Python dependencies installed"

  if [[ "$MINIMAL" != "1" ]]; then
    pip install "huggingface_hub>=0.23.0" "paramiko>=3.0.0" || true
    if [[ "$WITH_VOICE" == "1" ]]; then
      pip install "faster-whisper>=0.9.0" || warn "faster-whisper install skipped"
    fi
  fi
}

install_browser() {
  local dir="$1"
  [[ "$WITH_BROWSER" == "1" ]] || { warn "Skipping browser tools"; return 0; }
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "Installing Playwright + Chromium for Computer Agent…"
  pip install "playwright>=1.40.0" --quiet || { warn "playwright pip failed"; return 0; }
  if python -m playwright install chromium 2>/dev/null; then
    ok "Playwright Chromium installed"
  else
    warn "Playwright browser install can be completed later: python -m playwright install chromium"
  fi
}

install_ollama() {
  [[ "$WITH_OLLAMA" == "1" ]] || return 0
  log "Setting up Ollama (local offline LLM)…"
  if ! have ollama; then
    if [[ "$OS" == "linux" || "$OS" == "macos" ]]; then
      if confirm "Install Ollama now?"; then
        curl -fsSL https://ollama.com/install.sh | sh || warn "Ollama install script had warnings"
      fi
    fi
  else
    ok "Ollama already installed"
  fi
}

write_launchers() {
  local dir="$1"
  cd "$dir"

  mkdir -p bin
  cat > bin/hermus << 'EOF'
#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
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
if [[ -f "\$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "\$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="\$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
PORT="\${1:-$GATEWAY_PORT}"
echo "☤ Starting Hermus on http://0.0.0.0:\${PORT}"
echo "   Dashboard: http://localhost:\${PORT}/dashboard/legacy"
echo "   Pocket Remote: http://localhost:\${PORT}/remote"
exec python "\$ROOT/hermus.py" gateway start --port "\$PORT"
EOF
  chmod +x bin/hermus-gateway

  cat > activate.sh << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$ROOT/bin:$PATH"
echo "☤ Hermus environment active! Try: hermus --help or hermus-gateway"
EOF
  chmod +x activate.sh

  cat > hermus << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/hermus" "$@"
EOF
  chmod +x hermus

  ok "Launchers ready (./hermus, ./bin/hermus-gateway, source activate.sh)"
}

mkdirs_data() {
  local dir="$1"
  mkdir -p "$dir/data" "$dir/data/sessions" "$dir/data/tmp" "$dir/data/skins"
  ok "Data storage directories initialized"
}

verify_install() {
  local dir="$1"
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  export PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}"

  log "Verifying system components…"
  python - << 'PY'
import sys
mods = [
  "core.config", "core.agent", "core.llm", "core.multi_key",
  "core.free_keys", "core.tailscale", "gateway.gateway"
]
for m in mods:
    __import__(m)
    print("  ✓", m)
print("VERIFY_OK")
PY
  ok "All Hermus subsystems verified successfully!"
}

print_next_steps() {
  local dir="$1"
  cat << EOF

${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════════════${C_NC}
${C_CYAN}${C_BOLD}  ☤ Hermus Agent Free — Installation Complete!${C_NC}
${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════════════${C_NC}

${C_BOLD}Install Directory:${C_NC}  $dir

${C_BOLD}1. Start the Gateway & Dashboards:${C_NC}
   ${C_CYAN}./bin/hermus-gateway${C_NC}

${C_BOLD}2. Open in your Browser:${C_NC}
   • Quickstart Setup & Chat:  ${C_CYAN}http://localhost:${GATEWAY_PORT}/dashboard/legacy${C_NC}
   • Computer Agent Deck:      ${C_CYAN}http://localhost:${GATEWAY_PORT}/computer/dashboard${C_NC}
   • Mobile Pocket Remote:     ${C_CYAN}http://localhost:${GATEWAY_PORT}/remote${C_NC}

${C_BOLD}3. Use in Terminal (CLI):${C_NC}
   ${C_CYAN}./hermus${C_NC}

EOF
}

start_gateway_now() {
  local dir="$1"
  [[ "$START_GATEWAY" == "1" ]] || return 0
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  export PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}"
  exec python hermus.py gateway start --port "$GATEWAY_PORT"
}

# ---------- main ----------
main() {
  install_system_deps

  PY="$(find_python || true)"
  if [[ -z "${PY:-}" ]]; then
    die "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ required. Install python3 (3.10+) and re-run."
  fi
  ok "Detected Python: $($PY --version 2>&1)"

  REPO_DIR="$(resolve_repo_dir)"
  log "Repository path: $REPO_DIR"
  clone_or_update "$REPO_DIR"
  cd "$REPO_DIR"

  setup_venv "$REPO_DIR" "$PY"
  install_python_deps "$REPO_DIR"
  install_browser "$REPO_DIR"
  install_ollama
  mkdirs_data "$REPO_DIR"
  write_launchers "$REPO_DIR"
  verify_install "$REPO_DIR"
  print_next_steps "$REPO_DIR"
  start_gateway_now "$REPO_DIR"
}

main "$@"
