#!/usr/bin/env bash
# =============================================================================
# Hermus Agent Free — one-command setup (from scratch or inside the repo)
# =============================================================================
# From scratch (download + install):
#   curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/setup.sh | bash
#
# Or:
#   git clone https://github.com/trmv2007-bot/hermus-agent-free.git
#   cd hermus-agent-free && bash setup.sh
#
# Options (env vars or flags):
#   bash setup.sh                  # full ready-to-go setup
#   bash setup.sh --minimal        # Python deps only (no browser/whisper heavy bits)
#   bash setup.sh --with-ollama    # also install Ollama + pull llama3.1:8b
#   bash setup.sh --with-browser   # Playwright + Chromium (default: on)
#   bash setup.sh --no-browser     # skip Playwright
#   bash setup.sh --with-voice     # ensure faster-whisper (default: on via requirements)
#   bash setup.sh --groq-key KEY   # save Groq key during setup
#   bash setup.sh --start          # start gateway after setup
#   bash setup.sh --yes            # non-interactive (assume yes)
# =============================================================================
set -euo pipefail

HERMUS_REPO_URL="${HERMUS_REPO_URL:-https://github.com/trmv2007-bot/hermus-agent-free.git}"
HERMUS_RAW_SETUP="${HERMUS_RAW_SETUP:-https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/setup.sh}"
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
OPENROUTER_KEY=""
OPENAI_KEY=""
CUSTOM_PROVIDER=""
CUSTOM_KEY=""
CUSTOM_BASE_URL=""
CUSTOM_MODEL=""
INSTALL_DIR=""
SKIP_CLONE=0
PULL_MODEL="${HERMUS_OLLAMA_MODEL:-llama3.1:8b}"
GATEWAY_PORT="${HERMUS_GATEWAY_PORT:-8000}"

# colors
if [[ -t 1 ]]; then
  C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'
  C_BLUE='\033[0;34m'; C_BOLD='\033[1m'; C_DIM='\033[2m'; C_NC='\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_BLUE=''; C_BOLD=''; C_DIM=''; C_NC=''
fi

log()  { echo -e "${C_BLUE}[hermus]${C_NC} $*"; }
ok()   { echo -e "${C_GREEN}[  ok  ]${C_NC} $*"; }
warn() { echo -e "${C_YELLOW}[ warn ]${C_NC} $*"; }
err()  { echo -e "${C_RED}[error ]${C_NC} $*" >&2; }
die()  { err "$*"; exit 1; }
header() {
  echo ""
  echo -e "${C_BOLD}☤ Hermus Agent Free — Setup${C_NC}"
  echo -e "${C_DIM}   The agent that grows with you · free · self-hosted${C_NC}"
  echo ""
}

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \?//'
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

# Also accept env keys
GROQ_KEY="${GROQ_KEY:-${GROQ_API_KEY:-}}"
OPENROUTER_KEY="${OPENROUTER_KEY:-${OPENROUTER_API_KEY:-}}"
OPENAI_KEY="${OPENAI_KEY:-${OPENAI_API_KEY:-}}"

header

# ---------- helpers ----------
have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  local msg="$1"
  if [[ "$ASSUME_YES" == "1" ]]; then return 0; fi
  if [[ ! -t 0 ]]; then return 0; fi  # piped curl|bash → yes
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
  for c in python3.12 python3.11 python3.10 python3; do
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
        if [[ "$(id -u)" -eq 0 ]]; then
          SUDO=""
        elif have sudo; then
          SUDO="sudo"
        else
          warn "No sudo — skipping apt packages (git/python/venv/ffmpeg). Install them manually if missing."
          return 0
        fi
        if confirm "Install system packages via apt (git, python3, venv, pip, curl, ffmpeg)?"; then
          $SUDO apt-get update -y
          $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
            git curl ca-certificates \
            python3 python3-venv python3-pip python3-dev \
            build-essential \
            ffmpeg \
            || warn "Some apt packages failed — continuing if Python works"
        fi
      elif have dnf; then
        warn "Fedora/RHEL detected — ensure: git python3 python3-pip python3-virtualenv ffmpeg"
      elif have pacman; then
        warn "Arch detected — ensure: git python python-pip ffmpeg"
      fi
      ;;
    macos)
      if ! have brew; then
        warn "Homebrew not found. Install from https://brew.sh if setup fails."
      else
        if confirm "Install/update git, python, ffmpeg via Homebrew?"; then
          brew install git python ffmpeg || warn "brew install had issues — continuing"
        fi
      fi
      ;;
    windows)
      warn "Windows: use WSL2 (Ubuntu) and re-run this script inside WSL for best results."
      ;;
  esac
}

