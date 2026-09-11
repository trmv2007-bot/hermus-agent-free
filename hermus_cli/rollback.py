"""rollback — Transactional Rollback & Checkpoint Manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    rb_parser = subparsers.add_parser("rollback", help="Transactional Rollback & Checkpoint Manager")
    rb_sub = rb_parser.add_subparsers(dest="rollback_action")
    rb_chk = rb_sub.add_parser("checkpoint", help="Create a workspace snapshot checkpoint")
    rb_chk.add_argument("label", help="Checkpoint description/label")
    rb_res = rb_sub.add_parser("restore", help="Restore workspace to checkpoint state")
    rb_res.add_argument("checkpoint_id", help="Checkpoint ID")
    rb_diff = rb_sub.add_parser("diff", help="Compare workspace state against checkpoint")
    rb_diff.add_argument("checkpoint_id")
    rb_sub.add_parser("list", help="List saved checkpoints")


def run(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.rollback import rollback_manager

    if args.rollback_action == "checkpoint":
        cp = rollback_manager.checkpoint(label=args.label)
        print(f"Checkpoint created: {cp.id} ('{cp.label}')")
    elif args.rollback_action == "restore":
        res = rollback_manager.restore(args.checkpoint_id)
        print(json_lib.dumps(res, indent=2))
    elif args.rollback_action == "diff":
        res = rollback_manager.diff(args.checkpoint_id)
        print(json_lib.dumps(res, indent=2))
    elif args.rollback_action == "list":
        cps = rollback_manager.list_checkpoints()
        print(f"Checkpoints ({len(cps)}):")
        for c in cps:
            print(f" - {c.id} [{c.timestamp}] {c.label} ({len(c.files)} files)")
    else:
        ctx.parser.parse_args(["rollback", "--help"])
