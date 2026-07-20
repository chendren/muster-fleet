#!/bin/bash
# EPIC-4 AgentCore emulator launcher
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="/tmp/agentcore-emulator.pid"
LOG="/tmp/agentcore-emulator.log"

if [ -f "$PIDFILE" ] && ps -p "$(cat "$PIDFILE")" > /dev/null 2>&1; then
  echo "AgentCore already running (pid $(cat $PIDFILE))"
  exit 0
fi

cd "$DIR"
nohup python3 agentcore_emulator.py > "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "Started AgentCore emulator (pid $!, log $LOG)"