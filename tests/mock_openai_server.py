"""Tiny OpenAI-compatible mock server for testing custom base URLs."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

LOGS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models") or "/models" in self.path:
            self._send_json({
                "object": "list",
                "data": [
                    {"id": "test-model", "object": "model"},
                    {"id": "test-model-2", "object": "model"},
                ],
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw.decode(errors="replace")}
        auth = self.headers.get("Authorization", "")
        LOGS.append({
            "path": self.path,
            "auth": auth,
            "model": body.get("model"),
            "has_tools": "tools" in body,
        })
        self._send_json({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": body.get("model", "test-model"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": f"MOCK REPLY from {body.get('model')} via {self.path}"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        })


def serve(port=9999):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


if __name__ == "__main__":
    srv = serve()
    print("mock server on 127.0.0.1:9999")
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
