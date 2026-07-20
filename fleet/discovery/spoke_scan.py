#!/usr/bin/env python3
"""Spoke-side discovery scan for muster fleet (EPIC-6).

Run ON the spoke machine (MacBook). Emits machine-attributed JSON for hub merge.

Writes: /tmp/muster-discovery-spoke.json (override with DISCOVERY_OUT)
Stdout: same JSON document
Stderr: diagnostics only

Stdlib only. No pip deps.
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PATH = Path(os.environ.get("DISCOVERY_OUT", "/tmp/muster-discovery-spoke.json"))
MUSTER_HOME = Path(os.environ.get("MUSTER_HOME", Path.home() / ".local/share/muster"))
SOCK = MUSTER_HOME / "sock"
MACHINE = os.environ.get("MUSTER_MACHINE", "spoke")
CLI_NAMES = ("grok", "claude", "codex", "cursor-agent", "cursor")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout: {' '.join(cmd)}"


def host_info() -> dict[str, Any]:
    host = socket.gethostname()
    short = host.split(".")[0]
    try:
        local_ip = socket.gethostbyname(host)
    except OSError:
        local_ip = ""
    return {
        "hostname": host,
        "short_hostname": short,
        "fqdn": socket.getfqdn(),
        "local_ip": local_ip,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine_arch": platform.machine(),
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
        "home": str(Path.home()),
    }


def tunnel_health() -> dict[str, Any]:
    sock_ok = SOCK.is_socket() if hasattr(Path, "is_socket") else SOCK.exists()
    # pathlib is_socket exists on 3.12+; fallback via stat
    if not sock_ok and SOCK.exists():
        try:
            import stat as statmod

            sock_ok = statmod.S_ISSOCK(SOCK.stat().st_mode)
        except OSError:
            sock_ok = False

    sock_owner = "unknown"
    sock_peer = ""
    if sock_ok:
        code, out, _ = run(["lsof", str(SOCK)])
        if code == 0 and out:
            lines = out.splitlines()
            # header + rows; prefer sshd-session / ssh / muster
            for line in lines[1:]:
                parts = line.split()
                if not parts:
                    continue
                cmd = parts[0]
                if "ssh" in cmd.lower() or cmd == "sshd-session":
                    sock_owner = "ssh-tunnel"
                    sock_peer = line
                    break
                if cmd == "muster":
                    sock_owner = "muster-local"
                    sock_peer = line
            if sock_owner == "unknown" and len(lines) > 1:
                sock_owner = lines[1].split()[0] if lines[1].split() else "unknown"
                sock_peer = lines[1]

    no_autospawn = os.environ.get("MUSTER_NO_AUTOSPAWN", "")
    agents_ok = False
    agents_err = ""
    code, out, err = run(["muster", "agents"])
    if code == 0 and out:
        agents_ok = True
    else:
        agents_err = err or out or f"exit {code}"

    # Local serve may be empty when socket is reverse-forwarded from hub
    code2, serve_out, _ = run(["pgrep", "-lf", r"muster serve"])
    local_serve = bool(serve_out.strip()) if code2 == 0 else False

    status = "ok"
    notes: list[str] = []
    if not sock_ok:
        status = "broken"
        notes.append("sock missing or not a unix socket")
    elif not agents_ok:
        status = "degraded"
        notes.append(f"muster agents failed: {agents_err}")
    if sock_owner == "ssh-tunnel":
        notes.append("socket owned by reverse SSH tunnel (hub-hosted bus)")
    if no_autospawn in ("1", "true", "TRUE", "yes"):
        notes.append("MUSTER_NO_AUTOSPAWN set (correct for multi-machine)")
    elif sock_owner == "ssh-tunnel":
        notes.append("recommend MUSTER_NO_AUTOSPAWN=1 so clients do not spawn a second bus")

    return {
        "status": status,
        "sock_path": str(SOCK),
        "sock_ok": sock_ok,
        "sock_owner": sock_owner,
        "sock_peer_sample": sock_peer[:240] if sock_peer else "",
        "muster_no_autospawn": no_autospawn if no_autospawn != "" else "unset",
        "muster_agents_ok": agents_ok,
        "muster_agents_error": agents_err,
        "local_muster_serve": local_serve,
        "notes": notes,
    }


def parse_tmux_sessions() -> list[dict[str, Any]]:
    code, out, err = run(
        [
            "tmux",
            "list-sessions",
            "-F",
            "#{session_name}\t#{session_created}\t#{session_windows}\t#{session_attached}\t#{session_activity}",
        ]
    )
    sessions: list[dict[str, Any]] = []
    if code != 0:
        log(f"tmux list-sessions: {err or out or code}")
        return sessions
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name = parts[0]
        try:
            created = int(parts[1])
        except ValueError:
            created = 0
        try:
            windows = int(parts[2])
        except ValueError:
            windows = 0
        attached = parts[3] in ("1", "true", "True")
        activity = 0
        if len(parts) > 4:
            try:
                activity = int(parts[4])
            except ValueError:
                activity = 0
        model_guess = "unknown"
        lower = name.lower()
        if "grok" in lower:
            model_guess = "grok"
        elif "claude" in lower or "tui-claude" in lower:
            model_guess = "claude"
        elif "codex" in lower:
            model_guess = "codex"
        sessions.append(
            {
                "name": name,
                "created_unix": created,
                "windows": windows,
                "attached": attached,
                "activity_unix": activity,
                "model_guess": model_guess,
                "machine": MACHINE,
                "alias_hint": name,  # EPIC-6: register alias=session name
            }
        )
    return sessions


def _cmdline_for_pid(pid: int) -> str:
    code, out, _ = run(["ps", "-p", str(pid), "-o", "args="])
    return out if code == 0 else ""


def parse_cli_processes() -> list[dict[str, Any]]:
    """List coding-agent CLI processes (not Electron helpers / npm mcp noise)."""
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    # pgrep by exact binary name first
    for name in CLI_NAMES:
        code, out, _ = run(["pgrep", "-x", name])
        if code != 0 or not out:
            # fallback: path basename match via pgrep -f for known patterns
            continue
        for pid_s in out.split():
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            cmd = _cmdline_for_pid(pid)
            if not cmd:
                continue
            # skip helper noise
            if "Helper" in cmd or "Claude Helper" in cmd:
                continue
            alias = _extract_alias(cmd)
            found.append(
                {
                    "pid": pid,
                    "cli": name,
                    "cmd": cmd[:400],
                    "alias_hint": alias,
                    "machine": MACHINE,
                    "model_type": _model_for_cli(name, cmd),
                }
            )

    # Also catch `grok --permission-mode` etc when pgrep -x misses wrappers
    code, out, _ = run(["pgrep", "-lf", r"(^|/)(grok|claude|codex)( |$)"])
    if code == 0 and out:
        for line in out.splitlines():
            # "PID cmd..."
            m = re.match(r"^\s*(\d+)\s+(.*)$", line)
            if not m:
                continue
            pid = int(m.group(1))
            cmd = m.group(2)
            if pid in seen:
                continue
            if any(x in cmd for x in ("Helper", "Claude.app", "npm exec", "node_modules")):
                continue
            # only top-level agent binaries
            base = cmd.split()[0] if cmd.split() else ""
            cli = Path(base).name
            if cli not in CLI_NAMES and not any(c in cmd.split()[:3] for c in CLI_NAMES):
                # allow `exec grok ...` style after shell
                hit = None
                for c in CLI_NAMES:
                    if re.search(rf"(^|[\s/]){c}(\s|$)", cmd):
                        hit = c
                        break
                if not hit:
                    continue
                cli = hit
            seen.add(pid)
            found.append(
                {
                    "pid": pid,
                    "cli": cli,
                    "cmd": cmd[:400],
                    "alias_hint": _extract_alias(cmd),
                    "machine": MACHINE,
                    "model_type": _model_for_cli(cli, cmd),
                }
            )
    return found


def _extract_alias(cmd: str) -> str:
    # alias=foo or "alias=grok-spoke-a"
    m = re.search(r"alias=([A-Za-z0-9_.:-]+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"MUSTER_ALIAS=([A-Za-z0-9_.:-]+)", cmd)
    if m:
        return m.group(1)
    return ""


def _model_for_cli(cli: str, cmd: str) -> str:
    if "model_type=grok" in cmd or cli == "grok":
        return "grok"
    if "model_type=claude" in cmd or cli == "claude":
        return "claude"
    if "model_type=codex" in cmd or cli == "codex":
        return "codex"
    return "unknown"


def parse_muster_agents() -> list[dict[str, Any]]:
    """Parse `muster agents` table; tag local spoke sessions with machine=spoke."""
    code, out, err = run(["muster", "agents"])
    if code != 0:
        log(f"muster agents failed: {err or out}")
        return []
    agents: list[dict[str, Any]] = []
    for line in out.splitlines():
        # Skip header / separators
        if not line.strip() or line.startswith("PROJECT") or set(line.strip()) <= {"-", " "}:
            continue
        # Columns: PROJECT ALIAS LABEL MODEL LIVE (whitespace split; project may be (none))
        parts = line.split()
        if len(parts) < 4:
            continue
        # LIVE is last token (● or ✗ or similar)
        live_tok = parts[-1]
        live = live_tok in ("●", "*", "yes", "true", "live", "LIVE")
        # model is second to last typically
        model = parts[-2] if len(parts) >= 2 else ""
        # alias: first non-(none) token after optional project
        # Table: (none)   grok-spoke-a   —   grok   ●
        if parts[0] in ("(none)", "—", "-"):
            project = ""
            alias = parts[1] if len(parts) > 1 else ""
            label = parts[2] if len(parts) > 2 else ""
        else:
            project = parts[0]
            alias = parts[1] if len(parts) > 1 else ""
            label = parts[2] if len(parts) > 2 else ""
        if label in ("—", "-"):
            label = ""
        if not alias or alias in ("—", "-"):
            continue
        # Attribute: spoke-named aliases are local to this machine for EPIC-6
        is_local = alias.startswith("grok-spoke") or alias.startswith("spoke-")
        agents.append(
            {
                "alias": alias,
                "project": project,
                "label": label,
                "model_type": model if model not in ("—", "-") else "unknown",
                "live": live,
                "machine": MACHINE if is_local else "remote-or-hub",
                "local_to_scanner": is_local,
            }
        )
    return agents


def recommended_registrations(sessions: list[dict[str, Any]], clis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map tmux sessions + CLI processes to register_agent recommendations."""
    recs: list[dict[str, Any]] = []
    for s in sessions:
        name = s["name"]
        if not name.startswith(("grok-spoke", "spoke-", "hub-")) and "spoke" not in name:
            # still include grok-spoke* only for spoke machine primary duty
            if not name.startswith("grok-spoke"):
                continue
        model = s.get("model_guess") or "grok"
        if name.startswith("grok-spoke"):
            model = "grok"
        recs.append(
            {
                "alias": name,
                "role": "worker",
                "model_type": model,
                "session_name": name,
                "machine": MACHINE,
                "source": "tmux",
            }
        )
    # CLI-only aliases not already covered
    seen = {r["alias"] for r in recs}
    for c in clis:
        a = c.get("alias_hint") or ""
        if a and a not in seen:
            recs.append(
                {
                    "alias": a,
                    "role": "worker",
                    "model_type": c.get("model_type") or "grok",
                    "session_name": a,
                    "machine": MACHINE,
                    "source": "cli",
                }
            )
            seen.add(a)
    return recs


