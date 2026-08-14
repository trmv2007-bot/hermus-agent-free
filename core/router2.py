"""Model Router 2.0 — per-step model selection.

Routes each call to the best available model for that specific step, rather
than assuming a single provider for the whole session.

Signals considered:
- task type      (chat / code / reasoning / vision / research / summary / tooling / longcontext)
- reasoning difficulty (1..5)
- context size   (estimated token length)
- tool-calling   (does this step need reliable function calling?)
- provider health / latency (from the multi-key manager)
- free-first ordering: Ollama → free cloud → other local/cloud

Falls back gracefully to the configured default model when no workers are
discovered, so it never breaks an offline environment.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .config import config

# task type -> (preferred model keywords, needs_tools, wants_vision)
TASK_PROFILES: Dict[str, Dict[str, Any]] = {
    "chat":        {"keywords": ("instruct", "chat", "8b", "7b", "small", "llama"), "tools": False, "vision": False},
    "code":        {"keywords": ("code", "coder", "deepseek", "qwen", "starcoder"), "tools": True, "vision": False},
    "reasoning":   {"keywords": ("reason", "thinking", "r1", "70b", "large", "o3", "o1"), "tools": True, "vision": False},
    "vision":      {"keywords": ("vision", "llava", "bakllava", "moondream", "llama3.2-vision"), "tools": False, "vision": True},
    "research":    {"keywords": ("research", "70b", "large", "llama3.3", "deepseek"), "tools": True, "vision": False},
    "summary":     {"keywords": ("8b", "7b", "small", "mini", "instruct"), "tools": False, "vision": False},
    "tooling":     {"keywords": ("function", "tool", "instruct", "qwen", "8b"), "tools": True, "vision": False},
    "longcontext": {"keywords": ("context", "128k", "long", "qwen", "llama3.1"), "tools": False, "vision": False},
}

CODE_HINTS = ("def ", "class ", "import ", "function", "code", "bug", "fix", "refactor", "python", "javascript",
              "sql", "```", "api", "script", "compile", "deploy", "docker", "regex")
REASON_HINTS = ("why", "explain", "analyze", "prove", "reason", "compare", "design", "architecture",
                "trade-off", "tradeoff", "think", "plan", "strategy", "evaluate")
VISION_HINTS = ("image", "photo", "picture", "screenshot", "see", "look at", "what is in", "ocr")
RESEARCH_HINTS = ("research", "find", "search", "sources", "cite", "latest", "news", "investigate")


class ModelRouter:
    def __init__(self, ollama_base_url: Optional[str] = None):
        self.ollama_base_url = ollama_base_url or config.ollama_base_url

    # -- classification -------------------------------------------------
    def classify_task(self, text: str) -> str:
        t = (text or "").lower()
        if any(h in t for h in VISION_HINTS):
            return "vision"
        if any(h in t for h in RESEARCH_HINTS) or len(t) > 800:
            return "research" if len(t) > 200 else "research"
        if any(h in t for h in CODE_HINTS):
            return "code"
        if any(h in t for h in REASON_HINTS) or len(t) > 400:
            return "reasoning"
        return "chat"

    def estimate_context_tokens(self, text: str) -> int:
        # ~4 chars/token rough heuristic
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

    def _available_workers(self) -> List[Dict[str, Any]]:
        workers: List[Dict[str, Any]] = []
        try:
            from .model_fleet import _available_workers

            workers = _available_workers(limit=32)
        except Exception:
            workers = []
        return workers

    def _score_worker(self, w: Dict[str, Any], task_type: str, needs_tools: bool,
                      wants_vision: bool, context_tokens: int) -> Tuple[float, str]:
        provider = (w.get("provider") or "").lower()
        model = (w.get("model") or "").lower()
        keywords = TASK_PROFILES[task_type]["keywords"]
        score = 0.0
        reasons: List[str] = []

        # free-first provider ordering
        order = {"ollama": 0, "groq": 1, "huggingface": 2, "hf": 2, "openrouter": 3, "mock": 0}
        score += (6 - order.get(provider, 4)) * 1.0
        reasons.append(f"provider={provider}")

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
        if task_type in ("reasoning", "research") and any(k in model for k in ("70b", "large", "r1", "deepseek", "o3")):
            score += 3.0
        # small/fast for chat/summary
        if task_type in ("chat", "summary") and any(k in model for k in ("8b", "7b", "3b", "small", "mini")):
            score += 1.5

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

        # tool-calling: prefer providers known for reliable tools when needed
        if needs_tools and provider in ("ollama", "groq", "openrouter"):
            score += 1.0
            reasons.append("tools-ok")

        return score, ",".join(reasons)

    def rank(self, task_type: str, context_tokens: int = 100, needs_tools: bool = False,
             wants_vision: bool = False) -> List[Dict[str, Any]]:
        profile = TASK_PROFILES.get(task_type, TASK_PROFILES["chat"])
        needs_tools = needs_tools or profile["tools"]
        wants_vision = wants_vision or profile["vision"]
        ranked = []
        for w in self._available_workers():
            s, reason = self._score_worker(w, task_type, needs_tools, wants_vision, context_tokens)
            ranked.append({**w, "score": round(s, 2), "reason": reason, "task_type": task_type})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def select(self, text: str, context_tokens: Optional[int] = None,
               needs_tools: bool = False, wants_vision: bool = False) -> Dict[str, Any]:
        """Choose the best model for a single step from the given text."""
        task_type = self.classify_task(text)
        ctx = context_tokens or self.estimate_context_tokens(text)
        ranked = self.rank(task_type, context_tokens=ctx, needs_tools=needs_tools, wants_vision=wants_vision)
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
                "alternatives": [
                    f"{r['provider']}/{r['model']}" for r in ranked[1:6]
                ],
            }
        # graceful fallback to configured default
        return {
            "success": False,
            "task_type": task_type,
            "difficulty": self.classify_difficulty(text),
            "context_tokens": ctx,
            "provider": config.model.split("/", 1)[0] if "/" in config.model else "ollama",
            "model": config.model,
            "reason": "no workers discovered; using configured default",
            "alternatives": [],
        }


router2 = ModelRouter()
