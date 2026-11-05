#!/usr/bin/env bash
#
# HERMUS Agent Free — one-shot repository installer for Linux, macOS and
# Android/Termux. It downloads/clones the repository when needed, then
# delegates the actual environment setup to the canonical setup.sh.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh -o /tmp/hermus-install.sh
#   bash /tmp/hermus-install.sh
#
# Optional environment variables:
#   HERMUS_INSTALL_DIR   target directory (default: ~/hermus-agent-free)
#   HERMUS_REF           branch/tag (default: main)
#
set -Eeuo pipefail

REPO="https://github.com/trmv2007-bot/hermus-agent-free"
REF="${HERMUS_REF:-main}"
DEST="${HERMUS_INSTALL_DIR:-$HOME/hermus-agent-free}"

say() { printf '[HERMUS] %s\n' "$*"; }
fail() { printf '[HERMUS][FAIL] %s\n' "$*" >&2; exit 1; }

[ -n "${HOME:-}" ] || fail "HOME is not set."

if [ -d "$DEST/.git" ]; then
  command -v git >/dev/null 2>&1 || fail "Git is required to update an existing checkout."
  say "Updating existing checkout: $DEST"
  git -C "$DEST" fetch --depth 1 origin "$REF"
  git -C "$DEST" checkout -q "$REF" 2>/dev/null || git -C "$DEST" checkout -q -B "$REF" "origin/$REF"
  git -C "$DEST" pull --ff-only origin "$REF" || fail "Existing checkout has local/diverged changes. Resolve them, then re-run."
elif [ -e "$DEST" ]; then
  fail "Install directory already exists but is not a Git checkout: $DEST. Use setup.sh there, or choose another HERMUS_INSTALL_DIR."
else
  mkdir -p "$(dirname "$DEST")"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT

  if command -v git >/dev/null 2>&1; then
    say "Cloning HERMUS into $DEST"
    git clone --depth 1 --branch "$REF" "$REPO.git" "$DEST"
  else
    command -v tar >/dev/null 2>&1 || fail "tar is required when Git is not installed."
    if command -v curl >/dev/null 2>&1; then
      curl -fL --retry 3 "$REPO/archive/refs/heads/$REF.tar.gz" -o "$TMP/hermus.tar.gz"
    elif command -v wget >/dev/null 2>&1; then
      wget -q --tries=3 "$REPO/archive/refs/heads/$REF.tar.gz" -O "$TMP/hermus.tar.gz"
    else
      fail "curl or wget is required to download HERMUS."
    fi
    tar -xzf "$TMP/hermus.tar.gz" -C "$TMP"
    EXTRACTED="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d -name 'hermus-agent-free-*' | head -n 1)"
    [ -n "$EXTRACTED" ] || fail "Could not locate the extracted HERMUS repository."
    mv "$EXTRACTED" "$DEST"
  fi
fi

cd "$DEST"
[ -x ./setup.sh ] || chmod +x ./setup.sh
say "Starting canonical full installation"
exec ./setup.sh "$@"
