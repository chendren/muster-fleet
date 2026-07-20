---
name: fleet-spawn
description: >
  Use when the user wants to spawn a muster fleet worker with a specific
  CLI (Claude Code vs Grok CLI) on a specific machine (this hub Mac vs the
  MacBook Pro spoke) — for deliberate token/cost control, or to compose a
  fleet of "grok only", "grok and claude", or "claude only" workers. Fires
  on: spawn a worker, spawn a grok/claude worker, fleet composition, TUI
  type assignment, muster-spawn-tui, fleet not draining, restart hub workers,
  fleet-nudge-tui.
---

# Fleet worker spawning (precise CLI-type control)

`muster-spawn-tui` (installed at `~/.local/bin/muster-spawn-tui`) spawns a
real, muster-registered worker with an explicit CLI choice so work can be
steered toward Grok (separate token budget) or Claude.

**Canonical source:** `fleet/muster-spawn-tui.sh` on the `dashboard` branch
of **[chendren/muster-fleet](https://github.com/chendren/muster-fleet)**.
Redeploy after pull:

```bash
install -m 755 fleet/muster-spawn-tui.sh           ~/.local/bin/muster-spawn-tui
install -m 755 fleet/fleet-restart-hub-workers.sh  ~/.local/bin/fleet-restart-hub-workers
install -m 755 fleet/fleet-nudge-tui.sh            ~/.local/bin/fleet-nudge-tui
```

Full ops: [`docs/FLEET.md`](../../../docs/FLEET.md) in the repo (or
https://github.com/chendren/muster-fleet/blob/dashboard/docs/FLEET.md).

## Usage

```
muster-spawn-tui <claude|grok> <hub|spoke> <alias> [role]
fleet-restart-hub-workers          # kill+respawn grok-hub-a/b/c + nudge
fleet-nudge-tui                    # keep TUI workers draining (daemon)
fleet-nudge-tui once               # single kick
```

## What each combination does (tested)

| CLI | Machine | Result |
|-----|---------|--------|
| `claude` | `hub` | Real tmux TUI; session name **= alias**; full pane capture |
| `grok` | `hub` | **Headless drain loop** (not a visual TUI). npm grok-cli interactive multi-tool-call is broken; `-p` + early-kill poll ~10–15s |
| `grok` | `spoke` | Real tmux TUI (native/Rust grok) over SSH `muster-remote` |
| `claude` | `spoke` | **Refused** — subscription keychain needs GUI; open Terminal on the MacBook yourself |

## Drain (if “only the operator works”)

1. Hub Groks must run the **early-kill** loop (idle / `FLEET_CYCLE_N_DONE` kills hung `grok -p`).
2. TUI workers need **`fleet-nudge-tui`** (Claude local; spoke via SSH send-keys — never trust local `muster nudge` for spoke).
3. Restart: `fleet-restart-hub-workers`.

Logs: `/tmp/muster-loop-<alias>.log`, `/tmp/muster-fleet-nudge.log`.

## Compose a fleet

```bash
muster-spawn-tui grok   hub   grok-hub-a
muster-spawn-tui grok   hub   grok-hub-b
muster-spawn-tui grok   hub   grok-hub-c
muster-spawn-tui claude hub   hub-tui-claude
muster-spawn-tui grok   spoke grok-spoke-a
muster-spawn-tui grok   spoke grok-spoke-b
fleet-nudge-tui &   # or rely on fleet-restart-hub-workers
```

## Gotchas (built into the scripts)

- Resolve **npm** `~/.npm-global/bin/grok` and **tmux** by absolute path — never bare PATH.
- Workers `cd $HOME` so project `.grok/settings.json` cannot shadow global MCP.
- Claude needs **both** `--dangerously-skip-permissions` and
  `--permission-mode bypassPermissions`, plus workspace trust pre-accepted
  in `~/.claude.json`.
- tmux **session name = alias** (avoid double-registration / dashboard double-count).
- Kill restart/nudge by **pidfile**, not `pkill -f` (self-matching argv).
- Headless workers show `LIVE ✗` / no pane after `muster gc` — expected; they re-register each cycle.
