"""Deliberation Strategies (Phase 3) — bounded, free-stack reasoning wrappers.

Each strategy is a thin, cost-capped layer around the draft final answer:
- reflexion_in_loop   : critique pass -> revise pass (default for difficulty 3)
- self_consistency    : k parallel drafts -> merge/consensus call (difficulty 5)
- verify_with_tools   : key claims -> web_search evidence -> revise (difficulty 4)

All are bounded (extra calls capped), audited (strategy + reason logged), and
degrade gracefully: ANY failure returns the original draft unchanged.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ..config import config
from ..llm import FreeLLM


def _chat(llm: FreeLLM, system: str, user: str) -> str:
    resp = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return (resp.content or "").strip()


def _evidence_text(evidence: list[dict], limit: int = 1800) -> str:
    if not evidence:
        return "(no tool evidence gathered)"
    lines = []
    for e in evidence[:8]:
        name = e.get("tool", "?")
        args = json.dumps(e.get("args", {}), ensure_ascii=False)[:120]
        res = json.dumps(e.get("result", ""), ensure_ascii=False, default=str)[:220]
        lines.append(f"- {name}({args}) -> {res}")
    return "\n".join(lines)[:limit]


# ---------------------------------------------------------------- strategies


def reflexion_in_loop(
    user_message: str,
    evidence: list[dict],
    draft: str,
    model: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Critique the draft, then revise it. 2 extra calls max."""
    llm = FreeLLM(model or config.model)
    ev = _evidence_text(evidence)
    try:
        critique = _chat(
            llm,
            (
                "You are a strict quality critic for an AI agent's final answer. "
                "Find concrete problems: unverified claims, missing numbers, contradictions "
                "with the tool evidence, incomplete answers to the task, edge cases."
                "Return ONLY a short bulleted list of issues (max 5)."
            ),
            f"Task: {user_message}\n\nTool evidence:\n{ev}\n\nDraft answer:\n{draft[:3000]}",
        )
        revised = _chat(
            llm,
            (
                "You are a careful reviser. Given the critique, produce the FINAL improved "
                "answer. Fix every valid issue, keep what is good, be complete and accurate. "
                "Do not mention the critique process."
            ),
            f"Task: {user_message}\n\nTool evidence:\n{ev}\n\nDraft answer:\n{draft[:2500]}\n\nCritique:\n{critique[:1200]}",
        )
        if revised:
            return revised, {"strategy": "reflexion", "critique": critique[:500]}
    except Exception as e:
        print(f"[DeepThink] reflexion failed ({e}) - using original draft")
    return draft, {"strategy": "reflexion", "fallback": True}


def self_consistency(
    user_message: str,
    evidence: list[dict],
    draft: str,
    model: Optional[str] = None,
    k: Optional[int] = None,
) -> tuple[str, dict[str, Any]]:
    """k parallel drafts + one merge/consensus call. k extra LLM calls total."""
    k = k or getattr(config, "self_consistency_k", 3)
    k = max(2, min(5, int(k)))
    ev = _evidence_text(evidence)

    def draft_one(seed: int) -> str:
        try:
            llm = FreeLLM(model or config.model)  # fresh per thread (rotates keys)
            return _chat(
                llm,
                (
                    f"You are an analyst drafting answer variant {seed}/{k}. "
                    "Answer the task completely using only the tool evidence. Be concrete."
                ),
                f"Task: {user_message}\n\nTool evidence:\n{ev}",
            )
        except Exception:
            return ""

    drafts = []
    with ThreadPoolExecutor(max_workers=k) as ex:
        futures = [ex.submit(draft_one, i) for i in range(1, k + 1)]
        for f in as_completed(futures):
            d = f.result()
            if d:
                drafts.append(d)
    if not drafts:
        return draft, {"strategy": "self_consistency", "fallback": True}
    if len(drafts) == 1:
        return drafts[0], {"strategy": "self_consistency", "drafts": 1}

    try:
        merged = _chat(
            FreeLLM(model or config.model),
            (
                "You are a consensus synthesizer. Below are k independent drafts for the same "
                "task. Produce ONE final answer that keeps the facts and phrasing they AGREE "
                "on, resolves disagreements with the tool evidence, and drops anything unique "
                "to a single draft unless clearly correct. Do not mention the drafts."
            ),
            f"Task: {user_message}\n\nTool evidence:\n{ev}\n\nDrafts:\n"
            + "\n---DRAFT---\n".join(d[:1500] for d in drafts),
        )
        if merged:
            return merged, {"strategy": "self_consistency", "drafts": len(drafts)}
    except Exception as e:
        print(f"[DeepThink] self-consistency merge failed ({e}) - using best draft")
    # Fallback: longest non-empty draft (usually the most complete)
    best = max(drafts, key=len)
    return best, {"strategy": "self_consistency", "drafts": len(drafts), "fallback_merge": True}


