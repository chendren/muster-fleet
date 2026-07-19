#!/usr/bin/env python3
"""Spoke-side local activity collector for the muster fleet dashboard.

Emits one JSON document on stdout matching dashboard/CONTRACT.md (machine=spoke).
Diagnostics go to stderr only — stdout is sacred for the SSH/aggregator pipeline.

Stdlib only. No pip deps. No TTY assumptions.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Recent-session window: sessions older than this are treated as absent.
RECENT_SECONDS = 15 * 60
PREVIEW_CHARS = 200
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
GROK_SESSIONS = HOME / ".grok" / "sessions"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_ts(value: Any) -> Optional[float]:
    """Parse an ISO-8601 timestamp (with optional Z) to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: ms vs s
        v = float(value)
        if v > 1e12:
            return v / 1000.0
        return v
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def is_recent(ts: Optional[float], now: float) -> bool:
    if ts is None:
        return False
    return (now - ts) <= RECENT_SECONDS


def preview_text(text: str, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def extract_text_from_content(content: Any) -> Optional[str]:
    """Pull plain text from Claude/Grok content blocks (str | list | dict)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content if content.strip() else None
    if isinstance(content, dict):
        if content.get("type") == "text" and content.get("text"):
            return str(content["text"])
        if "text" in content and content["text"]:
            return str(content["text"])
        return None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            t = extract_text_from_content(block)
            if t:
                parts.append(t)
        joined = "\n".join(parts).strip()
        return joined or None
    return None


# ---------------------------------------------------------------------------
# Claude Code transcripts: ~/.claude/projects/**/*.jsonl
# ---------------------------------------------------------------------------
# Alias-to-session mapping is best-effort (see CONTRACT.md). This collector
# does not receive the muster agents list; it reports activity only and leaves
# alias empty so the aggregator can attach by machine + source/model_type.


def find_newest_claude_jsonl(now: float) -> Optional[Path]:
    if not CLAUDE_PROJECTS.is_dir():
        log(f"claude projects dir missing: {CLAUDE_PROJECTS}")
        return None
    newest: Optional[Path] = None
    newest_mtime = -1.0
    for path in CLAUDE_PROJECTS.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            log(f"stat failed {path}: {exc}")
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest = path
    if newest is None:
        log("no claude *.jsonl files found")
        return None
    if not is_recent(newest_mtime, now):
        age = int(now - newest_mtime)
        log(f"newest claude session stale ({age}s ago): {newest}")
        return None
    log(f"claude session: {newest} (mtime {int(now - newest_mtime)}s ago)")
    return newest


def parse_claude_transcript(path: Path) -> dict[str, Any]:
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    last_text: Optional[str] = None
    last_tool: Optional[str] = None
    turn_count = 0
    model: Optional[str] = None
    tokens = {k: 0 for k in TOKEN_KEYS}
    saw_usage = False
    updated_at: Optional[str] = None

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log(f"read failed {path}: {exc}")
        return {"source": "none"}

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        if obj.get("sessionId") and not session_id:
            session_id = str(obj["sessionId"])
        if obj.get("cwd"):
            cwd = str(obj["cwd"])
        if obj.get("timestamp"):
            updated_at = str(obj["timestamp"])

        # Sum usage from any message.usage object (per contract).
        usage = None
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        msg = obj.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            usage = msg["usage"]
        if usage:
            for k in TOKEN_KEYS:
                if k in usage and isinstance(usage[k], (int, float)):
                    tokens[k] += int(usage[k])
                    saw_usage = True

        if obj.get("type") != "assistant":
            continue

        turn_count += 1
        if isinstance(msg, dict):
            m = msg.get("model")
            if m and m != "<synthetic>":
                model = str(m)
            content = msg.get("content")
        else:
            content = obj.get("content")

        text = extract_text_from_content(content)
        if text:
            last_text = text

        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name")
                    if name:
                        last_tool = str(name)

    # Prefer session id from filename stem when top-level was missing.
    if not session_id:
        session_id = path.stem

    activity: dict[str, Any] = {
        "source": "claude_transcript",
        "session_path": str(path),
        "session_id": session_id,
        "turn_count": turn_count,
    }
    if cwd:
        activity["cwd"] = cwd
    if last_text:
        activity["last_message_preview"] = preview_text(last_text)
    if last_tool:
        activity["last_tool"] = last_tool
    if model:
        activity["model"] = model
    if updated_at:
        activity["updated_at"] = updated_at
    # tokens present ONLY for claude_transcript — include when we saw any usage.
    if saw_usage:
        activity["tokens"] = tokens
    return activity


def collect_claude(now: float) -> Optional[dict[str, Any]]:
    path = find_newest_claude_jsonl(now)
    if path is None:
        return None
    return parse_claude_transcript(path)


# ---------------------------------------------------------------------------
# Native Grok CLI: ~/.grok/sessions/**/summary.json (+ sibling chat_history.jsonl)
# Keyed by updated_at inside the file, not mtime.
# ---------------------------------------------------------------------------


def find_newest_grok_summary(now: float) -> Optional[tuple[Path, dict[str, Any]]]:
    if not GROK_SESSIONS.is_dir():
        log(f"grok sessions dir missing: {GROK_SESSIONS}")
        return None

    best_path: Optional[Path] = None
    best_data: Optional[dict[str, Any]] = None
    best_ts = -1.0

    for path in GROK_SESSIONS.rglob("summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as exc:
            log(f"skip grok summary {path}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        ts = parse_iso_ts(data.get("updated_at") or data.get("last_active_at"))
        if ts is None:
            try:
                ts = path.stat().st_mtime
            except OSError:
                continue
        if ts > best_ts:
            best_ts = ts
            best_path = path
            best_data = data

    if best_path is None or best_data is None:
        log("no grok summary.json files found")
        return None
    if not is_recent(best_ts, now):
        age = int(now - best_ts)
        log(f"newest grok session stale ({age}s ago): {best_path}")
        return None
    log(f"grok session: {best_path} (updated_at age {int(now - best_ts)}s)")
    return best_path, best_data


def grok_last_message_preview(session_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """Return (last_message_preview, last_tool) from sibling chat_history.jsonl."""
    hist = session_dir / "chat_history.jsonl"
    if not hist.is_file():
        return None, None
    last_text: Optional[str] = None
    last_tool: Optional[str] = None
    try:
        raw = hist.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log(f"read failed {hist}: {exc}")
        return None, None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type") or obj.get("role")
        if kind == "assistant":
            text = extract_text_from_content(obj.get("content"))
            if text:
                last_text = text
            for tc in obj.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("name"):
                    last_tool = str(tc["name"])
        elif kind in ("user", "human"):
            # Fallback preview if no assistant text yet
            text = extract_text_from_content(obj.get("content"))
            if text and last_text is None:
                last_text = text
    return last_text, last_tool


def collect_grok(now: float) -> Optional[dict[str, Any]]:
    found = find_newest_grok_summary(now)
    if found is None:
        return None
    path, data = found
    info = data.get("info") if isinstance(data.get("info"), dict) else {}

    # Contract: session_id from "id" field — live files nest it under info.id.
    session_id = data.get("id") or info.get("id") or path.parent.name
    model = data.get("current_model_id")
    turn_count = data.get("num_chat_messages")
    if not isinstance(turn_count, int):
        turn_count = data.get("num_messages")
    cwd = info.get("cwd") or data.get("cwd") or data.get("git_root_dir")
    updated_at = data.get("updated_at") or data.get("last_active_at")

    last_text, last_tool = grok_last_message_preview(path.parent)

    activity: dict[str, Any] = {
        "source": "grok_native_session",
        "session_id": str(session_id) if session_id else path.parent.name,
        "session_path": str(path.parent),
    }
    if cwd:
        activity["cwd"] = str(cwd).rstrip("/") or str(cwd)
    if last_text:
        activity["last_message_preview"] = preview_text(last_text)
    if last_tool:
        activity["last_tool"] = last_tool
    if isinstance(turn_count, int):
        activity["turn_count"] = turn_count
    if model:
        activity["model"] = str(model)
    if updated_at:
        activity["updated_at"] = str(updated_at)
    # Intentionally NO tokens field — Grok CLI does not expose token counts.
    return activity


# ---------------------------------------------------------------------------
# Assemble payload
# ---------------------------------------------------------------------------


def build_agent_entry(activity: dict[str, Any], alias: Optional[str] = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "activity": activity,
        # Spoke currently has no tmux capture path for agents; null is expected.
        "pane_snapshot": None,
    }
    if alias:
        entry["alias"] = alias
    return entry


def collect() -> dict[str, Any]:
    now = time.time()
    agents: list[dict[str, Any]] = []

    claude_activity = collect_claude(now)
    if claude_activity is not None:
        agents.append(build_agent_entry(claude_activity))

    grok_activity = collect_grok(now)
    if grok_activity is not None:
        agents.append(build_agent_entry(grok_activity))

    if not agents:
        # Nothing recent for either CLI — accurate absence, not a bug.
        agents.append(build_agent_entry({"source": "none"}))
        log("no recent claude or grok sessions; emitting source=none")

    return {
        "machine": "spoke",
        "collected_at": utc_now_iso(),
        "agents": agents,
    }


def main() -> int:
    try:
        payload = collect()
    except Exception as exc:  # last-resort: still emit valid JSON shape
        log(f"collector error: {exc}")
        payload = {
            "machine": "spoke",
            "collected_at": utc_now_iso(),
            "agents": [
                {
                    "activity": {"source": "none"},
                    "pane_snapshot": None,
                }
            ],
        }
    # stdout: JSON only, single document, trailing newline
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
