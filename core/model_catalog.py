"""The ranked catalog every free model choice is read from, and why each rank holds.

A picker that answers "which model?" with whatever the provider's API listed
first, or with the cheapest thing it recognises, is how an install on a machine
already holding a 4B and an 8B ended up talking to Mistral's smallest model.
Arrival order is not capability, and neither is a name that happens to start
with a letter early in the alphabet.

So the order here is authored data, one explicit list per provider, strongest
first, and every position carries the reason it holds it: parameter count,
model family and generation, context window, and whether the model can call
tools. :func:`pick` walks that list; :func:`best_of` compares candidates across
providers on the same data instead of trusting which one was discovered first.

What ``strength`` is, and what it is not
----------------------------------------
``strength`` is a coarse *effective capability* figure — parameter count adjusted
for generation and, for mixture-of-experts models, for how much of the model a
token actually touches. It exists only to order candidates against each other.
It is not a benchmark, it is never presented to a person as a score, and where a
model's size is unpublished the figure is authored from its family and position.
Within one provider the authored list order is authoritative; ``strength`` only
decides comparisons *across* providers (this machine versus that free tier).

What this module does *not* claim
---------------------------------
Availability. A free tier rate-limits per model, retires names without notice and
serves different models to different plans, so "this model works on your key" is a
fact only a request can establish. :mod:`core.model_health` makes that request;
this module ranks what the models *are*. No `why` below says a model is dead, and
none says it is reachable — both would be guesses wearing the clothes of data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: runtimes that answer without a network and without a key
LOCAL_PROVIDERS = ("ollama", "lmstudio", "nollama")

#: hosted endpoints that answer with no key *and* no account, in ladder order.
#: Each one is a documented anonymous tier, rate-limited per address, so it is a
#: rung of last resort rather than a replacement for a model on the machine.
#: Pollinations first: measured from this machine, it answers every request.
#: OVHcloud's anonymous quota turns out to be one request per model per *year*
#: per address (its own headers say so: ``x-ratelimit-limit-year: 1``), so it is
#: kept as the rung below — stronger models, but a fresh address is what makes
#: them usable, which is exactly what a second rung is for.
KEYLESS_PROVIDERS = ("pollinations", "ovh")

#: hosted providers whose free plan a person can paste a key for
FREE_PROVIDERS = ("mistral", "groq", "openrouter", "gemini", "cerebras", "together")

#: where each provider's free key is read from
FREE_KEYS = {
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "together": "TOGETHER_API_KEY",
}

#: the ladder a turn is resolved down, in order. Each rung is attempted only
#: when the one before it has nothing that can answer, and the reason it fell
#: through is kept, so "no model" is never the whole story.
TIERS = ("local", "keyless", "keyed", "stub")


@dataclass(frozen=True)
class Model:
    """One catalog row: what it is, what it can do, and why it sits where it does."""

    provider: str
    name: str
    family: str
    params_b: float | None
    context: int | None
    tools: bool
    why: str
    strength: float | None = None

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.name}"

    @property
    def size(self) -> str:
        if self.params_b is None:
            return "size not published"
        size = f"{self.params_b:g}B"
        return f"{size}, {self.context // 1000}k context" if self.context else size

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "name": self.name,
            "ref": self.ref,
            "family": self.family,
            "params_b": self.params_b,
            "context": self.context,
            "size": self.size,
            "tools": self.tools,
            "why": self.why,
            "strength": strength(self),
        }


# --------------------------------------------------------------- the catalog
# A snapshot of each free plan as of November 2026. When a provider moves a
# model, edit its list here — this is the only place a default model is named.

_MISTRAL = (
    Model(
        "mistral",
        "mistral-small-latest",
        "mistral",
        24,
        128_000,
        True,
        "the largest model Mistral serves on a free key (Small 4: 24B, 128k context, native function calling)",
        strength=24,
    ),
    Model(
        "mistral",
        "ministral-8b-latest",
        "ministral",
        8,
        128_000,
        True,
        "a 2026-generation 8B that Mistral positions above the older 12B Nemo on reasoning and instruction following",
        strength=9,
    ),
    Model(
        "mistral",
        "open-mistral-nemo",
        "mistral-nemo",
        12,
        128_000,
        True,
        "an older 12B: ranked below the newer Ministrals on generation, and only a probe can say whether a given key still reaches it",
        strength=7,
    ),
    Model(
        "mistral",
        "ministral-3b-latest",
        "ministral",
        3,
        128_000,
        True,
        "the smallest current Ministral: kept for machines that cannot hold anything bigger",
        strength=3,
    ),
)

_GROQ = (
    Model(
        "groq",
        "openai/gpt-oss-120b",
        "gpt-oss",
        120,
        131_072,
        True,
        "Groq's free plan serves this 120B mixture-of-experts at roughly 500 tokens/s; the strongest free model there by a wide margin",
        strength=48,
    ),
    Model(
        "groq",
        "qwen/qwen3.8-27b",
        "qwen3",
        27,
        131_072,
        True,
        "27B dense open-weight model on the free plan: less breadth than gpt-oss-120b, more headroom than the 20B",
        strength=27,
    ),
    Model(
        "groq",
        "openai/gpt-oss-20b",
        "gpt-oss",
        20,
        131_072,
        True,
        "the 20B sibling: same family and the same tool contract, with a larger free request budget and less depth",
        strength=20,
    ),
)

_OPENROUTER = (
    Model(
        "openrouter",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nemotron",
        550,
        1_000_000,
        True,
        "the largest free model on OpenRouter's public list — 550B mixture-of-experts, 1M context, tool calling",
        strength=55,
    ),
    Model(
        "openrouter",
        "nvidia/nemotron-3.5-lightning:free",
        "nemotron",
        None,
        1_000_000,
        True,
        "a 1M-context, tool-calling free model whose size is unpublished; ranked below Ultra on family position",
        strength=30,
    ),
    Model(
        "openrouter",
        "recursive/flint-v1.0:free",
        "flint",
        None,
        262_000,
        True,
        "an open-weight coding-agent model built for tool loops; narrower than Nemotron, better at code",
        strength=22,
    ),
    Model(
        "openrouter",
        "google/gemma-4-26b-a4b-it:free",
        "gemma4",
        26,
        262_000,
        False,
        "26B mixture-of-experts with no function calling, so it is skipped whenever a step needs a tool",
        strength=20,
    ),
    Model(
        "openrouter",
        "liquid/lfm-3-1.7b:free",
        "lfm3",
        1.7,
        262_000,
        True,
        "the last resort of the free list: 1.7B, tool-capable, good only for short answers",
        strength=1.7,
    ),
)

_GEMINI = (
    Model(
        "gemini",
        "gemini-3.8-flash",
        "gemini",
        None,
        1_000_000,
        True,
        "the current free Flash: 1M context and tool calling, and the top of the free plan since Pro left AI Studio",
        strength=40,
    ),
    Model(
        "gemini",
        "gemini-3.5-flash-lite",
        "gemini",
        None,
        1_000_000,
        True,
        "the smaller free sibling: a higher request budget and less reasoning headroom",
        strength=18,
    ),
    Model(
        "gemini",
        "gemini-2.5-flash",
        "gemini",
        None,
        1_000_000,
        True,
        "the previous Flash: Google lists it as deprecated for new accounts, so it ranks last; a probe decides whether this key still reaches it",
        strength=14,
    ),
)

# The two keyless rungs. Their names and their tool-calling behaviour were read
# from the endpoints themselves rather than from a doc page: the model list is
# what the endpoint serves, and one probe per family says whether a tool schema
# comes back as a tool call. Neither tier has a published model-retirement
# schedule, so nothing here claims one is permanent.
_OVH = (
    Model(
        "ovh",
        "Qwen3.5-397B-A17B",
        "qwen3",
        397,
        262_144,
        True,
        "the largest model on the anonymous endpoint: a 397B mixture-of-experts generation with 17B active that answered a tool call in 1.2s here — its anonymous allowance is one request per model per address per year",
        strength=50,
    ),
    Model(
        "ovh",
        "gpt-oss-120b",
        "gpt-oss",
        120,
        131_072,
        True,
        "OpenAI's 120B open-weight mixture-of-experts with the same tool contract, as a second name on the same endpoint when the top one is busy",
        strength=48,
    ),
    Model(
        "ovh",
        "Qwen3.8-27B",
        "qwen3",
        27,
        262_144,
        True,
        "the newest dense 27B generation on the endpoint: less breadth than the 397B and a fraction of the active parameters",
        strength=31,
    ),
    Model(
        "ovh",
        "Qwen3.6-27B",
        "qwen3",
        27,
        262_144,
        True,
        "the generation before it at the same size; kept so one name disappearing does not leave the rung empty",
        strength=28,
    ),
    Model(
        "ovh",
        "Meta-Llama-3_3-70B-Instruct",
        "llama3.3",
        70,
        131_072,
        True,
        "70B from the 2024 Llama generation: more parameters than the Qwens below it and older reasoning",
        strength=26,
    ),
    Model(
        "ovh",
        "nemotron3-nano-30b",
        "nemotron",
        30,
        131_072,
        True,
        "NVIDIA's 30B nano: the small fast option on this endpoint, kept for short turns",
        strength=20,
    ),
    Model(
        "ovh",
        "Qwen3.5-9B",
        "qwen3",
        9,
        262_144,
        True,
        "9B from the current Qwen generation: the smallest thing here that still calls tools, for prompts a large model would waste",
        strength=10,
    ),
)

_POLLINATIONS = (
    Model(
        "pollinations",
        "openai-fast",
        "gpt-oss",
        20,
        None,
        False,
        "GPT-OSS 20B reasoning behind an anonymous endpoint; its listing advertises tool calls, but a probe returned a plain reply instead of a tool call, so it is ranked chat-only",
        strength=20,
    ),
)

CATALOG: dict[str, tuple[Model, ...]] = {
    "mistral": _MISTRAL,
    "groq": _GROQ,
    "openrouter": _OPENROUTER,
    "gemini": _GEMINI,
    "ovh": _OVH,
    "pollinations": _POLLINATIONS,
}

# A provider whose free plan offers exactly one usable model says so here rather
# than inventing a ranking: the catalog is the place the product admits a thin
# free tier, not a place it pads one out.
THIN_FREE_PLANS = {
    "cerebras": "only the 8B and 70B dense models are free; ranked by size at runtime",
    "together": "the free models rotate monthly and no list is published; ranked by parsed size at runtime",
}


# ------------------------------------------------------- known local families
#: a newer family is worth more than an older one at the same size
FAMILY_WEIGHT = {
    "qwen3": 1.15,
    "qwen2.5": 1.05,
    "llama3.3": 1.05,
    "llama3.1": 0.95,
    "llama3.2": 0.9,
    "gemma4": 1.1,
    "gemma3": 1.0,
    "gemma2": 0.85,
    "phi4": 1.0,
    "phi3": 0.8,
    "deepseek": 1.15,
    "lfm3": 1.0,
    "lfm2": 1.0,
    "ministral": 1.05,
    "mistral": 0.95,
    "nemotron": 1.1,
    "gpt-oss": 1.1,
    "gemini": 1.1,
    "flint": 1.0,
    "granite": 0.95,
    "spark": 0.9,
}

#: families that honour the tool-calling contract, and families that do not
TOOL_FAMILIES = {
    "qwen3",
    "qwen2.5",
    "llama3.3",
    "llama3.1",
    "llama3.2",
    "deepseek",
    "lfm3",
    "lfm2",
    "ministral",
    "mistral",
    "mistral-nemo",
    "nemotron",
    "gpt-oss",
    "gemini",
    "flint",
    "granite",
    "phi4",
    "spark",
}
NO_TOOL_FAMILIES = {"gemma", "gemma2", "gemma3", "phi3"}

#: longest match first, so ``llama3.2`` is not read as ``llama3``
_FAMILIES = tuple(sorted(set(FAMILY_WEIGHT) | TOOL_FAMILIES | NO_TOOL_FAMILIES, key=len, reverse=True))
_SIZE = re.compile(r"(?:^|[^0-9.])(\d+(?:\.\d+)?)\s*b(?![0-9a-z])", re.IGNORECASE)


def provider_of(ref: str) -> str:
    return str(ref or "").split("/", 1)[0].strip().lower()


def name_of(ref: str) -> str:
    ref = str(ref or "")
    return ref.split("/", 1)[1] if "/" in ref else ref


def is_local(ref: str) -> bool:
    return provider_of(ref) in LOCAL_PROVIDERS


def is_keyless(ref: str) -> bool:
    """Does this provider answer without any credential at all?"""
    return provider_of(ref) in KEYLESS_PROVIDERS


def needs_key(provider: str) -> bool:
    """Does onboarding have to ask a person for a key to use this provider?

    Only the keyed free tiers do. A local runtime and a keyless endpoint answer
    as shipped, so asking for a credential there would be a question with no
    effect on the answer.
    """
    return provider not in LOCAL_PROVIDERS and provider not in KEYLESS_PROVIDERS


def tier_of(ref: str) -> str:
    """Which rung of the ladder a reference belongs to."""
    provider = provider_of(ref) if "/" in str(ref) else str(ref or "").lower()
    if provider in LOCAL_PROVIDERS:
        return "local"
    if provider in KEYLESS_PROVIDERS:
        return "keyless"
    if provider == "mock" or not provider:
        return "stub"
    return "keyed"


def family_of(name: str) -> str:
    lowered = str(name or "").lower()
    for family in _FAMILIES:
        if family in lowered:
            return family
    return "unknown"


def params_of(name: str) -> float | None:
    """The size in billions parsed from a name like ``qwen2.5-coder:14b``."""
    sizes = [float(m) for m in _SIZE.findall(str(name or ""))]
    return max(sizes) if sizes else None


def strength(entry: Model) -> float:
    """Effective capability, for ordering only. See the module docstring."""
    if entry.strength is not None:
        return float(entry.strength)
    params = entry.params_b if entry.params_b is not None else params_of(entry.name)
    weight = FAMILY_WEIGHT.get(family_of(entry.name), 0.9)
    return (params or 1.0) * weight


def supports_tools(ref: str) -> bool:
    """Does this model call tools? Catalog facts first, then the family."""
    entry = find(ref)
    if entry is not None:
        return entry.tools
    family = family_of(name_of(ref))
    if family in NO_TOOL_FAMILIES:
        return False
    return family in TOOL_FAMILIES


def inferred(provider: str, name: str) -> Model:
    """A model outside the catalog, described from its own name.

    Unknown free-tier models exist — OpenRouter's list moves weekly — so an
    unrecognised name is ranked by parsed size and family rather than trusted
    because it arrived first.
    """
    family = family_of(name)
    tools = family in TOOL_FAMILIES
    why = (
        f"not in the catalog; ranked from its name as a {family} model of {params_of(name) or 'unknown'}B"
        if family != "unknown"
        else "not in the catalog and its family is unknown; ranked only by parsed size, so it is never assumed strong"
    )
    return Model(
        provider=provider,
        name=name,
        family=family,
        params_b=params_of(name),
        context=None,
        tools=tools,
        why=why,
    )


def find(ref: str) -> Model | None:
    """The catalog row for one reference, if the catalog knows it."""
    provider, name = provider_of(ref), name_of(ref)
    for entry in CATALOG.get(provider, ()):
        if entry.name == name:
            return entry
    return None


def describe(ref: str) -> Model:
    """The catalog row, or one inferred from the name when it is outside it."""
    entry = find(ref)
    if entry is not None:
        return entry
    return inferred(provider_of(ref), name_of(ref))


def ranked(provider: str) -> list[Model]:
    """One provider's list, strongest first."""
    return list(CATALOG.get(provider, ()))


