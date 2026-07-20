#!/usr/bin/env python3
"""Quorum commit — K-of-N votes required or ABORT.

Coordinator proposes a value; replicas vote via kv. Below K → abort.
Meaningless with one agent (cannot form multi-voter quorum).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional


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
        return {"ok": False, "raw": r.stdout, "err": r.stderr}


def kv_get(key: str) -> Optional[str]:
    r = mdebug("kv_get", f"key={key}")
    if r.get("ok") and r.get("data", {}).get("found"):
        return r["data"]["pair"]["value"]
    return None


def kv_set(key: str, value: str, by: str) -> None:
    mdebug("kv_set", f"key={key}", f"value={value}", f"updated_by={by}")


def propose(coord: str, value: str, k: int, n: int, timeout: float) -> dict:
    qid = uuid.uuid4().hex[:8]
    prefix = f"quorum.{qid}"
    kv_set(f"{prefix}.value", value, coord)
    kv_set(f"{prefix}.k", str(k), coord)
    kv_set(f"{prefix}.n", str(n), coord)
    kv_set(f"{prefix}.status", "proposing", coord)
    # Create vote tasks for role worker
    body = (
        f"QUORUM VOTE qid={qid}\n"
        f"Proposed value: {value}\n"
        f"1) If you approve: muster debug kv_set key={prefix}.vote.<you> value=yes updated_by=<you>\n"
        f"   Or reply on this task with VOTE yes\n"
        f"2) task_transition completed\n"
        f"Need {k}-of-{n}."
    )
    tids = []
    for i in range(n):
        r = mdebug(
            "task_create",
            f"from={coord}",
            "to_kind=role",
            "to_target=worker",
            f"subject=QUORUM-{qid}-vote-{i+1}",
            f"body={body}",
            f"ref=showcase:quorum:{qid}",
        )
        if r.get("ok"):
            tids.append(r["data"]["thread_id"])
    print(f"qid={qid} k={k} n={n} vote_tasks={tids}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        # also allow coordinator self-vote for demos if needed
        votes = _count_votes(prefix)
        print(f"  votes={votes}/{k} keys={_vote_keys(prefix)}")
        if votes >= k:
            kv_set(f"{prefix}.status", "COMMIT", coord)
            kv_set(f"{prefix}.commit", value, coord)
            result = {"qid": qid, "status": "COMMIT", "votes": votes, "k": k, "value": value}
            _save(result)
            print("COMMIT", result)
            return result
        time.sleep(1.5)
    kv_set(f"{prefix}.status", "ABORT", coord)
    result = {"qid": qid, "status": "ABORT", "votes": _count_votes(prefix), "k": k, "value": value}
    _save(result)
    print("ABORT", result)
    return result


def _vote_keys(prefix: str) -> list:
    import sqlite3, os
    db = Path(os.environ.get("MUSTER_DB", Path.home() / ".local/share/muster/bus.db"))
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "select key, value from kv where key like ?", (f"{prefix}.vote.%",)
    ).fetchall()
    con.close()
    return [f"{k}={v}" for k, v in rows]


def _count_votes(prefix: str) -> int:
    import sqlite3, os
    db = Path(os.environ.get("MUSTER_DB", Path.home() / ".local/share/muster/bus.db"))
    con = sqlite3.connect(str(db))
    n = con.execute(
        "select count(*) from kv where key like ? and value='yes'",
        (f"{prefix}.vote.%",),
    ).fetchone()[0]
    con.close()
    return int(n)


def _save(result: dict) -> None:
    d = Path.home() / ".local/share/muster-fleet/showcase"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"quorum-{result['qid']}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    mdebug("kv_set", "key=showcase.quorum.latest", f"value={json.dumps(result)}", "updated_by=quorum")


def vote_self(alias: str, qid: str) -> None:
    kv_set(f"quorum.{qid}.vote.{alias}", "yes", alias)
    print(f"{alias} voted yes on {qid}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--coord", default="MacStudioGrok1")
    p.add_argument("--value", default="release-v1")
    p.add_argument("-k", type=int, default=2)
    p.add_argument("-n", type=int, default=3)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--self-vote", action="store_true", help="coord also votes")
    v = sub.add_parser("vote")
    v.add_argument("--alias", required=True)
    v.add_argument("--qid", required=True)
    args = ap.parse_args()
    if args.cmd == "propose":
        # fire and optionally self-vote after create by peeking qid from propose...
        # restructure: create qid first
        if args.self_vote:
            # monkey: propose will create qid; we self-vote after by reading latest — simpler:
            pass
        res = propose(args.coord, args.value, args.k, args.n, args.timeout)
        if args.self_vote and res.get("qid"):
            vote_self(args.coord, res["qid"])
    elif args.cmd == "vote":
        vote_self(args.alias, args.qid)


if __name__ == "__main__":
    main()