def build_document() -> dict[str, Any]:
    ts = utc_now_iso()
    host = host_info()
    tunnel = tunnel_health()
    sessions = parse_tmux_sessions()
    clis = parse_cli_processes()
    agents = parse_muster_agents()
    recs = recommended_registrations(sessions, clis)

    # Registration proof: which recommended aliases are live on the bus
    live_aliases = {a["alias"] for a in agents if a.get("live")}
    reg_proof = []
    for r in recs:
        reg_proof.append(
            {
                "alias": r["alias"],
                "registered_live": r["alias"] in live_aliases,
                "machine": MACHINE,
                "model_type": r["model_type"],
            }
        )

    return {
        "schema": "muster.discovery.spoke/v1",
        "machine": MACHINE,
        "scanned_at": ts,
        "scanned_at_unix": int(time.time()),
        "host": host,
        "tunnel": tunnel,
        "tmux_sessions": sessions,
        "cli_processes": clis,
        "muster_agents": agents,
        "recommended_registrations": recs,
        "registration_proof": reg_proof,
        "hub_pull": {
            "spoke_out": str(OUT_PATH),
            "ssh_examples": [
                "ssh muster-remote 'cat /tmp/muster-discovery-spoke.json'",
                "ssh chadhendren@mac.lan 'cat /tmp/muster-discovery-spoke.json'",
                "ssh chadhendren@192.168.12.75 'cat /tmp/muster-discovery-spoke.json'",
            ],
            "merge_note": "Hub EPIC-1 should merge this document into hub discovery state under machine=spoke; do not overwrite hub-local rows.",
        },
        "epic": "EPIC-6",
        "owner": "grok-spoke-a",
    }


def main() -> int:
    doc = build_document()
    text = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    try:
        OUT_PATH.write_text(text, encoding="utf-8")
        log(f"wrote {OUT_PATH}")
    except OSError as e:
        log(f"failed to write {OUT_PATH}: {e}")
        print(text, end="")
        return 1
    print(text, end="")
    if doc["tunnel"]["status"] == "broken":
        log("tunnel status=broken (soft fail; JSON still emitted)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
