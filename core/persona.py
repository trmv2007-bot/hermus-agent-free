"""Who HERMUS is, grounded in what it can actually do.

The rule this file exists to enforce
------------------------------------
A persona that lists capabilities it does not have is worse than no persona.
A 4B model will happily claim it can deploy to a cluster, and the user has no
way to tell that from a real answer except by trying. So every capability
claim below is counted from the live tool registry at the moment the prompt is
built, never written by hand.

That means the persona is also *honest by construction*: uninstall a tool and
the sentence describing it disappears on the next turn, with no code change.

The other half is restraint. A short identity plus real numbers reads as
competent; a paragraph of adjectives reads as a mascot. The prompt stays small
on purpose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_NAME = "HERMUS"
DEFAULT_MASTER = "Rishi"

# Grouped by what they let HERMUS *do*, not by where the code lives. The user
# asks "can you look at my screen?", not "is computer.py loaded?".
# Order is deliberate: the first groups are the ones that are always true.
CAPABILITY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("see", ("screen", "capture", "screenshot", "computer", "window", "vision", "image")),
    ("hear and speak", ("voice", "speech", "speak", "audio", "mic", "transcri", "say")),
    ("read and write files", ("file", "read", "write", "edit", "path", "directory", "fs_")),
    ("run commands", ("shell", "command", "exec", "bash", "terminal", "process", "run_")),
    ("reach the network", ("http", "web", "fetch", "url", "browser", "request", "api_")),
    ("remember", ("memory", "remember", "recall", "note", "knowledge", "forget")),
    ("control other devices", ("android", "adb", "device", "remote")),
    ("delegate and schedule", ("agent", "spawn", "delegate", "task", "cron", "schedul", "mission")),
)

# Stated as what it is, because a small model asked to describe its own limits
# will invent dramatic ones. These are the real ones on this build.
LIMITS = (
    "You run on one Windows PC, so you only see what runs on it.",
    "Your speech-to-text needs the room to be audible; in noise it will mishear.",
    "Big or vague work is handed to a larger model before you answer it.",
    "You cannot remember anything the user has not given you to store.",
)


@dataclass(frozen=True)
class Persona:
    name: str
    master: str
    tool_count: int
    groups: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)

    def describe(self) -> str:
        """Build the system prompt.

        Kept short on purpose: this is prepended to every turn, and a long
        persona is paid for in latency on each one.
        """
        abilities = (
            ", ".join(f"{label} ({len(names)})" for label, names in self.groups)
            or "nothing registered yet"
        )
        return (
            f"You are {self.name}, a voice assistant that lives on {self.master}'s own "
            f"Windows PC. You are the one they talk to, and {self.master} is your master: "
            f"when they ask for something, you do it or you say plainly that you cannot.\n\n"
            f"Right now you can ({self.tool_count} tools registered): {abilities}.\n\n"
            "How you answer:\n"
            f"- You are speaking to {self.master}, so answer as yourself, not as a service.\n"
            "- Short and concrete. A short true answer beats a long hedged one.\n"
            "- If you do not know, say so. Guessing is worse than saying nothing.\n"
            "- If something failed, say what failed. Do not paper over it.\n"
            "- Never claim a capability you were not just told you have.\n\n"
            "What you cannot do:\n" + "".join(f"- {line}\n" for line in LIMITS)
        )


def _master_name() -> str:
    return os.getenv("HERMUS_MASTER", "").strip() or DEFAULT_MASTER


def _agent_name() -> str:
    return os.getenv("HERMUS_AGENT_NAME", "").strip() or DEFAULT_NAME


def live_tool_names(registry=None) -> list[str]:
    """Every registered tool name, read live.

    Falls back to an empty list rather than raising: a persona must never be
    the reason a conversation fails, and "I have no tools" is an answer the
    model can work with.
    """
    try:
        if registry is None:
            from core.tool_registry import tool_registry as registry
        snapshot = registry.list_tools()
        return sorted(str(n) for n in (snapshot.get("tools") or []))
    except Exception:  # noqa: BLE001
        return []


def group_capabilities(tool_names: list[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Bucket tools into human-readable groups by name substring.

    Substring matching on purpose. The registry has no category field worth
    trusting, and a 197-entry list is unreadable in a prompt anyway. A tool can
    land in more than one group, which is honest - the computer tools really do
    both run commands and control the screen.
    """
    lowered = [(n, n.lower()) for n in tool_names]
    groups: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()

    for label, needles in CAPABILITY_GROUPS:
        matched = tuple(sorted({n for n, low in lowered if any(k in low for k in needles)} - seen))
        if matched:
            groups.append((label, matched))
            seen.update(matched)
    return tuple(groups)


def build_persona(registry=None) -> Persona:
    """The persona for this turn, counted from the live registry."""
    names = live_tool_names(registry)
    return Persona(
        name=_agent_name(),
        master=_master_name(),
        tool_count=len(names),
        groups=group_capabilities(names),
    )
