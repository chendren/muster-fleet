#!/bin/sh
# Leader failover demo: start two electors, show crown, kill leader, show new crown.
set -eu
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
PY="${ROOT}/fleet/showcase/leader_lease.py"
LEASE=10

echo "=== start elect-a (background) ==="
python3 "$PY" --alias elect-a --lease "$LEASE" --poll 2 > /tmp/elect-a.log 2>&1 &
echo $! > /tmp/elect-a.pid
sleep 5
echo "=== start elect-b (background) ==="
python3 "$PY" --alias elect-b --lease "$LEASE" --poll 2 > /tmp/elect-b.log 2>&1 &
echo $! > /tmp/elect-b.pid
sleep 6
echo "=== snapshot 1 (should have a leader) ==="
python3 "$PY" --alias watch --once | tee /tmp/failover-snap1.json
HOLDER=$(python3 -c "import json;print(json.load(open('/tmp/failover-snap1.json')).get('holder',''))")
echo "leader=$HOLDER"
if [ -z "$HOLDER" ]; then
  echo "WARN: no leader yet"
fi
# Kill the leader process
if [ "$HOLDER" = "elect-a" ]; then
  kill "$(cat /tmp/elect-a.pid)" 2>/dev/null || true
  echo "killed elect-a"
elif [ "$HOLDER" = "elect-b" ]; then
  kill "$(cat /tmp/elect-b.pid)" 2>/dev/null || true
  echo "killed elect-b"
else
  # kill a anyway to force expiry
  kill "$(cat /tmp/elect-a.pid)" 2>/dev/null || true
  echo "killed elect-a (fallback)"
fi
echo "=== wait for lease expiry + re-election (~$((LEASE+5))s) ==="
sleep $((LEASE + 5))
echo "=== snapshot 2 (crown should move or renew on survivor) ==="
python3 "$PY" --alias watch --once | tee /tmp/failover-snap2.json
# cleanup
kill "$(cat /tmp/elect-a.pid)" 2>/dev/null || true
kill "$(cat /tmp/elect-b.pid)" 2>/dev/null || true
rm -f /tmp/elect-a.pid /tmp/elect-b.pid
echo "=== done — compare holder in snap1 vs snap2 ==="
python3 - <<'PY'
import json
s1=json.load(open('/tmp/failover-snap1.json'))
s2=json.load(open('/tmp/failover-snap2.json'))
print('before:', s1)
print('after: ', s2)
if s1.get('holder') and s2.get('holder') and s1.get('holder') != s2.get('holder'):
    print('PROOF: crown moved', s1['holder'], '->', s2['holder'])
elif s2.get('holder'):
    print('NOTE: leader present after kill (may be re-elect same if other died first):', s2.get('holder'))
else:
    print('FAIL: no leader after failover window')
PY
