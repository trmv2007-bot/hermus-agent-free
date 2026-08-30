#!/usr/bin/env bash
# DEPRECATED COMPATIBILITY SHIM (Rebuild §18).
# The canonical, one-command bootstrap is:  ./hermus bootstrap
# This file exists only so existing workflows that `source activate.sh` keep
# working. It contains NO business logic — it only activates the venv and
# points users at the canonical command surface.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect if sourced vs executed.
(return 0 2>/dev/null) && IS_SOURCED=1 || IS_SOURCED=0

if [ "$IS_SOURCED" -eq 1 ]; then
  if [ -f "$ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
  fi
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  export PATH="$ROOT/bin:$ROOT:$PATH"
  echo -e "\033[0;32m✓\033[0m \033[1mHermus ready (compat shim).\033[0m"
  echo -e "   Deprecated: use \033[1;36m./hermus bootstrap\033[0m (one command) instead."
else
  echo -e "\033[1;33m⚠️  'activate.sh' is a deprecated compatibility shim.\033[0m"
  echo -e "   The one-command setup is: \033[1;36m./hermus bootstrap\033[0m"
  echo ""
  echo -e "   Start the gateway:  \033[1;36m./hermus-gateway\033[0m  (or \033[1;36m./bin/hermus-gateway\033[0m)"
  echo -e "   Terminal CLI:       \033[1;36m./hermus\033[0m"
fi
