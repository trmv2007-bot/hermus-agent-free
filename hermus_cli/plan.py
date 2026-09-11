"""plan — Plans - DeepThink plan persistence & resume."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    plan_parser = subparsers.add_parser("plan", help="Plans - DeepThink plan persistence & resume")
    plan_sub = plan_parser.add_subparsers(dest="plan_action")
    plan_sub.add_parser("list", help="List saved plans")
    plan_show = plan_sub.add_parser("show", help="Show a plan")
    plan_show.add_argument("session_id")
    plan_resume = plan_sub.add_parser("resume", help="Resume a plan (runs remaining steps)")
    plan_resume.add_argument("session_id")
    plan_resume.add_argument("--model", default=None)


def run(args, ctx: CLIContext) -> None:

    from core.reasoning.scaffold import list_plans, resume_plan, show_plan

    if args.plan_action == "list":
        plans = list_plans()
        if not plans:
            print("No plans saved yet — ask a multi-step task (DeepThink on) or run `hermus counsel run`.")
        for p in plans:
            print(f"  {p['session_id'][:38]:38s} steps={p['steps']} done={p['done']} status={p['status']} | {p['goal']}")
    elif args.plan_action == "show":
        plan = show_plan(args.session_id)
        if not plan:
            print(f"No plan found for '{args.session_id}'. Try `hermus plan list`")
        else:
            print(plan.to_prompt())
    elif args.plan_action == "resume":
        res = resume_plan(args.session_id, model=args.model)
        print(f"Resume result: success={res.get('success')} remaining_before={res.get('remaining_before')}")
        print(f"Response: {str(res.get('response'))[:400]}")
        if res.get("plan") and res["plan"].get("steps"):
            print("Plan state:")
            for i, s in enumerate(res["plan"]["steps"], 1):
                print(f"   {i}. [{s.get('status', '?')}] {s.get('goal', '')[:60]}")
    else:
        ctx.parser.parse_args(["plan", "--help"])
