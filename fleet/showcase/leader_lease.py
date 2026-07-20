#!/usr/bin/env python3
"""Mini leader-lease (raft-lite hero MVP).

Participants race to claim an election task for term N; winner writes
kv leader.* lease. When lease expires, survivors elect term N+1.

Run one process per agent (or drive from headless workers via tasks).
With one participant there is no failover story — that contrast is the demo.
"""
from __future__ import annotations
from typing import Optional

import argparse
import json
import os
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


def run_loop(alias: str, lease_s: float, poll_s: float) -> None:
    host = socket.gethostname()
    print(f"leader_lease alias={alias} host={host} lease={lease_s}s")
    while True:
        now = time.time()
        holder = kv_get("leader.holder") or ""
        lease_until = float(kv_get("leader.lease_until") or "0")
        term = int(kv_get("leader.term") or "0")

        if holder == alias and lease_until > now:
            # renew
            kv_set("leader.lease_until", str(now + lease_s), alias)
            kv_set("leader.host", host, alias)
            print(f"[{alias}] renew term={term} until={now + lease_s:.0f}")
            time.sleep(poll_s)
            continue

        if lease_until > now and holder and holder != alias:
            print(f"[{alias}] follower; leader={holder} term={term} remaining={lease_until - now:.1f}s")
            time.sleep(poll_s)
            continue

        # lease expired — elect
        new_term = term + 1
        subject = f"ELECTION-term-{new_term}"
        body = (
            f"Leader election term={new_term}. First claimer is leader.\n"
            f"On win: set kv leader.holder / leader.term / leader.lease_until / leader.host."
        )
        cr = muster_debug(
            "task_create",
            f"from={alias}",
            "to_kind=role",
            "to_target=worker",
            f"subject={subject}",
            f"body={body}",
        )
        if not cr.get("ok"):
            print(f"[{alias}] election create failed: {cr}")
            time.sleep(poll_s)
            continue
        tid = int(cr["data"]["thread_id"])
        claim = muster_debug("task_claim", f"thread_id={tid}", f"by={alias}")
        if claim.get("ok"):
            until = now + lease_s
            kv_set("leader.holder", alias, alias)
            kv_set("leader.term", str(new_term), alias)
            kv_set("leader.lease_until", str(until), alias)
            kv_set("leader.host", host, alias)
            muster_debug(
                "task_transition",
                f"thread_id={tid}",
                f"by={alias}",
                "status=completed",
                f"note=elected term {new_term}",
            )
            print(f"[{alias}] ELECTED leader term={new_term} host={host}")
        else:
            print(f"[{alias}] lost election term={new_term}: {claim}")
        time.sleep(poll_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alias", required=True)
    ap.add_argument("--lease", type=float, default=15.0)
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--once", action="store_true", help="one election attempt then exit")
    args = ap.parse_args()
    if args.once:
        # single shot: try elect if lease expired
        now = time.time()
        lease_until = float(kv_get("leader.lease_until") or "0")
        holder = kv_get("leader.holder") or ""
        print(json.dumps({
            "holder": holder,
            "term": kv_get("leader.term"),
            "lease_until": lease_until,
            "remaining": max(0, lease_until - now),
            "host": kv_get("leader.host"),
        }, indent=2))
        return
    run_loop(args.alias, args.lease, args.poll)


if __name__ == "__main__":
    main()
