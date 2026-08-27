"""Hierarchical sub-agent delegation over JSON-RPC 2.0.

The orchestrator (this module, running in the parent) spawns lightweight worker
agents as **separate processes** and talks to them with newline-delimited JSON-RPC
2.0 over stdin/stdout — LSP-style, so a worker that dies, hangs or prints junk
cannot take the parent with it. Each worker can itself delegate, subject to a
depth budget, which is what makes the tree hierarchical instead of one-level.

Protocol (one JSON object per line):

    parent → {"jsonrpc":"2.0","id":1,"method":"agent.run","params":{"task":"…"}}
    worker ← {"jsonrpc":"2.0","method":"event","params":{"type":"tool_call",…}}   (notification)
    worker ← {"jsonrpc":"2.0","id":1,"result":{"answer":"…","confidence":0.8,…}}  (response)
    parent → {"jsonrpc":"2.0","method":"$/cancel","params":{"id":1}}

Methods implemented by a worker: ``initialize``, ``agent.run``, ``tool.call``,
``memory.recall``, ``ping``, ``shutdown``.

Structured aggregation: every child result is normalised to
``{answer, evidence[], confidence, tool_calls[], status, artifacts[]}`` before the
tree reduces it (``synthesize`` via the free LLM, ``vote``, ``best``, ``concat``),
so the parent's context only ever sees the summary — the whole point of
delegation. Failure isolation: one child erroring is a data point in the
aggregate, not an exception in the orchestrator; a wedged child is cancelled and
reaped, and if process spawning is unavailable at all (weird sandboxes) the
degrader runs the child in-process instead.

Run a worker by hand to see the protocol:

    python -m core.delegation --depth 0
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable, Sequence

from .config import config

PROTOCOL_VERSION = "1.0"
JSONRPC = "2.0"

# JSON-RPC error codes (subset of the spec + LSP-style RequestCancelled)
ERR_PARSE = -32700
ERR_INVALID = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INTERNAL = -32603
ERR_CANCELLED = -32800
ERR_TIMEOUT = -32001


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int = ERR_INTERNAL, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


def rpc_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": req_id, "result": result}


def rpc_error(req_id: Any, message: str, code: int = ERR_INTERNAL, data: Any = None) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": req_id, "error": {"code": code, "message": str(message)[:1500],
                                                          "data": data}}


def rpc_notification(method: str, params: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC, "method": method, "params": params if params is not None else {}}


# ------------------------------------------------------------ structured results
def normalize_result(raw: Any) -> dict[str, Any]:
    """Coerce anything a child returned into the delegation result contract."""
    if raw is None:
        return {"answer": "", "evidence": [], "confidence": 0.0, "tool_calls": [],
                "status": "failed", "error": "no result", "artifacts": [], "usage": {}, "steps": None}
    if isinstance(raw, str):
        return {"answer": raw, "evidence": [], "confidence": 0.5, "tool_calls": [],
                "status": "done", "error": "", "artifacts": [], "usage": {}, "steps": None}
    if not isinstance(raw, dict):
        return {"answer": json.dumps(raw, default=str)[:4000], "evidence": [], "confidence": 0.4,
                "tool_calls": [], "status": "done", "error": "", "artifacts": [],
                "usage": {}, "steps": None}
    answer = raw.get("answer") or raw.get("response") or raw.get("final_answer") or raw.get("summary") or ""
    error = raw.get("error") or ""
    status = str(raw.get("status") or ("failed" if error else "done"))
    conf = raw.get("confidence")
    if conf is None:
        conf = 0.75 if status == "done" and answer else 0.2
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    evidence = raw.get("evidence")
    if not isinstance(evidence, list):
        evidence = [str(x) for x in (raw.get("tool_results") or [])][:8] if isinstance(raw.get("tool_results"), list) else []
    tools = raw.get("tool_calls")
    if not isinstance(tools, list):
        tools = []
    artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), list) else []
    return {
        "answer": str(answer)[:20000],
        "evidence": [str(e)[:600] for e in evidence[:20]],
        "confidence": round(conf, 3),
        "tool_calls": [str(t) for t in tools[:40]],
        "status": status if status in ("done", "failed", "cancelled", "partial") else "done",
        "error": str(error)[:1000],
        "artifacts": [str(a) for a in artifacts[:20]],
        "usage": raw.get("usage") or {},
        "steps": raw.get("steps"),
    }


# ------------------------------------------------------------- worker (child)
class DelegationWorker:
    """The child side: serve JSON-RPC on stdin/stdout until EOF/shutdown."""

    def __init__(self, depth: int = 1, session_id: str = "", max_steps: int = None):
        self.depth = int(depth)
        self.session_id = session_id or f"sub_{uuid.uuid4().hex[:8]}"
        self.max_steps = int(max_steps or 4)
        self._cancelled: set = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ serving
    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        self._send = self._make_writer(stdout)
        self._send(rpc_notification("ready", {
            "session_id": self.session_id, "depth": self.depth,
            "protocol": PROTOCOL_VERSION, "pid": os.getpid(),
            "capabilities": {"methods": ["initialize", "agent.run", "agent.autonomous",
                                          "tool.call", "memory.recall", "ping", "shutdown"],
                              "notifications": ["event", "log"]},
        }))
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                self._send(rpc_error(None, f"parse error: {e}", ERR_PARSE))
                continue
            if "id" not in msg and msg.get("method"):
                self._handle_notification(msg)
                continue
            resp = self.dispatch(msg)
            if resp is not None:
                self._send(resp)
            if msg.get("method") == "shutdown":
                break

    def _make_writer(self, stdout) -> Callable[[dict[str, Any]], None]:
        write_lock = threading.Lock()

        def _send(obj: dict[str, Any]) -> None:
            try:
                with write_lock:
                    stdout.write(json.dumps(obj, default=str) + "\n")
                    stdout.flush()
            except Exception:
                pass  # parent went away: exit quietly on the next read failure

        return _send

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "$/cancel":
            with self._lock:
                self._cancelled.add(params.get("id"))
        elif method == "ping":
            pass

    def emit(self, event_type: str, data: Optional[dict[str, Any]] = None) -> None:
        try:
            self._send(rpc_notification("event", {"type": event_type, **(data or {})}))
        except Exception:
            pass

    def cancelled(self, req_id: Any) -> bool:
        with self._lock:
            return req_id in self._cancelled

    # ---------------------------------------------------------------- dispatch
    def dispatch(self, msg: dict[str, Any]) -> Optional[dict[str, Any]]:
        req_id = msg.get("id")
        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        if not method:
            return rpc_error(req_id, "missing method", ERR_INVALID)
        try:
            if method == "initialize":
                return rpc_response(req_id, {
                    "protocol": PROTOCOL_VERSION, "session_id": self.session_id,
                    "depth": self.depth, "pid": os.getpid(),
                    "model": getattr(config, "model", ""),
                })
            if method == "ping":
                return rpc_response(req_id, {"pong": _now(), "depth": self.depth})
            if method in ("agent.run", "agent.autonomous"):
                return rpc_response(req_id, self.run_agent(params, req_id, autonomous=(method == "agent.autonomous")))
            if method == "tool.call":
                return rpc_response(req_id, self.call_tool(params))
            if method == "memory.recall":
                return rpc_response(req_id, self.recall(params))
            if method == "shutdown":
                return rpc_response(req_id, {"bye": True})
            return rpc_error(req_id, f"unknown method '{method}'", ERR_METHOD_NOT_FOUND)
        except Exception as e:  # never die on a bad request
            return rpc_error(req_id, f"{type(e).__name__}: {e}", ERR_INTERNAL)

    def run_agent(self, params: dict[str, Any], req_id: Any, *, autonomous: bool = False) -> dict[str, Any]:
        task = str(params.get("task") or params.get("text") or "")
        if not task.strip():
            return normalize_result({"error": "task required"})
        max_steps = int(params.get("max_steps") or self.max_steps)
        model = params.get("model") or None
        try:
            from .agent import HermusAgent

            agent = HermusAgent(model=model, session_id=f"{self.session_id}_{uuid.uuid4().hex[:4]}",
                                max_steps=max_steps)
            # depth guard: a child may not delegate further than the budget allows
            if self.depth >= int(getattr(config, "delegation_max_depth", 2)):
                try:
                    agent.tools = [t for t in agent.tools
                                   if t.get("function", {}).get("name") not in ("subagent_spawn", "delegate_tasks")]
                except Exception:
                    pass
            self.emit("agent_started", {"task": task[:300], "depth": self.depth, "max_steps": max_steps})
            if autonomous:
                report = agent.autonomous(task, max_repairs=int(params.get("max_repairs", 2)))
                result = {"answer": report.get("summary") or report.get("response") or "",
                          "status": "done" if report.get("verified") else "partial",
                          "steps": report.get("steps"), "evidence": [
                              f"[{s.get('status')}] {str(s.get('goal'))[:120]}" for s in (report.get("steps") or [])][:10]}
            else:
                out = agent.chat(
                    task,
                    on_event=lambda t, d: self.emit(t, {**d, "depth": self.depth}),
                    stream=bool(params.get("stream", False)),
                    should_cancel=lambda: self.cancelled(req_id),
                )
                result = {
                    "answer": out.get("response", ""),
                    "evidence": [
                        f"{tr.get('tool')}: {json.dumps(tr.get('result'), default=str)[:160]}"
                        for tr in (out.get("tool_results") or [])[:8]
                    ],
                    "tool_calls": out.get("tool_calls") or [],
                    "usage": out.get("usage") or {},
                    "steps": out.get("steps"),
                    "status": "failed" if out.get("error") else "done",
                    "error": out.get("error") or "",
                    "artifacts": out.get("artifacts") or [],
                    "skill_created": (out.get("skill_created") or {}).get("name") if isinstance(out.get("skill_created"), dict) else None,
                }
            self.emit("agent_finished", {"depth": self.depth, "chars": len(str(result.get("answer") or ""))})
            return normalize_result(result)
        except Exception as e:
            self.emit("agent_failed", {"error": str(e)[:400], "depth": self.depth})
            return normalize_result({"error": f"worker agent failed: {e}", "status": "failed"})

    @staticmethod
    def call_tool(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("tool") or params.get("name") or "")
        args = params.get("args") or params.get("arguments") or {}
        if not name:
            return {"error": "tool required"}
        try:
            from .tool_registry import tool_registry

            result = tool_registry.execute(name, args if isinstance(args, dict) else {"input": str(args)})
            return {"tool": name, "result": result}
        except Exception as e:
            return {"tool": name, "error": str(e)[:500]}

    @staticmethod
    def recall(params: dict[str, Any]) -> dict[str, Any]:
        query = str(params.get("query") or "")
        limit = int(params.get("limit", 5))
        try:
            from .memory2 import memory2

            hits = memory2.hybrid_recall(query, limit=limit)
            return {"query": query, "hits": [
                {"id": h.get("id"), "kind": h.get("kind"), "score": h.get("score"),
                 "rrf": h.get("rrf_score"), "text": (h.get("content") or "")[:400]}
                for h in hits
            ]}
        except Exception as e:
            return {"query": query, "error": str(e)[:300], "hits": []}


# ---------------------------------------------------------------- parent side
@dataclass
class DelegationNode:
    id: str
    task: str
    depth: int = 1
    parent: Optional[str] = None
    status: str = "pending"          # pending|running|done|failed|cancelled
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    pid: int = 0
    backend: str = ""
    started: float = 0.0
    finished: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "task": self.task[:400], "depth": self.depth, "parent": self.parent,
            "status": self.status, "error": self.error[:500], "pid": self.pid, "backend": self.backend,
            "duration_ms": int((self.finished - self.started) * 1000) if self.finished and self.started else None,
            "answer": (self.result or {}).get("answer", "")[:1500],
            "confidence": (self.result or {}).get("confidence"),
            "tool_calls": (self.result or {}).get("tool_calls", [])[:20],
            "evidence": (self.result or {}).get("evidence", [])[:6],
            "event_count": len(self.events),
        }


class RpcClient:
    """Parent-side handle on one worker process."""

    def __init__(self, *, depth: int = 1, model: str = "", extra_env: Optional[dict[str, str]] = None,
                 on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
                 spawn_timeout: float = 20.0):
        self.depth = depth
        self.on_event = on_event
        self._pending: dict[Any, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._notifications: "queue.Queue" = queue.Queue(maxsize=1000)
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._next_id = 0
        self.spawn_timeout = float(spawn_timeout)
        self.model = model
        self.extra_env = extra_env or {}
        self.alive = False
        self.started_at = time.time()
        self.backend = ""

    # ------------------------------------------------------------------ spawn
    def start(self) -> None:
        env = dict(os.environ)
        env["HERMUS_AGENT_DEPTH"] = str(self.depth)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        for k, v in self.extra_env.items():
            env[str(k)] = str(v)
        if self.model:
            env["HERMUS_MODEL"] = self.model
        cmd = [sys.executable, "-m", "core.delegation", "--depth", str(self.depth),
               "--max-steps", str(int(os.environ.get("HERMUS_CHILD_MAX_STEPS", "4")))]
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=str(Path(__file__).resolve().parent.parent), env=env,
            start_new_session=(os.name == "posix"),
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.alive = True
        self.backend = "subprocess-jsonrpc"

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    self._notify("log", {"level": "stderr-ish", "message": line[:400]})
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    q = None
                    with self._lock:
                        q = self._pending.pop(msg["id"], None)
                    if q is not None:
                        try:
                            q.put_nowait(msg)
                        except Exception:
                            pass
                    continue
                method = msg.get("method")
                params = msg.get("params") or {}
                if method == "ready":
                    self._notify("worker_ready", params)
                elif method == "event":
                    self._notify(str(params.get("type") or "event"),
                                 {k: v for k, v in params.items() if k != "type"})
                elif method == "log":
                    self._notify("log", params)
        except Exception as e:
            self._notify("worker_reader_error", {"error": str(e)[:200]})
        finally:
            self.alive = False
            self._notify("worker_closed", {"exit": self.exit_code()})

    def _notify(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            self._notifications.put_nowait({"type": event_type, **data})
        except Exception:
            pass
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception:
                pass

    def exit_code(self) -> Optional[int]:
        return self._proc.returncode if self._proc else None

    # ------------------------------------------------------------------ calls
    def request(self, method: str, params: Optional[dict[str, Any]] = None,
                timeout: float = 60.0) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RpcError("worker not started", ERR_INTERNAL)
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            box: "queue.Queue" = queue.Queue(maxsize=1)
            self._pending[req_id] = box
        body = json.dumps({"jsonrpc": JSONRPC, "id": req_id, "method": method,
                           "params": params or {}}, default=str)
        try:
            proc.stdin.write(body + "\n")
            proc.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id, None)
            raise RpcError(f"worker stdin closed: {e}", ERR_INTERNAL) from e
        try:
            msg = box.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(req_id, None)
            raise RpcError(f"timeout waiting for '{method}' after {timeout}s", ERR_TIMEOUT) from None
        if "error" in msg:
            err = msg.get("error") or {}
            raise RpcError(str(err.get("message"))[:600], int(err.get("code") or ERR_INTERNAL), err.get("data"))
        return msg.get("result") or {}

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(json.dumps({"jsonrpc": JSONRPC, "method": method,
                                         "params": params or {}}, default=str) + "\n")
            proc.stdin.flush()
        except Exception:
            pass

    def cancel(self, req_id: Optional[int] = None) -> None:
        self.notify("$/cancel", {"id": req_id})

    # ------------------------------------------------------------------ lifecycle
    def close(self, grace: float = 3.0) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            self.notify("shutdown")
            proc.stdin and proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=grace)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM the whole group
                else:
                    proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.alive = False

    def drain_stderr(self, limit: int = 2000) -> str:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            return proc.stderr.read(limit)
        except Exception:
            return ""


# --------------------------------------------------------------------- facade
class Delegation:
    """Orchestrator API used by the agent, the CLI and the gateway."""

    def __init__(self, max_workers: int = None, max_depth: int = None,
                 timeout: float = None, rpc: Optional[bool] = None):
        self.max_workers = int(max_workers if max_workers is not None
                               else getattr(config, "delegation_max_workers", 4))
        self.max_depth = int(max_depth if max_depth is not None
                             else getattr(config, "delegation_max_depth", 2))
        self.timeout = float(timeout if timeout is not None
                             else getattr(config, "delegation_timeout", 120))
        self.rpc = bool(getattr(config, "delegation_rpc", True) if rpc is None else rpc)
        self.trees: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._active: dict[str, list[RpcClient]] = {}

    # ------------------------------------------------------------------ helpers
    def _depth(self) -> int:
        try:
            return int(os.environ.get("HERMUS_AGENT_DEPTH", "0"))
        except (TypeError, ValueError):
            return 0

    def can_delegate(self) -> bool:
        return bool(getattr(config, "delegation_enabled", True)) and self._depth() < self.max_depth

    # --------------------------------------------------------------- execution
    def _run_child(
        self,
        node: DelegationNode,
        *,
        model: str = "",
        max_steps: int = 4,
        timeout: Optional[float] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        timeout = float(timeout or self.timeout)
        node.status = "running"
        node.started = time.time()
        client: Optional[RpcClient] = None
        emit = on_event or (lambda t, d: None)
        try:
            if self.rpc:
                client = RpcClient(depth=node.depth + 1, model=model, on_event=emit)
                client.start()
                node.pid = client._proc.pid if client._proc else 0
                node.backend = client.backend
                with self._lock:
                    self._active.setdefault(node.parent or "root", []).append(client)
                client.request("initialize", {"node": node.id}, timeout=min(15.0, timeout))
                result = client.request("agent.run", {
                    "task": node.task, "max_steps": max_steps, "model": model, "stream": stream,
                }, timeout=timeout)
                out = normalize_result(result)
            else:
                node.backend = "inprocess"
                out = self._run_inprocess(node, model=model, max_steps=max_steps, emit=emit,
                                          should_cancel=should_cancel)
            node.result = out
            node.status = "done" if out.get("status") in ("done", "partial") else "failed"
            if out.get("status") == "failed":
                node.error = out.get("error", "")[:500]
        except RpcError as e:
            # RPC broke → degrade once, don't lose the work
            emit("delegation_fallback", {"node": node.id, "reason": str(e)[:200], "code": e.code})
            try:
                out = self._run_inprocess(node, model=model, max_steps=max_steps, emit=emit,
                                          should_cancel=should_cancel)
                node.backend = "inprocess-fallback"
                node.result = out
                node.status = "done" if out.get("status") in ("done", "partial") else "failed"
            except Exception as e2:
                node.status = "failed"
                node.error = f"rpc: {e}; inprocess: {e2}"
                out = normalize_result({"error": node.error, "status": "failed"})
                node.result = out
        except Exception as e:
            node.status = "failed"
            node.error = f"{type(e).__name__}: {e}"
            node.result = normalize_result({"error": node.error, "status": "failed"})
            out = node.result
        finally:
            if client is not None:
                with self._lock:
                    lst = self._active.get(node.parent or "root", [])
                    if client in lst:
                        lst.remove(client)
                client.close()
            node.finished = time.time()
        return out

    @staticmethod
    def _run_inprocess(node: DelegationNode, *, model: str = "", max_steps: int = 4,
                       emit: Callable[[str, dict[str, Any]], None] = lambda t, d: None,
                       should_cancel: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
        """Fallback path: run the child agent in this process (no isolation)."""
        from .agent import HermusAgent

        agent = HermusAgent(model=model or None, session_id=f"{node.id}", max_steps=max_steps)
        out = agent.chat(node.task, on_event=emit, should_cancel=should_cancel)
        return normalize_result({
            "answer": out.get("response", ""),
            "tool_calls": out.get("tool_calls") or [],
            "evidence": [f"{tr.get('tool')}: {json.dumps(tr.get('result'), default=str)[:160]}"
                         for tr in (out.get("tool_results") or [])[:8]],
            "steps": out.get("steps"),
            "error": out.get("error") or "",
            "status": "failed" if out.get("error") else "done",
            "usage": out.get("usage") or {},
        })

    # ------------------------------------------------------------------ fan-out
    def fanout(
        self,
        tasks: Sequence[str],
        *,
        goal: str = "",
        model: str = "",
        max_steps: int = 4,
        depth: int = 1,
        timeout: Optional[float] = None,
        aggregate: str = "synthesize",
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        tree_id: str = "",
        max_children: Optional[int] = None,
    ) -> dict[str, Any]:
        """Run N workstreams in parallel and aggregate their structured results."""
        if not self.can_delegate() and self._depth() >= self.max_depth:
            return {"ok": False, "error": f"delegation depth budget exhausted (depth={self._depth()}, "
                                          f"max={self.max_depth})", "status": "refused"}
        emit = on_event or (lambda t, d: None)
        tasks = [str(t) for t in (tasks or []) if str(t).strip()]
        if not tasks:
            return {"ok": False, "error": "no tasks to delegate", "status": "failed"}
        limit = int(max_children or self.max_workers * 4)
        if len(tasks) > limit:
            tasks = tasks[:limit]
        tree_id = tree_id or f"tree_{uuid.uuid4().hex[:8]}"
        parent_id = f"{tree_id}"
        nodes = [DelegationNode(id=f"{tree_id}_{i+1}", task=t, depth=depth, parent=parent_id)
                 for i, t in enumerate(tasks)]
        tree = {"tree_id": tree_id, "goal": goal, "status": "running", "nodes": nodes,
                "started": time.time(), "aggregate": aggregate}
        with self._lock:
            self.trees[tree_id] = tree
        emit("delegation_fanout", {"tree": tree_id, "children": len(nodes), "goal": goal[:200]})

        workers = max(1, min(len(nodes), self.max_workers))
        results: dict[str, dict[str, Any]] = {}
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="deleg") as pool:
                futures = {
                    pool.submit(self._run_child, node, model=model, max_steps=max_steps,
                                timeout=timeout, on_event=lambda t, d, n=node: (
                                    n.events.append({"type": t, **d}), emit(t, {**d, "node": n.id})),
                                should_cancel=should_cancel): node
                    for node in nodes
                }
                for fut in as_completed(futures):
                    node = futures[fut]
                    try:
                        results[node.id] = fut.result()
                    except Exception as e:
                        node.status = "failed"
                        node.error = str(e)[:400]
                        node.result = normalize_result({"error": node.error, "status": "failed"})
                        results[node.id] = node.result
                    emit("delegation_child_done", {"tree": tree_id, "node": node.id,
                                                    "status": node.status,
                                                    "confidence": (node.result or {}).get("confidence")})
        except Exception as e:
            tree["status"] = "failed"
            tree["error"] = str(e)[:400]
            emit("delegation_failed", {"tree": tree_id, "error": str(e)[:300]})

        agg = aggregate_results([n.result for n in nodes], strategy=aggregate, goal=goal)
        done = sum(1 for n in nodes if n.status == "done")
        tree.update({
            "status": "done" if done == len(nodes) else ("partial" if done else "failed"),
            "finished": time.time(),
            "aggregate": agg.get("strategy", aggregate),
            "summary": agg.get("answer", ""),
            "disagreement": agg.get("disagreement", 0.0),
        })
        out = {
            "ok": done > 0,
            "tree_id": tree_id,
            "goal": goal,
            "status": tree["status"],
            "children": len(nodes),
            "succeeded": done,
            "failed": len(nodes) - done,
            "duration_ms": int((tree["finished"] - tree["started"]) * 1000),
            "aggregate": agg,
            "nodes": [n.to_dict() for n in nodes],
        }
        emit("delegation_finished", {k: out[k] for k in ("tree_id", "status", "children", "succeeded", "failed")})
        with self._lock:
            self.trees[tree_id] = {**tree, "result": out}
        return out

    # --------------------------------------------------------------- planner
    def decompose_and_run(
        self,
        goal: str,
        *,
        max_children: int = 4,
        model: str = "",
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        aggregate: str = "synthesize",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Plan → split into workstreams → run in parallel → aggregate."""
        plan = plan_workstreams(goal, max_children=max_children)
        emit = on_event or (lambda t, d: None)
        emit("delegation_planned", {"goal": goal[:200], "workstreams": plan.get("tasks", [])[:8],
                                    "planner": plan.get("planner")})
        return self.fanout(
            plan.get("tasks") or [goal], goal=goal, model=model, aggregate=aggregate,
            on_event=emit, timeout=timeout, max_children=max_children,
        )

    # ------------------------------------------------------------------ status
    def tree(self, tree_id: str) -> dict[str, Any]:
        with self._lock:
            t = self.trees.get(tree_id)
        if not t:
            return {"error": f"unknown tree '{tree_id}'"}
        nodes = t.get("nodes") or []
        return {
            "tree_id": tree_id,
            "goal": t.get("goal", ""),
            "status": t.get("status"),
            "children": [n.to_dict() if isinstance(n, DelegationNode) else n for n in nodes],
            "result": t.get("result"),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            trees = {k: {"status": v.get("status"), "goal": str(v.get("goal", ""))[:120],
                         "children": len(v.get("nodes") or [])} for k, v in self.trees.items()}
        return {
            "enabled": bool(getattr(config, "delegation_enabled", True)),
            "rpc": self.rpc,
            "depth": self._depth(),
            "max_depth": self.max_depth,
            "max_workers": self.max_workers,
            "timeout": self.timeout,
            "can_delegate": self.can_delegate(),
            "trees": trees,
            "active_clients": {k: len(v) for k, v in self._active.items() if v},
        }

    def cancel_tree(self, tree_id: str) -> dict[str, Any]:
        with self._lock:
            clients = list(self._active.get(tree_id, []))
        for c in clients:
            c.cancel()
        return {"cancelled": len(clients), "tree_id": tree_id,
                "note": "children stop at their next step boundary"}


def aggregate_results(results: Sequence[dict[str, Any]], *, strategy: str = "synthesize",
                      goal: str = "") -> dict[str, Any]:
    """Reduce child results into one answer, with disagreement measured."""
    norm = [normalize_result(r) for r in (results or [])]
    good = [r for r in norm if r.get("status") in ("done", "partial") and r.get("answer")]
    if not good:
        return {"strategy": strategy, "answer": "", "sections": [], "citations": [],
                "confidence": 0.0, "used": 0, "errors": [r.get("error", "")[:200] for r in norm if r.get("error")]}
    answers = [r["answer"].strip() for r in good]
    used = len(good)

    if strategy == "concat":
        answer = "\n\n".join(f"### Child {i+1}\n{a}" for i, a in enumerate(answers))
    elif strategy == "best":
        best = max(good, key=lambda r: float(r.get("confidence") or 0.0))
        answer = best["answer"]
        best_conf = float(best.get("confidence") or 0.0)
    elif strategy == "vote":
        key = Counter(_signature(a) for a in answers)
        winner_sig, count = key.most_common(1)[0]
        answer = next(a for a in answers if _signature(a) == winner_sig)
        strategy = "vote"
        used = count
    else:  # synthesize
        answer = _synthesize(goal, answers, good)

    confs = [float(r.get("confidence") or 0.0) for r in good]
    mean = sum(confs) / len(confs) if confs else 0.0
    if strategy == "best":
        mean = best_conf          # the winner's confidence, not the room average
    spread = (max(confs) - min(confs)) if len(confs) > 1 else 0.0
    uniq = len({_signature(a) for a in answers})
    disagreement = 0.0 if used < 2 else max(spread, (uniq - 1) / max(1, used - 1) * 0.5)
    return {
        "strategy": strategy,
        "answer": answer[:30000],
        "sections": [{"child": i + 1, "answer": a[:2000], "confidence": good[i].get("confidence"),
                       "tool_calls": good[i].get("tool_calls", [])[:10]} for i, a in enumerate(answers)],
        "citations": sorted({e for r in good for e in (r.get("evidence") or [])[:5]})[:24],
        "artifacts": sorted({a for r in good for a in (r.get("artifacts") or [])})[:20],
        "confidence": round(mean * (0.85 if disagreement > 0.4 else 1.0), 3),
        "disagreement": round(disagreement, 3),
        "used": used,
        "skipped": len(norm) - used,
        "errors": [r.get("error", "")[:200] for r in norm if r.get("error")][:8],
    }


def _signature(text: str) -> str:
    toks = [t for t in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split() if len(t) > 3]
    return " ".join(sorted(set(toks))[:12])


def _synthesize(goal: str, answers: Sequence[str], results: Sequence[dict[str, Any]]) -> str:
    """Merge child answers — LLM when available, deterministic otherwise."""
    joined = "\n\n".join(f"[child {i+1}] {a}" for i, a in enumerate(answers))
    try:
        from .llm import free_llm

        messages = [
            {"role": "system", "content": (
                "You merge the outputs of parallel sub-agents into ONE answer for the "
                "goal. Keep every distinct fact, resolve direct contradictions by preferring "
                "the higher-confidence child, and list remaining disagreements. No preamble."
            )},
            {"role": "user", "content": f"Goal: {goal[:600]}\n\nConfidences: "
                                        f"{[r.get('confidence') for r in results]}\n\n{joined[:12000]}"},
        ]
        resp = free_llm.chat(messages)
        text = (getattr(resp, "content", "") or "").strip()
        if text and "[MOCK" not in text[:20]:
            return text
    except Exception:
        pass
    header = f"Aggregate of {len(answers)} sub-agent result(s)"
    if goal:
        header += f" for: {goal[:120]}"
    body = "\n\n".join(f"{i+1}. {a}" for i, a in enumerate(answers))
    return f"{header}\n\n{body}"


def plan_workstreams(goal: str, *, max_children: int = 4) -> dict[str, Any]:
    """Split a goal into parallel workstreams (LLM-planned, heuristic fallback)."""
    goal = (goal or "").strip()
    if not goal:
        return {"tasks": [], "planner": "empty"}
    try:
        from .llm import free_llm

        messages = [
            {"role": "system", "content": (
                "Split the goal into at most {n} independent workstreams that can run in "
                "parallel by different agents. Only create a workstream when it genuinely "
                "reduces wall-clock time or context usage. Reply with JSON: "
                '{{"tasks": ["...", "..."]}}'.replace("{n}", str(int(max_children)))
            )},
            {"role": "user", "content": goal[:1200]},
        ]
        resp = free_llm.chat(messages)
        text = getattr(resp, "content", "") or ""
        import re

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m and "[MOCK" not in text[:20]:
            data = json.loads(m.group(0))
            tasks = [str(t).strip() for t in (data.get("tasks") or []) if str(t).strip()]
            if 1 < len(tasks) <= max(2, int(max_children)):
                return {"tasks": tasks[: int(max_children)], "planner": "llm"}
    except Exception:
        pass
    # Deterministic fallback: recognise common parallelisable shapes.
    low = goal.lower()
    tasks: list[str] = []
    if any(k in low for k in ("compare", "versus", " vs ", "each of", "all of")) and ("," in goal or " and " in low):
        parts = [p.strip(" .") for p in goal.replace(" and ", ",").split(",") if p.strip(" .")]
        tasks = [f"{p} — for goal: {goal[:160]}" for p in parts[: int(max_children)]]
    elif any(k in low for k in ("scrape", "log", "parse", "search", "review", "audit", "summarize", "analyze")):
        tasks = [
            f"Gather the raw material for: {goal[:200]}",
            f"Extract structured findings (facts, numbers, entities) for: {goal[:200]}",
            f"Cross-check the findings for gaps/contradictions: {goal[:200]}",
        ][: int(max_children)]
    if len(tasks) < 2:
        tasks = [goal]
    return {"tasks": tasks[: int(max_children)], "planner": "heuristic"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Child entrypoint: ``python -m core.delegation --depth 1``."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m core.delegation",
                                 description="Hermus sub-agent JSON-RPC worker (stdio)")
    ap.add_argument("--depth", type=int, default=int(os.environ.get("HERMUS_AGENT_DEPTH", "1")))
    ap.add_argument("--max-steps", dest="max_steps", type=int, default=4)
    ap.add_argument("--session", dest="session_id", default="")
    ap.add_argument("--self-test", action="store_true", help="answer one ping locally and exit")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        worker = DelegationWorker(depth=args.depth, session_id=args.session_id or "self",
                                  max_steps=args.max_steps)
        print(json.dumps(worker.dispatch({"id": 1, "method": "ping"})))
        return 0

    worker = DelegationWorker(depth=args.depth, session_id=args.session_id, max_steps=args.max_steps)
    try:
        worker.serve()
    except KeyboardInterrupt:
        return 130
    return 0


delegation = Delegation()

if __name__ == "__main__":
    raise SystemExit(main())
