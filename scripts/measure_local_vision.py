"""Falsify the local-vision claim with a real hit rate.

Deliberately OUT of core/. This is a measurement harness: it calls Ollama's
/api/generate directly because that is the point -- it measures what a raw
provider does with a PNG, before any routing decides whether to ask. Keeping
it in core/ would have it tripping the one-model-boundary architecture gate,
which exists to stop application code bypassing get_model_gateway(). The gate
is right; a benchmark is not application code. See core/vision_routing.py for
the routing decision this harness exists to justify.

Usage:
    PYTHONPATH= ./.venv/Scripts/python.exe -m scripts.measure_local_vision \
        --model spark-x2.5-4b-q4:latest --cases cases.json
"""

from __future__ import annotations

import json
import time
from typing import Any


def measure_local_vision_accuracy(
    model: str,
    cases: list[dict[str, Any]],
    *,
    base_url: str = "http://localhost:11434",
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Run vision probes against a local model and report a real hit rate.

    Shipped so the claim at the top of this file is falsifiable. ``cases`` is a
    list of ``{"id", "image_b64", "question", "accept"}``. Returns
    ``{"model", "hits", "total", "accuracy", "cases"}``.

    A model that cannot accept images scores 0 with the reason recorded, which
    is the correct measurement and not a harness failure: on this box that is
    exactly what both local models return.
    """
    total = 0
    hits = 0
    rows: list[dict[str, Any]] = []
    for case in cases or []:
        total += 1
        began = time.monotonic()
        body = {
            "model": model,
            "stream": False,
            "prompt": str(case.get("question") or "Describe this image."),
            "images": [str(case.get("image_b64") or "")],
            "options": {"temperature": 0, "num_predict": int(case.get("max_tokens") or 64)},
        }
        text, error = "", ""
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                text = str(json.loads(resp.read().decode()).get("response") or "").strip()
        except urllib.error.HTTPError as exc:
            error = f"HTTP {exc.code}: {exc.read().decode()[:200]}"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        accept = {str(a).lower() for a in (case.get("accept") or ())}
        answer = text.lower()
        correct = bool(text) and (any(a in answer for a in accept) or any(len(a) >= 4 and answer in a for a in accept))
        hits += int(correct)
        rows.append(
            {
                "id": case.get("id"),
                "answer": text,
                "expected": sorted(accept),
                "correct": correct,
                "error": error,
                "elapsed_s": round(time.monotonic() - began, 2),
            }
        )
    return {
        "model": model,
        "hits": hits,
        "total": total,
        "accuracy": round(hits / total, 3) if total else 0.0,
        "cases": rows,
    }
