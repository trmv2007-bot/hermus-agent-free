"""Measurable routing — local model or free hosted tier, decided and recorded.

The rule the product lives by is *free first*: a local model, then a free-tier
hosted key, then nothing. That rule is a policy, not a measurement, until every
turn says which tier it used and why, and until the misses are counted.

So routing here has three jobs:

1. **Decide.** :func:`decide_route` is a pure function over the request text,
   the local model reference and the configured free hosted references. No
   network, no model call, no tokens — the same input always gives the same
   decision, which is what makes it testable.

2. **Refuse to spend what is not free.** A provider that is not in the local set
   or the free-tier set never becomes a candidate. Unknown providers fail
   closed: a typo in an env var cannot quietly route a turn to a paid API.

3. **Count the misses.** After the turn, :func:`routing_outcome` labels the
   decision. A hosted turn that finished in a couple of steps spent capacity a
   local model would have covered — *over-routing is waste*. A local turn with
   two failed tool calls is a task the local model could not do — *under-routing*
   is the reason a repair turn escalates. :class:`RoutingLedger` keeps both
   counts on disk so the balance is a number, not an impression.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import model_catalog as catalog

# ---------------------------------------------------------------- tiers

TIER_LOCAL = "local"
TIER_FREE_HOSTED = "free_hosted"
TIER_UNAVAILABLE = "unavailable"

#: providers that run on this machine and cost nothing to call
LOCAL_PROVIDERS = ("ollama", "nollama", "lmstudio", "local", "llamacpp", "vllm")

#: hosted providers free-tier keys grant access to (see core/local_first.py)
FREE_HOSTED_PROVIDERS = ("groq", "mistral", "openrouter", "cerebras", "together", "gemini")

#: the free hosted model each provider serves by default. Read from the catalog so
#: there is one ranked list per provider: routing escalates to the strongest free
#: model, never to whatever name this file happened to remember.
FREE_HOSTED_DEFAULT_MODELS = {
    # Keyed tiers only: this map is what routing may escalate to, and the
    # keyless rungs are chosen by the ladder in core.brain instead.
    **{provider: catalog.default_model(provider) or "" for provider in FREE_HOSTED_PROVIDERS if catalog.ranked(provider)},
    "cerebras": "llama3.1-8b",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
}

#: config keys holding each provider's free-tier key
FREE_HOSTED_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "together": "TOGETHER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

#: difficulty at which the local model's ceiling is no longer assumed sufficient
ESCALATE_AT_DIFFICULTY = 3

#: once a measurement says the local model cannot use tools, a request this
#: difficult is multi-step enough to send to the strongest reachable endpoint
WEAK_LOCAL_ESCALATE_DIFFICULTY = 2

#: a hosted turn this cheap spent more capacity than the task needed
OVERROUTE_MAX_STEPS = 2

#: two failed tool calls on a local turn is the under-routing signal
UNDERROUTE_FAILED_CALLS = 2


def provider_of(ref: str) -> str:
    """``ollama/llama3.2:1b`` -> ``ollama``; a bare ref has no provider."""
    return str(ref or "").split("/", 1)[0].strip().lower()


def tier_of(ref: str) -> str:
    """Which free tier a model reference belongs to, or ``unavailable``."""
    provider = provider_of(ref)
    if provider in LOCAL_PROVIDERS:
        return TIER_LOCAL
    if provider in FREE_HOSTED_PROVIDERS:
        return TIER_FREE_HOSTED
    return TIER_UNAVAILABLE


# ------------------------------------------------------------- decision


@dataclass
class RouteDecision:
    """What this turn was routed to, and the reason a person can audit."""

    tier: str
    model: str
    difficulty: int
    reason: str
    signals: list[str] = field(default_factory=list)
    local_model: str = ""
    hosted_models: list[str] = field(default_factory=list)
    forced: bool = False

    @property
    def over_route_risk(self) -> bool:
        """Hosted capacity is only justified above the escalation difficulty."""
        return self.tier == TIER_FREE_HOSTED and self.difficulty < ESCALATE_AT_DIFFICULTY

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "model": self.model,
            "difficulty": self.difficulty,
            "reason": self.reason,
            "signals": list(self.signals),
            "model_reason": catalog.model_reason(self.model),
            "local_model": self.local_model,
            "hosted_models": list(self.hosted_models),
            "forced": self.forced,
        }


def _difficulty_signals(text: str) -> tuple[int, list[str]]:
    """Difficulty plus the evidence words behind it, from the governor's scale."""
    try:
        from ..reasoning.governor import governor

        difficulty = int(governor.classify_difficulty(text))
    except Exception:  # pragma: no cover - governor is always importable
        difficulty = 1
    signals: list[str] = []
    low = (text or "").lower()
    for marker in ("build", "implement", "refactor", "migrate", "debug", "research", "plan", "audit"):
        if marker in low:
            signals.append(f"marker:{marker}")
    if len(text or "") > 200:
        signals.append("long-request")
    return difficulty, signals


