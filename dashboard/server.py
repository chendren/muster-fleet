#!/usr/bin/env python3
"""
server.py — muster fleet dashboard aggregator.

Merges muster's own bus.db (agents/threads/events) with local activity
collectors (hub_local.py run in-process, spoke_local.py run over SSH) into
one JSON payload at GET /api/status, and serves the static frontend at /.

Stdlib only. Run on the hub machine:

    python3 dashboard/server.py

Then open http://localhost:8787/
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# launchd-run processes don't inherit a shell PATH (e.g. no
# /opt/homebrew/bin), so a bare "tmux" lookup silently fails there even
# though it works fine from an interactive terminal. Search PATH first,
# then fall back to the common install locations.
def _find_tmux():
    found = shutil.which("tmux")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if os.path.exists(candidate):
            return candidate
    return None


TMUX_BIN = _find_tmux()

HERE = Path(__file__).resolve().parent
FRONTEND_DIR = HERE / "frontend"
COLLECTORS_DIR = HERE / "collectors"

MUSTER_DB = Path(os.environ.get("MUSTER_DB", str(Path.home() / ".local/share/muster/bus.db")))
HUB_COLLECTOR = COLLECTORS_DIR / "hub_local.py"
SPOKE_COLLECTOR = COLLECTORS_DIR / "spoke_local.py"
SPOKE_SSH_HOST = os.environ.get("MUSTER_SPOKE_SSH_HOST", "muster-remote")
SPOKE_REMOTE_PY = os.environ.get("MUSTER_SPOKE_REMOTE_PY", "python3")

PORT = int(os.environ.get("MUSTER_DASHBOARD_PORT", "8787"))
EVENTS_LIMIT = 50
PANE_CAPTURE_LINES = 200  # scroll back this many lines of the pane for drill-down


def capture_tmux_pane(pane_id):
    """Return the verbatim content of a local tmux pane, ANSI escapes
    preserved (-e), or None if tmux isn't installed or the pane is gone.

    Only ever runs against a LOCAL tmux server — this machine's. There is
    no equivalent for the spoke today (no tmux installed there), so this
    is only ever called for hub-machine agents; see merge_pane_snapshots.
    """
    if not pane_id or not TMUX_BIN:
        return None
    try:
        out = subprocess.run(
            [TMUX_BIN, "capture-pane", "-e", "-p", "-t", pane_id,
             "-S", f"-{PANE_CAPTURE_LINES}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

# Static mapping of alias -> machine. muster's own schema doesn't record
# which physical machine an agent is on (by design — it's meant to be
# location-agnostic), so this is maintained by hand. Extend it as new
# agents join either machine.
ALIAS_MACHINE = {
    "chad-mac": "hub",
    "grok-hub": "hub",
    "claude-remot": "spoke",
    "grok-remote": "spoke",
    "grok-mbp": "spoke",
    "grok-mbp-worker": "spoke",
    "macbookpro-test": "spoke",
}


def local_tmux_pane_ids():
    """Every pane_id currently live on THIS machine's tmux server. Used to
    auto-detect "hub" for tmux-registered agents without hardcoding every
    new alias into ALIAS_MACHINE by hand.
    """
    if not TMUX_BIN:
        return set()
    try:
        out = subprocess.run(
            [TMUX_BIN, "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return set()
        return set(out.stdout.split())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()


def now_ms():
    return int(time.time() * 1000)


def query_bus_db():
    """Read agents/threads/events directly from muster's SQLite file.

    Read-only connection (mode=ro) so this never contends with or blocks
    the daemon's own writer.
    """
    uri = f"file:{MUSTER_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        agents = [dict(r) for r in conn.execute(
            "SELECT alias, role, model_type, session_name, pane_id, project, label, "
            "departed, registered_at, last_seen FROM agents ORDER BY alias"
        )]
        threads = [dict(r) for r in conn.execute(
            "SELECT id, kind, from_agent, to_kind, to_target, subject, status, "
            "intent, created_at, updated_at FROM threads ORDER BY updated_at DESC"
        )]
        events = [dict(r) for r in conn.execute(
            "SELECT id, ts, kind, agent, target, thread_id, count, detail "
            "FROM events ORDER BY id DESC LIMIT ?", (EVENTS_LIMIT,)
        )]
    finally:
        conn.close()
    return agents, threads, events


def run_hub_collector():
    try:
        out = subprocess.run(
            [sys.executable, str(HUB_COLLECTOR)],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            print(f"hub collector exited {out.returncode}: {out.stderr}", file=sys.stderr)
            return None
        return json.loads(out.stdout)
    except Exception as e:
        print(f"hub collector failed: {e}", file=sys.stderr)
        return None


def run_spoke_collector():
    try:
        out = subprocess.run(
            ["ssh", SPOKE_SSH_HOST, SPOKE_REMOTE_PY, "-"],
            input=SPOKE_COLLECTOR.read_text(),
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            print(f"spoke collector exited {out.returncode}: {out.stderr}", file=sys.stderr)
            return None
        return json.loads(out.stdout)
    except Exception as e:
        print(f"spoke collector failed: {e}", file=sys.stderr)
        return None


def merge_activity(agents, collector_payload, machine):
    """Attach a collector's single detected session to the best-matching
    alias on that machine, per the heuristic documented in CONTRACT.md:
    the most-recently-seen alias on that machine whose model_type matches
    the collector's source (claude_transcript -> claude, grok_* -> grok).
    """
    if not collector_payload:
        return
    for entry in collector_payload.get("agents", []):
        activity = entry.get("activity", {})
        source = activity.get("source", "none")
        if source == "none":
            continue
        wants_type = "claude" if source == "claude_transcript" else "grok"

        candidates = [
            a for a in agents
            if ALIAS_MACHINE.get(a["alias"]) == machine
            and a["model_type"] == wants_type
            and not a["departed"]
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda a: a["last_seen"], reverse=True)
        target = candidates[0]
        target["activity"] = activity
        target["pane_snapshot"] = entry.get("pane_snapshot")


def parse_iso_to_ms(iso_str):
    """Parse an ISO-8601 timestamp (with or without trailing Z) to epoch ms."""
    if not iso_str:
        return None
    try:
        s = iso_str.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def build_status():
    agents, threads, events = query_bus_db()
    local_panes = local_tmux_pane_ids()

    for a in agents:
        if a["alias"] in ALIAS_MACHINE:
            a["machine"] = ALIAS_MACHINE[a["alias"]]
        elif a["pane_id"] and a["pane_id"] in local_panes:
            a["machine"] = "hub"
        else:
            a["machine"] = "unknown"
        a["activity"] = {"source": "none"}
        a["pane_snapshot"] = None
        # attach current task: most recently updated open/claimed/needs_info/
        # blocked task addressed to this alias
        open_statuses = {"open", "claimed", "needs_info", "blocked"}
        my_tasks = [
            t for t in threads
            if t["kind"] == "task" and t["to_target"] == a["alias"]
            and t["status"] in open_statuses
        ]
        my_tasks.sort(key=lambda t: t["updated_at"], reverse=True)
        a["current_task"] = my_tasks[0] if my_tasks else None

    hub_payload = run_hub_collector()
    spoke_payload = run_spoke_collector()
    merge_activity(agents, hub_payload, "hub")
    merge_activity(agents, spoke_payload, "spoke")

    # Verbatim live pane capture — only possible for agents with a real
    # tmux pane_id on THIS (hub) machine's tmux server. The spoke has no
    # tmux installed today, so this never fires for spoke agents; they
    # keep pane_snapshot: null with the "no live pane" explanation the
    # frontend already renders calmly, not as an error.
    for a in agents:
        if a["machine"] == "hub" and a["pane_id"] and a["pane_id"] in local_panes:
            snapshot = capture_tmux_pane(a["pane_id"])
            if snapshot is not None:
                a["pane_snapshot"] = snapshot

    # "live" combines two independent signals, taking whichever is fresher:
    # muster's own last_seen (only updated by explicit bus calls — register,
    # send, task ops — NOT a continuous heartbeat) and the collector's
    # activity.updated_at (the local session's own transcript/log mtime,
    # which for an actively-working CLI session updates on every turn
    # regardless of whether it happens to touch the muster bus that turn).
    # Using last_seen alone under-reports liveness for a session that's
    # genuinely active but quiet on the bus for a stretch.
    for a in agents:
        candidates = [a["last_seen"]]
        act_ts = parse_iso_to_ms(a["activity"].get("updated_at"))
        if act_ts is not None:
            candidates.append(act_ts)
        most_recent = max(candidates)
        a["live"] = (now_ms() - most_recent) < 60_000 and not a["departed"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": agents,
        "threads": threads,
        "events": events,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean; default logging goes to stderr anyway

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            try:
                self._send_json(build_status())
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        # static file serving for the frontend
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        fs_path = (FRONTEND_DIR / path.lstrip("/")).resolve()
        if FRONTEND_DIR not in fs_path.parents and fs_path != FRONTEND_DIR:
            self.send_response(403)
            self.end_headers()
            return
        if not fs_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        content_type = "text/html" if fs_path.suffix == ".html" else "application/octet-stream"
        body = fs_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"muster fleet dashboard: http://localhost:{PORT}/", file=sys.stderr)
    print(f"  API:  http://localhost:{PORT}/api/status", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
