"""Facade used by HermusAgent each turn."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import bus, files, sessions
from .compaction import compact_messages
from .memory_graph import cascade_recall


class HarnessRuntime:
    def prepare_turn(
        self,
        session_id: str,
        user_message: str,
        messages: List[Dict[str, Any]],
        *,
        project: str = "",
        budget_chars: int = 24_000,
    ) -> Dict[str, Any]:
        sessions.touch(session_id, status="running", task=user_message[:200], turns_inc=1)
        compacted, report = compact_messages(messages, budget_chars=budget_chars)
        notices: List[str] = []

        for ev in files.pending(session_id):
            notices.append(
                f"FILE CHANGED UNDER YOU: {ev.get('path')} "
                f"(writer={ev.get('writer') or 'unknown'}). Re-read before editing."
            )
        files.ack(session_id)

        unread = bus.inbox(session_id, unread_only=True)
        for msg in unread:
            notices.append(f"SWARM {msg.get('kind')} from {msg.get('sender')}: {msg.get('body')}")
        if unread:
            bus.mark_read(session_id, [m["id"] for m in unread])

        memory = cascade_recall(user_message, limit=4, project=project)

        extra = ""
        if notices:
            extra += "\n\nHarness notices:\n" + "\n".join(f"- {n}" for n in notices)
        if memory.get("summary"):
            extra += "\n\nCascade memory:\n" + memory["summary"]

        if extra and compacted:
            # append to last system message if present
            for msg in compacted:
                if msg.get("role") == "system":
                    msg["content"] = str(msg.get("content") or "") + extra
                    break
            else:
                compacted.insert(0, {"role": "system", "content": extra.strip()})

        return {
            "messages": compacted,
            "compaction": report,
            "notices": notices,
            "memory": memory,
        }

    def observe_tool(self, session_id: str, tool_name: str, args: Dict[str, Any]) -> None:
        path = args.get("path") or args.get("file") or args.get("filename")
        if not path:
            return
        if tool_name in ("file_read", "read_file"):
            files.note_read(session_id, str(path))
        elif tool_name in ("file_write", "write_file", "file_edit", "str_replace"):
            files.note_write(str(path), writer=session_id)


harness = HarnessRuntime()
