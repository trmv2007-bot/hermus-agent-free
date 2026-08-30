"""Model capability negotiation — know what a model can do *before* starting.

The free default (``ollama/llama3.1:8b``) is only a *runtime dependency*: it
works when Ollama is installed, the weights are pulled, the context window is
big enough and the model actually honours the tool-calling contract. None of
that was checked before a mission started, so failures surfaced mid-run as
``no_evidence_of_work`` or as a silent mock answer.

This module answers, up front and explicitly::

    model supports tools?      YES / NO / UNKNOWN
    vision?                    YES / NO / UNKNOWN
    long context?              YES / NO / UNKNOWN
    structured outputs?        YES / NO / UNKNOWN
    streaming?                 YES / NO / UNKNOWN
    computer control?          YES / NO / UNKNOWN

and can pick a compatible model automatically (``select_compatible_model``)
instead of failing halfway through a mission.

Sources, in order of trust:

1. provider presets (``core.providers.PROVIDER_PRESETS`` → ``supports_tools``);
2. a curated matrix of well-known model families (context window, vision, …);
3. live probing — ``GET /v1/models`` / ``GET /api/tags`` for Ollama — to learn
   whether the endpoint is reachable and the model is actually present.

Probing is off by default in tests (``HERMUS_CAPABILITY_PROBE=0``) and always
best-effort: an unreachable endpoint yields ``unknown``, never a crash.
"""
from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import config

# --------------------------------------------------------------------- verdicts
YES = "yes"
NO = "no"
UNKNOWN = "unknown"

#: capability → human label
CAPABILITIES = (
    "tools",
    "vision",
    "long_context",
    "structured_outputs",
    "streaming",
    "computer_control",
)

#: what a capability means in one line (surfaced in /models/capabilities)
CAPABILITY_HELP = {
    "tools": "function/tool calling — required for every agent + mission run",
    "vision": "image understanding (screenshots, scans, charts)",
    "long_context": ">= 32k tokens of context (large codebases, long missions)",
    "structured_outputs": "JSON / schema-constrained responses",
    "streaming": "token streaming (SSE typewriter output)",
    "computer_control": "screen/mouse/keyboard control (computer-use missions)",
}

#: minimum context size we call "long"
LONG_CONTEXT_TOKENS = 32_000


@dataclass
class CapabilityReport:
    """Verdicts for one model (``model`` = ``provider/name``)."""

    model: str
    provider: str = ""
    name: str = ""
    reachable: Optional[bool] = None
    present: Optional[bool] = None
    capabilities: dict[str, str] = field(default_factory=dict)
    context_tokens: Optional[int] = None
    notes: list[str] = field(default_factory=list)

    # -- queries --------------------------------------------------------
    def supports(self, capability: str) -> str:
        return self.capabilities.get(capability, UNKNOWN)

    def has(self, capability: str) -> bool:
        return self.capabilities.get(capability) == YES

    def missing(self, required: Optional[list[str]] = None) -> list[str]:
        """Capabilities that are explicitly *not* available."""
        wanted = list(required or CAPABILITIES)
        return [c for c in wanted if self.capabilities.get(c) == NO]

    def unknown(self, required: Optional[list[str]] = None) -> list[str]:
        wanted = list(required or CAPABILITIES)
        return [c for c in wanted if self.capabilities.get(c, UNKNOWN) == UNKNOWN]

    def ok_for(self, required: Optional[list[str]] = None) -> bool:
        """True when no *required* capability is known-missing."""
        return not self.missing(required)

    def warnings(self, required: Optional[list[str]] = None) -> list[str]:
        out: list[str] = []
        for cap in self.missing(required):
            out.append(f"{self.model} does not support {cap} ({CAPABILITY_HELP.get(cap, '')})".strip())
        if self.present is False:
            out.append(f"model '{self.name}' is not available on provider '{self.provider}'")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "name": self.name,
            "reachable": self.reachable,
            "present": self.present,
            "capabilities": dict(self.capabilities),
            "context_tokens": self.context_tokens,
            "notes": list(self.notes),
            "missing": self.missing(),
            "unknown": self.unknown(),
        }


