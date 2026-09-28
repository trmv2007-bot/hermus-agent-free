"""Where a vision request goes, decided by what the local model can actually do.

The gap this closes
-------------------
HERMUS could capture the screen and read files, and had no model of what was
happening on the PC. It could see pixels but not understand them, which makes
vision close to useless: a screen capture plus a 4B model that cannot read the
capture is a more expensive way of getting nothing.

The earlier decision was "sees -> local spark-4b". This file exists because
that decision was made without measuring, and measurement says it is wrong on
this machine. The numbers are below, in the place the next person will read
them, the way ``core/confidence.py`` documents its thresholds.

What was measured on this box (RTX 3050 8GB, i7-12700F, 16GB)
-------------------------------------------------------------
``GET /api/show`` for every locally installed model reports its capabilities
directly, and none of them include ``vision``:

    qwen3:4b                  -> [completion, tools, thinking]
    spark-x2.5-4b-q4:latest   -> [tools, thinking, completion]
    SparkLLM/Spark-X2.5-4B    -> [tools, thinking, completion]

Sending each one a real PNG to Ollama's generate endpoint is not a bad answer,
it is a refusal. All 18 probes (2 models x 9 images) returned HTTP 400:

    "Multimodal data provided, but model does not support
     multimodal requests."

So the local tier scores 0/9 on reading a screen, and it does not score badly,
it does not run at all. A 4B text model is not a weak vision model on this
box; it is not a vision model. The two are different, and "4B is bad at
vision" is the wrong reason to reject local routing when the real reason is
that the weight file has no vision tower in it.

The default the code shipped with, ``llava:7b``, is not installed either.
``vision_analyze`` on a real screenshot returns:

    {"success": false, "error": "Model llava:7b not found. Pull with:
     ollama pull llava:7b (free)"}

which ``core/computer/video_analyzer.py`` also hardcodes as its default
``vision_model``. So every screen-understanding path in the repo - screen_watch,
screen_verify, screen_analyze, screen_understand - was wired to a model that
does not exist on this machine.

The hosted tier, same nine probes, same scorer
----------------------------------------------
``gemini-2.5-flash`` reads them: 9/9, 2.5-7s per call, with thinking disabled.
The first run scored 5/9 and that number was wrong in two separate ways, both
of which are worth recording because either one would have produced a
confidently wrong routing decision:

* ``maxOutputTokens=48`` was consumed entirely by thinking tokens
  (``thoughtsTokenCount=45``) and Gemini returned ``finishReason=MAX_TOKENS``
  with no text part at all. That is a harness bug that scores as a vision
  failure. Thinking is now off and the budget is 512.
* Gemini answered "NOTEP" for a title bar reading NOTEPAD. The substring
  scorer called that a miss. It is a correct read of the title bar, and
  counting it as an error is how a benchmark ends up justifying a routing
  change for the wrong reason.

Free tiers are also flaky: 4 of 9 probes hit HTTP 503 or a read timeout on a
single pass, so the probe retries. Provider availability is not a
visual-accuracy measurement.

The decision, and what it costs
------------------------------
Vision goes to the strongest tier, and the local tier is never asked to see.
This is a real trade: it spends a hosted call and puts an image on the wire
where the free-and-private design would not. That cost is worth paying
because the alternative is not a cheaper answer, it is no answer at all.

What would change the decision
------------------------------
A genuinely multimodal local model would. The recommendation below is
**researched, not guessed and not installed** - an earlier attempt to pull a
model by a guessed name failed outright, and a model that is named from a
vendor page rather than measured on this box is exactly the kind of number that
rots. The next step is to pull one and re-run the benchmark in
``scripts/measure_local_vision.py`` on this hardware, then rewrite this file
with the result.

**Recommended first candidate: ``qwen3-vl:4b``** (~3.3 GB). Cross-checked
across three independent sources for the 8 GB VRAM tier: an Ollama VRAM/size
guide that names it the sweet spot for 8 GB ("3.3GB, 256K context, leaves
roughly 4GB of headroom on an 8GB card"), a model-selection comparison
describing Qwen3-VL as the current strongest open vision family on Ollama and
the best performer for OCR, charts and screenshots, and the Ollama library page
itself (6.1M downloads) for the family's leading OCR and UI-understanding
claims. Budget the weights *plus* 1-2 GB for the vision projector, image tokens
and KV cache; that overhead is real and is what makes an 8 GB card a constraint
rather than a number.

Two specific warnings from that research, both relevant to this repo:

* **``llava:7b`` - the model this codebase already defaults to - is the wrong
  default now.** It has not been updated in roughly two years, carries a 32K
  context window against Qwen3-VL's 256K, and one guide explicitly advises
  against it at this VRAM tier in favour of ``qwen3-vl:4b`` (smaller download,
  larger context). So the fix is not "pull llava and route to it"; it is to
  change the default at the same time.
* **A sub-4B general VLM should not be trusted to read small or precise text.**
  The same guidance notes that dedicated small OCR models exist and win at it,
  and that Ollama's own moondream page warns it "may generate inaccurate
  statements". This is precisely the workload ``screen_verify`` and
  ``screen_understand`` perform, so a small VLM that passes a captioning
  benchmark is not evidence that it can verify a dialog on screen.

Do not copy the numbers above into a new comment and move on. Measure on the
box you are on.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

__all__ = [
    "VisionRoute",
    "TierChoice",
    "classify_vision_tools",
    "needs_vision",
    "plan_vision_route",
    "local_vision_support",
]


# --------------------------------------------------------------------------
# What counts as a vision request
# --------------------------------------------------------------------------
# The trap, measured on this repo
# ------------------------------
# The registry has 197 tools. A naive substring classifier over tool names for
# "vision|screen|capture|window|image" matches 17, and most of those 17 are
# wrong in a way that matters:
#
#   browser_screenshot, screen_record_start/stop/status/save,
#   android_get_screen, computer_action, computer_capability
#
# None of those need a model that can see. They take pixels, which is a
# different problem from reading them, and they work perfectly well on a box
# with no vision model at all. Routing them away from the local tier would
# break capture to "fix" understanding.
#
# Matching on descriptions is worse. "semantic" and "image" pull in
# embeddings_add, embeddings_search, memory2_recall, memory_ingest and nine
# other tools that have nothing to do with looking at a screen.
#
# So the classifier below is an explicit allowlist of the tools that actually
# consume an image, taken from the live registry rather than from a pattern.
# It is written out in full because the registry is a closed set of 197 names
# and a hand-maintained list is auditable in a way a heuristic is not. The
# unknown case is handled explicitly: an unrecognised tool is NOT vision, and
# ``classify_vision_tools`` reports it so a new vision tool is visible as a
# gap rather than silently misrouted.
VISION_TOOLS: frozenset[str] = frozenset(
    {
        # Reads an image and returns words about it. These are the ones that
        # need a model with a vision tower.
        "vision_analyze",
        "screen_analyze",
        "screen_understand",
        "screen_verify",
        "screen_watch",
    }
)

#: Capture-only tools. Named explicitly so the two sets can never be confused,
#: and asserted against the live registry in the tests. These do not need a
#: vision model and must keep working when none is installed.
CAPTURE_TOOLS: frozenset[str] = frozenset(
    {
        "browser_screenshot",
        "screen_record_start",
        "screen_record_stop",
        "screen_record_status",
        "screen_record_save",
        "screen_get_recent",
        "screen_action_before",
        "screen_action_after",
        "android_get_screen",
        "computer_action",
        "computer_capability",
        "vision_available_models",
    }
)

#: Asking in words. "look at my screen" means someone wants an image
#: *understood*, not captured - a request for a screenshot can be satisfied by
#: the capture tools alone, with no model involved.
_VISION_PHRASES = (
    "what am i looking at",
    "what's on my screen",
    "whats on my screen",
    "look at my screen",
    "look at the screen",
    "read my screen",
    "read the screen",
    "read the text",
    "what does this say",
    "what does it say",
    "on screen",
    "look at this",
    "analyze this",
    "analyse this",
    "describe this",
    "what do you see",
    "can you see",
    "screenshot",
    "screen shot",
    "take a picture",
    "my screen",
    "the screen",
)


def classify_vision_tools(tool_names: Iterable[str]) -> dict[str, list[str]]:
    """Split the live registry into what needs a vision model and what does not.

    Returns ``{"vision": [...], "capture": [...], "unclassified": [...]}``.

    ``unclassified`` is the important part: it is every registry tool that
    looks vision-ish by substring but is in neither set. It is the alarm for
    "a new vision tool was added and nobody routed it". A tool that is neither
    in the allowlist nor looks vision-ish is simply not vision and does not
    appear at all.
    """
    names = [str(n) for n in tool_names or []]
    vision = sorted(n for n in names if n in VISION_TOOLS)
    capture = sorted(n for n in names if n in CAPTURE_TOOLS)
    _visionish = re.compile(r"vision|screen|capture|screenshot|image|window|look|see|display|monitor", re.I)
    claimed = set(vision) | set(capture)
    unclassified = sorted(n for n in names if n not in claimed and _visionish.search(n))
    return {"vision": vision, "capture": capture, "unclassified": unclassified}


def needs_vision(text: str, tools: Iterable[str] = ()) -> bool:
    """Whether this turn needs a model that can see.

    True on an explicit vision tool in the offer, or on the user asking in
    words. The phrase list is checked against the whole lowercased text, which
    is right for a short voice command ("what am I looking at") and wrong for
    a long pasted document - so a long text only counts when it also offers a
    vision tool. A 5000-character paste containing the word "screenshot" is
    not a request to look at a screen.
    """
    tool_names = {str(t) for t in tools or []}
    if tool_names & VISION_TOOLS:
        return True
    low = str(text or "").lower()
    if len(low) <= 400 and any(phrase in low for phrase in _VISION_PHRASES):
        return True
    return False


# --------------------------------------------------------------------------
# Capability probing
# --------------------------------------------------------------------------
#: Ollama's own capability list, not a guess. ``/api/show`` reports it per
#: model, which is why the local tier does not have to be taken on trust.
_LOCAL_CAPABILITY_TTL_S = 300.0
_local_capability_cache: dict[str, Any] = {"at": 0.0, "value": None}


def local_vision_support(base_url: str = "http://localhost:11434", *, ttl_s: float | None = None) -> dict[str, Any]:
    """Which local models, if any, can actually accept an image.

    Reads ``/api/tags`` and then ``/api/show`` per model, and reports the
    ``vision`` capability Ollama itself advertises. Cached, because this runs
    on the routing path and a plan-then-vision turn would otherwise re-probe
    the local runtime for every screenshot.

    Never raises. A routing decision that fails because a local server was
    mid-restart is worse than a routing decision that assumes no local
    vision, which is the safe default anyway.
    """
    ttl = _LOCAL_CAPABILITY_TTL_S if ttl_s is None else ttl_s
    now = time.monotonic()
    if _local_capability_cache["value"] is not None and now - _local_capability_cache["at"] < ttl:
        return _local_capability_cache["value"]

    result: dict[str, Any] = {"reachable": False, "models": [], "vision_models": [], "error": ""}
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=4) as resp:
            tags = json.loads(resp.read().decode())
        result["reachable"] = True
        names = [m.get("name") for m in (tags.get("models") or []) if isinstance(m, dict) and m.get("name")]
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        _local_capability_cache.update({"at": now, "value": result})
        return result

    for name in names:
        entry: dict[str, Any] = {"name": name, "capabilities": [], "vision": False}
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/show",
                data=json.dumps({"model": name}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                show = json.loads(resp.read().decode())
            caps = show.get("capabilities") or []
            entry["capabilities"] = [str(c) for c in caps if isinstance(c, str)]
            entry["vision"] = "vision" in entry["capabilities"]
        except Exception:  # noqa: BLE001 - one unreadable model is not a total failure
            entry["error"] = "could not read /api/show"
        result["models"].append(entry)
        if entry["vision"]:
            result["vision_models"].append(name)

    _local_capability_cache.update({"at": now, "value": result})
    return result


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TierChoice:
    """One candidate model, and why it is or is not usable for an image."""

    model: str
    provider: str = ""
    usable: bool = False
    reason: str = ""
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "usable": self.usable,
            "reason": self.reason,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class VisionRoute:
    """The decision, with the evidence attached so it can be argued with."""

    needs_vision: bool
    plan: str = "not_a_vision_request"
    tiers: tuple[TierChoice, ...] = ()
    local_vision: tuple[str, ...] = ()
    #: Every number that went into the decision, so a reader can re-run them.
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_vision": self.needs_vision,
            "plan": self.plan,
            "tiers": [t.to_dict() for t in self.tiers],
            "local_vision_models": list(self.local_vision),
            "evidence": self.evidence,
        }


def _split_ref(ref: str) -> tuple[str, str]:
    """``google/gemini-2.5-flash`` -> ``("google", "gemini-2.5-flash")``.

    Only the first segment is the provider. Gemini model ids legitimately
    contain slashes (``google/gemma-3-27b-it``), so splitting on every slash
    would call the provider ``google/gemma-3-27b-it`` and then treat
    ``google`` as a local runtime, which is exactly the wrong answer to give
    about a hosted model.
    """
    text = str(ref or "").strip()
    if "/" not in text:
        return "", text
    provider, _, rest = text.partition("/")
    return provider.strip().lower(), rest.strip()


#: Providers that serve multimodal models. Ollama is deliberately absent: a
#: local Ollama *can* serve a VLM, but on this box it does not, and that is
#: decided by ``local_vision_support`` reading the real capability list
#: rather than by a hardcoded provider rule.
KNOWN_VISION_PROVIDERS = frozenset({"google", "gemini", "openai", "groq", "openrouter", "anthropic", "nous"})


def plan_vision_route(
    text: str = "",
    *,
    tools: Iterable[str] = (),
    tiers: Optional[list[str]] = None,
    ollama_base_url: str = "http://localhost:11434",
    trust_local: bool = False,
) -> VisionRoute:
    """Decide where a vision turn goes, cheapest usable first.

    The rule is one line: **use a local model for an image only when a local
    model on this machine has actually advertised the ``vision`` capability.**
    Not "a local model is configured", not "the local tier is free", not "4B
    models are usually fine at vision". A capability flag, read at runtime.

    ``trust_local`` exists so the decision is testable and overridable, and it
    defaults to False because the measured default on this box is that the
    local tier cannot see at all.
    """
    names = {str(t) for t in tools or ()}
    wants = needs_vision(text, names)
    if not wants:
        return VisionRoute(needs_vision=False)

    support = local_vision_support(ollama_base_url)
    local_vision = tuple(support.get("vision_models") or ())
    evidence: dict[str, Any] = {
        "local_runtime_reachable": bool(support.get("reachable")),
        "local_models": [
            {"name": m.get("name"), "vision": m.get("vision"), "capabilities": m.get("capabilities")}
            for m in (support.get("models") or [])
        ],
        "local_vision_models": list(local_vision),
        "probe_errors": support.get("error", ""),
    }

    if trust_local and local_vision:
        return VisionRoute(
            needs_vision=True,
            plan="local",
            tiers=(TierChoice("local:" + local_vision[0], "ollama", True, "local model advertises vision"),),
            local_vision=local_vision,
            evidence=evidence,
        )

    ordered: list[TierChoice] = []
    for ref in tiers or []:
        provider, name = _split_ref(ref)
        if not ref.strip():
            continue
        if provider in ("ollama", "lmstudio", "nollama", "vllm") and ref not in local_vision:
            ordered.append(
                TierChoice(
                    model=ref,
                    provider=provider,
                    usable=False,
                    reason="no local model on this machine advertises the vision capability",
                )
            )
            continue
        ordered.append(TierChoice(model=ref, provider=provider, usable=True, reason="hosted tier"))

    plan = "cascade" if any(t.usable for t in ordered) else "unroutable"
    return VisionRoute(needs_vision=True, plan=plan, tiers=tuple(ordered), local_vision=local_vision, evidence=evidence)


# --------------------------------------------------------------------------
# The probe that keeps this file honest
# --------------------------------------------------------------------------