def _hosted_target(current: str, hosted: list[str]) -> str:
    """The free hosted model a turn should run on.

    A turn already running on a free hosted model whose provider has a key is
    *on* the escalation tier: moving it to a different model of the same tier
    spends a second request and can land on one the key does not rank. It only
    moves when it is running somewhere else.
    """
    if tier_of(current) == TIER_FREE_HOSTED and provider_of(current) in {provider_of(ref) for ref in hosted}:
        return current
    return hosted[0]


def decide_route(
    text: str,
    *,
    local_model: str = "",
    hosted_models: list[str] | None = None,
    forced_tier: str | None = None,
    difficulty: int | None = None,
    weak_local_tools: bool = False,
) -> RouteDecision:
    """Pick the tier for one turn. Pure, deterministic, zero-token.

    ``forced_tier`` (``HERMUS_ROUTE_TIER``) pins the decision when a person wants
    it pinned; ``auto`` and empty fall through to the rules below. A forced tier
    that is unavailable is reported as such rather than silently overridden...
    except that a person who forced ``local`` gets local — the request is
    explicit, and honesty is served by saying why the ceiling is lower.

    ``weak_local_tools`` is a *measured* fact from
    :mod:`core.model_measurement`: this machine's local model failed the tool-use
    benchmark. A multi-step request then goes to the strongest reachable hosted
    endpoint instead of pretending the local model can chain tool calls. It is
    False when nothing was measured, so an unmeasured machine behaves exactly as
    before and a guess never moves traffic.
    """
    hosted = [ref for ref in (hosted_models or []) if tier_of(ref) == TIER_FREE_HOSTED]
    local_ok = tier_of(local_model) == TIER_LOCAL
    diff, signals = _difficulty_signals(text)
    if difficulty is not None:
        diff = max(1, min(5, int(difficulty)))
    if weak_local_tools:
        signals = signals + ["measured:local-weak-at-tools"]

    forced = str(forced_tier or "auto").strip().lower()
    forced = "" if forced in ("", "auto") else forced

    def _decide(tier: str, model: str, reason: str) -> RouteDecision:
        return RouteDecision(
            tier=tier,
            model=model,
            difficulty=diff,
            reason=reason,
            signals=signals + [f"difficulty:{diff}"],
            local_model=local_model,
            hosted_models=list(hosted),
            forced=bool(forced),
        )

    if forced == TIER_LOCAL:
        if local_ok:
            return _decide(TIER_LOCAL, local_model, f"tier forced to local; difficulty {diff} accepted on the local model")
        return _decide(
            TIER_UNAVAILABLE,
            "",
            "tier forced to local and no local model is bound; nothing free to run this turn",
        )

    if forced == TIER_FREE_HOSTED:
        if hosted:
            return _decide(TIER_FREE_HOSTED, hosted[0], f"tier forced to the free hosted tier ({hosted[0]})")
        return _decide(
            TIER_UNAVAILABLE,
            "",
            "tier forced to a free hosted tier but no free-tier key is configured",
        )

    if not local_ok and not hosted:
        return _decide(
            TIER_UNAVAILABLE,
            "",
            "no local model is bound and no free-tier key is configured; the turn has nothing free to run on",
        )

    if weak_local_tools and local_ok and hosted and diff >= WEAK_LOCAL_ESCALATE_DIFFICULTY:
        target = _hosted_target(local_model, hosted)
        return _decide(
            TIER_FREE_HOSTED,
            target,
            f"difficulty {diff} and this machine's local model measured weak at tool use, "
            f"so the turn goes to the strongest reachable endpoint ({target}) instead of faking it locally",
        )

    if diff < ESCALATE_AT_DIFFICULTY:
        if local_ok:
            return _decide(
                TIER_LOCAL,
                local_model,
                f"difficulty {diff}: the local model's ceiling covers this, so the free tier is not spent",
            )
        return _decide(
            TIER_FREE_HOSTED,
            _hosted_target(local_model, hosted),
            f"difficulty {diff}: no local model is bound, so the free hosted tier is the cheapest option",
        )

    if hosted:
        target = _hosted_target(local_model, hosted)
        if target == local_model:
            return _decide(
                TIER_FREE_HOSTED,
                target,
                f"difficulty {diff} would escalate, and the turn is already on the free tier ({target})",
            )
        return _decide(
            TIER_FREE_HOSTED,
            target,
            f"difficulty {diff} needs more reasoning than the local model reliably gives; "
            "escalating to the configured free-tier key",
        )

    return _decide(
        TIER_LOCAL,
        local_model,
        f"difficulty {diff} would escalate, but no free-tier key is configured; "
        "the local model is the only free option and may be too weak for this",
    )


