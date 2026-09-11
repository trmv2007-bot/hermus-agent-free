"""router — Model Router 2.0 - per-step model selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    router_parser = subparsers.add_parser("router", help="Model Router 2.0 - per-step model selection")
    router_sub = router_parser.add_subparsers(dest="router_action")
    router_choose = router_sub.add_parser("choose", help="Choose the best model for a step")
    router_choose.add_argument("text")


def run(args, ctx: CLIContext) -> None:
    from core.router2 import router2

    sel = router2.select(args.text)
    print(f"Task type : {sel['task_type']} (difficulty {sel['difficulty']}, ~{sel['context_tokens']} tokens)")
    print(f"Model     : {sel['model']}")
    print(f"Reason    : {sel['reason']}")
    if sel.get("alternatives"):
        print(f"Alt       : {', '.join(sel['alternatives'])}")