# ---------------------------------------------------------------- known families
#: curated facts for model families we know about (regex → facts)
_FAMILY_RULES: list[tuple[str, dict[str, Any]]] = [
    # local / open weights
    (r"^llama3\.1:8b|^llama-3\.1-8b", {"tools": YES, "vision": NO, "long_context": YES,
                                       "structured_outputs": UNKNOWN, "streaming": YES,
                                       "computer_control": NO, "context": 131072}),
    (r"^llama3\.2:.*(11b|90b)", {"tools": YES, "vision": YES, "long_context": YES,
                                 "structured_outputs": UNKNOWN, "streaming": YES,
                                 "computer_control": NO, "context": 131072}),
    (r"^llama3\.1:70b|^llama-3\.1-70b", {"tools": YES, "vision": NO, "long_context": YES,
                                         "structured_outputs": UNKNOWN, "streaming": YES,
                                         "computer_control": NO, "context": 131072}),
    (r"^llama3|^llama-3", {"tools": UNKNOWN, "vision": NO, "long_context": UNKNOWN,
                           "structured_outputs": UNKNOWN, "streaming": YES,
                           "computer_control": NO, "context": 8192}),
    (r"^qwen2\.5", {"tools": YES, "vision": UNKNOWN, "long_context": YES,
                    "structured_outputs": YES, "streaming": YES,
                    "computer_control": NO, "context": 32768}),
    (r"^mistral|^devstral|^codestral", {"tools": YES, "vision": NO, "long_context": YES,
                                        "structured_outputs": YES, "streaming": YES,
                                        "computer_control": NO, "context": 32768}),
    (r"^deepseek", {"tools": YES, "vision": NO, "long_context": YES,
                    "structured_outputs": UNKNOWN, "streaming": YES,
                    "computer_control": NO, "context": 65536}),
    (r"^gpt-4o", {"tools": YES, "vision": YES, "long_context": YES,
                  "structured_outputs": YES, "streaming": YES,
                  "computer_control": NO, "context": 128000}),
    (r"^gpt-4\.1|^gpt-5", {"tools": YES, "vision": YES, "long_context": YES,
                           "structured_outputs": YES, "streaming": YES,
                           "computer_control": UNKNOWN, "context": 128000}),
    (r"^gpt-oss", {"tools": YES, "vision": NO, "long_context": YES,
                   "structured_outputs": YES, "streaming": YES,
                   "computer_control": NO, "context": 131072}),
    (r"^claude", {"tools": YES, "vision": YES, "long_context": YES,
                  "structured_outputs": UNKNOWN, "streaming": YES,
                  "computer_control": YES, "context": 200000}),
    (r"^gemini", {"tools": YES, "vision": YES, "long_context": YES,
                  "structured_outputs": YES, "streaming": YES,
                  "computer_control": NO, "context": 128000}),
    (r"^nemotron", {"tools": YES, "vision": UNKNOWN, "long_context": YES,
                    "structured_outputs": UNKNOWN, "streaming": YES,
                    "computer_control": NO, "context": 131072}),
    (r"^phi", {"tools": UNKNOWN, "vision": NO, "long_context": UNKNOWN,
               "structured_outputs": UNKNOWN, "streaming": YES,
               "computer_control": NO, "context": 4096}),
    (r"^mock", {"tools": NO, "vision": NO, "long_context": NO,
                "structured_outputs": NO, "streaming": NO,
                "computer_control": NO, "context": 0}),
]

#: providers whose models we treat as tool-capable unless stated otherwise
_TOOL_CAPABLE_PROVIDERS = {
    "openai", "groq", "openrouter", "together", "fireworks", "deepseek",
    "mistral", "codestral", "nvidia", "ollama", "lmstudio", "vllm", "azure",
    "cohere", "cerebras", "sambanova", "xai",
}

#: providers known to reject tool calls (check preset ``supports_tools`` first)
_NON_TOOL_PROVIDERS = {"hf", "huggingface", "mock"}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _split_model(model: str) -> tuple[str, str]:
    s = str(model or "").strip()
    if not s:
        return "", ""
    if "/" in s:
        provider, name = s.split("/", 1)
        return provider.lower(), name
    return "ollama", s


def _provider_preset(provider: str) -> dict[str, Any]:
    try:
        from .providers import get_provider

        return get_provider(provider) or {}
    except Exception:
        return {}


