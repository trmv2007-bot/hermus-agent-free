"""``hermus --help`` — three commands first, everything else under Developers.

The parser knows fifty-odd commands because the repository can do that much. A
person who types ``hermus --help`` needs three of them, so the first section is
exactly those three and the rest sits under an explicitly labelled developer
heading. ``docs/DEVELOPERS.md`` carries the long form of the same list, and
``tests/test_cli_help.py`` holds both sections still.
"""

from __future__ import annotations

import argparse
import textwrap

#: The only commands a person ever needs, in the order they meet them. The first
#: entry is ``hermus`` itself: the launcher installs when the runtime is missing,
#: and the CLI starts the gateway and opens the room.
START_HERE: tuple[tuple[str, str], ...] = (
    ("hermus", "install it, or start it and open the room"),
    ("hermus undo", "undo the last thing it changed"),
    ("hermus understand", "say what it can and cannot do on this machine"),
)

DEVELOPERS_HEADING = "Developers - the rest of this repository, for people changing the code (docs/DEVELOPERS.md)"

#: Column the descriptions start in, and the width everything wraps to.
NAME_COLUMN = 22
WIDTH = 100


def plain(text: str) -> str:
    """One console-safe line: the Windows console is cp1252 and help must not die there."""
    out = []
    for char in str(text).replace("\n", " "):
        out.append(char if ord(char) < 128 else "-")
    return "".join(out).strip()


def rows(pairs, indent: int = 2) -> str:
    """``name   description`` records, one per command, wrapped under the column."""
    lines = []
    for name, text in pairs:
        head = " " * indent + f"{name:<{NAME_COLUMN - indent}}"
        wrapped = textwrap.wrap(plain(text), width=WIDTH, initial_indent=head, subsequent_indent=" " * NAME_COLUMN)
        lines.append("\n".join(wrapped) if wrapped else head.rstrip())
    return "\n".join(lines)


def render(parser: argparse.ArgumentParser, specs) -> str:
    """The whole top-level help: usage, three commands, developers, options."""
    usage = parser.format_usage().strip()

    options = argparse.HelpFormatter(prog=parser.prog)
    options.start_section("Before the command")
    for action in parser._actions:
        if action.dest in ("help", "command"):
            continue
        options.add_argument(action)
    options.end_section()

    return (
        "\n\n".join(
            [
                usage,
                plain(parser.description or ""),
                "Start here:\n" + rows(START_HERE),
                f"{DEVELOPERS_HEADING}:\n" + rows((spec.name, spec.help) for spec in specs),
                options.format_help().strip(),
            ]
        )
        + "\n"
    )
