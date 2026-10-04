"""Model Router 2.0 — Task-Aware and Capability-Aware Model Routing.

Routes each call to the best available model for that specific step, role, and task type:
- Coding tasks → coding-specialized models (Coder, DeepSeek-Coder, Qwen-Coder)
- Difficult reasoning & architecture → reasoning models (R1, o1/o3, QwQ, large reasoning)
- Visual tasks → vision-capable models (Llava, Bakllava, Vision)
- Critic & Verification → independent models distinct from generator
- Historical provider reliability & cooldown tracking
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from .config import config

# task type -> (preferred model keywords, needs_tools, wants_vision)
TASK_PROFILES: dict[str, dict[str, Any]] = {
    "chat": {"keywords": ("instruct", "chat", "8b", "7b", "small", "llama"), "tools": False, "vision": False},
    "code": {"keywords": ("code", "coder", "deepseek", "qwen", "starcoder"), "tools": True, "vision": False},
    "reasoning": {"keywords": ("reason", "thinking", "r1", "70b", "large", "o3", "o1"), "tools": True, "vision": False},
    "vision": {"keywords": ("vision", "llava", "bakllava", "moondream", "llama3.2-vision"), "tools": False, "vision": True},
    "research": {"keywords": ("research", "70b", "large", "llama3.3", "deepseek"), "tools": True, "vision": False},
    "summary": {"keywords": ("8b", "7b", "small", "mini", "instruct"), "tools": False, "vision": False},
    "tooling": {"keywords": ("function", "tool", "instruct", "qwen", "8b"), "tools": True, "vision": False},
    "longcontext": {"keywords": ("context", "128k", "long", "qwen", "llama3.1"), "tools": False, "vision": False},
    "critic": {
        "keywords": ("critic", "reviewer", "eval", "70b", "r1", "claude", "gpt", "deepseek"),
        "tools": False,
        "vision": False,
    },
    "verifier": {"keywords": ("verifier", "check", "coder", "deepseek", "qwen", "instruct"), "tools": True, "vision": False},
}

CODE_HINTS = (
    "def ",
    "class ",
    "import ",
    "function",
    "code",
    "bug",
    "fix",
    "refactor",
    "python",
    "javascript",
    "sql",
    "```",
    "api",
    "script",
    "compile",
    "deploy",
    "docker",
    "regex",
)
REASON_HINTS = (
    "why",
    "explain",
    "analyze",
    "prove",
    "reason",
    "compare",
    "design",
    "architecture",
    "trade-off",
    "tradeoff",
    "think",
    "plan",
    "strategy",
    "evaluate",
)
VISION_HINTS = ("image", "photo", "picture", "screenshot", "see", "look at", "what is in", "ocr")
RESEARCH_HINTS = ("research", "find", "search", "sources", "cite", "latest", "news", "investigate")


class ModelRouter:
    def __init__(self, ollama_base_url: str | None = None):
        self.ollama_base_url = ollama_base_url or config.ollama_base_url
        self._provider_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"successes": 0, "failures": 0, "consecutive_failures": 0, "last_failure_ts": 0.0}
        )

    def record_outcome(self, provider: str, model: str, success: bool, latency_ms: float | None = None) -> None:
        key = f"{provider}:{model}".lower()
        stats = self._provider_stats[key]
        if success:
            stats["successes"] += 1
            stats["consecutive_failures"] = 0
        else:
            stats["failures"] += 1
            stats["consecutive_failures"] += 1
            stats["last_failure_ts"] = time.time()

    # -- classification -------------------------------------------------
    def classify_task(self, text: str) -> str:
        t = (text or "").lower()
        if any(h in t for h in VISION_HINTS):
            return "vision"
        if any(h in t for h in ("review code", "audit security", "critique", "critic")):
            return "critic"
        if any(h in t for h in ("verify outcome", "verifier", "test proof")):
            return "verifier"
        if any(h in t for h in RESEARCH_HINTS) or len(t) > 800:
            return "research" if len(t) > 200 else "research"
        if any(h in t for h in CODE_HINTS):
            return "code"
        if any(h in t for h in REASON_HINTS) or len(t) > 400:
            return "reasoning"
        return "chat"

    def estimate_context_tokens(self, text: str) -> int:
        return max(1, len(text or "") // 4)

    def classify_difficulty(self, text: str) -> int:
        t = (text or "").lower()
        score = 1
        if len(t) > 400:
            score += 1
        if any(h in t for h in REASON_HINTS):
            score += 1
        if any(h in t for h in CODE_HINTS):
            score += 1
        if any(k in t for k in ("multi", " and ", "1.", "2.", "step", "then", "compare")):
            score += 1
        return min(5, max(1, score))

    def _available_workers(self) -> list[dict[str, Any]]:
        """Build candidates from the canonical ModelGateway catalog first."""
        try:
            from .models import get_model_gateway

            catalog = get_model_gateway().catalog(probe=False, refresh=False)
            rows = catalog.get("models") or []
            workers = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ref = str(row.get("ref") or "")
                provider, _, model = ref.partition("/")
                provider = provider or str(row.get("provider") or "")
                model = model or str(row.get("id") or "")
                if not provider or not model:
                    continue
                workers.append({
                    "provider": provider,
                    "model": model,
                    "name": row.get("provider_name") or provider,
                    "base_url": row.get("base_url"),
                    "source": row.get("source"),
                    "reachable": row.get("reachable"),
                    "healthy": row.get("healthy"),
                    "latency_ms": row.get("latency_ms"),
                    "context_window": row.get("context_window") or row.get("context_tokens") or 0,
                    "capabilities": dict(row.get("capabilities") or {}),
                })
            if workers:
                try:
                    health_models = (get_model_gateway().health() or {}).get("models") or {}
                    for worker in workers:
                        ref = f"{worker.get('provider')}/{worker.get('model')}"
                        stats = health_models.get(ref) or {}
                        if stats:
                            worker["success_rate"] = stats.get("success_rate")
                            worker["avg_latency_ms"] = stats.get("avg_latency_ms")
                            worker["quality_calls"] = stats.get("calls")
                except Exception:
                    pass
                return workers[:32]
        except Exception:
            pass

        try:
            from .model_fleet import _available_workers
            return _available_workers(limit=32)
        except Exception:
            return []

    def _score_worker(
        self, w: dict[str, Any], task_type: str, needs_tools: bool, wants_vision: bool, context_tokens: int
    ) -> tuple[float, str]:
        provider = (w.get("provider") or "").lower()
        model = (w.get("model") or "").lower()
        capabilities = w.get("capabilities") or {}
        source = str(w.get("source") or "").lower()
        profile = TASK_PROFILES.get(task_type, TASK_PROFILES["chat"])
        keywords = profile["keywords"]
        score = 0.0
        reasons: list[str] = []

        # free-first provider ordering
        order = {"ollama": 0, "groq": 1, "huggingface": 2, "hf": 2, "openrouter": 3, "mock": 0}
        score += (6 - order.get(provider, 4)) * 1.0
        reasons.append(f"provider={provider}")

        # Capability evidence is authoritative; unknown is not treated as support.
        if needs_tools:
            cap = capabilities.get("tools")
            if cap in ("no", False):
                return -100.0, "tools-unsupported"
            score += 8.0 if cap in ("yes", True) else -2.0
            reasons.append("tools-confirmed" if cap in ("yes", True) else "tools-unknown")
        if wants_vision:
            cap = capabilities.get("vision")
            if cap in ("no", False):
                return -100.0, "vision-unsupported"
            score += 8.0 if cap in ("yes", True) else -2.0
            reasons.append("vision-confirmed" if cap in ("yes", True) else "vision-unknown")
        if task_type in ("reasoning", "research", "critic", "verifier") and capabilities.get("reasoning") in ("yes", True):
            score += 4.0
            reasons.append("reasoning-confirmed")
        if w.get("reachable") is False or w.get("healthy") is False:
            return -100.0, "unreachable"
        if source in ("local", "runtime", "ollama", "env-local"):
            score += 1.5
            reasons.append("local-preferred")

        # model keyword match
        if any(k in model for k in keywords):
            score += 3.0
            reasons.append("keyword-match")
        # task-type specific
        if wants_vision and any(k in model for k in ("vision", "llava", "bakllava", "moondream")):
            score += 4.0
            reasons.append("vision-capable")
        if task_type == "longcontext" and any(k in model for k in ("128k", "long", "1m")):
            score += 3.0
        # size heuristic for reasoning/research
        if task_type in ("reasoning", "research", "critic") and any(k in model for k in ("70b", "large", "r1", "deepseek", "o3")):
            score += 3.0
        # small/fast for chat/summary
        if task_type in ("chat", "summary") and any(k in model for k in ("8b", "7b", "3b", "small", "mini")):
            score += 1.5

        # Historical model quality from the canonical ModelGateway.
        quality_success = w.get("success_rate")
        quality_latency = w.get("avg_latency_ms")
        quality_calls = int(w.get("quality_calls") or 0)
        if quality_success is not None and quality_calls >= 2:
            success_rate = float(quality_success)
            score += max(-6.0, min(6.0, (success_rate - 0.75) * 12.0))
            reasons.append(f"quality={success_rate:.2f}")
        if quality_latency is not None and quality_calls >= 2:
            score -= min(float(quality_latency) / 3000.0, 2.0)
            reasons.append(f"avg-latency={int(float(quality_latency))}ms")

        # historical router reliability / failure tracking
        key = f"{provider}:{model}".lower()
        stats = self._provider_stats.get(key)
        if stats:
            consec = stats.get("consecutive_failures", 0)
            if consec > 0:
                cooldown_penalty = min(15.0, consec * 4.0)
                score -= cooldown_penalty
                reasons.append(f"consec-fails={consec}")

        # health/latency penalty
        if w.get("healthy") is False:
            score -= 10.0
            reasons.append("unhealthy")
        if w.get("latency_ms"):
            score -= min(float(w["latency_ms"]) / 2000.0, 3.0)

        # context capacity vs need
        ctx = int(w.get("context_window") or 0)
        if ctx and context_tokens > ctx:
            score -= 8.0
            reasons.append("context-too-small")

        # Tool capability is scored from catalog evidence above; provider names are not proof.

        return score, ",".join(reasons)

    def rank(
        self, task_type: str, context_tokens: int = 100, needs_tools: bool = False, wants_vision: bool = False
    ) -> list[dict[str, Any]]:
        profile = TASK_PROFILES.get(task_type, TASK_PROFILES["chat"])
        needs_tools = needs_tools or profile["tools"]
        wants_vision = wants_vision or profile["vision"]
        ranked = []
        for w in self._available_workers():
            provider = (w.get("provider") or "").lower()
            if needs_tools and not self._supports_tools(provider):
                continue
            s, reason = self._score_worker(w, task_type, needs_tools, wants_vision, context_tokens)
            if s <= -90.0:
                continue
            ranked.append({**w, "score": round(s, 2), "reason": reason, "task_type": task_type})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    @staticmethod
    def _supports_tools(provider: str) -> bool:
        try:
            from .providers import get_provider

            return get_provider(provider).get("supports_tools") is not False
        except Exception:
            return True

    def select(
        self,
        text: str,
        context_tokens: int | None = None,
        needs_tools: bool = False,
        wants_vision: bool = False,
        exclude_models: list[str] | None = None,
        task_type: str | None = None,
    ) -> dict[str, Any]:
        """Choose the best model for a single step from the given text."""
        task_type = task_type or self.classify_task(text)
        ctx = context_tokens or self.estimate_context_tokens(text)
        ranked = self.rank(task_type, context_tokens=ctx, needs_tools=needs_tools, wants_vision=wants_vision)

        if exclude_models:
            exclude_set = {m.lower() for m in exclude_models}
            ranked = [
                r
                for r in ranked
                if f"{r.get('provider')}/{r.get('model')}".lower() not in exclude_set
                and r.get("model", "").lower() not in exclude_set
            ]

        if ranked:
            top = ranked[0]
            return {
                "success": True,
                "task_type": task_type,
                "difficulty": self.classify_difficulty(text),
                "context_tokens": ctx,
                "provider": top["provider"],
                "model": f"{top['provider']}/{top['model']}",
                "reason": top["reason"],
                "alternatives": [f"{r['provider']}/{r['model']}" for r in ranked[1:6]],
            }
        # graceful fallback to configured default
        reason = "no workers discovered; using configured default"
        try:
            from .provider_resolver import diagnose

            diag = diagnose(require_tools=needs_tools, model=config.model)
            configured = [p["provider"] for p in diag.get("configured", [])]
            if configured:
                reason = f"no usable workers discovered; configured providers: {', '.join(configured)}. Using configured default."
            else:
                reason = "no usable workers discovered; no provider credentials"
        except Exception:
            pass
        return {
            "success": False,
            "task_type": task_type,
            "difficulty": self.classify_difficulty(text),
            "context_tokens": ctx,
            "provider": config.model.split("/", 1)[0] if "/" in config.model else "ollama",
            "model": config.model,
            "reason": reason,
            "alternatives": [],
        }

    def select_for_role(self, role: str, text: str, exclude_models: list[str] | None = None) -> dict[str, Any]:
        """Select a capability-appropriate model for specific roles (coder, critic, verifier, architect)."""
        role_map = {
            "coder": "code",
            "architect": "reasoning",
            "reviewer": "critic",
            "critic": "critic",
            "security_auditor": "critic",
            "verifier": "verifier",
            "researcher": "research",
        }
        task_type = role_map.get(role.lower(), "chat")
        return self.select(
            text, task_type=task_type, needs_tools=(task_type in ("code", "verifier", "research")), exclude_models=exclude_models
        )


router2 = ModelRouter()
