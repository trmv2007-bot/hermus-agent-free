"""powers — Capability ledger - current, missing and discovered powers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    powers_parser = subparsers.add_parser("powers", help="Capability ledger - current, missing and discovered powers")
    powers_sub = powers_parser.add_subparsers(dest="powers_action")
    powers_sub.add_parser("list", help="List discovered possible powers")
    powers_add = powers_sub.add_parser("add", help="Add a discovered possible power to CAPABILITY_LEDGER.md")
    powers_add.add_argument("power")
    powers_add.add_argument("--use", default="")
    powers_add.add_argument("--risk", default="")
    powers_add.add_argument("--approval", default="")
    powers_add.add_argument("--status", default="not_granted")
    powers_propose = powers_sub.add_parser("propose", help="Generate a safe setup proposal for a missing/not-granted power")
    powers_propose.add_argument("power")
    powers_propose.add_argument("--write", action="store_true", help="Write docs/capability_proposals/<power>.md")
    powers_sub.add_parser("registry", help="List capability readiness/activation registry")
    powers_register = powers_sub.add_parser("register", help="Register/update capability readiness")
    powers_register.add_argument("power")
    powers_register.add_argument("--category", default="generic")
    powers_register.add_argument("--status", default="missing")
    powers_register.add_argument("--notes", default="")
    powers_setup = powers_sub.add_parser("setup", help="Generate setup proposal and planning command for a capability")
    powers_setup.add_argument("power")
    powers_setup.add_argument("--no-write", action="store_true")
    powers_activate_req = powers_sub.add_parser("request-activation", help="Create pending activation approval request")
    powers_activate_req.add_argument("power")
    powers_activate_req.add_argument("--reason", default="")
    powers_activate = powers_sub.add_parser("activate", help="Activate only after approved activation request")
    powers_activate.add_argument("power")
    powers_activate.add_argument("--approval-id", required=True)


def run(args, ctx: CLIContext) -> None:
    from core.capability_ledger import CapabilityEntry, get_capability_ledger

    ledger = get_capability_ledger()
    if args.powers_action == "list":
        rows = ledger.list_discovered()
        if not rows:
            print("No discovered possible powers recorded yet.")
        for row in rows:
            print(f" - {row.get('power')} | status={row.get('status')} | approval={row.get('needed_approval_setup')}")
    elif args.powers_action == "add":
        r = ledger.add_discovered(
            CapabilityEntry.create(
                power=args.power,
                use=args.use,
                risk=args.risk,
                needed_approval_setup=args.approval,
                status=args.status,
                source="cli",
            )
        )
        print(f"{'✅' if r.get('success') else '❌'} capability {args.power}" + (" (already listed)" if r.get("deduped") else ""))
    elif args.powers_action == "propose":
        r = ledger.propose(args.power, write=args.write)
        if args.write and r.get("path"):
            print(f"✅ proposal written: {r['path']}")
        else:
            print(r.get("markdown", ""))
    elif args.powers_action in {"registry", "register", "setup", "request-activation", "activate"}:
        from core.capability_registry import get_capability_registry

        registry = get_capability_registry()
        if args.powers_action == "registry":
            for rec in registry.list():
                print(
                    f" - {rec.get('name')} | status={rec.get('status')} | category={rec.get('category')} | activation={rec.get('activation_request_id')}"
                )
        elif args.powers_action == "register":
            r = registry.register(args.power, category=args.category, status=args.status, source="cli", notes=args.notes)
            print(f"{'✅' if r.get('success') else '❌'} {args.power} -> {r.get('record', {}).get('status')}")
        elif args.powers_action == "setup":
            r = registry.setup_plan(args.power, write_proposal=not args.no_write)
            rec = r.get("record", {})
            print(
                f"{'✅' if r.get('success') else '❌'} setup {args.power}: status={rec.get('status')} proposal={rec.get('proposal_path')}"
            )
            if rec.get("planning_command"):
                print(f" planning: {rec.get('planning_command')}")
        elif args.powers_action == "request-activation":
            r = registry.request_activation(args.power, reason=args.reason)
            print(f"{'✅' if r.get('success') else '❌'} activation request {r.get('request', {}).get('id', '')}")
        elif args.powers_action == "activate":
            r = registry.activate(args.power, approval_id=args.approval_id)
            print(
                f"{'✅' if r.get('success') else '❌'} activate {args.power}: {r.get('record', {}).get('status') or r.get('error')}"
            )
    else:
        ctx.parser.parse_args(["powers", "--help"])
