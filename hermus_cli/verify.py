"""verify — Domain Verification Subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    verify_parser = subparsers.add_parser("verify", help="Domain Verification Subsystem")
    verify_sub = verify_parser.add_subparsers(dest="verify_action")
    v_run = verify_sub.add_parser("run", help="Run domain verification")
    v_run.add_argument("--domain", default="auto", help="Domain name")
    v_run.add_argument("--path", default=None, help="Target path or file")
    v_run.add_argument("--task", default="", help="Original task description")
    v_run.add_argument("--output", default="", help="Execution output")
    verify_sub.add_parser("domains", help="List available domain verifiers")


def run(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.verifier_registry import verifier_registry

    if args.verify_action == "run":
        res = verifier_registry.verify(
            domain_or_auto=args.domain,
            context={"task": args.task, "target_path": args.path, "output": args.output},
        )
        print(json_lib.dumps(res.to_dict(), indent=2))
    elif args.verify_action == "domains":
        print("Registered Domain Verifiers:", verifier_registry.list_domains())
    else:
        ctx.parser.parse_args(["verify", "--help"])
