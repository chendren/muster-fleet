# Router Agent (EPIC-5 / 5b)

fleet/router/router.py implements:
- Agent discovery via `muster agents`
- Worker selection by role/machine heuristics
- Task creation via `muster send`
- Request logging to ~/.local/share/muster-fleet/requests.jsonl
- SQLite memory store (scope: fleet|agent|request|session)
- assert_context attaches memory to routed tasks

Endpoints (via dashboard/server.py):
- POST /api/router/route {goal, preferred_role?, preferred_machine?}
- GET  /api/router/requests
- POST /api/router/memory {key,scope,value}
- GET  /api/router/memory/<key>

CLI:
  fleet/router/route.sh "goal text"

All acceptance curls must succeed before completed.