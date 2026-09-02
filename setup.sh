#!/usr/bin/env bash
#
# Hermus Agent Free — safe, idempotent installer/orchestrator.
#
# The shell layer owns only host/OS detection and host packages.  The canonical
# Python bootstrap owns .venv, dependency files, runtime paths and Doctor/live
# verification.  It is intentionally a thin wrapper so setup, the CLI and the
# gateway health endpoint cannot drift into three different installations.
#
# Supported hosts: Linux, macOS and Android/Termux.  Windows is not silently
# guessed or partially installed by this POSIX script.
#
# Usage:
#   ./setup.sh                    install/repair and run live verification
#   ./setup.sh --repair           repair a broken venv/dependency installation
#   ./setup.sh --verify-only      do not install; run the live Doctor report
#   ./setup.sh --skip-system      do not call the host package manager
#   ./setup.sh --skip-browser     do not download Chromium (verification fails
#                                  if the configured browser is required)
#   ./setup.sh --skip-optional    leave optional integrations untouched
#

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -t 1 ]; then
  C_CYAN=$'\033[0;36m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[1;33m'
  C_RED=$'\033[0;31m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_RESET=""
fi

usage() {
  sed -n '1,34p' "$0"
}

VERIFY_ONLY=0
REPAIR=0
SKIP_SYSTEM=0
SKIP_BROWSER=0
SKIP_OPTIONAL=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=1 ;;
    --repair) REPAIR=1 ;;
    --skip-system) SKIP_SYSTEM=1 ;;
    --skip-browser) SKIP_BROWSER=1 ;;
    --skip-optional) SKIP_OPTIONAL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown setup option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%b\n' "$*"; }
info() { say "${C_CYAN}➜${C_RESET} $*"; }
ok() { say "${C_GREEN}✓${C_RESET} $*"; }
warn() { say "${C_YELLOW}⚠${C_RESET} $*"; }
fatal() { say "${C_RED}${C_BOLD}✗ SETUP HOST CHECK FAILED:${C_RESET} $*" >&2; exit 1; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

# Run a privileged host command. A missing privilege path is a real error for
# required packages; callers use it only inside an explicit if/then branch.
privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command_exists sudo; then
    sudo "$@"
  else
    say "No root privileges and sudo is unavailable; cannot run: $*" >&2
    return 1
  fi
}

OS_NAME="$(uname -s 2>/dev/null || printf 'unknown')"
TERMUX=0
case "${TERMUX_VERSION:-}" in
  "") ;;
  *) TERMUX=1 ;;
esac
case "${PREFIX:-}" in
  *com.termux*) TERMUX=1 ;;
esac
case "${PATH:-}" in
  *com.termux*) TERMUX=1 ;;
esac

if [ "$TERMUX" -eq 1 ]; then
  PLATFORM_FAMILY="android-termux"
else
  case "$OS_NAME" in
    Linux) PLATFORM_FAMILY="linux" ;;
    Darwin) PLATFORM_FAMILY="macos" ;;
    *) fatal "unsupported host '$OS_NAME'. Use Linux, macOS or Android/Termux." ;;
  esac
fi

say "${C_CYAN}${C_BOLD}HERMUS SETUP — ${PLATFORM_FAMILY}${C_RESET}"
say "Repository: $SCRIPT_DIR"
if [ "$VERIFY_ONLY" -eq 1 ]; then
  info "Verification-only mode: no package manager, venv, env file or optional install changes will be made."
elif [ "$REPAIR" -eq 1 ]; then
  info "Repair mode: existing user data/configuration is preserved; broken runtime pieces may be repaired."
fi

if [ ! -r "$SCRIPT_DIR/bootstrap.py" ] || [ ! -r "$SCRIPT_DIR/requirements.txt" ]; then
  fatal "bootstrap.py and requirements.txt must exist in the repository root."
fi

PYTHON_CMD=""
select_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command_exists "$candidate" && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PYTHON_CMD="$candidate"
      return 0
    fi
  done
  return 1
}

