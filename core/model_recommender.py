"""Rank vision models against what this machine actually has.

The recommender exists to answer one question honestly: *which model should
this person install on this box.* That is a budget calculation, not a taste
judgement, and the number that decides it is free VRAM rather than total VRAM.

Three rules the implementation holds to:

1. **Free VRAM, not total.** A card with 8 GB total and 1.1 GB in use has 6.9
   GB available right now. Recommending against the total produces a model
   that OOMs on the first real question.
2. **Weights are not the whole cost.** A vision encoder builds a large
   activation buffer on a full-screen image. That is in the budget.
3. **Never download.** This module reports what fits. Installing is a separate
   action the person takes deliberately, because these are multi-gigabyte pulls.

If the hardware cannot be measured, the answer is that no recommendation can be
made, rather than a guess.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .hardware_spec import MachineSpecs, detect_specs

CATALOG_PATH = Path(__file__).resolve().parent / "catalogs" / "vision_models.json"

# Ollama keeps model files under one of these; used to tell "installed" from
# "not installed" without asking the daemon for every model.
_OLLAMA_DIRS = [
    Path(os.path.expanduser("~/.ollama/models")),
    Path(os.path.expanduser("~/AppData/Local/Ollama/models")),
    Path(os.path.expanduser("~/Library/Application Support/Ollama/models")),
]


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or CATALOG_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["models"]


def _installed_vision_models() -> set[str]:
    """Which catalog entries are already on disk, per the real runtime."""
    found: set[str] = set()
    for base in _OLLAMA_DIRS:
        if not base.exists():
            continue
        try:
            for p in base.rglob("*.gguf"):
                # Ollama stores files named sha256-<digest>-<part>
                stem = p.stem
                if "-" in stem and stem.count("-") >= 1:
                    found.add(stem.split("-")[-1].lower())
        except Exception:
            continue
    return found


def _ollama_installed() -> list[str]:
    """Ask the daemon, and fall back to the filesystem if it is not running."""
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as r:
            tags = _json.loads(r.read().decode())
        return [t.get("name", "") for t in tags.get("models", [])]
    except Exception:
        return []


def is_installed(entry: dict[str, Any], ollama_names: list[str] | None = None) -> bool:
    names = ollama_names if ollama_names is not None else _ollama_installed()
    want = (entry.get("ollama") or entry.get("id", "")).split(":")[0]
    for n in names:
        if n.split(":")[0] == want:
            return True
    return False


def recommend(
    specs: MachineSpecs | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the ranked, explained recommendation table.

    Shape is stable so the UI never has to special-case a missing field:
    ``{"available": bool, "specs": {...}, "models": [...], "recommended": id|None, "reason": str}``
    """
    s = specs or detect_specs()
    entries = catalog if catalog is not None else load_catalog()
    names = _ollama_installed()

    budget = s.gpu.vram_free_gb
    rows: list[dict[str, Any]] = []

    for e in entries:
        need = float(e.get("required_vram_gb", 0))
        fits = budget is not None and budget >= need
        headroom = round(budget - need, 2) if (budget is not None and fits) else None
        rows.append({
            "id": e["id"],
            "display": e.get("display", e["id"]),
            "download_gb": e.get("download_gb"),
            "required_vram_gb": need,
            "fits": fits,
            "headroom_gb": headroom,
            "quality": e.get("quality"),
            "speed": e.get("speed"),
            "strengths": e.get("strengths", ""),
            "tradeoffs": e.get("tradeoffs", ""),
            "ollama": e.get("ollama", e["id"]),
            "installed": is_installed(e, names),
        })

    # Among what fits, prefer the higher quality; break ties toward speed, then
    # toward the smaller download so the default recommendation is the least
    # demanding thing that is still good.
    fitting = [r for r in rows if r["fits"]]
    fitting.sort(key=lambda r: (-(r["quality"] or 0), -(r["speed"] or 0), r["download_gb"] or 0))

    recommended = fitting[0]["id"] if fitting else None
    if budget is None:
        reason = (
            "Could not measure free VRAM on this machine, so no model is "
            "recommended. Guessing here would hand you a multi-gigabyte "
            "download that may not run."
        )
    elif not fitting:
        reason = (
            f"None of the catalogued vision models fit in {budget} GB of free "
            f"VRAM. The smallest needs {min((r['required_vram_gb'] for r in rows), default=0)} GB. "
            "Free some VRAM (close GPU apps) and ask again."
        )
    else:
        top = fitting[0]
        reason = (
            f"{top['display']} is the best quality that fits entirely in "
            f"{budget} GB of free VRAM, leaving {top['headroom_gb']} GB spare."
        )

    return {
        "available": budget is not None,
        "specs": s.to_dict(),
        "models": rows,
        "recommended": recommended,
        "reason": reason,
        "fits_count": len(fitting),
        "total_count": len(rows),
    }


if __name__ == "__main__":  # pragma: no cover - diagnostic
    print(json.dumps(recommend(), indent=2))
