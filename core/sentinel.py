"""Sentinel — the always-alert core (the FAIRY half of JARVIS × FAIRY).

The ambient loop polls every 15-30s and only observes. The sentinel is the
*instant* layer: it subscribes to the canonical EventBus and reacts **inline**
in the publisher's thread (``EventBus.publish`` fans out synchronously), so a
critical event is acted on in milliseconds — not at the next tick.

Safety contract (same invariants as the roadmap): the sentinel NEVER executes
tools itself. It can only:

* publish its own ``sentinel.decision`` events on the bus,
* enqueue jobs through the canonical JobQueue (the same path proactive rules
  use — approval / red-line / emergency-stop stay authoritative upstream),
* send notifications through configured channels (Telegram, desktop
  ``notify-send``, and always the local ``alerts.jsonl`` file),
* write attention digests / learned-rule proposals into memory + files
  (the "learning about my things" loop).

Decision per event — ``ignore`` / ``note`` / ``enqueue`` / ``alert`` — from a
salience score in [0, 1]: base weight by event type, +severity, +failure
status, +novelty (fingerprint dedupe so repeats don't re-alert), and a boost
when the event overlaps the user's current focus/goals (personal context).
Optionally, events above the triage threshold get a one-shot LLM triage
("should FAIRY act on this right now?") using any configured model
(``--model auto`` / role / provider/model / mock/mock — same resolution as the
harness). Unparseable or offline LLM replies fall back to ``note`` — never to
an automatic action.

Rule learning: when the sentinel acts (alert/enqueue) on the same event pattern
``rule_propose_after`` times, it writes a *proposal* to
``data/sentinel/proposed_rules.json`` and emits ``sentinel.rule_proposed``.
Proposals are never enabled by themselves — a human approves them with
``hermus sentinel approve <id>``, which adds a durable, opt-in
``ProactiveAutomation`` rule.

The same class drives the harness: ``run_sentinel_task`` feeds a task's events
through an isolated sentinel and writes ``sentinel_decisions.jsonl`` /
``enqueued.jsonl`` / ``alerts.jsonl`` into the task workspace, so suite checks
can verify the always-on behavior itself (see ``harness/suites/sentinel.json``
and ``hermus harness run sentinel``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config
from .log import get_logger

logger = get_logger(__name__)

# Base salience weights, matched by event-type prefix (most specific first).
BASE_WEIGHTS: list[tuple[str, float]] = [
    ("incident", 0.95),
    ("command.failed", 0.8),
    ("job.failed", 0.8),
    ("mission.failed", 0.85),
    ("error", 0.75),
    ("approval.required", 0.65),
    ("attention.raised", 0.7),
    ("watchdog", 0.6),
    ("harness.regression", 0.85),
    ("harness.run.failed", 0.55),
    ("harness.run.completed", 0.2),
    ("circuit", 0.55),
    ("signal", 0.45),
    ("attention.cleared", 0.15),
    ("state.changed", 0.08),
    ("presence", 0.05),
    ("heartbeat", 0.02),
]
DEFAULT_WEIGHT = 0.2

SEVERITY_BONUS = {"critical": 0.3, "high": 0.2, "warn": 0.1, "warning": 0.1, "info": 0.0, "low": 0.0}
FAILED_STATUS_BONUS = 0.15
FOCUS_BOOST = 0.2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _cfg(name: str, default: Any) -> Any:
    """Config value with env override (HERMUS_SENTINEL_*)."""
    env = os.environ.get(f"HERMUS_SENTINEL_{name.upper()}")
    if env is not None:
        try:
            return type(default)(env)
        except Exception:
            return default
    return getattr(config, f"sentinel_{name}", default)


def thresholds() -> dict[str, float]:
    return {
        "alert": float(_cfg("alert_threshold", 0.75)),
        "enqueue": float(_cfg("enqueue_threshold", 0.5)),
        "note": float(_cfg("note_threshold", 0.25)),
        "triage": float(_cfg("llm_triage_threshold", 0.45)),
    }


def envelope_from_dict(d: dict[str, Any]):
    """Build a canonical EventEnvelope from a plain dict (suite/CLI friendly)."""
    from .contracts.events import EventEnvelope

    d = dict(d or {})
    args = d.pop("args", None)
    if args is not None and "args_redacted" not in d:
        d["args_redacted"] = args if isinstance(args, dict) else {"value": args}
    allowed = {f for f in EventEnvelope.__dataclass_fields__}  # type: ignore[attr-defined]
    return EventEnvelope(**{k: v for k, v in d.items() if k in allowed})


def _base_weight(event_type: str) -> float:
    t = str(event_type or "")
    for prefix, w in BASE_WEIGHTS:
        if t == prefix or t.startswith(prefix + ".") or t.startswith(prefix):
            return w
    return DEFAULT_WEIGHT


def focus_keywords() -> list[str]:
    """Keywords from the user's current focus/goals (personal context)."""
    try:
        from .personal_context import PersonalContext

        snap = PersonalContext().snapshot()
        words: list[str] = []
        if snap.current_focus:
            words.append(str(snap.current_focus))
        for g in snap.goals or []:
            words.append(str(g.get("title") if isinstance(g, dict) else g))
        kw = []
        for w in words:
            kw.extend(t.strip().lower() for t in w.split() if len(t.strip()) >= 4)
        return kw
    except Exception:
        return []


