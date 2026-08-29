#!/usr/bin/env bash
# =============================================================================
# Hermus Agent Free — One-Line Download & Setup Wrapper
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash
# =============================================================================

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e
set -u
(set -o pipefail 2>/dev/null) && set -o pipefail

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
  echo "git is required. Please install git and re-run." >&2
  exit 1
fi

if [[ -d "$DIR/.git" ]]; then
  git -C "$DIR" pull --ff-only || true
else
  mkdir -p "$(dirname "$DIR")"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi

exec bash "$DIR/setup.sh" --yes --dir "$DIR" "$@"
