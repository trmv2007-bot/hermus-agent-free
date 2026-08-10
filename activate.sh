#!/usr/bin/env bash
# Usage: source activate.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$ROOT/bin:$PATH"
echo "☤ Hermus env active. Try: hermus --help   |   hermus-gateway"
