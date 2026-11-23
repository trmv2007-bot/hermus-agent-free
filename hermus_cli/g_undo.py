"""Undo commands — take back the last journaled action, or one by id.

``hermus undo``        the last action the tool path performed
``hermus undo --list`` the journal, newest first
``hermus undo <id>``   one specific entry
"""

from __future__ import annotations

from ._common import CLIContext
from ._spec import Command


def _configure_undo(subparsers) -> None:
    parser = subparsers.add_parser("undo", help="Undo the last journaled action (or one by id)")
    parser.add_argument("id", nargs="?", default=None, help="undo entry id (omit for the last action)")
    parser.add_argument("--list", dest="list_entries", action="store_true", help="List journaled actions newest first")
    parser.add_argument("--limit", type=int, default=20, help="Entries to list")
    parser.add_argument("--force", action="store_true", help="Restore even when the file changed after the action")


def _run_undo(args, ctx: CLIContext) -> None:
    from core.undo import get_undo_journal

    journal = get_undo_journal()
    if args.list_entries:
        entries = journal.list(int(args.limit))
        if not entries:
            print("undo journal is empty")
            return
        for entry in entries:
            mark = "undoable" if entry["reversible"] and entry["state"] == "applied" else entry["state"]
            print(f" {entry['id']}  {entry['created_at'][:19]}  {mark:14}  {entry['description']}")
            if not entry["reversible"] and entry["reason"]:
                print(f"    not reversible: {entry['reason']}")
        return
    result = journal.undo(args.id, force=bool(args.force)) if args.id else journal.undo_last(force=bool(args.force))
    if not result.get("success"):
        print(f"could not undo: {result.get('error')}")
        return
    print(f"undone {result.get('id')}: {result.get('description', '')}")
    for step in result.get("steps", []):
        where = step.get("path") or step.get("src") or step.get("keys") or ""
        outcome = "ok" if step.get("ok") else f"FAILED: {step.get('error')}"
        print(f"  {step.get('op')} {where} {outcome}")


COMMANDS: tuple[Command, ...] = (
    Command(
        name="undo",
        help="Undo the last journaled action, list the journal, or undo one id",
        configure=_configure_undo,
        run=_run_undo,
    ),
)

__all__ = ["COMMANDS"]
