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
from urllib.parse import parse_qs

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
EVENTS_LIMIT = 200
ENTRIES_LIMIT = 300
PANE_CAPTURE_LINES = 400  # scroll back this many lines of the pane for drill-down

VOICE_DIR = HERE / "voice"
VOICE_VENV_PY = VOICE_DIR / ".venv" / "bin" / "python3"
VOICE_MODELS = Path(os.environ.get(
    "MUSTER_VOICE_MODELS",
    str(Path.home() / ".local" / "share" / "muster-voice" / "models"),
))


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


def capture_tmux_pane_meta(pane_id):
    """Return live geometry / process metadata for a local tmux pane.

    Keys: pane_id, session, width, height, command, title, pid.
    None if the pane is gone or tmux is unavailable.
    """
    if not pane_id or not TMUX_BIN:
        return None
    try:
        out = subprocess.run(
            [TMUX_BIN, "list-panes", "-a", "-F",
             "#{pane_id}|#{session_name}|#{pane_width}|#{pane_height}|"
             "#{pane_current_command}|#{pane_title}|#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            parts = line.split("|", 6)
            if len(parts) < 7:
                continue
            if parts[0] != pane_id:
                continue
            try:
                width = int(parts[2])
                height = int(parts[3])
                pid = int(parts[6]) if parts[6] else None
            except ValueError:
                width = height = None
                pid = None
            return {
                "pane_id": parts[0],
                "session": parts[1],
                "width": width,
                "height": height,
                "command": parts[4],
                "title": parts[5],
                "pid": pid,
            }
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# Static mapping of alias -> machine. muster's own schema doesn't record
# which physical machine an agent is on (by design — it's meant to be
# location-agnostic), so this is maintained by hand. Extend it as new
# agents join either machine. Agents with a local tmux pane_id are also
# auto-tagged "hub" below even if missing from this map.
ALIAS_MACHINE = {
    "chad-mac": "hub",
    "grok-hub": "hub",
    "grok-hub-a": "hub",
    "grok-hub-b": "hub",
    "grok-hub-c": "hub",
    "hub-tui-claude": "hub",
    "MacStudioGrok1": "hub",
    "macstudio-grok1": "hub",
    "claude-remot": "spoke",
    "grok-remote": "spoke",
    "grok-mbp": "spoke",
    "grok-mbp-worker": "spoke",
    "grok-spoke-a": "spoke",
    "grok-spoke-b": "spoke",
    "macbookpro-test": "spoke",
    "ClaudeMacBookPro1": "spoke",
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


def agent_by_alias(alias):
    """Look up a single agent row from bus.db by alias, or None."""
    if not alias:
        return None
    uri = f"file:{MUSTER_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT alias, role, model_type, session_name, pane_id, project, label, "
            "departed, registered_at, last_seen FROM agents WHERE alias = ?",
            (alias,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def resolve_machine(alias, pane_id, local_panes):
    """Decide which physical machine an agent is on.

    Prefer the hand-maintained ALIAS_MACHINE map. Only fall back to
    "pane_id is on this box's tmux" when the alias is unmapped — and never
    treat a spoke-mapped alias as hub just because pane ids collide (%0
    exists independently on every tmux server).
    """
    if alias in ALIAS_MACHINE:
        return ALIAS_MACHINE[alias]
    if pane_id and pane_id in local_panes:
        return "hub"
    return "unknown"


def capture_spoke_tmux_pane(pane_id):
    """Capture a pane from the spoke over SSH. Returns (snapshot, meta).

    Best-effort: if ssh/tmux fails, returns (None, None). Uses explicit
    PATH on the remote so non-interactive SSH can still find tmux.
    """
    if not pane_id:
        return None, None
    # Quote pane_id — values look like %0 and must not be word-split / percent-expanded.
    safe_pane = pane_id.replace("'", "'\\''")
    remote = (
        "export PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\"; "
        f"tmux capture-pane -e -p -t '{safe_pane}' -S -{PANE_CAPTURE_LINES} 2>/dev/null; "
        "echo '__MUSTER_PANE_META__'; "
        "tmux list-panes -a -F "
        "'#{pane_id}|#{session_name}|#{pane_width}|#{pane_height}|"
        "#{pane_current_command}|#{pane_title}|#{pane_pid}' 2>/dev/null"
    )
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes",
             SPOKE_SSH_HOST, remote],
            capture_output=True, text=True, timeout=12,
        )
        if out.returncode != 0:
            return None, None
        text = out.stdout
        if "__MUSTER_PANE_META__" not in text:
            return text or None, None
        snap, _, meta_blob = text.partition("__MUSTER_PANE_META__\n")
        snapshot = snap if snap else None
        meta = None
        for line in meta_blob.splitlines():
            parts = line.split("|", 6)
            if len(parts) < 7 or parts[0] != pane_id:
                continue
            try:
                width = int(parts[2]); height = int(parts[3])
                pid = int(parts[6]) if parts[6] else None
            except ValueError:
                width = height = pid = None
            meta = {
                "pane_id": parts[0],
                "session": parts[1],
                "width": width,
                "height": height,
                "command": parts[4],
                "title": parts[5],
                "pid": pid,
                "remote": True,
            }
            break
        return snapshot, meta
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"spoke pane capture failed: {e}", file=sys.stderr)
        return None, None


def build_pane_payload(alias):
    """Fast path for the live terminal viewer: just this agent's pane.

    Hub panes are local tmux captures. Spoke panes go over SSH. Avoids the
    full collector pipeline so the UI can poll ~1s while a terminal is open.
    """
    agent = agent_by_alias(alias)
    if not agent:
        return {"error": f"unknown alias: {alias}", "alias": alias}, 404

    pane_id = agent.get("pane_id") or ""
    local_panes = local_tmux_pane_ids()
    machine = resolve_machine(alias, pane_id, local_panes)

    snapshot = None
    meta = None
    if pane_id:
        if machine == "hub" and pane_id in local_panes:
            snapshot = capture_tmux_pane(pane_id)
            meta = capture_tmux_pane_meta(pane_id)
        elif machine == "spoke":
            snapshot, meta = capture_spoke_tmux_pane(pane_id)

    return {
        "alias": alias,
        "pane_id": pane_id or None,
        "machine": machine,
        "model_type": agent.get("model_type"),
        "role": agent.get("role"),
        "session_name": agent.get("session_name") or None,
        "pane_snapshot": snapshot,
        "pane_meta": meta,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "has_live_pane": snapshot is not None,
    }, 200


def now_ms():
    return int(time.time() * 1000)


def query_bus_db():
    """Read agents/threads/events/entries directly from muster's SQLite file.

    Read-only connection (mode=ro) so this never contends with or blocks
    the daemon's own writer. Entries power the collaboration sequence view.
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
            "intent, ref, created_at, updated_at FROM threads ORDER BY updated_at DESC"
        )]
        events = [dict(r) for r in conn.execute(
            "SELECT id, ts, kind, agent, target, thread_id, count, detail "
            "FROM events ORDER BY id DESC LIMIT ?", (EVENTS_LIMIT,)
        )]
        entries = [dict(r) for r in conn.execute(
            "SELECT id, thread_id, from_agent, body, status_change, created_at "
            "FROM entries ORDER BY id DESC LIMIT ?", (ENTRIES_LIMIT,)
        )]
    finally:
        conn.close()
    return agents, threads, events, entries


def build_collaboration(threads, entries, events, agents):
    """Derive a sequence-diagram-friendly collaboration payload.

    Each `message` is one arrow on the sequence diagram:
      {id, ts, from, to, kind, subject, body, thread_id, status_change, intent}
    Lifelines are active (non-departed) agent aliases plus any peers that
    appear as senders/recipients in recent traffic.
    """
    active = [a["alias"] for a in agents if not a.get("departed")]
    thread_by_id = {t["id"]: t for t in threads}

    messages = []

    # Thread creation as opening arrows (from → to)
    for t in threads:
        messages.append({
            "id": f"thread-{t['id']}",
            "ts": t["created_at"],
            "from": t["from_agent"] or "?",
            "to": t["to_target"] if t.get("to_kind") != "broadcast" else "*broadcast*",
            "to_kind": t.get("to_kind") or "agent",
            "kind": t.get("kind") or "message",
            "subject": t.get("subject") or "",
            "body": "",
            "thread_id": t["id"],
            "status_change": t.get("status"),
            "intent": t.get("intent") or "",
            "source": "thread",
        })

    # Replies / status changes on threads
    for e in entries:
        thr = thread_by_id.get(e["thread_id"]) or {}
        # Reply goes back toward the thread originator (or the other party)
        to = thr.get("from_agent") or ""
        if e["from_agent"] == thr.get("from_agent"):
            to = thr.get("to_target") or to
        messages.append({
            "id": f"entry-{e['id']}",
            "ts": e["created_at"],
            "from": e["from_agent"] or "?",
            "to": to or "?",
            "to_kind": "agent",
            "kind": "reply" if not e.get("status_change") else "transition",
            "subject": thr.get("subject") or "",
            "body": (e.get("body") or "")[:280],
            "thread_id": e["thread_id"],
            "status_change": e.get("status_change") or "",
            "intent": thr.get("intent") or "",
            "source": "entry",
        })

    # Bus journal events that represent cross-agent signals.
    # Skip pure reads + noisy notify/nudge heartbeats — they drown the
    # collaboration diagram without showing real worker-to-worker work.
    skip_kinds = {"read", "notify", "nudge"}
    for ev in events:
        kind = (ev.get("kind") or "").lower()
        if kind in skip_kinds:
            continue
        detail = (ev.get("detail") or "")
        if detail.startswith("skipped:") or detail.startswith("error:"):
            continue
        target = ev.get("target") or ""
        # targets look like agent:x / role:r / broadcast / bare alias
        to = target
        if target.startswith("agent:"):
            to = target[6:]
        elif target.startswith("role:"):
            to = "role:" + target[5:]
        messages.append({
            "id": f"event-{ev['id']}",
            "ts": ev["ts"],
            "from": ev.get("agent") or "?",
            "to": to or "?",
            "to_kind": "event",
            "kind": kind or "event",
            "subject": "",
            "body": detail[:200],
            "thread_id": ev.get("thread_id") or 0,
            "status_change": "",
            "intent": "",
            "source": "event",
        })

    messages.sort(key=lambda m: m.get("ts") or 0)

    # Cap to recent traffic first so lifelines reflect the live fleet,
    # not every historical alias that ever spoke.
    if len(messages) > 200:
        messages = messages[-200:]

    # Lifelines = non-departed agents only (plus broadcast as a virtual peer
    # only when traffic actually targets it). Historical departed aliases
    # still appear as from/to labels on arrows but not as columns.
    active_set = {a["alias"] for a in agents if not a.get("departed")}
    lifelines = [a for a in active if a in active_set]
    # Prefer intentional aliases over muster-tui-* session-name twins
    lifelines = [a for a in lifelines if not a.startswith("muster-tui-")]
    # Stable order: claude first (orchestrator), then hub grok, then spoke
    def _life_rank(alias):
        aa = next((x for x in agents if x["alias"] == alias), {})
        mt = (aa.get("model_type") or "")
        machine = ALIAS_MACHINE.get(alias) or aa.get("machine") or "z"
        return (
            0 if "claude" in mt else 1,
            0 if machine == "hub" else 1,
            alias,
        )
    lifelines.sort(key=_life_rank)

    return {
        "lifelines": lifelines,
        "messages": messages,
    }

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
    agents, threads, events, entries = query_bus_db()
    local_panes = local_tmux_pane_ids()

    for a in agents:
        a["machine"] = resolve_machine(a["alias"], a.get("pane_id") or "", local_panes)
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

    # Verbatim live pane capture + geometry.
    # - hub agents: local tmux (pane_id must exist on THIS box)
    # - spoke agents: SSH + remote tmux (pane ids are per-server — never
    #   confuse spoke %0 with hub %0)
    # Skip departed agents: a tombstoned row can still hold a stale pane_id
    # that happens to match a live pane on this box (same %0 after a re-spawn
    # under a different alias), which would make the wall show ghost cards.
    for a in agents:
        a["pane_meta"] = None
        a["has_live_pane"] = False
        pane_id = a.get("pane_id") or ""
        if not pane_id or a.get("departed"):
            continue
        snapshot = None
        meta = None
        if a["machine"] == "hub" and pane_id in local_panes:
            snapshot = capture_tmux_pane(pane_id)
            meta = capture_tmux_pane_meta(pane_id)
        elif a["machine"] == "spoke":
            snapshot, meta = capture_spoke_tmux_pane(pane_id)
        if snapshot is not None:
            a["pane_snapshot"] = snapshot
            a["has_live_pane"] = True
        if meta is not None:
            a["pane_meta"] = meta

    # Deduplicate live panes: SessionStart hooks register under the tmux
    # session name while the prime prompt may register_agent under a shorter
    # alias — both rows share machine+pane_id. Keep a single card per real
    # pane (prefer the intentional alias over a muster-tui-* session-name
    # twin, then prefer non-empty role / fresher last_seen).
    seen_panes = {}
    for a in agents:
        if not a.get("has_live_pane"):
            continue
        key = (a.get("machine"), a.get("pane_id") or "")
        if not key[1]:
            continue
        prev = seen_panes.get(key)
        if prev is None:
            seen_panes[key] = a
            continue
        def _pane_rank(agent):
            alias = agent.get("alias") or ""
            session = agent.get("session_name") or ""
            # Prefer aliases that are NOT just the raw session name / muster-tui prefix
            intentional = 0 if (alias.startswith("muster-tui-") or alias == session) else 1
            has_role = 1 if agent.get("role") else 0
            return (intentional, has_role, agent.get("last_seen") or 0)
        winner, loser = (a, prev) if _pane_rank(a) > _pane_rank(prev) else (prev, a)
        seen_panes[key] = winner
        loser["has_live_pane"] = False
        loser["pane_snapshot"] = None
        # keep pane_meta on loser for debug but wall keys off has_live_pane
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

    collaboration = build_collaboration(threads, entries, events, agents)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": agents,
        "threads": threads,
        "events": events,
        "entries": entries,
        "collaboration": collaboration,
    }


def voice_python():
    """Prefer the isolated voice venv so Kokoro deps resolve."""
    if VOICE_VENV_PY.is_file():
        return str(VOICE_VENV_PY)
    return sys.executable


def voice_status():
    onnx = VOICE_MODELS / "kokoro-v1.0.onnx"
    voices = VOICE_MODELS / "voices-v1.0.bin"
    ollama_ok = False
    models = []
    try:
        out = subprocess.run(
            ["curl", "-s", "http://127.0.0.1:11434/api/tags"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout:
            models = [m.get("name") for m in json.loads(out.stdout).get("models", [])]
            ollama_ok = any("qwen2.5:3b" in (m or "") for m in models)
    except Exception:
        pass
    whisper = shutil.which("whisper")
    if not whisper:
        for candidate in ("/opt/homebrew/bin/whisper", "/usr/local/bin/whisper"):
            if os.path.exists(candidate):
                whisper = candidate
                break
    return {
        "ready": bool(onnx.is_file() and voices.is_file() and ollama_ok),
        "tts": {
            "engine": "kokoro-onnx",
            "onnx": str(onnx),
            "onnx_ok": onnx.is_file() and onnx.stat().st_size > 300_000_000,
            "voices_ok": voices.is_file(),
            "macos_say": False,
        },
        "llm": {
            "engine": "ollama",
            "model": "qwen2.5:3b",
            "ok": ollama_ok,
            "models": models,
        },
        "stt": {
            "engine": "whisper",
            "binary": whisper,
            "ok": bool(whisper),
        },
        "aliases": str(VOICE_DIR / "aliases.json"),
    }


def _voice_log(event):
    """Best-effort utterance log for debugging spoken phrases."""
    try:
        sys.path.insert(0, str(VOICE_DIR))
        from voice_log import log_event  # type: ignore
        return log_event(event)
    except Exception as e:
        print(f"[voice] log failed: {e}", file=sys.stderr, flush=True)
        return None


def run_voice_command(text: str):
    """Route spoken/typed text through computer.py / brain.py."""
    script = VOICE_DIR / "computer.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(VOICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    out = subprocess.run(
        [voice_python(), str(script), text],
        capture_output=True, text=True, timeout=60, cwd=str(VOICE_DIR), env=env,
    )
    if out.returncode != 0:
        data = {
            "speech": "Unable to comply.",
            "tool_calls": [],
            "error": (out.stderr or out.stdout or "brain failed")[:500],
            "transcript": text,
        }
        _voice_log({"stage": "command_error", **data})
        return data
    try:
        # computer.py prints pretty JSON (multi-line) — parse full stdout
        data = json.loads(out.stdout.strip())
    except Exception:
        # fallback: extract first {...} block
        try:
            s = out.stdout
            start, end = s.find("{"), s.rfind("}")
            data = json.loads(s[start:end + 1]) if start >= 0 and end > start else {}
        except Exception:
            data = {"speech": "Acknowledged.", "tool_calls": [], "raw": out.stdout[:500]}
    data["transcript"] = text
    _voice_log({
        "stage": "command",
        "transcript": text,
        "speech": data.get("speech"),
        "tool_calls": data.get("tool_calls") or [],
        "refused": bool(data.get("refused")),
        "refusal_reason": data.get("refusal_reason"),
        "error": data.get("error"),
    })
    return data


def run_voice_tts(text, voice=None):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        cmd = [
            voice_python(), str(VOICE_DIR / "tts_kokoro.py"),
            "--text", text, "--out", wav_path,
        ]
        if voice:
            cmd.extend(["--voice", voice])
        env = os.environ.copy()
        env["MUSTER_VOICE_MODELS"] = str(VOICE_MODELS)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        if out.returncode != 0 or not os.path.isfile(wav_path):
            raise RuntimeError(out.stderr or out.stdout or "tts failed")
        with open(wav_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def run_voice_stt(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    import tempfile
    suffix = Path(filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        src = tmp.name
    wav = src + ".wav"
    text = "[no transcript]"
    err = None
    try:
        # Normalize to 16k mono wav for whisper
        ff = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        subprocess.run(
            [ff, "-y", "-i", src, "-ac", "1", "-ar", "16000", wav],
            capture_output=True, timeout=30,
        )
        script = VOICE_DIR / "stt.py"
        path_env = {
            **os.environ,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""),
        }
        if script.is_file():
            out = subprocess.run(
                [voice_python(), str(script), wav if os.path.isfile(wav) else src],
                capture_output=True, text=True, timeout=120, env=path_env,
            )
            if out.returncode == 0 and out.stdout.strip():
                text = out.stdout.strip()
            else:
                err = (out.stderr or out.stdout or "")[:300]
        if text == "[no transcript]" or text.startswith("[STT"):
            # fallback: brew whisper CLI directly (launchd PATH is sparse)
            target = wav if os.path.isfile(wav) else src
            whisper_bin = shutil.which("whisper")
            if not whisper_bin:
                for candidate in ("/opt/homebrew/bin/whisper", "/usr/local/bin/whisper"):
                    if os.path.exists(candidate):
                        whisper_bin = candidate
                        break
            out = subprocess.run(
                [whisper_bin or "whisper", target, "--model", "base",
                 "--output_format", "txt", "--fp16", "False"],
                capture_output=True, text=True, timeout=180, env=path_env,
            )
            txt = Path(target).with_suffix(".txt")
            if txt.is_file():
                text = txt.read_text(encoding="utf-8", errors="replace").strip()
                try:
                    txt.unlink()
                except OSError:
                    pass
            elif out.stdout.strip():
                text = out.stdout.strip()
            else:
                err = (out.stderr or "")[:300]
                text = text if text.startswith("[STT") else "[no transcript]"
        _voice_log({
            "stage": "stt",
            "transcript": text,
            "bytes": len(audio_bytes or b""),
            "filename": filename,
            "error": err,
        })
        return text or "[no transcript]"
    finally:
        for p in (src, wav):
            try:
                os.unlink(p)
            except OSError:
                pass


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

    def _send_bytes(self, body: bytes, content_type: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        raw_path = self.path
        path = raw_path.split("?", 1)[0]
        query = {}
        if "?" in raw_path:
            query = {k: v[0] for k, v in parse_qs(raw_path.split("?", 1)[1]).items()}

        if path == "/api/status":
            try:
                self._send_json(build_status())
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        # Fast live-terminal endpoint — capture only this agent's pane so the
        # UI can refresh ~1s while a terminal is open without re-running
        # collectors/SSH for the whole fleet.
        if path == "/api/pane":
            alias = (query.get("alias") or "").strip()
            if not alias:
                self._send_json({"error": "alias query param required"}, status=400)
                return
            try:
                payload, status = build_pane_payload(alias)
                self._send_json(payload, status=status)
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        if path == "/api/voice/status":
            try:
                self._send_json(voice_status())
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        if path == "/api/voice/greet":
            try:
                env = os.environ.copy()
                env["PYTHONPATH"] = str(VOICE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
                out = subprocess.run(
                    [voice_python(), str(VOICE_DIR / "computer.py"), "--greet"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(VOICE_DIR), env=env,
                )
                data = json.loads(out.stdout.strip() or "{}")
                speech = data.get("speech") or "Computer online."
                try:
                    wav = run_voice_tts(speech)
                    import base64
                    data["audio_wav_b64"] = base64.b64encode(wav).decode("ascii")
                except Exception as e:
                    data["tts_error"] = str(e)
                _voice_log({"stage": "greet", "speech": speech, "tool_calls": []})
                self._send_json(data)
            except Exception as e:
                self._send_json({"error": str(e), "speech": "Computer online. Barely."}, status=500)
            return

        if path == "/api/voice/aliases":
            try:
                aliases_path = VOICE_DIR / "aliases.json"
                self._send_json(json.loads(aliases_path.read_text()))
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return

        if path == "/api/voice/log":
            try:
                n = int(query.get("n") or "40")
            except ValueError:
                n = 40
            try:
                sys.path.insert(0, str(VOICE_DIR))
                from voice_log import tail, LOG_PATH  # type: ignore
                refusals = []
                ref_path = Path.home() / ".local/share/muster-voice/refusals.jsonl"
                if ref_path.is_file():
                    lines = ref_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines[-n:]:
                        try:
                            refusals.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                self._send_json({
                    "path": str(LOG_PATH),
                    "entries": tail(n),
                    "refusals_path": str(ref_path),
                    "refusals": refusals,
                })
            except Exception as e:
                self._send_json({"error": str(e), "entries": []}, status=500)
            return

        if path == "/api/voice/help":
            # Phrase book for the Computer UI — human callsigns only
            self._send_json({
                "wake": ["Computer", "(optional)"],
                "phrases": [
                    {"say": "Show Chad", "does": "Open Chad’s terminal (hub Claude)"},
                    {"say": "Show Claude", "does": "Same as Chad"},
                    {"say": "Show Court", "does": "Open Court’s session (MBP Claude)"},
                    {"say": "Show Chris", "does": "Open Chris (Spoke A Grok TUI)"},
                    {"say": "Show Grok", "does": "Open Chris (default Grok TUI)"},
                    {"say": "Show Alex", "does": "Open Alex (Spoke B)"},
                    {"say": "Show Scout", "does": "Focus Scout (Hub Grok A, headless)"},
                    {"say": "Open collaboration on screen", "does": "Sequence view"},
                    {"say": "Show fleet", "does": "Fleet overview"},
                    {"say": "Open terminals", "does": "Terminals wall"},
                    {"say": "Focus Chris", "does": "Highlight Chris in collab"},
                    {"say": "Filter live", "does": "Fleet filter = live"},
                    {"say": "List the fleet", "does": "List people + aliases"},
                    {"say": "Report status", "does": "Counts"},
                    {"say": "On screen", "does": "Acknowledge / flash"},
                ],
                "names": [
                    "Chad = hub Claude TUI",
                    "Court = MacBook Claude",
                    "Chris = Spoke A Grok TUI",
                    "Alex = Spoke B Grok TUI",
                    "Scout / Rio / Nova = hub Grok workers (headless)",
                    "Morgan = MBP Grok worker",
                    "Sam = this Mac Studio Grok",
                ],
                "tips": [
                    "Use first names — never speak dashed IDs",
                    "YOU SAID appears in the Computer panel + utterances.jsonl",
                    "GET /api/voice/log for recent phrases",
                ],
            })
            return

        # static file serving for the frontend
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
        if fs_path.suffix == ".css":
            content_type = "text/css"
        elif fs_path.suffix == ".js":
            content_type = "application/javascript"
        body = fs_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/voice/command":
                raw = self._read_body()
                data = json.loads(raw.decode("utf-8") or "{}")
                text = (data.get("text") or "").strip()
                if not text:
                    self._send_json({"error": "text required"}, status=400)
                    return
                self._send_json(run_voice_command(text))
                return

            if path == "/api/voice/tts":
                raw = self._read_body()
                data = json.loads(raw.decode("utf-8") or "{}")
                text = (data.get("text") or "").strip()
                if not text:
                    self._send_json({"error": "text required"}, status=400)
                    return
                wav = run_voice_tts(text, voice=data.get("voice"))
                self._send_bytes(wav, "audio/wav")
                return

            if path == "/api/voice/stt":
                # multipart or raw body
                ctype = self.headers.get("Content-Type", "")
                body = self._read_body()
                filename = "audio.webm"
                if "multipart/form-data" in ctype:
                    import email
                    import cgi
                    # simple parse: look for filename= and binary after headers
                    # Use cgi.FieldStorage when available
                    try:
                        environ = {
                            "REQUEST_METHOD": "POST",
                            "CONTENT_TYPE": ctype,
                            "CONTENT_LENGTH": str(len(body)),
                        }
                        fs = cgi.FieldStorage(
                            fp=__import__("io").BytesIO(body),
                            headers=self.headers,
                            environ=environ,
                        )
                        field = fs["audio"] if "audio" in fs else fs["file"] if "file" in fs else None
                        if field is None:
                            # first file field
                            for k in fs.keys():
                                if getattr(fs[k], "file", None):
                                    field = fs[k]
                                    break
                        if field is None:
                            self._send_json({"error": "audio field required"}, status=400)
                            return
                        filename = getattr(field, "filename", None) or filename
                        audio = field.file.read()
                    except Exception as e:
                        self._send_json({"error": f"multipart parse: {e}"}, status=400)
                        return
                else:
                    audio = body
                text = run_voice_stt(audio, filename=filename)
                self._send_json({"text": text})
                return

            if path == "/api/voice/pipeline":
                body = self._read_body()
                ctype = self.headers.get("Content-Type", "")
                filename = "audio.webm"
                audio = body
                if "multipart/form-data" in ctype:
                    import cgi
                    environ = {
                        "REQUEST_METHOD": "POST",
                        "CONTENT_TYPE": ctype,
                        "CONTENT_LENGTH": str(len(body)),
                    }
                    fs = cgi.FieldStorage(
                        fp=__import__("io").BytesIO(body),
                        headers=self.headers,
                        environ=environ,
                    )
                    field = None
                    for key in ("audio", "file"):
                        if key in fs and getattr(fs[key], "file", None):
                            field = fs[key]
                            break
                    if field is None:
                        for k in fs.keys():
                            if getattr(fs[k], "file", None):
                                field = fs[k]
                                break
                    if field is None:
                        self._send_json({"error": "audio field required"}, status=400)
                        return
                    filename = getattr(field, "filename", None) or filename
                    audio = field.file.read()
                transcript = run_voice_stt(audio, filename=filename)
                result = run_voice_command(transcript)
                speech = result.get("speech") or "Acknowledged."
                try:
                    wav = run_voice_tts(speech)
                    import base64
                    result["audio_wav_b64"] = base64.b64encode(wav).decode("ascii")
                except Exception as e:
                    result["tts_error"] = str(e)
                result["transcript"] = transcript
                self._send_json(result)
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"muster fleet dashboard: http://localhost:{PORT}/", file=sys.stderr)
    print(f"  API:  http://localhost:{PORT}/api/status", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
