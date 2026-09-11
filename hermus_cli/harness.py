"""harness — Agent harness - sessions, swarm bus, file-shift, compaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    harness_parser = subparsers.add_parser("harness", help="Agent harness - sessions, swarm bus, file-shift, compaction")
    harness_sub = harness_parser.add_subparsers(dest="harness_action")
    harness_sub.add_parser("sessions", help="List server-owned sessions")
    h_attach = harness_sub.add_parser("attach", help="Attach a surface to a session")
    h_attach.add_argument("session_id")
    h_detach = harness_sub.add_parser("detach", help="Detach a surface from a session")
    h_detach.add_argument("session_id")
    h_msg = harness_sub.add_parser("send", help="Send a swarm message")
    h_msg.add_argument("body")
    h_msg.add_argument("--from", dest="sender", default="cli")
    h_msg.add_argument("--to", default="")
    h_msg.add_argument("--channel", default="")
    h_msg.add_argument("--kind", default="broadcast", choices=["dm", "broadcast", "channel"])
    h_inbox = harness_sub.add_parser("inbox", help="Read swarm inbox")
    h_inbox.add_argument("session_id")
    h_spawn = harness_sub.add_parser("spawn", help="Register swarm workers (no LLM)")
    h_spawn.add_argument("task")
    h_spawn.add_argument("--parent", default="cli")
    h_spawn.add_argument("--count", type=int, default=2)
    h_recall = harness_sub.add_parser("recall", help="Cascade memory recall")
    h_recall.add_argument("query")


def run(args, ctx: CLIContext) -> None:
    import json as _json

    from core.harness import bus, sessions
    from core.harness.memory_graph import cascade_recall
    from core.harness.swarm import spawn as swarm_spawn

    if args.harness_action == "sessions":
        items = sessions.list_sessions()
        print(f"Sessions ({len(items)}):")
        for s in items:
            print(f" - {s.get('id')} | {s.get('status')} | role={s.get('role')} | {str(s.get('task') or '')[:70]}")
    elif args.harness_action == "attach":
        print(_json.dumps(sessions.attach(args.session_id), indent=2))
    elif args.harness_action == "detach":
        print(_json.dumps(sessions.detach(args.session_id), indent=2))
    elif args.harness_action == "send":
        kind = args.kind if not args.channel else "channel"
        print(
            _json.dumps(bus.send(args.body, args.sender, to=args.to or None, channel=args.channel or None, kind=kind), indent=2)
        )
    elif args.harness_action == "inbox":
        print(_json.dumps(bus.inbox(args.session_id), indent=2, default=str))
    elif args.harness_action == "spawn":
        print(_json.dumps(swarm_spawn(args.task, args.parent, count=args.count), indent=2, default=str))
    elif args.harness_action == "recall":
        print(_json.dumps(cascade_recall(args.query), indent=2, default=str))
    else:
        ctx.parser.parse_args(["harness", "--help"])