def verify_with_tools(
    user_message: str,
    evidence: list[dict],
    draft: str,
    model: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Extract key claims, verify each with a web search, then revise. Bounded.

    Max cost: 1 claims call + up to 3 searches + 1 revise call. Any failure in a
    verification step keeps the draft and continues (never blocks the answer).
    """
    llm = FreeLLM(model or config.model)
    ev = _evidence_text(evidence)
    claims: list[str] = []
    try:
        claims_text = _chat(
            llm,
            (
                "Extract the KEY checkable claims from the draft answer (facts, numbers, "
                "names, dates, prices, comparisons). Return ONLY JSON: "
                '{"claims": ["claim 1", "claim 2", "claim 3"]} — max 3 claims.'
            ),
            f"Draft answer:\n{draft[:2500]}",
        )
        m = re.search(r"\{.*\}", claims_text, re.S)
        if m:
            data = json.loads(m.group(0))
            claims = [str(c)[:180] for c in data.get("claims", [])][:3]
    except Exception as e:
        print(f"[DeepThink] verify claims extraction failed ({e})")

    verified = []
    for claim in claims:
        try:
            from ..tool_registry import tool_registry

            res = tool_registry.execute("web_search", {"query": claim, "max_results": 3})
            text = json.dumps(res, ensure_ascii=False, default=str)[:400]
            verified.append(f"Claim: {claim}\nSearch: {text}")
        except Exception as e:
            verified.append(f"Claim: {claim}\nSearch failed: {e}")

    if verified:
        try:
            revised = _chat(
                llm,
                (
                    "You are a fact-checking reviser. Given verification search results for "
                    "key claims of a draft answer, produce the FINAL corrected answer: fix "
                    "claims the search contradicts, keep supported claims, and mark anything "
                    "still unverifiable as uncertain. Do not mention the verification process."
                ),
                f"Task: {user_message}\n\nDraft answer:\n{draft[:2500]}\n\nVerification results:\n"
                + "\n\n".join(verified)[:2500],
            )
            if revised:
                return revised, {"strategy": "verify", "claims": claims, "searches": len(verified)}
        except Exception as e:
            print(f"[DeepThink] verify revise failed ({e}) - using original draft")
    return draft, {"strategy": "verify", "claims": claims, "searches": len(verified), "fallback": True}


# ---------------------------------------------------------------- dispatcher


STRATEGIES = {
    "reflexion": reflexion_in_loop,
    "self_consistency": self_consistency,
    "verify": verify_with_tools,
}


def apply_strategy(
    strategy: str,
    user_message: str,
    evidence: list[dict],
    draft: str,
    model: Optional[str] = None,
    k: Optional[int] = None,
) -> tuple[str, dict[str, Any]]:
    """Run one strategy safely; returns (content, meta). Never raises."""
    fn = STRATEGIES.get(strategy)
    if not fn:
        return draft, {"strategy": strategy, "skipped": True}
    try:
        if strategy == "self_consistency":
            return fn(user_message, evidence, draft, model=model, k=k)
        return fn(user_message, evidence, draft, model=model)
    except Exception as e:
        print(f"[DeepThink] strategy {strategy} failed ({e})")
        return draft, {"strategy": strategy, "error": str(e)}
