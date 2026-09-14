#!/usr/bin/env python3
"""Hermus Agent Free - CLI Entry Point.

Thin shim: the implementation lives in :mod:`hermus_cli` (one module per
command group). Kept at this path so ``./hermus``, ``./bin/hermus``,
``python hermus.py`` and existing muscle memory keep working.
"""

import sys
from pathlib import Path

# Ensure imports work when invoked as `python hermus.py` (bin/hermus also sets PYTHONPATH).
sys.path.insert(0, str(Path(__file__).parent))

from hermus_cli import main

if __name__ == "__main__":
    main()
