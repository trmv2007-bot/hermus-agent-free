"""A local, OpenAI-compatible model for the room's browser tests.

Hermus talks to Ollama through the same ``/v1/chat/completions`` shape every
other provider speaks, so a tiny HTTP server on a port this suite owns is enough
to exercise the real streaming path end to end — no Ollama install, no network, no
key, nothing billed, and nothing that a real service or a sibling test run can
take away from it. It is a *model*, not a mock of the product:
the gateway, the run bus, the job result and the page are all real.

``GET /api/tags`` answers the probe ``core/brain.py`` uses to decide whether a
local model exists, so the room sees an honest "local" brain instead of the
stub, and a typed question really travels through the agent loop.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL_NAME = "room-test:1b"

#: What the fake model answers. The room test asserts this text reaches the
#: conversation, so keep it distinctive and stable.
ANSWER_TEXT = "I am here. This answer came from the local model on your machine."

#: Milliseconds spent before the last token, so a test can catch a turn that is
#: genuinely still running and press Stop.
SLOW_MS = 0

#: A gate a test can open when it has finished looking at the turn in flight.
#: The stub waits on it before its last token, so "mid-flight" is a state the
#: test chooses rather than a race it wins.
GATE = None


def _hold(after_tools: bool = True) -> None:
    """Wait out a test's gate and its delay, before the answer is written."""
    if GATE is not None and after_tools:
        GATE.wait(timeout=120)
    if SLOW_MS:
        import time

        time.sleep(SLOW_MS / 1000.0)


#: A test may script a turn instead of letting the model only talk:
#:
#: * ``{"tools": [{"name": ..., "arguments": {...}}, ...], "answer": "..."}``
#:   makes the model open by calling those tools, then answer with ``answer``
#:   once the tool results come back — a real multi-step turn through the real
#:   agent loop, with real tool results for the receipt;
#: * ``{"claim": "..."}`` makes it assert work it never did, which is how an
#:   honest "Not verified" is produced without a broken product.
#:
#: Empty means the model simply answers ``ANSWER_TEXT``.
SCRIPT: dict = {}


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 semantics on purpose: an SSE body is delimited by the close, so
    # the client sees the end of the stream instead of waiting on a kept-alive
    # socket that has nothing more to send.
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence the test log
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server naming
        if self.path.rstrip("/").endswith("/api/tags"):
            self._json({"models": [{"name": MODEL_NAME, "model": MODEL_NAME}]})
            return
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]})
            return
        self._json({"ok": True})

    def do_POST(self):  # noqa: N802 - http.server naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        wants_stream = bool(body.get("stream"))
        script = dict(SCRIPT)
        after_tools = "Tool results (step" in json.dumps(body.get("messages") or [], default=str)
        tools = list(script.get("tools") or []) if (not after_tools and body.get("tools")) else []
        answer = ANSWER_TEXT
        if after_tools and script.get("answer"):
            answer = str(script["answer"])
        elif script.get("claim"):
            answer = str(script["claim"])
        if not wants_stream:
            # The gate holds only the answer that follows real tool work: on a
            # turn's first model call it would stall the plan itself.
            _hold(after_tools)
            message = {"role": "assistant", "content": "" if tools else answer}
            if tools:
                message["tool_calls"] = _wire_tools(tools)
            self._json(
                {
                    "id": "chatcmpl-room",
                    "object": "chat.completion",
                    "model": MODEL_NAME,
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if tools else "stop",
                        }
                    ],
                }
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if tools:
            self._stream_tool_calls(tools)
            return

        pieces = [answer[:18], answer[18:38], answer[38:]]
        try:
            for i, piece in enumerate(pieces):
                if i == len(pieces) - 1:
                    _hold()
                chunk = {
                    "id": "chatcmpl-room",
                    "object": "chat.completion.chunk",
                    "model": MODEL_NAME,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            final = {
                "id": "chatcmpl-room",
                "object": "chat.completion.chunk",
                "model": MODEL_NAME,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The person pressed Stop; the socket closing is the expected result.
            return

    def _stream_tool_calls(self, tools: list[dict]) -> None:
        """One streamed tool-call chunk per call, then the stop that ends the turn."""
        try:
            for index, call in enumerate(_wire_tools(tools)):
                chunk = {
                    "id": "chatcmpl-room",
                    "object": "chat.completion.chunk",
                    "model": MODEL_NAME,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [dict(call, index=index)]},
                            "finish_reason": None,
                        }
                    ],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            final = {
                "id": "chatcmpl-room",
                "object": "chat.completion.chunk",
                "model": MODEL_NAME,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def _wire_tools(tools: list[dict]) -> list[dict]:
    """A scripted call in the OpenAI shape the gateway parses."""
    return [
        {
            "id": f"call_{index}",
            "type": "function",
            "function": {"name": str(call.get("name") or ""), "arguments": json.dumps(call.get("arguments") or {})},
        }
        for index, call in enumerate(tools)
    ]


#: The base URL of the stub that is serving, set by :func:`serve`. The gateway
#: is pointed here, so the suite never assumes a port another process may hold —
#: a real Ollama daemon or a second test run used to make these tests skip.
BASE = ""


def serve(port: int = 0) -> HTTPServer:
    """Serve the stub on ``port``; 0 means an ephemeral port, read back into BASE."""
    global BASE
    server = HTTPServer(("127.0.0.1", port), _Handler)
    BASE = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
