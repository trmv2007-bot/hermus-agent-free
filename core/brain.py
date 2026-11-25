"""Which model answers, where that came from, and the one command that fixes it.

A fresh install used to ship an unexamined default (``ollama/llama3.1:8b``) that
nothing installed, so the first answer a person got was the mock echo. It also
used to need a decision before it could answer at all: install a model, or paste
a key. Neither is a reasonable first five minutes for a free product.

So resolution walks a ladder, in this order:

1. **local** — a model on this machine answering on its own port. No rate limit,
   no allowance spent, and nothing typed by the person leaves the machine;
2. **keyless** — hosted free tiers that answer with no account and no key
   (:data:`core.model_catalog.KEYLESS_PROVIDERS`), so a machine with nothing
   installed still gets a real answer;
3. **keyed** — the free plans a person pastes a key for, used when the key
   exists and the rungs above cannot answer;
4. **stub** — the offline placeholder, named as the stub rather than dressed up
   as an answer.

Each rung that was tried and skipped records *why* in the result's ``skipped``
list, in the same shape the keyed tiers already use, and the whole walk is
returned in ``ladder`` so the first screen can show the decision instead of
only its outcome. Within a rung the strongest candidate wins by
:mod:`core.model_catalog`'s ranking, so arrival order still decides nothing.

Nothing here spends a token on a keyless endpoint (the ladder says what it would
use and the first request proves it), and nothing here installs anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from . import free_tier, key_sources, model_health, model_measurement
from . import model_catalog as catalog
from .providers import get_provider

# Ollama's own address, or wherever a person runs it: the same idea as
# ``config.ollama_base_url``, readable before the config singleton exists so a
# probe against "nothing is installed" does not need a real port to answer.
OLLAMA_BASE = os.environ.get("HERMUS_OLLAMA_BASE_URL") or "http://localhost:11434"
LMSTUDIO_BASE = "http://localhost:1234/v1"
NOLLAMA_BASE = "http://localhost:8010/v1"

# The smallest model that gives a usable first answer on a laptop CPU. 1B keeps
# the download ~0.6 GB and the first token under a second once warm, which is
# what makes the first-run promise ("answer in two seconds") survivable. This is
# what the install *offers to download*, not what answers: that is decided by the
# catalog, and a machine that already holds an 8B answers with the 8B.
#
# Measured on 2026-11-24 before changing it: ``qwen3.5:2b`` exists in the registry
# and is the better tool-caller on paper, but as served by Ollama 0.13 it answers
# a greeting in 145s and does not finish a single one of this repository's 15
# tool tasks (each killed at 200s with zero steps), because its default reasoning
# mode spends the whole budget thinking. With ``think: false`` the same request
# answers in 0.4s — so the swap belongs to the turn path that decides thinking,
# not to this constant. ``llama3.2:1b`` stays until that decision is made.
SMALLEST_LOCAL_MODEL = "llama3.2:1b"

# Free tiers: every one is a no-card free plan whose key the person pastes. The
# model is read from the catalog, never written here, so there is one ranked list
# per provider and no second copy to drift.
_FREE_KEYS = (
    ("mistral", "MISTRAL_API_KEY", "https://console.mistral.ai/api-keys"),
    ("groq", "GROQ_API_KEY", "https://console.groq.com/keys"),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/keys"),
    ("gemini", "GEMINI_API_KEY", "https://aistudio.google.com/apikey"),
)
FREE_TIER = tuple((provider, env_key, catalog.default_model(provider) or "", url) for provider, env_key, url in _FREE_KEYS)

STUB_MODEL = "mock/mock"

#: What the local-first order is worth, measured rather than argued. Two sibling
#: threads ran this repository's own 15-task tool set through the real turn path on
#: this PC, one isolated home per task (``tests/eval/thinking_bench.py``, 2026-11-24):
#: a local 8B passed 11/15 in 317s, a local 1B passed 7/15 in 397s, and the hosted
#: free tier passed 8/15 in 1031s because the provider refused 10 of the 15 tasks
#: with HTTP 429. So local first is not a preference in taste, it is the better
#: answer on this machine — and a hosted answer is a trade a person is owed.
MEASURED_LOCAL = "measured better and faster than the free tier on this machine (11/15 tasks in 317s against 8/15 in 1031s)"
MEASURED_HOSTED = (
    "this trades speed and quality for an endpoint that answers at all: the same tasks measured 8/15 in 1031s hosted "
    "against 11/15 in 317s on this machine"
)

# What the repository ships with before anyone has chosen. These are the values
# ``apply_resolved_default`` is allowed to replace; anything else is a decision
# somebody made, and is left alone.
SHIPPED_DEFAULTS = ("", "ollama/llama3.1:8b", "ollama/llama3.2:1b")


def _http_json(url: str, timeout: float = 1.0) -> Any:
    """GET one JSON document. Returns None on any failure — detection only."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed localhost URLs
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def ollama_serving(base: str | None = None) -> bool:
    return _http_json(f"{(base or OLLAMA_BASE).rstrip('/')}/api/tags", timeout=0.8) is not None


