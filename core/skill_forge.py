"""Skill forge — distill successful multi-step trajectories into SKILL.md skills.

The old path ("trajectory has ≥3 tool calls → ask an LLM to write some Python")
had three problems this module fixes:

1. **No evaluation gate.** A run that ended with a confident-looking but
   *failed* answer was distilled anyway, so the agent learned to reproduce
   mistakes. Every candidate now passes a scored evaluation: verification
   result, tool success rate, error/recovery evidence, and step count.
2. **Unstructured output.** Skills are emitted as a structured document —
   YAML frontmatter (agentskills.io style) for machine matching + an explicit
   Procedure / Verification / Pitfalls body — plus a deterministic
   `skill.py` that *replays the distilled procedure* through the tool registry
   rather than an opaque blob of generated code.
3. **No validation, no provenance, no dedupe.** A candidate is compiled,
   imported in a resource-limited **sandbox subprocess**, linted, and only then
   installed. Failures are quarantined under `.quarantine/` instead of being
   loaded into the next prompt. Duplicate procedures are detected against the
   existing skill set (token Jaccard + optional embeddings) and merged as a
   frequency bump instead of piling up near-identical skills.

Everything degrades gracefully: with no LLM available the deterministic
template still produces a valid, useful SKILL.md.

    from core.skill_forge import skill_forge
    skill_forge.harvest(goal, trajectory, verification=..., tool_results=...)
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable, Sequence

from .config import config

SLUG_RE = re.compile(r"[^a-z0-9]+")
ERROR_MARKERS = ("error", "failed", "exception", "traceback", "timeout", "denied")
OK_MARKERS = ("success", "done", "ok", "saved", "created", "verified")


def slugify(text: str, max_len: int = 44) -> str:
    s = SLUG_RE.sub("_", (text or "").lower()).strip("_")
    return (s[:max_len].rstrip("_")) or "auto_skill"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_error_blob(value: Any) -> bool:
    """Structural failure detection — not a substring grep of the payload.

    A tool result whose *content* happens to mention "timeout" (e.g. log
    analysis) is not a failed call; a result with `error`, `success: false`, or
    a non-zero `returncode` is.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        text = value.strip().lower()
        return text.startswith(("error", "traceback", "failed", "exception", "timeout")) or \
            text.startswith('"error"')
    if isinstance(value, dict):
        if value.get("success") is True:
            return False
        for key in ("error", "exception", "traceback"):
            if value.get(key) not in (None, "", False, [], {}):
                return True
        rc = value.get("returncode", value.get("exit_code", value.get("status_code")))
        if isinstance(rc, int) and rc != 0:
            return True
        if value.get("success") is False:
            return True
        return False
    if isinstance(value, list):
        return any(_is_error_blob(v) for v in value[:10] if isinstance(v, (dict, str)))
    return False


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _containment(needle: str, haystack: str) -> float:
    """Asymmetric overlap: what fraction of ``needle``'s tokens appear in the doc.

    A short candidate description compared against a long SKILL.md via Jaccard
    always looks dissimilar; containment is the right measure for dedupe.
    """
    ta, tb = _tokens(needle), _tokens(haystack)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


# --------------------------------------------------------------------- evaluate
@dataclass
class TaskEvaluation:
    """Scored verdict on whether a trajectory is worth distilling."""

    harvest: bool = False
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "harvest": self.harvest,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "metrics": self.metrics,
        }


