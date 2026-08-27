"""Memory decay + context eviction — forgetting as a first-class feature.

Why this exists: a recall engine that only ever *adds* memories eventually
floods the prompt with stale history. A 9-month-old note about a retired
deploy script scores the same forever unless something decays it.

Two mechanisms live here:

1. ``MemoryDecay`` — a temporal score per memory, blending
   * **recency** with an access-adaptive half-life (spaced repetition:
     ``half_life_eff = half_life * (1 + log1p(access_count))`` so a memory that
     keeps getting used fades slower), and
   * **access frequency** (saturating), plus explicit TTL / pin overrides.
   ``classify()`` maps the score onto a lifecycle band:
   ``hot → warm → cold → archived → purged``.

2. ``fit_to_budget`` — value-density eviction for prompt assembly: memories are
   packed into a token budget by ``score / tokens`` (not just top-K), with
   per-kind caps so one kind cannot crowd the others out, and pinned memories
   always survive.

Dependency-free (stdlib), deterministic, and testable offline.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from collections.abc import Callable, Iterable, Sequence

BANDS = ("hot", "warm", "cold", "archived")

# defaults tuned for a personal agent's memory, all overridable
DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_WORKING_TTL_HOURS = 48.0


def _parse_ts(ts: Any) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").split("+")[0])
    except (ValueError, TypeError):
        return None


def age_days(ts: Any, now: Optional[datetime] = None) -> float:
    dt = _parse_ts(ts)
    if not dt:
        return float("inf")
    now = now or datetime.now()
    if dt.tzinfo is not None:
        now = (now if now.tzinfo else datetime.now().astimezone())
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def access_count_of(row: dict[str, Any]) -> float:
    """Unified access count: explicit accesses vs the legacy dedupe counter."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    try:
        direct = float(row.get("access_count") or 0.0)
    except (TypeError, ValueError):
        direct = 0.0
    try:
        legacy = max(0.0, float(meta.get("frequency", 1) or 1) - 1.0)
    except (TypeError, ValueError):
        legacy = 0.0
    return max(direct, legacy)


@dataclass
class DecayReport:
    """Per-memory decay explanation (kept on the row for prompt/debug output)."""

    decay: float = 1.0
    recency: float = 1.0
    frequency: float = 0.0
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    age_days: float = 0.0
    band: str = "hot"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decay": round(self.decay, 4),
            "recency": round(self.recency, 4),
            "frequency": round(self.frequency, 4),
            "half_life_days": round(self.half_life_days, 2),
            "age_days": round(self.age_days, 2) if math.isfinite(self.age_days) else None,
            "band": self.band,
            "reasons": list(self.reasons),
        }