def ollama_models(base: str | None = None) -> list[str]:
    data = _http_json(f"{(base or OLLAMA_BASE).rstrip('/')}/api/tags", timeout=1.5)
    if not isinstance(data, dict):
        return []
    return [str(m.get("name") or "").strip() for m in data.get("models") or [] if m.get("name")]


def ollama_installed() -> bool:
    """Is the Ollama binary on PATH (or in its default Windows install dir)?"""
    if shutil.which("ollama"):
        return True
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        return os.path.isfile(os.path.join(local, "Programs", "Ollama", "ollama.exe"))
    return False


def lmstudio_installed() -> bool:
    """Is LM Studio present (its ``lms`` CLI, or the app itself)?"""
    if shutil.which("lms"):
        return True
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [os.path.join(home, ".lmstudio"), os.path.join(home, ".cache", "lm-studio")]
    if local:
        candidates += [
            os.path.join(local, "Programs", "LM Studio"),
            os.path.join(local, "LM-Studio"),
        ]
    if sys.platform == "darwin":
        candidates.append("/Applications/LM Studio.app")
    return any(os.path.exists(path) for path in candidates)


def _openai_models(base: str) -> list[str]:
    data = _http_json(f"{base.rstrip('/')}/models", timeout=0.45)
    if not isinstance(data, dict):
        return []
    return [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]


_status_cache: tuple[float, dict[str, Any]] | None = None
STATUS_TTL_SECONDS = 4.0


def local_status(force: bool = False) -> dict[str, Any]:
    """Everything the installers and the welcome screen need to decide, one probe.

    Cached for a few seconds and probed concurrently: the first screen must not
    wait on three localhost sockets that nothing is listening on (Windows takes
    about a second per refused connect), so worst case is one timeout, not three.
    """
    global _status_cache
    now = time.monotonic()
    if not force and _status_cache is not None and now - _status_cache[0] < STATUS_TTL_SECONDS:
        return _status_cache[1]

    from concurrent.futures import ThreadPoolExecutor

    def probe_ollama() -> tuple[bool, list[str]]:
        data = _http_json(f"{OLLAMA_BASE.rstrip('/')}/api/tags", timeout=0.8)
        if not isinstance(data, dict):
            return False, []
        models = [str(m.get("name") or "").strip() for m in data.get("models") or [] if m.get("name")]
        return True, models

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="hermus-brain-probe") as pool:
        ollama_job = pool.submit(probe_ollama)
        installed_job = pool.submit(ollama_installed)
        lm_installed_job = pool.submit(lmstudio_installed)
        lm_job = pool.submit(_openai_models, LMSTUDIO_BASE)
        nollama_job = pool.submit(_openai_models, NOLLAMA_BASE)
        ollama_up, models = ollama_job.result()
        installed = installed_job.result()
        lm_installed = lm_installed_job.result()
        lm_models = lm_job.result()
        nollama_models = nollama_job.result()

    status = {
        "ollama": {
            "installed": installed,
            "serving": ollama_up,
            "models": models,
        },
        "lmstudio": {"installed": lm_installed, "serving": bool(lm_models), "models": lm_models},
        "nollama": {"serving": bool(nollama_models), "models": nollama_models},
    }
    _status_cache = (now, status)
    return status