def default_recorder(path: str | Path) -> Callable[[dict], None]:
    """Append JSON lines to a file (daemon default: data/sentinel/decisions.jsonl)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def _rec(decision: dict) -> None:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision, default=str) + "\n")

    return _rec


def proposals_path() -> Path:
    return config.resolve_path("data/sentinel/proposed_rules.json")


def list_proposals() -> list[dict[str, Any]]:
    p = proposals_path()
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def approve_proposal(proposal_id: str) -> dict[str, Any]:
    """Human-approved: turn a sentinel proposal into a durable proactive rule."""
    from .proactive import ProactiveAutomation

    proposals = list_proposals()
    prop = next((p for p in proposals if p.get("id") == proposal_id), None)
    if prop is None:
        return {"ok": False, "error": f"proposal '{proposal_id}' not found"}
    if prop.get("approved"):
        return {"ok": False, "error": "already approved", "rule_id": prop.get("rule_id")}
    automation = ProactiveAutomation()
    rule = automation.add_rule(
        name=prop["name"],
        event_type=prop["event_type"],
        action_type="runtime.turn",
        task=prop["task"],
        enabled=True,
        cooldown_seconds=float(prop.get("cooldown_seconds", 60)),
        filters=prop.get("filters") or None,
    )
    prop["approved"] = True
    prop["rule_id"] = rule.id
    proposals_path().write_text(json.dumps(proposals, indent=2, default=str))
    return {"ok": True, "rule_id": rule.id, "proposal": prop}


class Sentinel:
    """Event-driven alert/act core. Injectable deps make it testable + harnessable."""

    def __init__(
        self,
        *,
        bus=None,
        enqueue: Callable[[str, dict], Any] | None = None,
        notify: Callable[[str, str], Any] | None = None,
        llm: Any = None,
        model: str | None = None,
        use_llm_triage: bool = True,
        recorder: Callable[[dict], None] | None = None,
        focus_provider: Callable[[], list[str]] | None = None,
        alert_threshold: float | None = None,
        enqueue_threshold: float | None = None,
        note_threshold: float | None = None,
        triage_threshold: float | None = None,
        proposals: str | Path | None = None,
        rule_propose_after: int | None = None,
    ) -> None:
        self.bus = bus
        self._enqueue = enqueue
        self._notify = notify
        self.llm = llm
        self.model = model
        self.use_llm_triage = use_llm_triage and llm is not None
        self._recorder = recorder
        self._focus_provider = focus_provider or focus_keywords
        th = thresholds()
        self.alert_threshold = alert_threshold if alert_threshold is not None else th["alert"]
        self.enqueue_threshold = enqueue_threshold if enqueue_threshold is not None else th["enqueue"]
        self.note_threshold = note_threshold if note_threshold is not None else th["note"]
        self.triage_threshold = triage_threshold if triage_threshold is not None else th["triage"]
        self._proposals_path = Path(proposals) if proposals else proposals_path()
        self.rule_propose_after = int(rule_propose_after if rule_propose_after is not None else _cfg("rule_propose_after", 3))
        self._pattern_counts: dict[str, int] = {}
        self._lock = threading.RLock()
        self._seen: set[str] = set()
        self.decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------ salience

    def _fingerprint(self, env) -> str:
        payload = json.dumps(env.args_redacted or {}, sort_keys=True, default=str)[:300]
        raw = f"{env.type}|{env.command}|{env.status}|{payload}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _pattern(self, env) -> str:
        return f"{env.type}|{env.command}"

    def salience(self, env) -> dict[str, Any]:
        reasons: list[str] = []
        score = _base_weight(env.type)
        reasons.append(f"base[{env.type}]={score:.2f}")
        args = env.args_redacted or {}
        sev = str(args.get("severity") or env.error_code or "").lower()
        if sev in SEVERITY_BONUS and SEVERITY_BONUS[sev]:
            score += SEVERITY_BONUS[sev]
            reasons.append(f"severity[{sev}]=+{SEVERITY_BONUS[sev]:.2f}")
        if str(env.status or "").lower() in ("failed", "error"):
            score += FAILED_STATUS_BONUS
            reasons.append(f"status[failed]=+{FAILED_STATUS_BONUS:.2f}")
        blob = json.dumps(args, default=str).lower()
        hits = [k for k in self._focus_provider() if k and k in blob]
        if hits:
            score += FOCUS_BOOST
            reasons.append(f"focus_match={hits[:3]} +{FOCUS_BOOST:.2f}")
        return {"score": round(min(1.0, score), 3), "reasons": reasons}

    # ------------------------------------------------------------ decisions

    def _default_enqueue(self, kind: str, payload: dict) -> Any:
        from gateway.queue import job_queue

        return job_queue.submit(kind, payload, session_key="sentinel").brief()

    def _default_notify(self, text: str, severity: str) -> Any:
        """Notify chain: always log locally -> Telegram if configured -> desktop notify-send."""
        result: dict[str, Any] = {"ok": False, "channel": "none"}
        try:
            default_recorder(config.resolve_path("data/sentinel/alerts.jsonl"))(
                {"text": text, "severity": severity, "ts": _now_iso()}
            )
            result["logged"] = True
        except Exception:
            pass
        try:
            from gateway.channels import get_telegram_token, telegram_send_message

            token = get_telegram_token()
            chat_id = getattr(config, "telegram_chat_id", "") or ""
            if token and chat_id:
                r = telegram_send_message(chat_id, f"⚡ HERMUS sentinel [{severity}]: {text}", token=token)
                result.update(ok=True, channel="telegram", telegram=r)
                return result
        except Exception as e:
            result["telegram_error"] = f"{type(e).__name__}: {e}"
        try:
            # desktop push only makes sense in a graphical session; headless
            # (tests, systemd without DISPLAY) skips it instantly.
            if shutil.which("notify-send") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                subprocess.run(
                    ["notify-send", "HERMUS sentinel", f"[{severity}] {text[:200]}"],
                    timeout=5,
                    capture_output=True,
                )
                result.update(ok=True, channel="notify-send")
                return result
        except Exception as e:
            result["notify_send_error"] = f"{type(e).__name__}: {e}"
        if result.get("logged"):
            result.update(ok=True, channel="alerts.jsonl")
        else:
            result["reason"] = "no telegram config, no notify-send, log failed"
        return result

    def _llm_triage(self, env, score: float) -> dict[str, Any]:
        """One-shot LLM decision, hard-bounded in time. Falls back to a
        conservative note on any failure or timeout — triage must never stall
        the event path (offline hosts would otherwise block on network)."""
        from concurrent.futures import ThreadPoolExecutor

        timeout_s = float(_cfg("triage_timeout_seconds", 4))
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(self._llm_triage_call, env, score)
                return fut.result(timeout=timeout_s)
        except Exception as e:
            reason = f"llm triage {'timed out' if 'TimeoutError' in type(e).__name__ else 'failed'}: {type(e).__name__}"
            return {"action": "note", "reason": reason, "llm": True}

    def _llm_triage_call(self, env, score: float) -> dict[str, Any]:
        try:
            from .harness.verify import _extract_json  # reuse the harness JSON extractor

            prompt = (
                "You are FAIRY, the proactive sentinel of HERMUS. Decide whether the assistant "
                "should act on this event RIGHT NOW. Reply with ONLY a JSON object: "
                '{"act": true|false, "action": "alert"|"enqueue"|"note"|"ignore", "reason": "..."}. '
                f"Event type: {env.type}. Command: {env.command}. Status: {env.status}. "
                f"Args: {json.dumps(env.args_redacted or {}, default=str)[:600]}. Salience: {score}."
            )
            resp = self.llm.chat([{"role": "user", "content": prompt}])
            obj = _extract_json(getattr(resp, "content", "") or "")
            if isinstance(obj, dict) and "action" in obj:
                action = str(obj.get("action") or "note").lower()
                if action not in ("alert", "enqueue", "note", "ignore"):
                    action = "note"
                return {"action": action, "reason": str(obj.get("reason") or "")[:200], "llm": True}
            return {"action": "note", "reason": "llm reply not parseable; conservative note", "llm": True}
        except Exception as e:
            return {"action": "note", "reason": f"llm triage failed: {type(e).__name__}", "llm": True}

    # ------------------------------------------------------------ rule learning

    def _note_pattern(self, env, action: str) -> dict[str, Any] | None:
        """Count acted-on patterns; propose a durable rule after N occurrences."""
        # alert/enqueue/note all count: a de-escalated repeat is still a
        # recurring pattern the human may want as a durable rule.
        if action not in ("alert", "enqueue", "note"):
            return None
        pat = self._pattern(env)
        with self._lock:
            self._pattern_counts[pat] = self._pattern_counts.get(pat, 0) + 1
            count = self._pattern_counts[pat]
            if count < self.rule_propose_after:
                return None
            try:
                existing_rows = json.loads(self._proposals_path.read_text(encoding="utf-8"))
                existing_rows = existing_rows if isinstance(existing_rows, list) else []
            except Exception:
                existing_rows = []
            if pat in {p.get("pattern") for p in existing_rows}:
                return None
            proposal = {
                "id": f"prop_{hashlib.sha1(pat.encode()).hexdigest()[:10]}",
                "pattern": pat,
                "event_type": str(env.type),
                "name": f"sentinel: {env.type} {env.command}".strip()[:80],
                "task": f"Handle repeated {env.type} '{env.command}' (seen {count}x) — sentinel proposal, approve to enable",
                "action_type": "runtime.turn",
                "cooldown_seconds": 300,
                "count": count,
                "approved": False,
                "ts": _now_iso(),
            }
            try:
                existing_rows.append(proposal)
                self._proposals_path.parent.mkdir(parents=True, exist_ok=True)
                self._proposals_path.write_text(json.dumps(existing_rows, indent=2, default=str))
            except Exception as e:
                logger.warning(f"[Sentinel] could not persist rule proposal: {e}")
                return None
        if self.bus is not None and not str(env.type).startswith("sentinel."):
            try:
                self.bus.publish(
                    envelope_from_dict(
                        {
                            "type": "sentinel.rule_proposed",
                            "command": "rule_proposal",
                            "status": "succeeded",
                            "args": {"proposal_id": proposal["id"], "pattern": pat, "count": count},
                        }
                    )
                )
            except Exception:
                pass
        logger.info(f"[Sentinel] rule proposal: {pat} (seen {count}x) — approve with: hermus sentinel approve {proposal['id']}")
        return proposal

    def handle_event(self, env) -> dict[str, Any]:
        """React to one event inline. Returns the decision record.

        The decision event is published AFTER the lock is released: bus.publish
        dispatches inline and this handler is itself a subscriber, so publishing
        under the lock can deadlock across threads.
        """
        to_publish = None
        with self._lock:
            fp = self._fingerprint(env)
            sal = self.salience(env)
            score = sal["score"]
            is_dup = fp in self._seen
            self._seen.add(fp)

            if score >= self.alert_threshold:
                action = "alert"
            elif score >= self.enqueue_threshold:
                action = "enqueue"
            elif score >= self.triage_threshold and self.use_llm_triage:
                t = self._llm_triage(env, score)
                action = t["action"]
                sal["reasons"].append(f"llm_triage={action} ({t.get('reason', '')[:80]})")
            elif score >= self.note_threshold:
                action = "note"
            else:
                action = "ignore"
            if is_dup and action in ("alert", "enqueue"):
                action = "note"  # repeats de-escalate; never re-alert the same event
                sal["reasons"].append("duplicate->de-escalated")

            decision = {
                "event_id": getattr(env, "event_id", ""),
                "type": env.type,
                "command": env.command,
                "status": env.status,
                "salience": score,
                "reasons": sal["reasons"],
                "action": action,
                "duplicate": is_dup,
                "model": self.model or "",
                "ts": _now_iso(),
            }
            enqueued = None
            notified = None
            if action in ("alert", "enqueue"):
                text = f"{env.type} {env.command}".strip() or env.type
                payload = {
                    "text": f"[sentinel:{action}] {text} — {json.dumps(env.args_redacted or {}, default=str)[:300]}",
                    "task": f"Handle {text}",
                    "platform": "sentinel",
                    "user_id": "sentinel",
                    "trigger": {"type": env.type, "salience": score, "event_id": decision["event_id"]},
                }
                enqueue = self._enqueue or self._default_enqueue
                try:
                    enqueued = enqueue("sentinel", payload)
                except Exception as e:
                    enqueued = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    decision["enqueue_error"] = str(enqueued.get("error"))[:200]
                decision["enqueued"] = enqueued
                if action == "alert":
                    notify = self._notify or self._default_notify
                    try:
                        notified = notify(text, str((env.args_redacted or {}).get("severity") or "high"))
                    except Exception as e:
                        notified = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    decision["notified"] = notified

            proposal = self._note_pattern(env, action)
            if proposal:
                decision["rule_proposal"] = proposal["id"]

            self.decisions.append(decision)
            if len(self.decisions) > 500:
                self.decisions = self.decisions[-500:]
            # every decision is recorded (observability contract); only the
            # log line is downgraded for ignores (high-volume lifecycle events)
            if self._recorder:
                try:
                    self._recorder(decision)
                except Exception:
                    pass
            if self.bus is not None and not str(env.type).startswith("sentinel."):
                # never republish our own decision events (would recurse);
                # published after the lock is released (inline bus dispatch)
                to_publish = envelope_from_dict(
                    {
                        "type": "sentinel.decision",
                        "command": f"sentinel.{action}",
                        "status": "succeeded",
                        "args": {
                            "action": action,
                            "salience": score,
                            "event_type": env.type,
                            "event_id": decision["event_id"],
                        },
                    }
                )
        if to_publish is not None:
            try:
                self.bus.publish(to_publish)
            except Exception:
                pass
        if action == "ignore":
            logger.debug(f"[Sentinel] ignore salience={score} type={env.type} cmd={env.command}")
        else:
            logger.info(f"[Sentinel] {action} salience={score} type={env.type} cmd={env.command}")
        return decision

    # ------------------------------------------------------------ lifecycle

    def subscribe(self):
        """Register on the bus (inline reactions). Returns self."""
        if self.bus is None:
            from .events import get_bus

            self.bus = get_bus()
        self.bus.subscribe()(self.handle_event)
        return self

    def consolidate(self, limit: int = 200, *, deep: bool = False) -> dict[str, Any]:
        """Distill recent bus events into memory — 'learning about my things'.

        ``deep=True`` (the "night shift") additionally turns repeated failures
        into durable lessons via the canonical LessonsStore.
        """
        if self.bus is None:
            return {"ok": False, "reason": "no bus"}
        try:
            events = self.bus.recent(limit=limit)
        except Exception:
            events = []
        by_type: dict[str, int] = {}
        failures: list[str] = []
        for e in events:
            t = getattr(e, "type", "?")
            by_type[t] = by_type.get(t, 0) + 1
            if str(getattr(e, "status", "")).lower() in ("failed", "error"):
                failures.append(f"{t} {getattr(e, 'command', '')}".strip())
        digest = {
            "ts": _now_iso(),
            "events": len(events),
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])[:15]),
            "failures": len(failures),
            "sentinel_decisions": sum(1 for d in self.decisions if d["action"] in ("alert", "enqueue")),
        }
        try:
            from .memory import get_memory

            mem = get_memory()
            res = mem.remember(
                "episodic",
                f"[sentinel] digest {digest['ts']}: {digest['events']} events; "
                f"top types {digest['by_type']}; {digest['failures']} failure(s); "
                f"sentinel acted {digest['sentinel_decisions']} time(s).",
            )
            digest["memory"] = res
        except Exception as e:
            digest["memory"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if deep:
            # night shift also sweeps memory: stale context stops polluting prompts
            try:
                from .memory import get_memory

                sweep = getattr(get_memory(), "sweep", None)
                if callable(sweep):
                    rep = sweep(dry_run=False)
                    if isinstance(rep, dict):
                        digest["decay"] = {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items() if k != "dry_run"}
                    else:
                        digest["decay"] = rep
            except Exception as e:
                digest["decay_error"] = f"{type(e).__name__}: {e}"
        if deep and failures:
            try:
                from .reasoning.lessons import LessonsStore

                store = LessonsStore()
                seen = set()
                added = 0
                for f in failures[:20]:
                    if f in seen:
                        continue
                    seen.add(f)
                    r = store.add(
                        lesson=f"Recurring failure observed by sentinel: {f}", category="sentinel", source="sentinel-consolidate"
                    )
                    added += 1 if (r or {}).get("success", True) else 0
                digest["lessons_added"] = added
            except Exception as e:
                digest["lessons_error"] = f"{type(e).__name__}: {e}"
        return digest

    def serve(self, *, consolidate_every_s: float | None = None, stop: Callable[[], bool] | None = None) -> None:
        """Subscribe and idle; periodically consolidate. Runs until stop() is true."""
        self.subscribe()
        every = consolidate_every_s if consolidate_every_s is not None else float(_cfg("consolidate_seconds", 300))
        logger.info(f"[Sentinel] alert core running — inline on EventBus, consolidating every {every}s")
        last = time.time()
        try:
            while True:
                if stop is not None and stop():
                    break
                time.sleep(0.25)
                if every > 0 and time.time() - last >= every:
                    last = time.time()
                    try:
                        self.consolidate()
                    except Exception as e:
                        logger.warning(f"[Sentinel] consolidate failed: {e}")
        finally:
            # never leave a stale handler on a shared (e.g. global) bus
            try:
                if self.bus is not None:
                    self.bus.unsubscribe(self.handle_event)
            except Exception:
                pass


def build_llm(model_spec: str | None):
    """Resolve a flexible model spec and build the canonical LLM client (or None)."""
    from .harness.models import resolve_model_spec
    from .models import get_model_gateway

    res = resolve_model_spec(model_spec or "auto")
    if not res.get("ok"):
        return None, res
    try:
        return get_model_gateway().llm(model=res["ref"]), res
    except Exception:
        return None, res


# --------------------------------------------------------------------------- harness driver


def run_sentinel_task(task, workspace, *, model: str | None = None) -> dict[str, Any]:
    """Drive one sentinel harness task: feed its events through an isolated sentinel.

    Writes ``sentinel_decisions.jsonl`` / ``enqueued.jsonl`` / ``alerts.jsonl`` into
    the task workspace so suite checks can verify side effects with file checks.
    """
    from .events.bus import EventBus

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    bus = EventBus(log_path=ws / "events.jsonl")

    enqueued: list[dict] = []
    alerts: list[dict] = []

    def _enqueue(kind: str, payload: dict) -> dict:
        rec = {"kind": kind, "payload": payload, "ts": _now_iso()}
        enqueued.append(rec)
        return {"ok": True, "job": rec}

    def _notify(text: str, severity: str) -> dict:
        rec = {"text": text, "severity": severity, "ts": _now_iso()}
        alerts.append(rec)
        return {"ok": True}

    dec_path = ws / "sentinel_decisions.jsonl"
    recorder = default_recorder(dec_path)

    llm, res = (None, {"ok": False, "note": "no model"})
    use_triage = bool(getattr(task, "use_llm_triage", False))
    if use_triage:
        llm, res = build_llm(model)

    sentinel = Sentinel(
        bus=bus,
        enqueue=_enqueue,
        notify=_notify,
        llm=llm,
        model=(res.get("ref") if res.get("ok") else model),
        use_llm_triage=use_triage,
        recorder=recorder,
        focus_provider=lambda: focus_keywords(),
        proposals=ws / "proposed_rules.json",
    )

    events = list(getattr(task, "events", None) or [])
    if getattr(task, "event", None):
        events.insert(0, task.event)
    for ev in events:
        # handle directly (not via bus.publish) so decisions[] has exactly one
        # entry per input event; the input + decision events are still logged
        env = envelope_from_dict(ev)
        bus.publish(envelope_from_dict({"type": "harness.input", "command": env.type, "args": dict(env.args_redacted or {})}))
        sentinel.handle_event(env)

    for name, rows in (("enqueued.jsonl", enqueued), ("alerts.jsonl", alerts)):
        with (ws / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")

    response = json.dumps(
        {
            "decisions": sentinel.decisions,
            "enqueued": enqueued,
            "alerts": alerts,
            "proposals": (
                json.loads((ws / "proposed_rules.json").read_text(encoding="utf-8"))
                if (ws / "proposed_rules.json").is_file()
                else []
            ),
            "model_resolution": res,
        },
        default=str,
    )
    return {
        "response": response,
        "steps": len(events),
        "tool_calls": ["sentinel.handle_event"] * len(events),
        "tool_results": [{"tool": "sentinel.handle_event", "result": d} for d in sentinel.decisions],
        "decisions": sentinel.decisions,
    }
