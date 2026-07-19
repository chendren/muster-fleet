#!/usr/bin/env python3
"""
hub_local.py — Dashboard collector for the 'hub' machine.

Scans ~/.claude/projects/**/*.jsonl for recent Claude Code activity and emits
a JSON payload matching the muster-fleet dashboard contract.

Stdlib only. Diagnostics go to stderr; only JSON is emitted on stdout.
"""

import json
import os
import sys
import time
import glob
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
MAX_AGE_SECONDS = 15 * 60  # 15 minutes


def find_most_recent_jsonl():
    """Return the most recently modified .jsonl under ~/.claude/projects/** or None."""
    pattern = str(CLAUDE_PROJECTS / "**" / "*.jsonl")
    candidates = glob.glob(pattern, recursive=True)
    if not candidates:
        return None

    candidates_with_mtime = []
    now = time.time()
    for path in candidates:
        try:
            mtime = os.path.getmtime(path)
            if (now - mtime) <= MAX_AGE_SECONDS:
                candidates_with_mtime.append((path, mtime))
        except OSError:
            continue

    if not candidates_with_mtime:
        return None

    candidates_with_mtime.sort(key=lambda x: x[1], reverse=True)
    return candidates_with_mtime[0][0]


def parse_jsonl(path):
    """Parse a Claude transcript JSONL and extract the required activity fields."""
    session_id = None
    cwd = None
    last_assistant_text = None
    last_tool_use = None
    turn_count = 0
    model = None
    tokens = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # sessionId at top level
                if "sessionId" in obj and session_id is None:
                    session_id = obj["sessionId"]

                # cwd at top level
                if "cwd" in obj and cwd is None:
                    cwd = obj["cwd"]

                # Only process assistant-type rows for messages/usage
                if obj.get("type") != "assistant":
                    continue

                message = obj.get("message", {})
                if not isinstance(message, dict):
                    continue

                # Model
                if model is None and "model" in message:
                    model = message["model"]

                # Usage tokens (nested under message.usage)
                usage = message.get("usage", {})
                if isinstance(usage, dict):
                    for k in tokens.keys():
                        if k in usage:
                            tokens[k] += usage[k]

                # Content blocks
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and last_assistant_text is None:
                            text = block.get("text", "")
                            if text:
                                last_assistant_text = text[:200]
                        elif block.get("type") == "tool_use":
                            name = block.get("name")
                            if name:
                                last_tool_use = name

                turn_count += 1

    except (OSError, IOError) as e:
        print(f"ERROR reading {path}: {e}", file=sys.stderr)
        return None

    # Build activity dict
    activity = {
        "source": "claude_transcript",
        "session_path": str(path),
        "session_id": session_id or "",
        "cwd": cwd or "",
        "last_message_preview": last_assistant_text or "",
        "last_tool": last_tool_use or "",
        "turn_count": turn_count,
        "model": model or "",
        "updated_at": datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat(),
        "tokens": tokens,
    }

    return activity


def main():
    result = {
        "machine": "hub",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "agents": [],
    }

    jsonl_path = find_most_recent_jsonl()
    if jsonl_path is None:
        # No recent activity — emit source:none
        result["agents"].append({
            "alias": "hub",
            "activity": {"source": "none"},
            "pane_snapshot": None,
        })
    else:
        activity = parse_jsonl(jsonl_path)
        if activity is None:
            # Parse failed — fall back to none
            result["agents"].append({
                "alias": "hub",
                "activity": {"source": "none"},
                "pane_snapshot": None,
            })
        else:
            result["agents"].append({
                "alias": "hub",
                "activity": activity,
                "pane_snapshot": None,
            })

    # Print ONLY JSON to stdout
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