def default_model(provider: str) -> str | None:
    """The model this catalog would pick for a provider, with tool calling on."""
    pick_result = pick(provider)
    return pick_result.choice.name if pick_result.choice else None


# ------------------------------------------------------------------ picking


@dataclass(frozen=True)
class Pick:
    """A choice, what was skipped to reach it, and what it beat.

    ``skipped`` is a model that was *stronger* and refused for a missing
    capability; ``runners`` are the models it won against. Keeping the two apart
    is what lets a reason sentence say "skipped X, chose Y" without pretending
    a weaker model was skipped.
    """

    choice: Model | None
    skipped: tuple[tuple[Model, str], ...] = ()
    runners: tuple[Model, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "choice": self.choice.to_dict() if self.choice else None,
            "skipped": [{"ref": entry.ref, "why": why} for entry, why in self.skipped],
            "runners": [entry.to_dict() for entry in self.runners],
            "note": self.note,
        }


def pick(provider: str, *, need_tools: bool = True) -> Pick:
    """Walk one provider's ranking and stop at the first model that fits.

    When a task needs tool calling, a stronger model without it is skipped and
    the skip is recorded — never silently replaced by something cheaper, and
    never kept just because it ranks first.
    """
    entries = ranked(provider)
    if not entries:
        allowed = THIN_FREE_PLANS.get(provider)
        note = f"no catalog for {provider}" + (f" ({allowed})" if allowed else "")
        return Pick(None, note=note)
    skipped: list[tuple[Model, str]] = []
    for entry in entries:
        if need_tools and not entry.tools:
            skipped.append((entry, "no function calling, and this product drives tools"))
            continue
        return Pick(entry, tuple(skipped), note="")
    return Pick(
        entries[0],
        # The chosen model was skipped first on this list, so it is not a skip.
        tuple(skipped[1:]),
        note=f"no free model on {provider} calls tools; {entries[0].name} is the strongest anyway",
    )


