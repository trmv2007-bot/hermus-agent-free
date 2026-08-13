"""CouncilSession — the council of AIs: convene, talk, vote, plan, execute, review.

Lifecycle (bounded and audited):
  1. convene()      — governor's difficulty decides roster size + rounds
  2. proposals()    — every member drafts its approach IN PARALLEL (own model/key)
  3. deliberate()   — members read each other's proposals and respond (Critic must
                      attach a concrete objection or explicit approval)
  4. vote()         — Judge scores proposals 0-10; Chair breaks ties
  5. finalize_plan()— Chair (or fallback) turns the winning proposal into a
                      structured Plan saved to data/counsel/plans/
  6. execute()      — plan steps run with real tools; failures trigger a bounded
                      mini-reconvene (Chair + Critic) to replan
  7. synthesize()   — Synthesizer merges plan + evidence into the final answer
  8. review()       — transcript saved; Meta-Counsel (meta.py) proposes self-upgrades

Everything degrades gracefully: any member/model failure falls back to text, and
any structural failure still yields a plain answer — the council never bricks a task.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import config
from ..llm import FreeLLM
from ..memory import memory
from ..reasoning.governor import governor
from ..reasoning.scaffold import Plan, PlanStep
from .constitution import constitution
from .members import CounselMember, build_roster, describe_roster

COUNCIL_DIR = "data/counsel"

# Roles that participate in debate (judge only votes; meta only reviews)
DEBATE_ROLES = ("chair", "researcher", "researcher2", "critic", "synthesizer")


class CouncilSession:
    def __init__(
        self,
        goal: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        difficulty: Optional[int] = None,
        max_members: Optional[int] = None,
        max_rounds: Optional[int] = None,
        execute: bool = True,
    ):
        self.goal = (goal or "").strip()
        if not self.goal:
            raise ValueError("CouncilSession needs a goal")
        self.session_id = session_id or f"council_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.model = model or config.model
        self.difficulty = difficulty or governor.classify_difficulty(self.goal)
        budget = governor.budget(self.difficulty)
        self.max_members = max_members or max(3, budget["max_members"])
        self.max_rounds = max_rounds or budget["max_rounds"]
        self.execute_plan_flag = execute
        self.doc = constitution.load()
        self.members: List[CounselMember] = []
        self.debaters: List[CounselMember] = []
        self.transcript: List[Dict] = []
        self.proposal_texts: Dict[str, str] = {}
        self.votes: List[Dict] = []
        self.plan: Optional[Plan] = None
        self.step_results: List[Dict] = []
        self.final_answer: str = ""
        self.replanned = False
        self.errors: List[str] = []
        self.task_id: Optional[str] = None
        self.rules = self.doc.get("rules", {})
        self.budget_doc = self.doc.get("budget", {})

    # ---------------------------------------------------------------- tracking

    def _track_start(self):
        try:
            from ..task_tracker import task_tracker

            self.task_id = task_tracker.add_task(
                task_id=self.session_id,
                task_type="council",
                description=self.goal[:100],
                model=",".join(m.model for m in self.members),
                agent=",".join(m.name for m in self.members),
            )
            for m in self.members:
                task_tracker.add_agent(
                    f"{self.session_id}_{m.role}",
                    m.name,
                    m.model,
                    persona=m.persona[:60],
                    task=self.goal[:100],
                )
        except Exception:
            self.task_id = None

    def _track(self, progress: str):
        try:
            from ..task_tracker import task_tracker

            task_tracker.update_task(self.task_id, progress=progress)
        except Exception:
            pass

    def _track_done(self, result: str):
        try:
            from ..task_tracker import task_tracker

            if self.task_id:
                task_tracker.complete_task(self.task_id, status="done", result=result[:200])
            for m in self.members:
                task_tracker.remove_agent(f"{self.session_id}_{m.role}", final_status="done")
        except Exception:
            pass

    # ---------------------------------------------------------------- lifecycle

    def run(self) -> Dict:
        """Full council lifecycle. Returns a summary dict (never raises)."""
        try:
            print(f"\n[⚖️ Counsel] Convening council for: {self.goal[:120]}")
            print(f"[⚖️ Counsel] difficulty={self.difficulty} | max_members={self.max_members} | rounds={self.max_rounds} | execute={self.execute_plan_flag}")

            self.convene()
            self.proposals()
            self.deliberate()
            self.vote()
            self.finalize_plan()
            if self.execute_plan_flag:
                self.execute()
            self.synthesize()
            self.save()

            summary = self.summary()
            self._maybe_review(summary)
            return summary
        except Exception as e:
            self.errors.append(str(e))
            print(f"[⚖️ Counsel] Fatal error: {e}")
            self.final_answer = self.final_answer or (
                f"Council could not complete ({e}). Partial transcript:\n"
                + "\n".join(f"{t.get('agent','?')}: {t.get('content','')[:200]}" for t in self.transcript[-6:])
            )
            return self.summary()

    def convene(self):
        self.members = build_roster(self.doc, self.max_members, model=self.model)
        self.debaters = [m for m in self.members if m.role in DEBATE_ROLES]
        print(f"[⚖️ Counsel] Roster: {describe_roster(self.members)}")
        self._add_turn("system", f"Council convened. Roster: {describe_roster(self.members)}")
        self._track_start()
        self._track("Convened; drafting proposals")

    # ---------------------------------------------------------------- talk

    def _add_turn(self, agent: str, content: str, round_num: int = 0, extra: Optional[Dict] = None):
        turn = {
            "agent": agent,
            "role": "council",
            "content": (content or "")[:2000],
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            **(extra or {}),
        }
        self.transcript.append(turn)
        return turn

    def _member_llm(self, member: CounselMember) -> FreeLLM:
        return member.llm()

    def _base_messages(self, member: CounselMember, extra_system: str = "") -> List[Dict]:
        rules_text = json.dumps(self.rules, indent=1)[:600]
        system = (
            f"{member.persona}\n\n"
            f"You are {member.name} on the Hermus Council. Task: {self.goal}\n"
            f"Other members: {describe_roster([m for m in self.members if m.name != member.name])}\n"
            f"Council rules:\n{rules_text}\n\n"
            "Rules of engagement:\n"
            "- Collaborate, do not compete. Build on ideas, disagree with evidence.\n"
            "- Be concise but concrete. No filler.\n"
            "- If you can use tools, prefer evidence over opinion.\n"
            f"{extra_system}"
        )
        return [{"role": "system", "content": system}]

    def proposals(self):
        """Round 1: every debater drafts its approach in parallel."""
        print(f"[⚖️ Counsel] Proposals from {len(self.debaters)} members (parallel)...")

        def draft(m: CounselMember) -> Dict:
            messages = self._base_messages(
                m,
                extra_system="\nYour task NOW: propose your approach to the task. End with 'PROPOSAL:' plus 3-6 concrete steps."
            )
            messages.append({"role": "user", "content": f"Task: {self.goal}\n\nPropose your approach (3-6 concrete steps)."})
            try:
                resp = self._member_llm(m).chat(messages)
                return {"member": m.name, "role": m.role, "content": resp.content or ""}
            except Exception as e:
                return {"member": m.name, "role": m.role, "content": f"(proposal failed: {e})"}

        with ThreadPoolExecutor(max_workers=max(2, len(self.debaters))) as ex:
            futures = [ex.submit(draft, m) for m in self.debaters]
            for f in as_completed(futures):
                r = f.result()
                self.proposal_texts[r["member"]] = r["content"]
                self._add_turn(r["member"], r["content"], round_num=1, extra={"role": r["role"], "model": self._model_for(r["member"])})
                print(f"\n[👤 {r['member']}] {r['content'][:280]}...")

    def _model_for(self, name: str) -> str:
        for m in self.members:
            if m.name == name:
                return m.model
        return self.model

    def deliberate(self):
        """Rounds 2..N: members read each other and respond; critic must take a side."""
        if self.max_rounds < 2 or len(self.debaters) < 2:
            return
        for round_num in range(2, self.max_rounds + 1):
            print(f"\n[⚖️ Counsel] Deliberation round {round_num}/{self.max_rounds}")
            self._track(f"Deliberation round {round_num}/{self.max_rounds}")
            for member in self.debaters:
                history = self._recent_transcript_text(limit=8)
                critic_rule = ""
                if member.role == "critic" and self.rules.get("critic_must_attach_evidence", True):
                    critic_rule = (
                        "\nYou MUST end with either 'OBJECTION:' and one concrete problem, "
                        "or 'APPROVAL:' and your reason."
                    )
                messages = self._base_messages(
                    member,
                    extra_system=f"\nDeliberation round {round_num}. Build on what others said. {critic_rule}",
                )
                messages.append(
                    {
                        "role": "user",
                        "content": f"Task: {self.goal}\n\nWhat the council said so far:\n{history}\n\nYour turn as {member.name}:",
                    }
                )
                try:
                    resp = self._member_llm(member).chat(messages)
                    content = resp.content or ""
                except Exception as e:
                    content = f"(deliberation failed: {e})"
                self._add_turn(member.name, content, round_num=round_num, extra={"model": member.model})
                print(f"\n[👤 {member.name} R{round_num}] {content[:280]}...")

    def _recent_transcript_text(self, limit: int = 8, max_len: int = 250) -> str:
        return "\n".join(
            f"[{t['agent']} R{t['round']}]: {t['content'][:max_len]}" for t in self.transcript[-limit:]
        )

    # ---------------------------------------------------------------- vote + plan

    def vote(self):
        """Judge scores proposals; majority/chair confirm the winner."""
        judge = next((m for m in self.members if m.role == "judge"), None)
        if not judge or not self.proposal_texts:
            return
        print("\n[⚖️ Counsel] Judge scoring proposals...")
        proposals_text = "\n\n".join(f"[{name}]:\n{content[:800]}" for name, content in self.proposal_texts.items())
        messages = [
            {"role": "system", "content": judge.persona + "\nYou score each proposal 0-10. Return ONLY JSON: {\"scores\": {\"<member>\": 0-10}, \"winner\": \"<member>\", \"reason\": \"...\"}"},
            {"role": "user", "content": f"Task: {self.goal}\n\nProposals:\n{proposals_text[:3000]}\n\nScore them."},
        ]
        try:
            resp = self._member_llm(judge).chat(messages)
            parsed = self._parse_vote_json(resp.content or "")
            if parsed:
                self.votes = [{"judge": judge.name, "scores": parsed["scores"], "winner": parsed["winner"], "reason": parsed["reason"][:400]}]
                self._add_turn(judge.name, json.dumps(parsed)[:800], round_num=99, extra={"model": judge.model})
                print(f"[👤 {judge.name}] winner={parsed['winner']} scores={parsed['scores']}")
                return
        except Exception as e:
            print(f"[⚖️ Counsel] Judge failed ({e}) - skipping formal vote")

    def _parse_vote_json(self, content: str) -> Optional[Dict]:
        import re

        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except Exception:
                return None
        if not isinstance(data, dict) or not isinstance(data.get("scores"), dict):
            return None
        winner = data.get("winner")
        if winner not in self.proposal_texts:
            winner = max(data["scores"], key=lambda k: data["scores"].get(k, 0)) if data["scores"] else next(iter(self.proposal_texts))
        return {"scores": data["scores"], "winner": winner, "reason": str(data.get("reason", ""))}

    def winner_proposal(self) -> str:
        if self.votes and self.votes[0].get("winner") in self.proposal_texts:
            return self.proposal_texts[self.votes[0]["winner"]]
        # No formal vote -> majority text pick: longest concrete proposal
        if self.proposal_texts:
            return max(self.proposal_texts.values(), key=lambda c: len(c))
        return self.goal

    def finalize_plan(self):
        """Chair turns the winning proposal into a structured Plan."""
        chair = next((m for m in self.members if m.role == "chair"), self.members[0] if self.members else None)
        winner_text = self.winner_proposal()
        plan = None
        if chair:
            messages = [
                {"role": "system", "content": (
                    "You are the Chair of the Hermus Council. Produce the final council plan.\n"
                    "Return ONLY JSON: {\"steps\": [{\"goal\": \"...\", \"action\": \"concrete action\", \"verify\": \"how to check\"}]}\n"
                    "3-6 steps, concrete and tool-friendly. No markdown."
                )},
                {"role": "user", "content": f"Task: {self.goal}\n\nWinning proposal:\n{winner_text[:2000]}\n\nVotes: {json.dumps(self.votes)[:400]}\n\nFinal plan JSON:"},
            ]
            try:
                resp = self._member_llm(chair).chat(messages)
                from ..reasoning.scaffold import PlanBuilder

                steps = PlanBuilder._parse_steps(resp.content or "")
                if steps:
                    plan = Plan(goal=self.goal, steps=steps, strategy="council", difficulty=self.difficulty, session_id=self.session_id)
            except Exception as e:
                print(f"[⚖️ Counsel] Chair plan parse failed ({e}) - fallback")
        if plan is None:
            # Fallback: bullets of the winning proposal become steps
            plan = self._fallback_plan(winner_text)
        plan.status = "active"
        self.plan = plan
        path = plan.save(config.resolve_path(f"{COUNCIL_DIR}/plans/{self.session_id}.json"))
        print(f"\n[📋 Counsel] Plan saved to {path}")
        print(plan.to_prompt())
        self._add_turn("chair", plan.to_prompt(), round_num=98, extra={"plan": True})

    def _fallback_plan(self, winner_text: str) -> Plan:
        import re

        bullets = [
            re.sub(r"^\s*(\d+[\.\):]|[-*])\s+", "", ln).strip()
            for ln in winner_text.splitlines()
            if re.match(r"^\s*(\d+[\.\):]|[-*])\s+", ln)
        ]
        from ..reasoning.scaffold import PlanBuilder

        steps = None
        if len(bullets) >= 2:
            steps = [PlanStep(goal=b[:200], action="investigate") for b in bullets[:6]]
        else:
            parts = re.split(r"(?<=[.!?])\s+|\band then\b|\bthen\b|;", winner_text)
            parts = [p.strip() for p in parts if p and len(p.strip()) > 15]
            if len(parts) >= 2:
                steps = [PlanStep(goal=p[:200], action="investigate") for p in parts[:6]]
        if steps is None:
            # Winning text too thin (e.g. degenerate model stub) -> plan from the goal
            steps = PlanBuilder._heuristic_steps(self.goal)
        return Plan(goal=self.goal, steps=steps, strategy="council", difficulty=self.difficulty, session_id=self.session_id)

    # ---------------------------------------------------------------- execute

    def execute(self):
        if not self.plan or not self.plan.steps:
            print("[⚖️ Counsel] No plan steps to execute")
            return
        executor = next((m for m in self.members if m.role in ("chair", "synthesizer")), self.members[0])
        print(f"\n[⚙️ Counsel] Executing {len(self.plan.steps)} plan steps (executor: {executor.name})")
        self._track(f"Executing {len(self.plan.steps)} steps")

        from ..tool_registry import tool_registry

        tool_registry.load()
        tools = tool_registry.get_definitions(allowed={"all"})
        failures = 0
        replans = 0
        reconvene_threshold = int(self.rules.get("reconvene_on_failures", 2))
        max_replans = int(self.rules.get("max_replans", 2))
        tool_rounds_cap = int(self.budget_doc.get("execution_tool_rounds_per_step", 3))

        for idx, step in enumerate(self.plan.steps):
            step.status = "active"
            messages = [
                {"role": "system", "content": (
                    f"You are the Hermus council executor ({executor.persona[:120]}).\n"
                    "Execute this ONE plan step using tools if needed. When the step is done, "
                    "state DONE followed by the result. Be concrete and cite what you actually obtained."
                )},
                {"role": "user", "content": f"Plan step {idx+1}/{len(self.plan.steps)}:\nGoal: {step.goal}\nAction: {step.action}\nVerify: {step.verify or 'n/a'}"},
            ]
            observations = []
            step_failed = False
            for round_i in range(tool_rounds_cap):
                try:
                    resp = executor.llm().chat(messages, tools=tools)
                except Exception as e:
                    observations.append(f"executor error: {e}")
                    step_failed = True
                    break
                if not resp.tool_calls:
                    break
                for tc in resp.tool_calls:
                    tname = tc.get("name")
                    targs = tc.get("arguments") or {}
                    if isinstance(targs, str):
                        try:
                            targs = json.loads(targs)
                        except Exception:
                            targs = {}
                    try:
                        print(f"[⚙️ {tname}({json.dumps(targs)[:120]})]")
                        result = tool_registry.execute(tname, targs)
                        text = json.dumps(result, default=str, ensure_ascii=False)[:600]
                    except Exception as e:
                        result = {"error": str(e)}
                        text = f"error: {e}"
                        failures += 1
                    # Lessons loop (Phase 3): council tool failures feed the prompt lessons
                    try:
                        if "error" in text[:300].lower() or "failed" in text[:300].lower():
                            from ..reasoning.lessons import lessons_store

                            lessons_store.distill_tool_failure(tname, text[:150])
                    except Exception:
                        pass
                    observations.append(f"tool {tname} -> {text}")
                    step.evidence.append(f"{tname}: {text[:150]}")
                    messages.append({"role": "user", "content": f"Tool {tname} returned: {text}"})

            result_text = observations[-1][:800] if observations else "no tools used"
            low = result_text.lower()
            if step_failed or "error" in low[:120] or "failed" in low[:120]:
                step.status = "failed"
                failures += 1
                self.step_results.append({"step": idx + 1, "goal": step.goal, "status": "failed", "evidence": step.evidence})
            else:
                step.status = "done"
                self.step_results.append({"step": idx + 1, "goal": step.goal, "status": "done", "evidence": step.evidence})
            self._add_turn("executor", f"Step {idx+1} [{step.status}]: {result_text}", round_num=97, extra={"step": idx + 1})

            # Mini-reconvene: too many failures -> Chair+Critic replan remaining steps
            if failures >= reconvene_threshold and replans < max_replans and idx + 1 < len(self.plan.steps):
                replans += 1
                self.replanned = True
                print(f"[⚖️ Counsel] {failures} failures -> mini-reconvene #{replans} to replan remaining steps")
                self._mini_reconvene(idx + 1, executor)

        self.plan.status = "done" if failures == 0 else "done_with_failures"
        self._track(f"Execution finished: {len(self.step_results)} steps, {failures} failures")

    def _mini_reconvene(self, from_step: int, executor: CounselMember):
        chair = next((m for m in self.members if m.role == "chair"), executor)
        critic = next((m for m in self.members if m.role == "critic"), None)
        remaining = "\n".join(f"{i+1}. {s.goal}" for i, s in enumerate(self.plan.steps[from_step:], start=from_step))
        prompt = (
            f"Task: {self.goal}\n\nSteps {from_step+1}.. of the plan are failing.\nRemaining steps:\n{remaining}\n\n"
            "Propose revised remaining steps: return ONLY JSON {\"steps\": [{\"goal\": \"...\", \"action\": \"...\", \"verify\": \"...\"}]}"
        )
        for member in [chair, critic] if critic else [chair]:
            if not member:
                continue
            try:
                resp = member.llm().chat([
                    {"role": "system", "content": member.persona + "\nYou are replanning failed council steps. Be pragmatic; prefer simpler actions and fallback tools."},
                    {"role": "user", "content": prompt},
                ])
                from ..reasoning.scaffold import PlanBuilder

                steps = PlanBuilder._parse_steps(resp.content or "")
                if steps:
                    new_steps = list(self.plan.steps[:from_step]) + steps
                    self.plan.steps = new_steps[:8]
                    self._add_turn(member.name, f"REPLAN: {json.dumps([s.to_dict() for s in steps])[:600]}", round_num=96)
                    print(f"[⚖️ Counsel] Replanned by {member.name}: {len(steps)} new steps")
                    return
            except Exception as e:
                print(f"[⚖️ Counsel] mini-reconvene failed ({e})")

    # ---------------------------------------------------------------- final answer

    def synthesize(self):
        synth = next((m for m in self.members if m.role == "synthesizer"), self.members[0] if self.members else None)
        plan_text = self.plan.to_prompt() if self.plan else "(no plan)"
        results_text = "\n".join(
            f"Step {r['step']} [{r['status']}]: {'; '.join(r['evidence'][:3]) or 'no evidence'}" for r in self.step_results
        ) or "(no execution)"
        if synth:
            messages = [
                {"role": "system", "content": synth.persona + "\nProduce the final answer to the user's task from the council's plan and evidence. Be complete and clear, no filler."},
                {"role": "user", "content": f"Task: {self.goal}\n\nPlan:\n{plan_text}\n\nExecution results:\n{results_text[:2500]}\n\nFinal answer:"},
            ]
            try:
                resp = synth.llm().chat(messages)
                self.final_answer = (resp.content or "").strip()
            except Exception as e:
                self.errors.append(str(e))
        if not self.final_answer:
            self.final_answer = (
                f"Council plan:\n{plan_text}\n\nExecution:\n{results_text}"
            )
        self._add_turn("synthesizer", self.final_answer, round_num=95)
        print(f"\n[✅ Counsel] Final answer ({len(self.final_answer)} chars)")

    # ---------------------------------------------------------------- persistence

    def save(self):
        base = config.resolve_path(COUNCIL_DIR)
        sessions_dir = base / "sessions"
        transcripts_dir = base / "transcripts"
        for d in (sessions_dir, transcripts_dir):
            d.mkdir(parents=True, exist_ok=True)
        (sessions_dir / f"{self.session_id}.json").write_text(json.dumps(self.summary(), indent=2))
        with open(transcripts_dir / f"{self.session_id}.jsonl", "w") as f:
            for t in self.transcript:
                f.write(json.dumps(t) + "\n")
        try:
            memory.add_session_message(self.session_id, "user", self.goal, metadata={"kind": "council"})
            memory.add_session_message(self.session_id, "assistant", self.final_answer[:2000], metadata={"kind": "council"})
        except Exception:
            pass
        self._track_done(self.final_answer)

    def summary(self) -> Dict:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "difficulty": self.difficulty,
            "max_members": self.max_members,
            "max_rounds": self.max_rounds,
            "members": [{"name": m.name, "role": m.role, "model": m.model} for m in self.members],
            "proposals": {k: v[:400] for k, v in self.proposal_texts.items()},
            "votes": self.votes,
            "plan": self.plan.to_dict() if self.plan else None,
            "step_results": self.step_results,
            "replanned": self.replanned,
            "final_answer": self.final_answer[:4000],
            "transcript_turns": len(self.transcript),
            "errors": self.errors,
            "timestamp": datetime.now().isoformat(),
        }

    def _maybe_review(self, summary: Dict):
        """Meta-Counsel reviews the session and proposes self-upgrades (Phase 2)."""
        if not config.counsel_auto_review:
            return
        try:
            from .meta import meta_counsel

            def review():
                try:
                    meta_counsel.review_session(summary)
                except Exception as e:
                    print(f"[🕯️ Meta-Counsel] review failed: {e}")

            threading.Thread(target=review, daemon=True).start()
        except Exception:
            pass
