"""watchdog — Self-healing watchdog - classify/repair errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    watchdog_parser = subparsers.add_parser("watchdog", help="Self-healing watchdog - classify/repair errors")
    watchdog_parser.add_argument("error", nargs="?", default="", help="Error text to classify/repair")


def run(args, ctx: CLIContext) -> None:
    from core.watchdog import watchdog as wd

    err = args.error or "JSONDecodeError: expecting value"
    r = wd.handle(err)
    print(f"Known={r['known']} action={r['action']} ok={r.get('ok')} fix={r.get('fix', '')}")
