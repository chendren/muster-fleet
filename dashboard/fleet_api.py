#!/usr/bin/env python3
"""EPIC-7 fleet APIs: mesh graph + fleet health + timeline helpers.

Stdlib only. Safe to merge into hub dashboard/server.py.
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

FLEET_DIR = Path(os.environ.get("MUSTER_FLEET_DIR", Path.home() / ".local" / "share" / "muster-fleet"))
TIMELINE_PATH = FLEET_DIR / "timeline.jsonl"
DISCOVERY_PATH = FLEET_DIR / "discovery.json"
LLM_MODE_PATH = FLEET_DIR / "llm-mode.json"
REQUESTS_PATH = FLEET_DIR / "requests.jsonl"
MUSTER_SOCK = Path(os.environ.get("MUSTER_HOME", Path.home() / ".local" / "share" / "muster")) / "sock"

# Candidate bus.db locations (hub + spoke tunnel)
BUS_DB_CANDIDATES = [
    Path.home() / ".local" / "share" / "muster" / "bus.db",
    Path(os.environ.get("MUSTER_HOME", "")) / "bus.db" if os.environ.get("MUSTER_HOME") else None,
    Path("/Users/chad/.local/share/muster/bus.db"),
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def ensure_fleet_dir() -> None:
    FLEET_DIR.mkdir(parents=True, exist_ok=True)


def append_timeline(event: str, **fields: Any) -> None:
    """Append a JSON line to the fleet request timeline."""
    ensure_fleet_dir()
    row = {"ts": _now_ms(), "event": event, **fields}
    with TIMELINE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _http_ok(url: str, timeout: float = 1.5) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as r:
            body = r.read(2048).decode("utf-8", errors="replace")
            return {"ok": True, "status": getattr(r, "status", 200), "body_snip": body[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tcp_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _infer_machine(alias: str) -> str:
    a = (alias or "").lower()
    if "spoke" in a or a.startswith("grok-mbp") or "macbook" in a:
        return "spoke"
    if a.startswith("grok-hub") or a in ("macstudiogrok1", "hub-tui-claude") or "hub" in a:
        return "hub"
    if a in ("chad-mac",):
        return "spoke"
    return "unknown"


def _parse_agents_cli() -> list[dict[str, Any]]:
    """Parse `muster agents` human table into structured rows."""
    code, out, err = _run(["muster", "agents"])
    if code != 0:
        return []
    agents: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line or line.startswith("PROJECT") or set(line.strip()) <= set("-"):
            continue
        # PROJECT  ALIAS  LABEL  MODEL  LIVE
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            # fallback whitespace split from right
            toks = line.split()
            if len(toks) < 4:
                continue
            live_tok = toks[-1]
            model = toks[-2]
            alias = toks[1] if len(toks) > 1 else toks[0]
            label = "—"
        else:
            # (none)   alias   —   grok   ●
            project, alias, label, model, live_tok = parts[0], parts[1], parts[2], parts[3], parts[4]
        live = live_tok.strip() in ("●", "*", "live", "LIVE", "yes", "1")
        agents.append(
            {
                "alias": alias,
                "label": None if label in ("—", "-", "") else label,
                "model": model,
                "live": live,
                "machine": _infer_machine(alias),
                "project": parts[0] if len(parts) >= 5 else None,
            }
        )
    return agents


def _read_discovery() -> dict[str, Any]:
    if not DISCOVERY_PATH.is_file():
        # spoke scan artifact
        spoke = Path("/tmp/muster-discovery-spoke.json")
        if spoke.is_file():
            try:
                return json.loads(spoke.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}
    try:
        return json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _open_threads_from_cli() -> list[dict[str, Any]]:
    """Collect open/claimed task threads via muster CLI for known aliases."""
    agents = _parse_agents_cli()
    aliases = [a["alias"] for a in agents] or ["grok-spoke-b"]
    seen: dict[int, dict[str, Any]] = {}
    for alias in aliases[:40]:
        code, out, _ = _run(["muster", "tasks", alias], timeout=6.0)
        if code != 0:
            continue
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("ID"):
                continue
            # ID KIND FROM TO STATUS LAST-FROM UNREAD SUBJECT
            m = re.match(
                r"^(\d+)\s+task\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(.*)$",
                line,
            )
            if not m:
                # looser: id ... status ...
                toks = line.split()
                if len(toks) < 5 or not toks[0].isdigit():
                    continue
                tid = int(toks[0])
                status = toks[3] if len(toks) > 3 else ""
                subject = " ".join(toks[6:]) if len(toks) > 6 else line
                from_agent = toks[2] if len(toks) > 2 else ""
                to_target = toks[3] if len(toks) > 3 else ""
            else:
                tid = int(m.group(1))
                from_agent = m.group(2)
                to_target = m.group(3)
                status = m.group(4)
                subject = m.group(7)
            if status not in ("open", "claimed", "needs_info", "blocked"):
                continue
            seen[tid] = {
                "id": tid,
                "kind": "task",
                "status": status,
                "from": from_agent,
                "to": to_target,
                "subject": subject,
            }
    return sorted(seen.values(), key=lambda t: t["id"])


def _try_bus_db_threads() -> tuple[list[dict[str, Any]], str | None]:
    """Best-effort read of open threads from bus.db if present on disk."""
    for cand in BUS_DB_CANDIDATES:
        if cand is None or not cand.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{cand}?mode=ro", uri=True, timeout=1.0)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            # schema-tolerant
            cols = [r[1] for r in cur.execute("PRAGMA table_info(threads)").fetchall()]
            if not cols:
                con.close()
                continue
            status_col = "status" if "status" in cols else None
            kind_col = "kind" if "kind" in cols else None
            q = "SELECT * FROM threads"
            rows = cur.execute(q).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = {k: r[k] for k in r.keys()}
                st = str(d.get("status") or "")
                if st and st not in ("open", "claimed", "needs_info", "blocked"):
                    continue
                if kind_col and str(d.get("kind") or "") not in ("task", "message", ""):
                    pass
                out.append(
                    {
                        "id": d.get("id"),
                        "kind": d.get("kind"),
                        "status": d.get("status"),
                        "from": d.get("from_agent") or d.get("from"),
                        "to": d.get("to_target") or d.get("to"),
                        "subject": d.get("subject"),
                    }
                )
            con.close()
            return out, str(cand)
        except Exception as e:
            return [], f"{cand}: {e}"
    return [], None


def build_mesh() -> dict[str, Any]:
    """machines → sessions → open threads graph for /api/mesh."""
    agents = _parse_agents_cli()
    discovery = _read_discovery()
    threads_cli = _open_threads_from_cli()
    threads_db, db_path = _try_bus_db_threads()
    threads = threads_db if threads_db else threads_cli

    machines: dict[str, dict[str, Any]] = {}
    for a in agents:
        mid = a["machine"]
        machines.setdefault(
            mid,
            {"id": mid, "hostname_hint": platform.node() if mid == "spoke" else mid, "sessions": []},
        )
        machines[mid]["sessions"].append(
            {
                "alias": a["alias"],
                "model": a["model"],
                "live": a["live"],
                "label": a.get("label"),
            }
        )

    # Attach open threads as edges (from → to)
    edges = []
    for t in threads:
        edges.append(
            {
                "thread_id": t.get("id"),
                "kind": t.get("kind"),
                "status": t.get("status"),
                "from": t.get("from"),
                "to": t.get("to"),
                "subject": t.get("subject"),
            }
        )

    # discovery overlay counts
    disc_sessions = []
    if isinstance(discovery, dict):
        for key in ("sessions", "agents", "tmux", "processes"):
            val = discovery.get(key)
            if isinstance(val, list):
                disc_sessions.extend(val)

    result = {
        "ok": True,
        "generated_at": _now_ms(),
        "source": {
            "agents": "muster agents",
            "threads": f"bus.db:{db_path}" if db_path else "muster tasks CLI",
            "discovery": str(DISCOVERY_PATH) if DISCOVERY_PATH.is_file() else (
                "/tmp/muster-discovery-spoke.json" if Path("/tmp/muster-discovery-spoke.json").is_file() else None
            ),
        },
        "machines": list(machines.values()),
        "sessions": agents,
        "open_threads": threads,
        "edges": edges,
        "discovery_overlay": {
            "path": result_path(discovery),
            "count": len(disc_sessions),
            "raw_keys": list(discovery.keys()) if isinstance(discovery, dict) else [],
        },
        "stats": {
            "machines": len(machines),
            "sessions": len(agents),
            "live": sum(1 for a in agents if a.get("live")),
            "open_threads": len(threads),
            "edges": len(edges),
        },
    }
    return result


def result_path(discovery: dict) -> str | None:
    if DISCOVERY_PATH.is_file():
        return str(DISCOVERY_PATH)
    if Path("/tmp/muster-discovery-spoke.json").is_file():
        return "/tmp/muster-discovery-spoke.json"
    return None


def _pidfile_alive(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"present": False, "alive": False, "path": str(path)}
    try:
        pid = int(path.read_text(encoding="utf-8").strip().split()[0])
    except Exception as e:
        return {"present": True, "alive": False, "path": str(path), "error": str(e)}
    alive = False
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    return {"present": True, "alive": alive, "pid": pid, "path": str(path)}


def build_fleet_health() -> dict[str, Any]:
    """Aggregate fleet process/service health for GET /api/fleet/health."""
    ensure_fleet_dir()

    # loop / worker pidfiles
    loop_pids = []
    for p in sorted(Path("/tmp").glob("muster-loop-*.pid")):
        loop_pids.append(_pidfile_alive(p))
    for p in sorted(Path("/tmp").glob("agentcore*.pid")):
        loop_pids.append(_pidfile_alive(p))

    sock_ok = MUSTER_SOCK.exists()
    # tunnel: sock exists and muster agents works
    code, agents_out, agents_err = _run(["muster", "agents"], timeout=6.0)
    tunnel = {
        "sock": str(MUSTER_SOCK),
        "sock_exists": sock_ok,
        "muster_agents_ok": code == 0,
        "muster_no_autospawn": os.environ.get("MUSTER_NO_AUTOSPAWN"),
        "error": agents_err.strip() if code != 0 else None,
        "agent_lines": len([l for l in agents_out.splitlines() if l.strip()]) if code == 0 else 0,
    }

    agentcore = {
        "url": "http://127.0.0.1:8790/health",
        **_http_ok("http://127.0.0.1:8790/health"),
        "tcp": _tcp_open("127.0.0.1", 8790),
    }

    llm_mode = {"path": str(LLM_MODE_PATH), "present": LLM_MODE_PATH.is_file()}
    if LLM_MODE_PATH.is_file():
        try:
            llm_mode["data"] = json.loads(LLM_MODE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            llm_mode["error"] = str(e)

    ollama = {
        "url": "http://127.0.0.1:11434/api/tags",
        **_http_ok("http://127.0.0.1:11434/api/tags"),
        "tcp": _tcp_open("127.0.0.1", 11434),
    }

    dashboard = {
        "pid": os.getpid(),
        "port_env": os.environ.get("PORT") or os.environ.get("MUSTER_DASHBOARD_PORT") or "8787",
        "host": platform.node(),
        "service": "muster-dashboard",
    }

    # nudge probe: binary present (does not actually nudge)
    nudge = {"muster_cli": _run(["which", "muster"])[0] == 0, "can_nudge": False}
    if nudge["muster_cli"]:
        # help documents nudge
        c, o, _ = _run(["muster", "help", "nudge"], timeout=4.0)
        nudge["can_nudge"] = c == 0 or "nudge" in (o or "").lower()

    discovery = {
        "path": str(DISCOVERY_PATH),
        "present": DISCOVERY_PATH.is_file(),
        "spoke_scan": Path("/tmp/muster-discovery-spoke.json").is_file(),
    }

    timeline = {
        "path": str(TIMELINE_PATH),
        "present": TIMELINE_PATH.is_file(),
        "bytes": TIMELINE_PATH.stat().st_size if TIMELINE_PATH.is_file() else 0,
    }

    hard_ok = sock_ok and code == 0
    checks = {
        "tunnel": tunnel["muster_agents_ok"],
        "dashboard": True,
        "agentcore": bool(agentcore.get("ok") or agentcore.get("tcp")),
        "llm_mode_file": llm_mode["present"],
        "ollama": bool(ollama.get("ok") or ollama.get("tcp")),
    }

    return {
        "ok": hard_ok,
        "generated_at": _now_ms(),
        "machine": {
            "hostname": platform.node(),
            "system": platform.system(),
            "arch": platform.machine(),
            "role_hint": "spoke",
        },
        "dashboard": dashboard,
        "loop_pids": loop_pids,
        "tunnel": tunnel,
        "agentcore": agentcore,
        "llm_mode": llm_mode,
        "ollama": ollama,
        "discovery": discovery,
        "timeline": timeline,
        "nudge": nudge,
        "checks": checks,
        "agents_live": sum(1 for a in _parse_agents_cli() if a.get("live")),
    }


def handle_fleet_request(method: str, path: str, headers: dict, body: bytes):
    """Return (status, headers, body) or None if not a fleet route."""
    parsed = urlparse(path)
    p = parsed.path.rstrip("/") or "/"
    method = method.upper()

    if method == "OPTIONS" and p.startswith("/api/"):
        return (
            204,
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Accept",
            },
            b"",
        )

    if method == "GET" and p == "/api/mesh":
        data = build_mesh()
        append_timeline("mesh.query", stats=data.get("stats"))
        raw = json.dumps(data).encode("utf-8")
        return 200, {"Content-Type": "application/json; charset=utf-8"}, raw

    if method == "GET" and p == "/api/fleet/health":
        data = build_fleet_health()
        raw = json.dumps(data).encode("utf-8")
        return 200, {"Content-Type": "application/json; charset=utf-8"}, raw

    if method == "GET" and p == "/api/fleet/timeline":
        ensure_fleet_dir()
        lines = []
        if TIMELINE_PATH.is_file():
            # last 100
            for line in TIMELINE_PATH.read_text(encoding="utf-8").splitlines()[-100:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except Exception:
                    lines.append({"raw": line})
        raw = json.dumps({"ok": True, "path": str(TIMELINE_PATH), "events": lines}).encode("utf-8")
        return 200, {"Content-Type": "application/json; charset=utf-8"}, raw

    if method == "POST" and p == "/api/fleet/timeline":
        ensure_fleet_dir()
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        event = str(payload.get("event") or "note")
        fields = {k: v for k, v in payload.items() if k != "event"}
        append_timeline(event, **fields)
        raw = json.dumps({"ok": True, "path": str(TIMELINE_PATH)}).encode("utf-8")
        return 200, {"Content-Type": "application/json; charset=utf-8"}, raw

    return None


def attach_cors(headers: dict[str, str]) -> dict[str, str]:
    h = dict(headers)
    h.setdefault("Access-Control-Allow-Origin", "*")
    return h
