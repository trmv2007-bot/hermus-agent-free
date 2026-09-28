"""Measure what the user actually waits for on one chat turn.

The number that matters is not the total, it is the gap between the last
frame before the first word and the first word itself. A turn that emits
``status: thinking`` and then nothing for 67s has converted a streaming
provider into a blocking one.

Usage:  python tools/measure_chat_latency.py "your question here"

Prints one line per SSE frame with the elapsed time, then a summary:

    first_frame_s / first_token_s / last_token_s / final_s / frame_count
    and, for an escalated turn, how many of those seconds were silent.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def frames(path: str, payload: dict, timeout: int = 300):
    """Yield (elapsed_s, event, data) as each SSE frame lands."""
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        buffer = ""
        for raw in response:
            buffer += raw.decode("utf-8", "replace")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                if not block.strip() or block.lstrip().startswith(":"):
                    continue
                event, data = "message", []
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                if not data:
                    continue
                try:
                    parsed = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    continue
                yield time.monotonic() - started, event, parsed


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "What is the capital of Peru, and why is it there?"
    verbose = "-q" not in sys.argv

    print(f"Q: {question}\n")
    first_frame = first_token = last_token = final_s = None
    count = 0
    chars = 0
    silent_windows: list[tuple[float, float, str]] = []
    previous_t = 0.0
    previous_event = "start"

    for t, event, data in frames("/api/v1/chat", {"message": question}):
        count += 1
        if first_frame is None:
            first_frame = t
        if event == "delta":
            chars += len(data.get("text", ""))
            if first_token is None:
                first_token = t
            last_token = t
        if event == "final":
            final_s = t
        gap = t - previous_t
        # A gap that is not a keepalive and produced no text is dead air.
        if previous_event != "keepalive" and gap > 3.0 and not (event == "delta"):
            silent_windows.append((previous_t, t, previous_event))
        if verbose:
            snippet = ""
            if event == "delta":
                snippet = repr(data.get("text", "")[:60])
            elif event == "activity":
                snippet = str((data.get("data") or {}).get("label", ""))[:70]
            elif event in ("escalated", "grounded"):
                snippet = json.dumps(data)[:130]
            elif event in ("final", "error", "status", "speaking", "keepalive"):
                snippet = json.dumps(data)[:110]
            print(f"  {t:7.2f}s  {event:<10} {snippet}")
        previous_t, previous_event = t, event

    print("\n--- summary ---")
    print(f"  frames          {count}")
    print(f"  first frame     {first_frame:.2f}s" if first_frame is not None else "  first frame     never")
    print(f"  first token     {first_token:.2f}s" if first_token is not None else "  first token     never")
    print(f"  last token      {last_token:.2f}s" if last_token is not None else "  last token      never")
    print(f"  final           {final_s:.2f}s" if final_s is not None else "  final           never")
    print(f"  streamed chars  {chars}")
    if first_token is not None and final_s is not None:
        print(f"  silence to answer {final_s - first_token:.2f}s of dead air after the first word")
    for start, end, ev in silent_windows:
        print(f"  DEAD AIR        {start:.1f}s -> {end:.1f}s ({end - start:.1f}s) waiting on '{ev}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