def best_of(refs: list[str], *, need_tools: bool = True) -> Pick:
    """The strongest candidate across providers. Local wins a tie.

    Order of arrival is irrelevant: the same set of references always resolves
    the same way, and a local model only beats a hosted one when it is at least
    as strong, so a machine with an 8B does not pretend it matches a 24B free
    tier and a hosted tier is not spent to duplicate what the machine can do.
    """
    candidates: list[Model] = []
    skipped: list[tuple[Model, str]] = []
    seen: set[str] = set()
    for ref in refs:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        entry = describe(ref)
        if need_tools and not entry.tools:
            skipped.append((entry, "no function calling, and this product drives tools"))
            continue
        candidates.append(entry)
    if not candidates:
        if skipped:
            skipped.sort(key=lambda item: -strength(item[0]))
            choice = skipped[0][0]
            return Pick(
                choice,
                tuple(skipped[1:]),
                note=f"nothing reachable calls tools; {choice.name} is the strongest of what is",
            )
        return Pick(None, note="nothing is reachable to run a turn on")
    candidates.sort(key=lambda entry: (-strength(entry), 0 if is_local(entry.ref) else 1, entry.ref))
    winner = candidates[0]
    return Pick(winner, tuple(skipped), tuple(candidates[1:8]), note="")