# -------------------------------------------------------------- outcome


def routing_outcome(
    decision: RouteDecision,
    *,
    steps: int = 0,
    failed_calls: int = 0,
    verified: bool | None = None,
) -> dict[str, Any]:
    """Score the decision against what the turn actually cost and achieved.

    * **over-routed** — hosted tier, but the turn finished within
      :data:`OVERROUTE_MAX_STEPS` steps. The local model would have done it.
    * **under-routed** — local tier with at least
      :data:`UNDERROUTE_FAILED_CALLS` failed tool calls. The task was beyond the
      local model and the turn paid for it in retries.
    """
    over = decision.tier == TIER_FREE_HOSTED and steps <= OVERROUTE_MAX_STEPS and steps > 0
    under = decision.tier == TIER_LOCAL and failed_calls >= UNDERROUTE_FAILED_CALLS
    wasted_steps = max(0, steps - OVERROUTE_MAX_STEPS) if over else 0
    return {
        "tier": decision.tier,
        "model": decision.model,
        "difficulty": decision.difficulty,
        "steps": int(steps),
        "failed_calls": int(failed_calls),
        "verified": verified,
        "over_routed": bool(over),
        "under_routed": bool(under),
        "wasted_steps": wasted_steps,
        "note": (
            f"hosted tier finished in {steps} step(s); the local model was likely enough"
            if over
            else (f"routed local and hit {failed_calls} failed tool call(s); this task needed a stronger model" if under else "")
        ),
    }


def configured_hosted_refs() -> list[str]:
    """Free-tier model references whose key is configured, cheapest first.

    Reads both the process environment and keys a person added through the UI
    (``core.multi_key``). Never returns a provider outside
    :data:`FREE_HOSTED_PROVIDERS`, so a paid key cannot enter routing. Where a
    tool-use measurement exists for these models it orders them, because the
    escalation target should be the endpoint that measured best, not the one
    whose name sorts first in the catalog.
    """
    refs: list[str] = []
    for provider in FREE_HOSTED_PROVIDERS:
        if not _free_key_present(provider):
            continue
        refs.append(f"{provider}/{FREE_HOSTED_DEFAULT_MODELS[provider]}")
    if len(refs) < 2:
        return refs
    try:
        from .. import model_measurement

        return model_measurement.order_refs(refs)
    except Exception:  # noqa: BLE001 - a measurement must never break routing
        return refs


def _free_key_present(provider: str) -> bool:
    env_key = FREE_HOSTED_KEY_ENV.get(provider)
    if env_key and os.environ.get(env_key):
        return True
    try:
        from ..multi_key import multi_key_manager

        keys = multi_key_manager.list_keys(provider)
        entries = keys.get(provider) if isinstance(keys, dict) else None
        return bool(entries)
    except Exception:
        return False


def _free_key(provider: str) -> str:
    """The key itself, for the availability probe. Never logged, never stored."""
    env_key = FREE_HOSTED_KEY_ENV.get(provider)
    value = os.environ.get(env_key) if env_key else ""
    if value:
        return str(value).strip()
    try:
        from ..multi_key import multi_key_manager

        return str(multi_key_manager.get_key(provider) or "").strip()
    except Exception:
        return ""


