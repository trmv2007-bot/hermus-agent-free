"""Isolated harness task worker — runs ONE agent task in a subprocess.

``python -m core.harness.worker --task task.json --workspace DIR --model mock/mock``

Why a subprocess: the agent's file/shell tools resolve against the process cwd,
so each task gets its own process with its own cwd (the task workspace). That
makes tasks truly isolated AND safe to run in parallel (``--workers N``),
which the in-process runner could not do (a global os.chdir would race).

Protocol: prints one line ``HARNESS_RESULT {json}`` on stdout; exit 0 on a
completed run (even if the agent errored — the error is inside the JSON),
exit 3 on a fatal worker crash. Token usage is estimated from the trajectory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _estimate_tokens(agent, response: str) -> int:
    try:
        from core.token_counter import token_counter

        msgs = [dict(m) for m in (getattr(agent, "trajectory", []) or []) if isinstance(m, dict)]
        msgs.append({"role": "assistant", "content": response})
        return int(token_counter.count_messages(msgs))
    except Exception:
        return max(1, len(response) // 4)


def main() -> int:
    ap = argparse.ArgumentParser(prog="core.harness.worker")
    ap.add_argument("--task", required=True, help="path to task.json")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--model", default="mock/mock")
    ap.add_argument("--max-steps", type=int, default=8)
    args = ap.parse_args()

    out: dict = {"response": "", "steps": 0, "tool_calls": [], "tool_results_count": 0, "tokens_est": 0, "error": ""}
    try:
        from core.harness.tasks import Task

        task = Task.from_dict(json.loads(Path(args.task).read_text(encoding="utf-8")))
        ws = Path(args.workspace)
        ws.mkdir(parents=True, exist_ok=True)
        os.chdir(ws)  # safe here: this process exists only for this task

        from core.agent import HermusAgent

        agent = HermusAgent(model=args.model, mode="agent", max_steps=args.max_steps)
        chat = agent.chat(task.prompt)
        out["response"] = str(chat.get("response") or chat.get("final_answer") or "")
        out["steps"] = int(chat.get("steps") or 0)
        trs = list(chat.get("tool_results") or [])
        out["tool_calls"] = [str(t.get("tool") or "") for t in trs] or [str(t) for t in chat.get("tool_calls") or []]
        out["tool_results_count"] = len(trs)
        if chat.get("error"):
            out["error"] = str(chat["error"])
        out["tokens_est"] = _estimate_tokens(agent, out["response"])
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        print("HARNESS_FATAL " + json.dumps(out, default=str))
        return 3
    print("HARNESS_RESULT " + json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
