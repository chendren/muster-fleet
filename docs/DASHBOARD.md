# Muster Fleet Dashboard

Operator UI for the multi-machine muster bus: live agents, tasks, collaboration
sequence, terminal drill-down, and the **Computer** voice panel.

**Repo:** [github.com/chendren/muster-fleet](https://github.com/chendren/muster-fleet)

## Run

On the **hub** (where `muster serve` and `bus.db` live):

```bash
cd /path/to/muster-fleet
python3 dashboard/server.py
# default http://127.0.0.1:8787
```

Requires local access to `~/.local/share/muster/bus.db` and (for pane
capture) hub `tmux`. Spoke activity is pulled via SSH collectors when
configured.

## Views

| View | Purpose |
|------|---------|
| **Fleet** | Agent cards (live / departed), filters (all / live / grok / claude / hub / spoke) |
| **Collab** | Collaboration sequence — thread focus, who messaged whom |
| **Terminals** | Live tmux pane drill-down (HTML ANSI) for agents with real panes |

Headless hub Groks (`grok-hub-*`) correctly show **no pane** — activity may
be `source: "none"`. That is accurate, not a dashboard bug.

## Key APIs

| Endpoint | Role |
|----------|------|
| `GET /api/status` | Merged fleet payload (bus + collectors) |
| `GET /api/pane?alias=` | Live pane snapshot for drill-down |
| `GET /api/discovery` · `POST /api/discovery/scan` | Session auto-discovery |
| `GET/POST /api/llm/mode` · `POST /api/llm/complete` | Local Ollama or **Cloud Haiku 4.5** ([`LLM_TOGGLE.md`](LLM_TOGGLE.md)) |
| `GET /api/agentcore/health` · `POST /api/agentcore/invoke` | Local AgentCore emulator proxy |
| `GET /api/router/requests` · `POST /api/router/route` · memory | Fleet router + context |
| `GET /api/mesh` · `GET /api/fleet/health` | Mesh graph + process health |
| `GET /api/voice/*` | Computer voice stack (see [`docs/VOICE.md`](VOICE.md)) |

Data shape for collectors: [`dashboard/CONTRACT.md`](../dashboard/CONTRACT.md).

## Liveness rules (don’t “fix” these away)

- Muster `last_seen` alone is noisy for headless workers (re-register bursts).
- Dashboard combines bus `last_seen` with collector `activity.updated_at`.
- Departed / tombstoned agents should collapse in the UI, not share equal
  weight with live workers.
- **Alias count ≠ pane count.** A 6-worker fleet may have 3 panes (Claude +
  two spoke Groks) and 3 headless hub Groks.

## Callsigns / human names

Voice and UI filters map spoken names to bus aliases via
`dashboard/voice/aliases.json` (e.g. “Number One” → `hub-tui-claude`,
“Hub Alpha” → `grok-hub-a`). Keep that file in sync when you rename aliases.

## Collectors

| Module | Machine | Role |
|--------|---------|------|
| `dashboard/collectors/hub_local.py` | hub | Claude transcripts, hub panes, npm/native grok signals |
| `dashboard/collectors/spoke_local.py` | spoke (via SSH) | spoke-side sessions |

## Frontend notes

- Single-file UI: `dashboard/frontend/index.html`
- Terminal panes: ANSI → HTML renderer
- Task board: FLIP animation for claim/complete transitions
- Computer panel: floating LCARS-style hold-to-talk; executes tool calls
  client-side against existing JS (`setView`, `openModal`, `setFocusAlias`, …)

## Related

- [`docs/FLEET.md`](FLEET.md) — spawn / drain / restart
- [`docs/VOICE.md`](VOICE.md) — STT / LLM / TTS
- [`dashboard/CONTRACT.md`](../dashboard/CONTRACT.md) — collector JSON contract
