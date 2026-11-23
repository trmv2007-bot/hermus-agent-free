"""A local, OpenAI-compatible model for the room's browser tests.

Hermus talks to Ollama through the same ``/v1/chat/completions`` shape every
other provider speaks, so a tiny HTTP server on the default Ollama port is
enough to exercise the real streaming path end to end — no Ollama install, no
network, no key, nothing billed. It is a *model*, not a mock of the product:
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
        if not wants_stream:
            self._json(
                {
                    "id": "chatcmpl-room",
                    "object": "chat.completion",
                    "model": MODEL_NAME,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": ANSWER_TEXT},
                            "finish_reason": "stop",
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

        pieces = [ANSWER_TEXT[:18], ANSWER_TEXT[18:38], ANSWER_TEXT[38:]]
        try:
            for i, piece in enumerate(pieces):
                if i == len(pieces) - 1 and SLOW_MS:
                    import time

                    time.sleep(SLOW_MS / 1000.0)
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


def serve(port: int = 11434) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def port_busy(port: int = 11434) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0
