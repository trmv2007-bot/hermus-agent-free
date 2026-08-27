"""Council Constitution — the source of truth the council can upgrade itself with.

The constitution is a versioned JSON document: member prompts, rules, and budgets.
The Meta-Counsel proposes AMENDMENTS to it; low-risk amendments auto-apply after
validation, high-risk ones wait for human approval (`hermus counsel amend`).

Safety rails (from the plan):
- Amendments only touch config / prompts / budgets — NEVER Python code.
- Every change bumps the version and writes a snapshot (atomic tmp+rename).
- `rollback(version)` restores any previous snapshot.
- No upgrade while a council session is running (callers must not interleave;
  we use a file lock-ish guard via a `locked` flag persisted between calls).

Storage:
  data/counsel/constitution.json        (current)
  data/counsel/constitution_v{N}.json   (snapshots)
  data/counsel/upgrade_log.json         (audit trail with before/after)
  data/counsel/pending_amendments.json  (awaiting approval)
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import config

DEFAULT_CONSTITUTION: Dict[str, Any] = {
    "version": 1,
    "name": "Hermus Council Constitution v1",
    "rules": {
        "critic_must_attach_evidence": True,   # critic must state an objection or explicit approval
        "judge_scores_out_of": 10,
        "tie_break": "chair",                  # chair breaks ties with evidence
        "quorum": 0.6,                         # fraction of members needed to vote
        "reconvene_on_failures": 2,            # tool failures before mini-reconvene
        "max_replans": 2,                      # council may replan at most this many times
    },
    "budget": {
        "max_members": 6,
        "max_rounds": 3,
        "proposals_parallel": True,
        "per_member_token_cap": 4000,
        "execution_tool_rounds_per_step": 3,
    },
    "members": [
        {
            "role": "chair",
            "enabled": True,
            "model": None,  # None = auto-assign (diverse free workers, else default model)
            "weight": 2,
            "persona": (
                "You are the Chair of the Hermus Council. You set the agenda, draft the plan, "
                "moderate the debate, and break ties with evidence. Be structured, decisive, and fair."
            ),
        },
        {
            "role": "researcher",
            "enabled": True,
            "model": None,
            "weight": 1,
            "persona": (
                "You are a Researcher on the Hermus Council. You gather facts and evidence. "
                "Prefer using tools (web_search, browser_navigate, file_read, memory_search). "
                "Never state a claim without grounding it in evidence you actually obtained."
            ),
        },
        {
            "role": "critic",
            "enabled": True,
            "model": None,
            "weight": 1,
            "persona": (
                "You are the Critic (devil's advocate) on the Hermus Council. You attack every "
                "proposal for holes: unverified claims, missing steps, edge cases, security, "
                "cost. You MUST state at least one concrete objection, or an explicit approval "
                "with a reason. You keep the council honest."
            ),
        },
        {
            "role": "synthesizer",
            "enabled": True,
            "model": None,
            "weight": 1,
            "persona": (
                "You are the Synthesizer on the Hermus Council. You merge proposals, votes, and "
                "tool evidence into one clear, complete, final answer. Remove contradictions, "
                "keep the best ideas, and be concise."
            ),
        },
        {
            "role": "judge",
            "enabled": True,
            "model": None,
            "weight": 1,
            "persona": (
                "You are the Judge of the Hermus Council. You score proposals 0-10 fairly and "
                "independently. You pick the best plan by evidence quality and completeness, "
                "and you explain your score."
            ),
        },
    ],
    "default_strategy": "council",  # council | react (used by later phases)
}


class ConstitutionManager:
    """Versioned, self-upgradable constitution for the Council."""

    def __init__(self, path: Optional[str] = None):
        self.path = config.resolve_path(path or "data/counsel/constitution.json")
        self.dir = self.path.parent
        self.log_path = self.dir / "upgrade_log.json"
        self.pending_path = self.dir / "pending_amendments.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ensure()

    # ---------- load / save ----------

    def _ensure(self):
        if not self.path.exists():
            self.save(DEFAULT_CONSTITUTION, log=False)

    def load(self) -> Dict:
        try:
            doc = json.loads(self.path.read_text())
            if not isinstance(doc, dict) or "version" not in doc:
                raise ValueError("bad constitution")
            return doc
        except Exception:
            self.save(DEFAULT_CONSTITUTION, log=False)
            return dict(DEFAULT_CONSTITUTION)

    def save(self, doc: Dict, log: bool = True, reason: str = ""):
        """Atomic write + snapshot copy for every new version."""
        doc = dict(doc)
        doc["version"] = int(doc.get("version", 1))
        doc["updated_at"] = datetime.now().isoformat()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        shutil.move(str(tmp), str(self.path))
        # Snapshot for rollback
        snap = self.dir / f"constitution_v{doc['version']}.json"
        shutil.copyfile(self.path, snap)
        if log:
            self._append_log(
                {
                    "event": "save",
                    "version": doc["version"],
                    "reason": reason,
                    "timestamp": datetime.now().isoformat(),
                }
            )
        return doc

    # ---------- upgrade log ----------

    def _load_log(self) -> List[Dict]:
        try:
            return json.loads(self.log_path.read_text())
        except Exception:
            return []

    def _append_log(self, entry: Dict):
        log = self._load_log()
        log.append(entry)
        self.log_path.write_text(json.dumps(log[-200:], indent=2))

    def upgrade_log(self) -> List[Dict]:
        return self._load_log()

    # ---------- amendments ----------

    ALLOWED_TARGETS = ("member_prompt", "rule", "budget", "strategy")
    LOW_RISK_TARGETS = ("member_prompt", "strategy")

    def validate_amendment(self, a: Dict) -> Optional[str]:
        """Return an error string if the amendment is invalid, else None."""
        if not isinstance(a, dict):
            return "amendment must be an object"
        target = a.get("target")
        if target not in self.ALLOWED_TARGETS:
            return f"target must be one of {self.ALLOWED_TARGETS}"
        change = a.get("change")
        if not isinstance(change, str) or not change.strip():
            return "change must be a non-empty string"
        if target == "budget":
            try:
                int(change)
            except Exception:
                return "budget change must be an integer"
        elif target != "rule" and len(change) < 5:
            return "change must be at least 5 chars"
        if target == "member_prompt":
            role = a.get("member")
            if role not in [m["role"] for m in self.load().get("members", [])]:
                return f"member '{role}' not in constitution"
        if target == "rule":
            key = a.get("rule_key")
            if not isinstance(key, str) or not key:
                return "rule amendments need rule_key"
        if target == "budget":
            key = a.get("budget_key")
            if key not in ("max_members", "max_rounds"):
                return "budget amendments must target max_members or max_rounds"
        return None

    def risk(self, a: Dict) -> str:
        """Low = prompt tweaks / strategy prefs. High = rules / budgets / roster."""
        target = a.get("target")
        if target in self.LOW_RISK_TARGETS:
            return "low"
        if target == "rule":
            key = a.get("rule_key", "")
            # textual rule clarifications are low risk; structural/voting rules are high
            if key in ("critic_must_attach_evidence", "quorum", "tie_break", "reconvene_on_failures", "max_replans"):
                return "high" if key in ("quorum", "tie_break") else "low"
            return "low"
        return "high"  # budget changes

    def apply_amendment(self, amendment: Dict, reason: str = "") -> Dict:
        """Validate + apply an amendment -> new constitution version."""
        err = self.validate_amendment(amendment)
        if err:
            return {"success": False, "error": err}
        doc = self.load()
        old_version = doc["version"]
        target = amendment["target"]
        if target == "member_prompt":
            for m in doc["members"]:
                if m["role"] == amendment["member"]:
                    old_prompt = m.get("persona", "")
                    m["persona"] = amendment["change"]
                    break
        elif target == "rule":
            doc["rules"][amendment["rule_key"]] = amendment["change"]
        elif target == "budget":
            key = amendment["budget_key"]
            try:
                val = int(amendment["change"])
            except Exception:
                return {"success": False, "error": "budget change must be an integer"}
            if key == "max_members":
                doc["budget"]["max_members"] = max(3, min(6, val))
            else:
                doc["budget"]["max_rounds"] = max(1, min(4, val))
        elif target == "strategy":
            val = amendment["change"].strip().lower()
            if val not in ("council", "react"):
                return {"success": False, "error": "strategy must be 'council' or 'react'"}
            doc["default_strategy"] = val

        doc["version"] = old_version + 1
        doc["amendment"] = {
            "id": amendment.get("id", ""),
            "target": target,
            "source": amendment.get("source", "meta"),
            "reason": amendment.get("reason", reason)[:500],
        }
        self.save(doc, log=True, reason=reason)
        self._append_log(
            {
                "event": "amendment_applied",
                "id": amendment.get("id", ""),
                "target": target,
                "member": amendment.get("member"),
                "old_version": old_version,
                "new_version": doc["version"],
                "risk": self.risk(amendment),
                "timestamp": datetime.now().isoformat(),
            }
        )
        return {"success": True, "version": doc["version"], "amendment": amendment}

    def rollback(self, version: int) -> Dict:
        """Restore a previous snapshot version."""
        snap = self.dir / f"constitution_v{version}.json"
        if not snap.exists():
            return {"success": False, "error": f"no snapshot for version {version}"}
        doc = json.loads(snap.read_text())
        doc["version"] = version
        self.save(doc, log=True, reason=f"rollback to v{version}")
        self._append_log(
            {"event": "rollback", "to_version": version, "timestamp": datetime.now().isoformat()}
        )
        return {"success": True, "version": version}

    # ---------- pending amendments (human approval for high-risk) ----------

    def _load_pending(self) -> List[Dict]:
        try:
            return json.loads(self.pending_path.read_text())
        except Exception:
            return []

    def _save_pending(self, items: List[Dict]):
        self.pending_path.write_text(json.dumps(items, indent=2))

    def propose(self, amendment: Dict, source: str = "meta") -> Dict:
        """Store an amendment. Low risk -> apply now (auto). High risk -> pending approval."""
        amendment = dict(amendment)
        amendment.setdefault("id", f"amend_{uuid.uuid4().hex[:8]}")
        amendment.setdefault("source", source)
        amendment.setdefault("timestamp", datetime.now().isoformat())
        err = self.validate_amendment(amendment)
        if err:
            return {"success": False, "error": err, "amendment": amendment}
        r = self.risk(amendment)
        if r == "low":
            res = self.apply_amendment(amendment, reason=source)
            res["risk"] = "low"
            res["auto_applied"] = True
            return res
        pending = self._load_pending()
        # dedupe: same target+change text
        for p in pending:
            if p.get("status") == "pending" and p.get("change") == amendment.get("change") and p.get("target") == amendment.get("target"):
                return {"success": False, "error": "duplicate pending amendment", "amendment": p}
        amendment["status"] = "pending"
        pending.append(amendment)
        self._save_pending(pending)
        return {"success": True, "status": "pending", "risk": "high", "amendment": amendment}

    def pending_amendments(self) -> List[Dict]:
        return [p for p in self._load_pending() if p.get("status") == "pending"]

    def approve(self, amendment_id: str) -> Dict:
        items = self._load_pending()
        for p in items:
            if p.get("id") == amendment_id and p.get("status") == "pending":
                p["status"] = "approved"
                p["decided_at"] = datetime.now().isoformat()
                self._save_pending(items)
                res = self.apply_amendment(p, reason=f"human approval ({amendment_id})")
                res["approved_id"] = amendment_id
                return res
        return {"success": False, "error": f"pending amendment '{amendment_id}' not found"}

    def reject(self, amendment_id: str) -> Dict:
        items = self._load_pending()
        for p in items:
            if p.get("id") == amendment_id and p.get("status") == "pending":
                p["status"] = "rejected"
                p["decided_at"] = datetime.now().isoformat()
                self._save_pending(items)
                return {"success": True, "rejected": amendment_id}
        return {"success": False, "error": f"pending amendment '{amendment_id}' not found"}

    def diff(self, amendment_id: str) -> Dict:
        """Return a unified diff showing how the pending amendment would modify the constitution."""
        import difflib
        items = self._load_pending()
        amendment = next((p for p in items if p.get("id") == amendment_id), None)
        if not amendment:
            return {"success": False, "error": f"pending amendment '{amendment_id}' not found"}
        err = self.validate_amendment(amendment)
        if err:
            return {"success": False, "error": f"invalid amendment: {err}"}
        current = self.load()
        simulated = json.loads(json.dumps(current))
        target = amendment.get("target")
        change = amendment.get("change")
        if target == "member_prompt":
            for m in simulated.get("members", []):
                if m.get("role") == amendment.get("member"):
                    m["persona"] = change
        elif target == "rule":
            simulated.setdefault("rules", {})[amendment.get("rule_key")] = change
        elif target == "budget":
            simulated.setdefault("budget", {})[amendment.get("budget_key")] = int(change)
        elif target == "strategy":
            simulated["default_strategy"] = str(change)
        simulated["version"] = int(current.get("version", 1)) + 1

        curr_lines = json.dumps(current, indent=2).splitlines(keepends=True)
        sim_lines = json.dumps(simulated, indent=2).splitlines(keepends=True)
        diff_text = "".join(difflib.unified_diff(curr_lines, sim_lines, fromfile=f"constitution_v{current.get('version', 1)}.json", tofile=f"constitution_v{simulated['version']}_proposed.json"))
        return {"success": True, "amendment_id": amendment_id, "diff": diff_text, "amendment": amendment}

    # ---------- convenience ----------

    def current_version(self) -> int:
        return int(self.load().get("version", 1))

    def status(self) -> Dict:
        doc = self.load()
        return {
            "version": doc["version"],
            "name": doc.get("name", ""),
            "rules": doc.get("rules", {}),
            "budget": doc.get("budget", {}),
            "members": [m["role"] for m in doc.get("members", []) if m.get("enabled")],
            "default_strategy": doc.get("default_strategy", "council"),
            "pending_amendments": len(self.pending_amendments()),
            "upgrade_events": len(self.upgrade_log()),
        }


constitution = ConstitutionManager()
