#!/usr/bin/env python3
"""Hermus Agent Free - single entrypoint for the CLI.

The command implementations live in ``hermus_cli/`` (grouped command modules
behind one dispatch table). This thin launcher exists so the shell wrappers in
``bin/`` (``hermes``, ``hermes-gateway``) and ``python hermes.py ...`` keep
working after the CLI was split out of this file.

Examples:
    python hermes.py --help
    python hermes.py doctor
    python hermes.py gateway start --port 8000
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from hermus_cli import main as cli_main

    try:
        cli_main()
    except KeyboardInterrupt:
        print(chr(10) + "Interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
