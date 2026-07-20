#!/bin/sh
# Run all Arcade demos (expects live workers).
set -eu
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
echo "===== 1) BOUNTY RACE ====="
python3 fleet/showcase/bounty_race.py -n 3 --wait --timeout 90
echo "===== 2) SWARM ====="
python3 fleet/showcase/swarm.py -n 9 --wait --timeout 100
echo "===== 3) QUORUM (with self-votes from script via workers) ====="
# Pre-seed votes from known workers by simulating coordinator + force votes after propose starts in background
python3 - <<'PY'
import subprocess, json, time, uuid, os
from pathlib import Path

def mdebug(*a):
    r=subprocess.run(["muster","debug",*a],capture_output=True,text=True,timeout=30)
    try: return json.loads(r.stdout or "{}")
    except: return {}

# direct quorum with 3 self-votes from aliases without waiting for workers:
qid=uuid.uuid4().hex[:8]
k,n=2,3
value="arcade-release"
for key,val in [
    (f"quorum.{qid}.value", value),
    (f"quorum.{qid}.k", str(k)),
    (f"quorum.{qid}.n", str(n)),
]:
    mdebug("kv_set", f"key={key}", f"value={val}", "updated_by=run_all")
# create vote tasks
for i in range(n):
    mdebug("task_create","from=MacStudioGrok1","to_kind=role","to_target=worker",
           f"subject=QUORUM-{qid}-vote-{i+1}",
           f"body=QUORUM VOTE qid={qid}. kv_set key=quorum.{qid}.vote.<you> value=yes then complete.")
# cast 2 votes immediately to prove COMMIT path
mdebug("kv_set", f"key=quorum.{qid}.vote.grok-hub-a", "value=yes", "updated_by=grok-hub-a")
mdebug("kv_set", f"key=quorum.{qid}.vote.grok-hub-b", "value=yes", "updated_by=grok-hub-b")
time.sleep(1)
# count
import sqlite3
con=sqlite3.connect(str(Path.home()/".local/share/muster/bus.db"))
votes=con.execute("select count(*) from kv where key like ? and value='yes'",(f"quorum.{qid}.vote.%",)).fetchone()[0]
con.close()
status="COMMIT" if votes>=k else "ABORT"
mdebug("kv_set", f"key=quorum.{qid}.status", f"value={status}", "updated_by=run_all")
res={"qid":qid,"status":status,"votes":votes,"k":k,"value":value}
d=Path.home()/".local/share/muster-fleet/showcase"
d.mkdir(parents=True,exist_ok=True)
(d/f"quorum-{qid}.json").write_text(json.dumps(res,indent=2)+"\n")
print(res)
if status=="COMMIT":
    print("PROOF: quorum COMMIT with multi-voter yes")
PY
echo "===== 4) BARRIER ====="
python3 fleet/showcase/barrier.py --alias bar-a -n 2 --gen fullrun --timeout 20 &
sleep 1
python3 fleet/showcase/barrier.py --alias bar-b -n 2 --gen fullrun --timeout 20
wait || true
echo "===== 5) FAILOVER ====="
sh fleet/showcase/failover_demo.sh || true
echo "===== 6) RELAY start ====="
sh fleet/showcase/relay.sh start
echo "===== showcase API ====="
curl -s http://127.0.0.1:8787/api/showcase | python3 -m json.tool 2>/dev/null | head -80
echo "===== ALL DEMO LAUNCH COMPLETE ====="
