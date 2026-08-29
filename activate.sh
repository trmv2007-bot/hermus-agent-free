#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if script is being sourced vs executed
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
  # User ran 'bash activate.sh' or './activate.sh' instead of 'source activate.sh'
  echo -e "\033[1;33m⚠️  Notice:\033[0m 'activate.sh' must be \033[1mSOURCE-loaded\033[0m to persist in your current terminal:"
  echo -e "  👉 \033[1;32msource activate.sh\033[0m   (or: \033[1;32m. activate.sh\033[0m)"
  echo ""
  echo -e "\033[1mAlternatively, launch directly without activating:\033[0m"
  echo -e "  • Start Dashboard: \033[1;36m./bin/hermus-gateway\033[0m  (or \033[1;36m./hermus-gateway\033[0m)"
  echo -e "  • Terminal CLI:    \033[1;36m./hermus\033[0m"
  echo ""
fi