resolve_repo_dir() {
  # If we're already inside the repo (hermus.py present), use it.
  local here
  here="$(pwd)"
  if [[ -f "$here/hermus.py" && -f "$here/requirements.txt" ]]; then
    SKIP_CLONE=1
    echo "$here"
    return 0
  fi
  # If script path is inside a clone
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$script_dir/hermus.py" && -f "$script_dir/requirements.txt" ]]; then
      SKIP_CLONE=1
      echo "$script_dir"
      return 0
    fi
  fi
  # Fresh install location
  if [[ -n "$INSTALL_DIR" ]]; then
    echo "$INSTALL_DIR"
  else
    echo "$HOME/$HERMUS_DIR_NAME"
  fi
}

clone_or_update() {
  local dir="$1"
  if [[ "$SKIP_CLONE" == "1" ]]; then
    ok "Using existing repo: $dir"
    if [[ -d "$dir/.git" ]] && have git; then
      if confirm "Pull latest changes from GitHub?"; then
        git -C "$dir" pull --ff-only || warn "git pull failed (continuing with local copy)"
      fi
    fi
    return 0
  fi

  if [[ -d "$dir/.git" ]]; then
    ok "Repo already at $dir"
    if confirm "Pull latest changes?"; then
      git -C "$dir" pull --ff-only || warn "git pull failed"
    fi
    return 0
  fi

  if [[ -d "$dir" && ! -f "$dir/hermus.py" ]]; then
    die "Directory exists but is not Hermus: $dir (use --dir PATH)"
  fi

  have git || die "git is required. Install git and re-run."
  log "Cloning $HERMUS_REPO_URL → $dir"
  mkdir -p "$(dirname "$dir")"
  git clone --depth 1 "$HERMUS_REPO_URL" "$dir"
  ok "Cloned Hermus"
}

setup_venv() {
  local dir="$1"
  local py="$2"
  cd "$dir"
  log "Creating virtualenv (.venv) with $py …"
  if [[ ! -d .venv ]]; then
    "$py" -m venv .venv
  else
    ok "Virtualenv already exists"
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip setuptools wheel
  ok "venv ready: $(python --version 2>&1)"
}

install_python_deps() {
  local dir="$1"
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate

  log "Installing Python dependencies (this may take a few minutes)…"
  if [[ "$MINIMAL" == "1" ]]; then
    pip install \
      "pydantic>=2.0.0" "python-dotenv>=1.0.0" "requests>=2.31.0" \
      "tiktoken>=0.5.0" "duckduckgo-search>=5.0.0" \
      "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" \
      "APScheduler>=3.10.0" "prompt_toolkit>=3.0.41" "rich>=13.0.0" \
      "pytest>=8.0.0" "Pillow>=10.0.0" "groq>=0.4.0" \
      || die "pip install minimal failed"
    ok "Minimal Python deps installed"
  else
    # Full requirements — tolerate optional heavy packages failing
    if ! pip install -r requirements.txt; then
      warn "Full requirements.txt had errors — installing core set"
      pip install \
        "pydantic>=2.0.0" "python-dotenv>=1.0.0" "requests>=2.31.0" \
        "tiktoken>=0.5.0" "duckduckgo-search>=5.0.0" \
        "fastapi>=0.104.0" "uvicorn[standard]>=0.24.0" \
        "APScheduler>=3.10.0" "prompt_toolkit>=3.0.41" "rich>=13.0.0" \
        "pytest>=8.0.0" "Pillow>=10.0.0" "groq>=0.4.0" \
        "huggingface_hub>=0.23.0" "paramiko>=3.0.0" \
        "python-telegram-bot>=20.0" "discord.py>=2.3.0" \
        || die "core pip install failed"
    fi
    ok "Python dependencies installed"
  fi

  if [[ "$WITH_VOICE" == "1" && "$MINIMAL" != "1" ]]; then
    log "Ensuring voice (faster-whisper)…"
    pip install "faster-whisper>=0.9.0" || warn "faster-whisper install failed (voice optional)"
  fi

  # ddgs rename friendliness
  pip install "ddgs" >/dev/null 2>&1 || true
}

install_browser() {
  local dir="$1"
  [[ "$WITH_BROWSER" == "1" ]] || { warn "Skipping browser (Playwright)"; return 0; }
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "Installing Playwright + Chromium (browser tools)…"
  pip install "playwright>=1.40.0" || { warn "playwright pip failed"; return 0; }
  if python -m playwright install chromium; then
    ok "Playwright Chromium installed"
  else
    # Linux often needs deps
    if [[ "$OS" == "linux" ]] && have sudo; then
      warn "Trying playwright install-deps (needs sudo)…"
      sudo python -m playwright install-deps chromium 2>/dev/null || true
      python -m playwright install chromium || warn "Chromium install failed — browser tools may not work"
    else
      warn "Chromium install failed — run later: python -m playwright install chromium"
    fi
  fi
}

