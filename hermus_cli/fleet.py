"""fleet — Model fleet - distribute tasks across multiple AI models + API keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    fleet_parser = subparsers.add_parser(
        "fleet",
        help="Model fleet - distribute tasks across multiple AI models + API keys",
    )
    fleet_sub = fleet_parser.add_subparsers(dest="fleet_action")
    fleet_workers = fleet_sub.add_parser("workers", help="List available model/key workers")
    fleet_workers.add_argument("--providers", help="Comma-separated providers")
    fleet_workers.add_argument("--models", help="Comma-separated provider/model")
    fleet_run = fleet_sub.add_parser("run", help="Distribute a goal (auto|fanout|map|race)")
    fleet_run.add_argument("goal", help="Goal / prompt")
    fleet_run.add_argument("--strategy", default="auto", choices=["auto", "fanout", "map", "race"])
    fleet_run.add_argument("--models", help="Comma-separated provider/model")
    fleet_run.add_argument("--providers", help="Comma-separated providers")
    fleet_run.add_argument("--workers", type=int, default=4)
    fleet_fanout = fleet_sub.add_parser("fanout", help="Same prompt → many models → consensus")
    fleet_fanout.add_argument("prompt")
    fleet_fanout.add_argument("--models", help="Comma-separated")
    fleet_fanout.add_argument("--providers", help="Comma-separated")
    fleet_fanout.add_argument("--workers", type=int, default=4)
    fleet_map = fleet_sub.add_parser("map", help="Split goal into subtasks across models")
    fleet_map.add_argument("goal")
    fleet_map.add_argument("--models", help="Comma-separated")
    fleet_map.add_argument("--providers", help="Comma-separated")
    fleet_map.add_argument("--workers", type=int, default=4)


def run(args, ctx: CLIContext) -> None:

    from core.model_fleet import model_fleet

    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()] or None

    if args.fleet_action == "workers":
        w = model_fleet.list_workers(models=_split(args.models), providers=_split(args.providers))
        print(f"\nFleet workers ({w.get('count', 0)}):")
        for worker in w.get("workers") or []:
            print(
                f" - {worker.get('name')}: {worker.get('provider')}/{worker.get('model')}"
                f" | key={'yes' if worker.get('has_key') else 'no'}"
                f" | base_url={worker.get('base_url') or '(preset)'}"
            )
        if w.get("providers_configured"):
            print(f"Providers configured: {', '.join(w['providers_configured'])}")
    elif args.fleet_action == "run":
        result = model_fleet.auto_distribute(
            args.goal,
            strategy=args.strategy,
            models=_split(args.models),
            providers=_split(args.providers),
            max_workers=args.workers,
        )
        print(
            f"\nFleet run: mode={result.get('mode')} strategy={result.get('strategy') or args.strategy}"
            f" | success={result.get('success')} workers_used={result.get('workers_used') or len(result.get('results') or [])}"
        )
        if result.get("subtasks"):
            print("Subtasks:")
            for i, s in enumerate(result["subtasks"], 1):
                print(f"   {i}. {s}")
        if result.get("consensus"):
            print("\n=== CONSENSUS ===\n", result["consensus"][:3000])
        elif result.get("merged"):
            print("\n=== MERGED ===\n", result["merged"][:3000])
        elif result.get("winner"):
            print("\n=== WINNER ===\n", (result["winner"].get("response") or "")[:3000])
        else:
            for r in (result.get("results") or [])[:5]:
                print(f"\n--- {r.get('model')} success={r.get('success')} ---")
                print((r.get("response") or r.get("error") or "")[:800])
        if result.get("error"):
            print(f"\n⚠️ {result['error']}")
    elif args.fleet_action == "fanout":
        result = model_fleet.fanout(
            args.prompt, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers
        )
        print("Workers:", result.get("workers_used"), "success:", result.get("success"))
        if result.get("consensus"):
            print("\n=== CONSENSUS ===\n", result["consensus"][:4000])
    elif args.fleet_action == "map":
        result = model_fleet.map_goal(
            args.goal, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers
        )
        print("Subtasks:")
        for i, s in enumerate(result.get("subtasks") or [], 1):
            print(f"   {i}. {s}")
        if result.get("merged"):
            print("\n=== MERGED ===\n", result["merged"][:4000])
    else:
        ctx.parser.parse_args(["fleet", "--help"])