def evaluate_trajectory(
    trajectory: Sequence[dict[str, Any]],
    *,
    verification: Optional[dict[str, Any]] = None,
    tool_results: Optional[Sequence[dict[str, Any]]] = None,
    min_tool_calls: int = None,
    final_answer: str = "",
) -> TaskEvaluation:
    """Post-task evaluation loop: did this run actually work, and is it reusable?

    Returns a verdict + the evidence the distiller needs (tool sequence,
    recoveries, failure markers).
    """
    min_tools = int(min_tool_calls if min_tool_calls is not None
                    else getattr(config, "skill_forge_min_tools", 3))
    traj = [dict(t) for t in (trajectory or [])]
    tool_calls = [
        {"tool": tc.get("name"), "args": tc.get("arguments") or tc.get("args") or {},
         "turn": i}
        for i, turn in enumerate(traj)
        for tc in (turn.get("tool_calls") or [])
        if isinstance(tc, dict) and tc.get("name")
    ]
    tool_results = list(tool_results or [])
    n_results = len(tool_results)
    failures = [
        tr for tr in tool_results
        if _is_error_blob(tr.get("result", tr))
    ]
    recovered = 0
    for idx, tr in enumerate(tool_results):
        if _is_error_blob(tr.get("result", tr)) and idx + 1 < len(tool_results):
            nxt = tool_results[idx + 1]
            if not _is_error_blob(nxt.get("result", nxt)):
                recovered += 1
    answer = final_answer or next(
        (t.get("content", "") for t in reversed(traj) if t.get("role") == "assistant"), ""
    )

    verified: Optional[bool] = None
    if isinstance(verification, dict) and "verified" in verification:
        verified = bool(verification.get("verified"))

    metrics = {
        "turns": len(traj),
        "tool_calls": len(tool_calls),
        "tool_results": n_results,
        "tool_failures": len(failures),
        "recoveries": recovered,
        "unique_tools": sorted({c["tool"] for c in tool_calls}),
        "answer_chars": len(answer or ""),
        "verified": verified,
    }
    reasons: list[str] = []
    score = 0.0

    if len(tool_calls) < min_tools:
        reasons.append(f"only {len(tool_calls)} tool call(s); need >= {min_tools}")
        return TaskEvaluation(False, 0.0, reasons, metrics)
    score += min(1.0, len(tool_calls) / max(1.0, min_tools * 2.0)) * 2.0

    if verified is False:
        reasons.append("verification explicitly failed this task")
        return TaskEvaluation(False, round(score, 3), reasons, metrics)
    if verified is True:
        score += 2.5
        reasons.append("verifier confirmed the outcome")
    else:
        score += 1.0  # unverified but not contradicted
        reasons.append("no verifier verdict; using heuristics")

    fail_rate = (len(failures) / n_results) if n_results else 0.0
    if fail_rate > 0.5:
        reasons.append(f"{len(failures)}/{n_results} tool results look like failures")
        return TaskEvaluation(False, round(score, 3), reasons, metrics)
    if fail_rate:
        score -= fail_rate
    else:
        score += 1.0
        reasons.append("all tool results look clean")
    if recovered:
        score += 0.5
        reasons.append(f"{recovered} failure(s) recovered from — worth capturing as a pitfall note")

    if not (answer or "").strip():
        reasons.append("empty final answer")
        return TaskEvaluation(False, round(score, 3), reasons, metrics)
    if len(set(metrics["unique_tools"])) < 2 and len(tool_calls) < 3:
        reasons.append("repeating one tool once or twice is not a reusable procedure")
        return TaskEvaluation(False, round(score, 3), reasons, metrics)

    # Reusability heuristic: goal text that is generic enough to recur.
    if len((answer or "")) < 40:
        score -= 0.5
        reasons.append("very short answer — thin evidence of a completed task")
    reasons.append(f"score {score:.2f} above harvest threshold")
    return TaskEvaluation(score >= 3.0, round(score, 3), reasons, metrics)


# ---------------------------------------------------------------------- extract
@dataclass
class DistilledStep:
    index: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    outcome: str = ""
    error: bool = False
    recovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "args": self.args,
            "intent": self.intent,
            "outcome": self.outcome,
            "error": self.error,
            "recovered": self.recovered,
        }


def extract_steps(
    trajectory: Sequence[dict[str, Any]],
    tool_results: Optional[Sequence[dict[str, Any]]] = None,
) -> list[DistilledStep]:
    """Flatten a trajectory into an ordered, de-duplicated procedure."""
    traj = [dict(t) for t in (trajectory or [])]
    goal = next((t.get("content", "") for t in traj if t.get("role") == "user"), "")
    by_call: dict[tuple[str, str], dict[str, Any]] = {}
    for tr in tool_results or []:
        key = (str(tr.get("tool")), json.dumps(tr.get("args") or {}, sort_keys=True, default=str)[:200])
        by_call[key] = tr

    steps: list[DistilledStep] = []
    seen: set = set()
    for turn in traj:
        for tc in turn.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name") or ""
            args = tc.get("arguments") or tc.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args[:200]}
            if not name:
                continue
            sig = (name, json.dumps(args, sort_keys=True, default=str)[:160])
            result = by_call.get((name, json.dumps(args, sort_keys=True, default=str)[:200]), {})
            err = _is_error_blob(result.get("result", {})) if result else False
            if sig in seen:
                # collapse repeated identical calls, but remember the retry
                for prev in reversed(steps):
                    if prev.tool == name and json.dumps(prev.args, sort_keys=True, default=str)[:160] == sig[1]:
                        prev.recovered = prev.recovered or err
                        break
                continue
            seen.add(sig)
            steps.append(
                DistilledStep(
                    index=len(steps) + 1,
                    tool=name,
                    args=args if isinstance(args, dict) else {"value": args},
                    intent=(turn.get("content") or "")[:160] or goal[:160],
                    outcome=_summarize_result(result.get("result", {})),
                    error=err,
                )
            )
    # mark recoveries: an errored step followed by a clean one using the same tool
    for i, st in enumerate(steps):
        if st.error and i + 1 < len(steps) and not steps[i + 1].error:
            st.recovered = True
    return steps


