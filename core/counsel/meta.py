"""Meta-Counsel — the council that improves the council.

After a session, the Meta-Counsel reads the transcript and outcome and proposes
AMENDMENTS to the constitution (member prompts, rules, budgets, strategy).
Low-risk amendments auto-apply after validation; high-risk ones wait for human
approval via `hermus counsel amend list|approve|reject`. Every change is
versioned and rollback-able — the system literally upgrades itself, safely.

Also feeds the existing self-improvement loop: reflection mistakes become
amendment proposals so yesterday's errors shape tomorrow's council.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Optional

from ..config import config
from .constitution import constitution

_META_SYSTEM = """You are the Meta-Counsel of the Hermus Council. After a council session you review the transcript and outcome, then propose how the council should upgrade ITSELF.

Propose 1-3 AMENDMENTS. Return ONLY JSON:
{"amendments": [
  {
    "target": "member_prompt" | "rule" | "budget" | "strategy",
    "member": "<role>",          // required if target=member_prompt
    "rule_key": "<rule>",        // required if target=rule (critic_must_attach_evidence, quorum, tie_break, reconvene_on_failures, max_replans)
    "budget_key": "max_members"|"max_rounds",  // required if target=budget
    "change": "the new prompt text / rule value / integer / 'council'|'react'",
    "reason": "why this makes the council better"
  }
]}

Rules:
- Improvements must be concrete and grounded in what happened in the session.
- Never propose changes to code. Only prompts, rules, budgets, strategy.
- Prefer member_prompt and rule tweaks; budget changes only for repeated failure/timeouts.
- No duplicate or trivial amendments.
"""


class MetaCounsel:
    def __init__(self):
        self.log_path = config.resolve_path("data/counsel/meta_reviews.json")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_log()

    def _ensure_log(self):
        if not self.log_path.exists():
            self.log_path.write_text("[]")

    def _load_log(self) -> list[dict]:
        try:
            return json.loads(self.log_path.read_text())
        except Exception:
            return []

    def _append_log(self, entry: dict):
        log = self._load_log()
        log.append(entry)
        self.log_path.write_text(json.dumps(log[-100:], indent=2))

    # ------------------------------------------------------------ session review

    def review_session(self, session_summary: dict) -> dict:
        """1 free LLM call: read the transcript tail + outcome -> propose amendments."""
        if not session_summary:
            return {"proposed": 0, "message": "no session to review"}
        goal = session_summary.get("goal", "")
        outcome = session_summary.get("final_answer", "")[:1500]
        errors = session_summary.get("errors", [])
        failures = [r for r in session_summary.get("step_results", []) if r.get("status") == "failed"]
        transcript = "\n".join(
            f"[{t.get('agent','?')} R{t.get('round',0)}]: {t.get('content','')[:200]}"
            for t in self._load_transcript(session_summary.get("session_id", ""))[-8:]
        )
        votes_text = json.dumps(session_summary.get("votes", []))[:400]

        prompt = (
            f"Task: {goal}\n"
            f"Votes: {votes_text}\n"
            f"Failed steps: {len(failures)} - {json.dumps(failures[:2])[:400]}\n"
            f"Errors: {json.dumps(errors)[:400]}\n"
            f"Transcript tail:\n{transcript or '(none)'}\n"
            f"Final answer: {outcome[:800]}\n\n"
            "Propose amendments to make the next council session better."
        )
        proposed = []
        try:
            from ..models import get_model_gateway

            resp = get_model_gateway().chat([{"role": "system", "content": _META_SYSTEM}, {"role": "user", "content": prompt}], model=config.model)
            proposed = self._parse_amendments(resp.content or "")
        except Exception as e:
            print(f"[🕯️ Meta-Counsel] review LLM failed: {e}")

        results = []
        for a in proposed:
            results.append(constitution.propose(a, source="meta_counsel"))

        entry = {
            "session_id": session_summary.get("session_id", ""),
            "goal": goal[:200],
            "proposed_count": len(results),
            "auto_applied": [r for r in results if r.get("auto_applied")],
            "pending": [r.get("amendment") for r in results if r.get("status") == "pending"],
            "timestamp": datetime.now().isoformat(),
        }
        self._append_log(entry)
        if results:
            print(f"[🕯️ Meta-Counsel] proposed {len(results)} amendment(s): "
                  f"{sum(1 for r in results if r.get('auto_applied'))} auto-applied, "
                  f"{sum(1 for r in results if r.get('status') == 'pending')} pending approval")
        return {"proposed": len(results), "results": results, "entry": entry}

    def _load_transcript(self, session_id: str) -> list[dict]:
        if not session_id:
            return []
        path = config.resolve_path(f"data/counsel/transcripts/{session_id}.jsonl")
        try:
            return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        except Exception:
            return []

    def _parse_amendments(self, content: str) -> list[dict]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return []
            try:
                data = json.loads(m.group(0))
            except Exception:
                return []
        items = data.get("amendments") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out = []
        for a in items:
            if not isinstance(a, dict):
                continue
            a = dict(a)
            a.setdefault("id", f"amend_{uuid.uuid4().hex[:8]}")
            a.setdefault("timestamp", datetime.now().isoformat())
            if not a.get("reason"):
                a["reason"] = "meta-counsel session review"
            out.append(a)
        return out[:3]

    # ------------------------------------------------------------ reflection hook

    def propose_from_reflection(self, reflection: dict, improvements: Optional[dict] = None) -> dict:
        """Self-improvement loop -> amendment proposals (no extra LLM call).

        Converts detected mistakes into concrete, deduped amendment candidates:
        - user corrections  -> critic prompt must demand evidence
        - tool failures     -> rule to prefer fallback tools / replan earlier
        - skill failures    -> reviewer role prompt tweak
        """
        mistakes = reflection.get("mistakes", [])
        if not mistakes:
            return {"proposed": 0}
        proposed = []
        text = " ".join(mistakes).lower()
        if any(k in text for k in ("correction", "wrong", "incorrect")):
            proposed.append({
                "target": "member_prompt",
                "member": "critic",
                "change": (
                    "You are the Critic (devil's advocate) on the Hermus Council. You attack every "
                    "proposal for holes: unverified claims, missing steps, edge cases, security, "
                    "cost. You MUST state at least one concrete objection, or an explicit approval "
                    "with a reason. When the user previously corrected the council, check the "
                    "corrected area FIRST and require evidence for every claim you endorse."
                ),
                "reason": "User corrections detected in reflection; critic must verify corrected areas and demand evidence.",
            })
        if any(k in text for k in ("tool", "failed", "timeout", "error")):
            proposed.append({
                "target": "rule",
                "rule_key": "reconvene_on_failures",
                "change": "1",
                "reason": "Tool failures detected in reflection; reconvene the council sooner after a failure.",
            })
        results = []
        for a in proposed:
            results.append(constitution.propose(a, source="reflection"))
        applied = [r for r in results if r.get("success")]
        self._append_log({
            "source": "reflection",
            "proposed_count": len(results),
            "applied": [r.get("version") for r in applied],
            "timestamp": datetime.now().isoformat(),
        })
        if applied:
            print(f"[🕯️ Meta-Counsel] reflection -> {len(applied)} amendment(s) applied (version "
                  f"{constitution.current_version()})")
        return {"proposed": len(results), "applied": applied}

    # ------------------------------------------------------------ CLI support

    def status(self) -> dict:
        return {
            "reviews_logged": len(self._load_log()),
            "constitution": constitution.status(),
            "pending_amendments": constitution.pending_amendments(),
        }


meta_counsel = MetaCounsel()
