#!/bin/sh
# Ghost Hands — cross-machine relay one-command demo.
# Hub creates hop-1 for hub-tui-claude (or hub worker), which stamps and
# creates hop-2 for spoke, which stamps and creates hop-3 back to hub.
#
# Usage:
#   fleet/showcase/relay.sh [start|verify]
set -eu
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
CMD="${1:-start}"
RELAY_ID="relay-$(date +%Y%m%d-%H%M%S)"
FROM="${RELAY_FROM:-MacStudioGrok1}"

mdebug() {
  muster debug "$@" 2>/dev/null
}

start_relay() {
  echo "relay_id=$RELAY_ID"
  body1="GHOST HANDS hop 1/3 (HUB).
RELAY_ID=$RELAY_ID
1) Write /tmp/fleet-relay-${RELAY_ID}-hop1.txt with: hop=1 machine=hub alias=<you> host=\$(hostname) ts=\$(date +%s)
2) Create a muster task to agent grok-spoke-a subject=GHOST-HANDS-${RELAY_ID}-hop2 with body instructing hop 2 (see below).
3) Reply on THIS thread with hop1 file contents; task_transition completed.

HOP2 body to create for grok-spoke-a:
GHOST HANDS hop 2/3 (SPOKE).
RELAY_ID=$RELAY_ID
1) Write /tmp/fleet-relay-${RELAY_ID}-hop2.txt: hop=2 machine=spoke alias=grok-spoke-a host=\$(hostname) ts=\$(date +%s)
2) Create task to agent grok-hub-b subject=GHOST-HANDS-${RELAY_ID}-hop3:
   Write /tmp/fleet-relay-${RELAY_ID}-hop3.txt hop=3 machine=hub; reply complete.
3) Reply with hop2 contents + hostname; complete."

  # Prefer hub worker for hop1 reliability (headless always drains)
  r=$(mdebug task_create \
    "from=$FROM" \
    to_kind=agent \
    to_target=grok-hub-a \
    "subject=GHOST-HANDS-${RELAY_ID}-hop1" \
    "body=$body1" \
    "ref=showcase:relay:${RELAY_ID}")
  echo "$r"
  echo "$RELAY_ID" > /tmp/fleet-relay-latest.id
  printf '%s\n' "$r" > "/tmp/fleet-relay-${RELAY_ID}.create.json"
  echo "Nudge fleet / wait ~60s then: $0 verify"
}

verify_relay() {
  RID="${2:-$(cat /tmp/fleet-relay-latest.id 2>/dev/null || true)}"
  if [ -z "$RID" ]; then
    echo "usage: $0 verify <relay_id>"
    exit 1
  fi
  echo "=== HUB stamps ==="
  ls -la /tmp/fleet-relay-${RID}-hop*.txt 2>/dev/null || echo "(none on hub)"
  cat /tmp/fleet-relay-${RID}-hop*.txt 2>/dev/null || true
  echo "=== SPOKE stamps (ssh) ==="
  ssh -o ConnectTimeout=8 -o BatchMode=yes muster-remote \
    "ls -la /tmp/fleet-relay-${RID}-hop*.txt 2>/dev/null; cat /tmp/fleet-relay-${RID}-hop*.txt 2>/dev/null; hostname" \
    2>&1 || echo "ssh failed"
  echo "=== negative: hop2 must not be on hub ==="
  if [ -f "/tmp/fleet-relay-${RID}-hop2.txt" ]; then
    echo "FAIL: hop2 present on hub (should only be on spoke)"
    exit 2
  else
    echo "OK: hop2 not on hub"
  fi
}

case "$CMD" in
  start) start_relay ;;
  verify) verify_relay "$@" ;;
  *) echo "usage: $0 start|verify [id]"; exit 1 ;;
esac
