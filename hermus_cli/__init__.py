"""Hermus CLI — grouped command modules behind one dispatch table.

The CLI used to be one module per command (43 files, each with its own docstring,
import shim and ``TYPE_CHECKING`` block). Commands now live in
``hermus_cli/g_<group>.py`` — one module per capability group, the same groups
the control room console uses (``core/console.py``) — and declare themselves as
:class:`hermus_cli._spec.Command` objects. ``build_parser()`` and ``main()`` read
that one list, so the parser can no longer disagree with the dispatcher.
"""

from __future__ import annotations

import argparse

from core.config import config
from core.log import setup_logging

from . import g_agents, g_memory, g_models, g_runtime, g_safety, g_workspace
from . import repl as repl_cmd
from ._common import CLIContext
from ._spec import Command, CommandModule

#: Ordered group modules. Order only affects ``--help`` listing.
_GROUPS = (g_runtime, g_agents, g_memory, g_models, g_safety, g_workspace)

SPECS: tuple[Command, ...] = tuple(command for group in _GROUPS for command in group.COMMANDS)

#: ``command name -> CommandModule``. Kept as the historical dict of module-like
#: objects so every existing caller (`main`, scripts, tests) keeps working.
COMMANDS: dict[str, CommandModule] = {
    spec.name: CommandModule(spec, group.__name__) for group in _GROUPS for spec in group.COMMANDS
}

# A duplicate name would make one command silently shadow another; a shared
# handler would make two commands behave identically by accident. Both are
# cheap to check at import time, so they fail here rather than at a terminal.
assert len(SPECS) == len(COMMANDS), "duplicate command name in the group modules"
assert len({spec.run for spec in SPECS}) == len(SPECS), "two commands share one handler"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermus Agent Free - The agent that grows with you, 100% free, no paywall", prog="hermus"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Default: start TUI chat
    parser.add_argument(
        "--model",
        default=config.model,
        help="Model: ollama/llama3.1:8b (free offline), groq/... (free tier), hf/... (free), mock/mock",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Mode: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys - persisted to user_model.json",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Persona profile (hermus profile list) - gives the agent an independent memory + system prompt",
    )

    for spec in SPECS:
        spec.configure(subparsers)
    return parser


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    ctx = CLIContext(parser=parser)
    if args.command is None:
        repl_cmd.run_default(args, ctx)
        return
    COMMANDS[args.command].run(args, ctx)


__all__ = ["COMMANDS", "SPECS", "build_parser", "main"]
