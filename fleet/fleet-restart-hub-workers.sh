#!/bin/sh
# Restart all hub headless Grok workers cleanly (kills stuck zombies first),
# then ensure the TUI nudge supervisor is running so Claude/spoke drain too.
set -eu
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
SPAWN="${SPAWN:-$HOME/.local/bin/muster-spawn-tui}"
NUDGE="${NUDGE:-$HOME/.local/bin/fleet-nudge-tui}"
ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"

# Prefer repo copies when invoked from the fleet/ dir; else installed bins.
if [ -x "$ROOT/muster-spawn-tui.sh" ]; then
  SPAWN="$ROOT/muster-spawn-tui.sh"
fi
if [ -x "$ROOT/fleet-nudge-tui.sh" ]; then
  NUDGE="$ROOT/fleet-nudge-tui.sh"
fi

# Global cleanup of stuck npm grok -p zombies + old loops.
# Kill by pidfile / exact script path — never `pkill -f fleet-nudge` from a
# wrapper whose argv contains that string (it suicides the restart shell).
for a in grok-hub-a grok-hub-b grok-hub-c grok-hub-d; do
  if [ -f "/tmp/muster-loop-$a.pid" ]; then
    old=$(cat "/tmp/muster-loop-$a.pid" 2>/dev/null || true)
    if [ -n "${old:-}" ]; then
      kill "$old" 2>/dev/null || true
      # children of the loop
      for c in $(pgrep -P "$old" 2>/dev/null); do
        kill "$c" 2>/dev/null || true
      done
    fi
  fi
done
# Orphan npm headless grok processes (pattern is specific enough)
pgrep -f '/.npm-global/bin/grok -p' 2>/dev/null | while read -r p; do
  kill "$p" 2>/dev/null || true
done
sleep 1

for a in grok-hub-a grok-hub-b grok-hub-c grok-hub-d; do
  echo "=== restart $a ==="
  "$SPAWN" grok hub "$a" worker
done

echo "=== pids ==="
for a in grok-hub-a grok-hub-b grok-hub-c grok-hub-d; do
  printf '%s: ' "$a"
  cat "/tmp/muster-loop-$a.pid" 2>/dev/null || echo missing
done

# TUI drain supervisor (Claude hub + spoke Groks)
if [ -x "$NUDGE" ]; then
  echo "=== start TUI nudge supervisor ==="
  if [ -f /tmp/muster-fleet-nudge.pid ]; then
    old=$(cat /tmp/muster-fleet-nudge.pid 2>/dev/null || true)
    if [ -n "${old:-}" ]; then
      kill "$old" 2>/dev/null || true
      sleep 0.5
      kill -9 "$old" 2>/dev/null || true
    fi
  fi
  nohup "$NUDGE" > /tmp/muster-fleet-nudge-nohup.log 2>&1 < /dev/null &
  echo $! > /tmp/muster-fleet-nudge.pid
  disown 2>/dev/null || true
  echo "nudge pid=$(cat /tmp/muster-fleet-nudge.pid)"
  # Immediate kick so open tasks don't wait a full interval
  "$NUDGE" once 2>/dev/null || true
else
  echo "WARN: fleet-nudge-tui not found — TUI workers will not auto-drain"
fi

echo "done"
