#!/bin/sh
# One-shot fleet health: tunnel, loops, dashboard, agentcore, llm, TUIs.
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
echo "== muster fleet status =="
echo "time: $(date -Iseconds 2>/dev/null || date)"
echo
echo "-- hub loops --"
for a in grok-hub-a grok-hub-b grok-hub-c grok-hub-d; do
  pf="/tmp/muster-loop-${a}.pid"
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    echo "  $a OK pid=$(cat "$pf")"
  else
    echo "  $a DOWN"
  fi
done
echo "-- nudge --"
if [ -f /tmp/muster-fleet-nudge.pid ] && kill -0 "$(cat /tmp/muster-fleet-nudge.pid)" 2>/dev/null; then
  echo "  OK pid=$(cat /tmp/muster-fleet-nudge.pid)"
else
  echo "  DOWN"
fi
echo "-- dashboard :8787 --"
curl -s -m 2 -o /dev/null -w "  http=%{http_code}\n" http://127.0.0.1:8787/api/status || echo "  DOWN"
echo "-- agentcore :8790 --"
curl -s -m 2 http://127.0.0.1:8790/health 2>/dev/null | head -c 100; echo
echo "-- llm mode --"
cat "$HOME/.local/share/muster-fleet/llm-mode.json" 2>/dev/null || echo "(unset)"
echo
echo "-- hub tmux --"
tmux ls 2>&1 | sed 's/^/  /' || true
echo "-- spoke tmux --"
ssh -o ConnectTimeout=5 -o BatchMode=yes muster-remote \
  'export PATH=/opt/homebrew/bin:$PATH; tmux ls 2>&1' 2>&1 | sed 's/^/  /' || echo "  ssh fail"
echo "-- spoke sock --"
ssh -o ConnectTimeout=5 -o BatchMode=yes muster-remote \
  'test -S "$HOME/.local/share/muster/sock" && echo OK || echo MISSING' 2>&1 | sed 's/^/  /'
echo "-- agents --"
muster agents 2>&1 | head -20 | sed 's/^/  /'
echo "-- smoke --"
if [ -x "$(dirname "$0")/acceptance/smoke.sh" ]; then
  "$(dirname "$0")/acceptance/smoke.sh" 2>&1 | sed 's/^/  /' || true
fi