# ---------------------------------------------------------------- the reasons


def size_phrase(entry: Model) -> str:
    """``24B``, or ``unpublished size`` — the short form a reason sentence uses."""
    if entry.params_b is None:
        return "unpublished size"
    return f"{entry.params_b:g}B"


def comparison_reason(winner: Model, others: list[Model]) -> str:
    """One line saying why this model won, naming what it beat.

    Read by a person on the onboarding screen, in the room, and in the turn's
    routing record, so it states the comparison rather than a score.
    """
    nearest = others[0] if others else None
    if nearest is None:
        return f"{winner.name} is the only free model reachable right now"
    if is_local(winner.ref) and is_local(nearest.ref):
        return (
            f"{winner.name} on your machine ({size_phrase(winner)}) is the strongest of "
            f"{len(others) + 1} pulled models, ahead of {nearest.name} ({size_phrase(nearest)})"
        )
    if is_local(winner.ref):
        return (
            f"{winner.name} on your machine ({size_phrase(winner)}) is at least as strong as "
            f"{nearest.ref} ({size_phrase(nearest)}), so nothing hosted is spent"
        )
    if is_local(nearest.ref):
        same_size = size_phrase(winner) == size_phrase(nearest)
        edge = (
            "at the same size, on a newer generation" if same_size else f"at {size_phrase(winner)} against {size_phrase(nearest)}"
        )
        return (
            f"{winner.provider} {winner.name} is stronger than your best local model "
            f"{nearest.name} — {edge}, so the free {winner.provider} tier wins"
        )
    return (
        f"{winner.provider} {winner.name} ({size_phrase(winner)}) is stronger than "
        f"{nearest.provider} {nearest.name} ({size_phrase(nearest)})"
    )


