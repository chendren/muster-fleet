# muster-fleet

**GitHub:** [github.com/chendren/muster-fleet](https://github.com/chendren/muster-fleet)  
**Owner:** [chendren](https://github.com/chendren)

A two-machine, multi-CLI coding-agent **fleet** on top of
[Court Schuett’s `muster`](https://github.com/schuettc/muster) — a local
coordination bus for independent coding-agent sessions (Claude Code, Grok
CLI, …) to message and hand tasks to each other without copy/paste.

This repository is the **working multi-machine deployment**: SSH socket
bridge, worker spawn/drain automation, operator dashboard, and a local
“Computer” voice stack. It is not a fork of `muster`; it is the layer
around it.

| Layer | What it is | Doc |
|-------|------------|-----|
| Bus bridge | Hub `muster serve` + spoke SSH reverse unix-socket tunnel | this README § Architecture–§ Verify |
| Fleet workers | Spawn Claude/Grok on hub or spoke; continuous inbox drain | [`docs/FLEET.md`](docs/FLEET.md) |
| Dashboard | Live agents, mesh, terminals, requests/memory | [`docs/DASHBOARD.md`](docs/DASHBOARD.md) |
| LLM toggle | **Local** Ollama or **Cloud** Claude Haiku 4.5 (subscription) | [`docs/LLM_TOGGLE.md`](docs/LLM_TOGGLE.md) |
| Discovery | Auto-scan CLI/tmux sessions on hub (+ spoke scan) | `fleet/discovery/` |
| AgentCore | Local Bedrock AgentCore-style runtime emulator | [`docs/AGENTCORE.md`](docs/AGENTCORE.md) |
| Router | Route goals, track requests, memory/context | [`docs/ROUTER.md`](docs/ROUTER.md) |
| Computer voice | Local Whisper + Ollama + Kokoro-ONNX (no macOS `say`) | [`docs/VOICE.md`](docs/VOICE.md) |
| Epic / landed | Multi-machine platform enhancement plan + status | [`docs/EPIC-FLEET-ENHANCE.md`](docs/EPIC-FLEET-ENHANCE.md) |
| War stories | Bugs we hit so you don’t re-derive them | [`PITFALLS.md`](PITFALLS.md) |

`muster` itself is **local-only** by design: one unix-socket daemon, one
SQLite file, no networking. We bridge that across two Macs on the same LAN
— one **hub** running the real daemon, one **spoke** reaching it over a
persistent SSH-forwarded socket — so agents on either machine share one
bus.

---

## Architecture

```
┌─────────────────────────┐     SSH reverse tunnel      ┌──────────────────────────┐
│   HUB (e.g. Mac Studio)  │  (unix-socket forward)      │  SPOKE (e.g. MacBook Pro) │
│                          │◄────────────────────────────│                           │
│  muster serve (daemon)   │                             │  muster client only        │
│  ~/.local/share/muster/  │                             │  MUSTER_NO_AUTOSPAWN=1    │
│    sock  ◄── real socket │──── forwarded to ─────────► │  sock (remote end)        │
│    bus.db (SQLite)       │                             │                           │
│                          │                             │                           │
│  Claude TUI  (tmux)      │                             │  Grok TUI  (tmux)         │
│  Grok headless loops     │                             │  (native/Rust grok)       │
│  Dashboard :8787         │                             │                           │
│  fleet-nudge-tui         │── SSH send-keys ───────────►│  spoke panes              │
└─────────────────────────┘                             └──────────────────────────┘
```

Only the hub runs `muster serve`. The spoke dials what looks like a local
socket; that file is the remote end of an SSH `-R` forward to the hub’s
real socket. Every register/send/task call from the spoke round-trips to
**one** daemon and **one** SQLite file.

`MUSTER_NO_AUTOSPAWN=1` on the spoke ensures a down tunnel fails loudly
instead of silently spawning a second local daemon (split-brain bus with
no merge path).

### Worker modes (important)

| Placement | Mode | Why |
|-----------|------|-----|
| Hub + Grok (npm CLI) | **Headless drain loop** | Interactive npm grok-cli corrupts multi-tool-call args; `-p` is reliable |
| Hub + Claude | **tmux TUI** | Real pane for dashboard drill-down; nudged every ~20s |
| Spoke + Grok (native) | **tmux TUI** | Native CLI is fine interactively; nudged over SSH |
| Spoke + Claude | **Not automatable** | Subscription keychain needs a GUI session |

See [`docs/FLEET.md`](docs/FLEET.md) for spawn/restart/drain details.

---

## Repository map

```
muster-fleet/
  README.md                 # this file
  PITFALLS.md               # post-mortems
  docs/
    FLEET.md                # spawn, drain, nudge, restart
    DASHBOARD.md            # operator UI
    VOICE.md                # Computer voice stack
    LLM_TOGGLE.md           # local Ollama vs cloud Haiku 4.5
    AGENTCORE.md            # local AgentCore emulator
    ROUTER.md               # request router + memory
    EPIC-FLEET-ENHANCE.md   # platform epic + landed checklist
  fleet/
    muster-spawn-tui.sh     # spawn Claude/Grok on hub|spoke
    fleet-restart-hub-workers.sh
    fleet-nudge-tui.sh      # keep TUI workers draining
    discovery/              # auto-discovery of CLI sessions
    llm/                    # local/cloud complete abstraction
    agentcore/              # AgentCore runtime emulator (:8790)
    router/                 # route / track / memory
    acceptance/smoke.sh     # API smoke tests
  dashboard/
    server.py               # aggregator + voice + fleet APIs
    frontend/index.html     # fleet / mesh / requests / memory / LLM toggle
    collectors/             # hub_local, spoke_local
    CONTRACT.md
    voice/                  # STT/LLM/TTS (see docs/VOICE.md)
  config/                   # Claude hooks, Grok coordination text
  patches/                  # npm grok-cli bugfixes
  .claude/skills/fleet-spawn/
```

---

## Quick start (already-bridged machines)

If the SSH tunnel and `muster serve` are already up:

```bash
# Install fleet tools on the hub
install -m 755 fleet/muster-spawn-tui.sh           ~/.local/bin/muster-spawn-tui
install -m 755 fleet/fleet-restart-hub-workers.sh  ~/.local/bin/fleet-restart-hub-workers
install -m 755 fleet/fleet-nudge-tui.sh            ~/.local/bin/fleet-nudge-tui

# Bring up a standard 3× hub Grok + nudge supervisor
fleet-restart-hub-workers

# Optional: Claude TUI + spoke Groks
muster-spawn-tui claude hub   hub-tui-claude worker
muster-spawn-tui grok   spoke grok-spoke-a   worker
muster-spawn-tui grok   spoke grok-spoke-b   worker

# Dashboard (+ fleet APIs on :8787)
python3 dashboard/server.py   # http://127.0.0.1:8787

# Optional: AgentCore emulator
fleet/agentcore/run.sh        # http://127.0.0.1:8790/health
```

### Local vs Cloud LLM (Claude Haiku 4.5)

The dashboard and fleet services share a single LLM mode, persisted at
`~/.local/share/muster-fleet/llm-mode.json`.

| Mode | Backend | Model |
|------|---------|--------|
| `local` | Ollama `http://127.0.0.1:11434` | `qwen2.5:3b` (override with `FLEET_LOCAL_MODEL`) |
| `cloud` | Claude Code CLI on the hub (`claude -p`) | **`claude-haiku-4-5-20251001`** (Claude Haiku 4.5) via **subscription** — no API key in repo |

Cloud uses the same logged-in Claude Code subscription as interactive
Claude on the hub (`claude login`), not a separate Anthropic API key file.

```bash
# Prefer cloud Haiku 4.5
curl -s -X POST http://127.0.0.1:8787/api/llm/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"cloud"}'

# Prove mode
curl -s http://127.0.0.1:8787/api/llm/mode
# → {"mode":"cloud"}

# Complete through the dashboard (uses cloud when mode=cloud)
curl -s -X POST http://127.0.0.1:8787/api/llm/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Reply with exactly: FLEET_CLOUD_TEST_OK"}'
# → {"text":"FLEET_CLOUD_TEST_OK","mode":"cloud","latency_ms":...}

# Prove the runtime model id (Claude Code JSON)
claude -p --model claude-haiku-4-5-20251001 --output-format json \
  'Reply with exactly: DIRECT_HAIKU_OK' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(list((d.get('modelUsage') or {}).keys()))"
# → ['claude-haiku-4-5-20251001']
```

Full detail: [`docs/LLM_TOGGLE.md`](docs/LLM_TOGGLE.md).

### Fleet platform APIs (dashboard `:8787`)

| Method | Path | Role |
|--------|------|------|
| GET | `/api/status` | Merged fleet wall |
| GET / POST | `/api/discovery` / `/api/discovery/scan` | Session discovery |
| GET / POST | `/api/llm/mode` | `local` \| `cloud` |
| POST | `/api/llm/complete` | Completion via active mode |
| GET | `/api/agentcore/health` | Proxy to local emulator `:8790` |
| POST | `/api/agentcore/invoke` | Invoke AgentCore emulator |
| GET | `/api/router/requests` | Tracked routed requests |
| POST | `/api/router/route` | Route a goal across the fleet |
| GET / POST | `/api/router/memory…` | Context / memory store |
| GET | `/api/mesh` | Machines → sessions → threads |
| GET | `/api/fleet/health` | Loops, nudge, agentcore, llm mode |

Smoke test:

```bash
fleet/acceptance/smoke.sh
```

Prove drain (create tasks via muster MCP or CLI tooling) and expect stamp
files under `/tmp/fleet-drain-*.txt` within one headless cycle or one nudge
interval — see [`docs/FLEET.md`](docs/FLEET.md#smoke-test-prove-the-fleet-drains).

---

## Prerequisites

- Two Macs on the same LAN (Linux spokes/hubs need trivial path tweaks;
  Windows needs WSL2 per upstream `muster`).
- SSH from hub → spoke with a **passphrase-less** bridge key.
- Claude Code and/or Grok CLI installed where you will run them.
- Hub: `tmux`, Python 3, optional Ollama + Whisper for voice.

---

## 1. Install `muster` on both machines

```bash
curl -fsSL https://muster.tools/install.sh | sh
```

Installs to `~/.local/bin/muster`. Do this on **both** hub and spoke.

## 2. Passwordless SSH from hub to spoke

Generate a **dedicated, passphrase-less** key on the hub (don’t reuse a
passphrase-locked personal key — it breaks automated tunnel reconnect):

```bash
# on the hub
ssh-keygen -t ed25519 -f ~/.ssh/id_muster -N "" -C "muster-bridge"
cat ~/.ssh/id_muster.pub
```

On the **spoke**, install that public key:

```bash
# on the spoke
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA...output-from-above..." > ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Enable Remote Login on the spoke: **System Settings → General → Sharing →
Remote Login**.

Hub `~/.ssh/config`:

```
Host muster-remote
    HostName <spoke-lan-ip>
    User <spoke-username>
    IdentityFile ~/.ssh/id_muster
    IdentitiesOnly yes
```

```bash
ssh muster-remote 'echo OK'
```

## 3. Prep the spoke’s socket path

```bash
ssh muster-remote 'mkdir -p ~/.local/share/muster && rm -f ~/.local/share/muster/sock'
```

## 4. Persistent reverse tunnel (launchd, self-healing)

On the **hub**, `~/.local/bin/muster-tunnel.sh`:

```sh
#!/bin/sh
LOCAL_SOCK="$HOME/.local/share/muster/sock"
REMOTE_SOCK="/Users/<spoke-username>/.local/share/muster/sock"
while true; do
  ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      muster-remote "rm -f $REMOTE_SOCK" 2>/dev/null
  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -R "$REMOTE_SOCK:$LOCAL_SOCK" muster-remote
  sleep 5
done
```

```bash
chmod +x ~/.local/bin/muster-tunnel.sh
```

`~/Library/LaunchAgents/tools.muster.tunnel.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>tools.muster.tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/<hub-username>/.local/bin/muster-tunnel.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/muster-tunnel.log</string>
  <key>StandardErrorPath</key><string>/tmp/muster-tunnel.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/tools.muster.tunnel.plist
```

## 5. Make the hub resilient

**Daemon** — `~/Library/LaunchAgents/tools.muster.daemon.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>tools.muster.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/<hub-username>/.local/bin/muster</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/muster-daemon.log</string>
  <key>StandardErrorPath</key><string>/tmp/muster-daemon.err</string>
</dict>
</plist>
```

**Prevent sleep** — `tools.muster.caffeinate.plist` running
`/usr/bin/caffeinate -disu` with `RunAtLoad` + `KeepAlive`.

```bash
launchctl load ~/Library/LaunchAgents/tools.muster.daemon.plist
launchctl load ~/Library/LaunchAgents/tools.muster.caffeinate.plist
sudo pmset -a autorestart 1   # after power loss
```

Enable auto-login on the hub if you need unattended reboot recovery
(FileVault still requires an unlock — expected).

## 6. Register `muster` as MCP (both machines, both CLIs)

**Claude Code:**

```bash
claude mcp add muster -s user -- muster mcp
```

**Grok CLI (npm `@vibe-kit/grok-cli`):**

```bash
grok mcp add muster -c muster -a mcp
```

**Grok CLI (native/Rust):**

```bash
grok mcp add muster -- muster mcp
```

On the spoke, also set `MUSTER_NO_AUTOSPAWN=1` on each registration.

## 7. Auto-register sessions on start

**Claude Code** — merge hooks from
[`config/claude-settings-hooks.json`](config/claude-settings-hooks.json)
into `~/.claude/settings.json` (append, don’t clobber). Use the **absolute
path** to `muster` in hook commands.

**Grok CLI** has no lifecycle hooks. Append
[`config/grok-coordination-instructions.md`](config/grok-coordination-instructions.md)
to `~/.grok/GROK.md` (npm) or `~/.grok/AGENTS.md` (native).

**Fleet workers** should still be spawned via `muster-spawn-tui` so
register + drain is deterministic (not “maybe the model reads GROK.md”).

## 8. Verify the bus

```bash
muster agents
muster send <alias> "hi" --from you
muster inbox <alias>
```

End-to-end: two agents, `send_message` / `get_inbox` / `reply`, then
`task_create` → `task_claim` → `task_transition` across machines.

## 9. Fleet + dashboard + voice

```bash
fleet-restart-hub-workers
python3 dashboard/server.py
# optional voice models:
cd dashboard/voice && ./download_models.sh
```

| Doc | Topic |
|-----|--------|
| [`docs/FLEET.md`](docs/FLEET.md) | Spawn matrix, early-kill loops, TUI nudge, smoke tests |
| [`docs/DASHBOARD.md`](docs/DASHBOARD.md) | UI views, APIs, liveness rules |
| [`docs/VOICE.md`](docs/VOICE.md) | Computer panel, local models, tool catalog |

---

## Failure modes and limits

**Self-healing (no hands):**

- Hub sleep prevented (`caffeinate`)
- Daemon crash → launchd restart
- Tunnel drop → retry loop (~5s after detect)
- Hub reboot → `RunAtLoad` + auto-login (if configured)

**Not fixable by config (shape of `muster`):**

- Hub permanently off / disk dead → bus is down (single daemon, no HA)
- LAN/router down → no spoke path

For true hub-loss survival you need either two independent buses with
manual handoff, or upstream HA/replication.

---

## Known upstream bugs (npm `@vibe-kit/grok-cli`)

Native/Rust `grok` did not show these. On npm `0.0.34`-era builds:

1. Headless MCP tools never loaded — missing `await` on MCP init + broken
   ESM import (`.js` extension) swallowed by `.catch(() => {})`.
2. Live Search `search_parameters` field 410s — strip it.
3. Interactive multi-tool-call argument corruption (see Fleet section).

Patches under [`patches/`](patches/) apply to compiled output under
`~/.npm-global/lib/node_modules/@vibe-kit/grok-cli/dist/` and are wiped by
the next `npm i -g` — reapply or check upstream.

---

## Credit

Bus, protocol, daemon, MCP server, CLI, hooks:
[Court Schuett’s `muster`](https://github.com/schuettc/muster).

This repo ([**chendren/muster-fleet**](https://github.com/chendren/muster-fleet))
is the multi-machine bridge, fleet spawn/drain automation, dashboard, and
local Computer voice stack on top. If you only need a single-machine bus,
use upstream `muster` directly.
