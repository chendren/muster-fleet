#!/usr/bin/env python3
"""Fleet barrier — synchronized salute.

Each participant calls arrive(); all block until arrivals == N.
With N-1 online the barrier never releases — that hang is the demo.
"""
from __future__ import annotations
from typing import Optional

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path


def muster_debug(*args: str) -> dict:
    r = subprocess.run(
        ["muster", "debug", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": r.stdout or r.stderr}


def kv_get(key: str) -> Optional[str]:
    r = muster_debug("kv_get", f"key={key}")
    if r.get("ok") and r.get("data", {}).get("found"):
        return r["data"]["pair"]["value"]
    return None


def kv_set(key: str, value: str, by: str) -> None:
    muster_debug("kv_set", f"key={key}", f"value={value}", f"updated_by={by}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alias", required=True)
    ap.add_argument("--gen", default="1", help="barrier generation id")
    ap.add_argument("-n", type=int, default=3, help="expected participants")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    gen = args.gen
    arrived_key = f"barrier.{gen}.arrived"
    n_key = f"barrier.{gen}.n"
    agents_key = f"barrier.{gen}.agents"
    host = socket.gethostname()

    kv_set(n_key, str(args.n), args.alias)
    # append alias to agent list
    agents = kv_get(agents_key) or "[]"
    try:
        alist = json.loads(agents)
    except json.JSONDecodeError:
        alist = []
    if args.alias not in alist:
        alist.append(args.alias)
        kv_set(agents_key, json.dumps(alist), args.alias)

    # Per-agent marker (avoids lost updates from naive RMW counters)
    kv_set(f"barrier.{gen}.by.{args.alias}", f"{host}:{int(time.time())}", args.alias)
    agents = json.loads(kv_get(agents_key) or "[]")
    print(f"[{args.alias}] arrived gen={gen} host={host} roster_so_far={agents}")

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        # Count arrival markers via bus.db for accuracy
        import sqlite3, os
        db = Path(os.environ.get("MUSTER_DB", Path.home() / ".local/share/muster/bus.db"))
        con = sqlite3.connect(str(db))
        rows = con.execute(
            "select key from kv where key like ?", (f"barrier.{gen}.by.%",)
        ).fetchall()
        con.close()
        arrived_aliases = [r[0].rsplit(".", 1)[-1] for r in rows]
        got = len(arrived_aliases)
        n = int(kv_get(n_key) or str(args.n))
        kv_set(arrived_key, str(got), args.alias)
        print(f"[{args.alias}] waiting {got}/{n} present={arrived_aliases}")
        if got >= n:
            print(f"[{args.alias}] *** BARRIER RELEASE gen={gen} *** simultaneous salute")
            kv_set(f"barrier.{gen}.released", str(time.time()), args.alias)
            return
        time.sleep(1.0)
    print(f"[{args.alias}] TIMEOUT still waiting — missing peers (this IS the demo if intentional)")


if __name__ == "__main__":
    main()
