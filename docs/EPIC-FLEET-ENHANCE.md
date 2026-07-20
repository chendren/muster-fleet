# EPIC: Fleet-wide platform enhancement

**Issued by:** MacStudioGrok1 (producer / fleet command)  
**Repo:** `/Users/chad/muster-fleet-dashboard` (GitHub `chendren/muster-fleet`)  
**Mode:** FLEET ONLY — workers claim tasks, implement, reply, `task_transition completed`.  
**Do not** wait for the producer to write your code. Coordinate via muster replies.

## Goals (must land)

1. **UI enhancement** — multi-machine, n-session fleet wall; clearer mesh/comms; request+memory views.
2. **Cross-machine comms for n CLI sessions** — hub + spoke (and future spokes) with arbitrary session count.
3. **Auto-discovery + auto-registration** of CLI sessions (tmux + known CLI processes) on each machine.
4. **Local ↔ Cloud LLM toggle** — Cloud = **Claude Haiku 4.5 via user’s Claude subscription** (Claude Code / Anthropic sub, not raw API key if avoidable); Local = Ollama (`qwen2.5:3b` or better available).
5. **Local microVM-style Amazon Bedrock AgentCore emulator** on the Mac Studio (hub) + **router agent** that routes requests, tracks them across the fleet, and asserts context/memory correctly.
6. **Bonus features** that fully leverage the fleet (tracking board, memory inspector, discovery health, mesh diagram).

## Non-goals / constraints

- No secrets in git. Use env / user keychain already on machine.
- No macOS `say` as primary TTS (existing Kokoro path stays).
- Hub npm Grok interactive is broken — keep hub Grok **headless** drain loops.
- Claude on spoke cannot be SSH-spawned (keychain).
- Prefer stdlib / light deps; AgentCore emulator can use FastAPI or pure `http.server`.
- “MicroVM” on Mac Studio: prefer **Lima / Virtualization.framework lightweight VM** if available; if not installable quickly, ship a **process-isolated AgentCore Runtime emulator** with a clear `MICROVM.md` path to real Lima later. Label honesty in README.

## Architecture target

```
                    ┌─────────────────────────────────────┐
                    │  Dashboard UI (:8787)               │
                    │  fleet | mesh | requests | memory   │
                    │  LLM toggle: local | cloud(haiku)   │
                    └──────────────┬──────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
  discovery daemons          agentcore-router           muster bus
  hub + spoke                (routes + memory)          sock + bus.db
         │                         │                         │
         │                         ▼                         │
         │              AgentCore emulator (local)           │
         │              :8790  (microvm or process)          │
         ▼                         │                         ▼
  auto-register ──────────► n CLI sessions on hub/spoke ◄── tasks
```

## Shared contracts (all workers)

### Paths

| Path | Owner | Purpose |
|------|-------|---------|
| `docs/EPIC-FLEET-ENHANCE.md` | producer | this plan |
| `fleet/discovery/` | EPIC-1 | auto-discovery service |
| `fleet/agentcore/` | EPIC-4 | AgentCore emulator |
| `fleet/router/` | EPIC-5 | request router + memory |
| `dashboard/server.py` | EPIC-2/3/6 | HTTP APIs |
| `dashboard/frontend/index.html` | EPIC-2/3/6 | UI |
| `docs/AGENTCORE.md` | EPIC-4 | operator doc |
| `docs/LLM_TOGGLE.md` | EPIC-3 | operator doc |
| `/tmp/fleet-epic-status.json` | each worker | append status on complete |

### APIs to add (coordinate; don’t break existing `/api/status`, `/api/voice/*`)

| Method | Path | Role |
|--------|------|------|
| GET | `/api/discovery` | discovered sessions + registration state |
| POST | `/api/discovery/scan` | force scan hub (+ spoke via SSH) |
| GET/POST | `/api/llm/mode` | `{mode: "local"\|"cloud"}` persist + apply |
| POST | `/api/llm/complete` | route completion via local or cloud |
| GET | `/api/agentcore/health` | proxy/health of local emulator |
| POST | `/api/agentcore/invoke` | invoke routed agent runtime |
| GET | `/api/router/requests` | tracked request list |
| GET | `/api/router/memory/:key` | memory assertion / get |
| POST | `/api/router/memory` | upsert memory with scope |
| GET | `/api/mesh` | n-machine session graph for UI |

### LLM toggle semantics

- **local**: Ollama HTTP `http://127.0.0.1:11434` model env `FLEET_LOCAL_MODEL` default `qwen2.5:3b`
- **cloud**: Claude **Haiku 4.5** via subscription path:
  - Prefer `claude -p --model haiku` / whatever the installed Claude Code accepts for Haiku 4.5 (probe `claude --help` / model list; document exact flag).
  - Must use existing logged-in subscription on hub (same as hub-tui-claude), not invent API keys.
  - Persist choice in `~/.local/share/muster-fleet/llm-mode.json`

