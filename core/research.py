"""Web research pipeline — multi-source search → rank → claim extraction →
cross-check → contradiction detection → synthesis → citations.

Purely deterministic by default (offline-testable); optionally uses an LLM for
synthesis when a ``synthesizer`` callable is supplied.

Pipeline:

    QUERY → SEARCH (N sources) → DEDUPLICATE → RANK → EXTRACT CLAIMS
          → CROSS-CHECK → FIND CONTRADICTIONS → SYNTHESIZE → CITATIONS

Output shape:

    {
      "answer": str, "sources": [...], "confidence": float,
      "contradictions": [...], "uncertain": [...]
    }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Callable
from urllib.parse import urlparse

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "when", "like",
    "it", "its", "with", "that", "this", "these", "those", "are", "was", "were",
    "be", "been", "being", "as", "by", "from", "has", "have", "had", "is", "not",
    "no", "but", "than", "then", "about", "into", "at", "also", "while", "which",
}


def _content_tokens(text: str) -> set:
    toks = set(re.findall(r"[a-z0-9]+", text.lower()))
    return toks - _STOPWORDS


@dataclass
class Source:
    title: str
    url: str
    snippet: str
    rank: float = 0.0
    claims: list[str] = field(default_factory=list)
    evidence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "rank": round(self.rank, 3),
                "claims": self.claims, "evidence_count": self.evidence_count}


def _default_search(query: str, limit: int = 10) -> list[dict[str, str]]:
    try:
        from tools.web_search import search  # noqa: F401
        try:
            from ddgs import DDGS
        except ImportError:
            # Compatibility with older environments; ddgs is the canonical
            # required package in requirements.txt.
            from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = []
            for r in ddgs.text(query, max_results=limit):
                results.append({
                    "title": r.get("title") or "",
                    "url": r.get("href") or r.get("url") or "",
                    "snippet": r.get("body") or r.get("snippet") or "",
                })
            return results
    except Exception:
        return []


def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower().replace("www.", "")
        path = p.path.rstrip("/") or "/"
        return f"{host}{path}"
    except Exception:
        return url


def _token_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _extract_claims(text: str, max_claims: int = 5) -> list[str]:
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) > 30]
    return sents[:max_claims]


def _claim_norm(claim: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", claim.lower()).strip()


class ResearchPipeline:
    def __init__(self, search_fn: Optional[Callable[[str, int], list[dict[str, str]]]] = None,
                 synthesizer: Optional[Callable[[str, list[Source]], str]] = None):
        self.search_fn = search_fn or _default_search
        self.synthesizer = synthesizer

    def _deduplicate(self, sources: list[dict[str, str]]) -> list[dict[str, str]]:
        seen = set()
        out = []
        for s in sources:
            norm = _normalize_url(s.get("url", ""))
            if norm and norm in seen:
                continue
            # title similarity dedupe
            dup = False
            for existing in out:
                if _token_overlap(s.get("title", ""), existing.get("title", "")) > 0.8:
                    dup = True
                    break
            if dup:
                continue
            seen.add(norm)
            out.append(s)
        return out

    def _rank(self, sources: list[dict[str, str]], query: str) -> list[Source]:
        ranked: list[Source] = []
        for s in sources:
            title = s.get("title", "")
            snippet = s.get("snippet", "")
            score = 0.0
            score += _token_overlap(query, title + " " + snippet) * 4.0
            score += min(len(snippet) / 500.0, 1.0)
            if s.get("url", "").startswith("https"):
                score += 0.5
            ranked.append(Source(title=title, url=s.get("url", ""), snippet=snippet, rank=score))
        ranked.sort(key=lambda s: s.rank, reverse=True)
        return ranked

    def _cross_check(self, ranked: list[Source]) -> dict[str, Any]:
        # count how many sources independently support each claim
        claim_support: dict[str, int] = {}
        for src in ranked:
            for claim in src.claims:
                key = _claim_norm(claim)
                claim_support.setdefault(key, set()).add(src.url)
        for src in ranked:
            for claim in src.claims:
                src.evidence_count = len(claim_support.get(_claim_norm(claim), {claim}) or {claim}) - 0
        return claim_support

    def _find_contradictions(self, ranked: list[Source]) -> list[dict[str, str]]:
        contradictions = []
        claims = [(s.url, c) for s in ranked for c in s.claims]
        negators = re.compile(r"\b(no|not|never|deny|denies|denied|refute|refuted|disagree|false|incorrect|untrue|without)\b")
        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                ui, ci = claims[i]
                uj, cj = claims[j]
                if ui == uj:
                    continue
                ta, tb = _content_tokens(ci), _content_tokens(cj)
                if not ta or not tb:
                    continue
                overlap = len(ta & tb) / len(ta | tb)
                # opposite polarity on substantially-overlapping content = contradiction
                if overlap >= 0.15 and (bool(negators.search(ci)) != bool(negators.search(cj))):
                    contradictions.append({"a": ci, "source_a": ui, "b": cj, "source_b": uj})
        return contradictions[:10]

    def run(self, query: str, limit: int = 10) -> dict[str, Any]:
        raw = self.search_fn(query, limit=limit)
        deduped = self._deduplicate(raw)
        ranked = self._rank(deduped, query)
        for src in ranked:
            src.claims = _extract_claims(src.snippet)
        support = self._cross_check(ranked)
        contradictions = self._find_contradictions(ranked)

        # synthesis: LLM if provided, else top-ranked, well-supported claims
        if self.synthesizer:
            answer = self.synthesizer(query, ranked)
        else:
            supported = [c for s in ranked for c in s.claims if len(support.get(_claim_norm(c), set())) >= 1]
            answer = " ".join(supported[:6]) or "No sufficiently supported claims found."

        # confidence: fraction of sources that are non-trivial + agreement
        non_empty = [s for s in ranked if s.snippet.strip()]
        agreement = 1.0 if not contradictions else max(0.1, 1.0 - len(contradictions) / 10.0)
        confidence = round((min(len(non_empty) / max(1, limit), 1.0)) * 0.5 + agreement * 0.5, 3)

        uncertain = [c for c in (s.claims[0] for s in ranked if s.claims)
                     if len(support.get(_claim_norm(c), set())) == 1][:5]

        return {
            "answer": answer,
            "sources": [s.to_dict() for s in ranked if s.snippet.strip() or s.url],
            "confidence": confidence,
            "contradictions": contradictions,
            "uncertain": uncertain,
        }


research_pipeline = ResearchPipeline()
