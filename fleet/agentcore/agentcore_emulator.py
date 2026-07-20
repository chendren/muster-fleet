#!/usr/bin/env python3
"""Local AgentCore Runtime emulator (EPIC-4).

Minimal surface:
  GET  /health
  GET  /agents
  POST /invoke
  GET/PUT /sessions/{id}/memory

All responses include X-Request-Id header for tracing.
"""
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8790
SESSIONS = {}  # in-memory {session_id: {memory: {...}}}


def rid():
    return str(uuid.uuid4())


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Request-Id", rid())
        self.end_headers()
        if isinstance(body, (dict, list)):
            self.wfile.write(json.dumps(body, indent=2).encode())
        else:
            self.wfile.write(body.encode())

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/health":
            self._send(200, {"status": "ok", "service": "agentcore-emulator", "ts": time.time()})
        elif p.path == "/agents":
            self._send(200, {"agents": [{"id": "echo", "name": "EchoAgent"}]})
        elif p.path.startswith("/sessions/") and p.path.endswith("/memory"):
            sid = p.path.split("/")[2]
            mem = SESSIONS.get(sid, {}).get("memory", {})
            self._send(200, {"session_id": sid, "memory": mem})
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self):
        p = urlparse(self.path)
        if p.path.startswith("/sessions/") and p.path.endswith("/memory"):
            sid = p.path.split("/")[2]
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            SESSIONS.setdefault(sid, {})["memory"] = data.get("memory", {})
            self._send(200, {"session_id": sid, "memory": SESSIONS[sid]["memory"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/invoke":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            agent_id = data.get("agent_id", "echo")
            session_id = data.get("session_id", rid())
            inp = data.get("input", "")
            # simple echo agent
            result = {"output": f"[{agent_id}] echo: {inp}", "session_id": session_id}
            self._send(200, result)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print(f"[agentcore] {fmt % args}")


def main():
    print(f"AgentCore emulator listening on {HOST}:{PORT}")
    server = HTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()