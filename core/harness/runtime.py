"""Facade used by HermusAgent each turn.

One call does the four things a turn needs before the model sees it:

1. start the server-owned session record (or heartbeat an existing one);
2. compact the **fully assembled** message list to the token budget;
3. turn pending file-shift and swarm-mail into explicit notices;
4. run the single cascade recall and trim it to the memory budget.

It then reports what it did on the canonical event stream so the Control Room
can show real compaction/recall numbers instead of a spinner.
"""

from __future__ import annotations

from typing import Any

from ..config import config
from . import bus, files, sessions
from .compaction import compact_messages
from .memory_graph import cascade_recall


def _emit(event_type: str, data: dict[str, Any]) -> None:
    """Publish a harness event; a broken bus must never break a turn."""
    try:
        from ..dashboard_events import dashboard_event_bus

        dashboard_event_bus.publish(event_type, data)
    except Exception:
        pass


class HarnessRuntime:
    def prepare_turn(
        self,
        session_id: str,
        user_message: str,
        messages: list[dict[str, Any]],
        *,
        project: str = "",
        budget_tokens: int | None = None,
        budget_chars: int | None = None,
        model: str = "",
    ) -> dict[str, Any]:
        """Return ``{messages, compaction, notices, memory, session, enabled}``.

        ``messages`` is always a new list; the caller's trajectory is never
        mutated.
        """
        if not bool(getattr(config, "harness_enabled", True)):
            return {
                "messages": [dict(m) for m in messages],
                "compaction": {"compacted": False, "enabled": False},
                "notices": [],
                "memory": {"count": 0, "summary": "", "hits": [], "mode": "disabled"},
                "session": None,
                "enabled": False,
            }

        try:
            sessions.gc_once_per_hour()
        except Exception:
            pass

        session = sessions.begin(
            session_id,
            task=user_message,
            model=model,
            project=project,
        )
        if budget_chars:
            budget_tokens = max(256, int(budget_chars) // 4)
        tokens = int(budget_tokens if budget_tokens is not None else getattr(config, "harness_budget_tokens", 6000))

        compacted, report = compact_messages(
            messages,
            budget_tokens=tokens,
            keep_recent=int(getattr(config, "harness_keep_recent", 6)),
            tool_limit=int(getattr(config, "harness_tool_payload_chars", 1200)),
        )

        notices: list[str] = []
        for event in files.pending(session_id):
            notices.append(
                f"FILE CHANGED UNDER YOU: {event.get('path')} "
                f"(writer={event.get('writer') or 'unknown'}). Re-read before editing."
            )
        if notices:
            files.ack(session_id)

        unread = bus.inbox(session_id, unread_only=True)
        for msg in unread:
            notices.append(f"SWARM {msg.get('kind')} from {msg.get('sender')}: {str(msg.get('body') or '')[:400]}")
        if unread:
            bus.mark_read(session_id, [m["id"] for m in unread])

        memory = cascade_recall(
            user_message,
            limit=int(getattr(config, "harness_recall_limit", 4)),
            project=project,
        )

        extra = ""
        if notices:
            extra += "\n\nHarness notices:\n" + "\n".join(f"- {n}" for n in notices)
        if memory.get("summary"):
            extra += "\n\nCascade memory:\n" + memory["summary"]

        if extra:
            for msg in compacted:
                if msg.get("role") == "system":
                    msg["content"] = str(msg.get("content") or "") + extra
                    break
            else:
                compacted.insert(0, {"role": "system", "content": extra.strip()})

        stats = {
            "notices": len(notices),
            "recall_hits": int(memory.get("count") or 0),
            "tokens_in": int(report.get("tokens_after") or report.get("tokens") or 0),
        }
        if report.get("compacted"):
            stats["compactions"] = 1
        try:
            sessions.record(session_id, **stats)
        except Exception:
            pass

        _emit(
            "harness.turn_prepared",
            {
                "session_id": session_id,
                "compacted": bool(report.get("compacted")),
                "tokens_before": report.get("tokens_before"),
                "tokens_after": report.get("tokens_after", report.get("tokens")),
                "elided": report.get("elided", 0),
                "notices": len(notices),
                "recall_mode": memory.get("mode"),
                "recall_count": memory.get("count"),
                "recall_ms": memory.get("elapsed_ms"),
            },
        )
        if report.get("compacted"):
            _emit("harness.compacted", {"session_id": session_id, **report})
        for notice in notices:
            kind = "file_changed" if notice.startswith("FILE CHANGED") else "swarm_message"
            _emit("harness.notice", {"session_id": session_id, "kind": kind, "text": notice})

        return {
            "messages": compacted,
            "compaction": report,
            "notices": notices,
            "memory": memory,
            "session": session,
            "enabled": True,
        }

    def observe_tool(self, session_id: str, tool_name: str, args: dict[str, Any]) -> None:
        """Track which files a session read or wrote so siblings get warned."""
        path = args.get("path") or args.get("file") or args.get("filename")
        if not path:
            return
        try:
            if tool_name in ("file_read", "read_file"):
                files.note_read(session_id, str(path))
            elif tool_name in ("file_write", "write_file", "file_edit", "str_replace"):
                warning = files.note_write(str(path), writer=session_id)
                if warning:
                    _emit("harness.file_shift", {"session_id": session_id, "path": str(path), "readers": len(warning)})
        except Exception:
            pass

    def resume(self, session_id: str, *, limit: int = 8) -> dict[str, Any]:
        """Return what is provably known about a session — never a fake transcript.

        This store keeps the session *record* and accumulated stats. The turn
        transcript is owned by the memory facade, which has no public
        per-session reader, so a resume reports the record plus a recall of
        what the session learned and says so explicitly.
        """
        record = sessions.get(session_id)
        if not record:
            return {"resumable": False, "reason": "unknown session", "session_id": session_id}
        memory = cascade_recall(
            str(record.get("task") or session_id),
            limit=limit,
            project=str(record.get("project") or ""),
        )
        return {
            "resumable": True,
            "session_id": session_id,
            "session": record,
            "recall": memory,
            "transcript": [],
            "note": "session record and recall only; the turn transcript is not owned by the harness store",
        }


harness = HarnessRuntime()


def governed_turn(
    session_id: str,
    user_message: str,
    messages: list[dict[str, Any]],
    *,
    project: str = "",
    model: str = "",
    budget_tokens: int | None = None,
) -> dict[str, Any]:
    """THE one entry point every model turn goes through.

    Returns ``prepare_turn``'s projection — ``{messages, compaction, notices,
    memory, session, enabled}`` — where ``messages`` is the prompt the model
    should actually see: compacted to budget, with file-shift and swarm-mail
    notices and the cascade recall injected. ``prepare_turn`` raises nothing the
    caller should handle: a harness failure degrades to the caller's own message
    list and is recorded as an issue, exactly like ``HermusAgent``'s turn path.
    Paths that build a prompt by hand (counsel deliberation, delegation workers,
    multi-model panels, the fleet probe) call this instead of sending
    ``messages`` straight to the model: an ungoverned turn is a turn without
    budget, memory, swarm mail or a session record.
    """
    fallback = {"messages": [dict(m) for m in messages], "enabled": False}
    try:
        prepared = harness.prepare_turn(
            session_id,
            user_message,
            messages,
            project=str(project or ""),
            model=str(model or ""),
            budget_tokens=budget_tokens,
        )
        if not isinstance(prepared, dict):
            return fallback
        prepared.setdefault("messages", fallback["messages"])
        if not prepared.get("messages"):
            prepared["messages"] = fallback["messages"]
        return prepared
    except Exception as exc:
        from ..run_events import record_issue

        record_issue(
            "harness",
            "prepare_turn",
            exc,
            retryable=False,
            fallback="turn continues without harness context",
        )
        return fallback


def governed_call(
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
    model: str = "",
    budget_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Budget-only governance for single-shot calls with a strict output contract.

    Workstream planning and sub-agent answer synthesis must get JSON (or a merged
    answer) back, so they cannot carry the memory/notices block that
    :func:`governed_turn` appends to the system message — that is how a
    single-shot call starts answering in prose. They still get the two things
    that are safe and useful: compaction to the token budget, and an event so the
    Control Room can see the call was real. Never raises.
    """
    fallback = [dict(m) for m in messages]
    try:
        tokens = int(budget_tokens) if budget_tokens else int(getattr(config, "harness_budget_tokens", 6000))
        compacted, report = compact_messages(
            fallback,
            budget_tokens=tokens,
            keep_recent=int(getattr(config, "harness_keep_recent", 6)),
            tool_limit=int(getattr(config, "harness_tool_payload_chars", 1200)),
        )
        _emit(
            "harness.call_prepared",
            {
                "session_id": str(session_id or ""),
                "model": str(model or ""),
                "compacted": bool(report.get("compacted")),
                "tokens_before": report.get("tokens_before"),
                "tokens_after": report.get("tokens_after", report.get("tokens")),
            },
        )
        return compacted or fallback
    except Exception as exc:
        from ..run_events import record_issue

        record_issue(
            "harness",
            "governed_call",
            exc,
            retryable=False,
            fallback="single-shot call proceeds uncompacted",
        )
        return fallback


__all__ = ["HarnessRuntime", "harness", "governed_turn", "governed_call"]