def free_key_status() -> dict[str, bool]:
    """Which free tiers have a key configured, from any of the places one can live."""
    return {provider: key_sources.available(provider) for provider, _env_key, _url in _FREE_KEYS}


def _pick_pulled(models: list[str]) -> str | None:
    """Choose from already-pulled models by capability, never by arrival order.

    The old rule took the smallest model it recognised, which is how a machine
    holding an 8B answered with a 1B. Ranking lives in the catalog so this and
    the cross-provider comparison can never disagree.
    """
    if not models:
        return None
    picked = catalog.best_of([f"ollama/{name}" for name in models])
    return picked.choice.name if picked.choice else None


def local_brain(model: str, source: str, provider: str = "ollama", *, reason: str = "") -> dict[str, Any]:
    """A local model as the brain. ``model`` is the runtime's own name verbatim.

    Namespaced pulls (``SparkLLM/Spark-X2.6-4B``) keep their namespace: the old
    code stripped everything before the first slash and produced a reference the
    runtime could not answer for.
    """
    return {
        "kind": "local",
        "provider": provider,
        "name": model,
        "model": f"{provider}/{model}",
        "source": source,
        "reason": reason,
        "reachable": True,
        "present": None,
        "leaves_machine": False,
        "privacy": "",
    }


def free_brain(provider: str, model: str, env_key: str, *, reason: str = "") -> dict[str, Any]:
    return {
        "kind": "freekey",
        "provider": provider,
        "name": model,
        "model": f"{provider}/{model}",
        "source": f"{provider} free tier — key from your .env ({env_key})",
        "reason": reason,
        "reachable": True,
        "present": None,
        "leaves_machine": True,
        "privacy": privacy_line(provider),
    }


def keyless_brain(provider: str, model: str, *, reason: str = "", note: str = "") -> dict[str, Any]:
    """A hosted free tier that answers with no key at all.

    It is a real answer and it is not a local one, so the two facts travel
    together: the person is told which endpoint answered and that the text of
    their turn left the machine, in the same breath as the good news.
    """
    entry = catalog.find(f"{provider}/{model}")
    why = f"; {note}" if note else ""
    return {
        "kind": "keyless",
        "provider": provider,
        "name": model,
        "model": f"{provider}/{model}",
        # The room shows ``pickup · source`` under every answer, so the source of
        # a hosted model is the sentence a local-first product owes the person.
        "source": privacy_line(provider),
        "reason": reason or f"{model} answers on {tier_label(provider)} with no key{why}",
        "reachable": True,
        "present": None,
        "leaves_machine": True,
        "privacy": privacy_line(provider),
        "tools": bool(entry.tools) if entry else None,
    }


def tier_label(provider: str) -> str:
    """How a keyless endpoint is named to a person, never which company owns it."""
    preset = get_provider(provider)
    name = str(preset.get("name") or provider)
    return name.split(" (")[0]


def privacy_line(provider: str) -> str:
    """One sentence for the moment a turn stops being local.

    The product promises local-first, so a hosted answer is not something to
    leave implicit: the words of the turn, and anything the tools hand the model,
    travel to that endpoint.
    """
    return f"your words leave this machine for {tier_label(provider)} — install Ollama to keep them local"


def speed_hint(brain: dict[str, Any]) -> str:
    """One line on what would make this brain faster, for the first screen."""
    if brain.get("kind") == "local":
        return "already answering on your machine, with nothing leaving it"
    if brain.get("kind") == "keyless":
        return (
            "install Ollama to answer on your machine with no rate limit, or paste a free Groq key "
            "for a much larger allowance than the anonymous tier gives"
        )
    if brain.get("kind") == "freekey":
        return "install Ollama to answer on your machine, or stay on the free plan you already have"
    return "install Ollama (free, no account) or paste a free-tier key to get a real answer"


def stub_brain(reason: str) -> dict[str, Any]:
    return {
        "kind": "stub",
        "provider": "mock",
        "name": "stub",
        "model": STUB_MODEL,
        "source": "offline stub — no model installed",
        "reachable": False,
        "present": None,
        "reason": reason,
        "skipped": [],
        "leaves_machine": False,
        "privacy": "",
    }