# The system checks are skipped only when explicitly requested. Even then the
# canonical bootstrap must still report a missing host/runtime component.
if [ "$VERIFY_ONLY" -eq 0 ] && [ "$SKIP_SYSTEM" -eq 0 ]; then
  info "Checking host developer tools and installing only missing required packages"
  if ! select_python; then
    PYTHON_CMD=""
  fi

  case "$PLATFORM_FAMILY" in
    android-termux)
      if [ -z "$PYTHON_CMD" ] || ! command_exists git || ! command_exists curl; then
        if ! command_exists pkg; then
          fatal "Python 3.10+ or Git is missing and Termux pkg is unavailable. Install them, then re-run setup.sh."
        fi
        info "Termux: installing required Python/Git/Curl packages with pkg"
        if ! pkg update -y; then
          fatal "Termux package index update failed; fix pkg/network access and re-run setup.sh."
        fi
        if ! pkg install -y python git curl; then
          fatal "Termux could not install required python/git/curl packages."
        fi
      fi
      ;;
    linux)
      if [ -z "$PYTHON_CMD" ] || ! command_exists git || ! command_exists curl; then
        if ! command_exists apt-get && ! command_exists dnf && ! command_exists yum && ! command_exists pacman && ! command_exists apk; then
          fatal "Python 3.10+ or Git is missing and no supported Linux package manager was found."
        fi
        if command_exists apt-get; then
          info "Linux: installing required Python/Git/venv/build tooling with apt"
          if ! privileged apt-get update; then
            fatal "apt-get update failed while required host packages are missing."
          fi
          if ! privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y \
              python3 python3-pip python3-venv python3-dev git curl ca-certificates build-essential; then
            fatal "apt-get could not install required Python/Git/venv packages."
          fi
        elif command_exists dnf; then
          if ! privileged dnf install -y python3 python3-pip python3-devel git curl ca-certificates gcc gcc-c++; then
            fatal "dnf could not install required Python/Git/venv packages."
          fi
        elif command_exists yum; then
          if ! privileged yum install -y python3 python3-pip python3-devel git curl ca-certificates gcc gcc-c++; then
            fatal "yum could not install required Python/Git/venv packages."
          fi
        elif command_exists pacman; then
          if ! privileged pacman -Sy --noconfirm python python-pip git curl ca-certificates base-devel; then
            fatal "pacman could not install required Python/Git/venv packages."
          fi
        else
          if ! privileged apk add python3 py3-pip python3-dev git curl ca-certificates build-base; then
            fatal "apk could not install required Python/Git/venv packages."
          fi
        fi
      fi
      ;;
    macos)
      if [ -z "$PYTHON_CMD" ] || ! command_exists git || ! command_exists curl; then
        if ! command_exists brew; then
          fatal "Python 3.10+ or Git is missing and Homebrew is unavailable. Install Homebrew or the required tools, then re-run setup.sh."
        fi
        info "macOS: installing missing Python/Git tooling with Homebrew"
        if ! brew install python@3.12 git curl; then
          fatal "Homebrew could not install required Python/Git packages."
        fi
        brew_python_prefix=""
        if brew_python_prefix="$(brew --prefix python@3.12 2>/dev/null)"; then
          export PATH="$brew_python_prefix/bin:$PATH"
        fi
      fi
      ;;
  esac

  select_python || fatal "Python 3.10+ is required but no supported interpreter was found."
  ok "Host Python selected: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"

  if ! "$PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
    if ! "$PYTHON_CMD" -m ensurepip --upgrade; then
      fatal "Python pip is unavailable and ensurepip could not repair it."
    fi
  fi
  if ! "$PYTHON_CMD" -m venv --help >/dev/null 2>&1; then
    fatal "Python venv support is unavailable; install python3-venv (Linux) or a full Python distribution (macOS/Termux)."
  fi
  command_exists git || fatal "Git is required by the current updater/project workflow."
  command_exists curl || fatal "curl is required for secure runtime/package downloads."
  if ! git -C "$SCRIPT_DIR" rev-parse --show-toplevel >/dev/null 2>&1; then
    warn "This directory is not a Git worktree; Git is installed but updater metadata cannot be verified."
  else
    ok "Git worktree detected"
  fi

  # Node/npm are used by the checked-in JavaScript client tests, but there is no
  # Node application/package manifest in this repository. Try the native package
  # manager when straightforward; failure remains an explicit optional report,
  # never a hidden required failure.
  if ! command_exists node || ! command_exists npm; then
    case "$PLATFORM_FAMILY" in
      android-termux)
        if command_exists pkg; then
          if ! pkg install -y nodejs-lts; then warn "Optional Node.js/npm install failed on Termux."; fi
        else
          warn "Node.js/npm unavailable on Termux (optional: no Node package manifest is present)."
        fi
        ;;
      linux)
        if command_exists apt-get; then
          if ! privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm; then
            warn "Optional Node.js/npm install failed; Python Hermus remains installable."
          fi
        else
          warn "Node.js/npm unavailable (optional: no Node package manifest is present)."
        fi
        ;;
      macos)
        if command_exists brew; then
          if ! brew install node; then warn "Optional Node.js/npm install failed; Python Hermus remains installable."; fi
        else
          warn "Node.js/npm unavailable (optional: no Node package manifest is present)."
        fi
        ;;
    esac
  fi
  if command_exists bun; then
    ok "Optional Bun runtime detected: $(bun --version 2>/dev/null || printf 'version unavailable')"
  else
    warn "Optional Bun runtime unavailable (no Bun package manifest is present; Node/npm remain the supported JS check)."
  fi
else
  if [ "$VERIFY_ONLY" -eq 1 ]; then
    info "Host package installation skipped by --verify-only"
  else
    info "Host package installation skipped by --skip-system"
  fi
fi

# Playwright can install distro libraries with its own supported resolver. Do
# this only on ordinary Linux; Termux's package layout is different and the
# Doctor will classify an unavailable browser as platform-optional.
if [ "$VERIFY_ONLY" -eq 0 ] && [ "$SKIP_SYSTEM" -eq 0 ] && [ "$PLATFORM_FAMILY" = "linux" ]; then
  export HERMUS_PLAYWRIGHT_WITH_DEPS=1
fi

# Keep the repository root on the bootstrap process path. The final delegation
# uses the existing launcher, which selects the same .venv interpreter.
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

BOOTSTRAP_ARGS=()
if [ "$VERIFY_ONLY" -eq 1 ]; then BOOTSTRAP_ARGS+=(--verify-only); fi
if [ "$REPAIR" -eq 1 ]; then BOOTSTRAP_ARGS+=(--repair); fi
if [ "$SKIP_BROWSER" -eq 1 ]; then BOOTSTRAP_ARGS+=(--skip-browser); fi
if [ "$SKIP_OPTIONAL" -eq 1 ]; then BOOTSTRAP_ARGS+=(--skip-optional); fi

if [ ! -x "$SCRIPT_DIR/bin/hermus" ]; then
  if ! chmod u+x "$SCRIPT_DIR/bin/hermus"; then
    fatal "bin/hermus exists but cannot be made executable; fix its permissions and re-run setup.sh."
  fi
fi

say "${C_CYAN}➜${C_RESET} Delegating to canonical bootstrap/Doctor (same interpreter used by Hermus launchers)"
# Keep this as the one canonical entry point. bootstrap.py re-execs into .venv
# when setup was started with a system Python.
exec "./bin/hermus" bootstrap "${BOOTSTRAP_ARGS[@]}"
