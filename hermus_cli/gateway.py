"""gateway — Gateway - single process for Telegram/Discord/CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import config

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    gateway_parser = subparsers.add_parser("gateway", help="Gateway - single process for Telegram/Discord/CLI")
    gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
    gateway_setup = gateway_sub.add_parser("setup", help="Setup gateway for platform")
    gateway_setup.add_argument("--platform", default="telegram", help="telegram, discord, slack, etc.")
    gateway_start = gateway_sub.add_parser("start", help="Start gateway")
    gateway_start.add_argument("--port", type=int, default=config.gateway_port)


def run(args, ctx: CLIContext) -> None:
    if args.gateway_action == "setup":
        from gateway.gateway import setup

        setup(args.platform)
    elif args.gateway_action == "start":
        from gateway.gateway import start

        start(args.port)
    else:
        ctx.parser.parse_args(["gateway", "--help"])
