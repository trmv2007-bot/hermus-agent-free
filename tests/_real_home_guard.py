"""Refuse to put test state in a person's own HERMUS home.

On 2026-11-23 the browser suite wrote a fake onboarding record into Rishi's
``~/.hermus`` and his real ``onboarding.json`` was lost with it. A convention
would not have stopped that, so this module makes it impossible: a session whose
home resolves into the real one does not start, and a fixture cannot be handed a
home that sits outside pytest's temp tree.

The override is deliberate and loud: ``HERMUS_ALLOW_REAL_HOME=1`` is the only way
past it, and nothing in the repository sets it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: The one escape hatch, for a human who means to work against their own data.
ALLOW_REAL_HOME = "HERMUS_ALLOW_REAL_HOME"

#: The message every refusal carries, so a reader knows why the suite stopped.
WHY = (
    "the 2026-11-23 incident destroyed a real onboarding record from a test run; "
    f"point it at a temporary directory instead, or set {ALLOW_REAL_HOME}=1 on purpose"
)


def product_home() -> Path:
    """Where the gateway keeps a person's data when ``HERMUS_HOME`` is unset."""
    return (Path.home() / ".hermus").resolve()


def real_home_hit(path: str | Path) -> Path | None:
    """The real home that ``path`` lands in, or ``None`` when it is somewhere safe.

    ``~/.hermus`` is caught anywhere inside it. The profile root is caught only
    when it *is* the path: on Windows pytest's own ``tmp_path`` lives under
    ``%LOCALAPPDATA%\\Temp``, inside the profile, and that is where tests are
    supposed to write — a prefix rule on the profile would refuse every test.
    """
    resolved = Path(path).expanduser().resolve()
    product = product_home()
    if resolved == product or product in resolved.parents:
        return product
    profile = Path.home().resolve()
    if resolved == profile:
        return profile
    return None


def refuse_real_home(path: str | Path, what: str) -> Path:
    """The resolved ``path``, or a refusal naming where it would have written.

    Raises :class:`pytest.UsageError`, which stops a session before any test
    runs and prints the message without a traceback.
    """
    resolved = Path(path).expanduser().resolve()
    if os.environ.get(ALLOW_REAL_HOME) == "1":
        return resolved
    hit = real_home_hit(resolved)
    if hit is not None:
        raise pytest.UsageError(f"{what} resolves to {resolved}, inside the real user home {hit}. {WHY}")
    return resolved


def temp_home(tmp_path_factory: pytest.TempPathFactory, path: str | Path) -> Path:
    """``path``, proved to sit inside this session's temp tree.

    The browser fixtures make their own homes; this is what stops one of them
    from ever receiving a path outside ``tmp_path``, however it was computed.
    """
    resolved = Path(path).resolve()
    base = Path(tmp_path_factory.getbasetemp()).resolve()
    if base not in resolved.parents:
        raise AssertionError(f"test home {resolved} escaped the session temp root {base}")
    return refuse_real_home(resolved, f"test home {resolved}")
