#!/usr/bin/env python3
"""Competitive work-stealing swarm — dump N tasks to role=worker, race claims.

No central scheduler: first claimer wins each task. Kill half the swarm and
survivors absorb the rest (observe via claim counts).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path


def mdebug(*args: str) -> dict:
    r = subprocess.run(
        ["muster", "debug", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "raw": r.stdout}


def dump_tasks(n: int, from_alias: str, swarm_id: str) -> list:
    tids = []
    for i in range(1, n + 1):
        body = (
            f"SWARM {swarm_id} item {i}/{n}.\n"
            f"1) claim\n2) reply SWARM_DONE id={swarm_id} i={i} alias=<you>\n"
            f"3) complete\n"
        )
        r = mdebug(
            "task_create",
            f"from={from_alias}",
            "to_kind=role",
            "to_target=worker",
            f"subject=SWARM-{swarm_id}-{i:03d}",
            f"body={body}",
            f"ref=showcase:swarm:{swarm_id}",
        )
        if r.get("ok"):
            tids.append(int(r["data"]["thread_id"]))
            print(f"  task {i} tid={tids[-1]}")
    return tids


def tally(tids: list, timeout: float) -> dict:
    import sqlite3, os
    db = Path(os.environ.get("MUSTER_DB", Path.home() / ".local/share/muster/bus.db"))
    deadline = time.time() + timeout
    claimers = {}
    done = set()
    while time.time() < deadline and len(done) < len(tids):
        con = sqlite3.connect(str(db))
        for tid in tids:
            if tid in done:
                continue
            row = con.execute("select status from threads where id=?", (tid,)).fetchone()
            if not row:
                continue
            if row[0] in ("claimed", "completed"):
                e = con.execute(
                    "select from_agent from entries where thread_id=? and status_change='claimed' order by id limit 1",
                    (tid,),
                ).fetchone()
                if e:
                    claimers[e[0]] = claimers.get(e[0], 0) + 1
                    done.add(tid)
        con.close()
        print(f"  progress {len(done)}/{len(tids)} leaderboard={claimers}")
        if len(done) >= len(tids):
            break
        time.sleep(2)
    return {"claimed": len(done), "total": len(tids), "leaderboard": claimers}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=12)
    ap.add_argument("--from", dest="frm", default="MacStudioGrok1")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--wait", action="store_true")
    args = ap.parse_args()
    sid = uuid.uuid4().hex[:8]
    print(f"swarm_id={sid} n={args.n}")
    tids = dump_tasks(args.n, args.frm, sid)
    payload = {"swarm_id": sid, "thread_ids": tids, "n": args.n, "created_at": time.time()}
    d = Path.home() / ".local/share/muster-fleet/showcase"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"swarm-{sid}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    if args.wait:
        results = tally(tids, args.timeout)
        payload["results"] = results
        path.write_text(json.dumps(payload, indent=2) + "\n")
        print("RESULTS", results)
        if len(results.get("leaderboard", {})) >= 2:
            print("PROOF: multi-worker swarm — ≥2 claimers")
        else:
            print("NOTE: need ≥2 live workers for a convincing swarm")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
