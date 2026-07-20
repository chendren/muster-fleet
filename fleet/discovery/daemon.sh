#!/usr/bin/env bash
# Discovery daemon — runs every 20s, writes discovery.json
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while true; do
  python3 "$DIR/discover.py" >/dev/null 2>&1 || true
  sleep 20
done