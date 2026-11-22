"""Measure the first screen: page-load traffic, starvation and time-to-answer.

One script, four numbers, measured the same way before and after a change:

1. ``page_load_requests`` — how many requests the Control Room issues in its
   first ten seconds.
2. ``pending_after_10s`` — how many of those are still in flight ten seconds
   after the load event, and how many sockets Chrome holds to the gateway.
3. ``first_screen_ms`` — p50/p95 of every request that starts in the first
   three seconds (the ones a person is waiting on).
4. ``answer_ms`` — type a command, press Enter, and how long until the answer
   text is in the conversation.

It also times ``POST /jobs`` from inside the page and again from plain HTTP on
the same server: if the first is seconds and the second is milliseconds, the
request was waiting for a socket, not for the gateway.

Usage::

    python scripts/measure_first_screen.py --base http://127.0.0.1:8211 \
        --out ~/.capy/work/hrs52/before.json

Chromium is optional for CI: with no browser installed the script exits 3 and
says so instead of failing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def _socket_count(port: int) -> int | None:
    """TCP sockets established to ``port`` from any local process (Chrome's)."""
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:  # noqa: BLE001
        return None
    return sum(1 for c in conns if c.raddr and c.raddr.port == port and c.status == "ESTABLISHED")


def measure(base: str, out: Path, prompt: str, answer_timeout: float) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        print(f"playwright unavailable: {exc}", file=sys.stderr)
        raise SystemExit(3) from None

    port = int(base.rsplit(":", 1)[1].rstrip("/"))
    report: dict[str, Any] = {"base": base, "prompt": prompt}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            print(f"chromium unavailable: {exc}", file=sys.stderr)
            raise SystemExit(3) from None
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")

        # (request_id -> record) so a request that never finishes stays visible.
        inflight: dict[str, dict[str, Any]] = {}
        finished: list[dict[str, Any]] = []
        t_origin = time.time()

        def on_sent(params: dict[str, Any]) -> None:
            req = params.get("request", {}) or {}
            inflight[params["requestId"]] = {
                "url": req.get("url", ""),
                "method": req.get("method", ""),
                "at": time.time() - t_origin,
                "type": params.get("type", ""),
            }

        def on_done(params: dict[str, Any]) -> None:
            rec = inflight.pop(params["requestId"], None)
            if rec:
                rec["done_at"] = time.time() - t_origin
                finished.append(rec)

        cdp.on("Network.requestWillBeSent", on_sent)
        cdp.on("Network.loadingFinished", on_done)
        cdp.on("Network.loadingFailed", on_done)

        page.goto(base + "/control", wait_until="load", timeout=60000)
        load_at = time.time() - t_origin
        page.wait_for_timeout(10000)

        sockets_10s = _socket_count(port)
        sample_at = time.time() - t_origin
        # A live dashboard always has a poll in flight at any instant. What
        # matters is whether a request *hangs*: count only those already open
        # for more than a second when the page has been sitting idle.
        stuck = {rid: c for rid, c in inflight.items() if sample_at - c["at"] > 1.0}
        report["load_at_s"] = round(load_at, 2)
        report["sockets_10s"] = sockets_10s
        report["page_load_requests"] = len(finished) + len(inflight)
        report["pending_after_10s"] = len(inflight)
        report["stuck_over_1s"] = len(stuck)
        report["stuck_urls"] = [c["url"].replace(base, "") for c in stuck.values()][:20]
        report["max_inflight_age_ms"] = round(max(sample_at - c["at"] for c in inflight.values()) * 1000, 1) if inflight else 0.0
        report["boot_urls"] = sorted({c["url"].replace(base, "") for c in finished + list(inflight.values())})
        report["by_type"] = {}
        for c in finished + list(inflight.values()):
            kind = c.get("type") or "Other"
            report["by_type"][kind] = report["by_type"].get(kind, 0) + 1

        first = [round((c["done_at"] - c["at"]) * 1000, 1) for c in finished if c["at"] <= 3.0]
        report["first_screen_ms"] = {
            "n": len(first),
            "p50": _pct(first, 50),
            "p95": _pct(first, 95),
            "max": round(max(first), 1) if first else None,
            "over_300ms": sorted(
                (
                    {"url": c["url"].replace(base, ""), "ms": round((c["done_at"] - c["at"]) * 1000, 1)}
                    for c in finished
                    if c["at"] <= 3.0 and (c["done_at"] - c["at"]) > 0.3
                ),
                key=lambda r: -r["ms"],
            )[:20],
        }

        # Server-side control: the same POST, issued from this process instead
        # of the browser, against the same gateway.
        report["jobs_server_ms"] = _post_jobs_direct(base)

        # In-page POST /jobs: measured while the page is still holding sockets.
        page.evaluate("() => localStorage.removeItem('hermus_transcript_v1')")
        jobs_in_page = page.evaluate(
            """async () => {
                const t0 = performance.now();
                const r = await fetch('/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({kind:'runtime.turn', payload:{text:'measure socket wait', platform:'web', mode:'chat', stream:false, user_id:'default'}})});
                const body = await r.text();
                return {status: r.status, ms: performance.now() - t0, body: body.slice(0, 200)};
            }"""
        )
        report["jobs_in_page_ms"] = round(jobs_in_page["ms"], 1)
        report["jobs_in_page_status"] = jobs_in_page["status"]
        report["sockets_before_prompt"] = _socket_count(port)

        # The number a person feels: type, press Enter, wait for the answer.
        page.reload(wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        # Count the answers already in the conversation (the greeting), so the
        # latency below measures THIS command's answer and nothing else.
        baseline = page.evaluate("() => document.querySelectorAll('#transcriptBody .msg.hermus').length")
        page.fill("#command", prompt)
        started = time.time()
        page.press("#command", "Enter")
        answer_text = ""
        answer_at = None
        deadline = started + answer_timeout
        while time.time() < deadline:
            page.wait_for_timeout(250)
            row = page.evaluate(
                """(baseline) => {
                    const rows = [...document.querySelectorAll('#transcriptBody .msg.hermus')];
                    if (rows.length <= baseline) return '';
                    const last = rows[rows.length - 1];
                    return last ? (last.innerText || '').trim() : '';
                }""",
                baseline,
            )
            if row and len(row) > 24:
                answer_text = row
                answer_at = time.time() - started
                break
        report["answer_ms"] = round(answer_at * 1000, 1) if answer_at else None
        report["answer_chars"] = len(answer_text)
        report["answer_head"] = answer_text[:300]
        report["sockets_after_answer"] = _socket_count(port)
        page.screenshot(path=str(out.with_suffix(".png")), full_page=False)
        browser.close()

    return report


def _post_jobs_direct(base: str) -> float | None:
    try:
        import requests
    except Exception:  # noqa: BLE001
        return None
    payload = {
        "kind": "runtime.turn",
        "payload": {"text": "measure direct", "platform": "web", "mode": "chat", "stream": False, "user_id": "default"},
    }
    try:
        t0 = time.perf_counter()
        requests.post(base + "/jobs", json=payload, timeout=60)
        return round((time.perf_counter() - t0) * 1000, 1)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8211")
    ap.add_argument("--out", default="first-screen.json")
    ap.add_argument("--prompt", default="Hello HERMUS, what can you do?")
    ap.add_argument("--answer-timeout", type=float, default=60.0)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    report = measure(args.base.rstrip("/"), out, args.prompt, args.answer_timeout)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {
        k: report.get(k)
        for k in (
            "page_load_requests",
            "pending_after_10s",
            "stuck_over_1s",
            "max_inflight_age_ms",
            "sockets_10s",
            "jobs_in_page_ms",
            "jobs_server_ms",
            "answer_ms",
        )
    }
    summary["first_screen_p95_ms"] = (report.get("first_screen_ms") or {}).get("p95")
    print(json.dumps(summary, indent=1))
    print(f"wrote {out}")
    if report.get("answer_ms") is None:
        print("no answer arrived", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