def _probe_named(model: str) -> dict[str, Any]:
    """Honour a named model, reporting reachability instead of assuming it."""
    provider = model.split("/", 1)[0].lower() if "/" in model else "ollama"
    name = model.split("/", 1)[1] if "/" in model else model
    reachable = False
    present = None
    reason = ""
    if provider == "ollama":
        if ollama_serving():
            pulled = ollama_models()
            present = name in pulled or any(p.split(":")[0] == name.split(":")[0] for p in pulled)
            reachable = bool(present)
            if not present:
                reason = f"'{name}' is not pulled (run: ollama pull {name})"
        else:
            reason = "Ollama is not running (run: ollama serve)"
    elif provider in ("lmstudio", "nollama"):
        base = LMSTUDIO_BASE if provider == "lmstudio" else NOLLAMA_BASE
        reachable = bool(_openai_models(base))
        if not reachable:
            reason = f"{provider} is not serving on {base}"
    elif provider == "mock":
        reachable, present = True, True
    elif catalog.is_keyless(provider):
        down = free_tier.down_reason(provider)
        reachable = not down
        reason = down
    else:
        env_key = next((k for p, k, _, _ in FREE_TIER if p == provider), None)
        reachable = bool(env_key and os.environ.get(env_key))
        if not reachable:
            reason = f"{provider} needs {env_key or 'an API key'} in your environment"
    kind = (
        "local" if provider in ("ollama", "lmstudio", "nollama") else ("keyless" if catalog.is_keyless(provider) else "freekey")
    )
    if provider == "mock":
        kind = "stub"
    return {
        "kind": kind,
        "provider": provider,
        "name": name,
        "model": model,
        "source": "set by you (HERMUS_MODEL)",
        "reachable": bool(reachable),
        "present": present,
        "reason": reason,
        "leaves_machine": not catalog.is_local(model),
        "privacy": "" if catalog.is_local(model) else privacy_line(provider),
    }


def _local_refs(st: dict[str, Any]) -> list[str]:
    """Every model each serving local runtime holds, as provider/name references."""
    refs: list[str] = []
    for provider in ("ollama", "lmstudio", "nollama"):
        info = st.get(provider) or {}
        if not info.get("serving"):
            continue
        refs.extend(f"{provider}/{name}" for name in info.get("models") or [] if name)
    return refs