install_ollama() {
  [[ "$WITH_OLLAMA" == "1" ]] || return 0
  log "Setting up Ollama (local free LLM)…"
  if ! have ollama; then
    if [[ "$OS" == "linux" || "$OS" == "macos" ]]; then
      if confirm "Install Ollama now?"; then
        curl -fsSL https://ollama.com/install.sh | sh || warn "Ollama install script failed"
      fi
    else
      warn "Install Ollama manually: https://ollama.com"
      return 0
    fi
  else
    ok "Ollama already installed"
  fi

  if have ollama; then
    # start server if needed
    if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      log "Starting ollama serve in background…"
      nohup ollama serve >/tmp/hermus-ollama.log 2>&1 &
      sleep 2
    fi
    log "Pulling model: $PULL_MODEL (large download on first run)…"
    ollama pull "$PULL_MODEL" || warn "ollama pull $PULL_MODEL failed"
    # lightweight embed model for semantic memory
    ollama pull nomic-embed-text >/dev/null 2>&1 || true
    ok "Ollama ready"
  fi
}

write_launchers() {
  local dir="$1"
  cd "$dir"

  # bin/hermus — always uses venv
  mkdir -p bin
  cat > bin/hermus << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
else
  echo "Hermus venv missing. Run: bash setup.sh" >&2
  exit 1
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python "$ROOT/hermus.py" "$@"
EOF
  chmod +x bin/hermus

  cat > bin/hermus-gateway << EOF
#!/usr/bin/env bash
set -euo pipefail
ROOT="\$(cd "\$(dirname "\$0")/.." && pwd)"
# shellcheck disable=SC1091
source "\$ROOT/.venv/bin/activate"
export PYTHONPATH="\$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
PORT="\${1:-$GATEWAY_PORT}"
echo "☤ Starting Hermus gateway on http://0.0.0.0:\${PORT}"
echo "   Dashboard: http://localhost:\${PORT}/dashboard"
exec python "\$ROOT/hermus.py" gateway start --port "\$PORT"
EOF
  chmod +x bin/hermus-gateway

  cat > activate.sh << 'EOF'
#!/usr/bin/env bash
# Usage: source activate.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$ROOT/bin:$PATH"
echo "☤ Hermus env active. Try: hermus --help   |   hermus-gateway"
EOF
  chmod +x activate.sh

  # Convenience root launcher
  cat > hermus << 'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/hermus" "$@"
EOF
  chmod +x hermus

  ok "Launchers: ./hermus  ./bin/hermus  ./bin/hermus-gateway  source activate.sh"
}

write_env_example() {
  local dir="$1"
  cat > "$dir/.env.example" << 'EOF'
# Copy to .env and fill in (optional — keys can also be added via CLI/dashboard)
# GROQ_API_KEY=gsk_...
# OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=sk-...
# GEMINI_API_KEY=AIza...
# TELEGRAM_BOT_TOKEN=123:ABC
# DISCORD_BOT_TOKEN=...
# HERMUS_GATEWAY_TOKEN=change-me
# HERMUS_MAX_TOOL_STEPS=8
# HERMUS_TELEGRAM_MODE=auto
EOF
  if [[ ! -f "$dir/.env" ]]; then
    cp "$dir/.env.example" "$dir/.env"
  fi
}

mkdirs_data() {
  local dir="$1"
  mkdir -p "$dir/data" "$dir/data/sessions" "$dir/data/tmp" "$dir/data/skins"
  ok "Data directories ready"
}

register_keys() {
  local dir="$1"
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  export PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}"

  add_one() {
    local provider="$1" key="$2" name="$3" base="${4:-}" model="${5:-}"
    [[ -n "$key" ]] || return 0
    log "Registering $provider key ($name)…"
    local args=(multikey add --provider "$provider" --key "$key" --name "$name")
    [[ -n "$base" ]] && args+=(--base-url "$base")
    [[ -n "$model" ]] && args+=(--model "$model")
    python hermus.py "${args[@]}" || warn "Failed to add $provider key"
  }

  add_one groq "$GROQ_KEY" "setup_groq"
  add_one openrouter "$OPENROUTER_KEY" "setup_openrouter"
  add_one openai "$OPENAI_KEY" "setup_openai"
  if [[ -n "$CUSTOM_KEY" && -n "${CUSTOM_BASE_URL:-}" ]]; then
    add_one "${CUSTOM_PROVIDER:-custom}" "$CUSTOM_KEY" "setup_custom" "$CUSTOM_BASE_URL" "$CUSTOM_MODEL"
  elif [[ -n "$CUSTOM_KEY" ]]; then
    add_one "${CUSTOM_PROVIDER:-custom}" "$CUSTOM_KEY" "setup_custom" "" "$CUSTOM_MODEL"
  fi
}

