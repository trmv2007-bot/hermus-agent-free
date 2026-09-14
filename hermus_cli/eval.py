"""eval — Eval harness - measure thinking strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    eval_parser = subparsers.add_parser("eval", help="Eval harness - measure thinking strategies")
    eval_sub = eval_parser.add_subparsers(dest="eval_action")
    eval_run = eval_sub.add_parser("run", help="Run benchmark tasks under a strategy")
    eval_run.add_argument("--strategy", default="auto", help="auto|none|reflexion|self_consistency|verify")
    eval_run.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    eval_run.add_argument("--category", default=None, help="Only one category: fact|research|code|extraction|math")
    eval_run.add_argument("--model", default=None, help="Model for the solver")
    eval_sub.add_parser("list", help="List benchmark tasks")
    eval_compare = eval_sub.add_parser("compare", help="A/B two strategies on the same tasks")
    eval_compare.add_argument("--a", required=True, help="Strategy A: auto|none|reflexion|self_consistency|verify")
    eval_compare.add_argument("--b", required=True, help="Strategy B")
    eval_compare.add_argument("--limit", type=int, default=None)
    eval_compare.add_argument("--model", default=None)
    eval_history = eval_sub.add_parser("history", help="Show eval run history")
    eval_history.add_argument("--limit", type=int, default=10)


def run(args, ctx: CLIContext) -> None:

    from core.reasoning.eval import eval_harness

    if args.eval_action == "run":
        tasks = eval_harness.load_tasks()
        if args.category:
            tasks = [t for t in tasks if t.get("category") == args.category]
            print(f"Eval tasks filtered to category '{args.category}': {len(tasks)}")
        res = eval_harness.run(strategy=args.strategy, tasks=tasks, limit=args.limit, model=args.model)
        print(f"\n=== Eval run: strategy={res.get('strategy')} ===")
        print(
            f"  {res.get('success')}/{res.get('runs')} passed | success_rate={res.get('success_rate')} | "
            f"avg_steps={res.get('avg_steps')} | avg_tool_failures={res.get('avg_tool_failures')}"
        )
        for cat, c in (res.get("by_category") or {}).items():
            print(f"  {cat:12s} {c['success']}/{c['runs']}")
        for r in res.get("results", []):
            print(
                f"  [{'✅' if r['success'] else '❌'}] {r['id']:14s} ({r['strategy']:16s}) steps={r['steps']} fails={r['tool_failures']}"
            )
    elif args.eval_action == "list":
        for t in eval_harness.load_tasks():
            print(f"  {t['id']:16s} {t['category']:10s} {t['prompt'][:70]}")
    elif args.eval_action == "compare":
        res = eval_harness.compare(args.a, args.b, limit=args.limit, model=args.model)
        print(f"Compare {args.a} vs {args.b}: WINNER = {res['winner']}")
        print(f"  {args.a}: {res['a']}")
        print(f"  {args.b}: {res['b']}")
        print("Tip: rerun with different strategies or --limit to get stable results on real models.")
    elif args.eval_action == "history":
        h = eval_harness.history(limit=args.limit)
        if not h:
            print("No eval runs yet — `hermus eval run`")
        for run in h:
            print(
                f"  {run.get('timestamp', '')[:19]} | {str(run.get('strategy'))[:16]:16s} | "
                f"rate={run.get('success_rate')} | runs={run.get('runs')} | tag={run.get('tag', '')}"
            )
    else:
        ctx.parser.parse_args(["eval", "--help"])
