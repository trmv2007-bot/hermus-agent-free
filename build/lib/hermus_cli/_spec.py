"""CLI command specs — the one table the parser and the dispatcher both read.

Before this, every command was a module that had to export ``register()`` and
``run()``; the parser and the dispatcher each re-derived the command list from
that convention, and a typo between the two showed up only at a user's terminal.
A :class:`Command` now carries its own name, help, argument wiring and handler,
so the parser, the dispatcher and the tests can only ever see one list.

Command modules live in ``hermus_cli/g_<group>.py`` (one per capability group,
mirroring the groups the control room console uses); this module holds the type
they declare and the adapter that keeps the historical per-command module
interface working for callers that still expect it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


@dataclass(frozen=True)
class Command:
    """One CLI command: what it is called, how it parses, what it runs."""

    name: str
    help: str
    configure: Callable[[argparse._SubParsersAction], None]
    run: Callable[[argparse.Namespace, CLIContext], None]


class CommandModule:
    """A ``Command`` wearing the old per-command module interface.

    ``build_parser`` and ``main`` read :class:`Command` objects directly. This
    adapter exists so ``COMMANDS[name]`` still answers ``.register(subparsers)``
    and ``.run(args, ctx)`` the way the previous one-module-per-command layout
    did — including for tests that replace ``run`` with a spy to prove dispatch.
    """

    def __init__(self, command: Command, group_module: str) -> None:
        self.command = command
        self.__name__ = f"{group_module}.{command.name}"
        self.__doc__ = command.help

    def register(self, subparsers: argparse._SubParsersAction) -> None:
        self.command.configure(subparsers)

    def run(self, args: argparse.Namespace, ctx: CLIContext) -> None:
        self.command.run(args, ctx)


def no_action(ctx: CLIContext, name: str) -> None:
    """What a bare ``hermus <command>`` does: show that command's help.

    Previously each command repeated this parser round-trip in its own final
    ``else`` branch; one implementation keeps the behaviour and removes the copy
    per command.
    """
    ctx.parser.parse_args([name, "--help"])


__all__ = ["Command", "CommandModule", "no_action"]
