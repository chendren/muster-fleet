#!/usr/bin/env python3
"""Append-only voice utterance log (spoken phrases + tool routing)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(os.environ.get(
    "MUSTER_VOICE_LOG_DIR",
    Path.home() / ".local" / "share" / "muster-voice",
))
LOG_PATH = LOG_DIR / "utterances.jsonl"


def log_event(event: dict) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch_ms": int(time.time() * 1000),
    }
    row.update(event or {})
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # also mirror a human line to stderr for launchd logs
    phrase = row.get("transcript") or row.get("text") or ""
    tools = row.get("tool_calls") or []
    print(
        f"[voice] heard={phrase!r} tools={len(tools)} speech={row.get('speech')!r}",
        flush=True,
    )
    return str(LOG_PATH)


def tail(n: int = 30):
    if not LOG_PATH.is_file():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines[-max(1, n):]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line})
    return out


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    for row in tail(n):
        print(json.dumps(row, indent=2))
