"""counsel — Counsel System - council of AIs plans together, then upgrades itself."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import config

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    counsel_parser = subparsers.add_parser(
        "counsel",
        help="Counsel System - council of AIs plans together, then upgrades itself",
    )
    counsel_sub = counsel_parser.add_subparsers(dest="counsel_action")
    counsel_run = counsel_sub.add_parser("run", help="Convene the council for a task")
    counsel_run.add_argument("goal", help="The task/goal for the council")
    counsel_run.add_argument("--rounds", type=int, default=None, help="Override deliberation rounds")
    counsel_run.add_argument("--difficulty", type=int, default=None, choices=[1, 2, 3, 4, 5], help="Override difficulty 1-5")
    counsel_run.add_argument("--members", type=int, default=None, help="Override max members")
    counsel_run.add_argument("--no-execute", action="store_true", help="Plan only, skip tool execution")
    counsel_run.add_argument("--model", default=None, help="Base model for members (auto-diversified)")
    counsel_sub.add_parser("status", help="Council status: constitution version, roster, upgrades")
    counsel_amend = counsel_sub.add_parser("amend", help="Self-upgrade amendments (Meta-Counsel)")
    counsel_amend_sub = counsel_amend.add_subparsers(dest="counsel_amend_action")
    counsel_amend_sub.add_parser("list", help="List pending amendments + upgrade history")
    counsel_amend_diff = counsel_amend_sub.add_parser("diff", help="View unified diff of a pending amendment")
    counsel_amend_diff.add_argument("amendment_id")
    counsel_amend_approve = counsel_amend_sub.add_parser("approve", help="Approve a pending high-risk amendment")
    counsel_amend_approve.add_argument("amendment_id")
    counsel_amend_reject = counsel_amend_sub.add_parser("reject", help="Reject a pending amendment")
    counsel_amend_reject.add_argument("amendment_id")
    counsel_amend_rollback = counsel_amend_sub.add_parser("rollback", help="Roll back constitution to a previous version")
    counsel_amend_rollback.add_argument("version", type=int)
    counsel_review = counsel_sub.add_parser("review", help="Run Meta-Counsel review on the last council session")
    counsel_review.add_argument("--session-id", default=None, help="Specific session id (default: latest)")


def run(args, ctx: CLIContext) -> None:
    import json as _json

    from core.counsel.constitution import constitution
    from core.counsel.council import CouncilSession
    from core.counsel.meta import meta_counsel

    if args.counsel_action == "run":
        result = CouncilSession(
            args.goal,
            model=args.model,
            difficulty=args.difficulty,
            max_members=args.members,
            max_rounds=args.rounds,
            execute=not args.no_execute,
        ).run()
        print(f"\n{'=' * 60}")
        print(f"⚖️ COUNSEL SESSION: {result['session_id']}")
        print(f"   Difficulty: {result['difficulty']} | Members: {', '.join(m['name'] for m in result['members'])}")
        votes = result.get("votes")
        if votes:
            print("   Votes:")
            for k, v in votes.items():
                print(f"     - {k}: {str(v)[:100]}")
        print(f"{'=' * 60}")
        if result.get("plan") and result["plan"].get("steps"):
            print("\n📋 VOTED PLAN:")
            for i, s in enumerate(result["plan"]["steps"], 1):
                print(f"   {i}. [{s.get('status', 'pending')}] {s.get('goal', '')}")
        print(f"\n✅ FINAL ANSWER:\n{result['final_answer']}")
        if result.get("errors"):
            print(f"\n⚠️  Council errors: {result['errors']}")
    elif args.counsel_action == "status":
        st = meta_counsel.status()
        print(f"Council status — constitution v{st['constitution']['version']} ({st['constitution']['name']})")
        print(f"  Members: {', '.join(st['constitution']['members'])}")
        rules = st.get("constitution", {}).get("rules", {})
        if rules:
            print("  Rules:")
            for k, v in rules.items():
                print(f"    - {k}: {str(v)[:120]}")
        budget = st.get("constitution", {}).get("budget", {})
        if budget:
            print(f"  Budget: max_members={budget.get('max_members')}, max_rounds={budget.get('max_rounds')}")
        print(f"  Pending amendments: {st['pending_amendments']}")
        print(f"  Meta reviews logged: {st['reviews_logged']}")
        print(f"  Upgrade events: {st['constitution']['upgrade_events']}")
        for ev in constitution.upgrade_log()[-5:]:
            print(
                f"    - {ev.get('event')} v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} {ev.get('timestamp', '')[:19]}"
            )
    elif args.counsel_action == "amend":
        if args.counsel_amend_action == "list":
            pending = constitution.pending_amendments()
            print(f"Pending amendments ({len(pending)}):")
            for p in pending:
                print(f"  - [{p['id']}] target={p.get('target')} risk=high | {p.get('change', '')[:120]}")
                print(f"      reason: {p.get('reason', '')[:180]}")
            print("\nUpgrade history (last 10):")
            for ev in constitution.upgrade_log()[-10:]:
                print(
                    f"  - {ev.get('event')} | v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} | {ev.get('reason', '')[:100]} | {ev.get('timestamp', '')[:19]}"
                )
            print("\nUse: hermus counsel amend approve <id> | reject <id> | diff <id> | rollback <version>")
        elif args.counsel_amend_action == "diff":
            res = constitution.diff(args.amendment_id)
            if res.get("success"):
                print(f"=== Unified Diff for Amendment {args.amendment_id} ===")
                print(res.get("diff") or "(no textual diff)")
            else:
                print(f"Diff error: {res.get('error')}")
        elif args.counsel_amend_action == "approve":
            res = constitution.approve(args.amendment_id)
            print(f"Approve result: {res}")
        elif args.counsel_amend_action == "reject":
            res = constitution.reject(args.amendment_id)
            print(f"Reject result: {res}")
        elif args.counsel_amend_action == "rollback":
            res = constitution.rollback(args.version)
            print(f"Rollback result: {res}")
        else:
            ctx.parser.parse_args(["counsel", "amend", "--help"])
    elif args.counsel_action == "review":
        session_id = args.session_id
        if not session_id:
            import glob

            files = sorted(glob.glob(str(config.resolve_path("data/counsel/sessions/*.json"))))
            if not files:
                print('No council sessions yet — run `hermus counsel run "task"` first')
                sys.exit(1)
            session_id = Path(files[-1]).stem

        summary = _json.loads(Path(config.resolve_path(f"data/counsel/sessions/{session_id}.json")).read_text())
        res = meta_counsel.review_session(summary)
        print(f"Meta-Counsel review of {session_id}: proposed {res['proposed']} amendment(s)")
        for r in res.get("results", []):
            print(f"  - {r}")
    else:
        ctx.parser.parse_args(["counsel", "--help"])
