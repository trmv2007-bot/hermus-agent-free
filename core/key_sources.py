"""Where a free-tier key comes from, and which of them actually works.

A person can end up with two keys for the same provider: the one a setup script
wrote into a checkout's ``.env``, and the one they keep in their own home
directory. Choosing by whichever file is found first silently uses that one, and
when it has been revoked the provider reads as "denied" forever while the other
key answers perfectly.

So every configured key is a candidate. Candidates are collected from every place
the product reads keys, probed in order, and the one that answers is the one
used — with the path it came from kept in the reason, because "denied" on its own
names nothing a person can fix and "denied, from C:\\Users\\…\\.env" is actionable
in one second.

Order is deliberate: an exported variable or the checkout's ``.env`` first (that
is what the setup flow writes), then the person's own home ``.env``, then the key
store the control room writes. Order only decides ties; a key that does not work
never wins just for being first.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import model_catalog as catalog
from . import model_health

#: how long a resolution is believed before the keys are asked again
TTL = 600.0

_cache: dict[str, tuple[float, Resolution]] = {}


@dataclass
class Resolution:
    """The key that answered for one provider, and every attempt that did not."""

    provider: str
    key: str = ""
    source: str = ""
    ref: str = ""
    refusals: list[dict[str, Any]] = field(default_factory=list)
    checked_at: float = 0.0

    @property
    def usable(self) -> bool:
        return bool(self.ref)

    def to_dict(self) -> dict[str, Any]:
        """Never includes the key itself — these dicts go into payloads and logs."""
        return {
            "provider": self.provider,
            "source": self.source,
            "ref": self.ref,
            "refusals": list(self.refusals),
            "checked_at": self.checked_at,
        }


def home_env_path() -> Path:
    return Path(os.path.expanduser("~")) / ".env"


def dotenv_allowed() -> bool:
    """Respect the same switch core.config uses, so a developer's file never leaks in.

    ``HERMUS_NO_DOTENV=1`` is what the test suite sets; without this check a test
    run would read whatever keys the person happens to keep on that machine.
    """
    return os.environ.get("HERMUS_NO_DOTENV", "") not in ("1", "true", "True")


def checkout_env_path() -> Path:
    """The .env beside the package, which is what the setup flow writes."""
    return Path(__file__).resolve().parent.parent / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in catalog.FREE_KEYS.values():
            out[name] = value.strip().strip('"').strip("'")
    return out


def store_keys(provider: str) -> list[str]:
    """Keys pasted through the control room, in the order they were added."""
    try:
        from .multi_key import multi_key_manager

        entries = (multi_key_manager.list_keys(provider) or {}).get(provider) or []
    except Exception:  # noqa: BLE001 - no store is "no key"
        return []
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("key"):
            out.append(str(entry["key"]))
    return out


def sources(provider: str) -> list[tuple[str, str]]:
    """Every configured key for a provider, as ``(value, where it came from)``."""
    env_key = catalog.FREE_KEYS.get(provider, "")
    found: list[tuple[str, str]] = []

    value = os.environ.get(env_key, "") if env_key else ""
    if value:
        found.append((value, "your environment"))

    if dotenv_allowed():
        checkout = _read_env_file(checkout_env_path()).get(env_key, "")
        if checkout:
            found.append((checkout, str(checkout_env_path())))
        home = _read_env_file(home_env_path()).get(env_key, "")
        if home:
            found.append((home, str(home_env_path())))

    found.extend((key, "the key store in the app") for key in store_keys(provider))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for value, label in found:
        if value and value not in seen:
            seen.add(value)
            unique.append((value, label))
    return unique


def available(provider: str) -> bool:
    return bool(sources(provider))


def reset_cache() -> None:
    _cache.clear()


def resolve(provider: str, *, need_tools: bool = True, force: bool = False) -> Resolution:
    """The first key whose model answers, and the path it was read from.

    Without probing enabled (the test suite), the first configured key is used and
    labelled unprobed, so the picker still behaves and nothing is claimed.
    """
    cache_key = f"{provider}:{int(bool(need_tools))}"
    cached = _cache.get(cache_key)
    if cached is not None and not force and time.time() - cached[0] < TTL:
        return cached[1]

    candidates = sources(provider)
    if not candidates:
        return Resolution(provider)

    picked = catalog.pick(provider, need_tools=need_tools)
    fallback_model = picked.choice.name if picked.choice else ""

    if not model_health.probe_enabled():
        result = Resolution(
            provider,
            key=candidates[0][0],
            source=candidates[0][1],
            ref=f"{provider}/{fallback_model}" if fallback_model else "",
            refusals=[{"ref": f"{provider}/{fallback_model}", "why": "not probed", "state": model_health.UNKNOWN}]
            if fallback_model
            else [],
            checked_at=time.time(),
        )
        _cache[cache_key] = (time.time(), result)
        return result

    refusals: list[dict[str, Any]] = []
    unproven: tuple[str, str, str] | None = None
    for value, label in candidates:
        answers: list[model_health.ModelState] = []
        for state in model_health.rank(provider, value, force=force):
            entry = catalog.find(state.ref)
            if need_tools and entry is not None and not entry.tools:
                refusals.append(
                    {
                        "ref": state.ref,
                        "why": f"no function calling, and this product drives tools (key from {label})",
                        "state": state.state,
                    }
                )
                continue
            if state.state == model_health.OK:
                answers.append(state)
                result = Resolution(
                    provider=provider,
                    key=value,
                    source=label,
                    ref=state.ref,
                    refusals=refusals,
                    checked_at=time.time(),
                )
                _cache[cache_key] = (time.time(), result)
                return result
            if state.state == model_health.UNKNOWN and unproven is None:
                unproven = (value, label, state.ref)
            refusals.append(
                {
                    "ref": state.ref,
                    "why": f"{state.detail or state.state} (key from {label})",
                    "state": state.state,
                }
            )
        if answers:  # pragma: no cover - kept for clarity; returns above
            break

    if unproven is not None:
        value, label, ref = unproven
        result = Resolution(provider, key=value, source=label, ref=ref, refusals=refusals, checked_at=time.time())
        _cache[cache_key] = (time.time(), result)
        return result

    result = Resolution(provider, refusals=refusals, checked_at=time.time())
    _cache[cache_key] = (time.time(), result)
    return result