def _summarize_result(result: Any) -> str:
    if not result:
        return ""
    try:
        text = json.dumps(result, default=str)
    except Exception:
        text = str(result)
    return text[:220]


# ------------------------------------------------------------------------ forge
@dataclass
class SkillCandidate:
    name: str
    title: str
    description: str
    goal: str
    steps: list[DistilledStep]
    when_to_use: str = ""
    inputs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    verification: str = ""
    pitfalls: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    merged_with: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d


class SkillForge:
    """Turn one verified agent run into one validated, indexed skill."""

    PROMPT = (
        "You are Hermus' skill distiller. Convert a VERIFIED multi-step agent "
        "trajectory into a reusable skill. Return ONLY a JSON object with keys: "
        "name (snake_case, <=44 chars), title, description (one sentence, imperative), "
        "when_to_use (2-4 bullet sentences), inputs (list of parameter names the caller "
        "should supply), tags (list of <=5 lowercase tags), verification (how a future "
        "run proves it worked). No code, no markdown fences, no commentary."
    )

    def __init__(self, skills_dir: Optional[str] = None, llm: Optional[Callable[[list[dict]], str]] = None):
        self.skills_dir = Path(skills_dir or config.resolve_path(config.skills_dir))
        self._llm = llm
        self.quarantine_dir = self.skills_dir / ".quarantine"

    # ------------------------------------------------------------------ registry
    @property
    def registry_path(self) -> Path:
        return self.skills_dir / "registry.json"

    def _registry(self) -> dict[str, Any]:
        try:
            return json.loads(self.registry_path.read_text())
        except Exception:
            return {"version": 1, "updated": None, "skills": {}}

    def _save_registry(self, reg: dict[str, Any]) -> None:
        reg["updated"] = _now()
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.registry_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
            tmp.replace(self.registry_path)
        except Exception:
            pass

    def index(self) -> dict[str, Any]:
        reg = self._registry()
        return {
            "count": len(reg.get("skills", {})),
            "updated": reg.get("updated"),
            "path": str(self.registry_path),
            "skills": reg.get("skills", {}),
        }

    # ------------------------------------------------------------------ dedupe
    def _existing_skills(self) -> list[dict[str, Any]]:
        out = []
        try:
            for d in sorted(self.skills_dir.iterdir()):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                md = d / "SKILL.md"
                if not md.exists():
                    continue
                try:
                    doc = md.read_text(errors="ignore")[:4000]
                except Exception:
                    continue
                out.append({"name": d.name, "doc": doc, "summary": _frontmatter_summary(doc) or d.name})
        except Exception:
            pass
        return out

    def find_similar(self, description: str, *, threshold: float = None) -> Optional[dict[str, Any]]:
        th = float(threshold if threshold is not None else getattr(config, "skill_forge_dedupe_similarity", 0.55))
        best, best_score = None, 0.0
        for sk in self._existing_skills():
            # Compare against the skill's own summary (frontmatter description),
            # not its whole README — long docs otherwise "contain" everything and
            # every new skill looks like a duplicate of the biggest skill.
            score = max(
                _jaccard(description, sk.get("summary") or ""),
                _containment(description, sk.get("summary") or ""),
            )
            if score > best_score:
                best, best_score = sk, score
        if best and best_score >= th:
            return {"name": best["name"], "doc": best.get("summary", "")[:400],
                    "similarity": round(best_score, 3)}
        # semantic second opinion, when embeddings are available
        try:
            from .embeddings import embedding_store

            hits = embedding_store.search(description, limit=1).get("results") or []
            for hit in hits:
                content = hit.get("content") or ""
                if _containment(description, content) >= 0.4 and float(hit.get("score") or 0) >= 0.82:
                    return {
                        "name": (hit.get("metadata") or {}).get("skill", ""),
                        "doc": content[:400],
                        "similarity": round(float(hit["score"]), 3),
                        "via": "embedding",
                    }
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ distill
    def distill(
        self,
        goal: str,
        steps: Sequence[DistilledStep],
        evaluation: Optional[TaskEvaluation] = None,
        session_id: str = "",
    ) -> SkillCandidate:
        tools = [s.tool for s in steps]
        fallback_name = slugify(goal or (tools[0] if tools else "auto_skill")) or "auto_skill"
        desc = (
            f"{(goal or '').strip().rstrip('.')[:180]} — distilled from a verified "
            f"{len(steps)}-step run using {', '.join(dict.fromkeys(tools))[:120]}"
        )
        cand = SkillCandidate(
            name=fallback_name,
            title=(goal or "Reusable procedure").strip().capitalize()[:80],
            description=desc,
            goal=(goal or "").strip()[:1000],
            steps=list(steps),
            when_to_use=self._when_to_use(goal, tools),
            inputs=self._inputs_for(steps),
            tags=["auto-distilled", *tools[:3]],
            verification=(
                "Re-run the procedure and confirm each step's expected outcome is present; "
                "the final answer must reference the same artifacts as the recorded run."
            ),
            pitfalls=[
                f"Step {s.index} ({s.tool}) errored during the recorded run and was recovered: "
                f"{(s.outcome or '')[:120]}"
                for s in steps if s.recovered
            ],
            provenance={
                "session": session_id,
                "created": _now(),
                "hash": hashlib.sha1(json.dumps([s.to_dict() for s in steps], default=str).encode()).hexdigest()[:12],
                "evaluation": evaluation.to_dict() if evaluation else {},
                "generator": "hermus-skill-forge",
            },
        )
        if getattr(config, "skill_forge_use_llm", True):
            cand = self._llm_polish(cand, steps, goal)
        cand.name = slugify(cand.name) or fallback_name
        cand.tags = [t for t in dict.fromkeys(cand.tags) if t][:6]
        return cand

    @staticmethod
    def _when_to_use(goal: str, tools: Sequence[str]) -> str:
        return (
            f"Use when the user asks for something equivalent to: \"{(goal or '').strip()[:160]}\". "
            f"The recorded run used {len(set(tools))} distinct tool(s) in a fixed order, so prefer "
            f"this skill over re-planning from scratch."
        )

    @staticmethod
    def _inputs_for(steps: Sequence[DistilledStep]) -> list[str]:
        keys: list[str] = []
        for s in steps:
            for k in (s.args or {}):
                if k not in keys:
                    keys.append(str(k))
        return keys[:12]

    def _llm_polish(self, cand: SkillCandidate, steps: Sequence[DistilledStep], goal: str) -> SkillCandidate:
        """Best-effort LLM naming/description pass; template output on any failure."""
        payload = {
            "goal": goal[:600],
            "procedure": [
                {"step": s.index, "tool": s.tool, "args_keys": sorted((s.args or {}).keys()),
                 "intent": s.intent[:120], "error": s.error}
                for s in steps
            ],
        }
        messages = [
            {"role": "system", "content": self.PROMPT},
            {"role": "user", "content": json.dumps(payload, default=str)[:6000]},
        ]
        try:
            raw = self._call_llm(messages)
            data = _parse_json_object(raw)
            if not data:
                return cand
            cand.name = slugify(str(data.get("name") or cand.name)) or cand.name
            cand.title = str(data.get("title") or cand.title)[:120]
            cand.description = str(data.get("description") or cand.description)[:400]
            cand.when_to_use = str(data.get("when_to_use") or cand.when_to_use)[:1200]
            inputs = data.get("inputs")
            if isinstance(inputs, list) and inputs:
                cand.inputs = [str(i) for i in inputs[:12]]
            tags = data.get("tags")
            if isinstance(tags, list) and tags:
                cand.tags = ["auto-distilled"] + [str(t).lower() for t in tags[:5]]
            cand.verification = str(data.get("verification") or cand.verification)[:600]
            cand.provenance["llm_distilled"] = True
        except Exception as e:  # never let the LLM break skill creation
            cand.provenance["llm_distilled"] = False
            cand.provenance["llm_error"] = str(e)[:160]
        return cand

    def _call_llm(self, messages: list[dict]) -> str:
        if self._llm:
            return self._llm(messages)
        try:
            from .llm import free_llm

            resp = free_llm.chat(messages)
            return getattr(resp, "content", "") or ""
        except Exception:
            return ""

    # -------------------------------------------------------------------- write
    def skill_md(self, cand: SkillCandidate) -> str:
        front = {
            "name": cand.name,
            "description": cand.description[:500],
            "when_to_use": " ".join(cand.when_to_use.split())[:600],
            "inputs": cand.inputs,
            "tools": [s.tool for s in cand.steps],
            "tags": cand.tags,
            "verification": cand.verification[:400],
            "generated_by": "hermus-skill-forge",
            "generated_at": cand.provenance.get("created"),
            "source_session": cand.provenance.get("session"),
            "trajectory_hash": cand.provenance.get("hash"),
        }
        fm = "\n".join(
            f"{k}: {json.dumps(v) if isinstance(v, (list, dict)) else _yaml_str(v)}"
            for k, v in front.items()
        )
        lines = [f"---\n{fm}\n---", "", f"# {cand.title}", "", cand.description, ""]
        lines += ["## When to use", "", cand.when_to_use, ""]
        if cand.inputs:
            lines += ["## Inputs", ""] + [f"- `{i}`" for i in cand.inputs] + [""]
        lines += ["## Procedure", ""]
        for s in cand.steps:
            args = ", ".join(f"{k}=<{k}>" for k in sorted((s.args or {}).keys())[:6]) or "(no args)"
            lines.append(f"{s.index}. Call `{s.tool}` {args}")
            if s.intent:
                lines.append(f"   - intent: {s.intent.strip()[:160]}")
            if s.outcome:
                lines.append(f"   - expected outcome: `{s.outcome.strip()[:160]}`")
        lines += ["", "## Verification", "", cand.verification, ""]
        if cand.pitfalls:
            lines += ["## Pitfalls observed", ""] + [f"- {p}" for p in cand.pitfalls] + [""]
        lines += [
            "## Usage", "", "```python",
            "from core.skill_forge import skill_forge",
            f"skill_forge.run('{cand.name}', task='...')   # or skill_use('{cand.name}', ...)",
            "```", "",
            "## Provenance", "",
            f"- session: `{cand.provenance.get('session', '')}`",
            f"- created: {cand.provenance.get('created')}",
            f"- trajectory hash: `{cand.provenance.get('hash')}`",
            f"- evaluation score: {cand.provenance.get('evaluation', {}).get('score', 'n/a')}",
            "",
        ]
        return "\n".join(lines)

    def skill_py(self, cand: SkillCandidate) -> str:
        steps = [s.to_dict() for s in cand.steps]
        return textwrap.dedent(
            f'''
    """Auto-distilled skill `{cand.name}` — generated by hermus-skill-forge.

    Replays the verified procedure through the Hermus tool registry. Each step may
    be overridden via `overrides={{tool_name: {{arg: value}}}}`; unknown kwargs are
    merged into every step so callers can re-parameterise the whole run.
    """
    from __future__ import annotations

    import json
    from typing import Any, Dict, List, Optional

    _STEPS_RAW = {json.dumps(json.dumps(steps, default=str))}
    STEPS: List[Dict[str, Any]] = json.loads(_STEPS_RAW)

    SKILL_META = {{
        "name": {cand.name!r},
        "description": {cand.description!r},
        "verification": {cand.verification!r},
        "inputs": {cand.inputs!r},
        "provenance": {cand.provenance!r},
    }}


    def plan() -> List[Dict[str, Any]]:
        """The distilled procedure (tool name + recorded arguments)."""
        return [dict(s) for s in STEPS]


    def run(task: str = "", query: str = "", overrides: Optional[Dict[str, Any]] = None,
            execute: bool = True, **context: Any) -> Dict[str, Any]:
        """Run the skill. `execute=False` returns the plan only (dry run)."""
        overrides = overrides or {{}}
        results: List[Dict[str, Any]] = []
        if not execute:
            return {{"success": True, "dry_run": True, "skill": SKILL_META["name"], "steps": plan()}}
        try:
            from core.tool_registry import tool_registry
        except Exception as e:  # registry unavailable → return the plan, never crash
            return {{"success": False, "error": f"tool registry unavailable: {{e}}", "steps": plan()}}
        for i, step in enumerate(plan(), 1):
            name = step.get("tool")
            args = {{k: v for k, v in (step.get("args") or {{}}).items()}}
            args.update(overrides.get(name, {{}}))
            for key, value in context.items():
                if isinstance(value, (str, int, float, bool)):
                    args.setdefault(key, value)
            if task and "task" not in args:
                args["task"] = task
            if query and "query" not in args:
                args["query"] = query
            try:
                out = tool_registry.execute(name, args)
            except Exception as e:
                out = {{"error": str(e)}}
            failed = isinstance(out, dict) and (
                out.get("success") is False or bool(out.get("error")) or bool(out.get("exception"))
            )
            results.append({{"step": i, "tool": name, "args": args, "result": out, "error": failed}})
            if failed:
                return {{"success": False, "skill": SKILL_META["name"], "stopped_at": i,
                         "steps": results, "hint": "recorded step failed; re-plan or override args"}}
        return {{"success": True, "skill": SKILL_META["name"], "steps": results,
                 "verification": SKILL_META["verification"]}}
    ''').lstrip("\n")

    def smoke_test(self, cand: SkillCandidate) -> str:
        return textwrap.dedent(
            f'''
    """Auto-generated validation for the distilled skill `{cand.name}`."""
    from pathlib import Path

    import importlib.util


    def _load():
        spec = importlib.util.spec_from_file_location(
            "skills.{cand.name}.skill", Path(__file__).parent / "skill.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


    def test_{cand.name}_shape():
        mod = _load()
        assert callable(mod.run), "skill must expose run()"
        steps = mod.plan()
        assert isinstance(steps, list) and steps, "skill must carry at least one step"
        assert all("tool" in s for s in steps)


    def test_{cand.name}_dry_run():
        mod = _load()
        res = mod.run(task="smoke", execute=False)
        assert res["success"] and res["dry_run"]
    ''').lstrip("\n")

    # -------------------------------------------------------------- validation
    def validate(self, skill_dir: Path, *, timeout: int = 25) -> dict[str, Any]:
        """Compile + import + smoke-test a skill in a sandboxed subprocess.

        Runs through ``core.sandbox`` when available so a bad distilled skill
        cannot hang or blow up the agent process.
        """
        report: dict[str, Any] = {"path": str(skill_dir), "checks": []}
        md = skill_dir / "SKILL.md"
        py = skill_dir / "skill.py"
        if not md.exists():
            return {**report, "valid": False, "error": "SKILL.md missing"}
        if not py.exists():
            return {**report, "valid": False, "error": "skill.py missing"}
        text = md.read_text(errors="ignore")
        report["checks"].append({"name": "frontmatter", "ok": text.startswith("---") and "---" in text[3:],
                                 "detail": "SKILL.md must start with YAML frontmatter"})
        for key in ("name:", "description:", "when_to_use:"):
            report["checks"].append({"name": f"frontmatter:{key[:-1]}", "ok": key in text.split("---")[1],
                                     "detail": f"required frontmatter key '{key[:-1]}'"})
        try:
            import py_compile

            py_compile.compile(str(py), doraise=True, cfile=str(skill_dir / "__pycache__" / "skill.pyc"))
            report["checks"].append({"name": "compile", "ok": True, "detail": ""})
        except Exception as e:
            return {**report, "valid": False, "error": f"skill.py does not compile: {e}"}

        probe = (
            "import importlib.util,sys,json;"
            f"spec=importlib.util.spec_from_file_location('sk', r'{py}');"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            "steps=m.plan();r=m.run(task='validate', execute=False);"
            "print(json.dumps({'ok':bool(r.get('success')),'steps':len(steps)}))"
        )
        rc, out, err = self._probe(probe, timeout=timeout)
        report["checks"].append({"name": "import+entrypoint", "ok": rc == 0 and '"ok": true' in out,
                                 "detail": (out or err)[:400]})
        if rc != 0:
            return {**report, "valid": False, "error": f"import failed: {(err or out)[:300]}"}
        report["valid"] = all(c["ok"] for c in report["checks"])
        return report

    def _probe(self, code: str, timeout: int = 25) -> tuple[int, str, str]:
        """Execute a validation snippet under resource limits (sandbox if present)."""
        try:
            from .sandbox import sandbox

            res = sandbox.run_python(code, timeout=timeout, purpose="skill-validation")
            return int(res.get("returncode", 1)), str(res.get("stdout", "")), str(res.get("stderr", ""))
        except Exception:
            pass
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout,
                cwd=str(self.skills_dir.parent),
            )
            return proc.returncode, proc.stdout, proc.stderr
        except Exception as e:
            return 1, "", str(e)

    # ------------------------------------------------------------------ install
    def install(self, cand: SkillCandidate, *, validate: bool = True) -> dict[str, Any]:
        target = self.skills_dir / cand.name
        version = 1
        if target.exists():
            prev = (self._registry().get("skills") or {}).get(cand.name) or {}
            try:
                version = int(prev.get("version") or 1)
            except Exception:
                version = 1
            same_content = bool(prev.get("hash")) and prev.get("hash") == cand.provenance.get("hash")
            if not same_content:
                # genuinely different procedure with a colliding name → keep both,
                # versioned side by side, instead of silently clobbering knowledge
                cand.name = slugify(f"{cand.name}_{cand.provenance.get('hash', 'x')[:6]}")
                target = self.skills_dir / cand.name
                version = 1
            else:
                version += 1  # re-harvest of the same trajectory → refresh in place
        staging = self.skills_dir / f".staging_{cand.name}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "SKILL.md").write_text(self.skill_md(cand))
            (staging / "skill.py").write_text(self.skill_py(cand))
            (staging / "test_skill.py").write_text(self.smoke_test(cand))
            report = self.validate(staging) if validate else {"valid": True, "skipped": True}
            if not report.get("valid"):
                self.quarantine_dir.mkdir(parents=True, exist_ok=True)
                bad = self.quarantine_dir / f"{cand.name}_{datetime.now().strftime('%H%M%S')}"
                shutil.move(str(staging), str(bad))
                try:
                    (bad / "VALIDATION_ERROR.txt").write_text(json.dumps(report, indent=2, default=str))
                except Exception:
                    pass
                return {"installed": False, "quarantined": str(bad), "report": report}
            if target.exists():
                shutil.rmtree(str(target), ignore_errors=True)
            staging.replace(target)
        except Exception as e:
            shutil.rmtree(str(staging), ignore_errors=True)
            return {"installed": False, "error": str(e)}
        finally:
            shutil.rmtree(str(staging), ignore_errors=True)

        reg = self._registry()
        reg.setdefault("skills", {})[cand.name] = {
            "path": str(target),
            "title": cand.title,
            "description": cand.description,
            "tools": [s.tool for s in cand.steps],
            "created": cand.provenance.get("created"),
            "source_session": cand.provenance.get("session"),
            "hash": cand.provenance.get("hash"),
            "evaluation": cand.provenance.get("evaluation", {}),
            "status": "active",
            "version": version,
            "runs": (reg.get("skills", {}).get(cand.name) or {}).get("runs", 0),
            "successes": (reg.get("skills", {}).get(cand.name) or {}).get("successes", 0),
        }
        cap = int(getattr(config, "skill_forge_max_skills", 200))
        if len(reg["skills"]) > cap:
            reg["skills"] = dict(list(reg["skills"].items())[-cap:])
        self._save_registry(reg)
        try:
            from .cache import skill_cache

            skill_cache.clear()
        except Exception:
            pass
        self._embed(cand, str(target))
        return {"installed": True, "name": cand.name, "path": str(target), "report": report}

    def _embed(self, cand: SkillCandidate, path: str) -> None:
        try:
            from .embeddings import embedding_store

            embedding_store.add_text(
                f"{cand.name}: {cand.description}\nWhen to use: {cand.when_to_use}",
                metadata={"type": "skill", "skill": cand.name, "path": path},
                source=f"skill:{cand.name}",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ harvest
    def harvest(
        self,
        goal: str,
        trajectory: Sequence[dict[str, Any]],
        *,
        verification: Optional[dict[str, Any]] = None,
        tool_results: Optional[Sequence[dict[str, Any]]] = None,
        session_id: str = "",
        final_answer: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Full pipeline: evaluate → extract → distill → dedupe → validate → install."""
        evaluation = evaluate_trajectory(
            trajectory, verification=verification, tool_results=tool_results, final_answer=final_answer
        )
        if not evaluation.harvest:
            return {"created": False, "stage": "evaluation", "evaluation": evaluation.to_dict()}
        steps = extract_steps(trajectory, tool_results)
        if len(steps) < 2:
            return {"created": False, "stage": "extraction",
                    "reason": f"only {len(steps)} distinct step(s) after collapsing retries",
                    "evaluation": evaluation.to_dict()}
        cand = self.distill(goal, steps, evaluation, session_id=session_id)
        similar = self.find_similar(cand.description)
        if similar and similar.get("name"):
            try:
                from .memory2 import memory2

                memory2.remember(
                    "procedural", cand.description,
                    importance=6.0, success=True, session=session_id,
                    metadata={"skill": similar["name"], "deduped": True},
                )
            except Exception:
                pass
            return {
                "created": False, "stage": "dedupe", "merged_into": similar["name"],
                "similarity": similar.get("similarity"), "name": cand.name,
                "evaluation": evaluation.to_dict(),
                "skill_md": cand.to_dict() if dry_run else None,
            }
        if dry_run:
            return {"created": False, "stage": "dry_run", "candidate": cand.to_dict(),
                    "skill_md": self.skill_md(cand), "evaluation": evaluation.to_dict()}
        result = self.install(cand)
        result["evaluation"] = evaluation.to_dict()
        result["steps"] = len(steps)
        # normalised verdict so callers (agent loop, CLI, /command) read one shape
        result["created"] = bool(result.get("installed"))
        result["stage"] = ("installed" if result["created"]
                           else "quarantined" if result.get("quarantined") else "install_failed")
        result.setdefault("name", cand.name)
        result.setdefault("path", str(self.skills_dir / cand.name))
        return result

    def run(self, name: str, **kwargs) -> dict[str, Any]:
        """Execute an installed distilled skill (used by the CLI + skill_use)."""
        py = self.skills_dir / name / "skill.py"
        if not py.exists():
            return {"success": False, "error": f"skill '{name}' not found"}
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(f"hermus_skill_{name}", str(py))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out = mod.run(**kwargs)
            try:
                self.record_outcome(name, success=bool(out.get("success")), note=str(out)[:200])
            except Exception:
                pass
            return out if isinstance(out, dict) else {"success": True, "result": out}
        except Exception as e:
            try:
                self.record_outcome(name, success=False, note=str(e)[:200])
            except Exception:
                pass
            return {"success": False, "error": str(e)}

    def record_outcome(self, name: str, success: bool, note: str = "") -> dict[str, Any]:
        """Feed the self-improvement loop: outcomes land in skill_usage + forge log."""
        try:
            from .skill_manager import skill_manager

            skill_manager.log_skill_usage(name, success, note)
        except Exception:
            pass
        # keep the registry counters current so `forge list`/stats can rank skills
        # and a persistently failing one can be spotted
        try:
            reg = self._registry()
            entry = (reg.get("skills") or {}).get(name)
            if entry is not None:
                entry["runs"] = int(entry.get("runs") or 0) + 1
                entry["successes"] = int(entry.get("successes") or 0) + (1 if success else 0)
                entry["last_outcome"] = {"success": bool(success), "note": (note or "")[:200],
                                         "ts": _now()}
                self._save_registry(reg)
        except Exception:
            pass
        path = self.skills_dir / "forge_log.jsonl"
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": _now(), "skill": name, "success": bool(success),
                                    "note": (note or "")[:300]}, default=str) + "\n")
        except Exception:
            pass
        return {"recorded": True, "skill": name, "success": success}

    def stats(self) -> dict[str, Any]:
        reg = self._registry()
        skills = list(reg.get("skills", {}).values())
        harvested = sum(1 for s in skills if s.get("created"))
        quarantined = []
        try:
            quarantined = [p.name for p in self.quarantine_dir.iterdir() if p.is_dir()]
        except Exception:
            pass
        outcomes: list[bool] = []
        try:
            for line in (self.skills_dir / "forge_log.jsonl").read_text().splitlines()[-200:]:
                outcomes.append(bool(json.loads(line).get("success")))
        except Exception:
            pass
        return {
            "registered_skills": len(skills),
            "harvested": harvested,
            "quarantined": len(quarantined),
            "quarantine_names": quarantined[:10],
            "recent_outcome_rate": (
                round(sum(1 for o in outcomes if o) / len(outcomes), 3) if outcomes else None
            ),
            "registry": str(self.registry_path),
            "skills_dir": str(self.skills_dir),
        }


def _yaml_str(v: Any) -> str:
    s = str(v if v is not None else "")
    s = s.replace("\n", " ").strip()
    if re.match(r"^[\w \-.,:()/]+$", s) and ":" not in s and not s.startswith("-"):
        return s
    return json.dumps(s, default=str)


def _frontmatter_summary(doc: str) -> str:
    """Pull name/description/when_to_use out of SKILL.md frontmatter."""
    if not doc.startswith("---"):
        return doc.splitlines()[0][:200] if doc.strip() else ""
    head = doc.split("---", 2)[1] if doc.count("---") >= 2 else ""
    bits = []
    for line in head.splitlines():
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key in ("name", "description", "when_to_use", "title"):
            val = val.strip().strip("\"'")
            if val:
                bits.append(val)
    return " ".join(bits)[:600] or head.strip()[:200]


def _parse_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if not brace:
            return None
        text = brace.group(0)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


skill_forge = SkillForge()
