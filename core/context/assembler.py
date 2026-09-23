"""The single owner of what the model is told on a turn.

Everything here exists to answer one question: is this block necessary for the
task the model is doing right now? Architecture prose, endpoint inventories,
full tool catalogs and repeated memory recall are available on demand through
:mod:`core.context.ondemand` instead of being injected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .tiers import ContextBlock, ContextBudget, Tier, fit

#: How much lesson text one turn may spend. Lessons are useful and unbounded.
LESSON_LIMIT = 3
LESSON_CHARS = 140

#: The user model is a dict of arbitrary size; only these keys change behaviour.
USER_MODEL_KEYS = ("name", "preferences", "goals", "constraints", "expertise", "projects")


@dataclass
class ContextRequest:
    user_message: str = ""
    session_id: str = ""
    user_id: str = "default"
    mode: str = "agent"
    project: str = ""
    model_name: str = ""
    max_steps: int = 20
    profile: str = ""
    capability_line: str = ""
    task_brief: str = ""
    mission_state: str = ""
    lean: bool = False
    budget: ContextBudget = field(default_factory=ContextBudget)


def build_system_prompt(req: ContextRequest, emit=None) -> tuple[str, dict]:
    """Render the tiered prompt and return it with its measurement report."""
    from core.run_events import record_issue  # local import: keeps context importable anywhere

    blocks: list[ContextBlock] = []
    blocks.append(_identity(req))
    if req.capability_line:
        blocks.append(
            ContextBlock(
                kind="capabilities",
                tier=Tier.CORE,
                text=req.capability_line.strip(),
                source="caller tool selection",
                priority=90,
            )
        )
    blocks.append(_constraints(req))
    blocks.append(_minimal_state(req))

    if req.task_brief:
        blocks.append(
            ContextBlock(
                kind="task",
                tier=Tier.TASK,
                text=req.task_brief.strip(),
                source="mission/delegation brief",
                priority=100,
            )
        )
    if req.mission_state:
        blocks.append(
            ContextBlock(
                kind="mission",
                tier=Tier.TASK,
                text=req.mission_state.strip(),
                source="mission state",
                priority=95,
            )
        )

    memory_block = _memory(req, emit)
    if memory_block:
        blocks.append(memory_block)

    lessons = _lessons(req)
    if lessons:
        blocks.append(lessons)

    if not req.lean:
        continuity = _continuity(req)
        if continuity:
            blocks.append(continuity)
        user = _user_model()
        if user:
            blocks.append(user)
        skills = _skills()
        if skills:
            blocks.append(skills)

    try:
        plan = fit(blocks, req.budget)
    except Exception as exc:  # never let packing break a turn
        record_issue("context", "fit", exc, retryable=False, fallback="unbudgeted blocks rendered in collection order")
        from .tiers import ContextPlan

        plan = ContextPlan(blocks=blocks, omitted=[], budget=req.budget)

    if emit is not None:
        try:
            emit("context_assembled", plan.report())
        except Exception:
            pass
    return plan.render(), plan.report()


def _identity(req: ContextRequest) -> ContextBlock:
    """One identity statement. Presence and profile personas extend it; they do
    not restate it."""
    lines = ["You are Hermus — an agent that gets real work done and checks its own results."]
    if req.profile:
        persona = ""
        try:
            from core.profiles import profile_manager

            persona = str(profile_manager.system_prompt(req.profile) or "").strip()
        except Exception:
            persona = ""
        if persona:
            lines.append(f"Persona ({req.profile}): {persona}")
    return ContextBlock(
        kind="identity",
        tier=Tier.CORE,
        text="\n".join(lines),
        source="core.context",
        priority=100,
    )


def _constraints(req: ContextRequest) -> ContextBlock:
    rules = [
        "Rules:",
        "- Look things up instead of guessing; use tools when a tool can settle the question.",
        "- Never claim an action succeeded unless a tool result or observation confirms it. "
        "An unverified result is reported as unverified.",
        "- When the work is done, answer plainly and stop calling tools.",
        f"- Tool budget for this turn: {req.max_steps} steps.",
    ]
    if not req.lean:
        rules.append("- Ask for deeper context with `context_read` rather than guessing at internals.")
    return ContextBlock(
        kind="constraints",
        tier=Tier.CORE,
        text="\n".join(rules),
        source="core.context",
        priority=80,
    )


def _minimal_state(req: ContextRequest) -> ContextBlock:
    text = (
        f"Runtime: model={req.model_name or 'unrouted'} mode={req.mode} "
        f"session={req.session_id or '-'} project={req.project or '-'}"
    )
    return ContextBlock(kind="state", tier=Tier.CORE, text=text, source="core.context", priority=70)


def _memory(req: ContextRequest, emit=None) -> ContextBlock | None:
    """Exactly one memory block per turn.

    Previously four independent producers each added their own recall header and
    body, so the same remembered fact could appear three times in one prompt.
    Typed recall wins; curated memory is the fallback, never the addition.
    """
    from core.config import config
    from core.memory import memory

    text = ""
    provenance = "typed recall"
    if getattr(config, "memory2_enabled", True) and req.user_message:
        try:
            ctx = memory.recall_context(req.user_message, limit=5, project=req.project) or {}
            text = str(ctx.get("text") or "").strip()
            if emit is not None and ctx:
                emit(
                    "memory_recalled",
                    {
                        "mode": ctx.get("mode"),
                        "kept": len(ctx.get("kept") or []),
                        "evicted": len(ctx.get("evicted") or []),
                        "ids": (ctx.get("ids") or [])[:12],
                        "tokens": ctx.get("tokens"),
                        "budget_tokens": ctx.get("budget_tokens"),
                        "index": ctx.get("index"),
                    },
                )
        except Exception as exc:
            from core.run_events import record_issue

            record_issue("memory", "memory2_recall", exc, retryable=False, fallback="curated memory used instead")
            text = ""

    if not text and not req.lean:
        try:
            curated = memory.get_curated_memory(limit=6)
        except Exception:
            curated = []
        if curated:
            provenance = "curated memory"
            text = "Curated memory:\n" + "\n".join(
                f"- {m.get('key')}: {str(m.get('value'))[:160]}" for m in curated
            )

    if req.lean:
        return None
    if not text:
        return None
    return ContextBlock(
        kind="memory",
        tier=Tier.TASK,
        text=text,
        source=f"core.memory ({provenance})",
        priority=60,
        truncate_at=2200,
    )


def _lessons(req: ContextRequest) -> ContextBlock | None:
    """A hard cap on lesson text, with the same applied-marking the old
    unbounded block performed."""
    try:
        from core.reasoning.lessons import lessons_store

        picked = lessons_store.relevant(req.user_message, limit=LESSON_LIMIT) or []
    except Exception:
        return None
    lines = []
    for lesson in picked:
        body = str(lesson.get("lesson") or "").strip()
        if not body:
            continue
        lines.append(f"- [{lesson.get('category') or 'lesson'}] {body[:LESSON_CHARS]}")
        try:
            lessons_store.mark_applied(lesson["id"])
        except Exception:
            pass
    if not lines:
        return None
    text = "Lessons learned (from past sessions):\n" + "\n".join(lines)
    return ContextBlock(
        kind="lessons",
        tier=Tier.TASK,
        text=text,
        source="core.reasoning.lessons",
        priority=45,
        truncate_at=LESSON_LIMIT * (LESSON_CHARS + 8),
    )


def _continuity(req: ContextRequest) -> ContextBlock | None:
    try:
        from core.presence import get_presence

        text = str(
            get_presence().prompt_block(session_id=req.session_id, user_id=str(req.user_id or "default"))
        ).strip()
    except Exception as exc:
        from core.run_events import record_issue

        record_issue("presence", "prompt_block", exc, retryable=False, fallback="no continuity block")
        return None
    if not text:
        return None
    return ContextBlock(
        kind="continuity",
        tier=Tier.TASK,
        text=text,
        source="core.presence",
        priority=40,
        truncate_at=900,
    )


def _user_model() -> ContextBlock | None:
    try:
        from core.memory import memory

        digest = user_model_digest(memory.load_user_model())
    except Exception:
        return None
    if not digest:
        return None
    return ContextBlock(
        kind="user",
        tier=Tier.TASK,
        text=f"What is known about the user: {digest}",
        source="core.memory.user_model",
        priority=38,
        truncate_at=340,
    )


def _skills() -> ContextBlock | None:
    try:
        from core.skill_manager import skill_manager

        names = [str(s.get("name")) for s in skill_manager.list_skills()[:12] if s.get("name")]
    except Exception:
        return None
    if not names:
        return None
    return ContextBlock(
        kind="skills",
        tier=Tier.TASK,
        text="Known skills (prefer a skill over re-deriving a known workflow): " + ", ".join(names),
        source="core.skill_manager",
        priority=35,
        truncate_at=400,
    )


def user_model_digest(user_model: Any, limit: int = 320) -> str:
    """Compact the user model to the keys that steer behaviour.

    The full dict was previously JSON-dumped to 1000 characters on every step of
    every turn, most of it counters and timestamps.
    """
    if not isinstance(user_model, dict) or not user_model:
        return ""
    picked = {k: user_model[k] for k in USER_MODEL_KEYS if k in user_model}
    if not picked:
        picked = dict(list(user_model.items())[:4])
    try:
        text = json.dumps(picked, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return ""
    return text if len(text) <= limit else text[:limit] + "…(truncated)"