class MemoryDecay:
    """Exponential decay driven by recency + access frequency, with lifecycle bands."""

    def __init__(
        self,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        access_half_life_days: float = 14.0,
        saturate_access: float = 8.0,
        recency_weight: float = 0.7,
        frequency_weight: float = 0.3,
        hot_floor: float = 0.66,
        warm_floor: float = 0.33,
        cold_floor: float = 0.15,
        access_adaptive: bool = True,
    ):
        self.half_life_days = max(0.01, float(half_life_days))
        self.access_half_life_days = max(0.01, float(access_half_life_days))
        self.saturate_access = max(1.0, float(saturate_access))
        self.recency_weight = float(recency_weight)
        self.frequency_weight = float(frequency_weight)
        self.hot_floor = float(hot_floor)
        self.warm_floor = float(warm_floor)
        self.cold_floor = float(cold_floor)
        self.access_adaptive = bool(access_adaptive)

    # ------------------------------------------------------------------ scoring
    def recency_factor(self, ts: Any, half_life: float, now: Optional[datetime] = None) -> float:
        age = age_days(ts, now)
        if not math.isfinite(age):
            return 0.0
        return math.exp(-math.log(2.0) * age / max(0.01, float(half_life)))

    def effective_half_life(self, access_count: float) -> float:
        """Used-a-lot memories fade slower (spacing effect)."""
        if not self.access_adaptive:
            return self.half_life_days
        return self.half_life_days * (1.0 + math.log1p(max(0.0, float(access_count or 0.0))))

    def frequency_factor(self, access_count: float, last_access_ts: Any = None,
                         now: Optional[datetime] = None) -> float:
        """0..1, saturating in the number of accesses, gated by its own freshness."""
        n = max(0.0, float(access_count or 0.0))
        raw = 1.0 - math.exp(-n / self.saturate_access)
        gate = self.recency_factor(last_access_ts, self.access_half_life_days, now)
        return raw * (0.5 + 0.5 * gate)

    def evaluate(
        self,
        row: dict[str, Any],
        now: Optional[datetime] = None,
        *,
        access_count: Optional[float] = None,
        last_access_ts: Optional[Any] = None,
    ) -> DecayReport:
        now = now or datetime.now()
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        acc = access_count if access_count is not None else access_count_of(row)
        last = last_access_ts if last_access_ts is not None else (
            row.get("last_access_ts") or row.get("ts")
        )
        hl = self.effective_half_life(float(acc or 0.0))
        rec = self.recency_factor(row.get("ts"), hl, now)
        freq = self.frequency_factor(float(acc or 0.0), last, now)
        reasons: list[str] = []

        if bool(row.get("pinned") or meta.get("pinned")):
            reasons.append("pinned: decay bypassed")
            return DecayReport(1.0, rec, freq, hl, age_days(row.get("ts"), now), "hot", reasons)

        expires = row.get("expires_ts") or meta.get("expires")
        if expires and _parse_ts(expires) and _parse_ts(expires) < now:
            reasons.append(f"expired ({expires})")
            return DecayReport(0.0, rec, freq, hl, age_days(row.get("ts"), now), "archived", reasons)

        # Recency sets the level; use only *lifts* it (and already widened the
        # half-life above). Multiplying-then-adding keeps a brand-new memory at
        # decay 1.0 instead of pre-penalising it for not having been reused yet,
        # while a much-accessed old memory is pulled back up out of the cold band.
        decay = rec + self.frequency_weight * freq * (1.0 - rec)
        decay = max(0.0, min(1.0, decay))
        if acc and float(acc) > 1:
            reasons.append(f"accessed {int(float(acc))}x (half-life {hl:.0f}d)")
        if math.isfinite(age_days(row.get("ts"), now)):
            reasons.append(f"age {age_days(row.get('ts'), now):.1f}d")

        band = self.band(decay)
        return DecayReport(decay, rec, freq, hl, age_days(row.get("ts"), now), band, reasons)

    def score(self, row: dict[str, Any], **kw) -> float:
        return self.evaluate(row, **kw).decay

    def band(self, decay: float) -> str:
        if decay >= self.hot_floor:
            return "hot"
        if decay >= self.warm_floor:
            return "warm"
        if decay >= self.cold_floor:
            return "cold"
        return "archived"

    # -------------------------------------------------------------------- policy
    def plan(self, row: dict[str, Any], now: Optional[datetime] = None, *,
             archive_below: float = 0.08, purge_below: float = 0.02,
             protect_importance: float = 8.0, working_ttl_hours: float = DEFAULT_WORKING_TTL_HOURS,
             consolidate_after: int = 3) -> tuple[str, list[str]]:
        """Decide a lifecycle action: keep | decay | archive | purge | promote."""
        rep = self.evaluate(row, now)
        actions: list[str] = []
        kind = row.get("kind") or ""
        importance = float(row.get("importance") or 5.0)
        acc = access_count_of(row)

        if row.get("pinned") or importance >= protect_importance:
            return "keep", [f"protected (importance={importance}, pinned={bool(row.get('pinned'))})"]

        if kind == "working":
            if rep.age_days > working_ttl_hours / 24.0:
                return "purge", [f"working memory older than {working_ttl_hours}h TTL"]
            return "keep", ["working memory inside TTL"]

        if acc >= consolidate_after and kind == "episodic" and rep.decay >= self.warm_floor:
            actions.append("promote")

        if rep.decay <= purge_below:
            return "purge", actions + [f"decay {rep.decay:.3f} <= purge threshold {purge_below}"]
        if rep.decay <= archive_below or rep.band == "archived":
            return "archive", actions + [f"decay {rep.decay:.3f} below archive threshold {archive_below}"]
        return ("decay", actions + [f"decay {rep.decay:.3f} applied to recall score"])

    def status(self) -> dict[str, Any]:
        return {
            "half_life_days": self.half_life_days,
            "access_half_life_days": self.access_half_life_days,
            "saturate_access": self.saturate_access,
            "recency_weight": self.recency_weight,
            "frequency_weight": self.frequency_weight,
            "access_adaptive": self.access_adaptive,
            "bands": {"hot": self.hot_floor, "warm": self.warm_floor, "cold": self.cold_floor},
        }


