"""
MCP (Model Context Protocol) Client - Free
Connect Hermus to external MCP servers (stdio) and expose their tools to the agent.

Config file: data/mcp_servers.json
[
  {
    "name": "filesystem",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    "env": {},
    "enabled": true
  }
]

Protocol: newline-delimited JSON-RPC (simple servers) OR Content-Length framing (spec).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from collections.abc import Callable

from .config import config


def _mcp_config_path() -> Path:
    return config.resolve_path(getattr(config, "mcp_servers_path", "data/mcp_servers.json"))


class MCPServerConnection:
    """Minimal JSON-RPC MCP client over stdio (NDJSON + Content-Length)."""

    def __init__(self, name: str, command: str, args: list[str] = None, env: dict = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self._tools: list[dict] = []
        self._initialized = False
        self.last_error: Optional[str] = None
        self._buf = b""

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def start(self, timeout: float = 12.0) -> bool:
        if self.proc and self.proc.poll() is None and self._initialized:
            return True
        self.stop()
        try:
            env = os.environ.copy()
            env.update({k: str(v) for k, v in self.env.items()})
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,  # unbuffered binary
            )
            self._buf = b""
            init = self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "hermus-agent-free", "version": "2.1"},
                },
                timeout=timeout,
            )
            if init.get("error"):
                self.last_error = str(init["error"])
                self.stop()
                return False
            self._notify("notifications/initialized", {})
            self._initialized = True
            tools_resp = self._request("tools/list", {}, timeout=timeout)
            if tools_resp.get("error"):
                self.last_error = str(tools_resp["error"])
                # still mark initialized; tools empty
                self._tools = []
                return True
            result = tools_resp.get("result") or {}
            self._tools = result.get("tools") or []
            return True
        except FileNotFoundError:
            self.last_error = f"Command not found: {self.command}"
            self.stop()
            return False
        except Exception as e:
            # capture stderr for debug
            err_extra = ""
            try:
                if self.proc and self.proc.stderr:
                    # non-blocking drain not always possible; peek via communicate only if dead
                    if self.proc.poll() is not None:
                        err_extra = (self.proc.stderr.read() or b"").decode("utf-8", "ignore")[:400]
            except Exception:
                pass
            self.last_error = f"{e}" + (f" | stderr: {err_extra}" if err_extra else "")
            self.stop()
            return False

    def stop(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    try:
                        self.proc.stdin.close()
                    except Exception:
                        pass
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self._initialized = False
        self._buf = b""

    def _write_message(self, msg: dict, framing: str = "ndjson"):
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP process not running")
        raw = json.dumps(msg).encode("utf-8")
        if framing == "content-length":
            header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
            self.proc.stdin.write(header + raw)
        else:
            self.proc.stdin.write(raw + b"\n")
        self.proc.stdin.flush()

    def _read_message(self, timeout: float = 12.0) -> dict:
        """Read one JSON-RPC message supporting NDJSON or Content-Length."""
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("MCP process not running")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                err = b""
                try:
                    err = self.proc.stderr.read() if self.proc.stderr else b""
                except Exception:
                    pass
                raise RuntimeError(
                    f"MCP server exited code={self.proc.returncode}: {err.decode('utf-8','ignore')[:300]}"
                )

            # Try parse from buffer first
            parsed = self._try_parse_buffer()
            if parsed is not None:
                return parsed

            # Read more bytes with timeout via select
            import select

            remaining = max(0.05, deadline - time.time())
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                continue
            chunk = self.proc.stdout.read(4096)
            if chunk:
                self._buf += chunk
            elif chunk == b"" and self.proc.poll() is not None:
                continue
        raise TimeoutError("MCP response timeout")

    def _try_parse_buffer(self) -> Optional[dict]:
        if not self._buf:
            return None
        # Content-Length framing
        if self._buf.lower().startswith(b"content-length:"):
            # Find header end
            sep = self._buf.find(b"\r\n\r\n")
            if sep < 0:
                sep = self._buf.find(b"\n\n")
                header_end = sep + 2 if sep >= 0 else -1
            else:
                header_end = sep + 4
            if sep < 0:
                return None
            header = self._buf[:sep].decode("utf-8", "ignore")
            length = None
            for line in header.splitlines():
                if line.lower().startswith("content-length:"):
                    try:
                        length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        length = None
            if length is None:
                # drop bad header line
                self._buf = self._buf[header_end:]
                return None
            body_start = header_end
            if len(self._buf) < body_start + length:
                return None
            body = self._buf[body_start : body_start + length]
            self._buf = self._buf[body_start + length :]
            return json.loads(body.decode("utf-8"))

        # NDJSON: one JSON object per line
        nl = self._buf.find(b"\n")
        if nl < 0:
            # maybe full object without newline yet — try decode whole buffer if looks complete
            try:
                txt = self._buf.decode("utf-8").strip()
                if txt.startswith("{") and txt.endswith("}"):
                    obj = json.loads(txt)
                    self._buf = b""
                    return obj
            except Exception:
                pass
            return None
        line = self._buf[:nl].strip()
        self._buf = self._buf[nl + 1 :]
        if not line:
            return self._try_parse_buffer()
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return self._try_parse_buffer()

    def _request(self, method: str, params: dict, timeout: float = 12.0) -> dict:
        with self._lock:
            req_id = self._next_id()
            msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            # Prefer NDJSON first (our echo server); many official servers use Content-Length
            self._write_message(msg, framing="ndjson")
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = max(0.1, deadline - time.time())
                msg_in = self._read_message(timeout=remaining)
                # skip notifications (no id)
                if "id" not in msg_in:
                    continue
                if msg_in.get("id") == req_id:
                    return msg_in
            raise TimeoutError(f"MCP response timeout waiting for id={req_id}")

    def _notify(self, method: str, params: dict):
        with self._lock:
            msg = {"jsonrpc": "2.0", "method": method, "params": params}
            self._write_message(msg, framing="ndjson")

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.start()
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict = None) -> dict:
        if not self._initialized and not self.start():
            return {"error": self.last_error or "MCP server failed to start", "server": self.name}
        try:
            resp = self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                timeout=60,
            )
            if resp.get("error"):
                return {"error": resp["error"], "server": self.name, "tool": tool_name}
            result = resp.get("result") or {}
            content = result.get("content")
            if isinstance(content, list):
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(c.get("text", ""))
                    else:
                        texts.append(str(c)[:500])
                return {
                    "success": True,
                    "server": self.name,
                    "tool": tool_name,
                    "content": "\n".join(texts)[:8000],
                    "isError": result.get("isError", False),
                    "raw": result,
                }
            return {"success": True, "server": self.name, "tool": tool_name, "result": result}
        except Exception as e:
            self.last_error = str(e)
            return {"error": str(e), "server": self.name, "tool": tool_name}


class MCPManager:
    """Manage multiple MCP server configs and expose tools to Hermus."""

    def __init__(self):
        self.servers: dict[str, MCPServerConnection] = {}
        self._config: list[dict] = []
        self.load_config()

    def load_config(self) -> list[dict]:
        path = _mcp_config_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            example = [
                {
                    "name": "example_echo",
                    "command": "python3",
                    "args": [str(config.base_dir / "tools" / "mcp_echo_server.py")],
                    "env": {},
                    "enabled": False,
                    "note": "Set enabled=true or: hermus mcp add --name echo --command python3 --arg tools/mcp_echo_server.py",
                }
            ]
            path.write_text(json.dumps(example, indent=2))
            self._config = example
            return self._config
        try:
            self._config = json.loads(path.read_text())
            if not isinstance(self._config, list):
                self._config = []
        except Exception:
            self._config = []
        return self._config

    def save_config(self):
        path = _mcp_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._config, indent=2))

    def add_server(
        self,
        name: str,
        command: str,
        args: list[str] = None,
        env: dict = None,
        enabled: bool = True,
    ) -> dict:
        self.load_config()
        self._config = [s for s in self._config if s.get("name") != name]
        entry = {
            "name": name,
            "command": command,
            "args": args or [],
            "env": env or {},
            "enabled": enabled,
        }
        self._config.append(entry)
        self.save_config()
        if name in self.servers:
            self.servers[name].stop()
            del self.servers[name]
        return {"success": True, "server": entry}

    def remove_server(self, name: str) -> dict:
        self.load_config()
        before = len(self._config)
        self._config = [s for s in self._config if s.get("name") != name]
        self.save_config()
        if name in self.servers:
            self.servers[name].stop()
            del self.servers[name]
        return {"success": True, "removed": before - len(self._config)}

    def list_servers(self) -> list[dict]:
        self.load_config()
        out = []
        for s in self._config:
            info = dict(s)
            conn = self.servers.get(s["name"])
            info["running"] = bool(conn and conn.proc and conn.proc.poll() is None)
            info["tool_count"] = len(conn._tools) if conn and conn._initialized else None
            info["last_error"] = conn.last_error if conn else None
            out.append(info)
        return out

    def _get_conn(self, entry: dict) -> MCPServerConnection:
        name = entry["name"]
        if name in self.servers:
            return self.servers[name]
        conn = MCPServerConnection(
            name=name,
            command=entry["command"],
            args=entry.get("args") or [],
            env=entry.get("env") or {},
        )
        self.servers[name] = conn
        return conn

    def connect_enabled(self) -> dict:
        self.load_config()
        results = []
        for entry in self._config:
            if not entry.get("enabled", True):
                results.append({"name": entry["name"], "success": False, "skipped": True, "reason": "disabled"})
                continue
            conn = self._get_conn(entry)
            ok = conn.start()
            results.append(
                {
                    "name": entry["name"],
                    "success": ok,
                    "tools": len(conn.list_tools()) if ok else 0,
                    "error": conn.last_error,
                }
            )
        return {"results": results}

    def get_tools_and_executors(self) -> tuple[list[dict], dict[str, Callable]]:
        self.load_config()
        definitions: list[dict] = []
        executors: dict[str, Callable] = {}

        for entry in self._config:
            if not entry.get("enabled", True):
                continue
            conn = self._get_conn(entry)
            if not conn._initialized:
                if not conn.start():
                    continue
            for tool in conn.list_tools():
                tname = tool.get("name") or "unknown"
                full_name = f"mcp_{entry['name']}_{tname}".replace("-", "_").replace(".", "_")
                schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": full_name,
                            "description": f"[MCP:{entry['name']}] " + (tool.get("description") or tname),
                            "parameters": schema,
                        },
                    }
                )

                def make_exec(server_conn: MCPServerConnection, original_name: str):
                    def _exec(**kwargs):
                        return server_conn.call_tool(original_name, kwargs)

                    return _exec

                executors[full_name] = make_exec(conn, tname)

        return definitions, executors

    def call(self, server: str, tool: str, arguments: dict = None) -> dict:
        self.load_config()
        entry = next((s for s in self._config if s.get("name") == server), None)
        if not entry:
            return {"error": f"Unknown MCP server: {server}"}
        conn = self._get_conn(entry)
        if not conn._initialized and not conn.start():
            return {"error": conn.last_error or "failed to start"}
        return conn.call_tool(tool, arguments or {})


mcp_manager = MCPManager()
