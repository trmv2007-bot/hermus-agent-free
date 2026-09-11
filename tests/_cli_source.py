"""Test helper: concatenated CLI source text.

The CLI implementation split out of ``hermus.py`` into the ``hermus_cli/``
package (one module per command group). Tests that pin CLI surface text
(parser names, flags) should search this instead of ``hermus.py`` alone.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cli_source() -> str:
    """All CLI source (shim + every command module) as one string."""
    parts = [(ROOT / "hermus.py").read_text(encoding="utf-8")]
    for mod in sorted((ROOT / "hermus_cli").glob("*.py")):
        parts.append(mod.read_text(encoding="utf-8"))
    return "\n".join(parts)