verify_install() {
  local dir="$1"
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  export PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}"

  log "Verifying install…"
  python - << 'PY'
import sys
print("Python", sys.version.split()[0])
mods = [
  "core.config", "core.agent", "core.llm", "core.multi_key",
  "core.model_fleet", "core.tool_registry", "core.embeddings",
  "gateway.gateway", "gateway.channels",
]
failed = []
for m in mods:
    try:
        __import__(m)
        print("  OK", m)
    except Exception as e:
        print("  FAIL", m, e)
        failed.append(m)
from core.tool_registry import tool_registry
tool_registry.load(force=True)
info = tool_registry.list_tools()
print(f"  Tools registered: {info['count']}")
if info["count"] < 20:
    failed.append("tools")
if failed:
    print("VERIFY_FAILED", ",".join(failed))
    sys.exit(1)
print("VERIFY_OK")
PY
  ok "Verification passed"

  # quick CLI smoke
  python hermus.py multikey providers >/dev/null
  python hermus.py tools >/dev/null || true
  ok "CLI responds"
}

print_next_steps() {
  local dir="$1"
  local default_model="mock/mock"
  if have ollama && curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    default_model="ollama/${PULL_MODEL}"
  elif [[ -n "$GROQ_KEY" ]]; then
    default_model="groq/openai/gpt-oss-20b"
  elif [[ -n "$OPENROUTER_KEY" ]]; then
    default_model="openrouter/auto"
  elif [[ -n "$OPENAI_KEY" ]]; then
    default_model="openai/gpt-4o-mini"
  fi

  cat << EOF

${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════${C_NC}
${C_GREEN}${C_BOLD}  ☤ Hermus is ready!${C_NC}
${C_GREEN}${C_BOLD}═══════════════════════════════════════════════════════════${C_NC}

${C_BOLD}Install path:${C_NC}  $dir

${C_BOLD}1) Activate (each new terminal):${C_NC}
   cd "$dir"
   source activate.sh

${C_BOLD}2) Chat:${C_NC}
   ./hermus --model ${default_model}
   # or:  hermus --mode multi-agent --model ${default_model}

${C_BOLD}3) Dashboard + API gateway:${C_NC}
   ./bin/hermus-gateway
   # open http://localhost:${GATEWAY_PORT}/dashboard

${C_BOLD}4) Add more API keys (any OpenAI-compatible gateway):${C_NC}
   ./hermus multikey add --provider groq --key gsk_...
   ./hermus multikey add --provider custom --key sk_... \\
       --base-url https://your-gateway.example.com/v1 --model my-model
   ./hermus multikey health
   ./hermus multikey providers

${C_BOLD}5) Multi-model fleet:${C_NC}
   ./hermus fleet run "Your hard goal" --strategy auto

${C_BOLD}Optional env keys for next time:${C_NC}
   export GROQ_API_KEY=gsk_...
   export TELEGRAM_BOT_TOKEN=...   # then restart gateway for Telegram

${C_DIM}Docs: README.md · SIMPLE_GUIDE.md · http://localhost:${GATEWAY_PORT}/docs${C_NC}

EOF
}

start_gateway_now() {
  local dir="$1"
  [[ "$START_GATEWAY" == "1" ]] || return 0
  cd "$dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  export PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}"
  log "Starting gateway on port $GATEWAY_PORT …"
  echo "Dashboard: http://localhost:${GATEWAY_PORT}/dashboard"
  exec python hermus.py gateway start --port "$GATEWAY_PORT"
}

# ---------- main ----------
main() {
  install_system_deps

  PY="$(find_python || true)"
  if [[ -z "${PY:-}" ]]; then
    die "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ required. Install python3 and re-run."
  fi
  ok "Found $($PY --version 2>&1)"

  REPO_DIR="$(resolve_repo_dir)"
  log "Target directory: $REPO_DIR"
  clone_or_update "$REPO_DIR"
  cd "$REPO_DIR"

  setup_venv "$REPO_DIR" "$PY"
  install_python_deps "$REPO_DIR"
  install_browser "$REPO_DIR"
  install_ollama
  mkdirs_data "$REPO_DIR"
  write_launchers "$REPO_DIR"
  write_env_example "$REPO_DIR"
  register_keys "$REPO_DIR"
  verify_install "$REPO_DIR"
  print_next_steps "$REPO_DIR"
  start_gateway_now "$REPO_DIR"
}

main "$@"