def _hosted_refs(need_tools: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
    """Each keyed free tier's best candidate *this key can reach*, plus the refusals.

    The catalog says which model would be strongest; the probe says which ones
    answer, and which of the person's keys answers. A model refused with HTTP 429
    is recorded as capacity — it is not gone, and it is not a reason to migrate
    anything — and the next candidate down the ranking is offered instead. Every
    refusal names the key's path, so a dead key is one edit away from working.
    """
    refs: list[str] = []
    refused: list[dict[str, Any]] = []
    for provider, _env_key, _url in _FREE_KEYS:
        resolution = key_sources.resolve(provider, need_tools=need_tools)
        if resolution.usable:
            refs.append(resolution.ref)
        refused.extend(resolution.refusals)
    return refs, refused


def local_origin(provider: str, st: dict[str, Any]) -> str:
    """The plain-language origin of a local model, as the room shows it."""
    if provider == "ollama":
        return f"Ollama on your machine ({len((st.get('ollama') or {}).get('models') or [])} model(s) pulled)"
    if provider == "lmstudio":
        return "LM Studio on your machine"
    return "NoLlama on your GPU/NPU"


def _step(rung: str, state: str, *, ref: str = "", why: str = "", provider: str = "") -> dict[str, Any]:
    """One rung of the ladder: what it offered, or why it was passed over."""
    return {"rung": rung, "state": state, "ref": ref, "provider": provider, "why": why}


def _local_absence(st: dict[str, Any]) -> str:
    """Why no model on this machine can answer, in the words its fix needs."""
    if st["ollama"]["serving"]:
        return "Ollama is answering but has no model pulled"
    if st.get("lmstudio", {}).get("serving") or st.get("nollama", {}).get("serving"):
        return "a local runtime is serving but lists no model"
    if st["ollama"]["installed"]:
        return "Ollama is installed but not running"
    return "no local runtime is installed on this machine"


def _measured_pick(refs: list[str], *, need_tools: bool) -> tuple[catalog.Pick, dict[str, Any]]:
    """The strongest of these by measurement, or by the catalog when nothing was measured.

    The recorded model is the incumbent: a challenger has to beat it by more than
    noise before this moves a person onto it. A recorded reference that is not in
    ``refs`` is not a candidate, so it cannot hold the choice hostage.
    """
    incumbent = ""
    try:
        from .models.model_preferences import model_preferences

        incumbent = str(model_preferences.get("default") or "")
    except Exception:  # noqa: BLE001 - no store is no incumbent
        incumbent = ""
    if incumbent and incumbent not in refs:
        incumbent = ""
    picked, decision = model_measurement.pick(refs, need_tools=need_tools, incumbent=incumbent)
    return picked, decision.to_dict()


def _local_rung(st: dict[str, Any], *, need_tools: bool, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rung 1: a model on this machine, the one that measured best first."""
    refs = _local_refs(st)
    if not refs:
        steps.append(_step("local", "skipped", why=_local_absence(st)))
        return None
    picked, decision = _measured_pick(refs, need_tools=need_tools)
    for entry, why in picked.skipped:
        steps.append(_step("local", "skipped", ref=entry.ref, provider=catalog.provider_of(entry.ref), why=why))
    entry = picked.choice
    if entry is None or (need_tools and not entry.tools):
        # A model that answers prose cannot drive this product's tools, so this
        # rung offers nothing for a tool-driven turn and the next one gets it.
        if entry is not None:
            steps.append(
                _step(
                    "local",
                    "skipped",
                    ref=entry.ref,
                    provider=entry.provider,
                    why=f"no function calling, and a hosted free tier below can call tools instead ({entry.name})",
                )
            )
        else:
            steps.append(_step("local", "skipped", why="nothing pulled on this machine can call tools"))
        return None
    if decision.get("basis") == "measured":
        reason = str(decision.get("reason") or "")
    else:
        reason = catalog.comparison_reason(entry, list(picked.runners))
        if picked.note:
            reason = f"{reason}; {picked.note}"
    brain = local_brain(entry.name, local_origin(entry.provider, st), provider=entry.provider, reason=reason)
    brain["source"] = f"{brain['source']}; {catalog.short_reason(entry, list(picked.runners))}"
    brain["evidence"] = MEASURED_LOCAL
    brain["measurement"] = decision
    brain["weak_at_tools"] = bool(decision.get("weak_at_tools"))
    steps.append(
        _step(
            "local",
            "chose",
            ref=entry.ref,
            provider=entry.provider,
            why=f"answering locally: {reason}; {MEASURED_LOCAL}",
        )
    )
    return brain


def _keyless_rung(*, need_tools: bool, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rung 2: a hosted free tier that answers with no key at all.

    Nothing is spent to decide this rung, because a probe would cost one of two
    requests a minute, so the endpoint's own health cache decides instead: a tier
    that refused the last request is skipped with that refusal carried into the
    reason rather than offered again and failed again.
    """
    for provider in catalog.KEYLESS_PROVIDERS:
        down = free_tier.down_reason(provider)
        if down:
            steps.append(_step("keyless", "skipped", ref=provider, provider=provider, why=down))
            continue
        picked = catalog.pick(provider, need_tools=need_tools)
        entry = picked.choice
        if entry is None:
            steps.append(_step("keyless", "skipped", ref=provider, provider=provider, why=picked.note or "no model is listed"))
            continue
        for skipped_entry, why in picked.skipped:
            steps.append(_step("keyless", "skipped", ref=skipped_entry.ref, provider=provider, why=why))
        brain = keyless_brain(provider, entry.name, note=picked.note)
        brain["evidence"] = MEASURED_HOSTED
        steps.append(_step("keyless", "chose", ref=entry.ref, provider=provider, why=f"{brain['reason']}; {MEASURED_HOSTED}"))
        return brain
    return None


def _keyed_rung(*, need_tools: bool, steps: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Rung 3: a free plan the person pasted a key for, probed per candidate."""
    hosted, refused = _hosted_refs(need_tools=need_tools)
    if not hosted:
        missing = ", ".join(env for _provider, env, _url in _FREE_KEYS)
        steps.append(_step("keyed", "skipped", why=f"no free-tier key is configured anywhere this app reads keys ({missing})"))
        return None, refused
    picked, decision = _measured_pick(hosted, need_tools=need_tools)
    entry = picked.choice
    if entry is None:
        steps.append(_step("keyed", "skipped", why="no keyed model answered when it was tried"))
        return None, refused
    if decision.get("basis") == "measured":
        reason = str(decision.get("reason") or "")
    else:
        reason = catalog.comparison_reason(entry, list(picked.runners))
        if picked.note:
            reason = f"{reason}; {picked.note}"
    brain = free_brain(entry.provider, entry.name, catalog.FREE_KEYS[entry.provider], reason=reason)
    brain["source"] = f"{brain['source']}; {catalog.short_reason(entry, list(picked.runners))}"
    brain["measurement"] = decision
    brain["weak_at_tools"] = bool(decision.get("weak_at_tools"))
    steps.append(_step("keyed", "chose", ref=entry.ref, provider=entry.provider, why=reason))
    for skipped_entry, why in picked.skipped:
        steps.append(_step("keyed", "skipped", ref=skipped_entry.ref, provider=entry.provider, why=why))
    return brain, refused


def resolve_brain(
    preferred: str | None = None, *, status: dict[str, Any] | None = None, need_tools: bool = True
) -> dict[str, Any]:
    """Resolve once down the ladder: local, keyless, keyed, then the honest stub.

    A named model (``HERMUS_MODEL``, or the one onboarding recorded) is honoured
    as named and probed for reachability instead, because a person who pinned a
    model is not asking to have it reconsidered.

    The result always carries ``ladder`` — every rung in order, which one chose
    and why the others were passed over — and ``skipped`` in the same
    ``{ref, why}`` shape the keyed tiers already reported.
    """
    named = (preferred or os.environ.get("HERMUS_MODEL") or "").strip()
    if named:
        probed = _probe_named(named)
        tier = catalog.tier_of(named)
        probed["tier"] = tier
        probed["ladder"] = [
            _step(
                tier,
                "chose" if probed["kind"] != "stub" else "skipped",
                ref=named,
                why=probed.get("reason") or "chosen by you",
            )
        ]
        return probed

    st = status or local_status()
    steps: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    brain = _local_rung(st, need_tools=need_tools, steps=steps)
    if brain is None:
        brain = _keyless_rung(need_tools=need_tools, steps=steps)
    if brain is None:
        brain, refused = _keyed_rung(need_tools=need_tools, steps=steps)
    if brain is None:
        fell_through = "; ".join(f"{step['rung']}: {step['why']}" for step in steps if step["why"]) or "nothing was reachable"
        brain = stub_brain(f"no local model and no free-tier key ({fell_through})")

    brain["ladder"] = steps
    brain["tier"] = catalog.tier_of(brain["model"]) if brain["kind"] != "stub" else "stub"
    skips = [{"ref": step["ref"], "why": step["why"]} for step in steps if step["state"] == "skipped" and step["ref"]]
    brain["skipped"] = refused + skips
    return brain


def _recorded_gone(recorded: str, st: dict[str, Any]) -> str:
    """Why a recorded model cannot answer any more, or '' when it still can.

    For a hosted model the answer comes from the provider, not from the catalog: a
    429 is capacity (the model stays), and only a 404 or a key that no longer
    exists counts as gone. Treating a rate limit as a retirement would migrate a
    person off a model that works the next minute.
    """
    provider, name = catalog.provider_of(recorded), catalog.name_of(recorded)
    if provider == "ollama":
        info = st.get("ollama") or {}
        if not info.get("serving"):
            return "Ollama is not running"
        pulled = info.get("models") or []
        if name not in pulled:
            return f"it is no longer pulled ({len(pulled)} other model(s) are)"
        return ""
    if provider in ("lmstudio", "nollama"):
        info = st.get(provider) or {}
        if not info.get("serving"):
            return f"{provider} is not serving"
        return "" if name in (info.get("models") or []) else "the runtime no longer lists it"
    if provider not in catalog.FREE_KEYS:
        return ""
    resolution = key_sources.resolve(provider)
    if not resolution.key:
        # No key, and no attempt either: nothing is configured. A key that exists
        # but answered nothing is a different story, and only a refusal (401/403 or
        # 404) counts as disappearance — a 429 means come back in a minute.
        if not resolution.refusals:
            return f"{catalog.FREE_KEYS[provider]} is not configured anywhere this app reads keys"
        states = {str(row.get("state") or "") for row in resolution.refusals}
        if model_health.DENIED in states or model_health.GONE in states:
            first = next(row for row in resolution.refusals if row.get("state") in (model_health.DENIED, model_health.GONE))
            return str(first.get("why") or "")
        return ""
    state = model_health.probe(provider, resolution.key, name)
    where = f" (key from {resolution.source})" if resolution.source else ""
    if state.state == model_health.GONE:
        return f"{state.detail or 'the provider does not serve it to this key'}{where}"
    if state.state == model_health.DENIED:
        return f"{state.detail or 'the key was refused'}{where}"
    return ""


def upgrade_offer(
    *, recorded: str | None = None, status: dict[str, Any] | None = None, need_tools: bool = True
) -> dict[str, Any]:
    """The recorded model against the strongest one reachable now.

    A model whose key or runtime no longer serves it, one the provider answers with
    HTTP 404, and one that is simply weaker than something reachable all count as
    below best. A 429 does not: that is capacity, and the model stays. This decides
    and explains; :func:`apply_resolved_default` performs the change at startup.
    """
    st = status or local_status()
    best = resolve_brain(status=st, need_tools=need_tools)
    if best["kind"] == "stub":
        return {
            "needed": False,
            "from": recorded,
            "to": None,
            "gone": "",
            "reason": f"nothing better is reachable: {best['reason']}",
            "best": best,
        }
    if not recorded:
        return {
            "needed": True,
            "from": None,
            "to": best["model"],
            "gone": "",
            "reason": f"no model is recorded; {best['model']} is the strongest reachable one",
            "best": best,
        }

    old = catalog.describe(recorded)
    new = catalog.describe(best["model"])
    gone = _recorded_gone(recorded, st)
    weaker = catalog.strength(new) > catalog.strength(old)
    # A measured choice is evidence where the ranking is a proxy: if this machine
    # has tool-use numbers and they picked another model, the recorded model
    # gives way even when it is larger. The measurement's own margin rule has
    # already kept a recorded model that still works.
    decided = best.get("measurement") or {}
    measured = decided.get("basis") == "measured" and bool(best["model"]) and best["model"] != recorded
    # A recorded hosted model gives way to a model on this machine even when the
    # hosted one is larger: the ladder puts local first for reasons the ranking
    # does not price — nothing leaves the machine, and there is no allowance to
    # run out of.
    old_tier, new_tier = catalog.tier_of(recorded), catalog.tier_of(best["model"])
    nearer = catalog.TIERS.index(new_tier) < catalog.TIERS.index(old_tier)
    if gone:
        reason = f"{recorded} cannot answer any more ({gone}); {best['model']} is the strongest reachable"
    elif measured:
        reason = f"measured on this machine, {decided.get('reason') or best['model'] + ' is the better tool-caller'}"
    elif nearer:
        reason = f"{recorded} answers on {old_tier} and {best['model']} answers on {new_tier}, which the ladder puts first"
    elif weaker:
        reason = catalog.upgrade_reason(old, new, gone=gone)
    else:
        reason = f"{recorded} is still at least as strong as {best['model']}, so it stays"
    move = bool(gone or weaker or nearer or measured)
    return {
        "needed": move,
        "from": recorded,
        "to": best["model"] if move else recorded,
        "gone": gone,
        "reason": reason,
        "from_strength": round(catalog.strength(old), 2),
        "to_strength": round(catalog.strength(new), 2),
        "best": best,
    }


def pickup_line(brain: dict[str, Any]) -> str:
    """The one sentence first run says out loud, never a silent default.

    A keyless answer names its endpoint in the same sentence, because "it worked"
    and "it worked by sending your words to a stranger's machine" are different
    facts and the person is owed both at once.
    """
    if brain["kind"] == "local":
        return f"answering with {brain['name']} on your machine"
    if brain["kind"] == "keyless":
        return f"answering with {brain['name']} from the {tier_label(brain['provider'])} free tier — no key, no account"
    if brain["kind"] == "freekey":
        return f"answering with {brain['name']} from the {brain['provider']} free tier"
    return "answering with the offline stub — no model installed"


def fix_command(brain: dict[str, Any] | None = None, *, status: dict[str, Any] | None = None) -> str:
    """The one command that turns this brain into a real one."""
    st = status or local_status()
    if brain and brain.get("kind") == "freekey":
        return f"add {brain['provider'].upper()}_API_KEY to .env and restart the gateway"
    if not st["ollama"]["serving"]:
        if st["ollama"]["installed"]:
            return "ollama serve"
        if st.get("lmstudio", {}).get("installed"):
            return "open LM Studio, load a model and start its local server (Developer → Start Server)"
        return "install Ollama from https://ollama.com/download, then run: ollama pull " + SMALLEST_LOCAL_MODEL
    return f"ollama pull {SMALLEST_LOCAL_MODEL}"


def apply_resolved_default(*, status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Point ``config.model`` at the strongest free model, without overriding a person.

    Called once at gateway startup, and it handles two cases:

    * the configured model is still the untouched shipment default (or empty) —
      adopt the strongest reachable model;
    * the configured model is the one onboarding recorded and it is now below the
      strongest reachable one — its runtime lost it, the provider no longer serves
      it to this key, or a stronger free model answered when probed.

    Either way the choice is written into the onboarding state, because a model
    the gateway is using but the state does not know about reads as an unfinished
    install on the first screen. Onboarding does not run again and the person's
    name and permission choice are untouched.

    A model pinned with ``HERMUS_MODEL``, and one that is already the best
    available, are left alone.
    """
    from . import onboarding
    from .config import config

    current = str(getattr(config, "model", "") or "").strip()
    stored = (onboarding.load().get("brain") or {}).get("model")
    pinned = (os.environ.get("HERMUS_MODEL") or "").strip()
    if pinned and pinned != stored:
        return {"applied": False, "model": current, "reason": "HERMUS_MODEL pins this model"}

    replacing_shipped = not current or current in SHIPPED_DEFAULTS
    replacing_recorded = bool(stored) and current == stored
    if not (replacing_shipped or replacing_recorded):
        return {"applied": False, "model": current, "reason": "a model is already chosen"}

    offer = upgrade_offer(recorded=stored, status=status)
    best = offer.get("best") or {}
    if best.get("kind") == "stub":
        return {"applied": False, "model": current, "reason": best.get("reason") or "no free model is reachable"}
    target = best["model"]
    if current == target:
        return {"applied": False, "model": current, "reason": "already the strongest reachable free model"}

    config.model = target
    config.ollama_default_model = best["name"]
    picked = {
        "applied": True,
        "model": target,
        "from": stored or current or None,
        "source": best["source"],
        "reason": offer.get("reason") or best["reason"],
        "model_reason": best["reason"],
        "upgraded": bool(offer.get("needed")),
    }
    # Record the choice when there is something to record against: a model the
    # person already had, or a workspace that has been used before. A genuine
    # first run keeps its welcome screen, which is where the model gets chosen.
    if stored or onboarding.install_has_history():
        if onboarding.record_upgrade(onboarding.load(), best, previous=stored, reason=picked["reason"]):
            picked["recorded"] = True
    return picked


def warm(model: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Load the model into memory so the person's first keystroke is not the wait.

    Free and local: an empty-prompt generation, which Ollama answers by loading
    the weights. Never fatal — a cold start only makes the next call slower.
    """
    provider = model.split("/", 1)[0].lower() if "/" in model else "ollama"
    name = model.split("/", 1)[1] if "/" in model else model
    if provider != "ollama":
        return {"warmed": False, "reason": f"warm-up only applies to Ollama (got {provider})"}
    started = time.time()
    body = json.dumps({"model": name, "prompt": "", "keep_alive": "30m"}).encode()
    req = urllib.request.Request(f"{OLLAMA_BASE}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed localhost URL
            resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"warmed": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"warmed": True, "seconds": round(time.time() - started, 2)}
