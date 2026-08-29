#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$ROOT/bin:$PATH"
if [ -d "$HOME/.bun/bin" ]; then
  export PATH="$HOME/.bun/bin:$PATH"
fi
echo "☤ Hermus environment active! Run 'hermus --help' or 'hermus-gateway'"
