#!/usr/bin/env python3
"""Muster Fleet Discovery — scans local system for active CLI sessions."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

HOME = Path.home()
OUT_DIR = HOME / ".local/share/muster-fleet"
OUT_FILE = OUT_DIR / "discovery.json"


def run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except Exception:
        return ""


def discover():
    sessions = []

    # 1. tmux sessions
    tmux_out = run(["tmux", "ls", "-F", "#{session_name}:#{session_attached}"])
    for line in tmux_out.strip().splitlines():
        if not line:
            continue
        name, attached = line.split(":", 1)
        sessions.append({
            "type": "tmux",
            "name": name,
            "attached": attached == "1",
            "ts": int(time.time())
        })

    # 2. pgrep for claude / grok processes (simple heuristic)
    pgrep_out = run(["pgrep", "-a", "-f", "(claude|codex|grok)"])
    for line in pgrep_out.strip().splitlines():
        if not line:
            continue
        pid, *rest = line.split()
        cmd = " ".join(rest)
        m = re.search(r"(claude|codex|grok)[-_](\w+)", cmd, re.I)
        role = m.group(2) if m else "unknown"
        sessions.append({
            "type": "process",
            "name": cmd[:80],
            "pid": int(pid),
            "role": role.lower(),
            "ts": int(time.time())
        })

    # 3. /tmp/muster-loop-*.pid files
    for pidfile in Path("/tmp").glob("muster-loop-*.pid"):
        try:
            pid = int(pidfile.read_text().strip())
            sessions.append({
                "type": "loop-pid",
                "name": pidfile.name,
                "pid": pid,
                "ts": int(time.time())
            })
        except Exception:
            pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {"sessions": sessions, "generated_at": int(time.time())}
    OUT_FILE.write_text(json.dumps(data, indent=2))
    return data


if __name__ == "__main__":
    print(json.dumps(discover(), indent=2))