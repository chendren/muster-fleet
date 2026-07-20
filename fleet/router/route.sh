#!/bin/bash
# fleet/router/route.sh — CLI to route a goal through the fleet router
set -e
GOAL="$*"
if [ -z "$GOAL" ]; then
  echo "Usage: route.sh <goal text>"
  exit 1
fi
python3 "$(dirname "$0")/router.py" "$GOAL"