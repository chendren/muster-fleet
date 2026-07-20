#!/bin/sh
# fleet-nudge-tui.sh — keep TUI-backed muster workers draining.
#
# Headless hub Grok workers poll themselves via muster-loop-*.sh.
# TUI workers (Claude on hub, Grok on spoke) only act when something is
# typed into their pane. Without this nudge loop, they register once,
# go idle, and never pick up new tasks — which is exactly "only the
# operator agent does work."
#
# Hub Claude: local `muster nudge` + direct tmux send-keys fallback.
# Spoke Grok: SSH into muster-remote and send-keys into the real remote
# tmux session (local muster nudge is wrong for spoke — agents register
# with hub-side socket paths that do not map to the MacBook panes).
#
# Usage:
#   fleet-nudge-tui.sh              # run forever (default)
#   fleet-nudge-tui.sh once         # single pass then exit
#
# Env:
#   NUDGE_INTERVAL  seconds between rounds (default 20)

set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

MODE="${1:-loop}"
INTERVAL="${NUDGE_INTERVAL:-20}"
LOG="${NUDGE_LOG:-/tmp/muster-fleet-nudge.log}"
PIDFILE="${NUDGE_PIDFILE:-/tmp/muster-fleet-nudge.pid}"
DRAIN_MSG='Call get_inbox for your muster alias. Claim every open or claimed task addressed to you, do the work fully, reply, task_transition completed (or needs_info/blocked). Drain unread action/reply-requested messages. When nothing actionable remains, say idle and wait for the next nudge.'

log() {
  echo "[$(date -Iseconds 2>/dev/null || date)] $*" >>"$LOG"
}

# Escape a string for tmux send-keys -l (literal). We pass via printf %s.
send_local_tmux() {
  _session="$1"
  _msg="$2"
  if ! command -v tmux >/dev/null 2>&1; then
    log "no local tmux"
    return 1
  fi
  if ! tmux has-session -t "$_session" 2>/dev/null; then
    log "local session missing: $_session"
    return 1
  fi
  # Clear any half-typed line, inject drain, submit.
  tmux send-keys -t "$_session" C-u 2>/dev/null || true
  tmux send-keys -t "$_session" -l "$_msg" 2>/dev/null || return 1
  tmux send-keys -t "$_session" Enter 2>/dev/null || return 1
  log "local kick ok session=$_session"
  return 0
}

send_remote_tmux() {
  _session="$1"
  _msg="$2"
  # shellcheck disable=SC2029
  ssh -o ConnectTimeout=8 -o BatchMode=yes muster-remote \
    "export PATH=\"\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH\"; \
     tmux has-session -t '$_session' 2>/dev/null || exit 2; \
     tmux send-keys -t '$_session' C-u 2>/dev/null || true; \
     tmux send-keys -t '$_session' -l $(printf '%q' "$_msg"); \
     tmux send-keys -t '$_session' Enter" 2>>"$LOG"
  _rc=$?
  if [ "$_rc" -eq 0 ]; then
    log "remote kick ok session=$_session"
  else
    log "remote kick fail session=$_session rc=$_rc"
  fi
  return $_rc
}

nudge_once() {
  # Hub Claude TUI — prefer muster nudge (model-aware), fall back to raw tmux.
  if out=$(muster nudge hub-tui-claude 2>&1); then
    log "muster nudge hub-tui-claude :: $out"
  else
    log "muster nudge hub-tui-claude failed :: $out"
    send_local_tmux "muster-tui-hub-hub-tui-claude" "$DRAIN_MSG" || \
      send_local_tmux "hub-tui-claude" "$DRAIN_MSG" || true
  fi

  # Also force a stronger drain line every round (nudge text can be mild).
  send_local_tmux "muster-tui-hub-hub-tui-claude" "$DRAIN_MSG" 2>/dev/null || true

  # Spoke Grok TUIs live on MacBook Pro (muster-remote).
  # Session names from spawn: alias itself, plus legacy muster-tui-spoke-<alias>.
  for s in grok-spoke-a muster-tui-spoke-grok-spoke-a; do
    if send_remote_tmux "$s" "You are muster worker alias=grok-spoke-a. $DRAIN_MSG"; then
      break
    fi
  done
  for s in grok-spoke-b muster-tui-spoke-grok-spoke-b; do
    if send_remote_tmux "$s" "You are muster worker alias=grok-spoke-b. $DRAIN_MSG"; then
      break
    fi
  done
}

if [ "$MODE" = "once" ]; then
  nudge_once
  exit 0
fi

# Kill previous nudge loop if any
if [ -f "$PIDFILE" ]; then
  old=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${old:-}" ] && [ "$old" != "$$" ] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
    sleep 1
    kill -9 "$old" 2>/dev/null || true
  fi
fi
echo $$ >"$PIDFILE"
log "fleet-nudge-tui start interval=${INTERVAL}s pid=$$"

while true; do
  nudge_once
  sleep "$INTERVAL"
done
