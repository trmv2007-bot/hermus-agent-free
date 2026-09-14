"""delegate — Fan work out to parallel sub-agents (JSON-RPC workers)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    deleg_parser = subparsers.add_parser("delegate", help="Fan work out to parallel sub-agents (JSON-RPC workers)")
    deleg_parser.add_argument("goal", nargs="+")
    deleg_parser.add_argument(
        "--task", dest="tasks", action="append", default=None, help="Explicit workstream (repeatable); omit to auto-plan"
    )
    deleg_parser.add_argument("--max-children", type=int, default=4)
    deleg_parser.add_argument("--aggregate", default="synthesize", choices=["synthesize", "concat", "vote", "best"])
    deleg_parser.add_argument("--model", default=None)
    deleg_parser.add_argument("--json", action="store_true", help="Print the full structured tree as JSON")


def run(args, ctx: CLIContext) -> None:
    from core.delegation import delegation

    goal = " ".join(args.goal)
    sink = (
        (lambda t, d: print(f"  · {t}: {str(d.get('task') or d.get('tool') or d.get('answer') or '')[:80]}"))
        if not args.json
        else None
    )
    if args.tasks:
        out = delegation.fanout(
            args.tasks,
            goal=goal,
            max_children=args.max_children,
            aggregate=args.aggregate,
            model=args.model or "",
            on_event=sink,
        )
    else:
        out = delegation.decompose_and_run(
            goal, max_children=args.max_children, aggregate=args.aggregate, model=args.model or "", on_event=sink
        )
    if args.json:
        print(__import__("json").dumps(out, indent=2, default=str))
    else:
        agg = out.get("aggregate") or {}
        print(
            f"\ndelegated '{str(out.get('goal'))[:60]}' → {out.get('succeeded')}/{out.get('children')} "
            f"children ok ({out.get('duration_ms')}ms, tree={out.get('tree_id')})"
        )
        for n in out.get("nodes") or []:
            mark = {"done": "✅", "failed": "❌", "cancelled": "⛔", "timeout": "⏱"}.get(n.get("status"), "…")
            print(
                f" {mark} {str(n.get('task'))[:56]:58s} {n.get('status')} "
                f"[{n.get('backend')}] {n.get('duration_ms')}ms tools={len(n.get('tool_calls') or [])}"
            )
        sections = agg.get("sections") or []
        if isinstance(sections, dict):  # older handlers returned a mapping
            sections = [{"child": k, "answer": v} for k, v in sections.items()]
        for sec in sections:
            conf = sec.get("confidence")
            print(f"\n### {sec.get('child', 'section')}" + (f"  (conf {conf})" if conf is not None else ""))
            print(str(sec.get("answer", ""))[:900])
        if not sections and agg.get("answer"):
            print(f"\n{str(agg['answer'])[:3000]}")
        if agg.get("disagreement"):
            print(f"\n(disagreement among children: {agg['disagreement']:.0%})")
    if out.get("errors"):
        print("errors:", "; ".join(str(e)[:120] for e in out["errors"]))
