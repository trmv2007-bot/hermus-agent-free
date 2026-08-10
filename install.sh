#!/usr/bin/env bash
# Thin wrapper: download Hermus (if needed) and run full setup.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --with-ollama --groq-key gsk_xxx --start
set -euo pipefail

REPO="${HERMUS_REPO_URL:-https://github.com/trmv2007-bot/hermus-agent-free.git}"
DIR="${HERMUS_INSTALL_DIR:-$HOME/hermus-agent-free}"
BRANCH="${HERMUS_BRANCH:-main}"

echo "☤ Hermus install → $DIR"

if [[ -f "./setup.sh" && -f "./hermus.py" ]]; then
  exec bash ./setup.sh --yes "$@"
fi

if [[ -f "$DIR/setup.sh" && -f "$DIR/hermus.py" ]]; then
  exec bash "$DIR/setup.sh" --yes "$@"
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required. Install git, then re-run." >&2
  exit 1
fi

if [[ -d "$DIR/.git" ]]; then
  git -C "$DIR" pull --ff-only || true
else
  mkdir -p "$(dirname "$DIR")"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi

exec bash "$DIR/setup.sh" --yes --dir "$DIR" "$@"
