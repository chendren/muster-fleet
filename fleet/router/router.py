#!/usr/bin/env python3
"""
fleet/router/router.py — Full router agent implementation
Routes goals to fleet workers, tracks requests, asserts memory/context.
"""

import json
import os
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / ".local" / "share" / "muster-fleet"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REQUESTS_LOG = DATA_DIR / "requests.jsonl"
MEMORY_DB = DATA_DIR / "memory.db"

BUS_DB = Path.home() / ".local" / "share" / "muster" / "bus.db"


def init_memory_db():
    conn = sqlite3.connect(MEMORY_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            scope TEXT,
            value TEXT,
            updated_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


def load_agents() -> List[Dict[str, Any]]:
    """Load agents via `muster agents` CLI."""
    try:
        out = subprocess.check_output(["muster", "agents"], text=True)
        agents = []
        for line in out.strip().split("\n"):
            if not line or "alias" in line.lower():
                continue
            parts = line.split()
            if len(parts) >= 2:
                agents.append({"alias": parts[0], "role": parts[1] if len(parts) > 1 else "worker"})
        return agents
    except Exception:
        return []


def pick_worker(preferred_role: Optional[str] = None, preferred_machine: Optional[str] = None) -> Optional[str]:
    agents = load_agents()
    if not agents:
        return None
    if preferred_role:
        for a in agents:
            if preferred_role.lower() in a.get("role", "").lower():
                return a["alias"]
    if preferred_machine:
        for a in agents:
            if preferred_machine.lower() in a.get("alias", "").lower():
                return a["alias"]
    # default: first worker
    for a in agents:
        if "worker" in a.get("role", "").lower():
            return a["alias"]
    return agents[0]["alias"] if agents else None


def log_request(request_id: str, goal: str, assignee: Optional[str], status: str = "routed"):
    entry = {
        "request_id": request_id,
        "goal": goal,
        "assignee": assignee,
        "status": status,
        "timestamp": int(time.time()),
        "iso": datetime.utcnow().isoformat() + "Z",
    }
    with open(REQUESTS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def create_muster_task(goal: str, assignee: str) -> bool:
    """Create task via muster CLI send."""
    try:
        subprocess.run([
            "muster", "send",
            "--to-kind", "agent",
            "--to-target", assignee,
            "--subject", f"ROUTED:{goal[:40]}",
            "--body", goal,
            "--intent", "action-requested"
        ], check=True, capture_output=True)
        return True
    except Exception:
        return False


def assert_context(request_id: str) -> Dict[str, Any]:
    """Load memory snippets for this request and attach."""
    init_memory_db()
    conn = sqlite3.connect(MEMORY_DB)
    c = conn.cursor()
    c.execute("SELECT key, scope, value FROM memory WHERE scope IN ('fleet','request') LIMIT 20")
    rows = c.fetchall()
    conn.close()
    snippets = [{"key": r[0], "scope": r[1], "value": r[2]} for r in rows]
    return {"request_id": request_id, "context": snippets}


def store_memory(key: str, scope: str, value: Any):
    init_memory_db()
    conn = sqlite3.connect(MEMORY_DB)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO memory (key, scope, value, updated_at)
        VALUES (?, ?, ?, ?)
    """, (key, scope, json.dumps(value) if not isinstance(value, str) else value, int(time.time())))
    conn.commit()
    conn.close()


def get_memory(key: str) -> Optional[str]:
    conn = sqlite3.connect(MEMORY_DB)
    c = conn.cursor()
    c.execute("SELECT value FROM memory WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def route_goal(goal: str, preferred_role: Optional[str] = None, preferred_machine: Optional[str] = None) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())[:8]
    assignee = pick_worker(preferred_role, preferred_machine)
    if not assignee:
        return {"error": "no workers available", "request_id": request_id}

    success = create_muster_task(goal, assignee)
    status = "routed" if success else "queued"
    log_request(request_id, goal, assignee, status)

    ctx = assert_context(request_id)
    return {
        "request_id": request_id,
        "assignee": assignee,
        "status": status,
        "context": ctx["context"],
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        goal = " ".join(sys.argv[1:])
        print(json.dumps(route_goal(goal), indent=2))
    else:
        print("Usage: router.py <goal text>")