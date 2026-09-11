"""emergency — Global Red Line 1 emergency stop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    emergency_parser = subparsers.add_parser("emergency", help="Global Red Line 1 emergency stop")
    emergency_sub = emergency_parser.add_subparsers(dest="emergency_action")
    emergency_stop_p = emergency_sub.add_parser("stop", help="Activate global emergency stop")
    emergency_stop_p.add_argument("reason", nargs="*", default=[])
    emergency_resume_p = emergency_sub.add_parser("resume", help="Clear global emergency stop")
    emergency_resume_p.add_argument("reason", nargs="*", default=[])
    emergency_sub.add_parser("status", help="Show global emergency stop state")


def run(args, ctx: CLIContext) -> None:
    from core.emergency_stop import get_emergency_stop

    brake = get_emergency_stop()
    if args.emergency_action == "stop":
        r = brake.activate(" ".join(args.reason) or "manual emergency stop", set_by="cli")
        print(f"✅ emergency stop active: {r['state'].get('reason')}")
    elif args.emergency_action == "resume":
        r = brake.clear(" ".join(args.reason) or "manual resume", set_by="cli")
        print(f"✅ emergency stop cleared: {r['state'].get('reason')}")
    elif args.emergency_action == "status":
        s = brake.state().to_dict()
        print(f"emergency_stop={s.get('active')} reason={s.get('reason')} updated={s.get('updated_at')}")
    else:
        ctx.parser.parse_args(["emergency", "--help"])
