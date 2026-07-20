#!/usr/bin/env python3
"""Bounty Race — atomic first-writer-wins claim showdown.

Drops N identical open tasks to role=worker. Live fleet workers race
task_claim; only one claim per bounty succeeds. Results land in:
  ~/.local/share/muster-fleet/showcase/bounty-<id>.json
  kv keys showcase.bounty.*

Fails to impress with one agent (no race). Spectacular with many.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path

DATA = Path.home() / ".local/share/muster-fleet/showcase"
DATA.mkdir(parents=True, exist_ok=True)


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
        return {"ok": False, "error": r.stdout or r.stderr, "rc": r.returncode}


def create_bounty(race_id: str, index: int, from_alias: str) -> int:
    subject = f"BOUNTY-{race_id}-{index:02d}"
    body = (
        f"BOUNTY RACE {race_id} #{index}.\n"
        f"Atomic claim showdown — first claimer wins.\n"
        f"1) task_claim this thread\n"
        f"2) reply with: WON bounty={race_id} index={index} alias=<you> host=$(hostname) ts=<unix>\n"
        f"3) task_transition completed\n"
        f"If claim fails, someone else already won — stop."
    )
    resp = muster_debug(
        "task_create",
        f"from={from_alias}",
        "to_kind=role",
        "to_target=worker",
        f"subject={subject}",
        f"body={body}",
        f"ref=showcase:bounty:{race_id}:{index}",
    )
    if not resp.get("ok"):
        raise RuntimeError(f"task_create failed: {resp}")
    return int(resp["data"]["thread_id"])


def poll_results(thread_ids: list[int], timeout_s: float = 90.0) -> list[dict]:
    import sqlite3
    import os

    db = Path(os.environ.get("MUSTER_DB", Path.home() / ".local/share/muster/bus.db"))
    deadline = time.time() + timeout_s
    results = {tid: None for tid in thread_ids}
    while time.time() < deadline and any(v is None for v in results.values()):
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        for tid in thread_ids:
            if results[tid] is not None:
                continue
            row = con.execute(
                "select id, status, subject from threads where id=?", (tid,)
            ).fetchone()
            if not row:
                continue
            claimer = None
            for e in con.execute(
                "select from_agent, status_change, body, created_at from entries "
                "where thread_id=? order by id",
                (tid,),
            ):
                if e[1] == "claimed":
                    claimer = {
                        "thread_id": tid,
                        "claimer": e[0],
                        "claimed_at": e[3],
                        "status": row["status"],
                        "subject": row["subject"],
                    }
                if e[1] == "completed" and claimer:
                    claimer["status"] = "completed"
                    claimer["completed_at"] = e[3]
                    claimer["reply"] = (e[2] or "")[:200]
            if claimer and row["status"] in ("claimed", "completed"):
                results[tid] = claimer
        con.close()
        if all(v is not None for v in results.values()):
            break
        time.sleep(1.5)
    return [results[tid] or {"thread_id": tid, "status": "timeout"} for tid in thread_ids]


def main() -> None:
    ap = argparse.ArgumentParser(description="Muster bounty race showcase")
    ap.add_argument("-n", type=int, default=3, help="number of bounty tasks")
    ap.add_argument("--from", dest="from_alias", default="MacStudioGrok1")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--wait", action="store_true", help="poll until claimed/completed")
    args = ap.parse_args()

    race_id = uuid.uuid4().hex[:8]
    print(f"race_id={race_id} n={args.n}")
    muster_debug(
        "kv_set",
        f"key=showcase.bounty.current",
        f"value={race_id}",
        f"updated_by={args.from_alias}",
    )
    thread_ids = []
    for i in range(1, args.n + 1):
        tid = create_bounty(race_id, i, args.from_alias)
        thread_ids.append(tid)
        print(f"  bounty #{i} thread_id={tid}")
    payload = {
        "race_id": race_id,
        "thread_ids": thread_ids,
        "created_at": time.time(),
        "n": args.n,
    }
    path = DATA / f"bounty-{race_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    muster_debug(
        "kv_set",
        "key=showcase.bounty.latest",
        f"value={json.dumps(payload)}",
        f"updated_by={args.from_alias}",
    )
    print(f"wrote {path}")
    print("Workers with role=worker will race task_claim. Nudge TUIs if needed.")

    if args.wait:
        print(f"waiting up to {args.timeout}s for claims...")
        results = poll_results(thread_ids, args.timeout)
        payload["results"] = results
        path.write_text(json.dumps(payload, indent=2) + "\n")
        winners = [r for r in results if r.get("claimer")]
        claimers = {}
        for r in winners:
            claimers.setdefault(r["claimer"], 0)
            claimers[r["claimer"]] += 1
        print("RESULTS:")
        for r in results:
            print(f"  thread={r.get('thread_id')} claimer={r.get('claimer')} status={r.get('status')}")
        print("leaderboard:", claimers)
        if len(claimers) >= 2:
            print("PROOF: multi-agent race — ≥2 distinct claimers")
        elif len(claimers) == 1:
            print("NOTE: single claimer — race needs multiple live workers")
        else:
            print("FAIL: no claims yet — is the fleet draining?")


if __name__ == "__main__":
    main()