def short_reason(winner: Model, others: list[Model]) -> str:
    """The compact form of the same decision, for a one-line model badge."""
    nearest = others[0] if others else None
    if nearest is None:
        return "the only free model reachable"
    if is_local(winner.ref) and is_local(nearest.ref):
        return f"strongest of {len(others) + 1} pulled, ahead of {nearest.name} ({size_phrase(nearest)})"
    return f"strongest reachable, ahead of {nearest.name} ({size_phrase(nearest)})"


def upgrade_reason(old: Model, new: Model, *, gone: str = "") -> str:
    """Why a recorded model is being replaced, in words the person can check.

    ``gone`` is a measured cause ("HTTP 404 — this key cannot call the name",
    "Ollama is not running"); when it is empty the reason is capability alone.
    """
    if gone:
        return f"{old.ref} cannot answer any more ({gone}), so it is replaced by {new.name} ({size_phrase(new)})"
    return (
        f"the recorded {old.ref} ({size_phrase(old)}) is weaker than {new.ref} ({size_phrase(new)}), "
        "which is reachable now, so it is upgraded"
    )


def model_reason(ref: str) -> str:
    """Why this reference sits where it does, for the turn's routing record.

    Empty for a reference the catalog knows nothing about (a mock, or a name with
    no recognisable family), so a record never claims a reason it does not have.
    """
    entry = find(ref)
    if entry is not None:
        return entry.why
    unknown = inferred(provider_of(ref), name_of(ref))
    return "" if unknown.family == "unknown" else unknown.why


def as_rows() -> list[dict[str, Any]]:
    """Every catalog row, in rank order, for a table in a report or the API."""
    rows: list[dict[str, Any]] = []
    for provider in CATALOG:
        for position, entry in enumerate(ranked(provider), start=1):
            row = entry.to_dict()
            row["rank"] = position
            rows.append(row)
    return rows


def catalog_payload() -> dict[str, Any]:
    """The catalog as the API serves it: one ranked list per provider."""
    return {provider: [entry.to_dict() for entry in ranked(provider)] for provider in CATALOG}
