#!/usr/bin/env bash
# EPIC-6 spoke discovery entrypoint. Run ON the spoke (MacBook).
# Writes /tmp/muster-discovery-spoke.json and prints the same JSON to stdout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export MUSTER_MACHINE="${MUSTER_MACHINE:-spoke}"
export DISCOVERY_OUT="${DISCOVERY_OUT:-/tmp/muster-discovery-spoke.json}"
export MUSTER_HOME="${MUSTER_HOME:-$HOME/.local/share/muster}"

# Prefer shared bus; do not spawn a second daemon when tunnel is intentional.
export MUSTER_NO_AUTOSPAWN="${MUSTER_NO_AUTOSPAWN:-1}"

exec python3 "$ROOT/spoke_scan.py" "$@"
