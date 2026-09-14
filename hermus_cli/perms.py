"""perms — Permission manager - ALLOW/ASK/DENY."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    perms_parser = subparsers.add_parser("perms", help="Permission manager - ALLOW/ASK/DENY")
    perms_sub = perms_parser.add_subparsers(dest="perms_action")
    perms_check = perms_sub.add_parser("check", help="Check a tool's permission decision")
    perms_check.add_argument("tool")
    perms_set = perms_sub.add_parser("set", help="Override a tool's policy")
    perms_set.add_argument("tool")
    perms_set.add_argument("decision", choices=["allow", "ask", "deny"])
    perms_set.add_argument("--agent", default=None)
    perms_approve = perms_sub.add_parser("approve", help="Create a scoped approval grant for yellow red-line actions")
    perms_approve.add_argument("title")
    perms_approve.add_argument("--tool", default="*")
    perms_approve.add_argument("--red-line", dest="red_lines", type=int, action="append", default=[])
    perms_approve.add_argument("--resource", dest="resources", action="append", default=[])
    perms_approve.add_argument("--purpose", default="")
    perms_approve.add_argument("--ttl-minutes", type=int, default=None)
    perms_approve.add_argument("--max-uses", type=int, default=None)
    perms_approve.add_argument("--notes", default="")
    perms_revoke = perms_sub.add_parser("revoke", help="Revoke an approval grant")
    perms_revoke.add_argument("id")
    perms_pending = perms_sub.add_parser("pending", help="List pending yellow-action approval prompts")
    perms_pending.add_argument("--all", action="store_true", help="Include resolved prompts")
    perms_resolve = perms_sub.add_parser("resolve", help="Resolve a pending approval prompt")
    perms_resolve.add_argument("id")
    perms_resolve.add_argument("decision", choices=["approve", "deny"])
    perms_resolve.add_argument("--resource", dest="resources", action="append", default=[])
    perms_resolve.add_argument("--purpose", default="")
    perms_resolve.add_argument("--ttl-minutes", type=int, default=None)
    perms_resolve.add_argument("--max-uses", type=int, default=None)
    perms_resolve.add_argument("--notes", default="")
    perms_resolve.add_argument(
        "--retry", action="store_true", help="Retry the approved action immediately through the ToolGateway"
    )
    perms_retry = perms_sub.add_parser("retry", help="Retry an approved pending action through the ToolGateway")
    perms_retry.add_argument("id")
    perms_bundles = perms_sub.add_parser("bundles", help="List pending approval bundles")
    perms_bundles.add_argument("--all", action="store_true", help="Include resolved bundles")
    perms_bundle_resolve = perms_sub.add_parser("resolve-bundle", help="Approve or deny an approval bundle")
    perms_bundle_resolve.add_argument("id")
    perms_bundle_resolve.add_argument("decision", choices=["approve", "deny"])
    perms_bundle_resolve.add_argument("--ttl-minutes", type=int, default=None)
    perms_bundle_resolve.add_argument("--max-uses", type=int, default=None)
    perms_bundle_resolve.add_argument("--notes", default="")
    perms_bundle_resolve.add_argument(
        "--resume", action="store_true", help="Resume the mission attached to the bundle after approval"
    )
    perms_sub.add_parser("approvals", help="List active approval grants")
    perms_sub.add_parser("list", help="Recent audit log")


def run(args, ctx: CLIContext) -> None:
    from core.permissions import permission_manager

    if args.perms_action == "check":
        r = permission_manager.check(args.tool)
        safety = r.get("safety") or {}
        print(
            f"{args.tool}: risk={r['risk']} decision={r['decision']} safety={safety.get('zone', 'green')} red_lines={safety.get('red_lines', [])}"
        )
        if r.get("approval"):
            print(f" approval={r['approval'].get('grant', {}).get('id')}")
    elif args.perms_action == "set":
        r = permission_manager.set_policy(args.tool, args.decision, agent=args.agent)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.tool} -> {args.decision}"
            + (f" (agent={args.agent})" if args.agent else "")
        )
    elif args.perms_action == "approve":
        r = permission_manager.approval_grant(
            args.title,
            tool=args.tool,
            red_lines=args.red_lines,
            resources=args.resources,
            purpose=args.purpose,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        grant = r.get("grant", {})
        print(f"{'✅' if r.get('success') else '❌'} approval {grant.get('id', '')} {grant.get('title', args.title)}")
    elif args.perms_action == "revoke":
        r = permission_manager.approval_revoke(args.id)
        print(f"{'✅' if r.get('success') else '❌'} revoked {args.id}")
    elif args.perms_action == "pending":
        for req in permission_manager.approval_pending(include_resolved=args.all):
            print(
                f" {req.get('id')} status={req.get('status')} tool={req.get('tool')} safety={req.get('safety', {}).get('zone')} red_lines={req.get('safety', {}).get('red_lines')} resources={req.get('suggested_resources')}"
            )
    elif args.perms_action == "resolve":
        r = permission_manager.approval_resolve(
            args.id,
            args.decision,
            resources=args.resources or None,
            purpose=args.purpose,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        retry = None
        if args.retry and r.get("success") and args.decision == "approve":
            retry = permission_manager.approval_retry(args.id)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.decision} {args.id}"
            + (f" grant={r.get('grant', {}).get('id')}" if r.get("grant") else "")
        )
        if retry is not None:
            print(f" retry={'✅' if retry.get('success') else '❌'}")
    elif args.perms_action == "retry":
        r = permission_manager.approval_retry(args.id)
        print(f"{'✅' if r.get('success') else '❌'} retry {args.id}")
        if r.get("error"):
            print(r.get("error"))
    elif args.perms_action == "bundles":
        for bundle in permission_manager.approval_bundles(include_resolved=args.all):
            print(
                f" {bundle.get('id')} status={bundle.get('status')} mission={bundle.get('mission_id')} requests={len(bundle.get('request_ids') or [])} title={bundle.get('title')}"
            )
    elif args.perms_action == "resolve-bundle":
        r = permission_manager.approval_bundle_resolve(
            args.id,
            args.decision,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        print(f"{'✅' if r.get('success') else '❌'} {args.decision} bundle {args.id}")
        bundle = r.get("bundle") or {}
        if args.resume and args.decision == "approve" and bundle.get("mission_id"):
            try:
                from core.mission import mission_engine

                resumed = mission_engine.resume_mission(bundle["mission_id"], extra_steps=8)
                print(f" resume={resumed.state} mission={resumed.mission_id}")
            except Exception as exc:
                print(f" resume=❌ {exc}")
    elif args.perms_action == "approvals":
        for grant in permission_manager.approvals_list():
            print(
                f" {grant.get('id')} tool={grant.get('tool')} red_lines={grant.get('red_lines')} resources={grant.get('resources')} uses={grant.get('uses')}/{grant.get('max_uses')}"
            )
    elif args.perms_action == "list":
        for e in permission_manager.recent():
            print(
                f" {e.get('ts', '')[:19]} {e.get('tool')} -> {e.get('decision')} (risk={e.get('risk')}, agent={e.get('agent')})"
            )
    else:
        ctx.parser.parse_args(["perms", "--help"])