def _probe_http(url: str, timeout: float = 2.0) -> Optional[str]:
    """GET a URL; returns the body or ``None``. Never raises."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def probe_ollama(model_name: str, *, timeout: float = 2.0) -> tuple[Optional[bool], Optional[bool]]:
    """Return ``(reachable, model_present)`` for a local Ollama server."""
    base = (
        os.getenv("OLLAMA_HOST")
        or str(getattr(config, "ollama_base_url", "") or "")
        or "http://localhost:11434"
    ).rstrip("/")
    body = _probe_http(f"{base}/api/tags", timeout=timeout)
    if body is None:
        return False, None
    names: set[str] = set()
    try:  # tiny JSON parse without importing json plumbing helpers
        import json

        data = json.loads(body)
        for item in (data or {}).get("models", []) or []:
            if isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))
    except Exception:
        return True, None
    if not model_name:
        return True, None
    bare = model_name.split(":")[0]
    present = any(n == model_name or n.startswith(f"{bare}:") or n == bare for n in names)
    return True, present


def negotiate(model: str, *, probe: Optional[bool] = None) -> CapabilityReport:
    """Build the capability report for ``model`` (``provider/name``)."""
    model = str(model or "").strip() or str(getattr(config, "model", "") or "ollama/llama3.1:8b")
    provider, name = _split_model(model)
    preset = _provider_preset(provider)
    report = CapabilityReport(model=model, provider=provider, name=name)

    # 1. provider-level facts
    caps: dict[str, str] = {c: UNKNOWN for c in CAPABILITIES}
    if preset.get("supports_tools") is True:
        caps["tools"] = YES
    elif preset.get("supports_tools") is False or provider in _NON_TOOL_PROVIDERS:
        caps["tools"] = NO
    elif provider in _TOOL_CAPABLE_PROVIDERS:
        caps["tools"] = YES
    caps["streaming"] = YES if preset.get("supports_tools", True) else UNKNOWN
    caps["structured_outputs"] = YES if provider in (
        "openai", "groq", "openrouter", "mistral", "fireworks", "together", "gemini",
    ) else UNKNOWN
    caps["computer_control"] = YES if provider in ("anthropic", "openai") else (
        NO if provider == "ollama" else UNKNOWN
    )

    # 2. model-family facts (more specific than the provider)
    low = name.lower()
    for pattern, facts in _FAMILY_RULES:
        if re.match(pattern, low):
            for cap in CAPABILITIES:
                value = facts.get(cap)
                if value:
                    caps[cap] = value
            if facts.get("context"):
                report.context_tokens = int(facts["context"])
            report.notes.append(f"matched known family '{pattern}'")
            break
    else:
        report.notes.append("unknown model family — capability verdicts are provider-level only")

    if re.search(r"(vl|vision|-v$|4o|gemini|claude-3)", low):
        caps["vision"] = YES if caps.get("vision") != NO else NO

    # 3. live probing (best effort, opt-out via env)
    if probe is None:
        probe = _env_flag("HERMUS_CAPABILITY_PROBE", True)
    if probe:
        if provider == "ollama":
            reachable, present = probe_ollama(name)
            report.reachable, report.present = reachable, present
            if reachable is False:
                report.notes.append("Ollama endpoint not reachable — install/start Ollama or pick another provider")
            elif present is False:
                report.notes.append(f"model '{name}' is not pulled locally (run: ollama pull {name})")
        elif provider == "mock":
            report.reachable, report.present = True, True
            report.notes.append("mock provider — no real model is executed")

    # A model we know is missing cannot be relied on for anything.
    if report.present is False and caps.get("tools") == YES:
        caps["tools"] = UNKNOWN
        report.notes.append("tool support downgraded to 'unknown': the model is not present locally")

    # long-context verdict
    if report.context_tokens is None:
        caps["long_context"] = UNKNOWN
    else:
        caps["long_context"] = YES if report.context_tokens >= LONG_CONTEXT_TOKENS else NO

    report.capabilities = caps
    return report


# ------------------------------------------------------------------- selection
#: candidate models to consider when auto-selecting (free-first ordering)
AUTO_SELECT_CANDIDATES: tuple[str, ...] = (
    "ollama/llama3.1:8b",
    "ollama/qwen2.5:7b",
    "ollama/llama3.2:11b",
    "groq/openai/gpt-oss-20b",
    "nvidia/nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "openrouter/openrouter/auto",
    "openai/gpt-4o-mini",
)


def select_compatible_model(
    required: Optional[list[str]] = None,
    *,
    candidates: Optional[list[str]] = None,
    prefer_current: bool = True,
    probe: Optional[bool] = None,
) -> tuple[Optional[str], dict[str, Any]]:
    """Pick the first candidate that satisfies ``required`` capabilities.

    Returns ``(model_or_None, {"reports": [...], "required": [...]})``. The
    current ``config.model`` is tried first (``prefer_current``) so the user's
    explicit choice always wins when it is good enough.
    """
    required = list(required or ["tools"])
    pool: list[str] = []
    if prefer_current:
        pool.append(str(getattr(config, "model", "") or ""))
    # Providers configured only through .env (OpenRouter, Gemini, NVIDIA, ...)
    # must be candidates even when they are not in the static list below and
    # nothing was added through `hermus multikey add`.
    configured_hosted: set[str] = set()
    try:
        from .provider_resolver import discover_runtime_bundles

        env_bundles = discover_runtime_bundles(include_local=False)
        configured_hosted = {b.get("provider") for b in env_bundles if b.get("provider") and not b.get("retired")}
        for b in env_bundles:
            provider = b.get("provider") or ""
            model = b.get("default_model") or ""
            if not provider or not model:
                continue
            if b.get("retired"):
                continue
            if "tools" in required and b.get("supports_tools") is False:
                continue
            cand = f"{provider}/{model}"
            if cand not in pool:
                pool.append(cand)
    except Exception:
        pass
    pool.extend([c for c in (candidates or AUTO_SELECT_CANDIDATES) if c not in pool])

    reports: list[dict[str, Any]] = []
    fallback: Optional[str] = None
    best: Optional[str] = None
    best_unknown: Optional[str] = None
    for idx, cand in enumerate(pool):
        if not cand:
            continue
        rep = negotiate(cand, probe=probe)
        reports.append(rep.to_dict())
        report = rep

        # A known-missing/local-unreachable model can never be the winner.
        if report.missing(required) or report.present is False or report.reachable is False:
            if best_unknown is None and report.unknown(required) and not report.missing(required):
                best_unknown = cand
            continue

        confirmed = all(report.capabilities.get(cap) == YES for cap in required)
        provider, _name = _split_model(cand)
        local_unproven = (
            provider in ("ollama", "lmstudio", "nollama", "vllm")
            and report.reachable is not True
            and bool(configured_hosted)
            and provider not in configured_hosted
        )
        # Prefer confirmed capabilities first, then a known-configured hosted
        # provider over an unproven local runtime, then pool order.
        candidate_key = (1 if local_unproven else 0, 0 if confirmed else 1, idx)
        if best is None or candidate_key < best[0]:
            best = (candidate_key, cand)
        if fallback is None and report.unknown(required) and not report.missing(required):
            fallback = cand

    if best is not None:
        return best[1], {"required": required, "reports": reports,
                         "selected": best[1], "reason": "preferred capability-compatible model"}
    return (best_unknown or fallback, {"required": required, "reports": reports,
                                       "selected": best_unknown or fallback,
                                       "reason": "no model with confirmed capabilities; using best unknown"})


def mission_capability_gate(
    model: str,
    *,
    needs_computer: bool = False,
    needs_vision: bool = False,
    auto_select: Optional[bool] = None,
) -> dict[str, Any]:
    """Pre-flight check used by the mission runtime and the gateway.

    Returns a dict with ``report``, ``warnings``, ``blocked`` and — when auto
    selection is enabled and the model is unusable — ``recommended_model``.
    """
    required = ["tools"]
    if needs_computer:
        required.append("computer_control")
    if needs_vision:
        required.append("vision")

    report = negotiate(model)
    warnings = report.warnings(required)
    result: dict[str, Any] = {
        "model": model,
        "report": report.to_dict(),
        "required": required,
        "warnings": warnings,
        "blocked": bool(report.missing(required)),
    }
    if result["blocked"] or report.present is False:
        if auto_select is None:
            auto_select = _env_flag("HERMUS_AUTO_SELECT_MODEL", True)
        if auto_select:
            pick, info = select_compatible_model(required)
            result["recommended_model"] = pick
            result["selection"] = info
    return result