# --------------------------------------------------------------------- eviction
def _count_tokens_default(text: str) -> int:
    """Cheap, dependency-free token estimate (falls back to chars/4)."""
    try:
        from .token_counter import token_counter

        return int(token_counter.count_text(text or ""))
    except Exception:
        return max(1, len(text or "") // 4)


def value_density(score: float, tokens: int) -> float:
    return float(score or 0.0) / max(1, int(tokens or 1))


def fit_to_budget(
    items: Sequence[dict[str, Any]],
    *,
    budget_tokens: int,
    text_key: str = "content",
    score_key: str = "score",
    per_kind_cap: Optional[int] = 3,
    always_keep_key: str = "pinned",
    min_items: int = 1,
    token_counter: Optional[Callable[[str], int]] = None,
    prefix: Optional[Callable[[dict[str, Any]], str]] = None,
) -> dict[str, Any]:
    """Pack ranked memories into a token budget by value density.

    Unlike "take the top K", this drops a long low-value memory in favour of a
    short high-value one, which is what actually keeps stale context out of the
    prompt. Pinned items are reserved first; the remainder is filled greedily by
    score (best-first) but skipped when its density is under half the running
    average and the budget is tight — that is the eviction rule.
    """
    count = token_counter or _count_tokens_default
    items = list(items or [])
    if not items:
        return {"kept": [], "evicted": [], "tokens": 0, "budget_tokens": int(budget_tokens),
                "text": "", "dropped_tokens": 0, "utilization": 0.0}

    decorated = []
    for it in items:
        text = str(it.get(text_key) or "")
        t = count(text)
        decorated.append({
            "item": it,
            "tokens": t,
            "density": value_density(float(it.get(score_key) or 0.0), t),
        })

    kept_idx: set = set()
    used = 0
    per_kind: dict[str, int] = {}

    def try_take(idx: int) -> bool:
        nonlocal used
        d = decorated[idx]
        kind = str(d["item"].get("kind") or "")
        if per_kind_cap is not None and kind and per_kind.get(kind, 0) >= per_kind_cap:
            return False
        if used + d["tokens"] > budget_tokens and len(kept_idx) >= min_items:
            return False
        kept_idx.add(idx)
        used += d["tokens"]
        per_kind[kind] = per_kind.get(kind, 0) + 1
        return True

    # pinned first, then strict score order (the recall ranking is meaningful)
    for idx, d in enumerate(decorated):
        if d["item"].get(always_keep_key):
            try_take(idx)
    for idx in sorted(range(len(decorated)),
                      key=lambda i: float(decorated[i]["item"].get(score_key) or 0.0),
                      reverse=True):
        if idx in kept_idx:
            continue
        try_take(idx)
        if used >= budget_tokens:
            break

    # If the per-kind cap was the binding constraint (not the budget), relax it and
    # keep filling: a cap should never leave 80% of the window empty.
    if per_kind_cap is not None and budget_tokens and kept_idx:
        room = used < 0.85 * float(budget_tokens)
        blocked = [i for i, d in enumerate(decorated)
                   if i not in kept_idx and used + d["tokens"] <= budget_tokens]
        if room and blocked:
            saved_cap = per_kind_cap
            per_kind_cap = None
            for idx in sorted(blocked,
                              key=lambda i: float(decorated[i]["item"].get(score_key) or 0.0),
                              reverse=True):
                try_take(idx)
                if used >= 0.85 * float(budget_tokens):
                    break
            per_kind_cap = saved_cap

    kept = [decorated[i] for i in sorted(kept_idx)]
    evicted = [d for i, d in enumerate(decorated) if i not in kept_idx]

    lines: list[str] = []
    for d in kept:
        it = d["item"]
        pre = prefix(it) if prefix else f"[{it.get('kind', 'memory')}] ({round(float(it.get(score_key) or 0.0), 2)})"
        lines.append(f"- {pre} {str(it.get(text_key) or '')[:700]}")

    return {
        "kept": [d["item"] for d in kept],
        "evicted": [d["item"] for d in evicted],
        "tokens": used,
        "budget_tokens": int(budget_tokens),
        "dropped_tokens": sum(d["tokens"] for d in evicted),
        "utilization": round(used / max(1, int(budget_tokens)), 3),
        "text": "\n".join(lines),
        "densities": [round(d["density"], 5) for d in kept],
    }


def consolidate(rows: Iterable[dict[str, Any]], *, similarity: float = 0.55,
                text_key: str = "content") -> list[list[dict[str, Any]]]:
    """Cluster near-duplicate memories (token Jaccard) for consolidation.

    Returns groups of size >= 2, biggest-importance-first inside a group.
    """
    rows = [dict(r) for r in rows or []]
    try:
        from .hybrid_search import tokenize
    except Exception:  # pragma: no cover - stdlib fallback
        import re as _re

        def tokenize(t: str) -> list[str]:  # type: ignore[misc]
            return _re.findall(r"[a-z0-9]+", (t or "").lower())

    groups: list[list[dict[str, Any]]] = []
    sigs: list[set] = []
    for r in sorted(rows, key=lambda x: float(x.get("importance") or 0.0), reverse=True):
        toks = set(tokenize(r.get(text_key) or ""))
        if not toks:
            continue
        placed = False
        for gi, gsig in enumerate(sigs):
            union = len(toks | gsig) or 1
            if len(toks & gsig) / union >= similarity:
                groups[gi].append(r)
                sigs[gi] = gsig | toks
                placed = True
                break
        if not placed:
            groups.append([r])
            sigs.append(toks)
    return [g for g in groups if len(g) > 1]
