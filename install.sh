#!/usr/bin/env bash
# Forwarder to setup.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/setup.sh" "$@"
