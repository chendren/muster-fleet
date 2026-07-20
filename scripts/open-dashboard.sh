#!/usr/bin/env bash
# Auto-open Muster Fleet Dashboard in the default browser.
set -euo pipefail
BASE="${1:-http://127.0.0.1:8787}"
# Prefer health first so we do not open a dead tab
if ! curl -sS -m 2 "$BASE/api/health" >/dev/null 2>&1; then
  echo "WARN: $BASE/api/health not reachable — opening anyway" >&2
fi
if command -v open >/dev/null 2>&1; then
  open "$BASE"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$BASE"
else
  echo "Open manually: $BASE"
fi
echo "opened $BASE"
