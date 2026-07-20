#!/bin/sh
set -u
BASE=${BASE:-http://127.0.0.1:8787}
fail=0
check() {
  url="$1"; name="$2"
  code=$(curl -s -o /tmp/smoke_body -w '%{http_code}' -m 5 "$url" || echo 000)
  if [ "$code" = "200" ]; then echo "OK  $name ($code)"; else echo "FAIL $name ($code)"; fail=1; fi
}
check "$BASE/api/status" status
check "$BASE/api/discovery" discovery
check "$BASE/api/llm/mode" llm_mode
check "$BASE/api/agentcore/health" agentcore
check "$BASE/api/router/requests" router
check "$BASE/api/mesh" mesh
check "$BASE/api/fleet/health" fleet_health
check http://127.0.0.1:8790/health agentcore_direct
exit $fail