### AgentCore emulator minimum surface

Emulate enough of Bedrock AgentCore Runtime to be useful locally:

- `POST /runtimes/{id}/invocations` (or simplified `POST /invoke`) with session id
- Session memory get/put
- Agent listing
- Health
- Request id tracing headers

Router agent uses emulator + muster bus to assign work to fleet aliases.

### Memory / context assertion

- Store: `~/.local/share/muster-fleet/memory.db` (sqlite) or jsonl if simpler
- Scopes: `session`, `agent`, `fleet`, `request`
- Router must attach relevant memory when routing; workers must write back summaries on task complete when marked `memory: true` in body

## Task board (claim your ID)

| ID | Assignee | Title | Depends |
|----|----------|-------|---------|
| EPIC-1 | `grok-hub-a` | Auto-discovery + auto-registration daemon | — |
| EPIC-2 | `hub-tui-claude` | Dashboard UI: mesh, n-sessions, request+memory panels, LLM toggle chrome | EPIC-1 APIs stubs OK |
| EPIC-3 | `grok-hub-b` | Local/Cloud LLM backend + `/api/llm/*` + wire voice/brain | — |
| EPIC-4 | `grok-hub-c` | AgentCore microVM/emulator on hub + docs | — |
| EPIC-5 | `grok-hub-d` | Router agent: route, track, memory assert; integrate AgentCore | EPIC-4 health |
| EPIC-6 | `grok-spoke-a` | Spoke discovery client + multi-machine tunnel health + register remote sessions | EPIC-1 contract |
| EPIC-7 | `grok-spoke-b` | Fleet extras: e2e acceptance script, mesh data from spoke, status aggregator | 1–6 partial OK |
| EPIC-8 | `hub-tui-claude` (second pass) | Polish UX + integrate all APIs in UI when ready | after replies |

## Definition of done (per task)

1. Code in repo paths above; no secrets.
2. Reply on task thread with: files changed, how to run, residual risks.
3. `task_transition` → `completed` (or `needs_info` / `blocked` with note).
4. Append one line to `/tmp/fleet-epic-status.jsonl`:  
   `{"alias":"...","task":"EPIC-N","status":"completed","ts":...}`

## Coordination rules

- Read this file first: `/Users/chad/muster-fleet-dashboard/docs/EPIC-FLEET-ENHANCE.md`
- Prefer additive APIs; keep existing dashboard working.
- If two workers touch `server.py` / `index.html`, **rebase carefully**: make small functions, avoid rewriting entire files; reply on thread if conflict.
- Claude owns large UI edits; Grok owns services/daemons/scripts.
- After major landings, post FYI broadcast with subject `EPIC-PROGRESS`.

## Smoke acceptance (EPIC-7)

```bash
# discovery sees hub+spoke sessions
curl -s localhost:8787/api/discovery | head
# llm toggle
curl -s -X POST localhost:8787/api/llm/mode -d '{"mode":"local"}' -H 'Content-Type: application/json'
curl -s -X POST localhost:8787/api/llm/complete -d '{"prompt":"ping"}' -H 'Content-Type: application/json'
# agentcore
curl -s localhost:8787/api/agentcore/health
# router
curl -s localhost:8787/api/router/requests | head
# UI loads
curl -s -o /dev/null -w '%{http_code}\n' localhost:8787/
```

## Landed (fleet + orchestrator integration)

As of epic run (MacStudioGrok1 producer command):

| Component | Status | Path / proof |
|-----------|--------|----------------|
| Discovery scan | landed | `fleet/discovery/discover.py` → `GET /api/discovery` |
| Discovery daemon | landed | `fleet/discovery/daemon.sh` |
| Spoke discovery JSON | landed | spoke `/tmp/muster-discovery-spoke.json` |
| LLM local/cloud toggle | landed | `fleet/llm/complete.py`, `docs/LLM_TOGGLE.md`, Cloud model `claude-haiku-4-5-20251001` via subscription `claude -p` |
| LLM complete local | verified | `POST /api/llm/complete` → pong via Ollama |
| AgentCore emulator | landed | `fleet/agentcore/` :8790 + `GET /api/agentcore/health` |
| Router + memory | landed | `fleet/router/router.py`, requests.jsonl + memory.db |
| Mesh + fleet health | landed | `GET /api/mesh`, `GET /api/fleet/health` |
| UI mesh/LLM/requests/memory | in progress (Claude) | tabs + panels in `dashboard/frontend/index.html` |
| Smoke | landed | `fleet/acceptance/smoke.sh` all OK |
| Deep-work hub loops | running | grok-hub-a/b/c/d 600s cycles |
| TUI nudge | running | `fleet-nudge-tui` |

**Orchestrator note:** fleet modules were correct but several routes were nested under `do_POST` incorrectly; producer rewired GET/POST handlers and restarted dashboard. Claude must not kill :8787 while testing.