def answering_hosted_ref(catalog_ref: str) -> str:
    """The model this free key *answers* with, or ``""`` when it answers none.

    The catalog's first choice is a ranking, and a free plan rate-limits the top
    of its own list: Mistral's key answers ``ministral-8b-latest`` with 200 and
    the catalog's top pick with 429. Naming the unmeasured top pick meant an
    escalated turn spent its one request on a rate limit and got the provider's
    error back as the answer — the escalation path failing at the seam, not at
    the model.

    So the model is measured before a turn is sent to it, through the same
    availability probe the picker uses (and its cache, so a warm process pays
    nothing). Three honest outcomes:

    * a candidate answers -> that candidate is the escalation target, even when
      the catalog ranks it second;
    * every candidate answers 429/404/401 -> ``""``: this key cannot run the
      turn, and saying so beats spending a request to fail;
    * nothing could be measured (offline, probing disabled) -> the catalog
      choice, unchanged, because there is no evidence against it.
    """
    provider = provider_of(catalog_ref)
    if provider not in FREE_HOSTED_PROVIDERS:
        return ""
    key = _free_key(provider)
    if not key:
        return ""
    try:
        from .. import model_health
    except Exception:
        return catalog_ref
    names: list[str] = []
    if "/" in str(catalog_ref):
        names.append(str(catalog_ref).split("/", 1)[1])
    names += [entry.name for entry in catalog.ranked(provider) if entry.name not in names]
    evidence = False
    for name in names:
        try:
            state = model_health.probe(provider, key, name)
        except Exception:
            return catalog_ref
        if state.state == model_health.OK:
            return f"{provider}/{name}"
        if state.state in (model_health.BUSY, model_health.GONE, model_health.DENIED):
            evidence = True
    # Measured refusals with nothing answering: this tier cannot run a turn now.
    return "" if evidence else catalog_ref


# --------------------------------------------------------------- ledger


def thinking_dir() -> Path:
    """Where routing decisions live. ``HERMUS_THINKING_DIR`` overrides it."""
    override = os.environ.get("HERMUS_THINKING_DIR")
    if override:
        return Path(override)
    try:
        from ..config import config

        return config.resolve_path("data/thinking")
    except Exception:  # pragma: no cover - config is always importable
        return Path("data/thinking")


class RoutingLedger:
    """Append-only record of routing decisions and how each one turned out."""

    def __init__(self, path: Path | None = None, *, limit: int = 500):
        self._override = Path(path) if path is not None else None
        self._limit = max(1, int(limit))

    @property
    def path(self) -> Path:
        # Resolved per call, not at import: HERMUS_THINKING_DIR (tests, the
        # benchmark) must apply to the process-wide ledger too.
        return self._override if self._override is not None else thinking_dir() / "routing.jsonl"

    def record(self, decision: RouteDecision, outcome: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        row = {
            "at": datetime.now(timezone.utc).isoformat(),
            "decision": decision.to_dict(),
            "outcome": outcome or None,
        }
        row.update({k: v for k, v in extra.items() if v is not None})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except OSError:
            # A ledger that cannot be written must never break a turn: the
            # decision is still in the result and the event stream.
            return row
        return row

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        return rows[-max(1, int(limit)) :]

    def summary(self) -> dict[str, Any]:
        rows = self.recent(self._limit)
        scored = [r for r in rows if isinstance(r.get("outcome"), dict)]
        tiers: dict[str, int] = {}
        steps_total = 0
        for row in scored:
            tier = str((row.get("outcome") or {}).get("tier") or "unknown")
            tiers[tier] = tiers.get(tier, 0) + 1
            steps_total += int((row.get("outcome") or {}).get("steps") or 0)
        over = sum(1 for r in scored if (r.get("outcome") or {}).get("over_routed"))
        under = sum(1 for r in scored if (r.get("outcome") or {}).get("under_routed"))
        verified = sum(1 for r in scored if (r.get("outcome") or {}).get("verified") is True)
        return {
            "turns": len(scored),
            "tiers": tiers,
            "over_routed": over,
            "under_routed": under,
            "verified": verified,
            "avg_steps": round(steps_total / len(scored), 2) if scored else 0.0,
            "ledger": str(self.path),
        }


routing_ledger = RoutingLedger()
