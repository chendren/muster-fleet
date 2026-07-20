---
name: fleet-spawn
description: >
  Use when the user wants to spawn a muster fleet worker with a specific
  CLI (Claude Code vs Grok CLI) on a specific machine (this hub Mac vs the
  MacBook Pro spoke) — for deliberate token/cost control, or to compose a
  fleet of "grok only", "grok and claude", or "claude only" workers. Fires
  on: spawn a worker, spawn a grok/claude worker, fleet composition, TUI
  type assignment, muster-spawn-tui.
---

# Fleet worker spawning (precise CLI-type control)

`muster-spawn-tui` (installed at `~/.local/bin/muster-spawn-tui` on both
the hub and the spoke) spawns a real, muster-registered worker with an
explicit choice of CLI — so you can steer work toward Grok CLI (separate
token/cost accounting from Claude Code) instead of defaulting everything
to Claude Code.

Canonical source: `fleet/muster-spawn-tui.sh` in the `dashboard` branch of
`chendren/muster-fleet` on GitHub. Redeploy from there if either machine's
copy goes stale.

## Usage

```
muster-spawn-tui <claude|grok> <hub|spoke> <alias> [role]
```

## What each combination actually does — tested, not assumed

| CLI | Machine | Result |
|---|---|---|
| `claude` | `hub` | Real tmux TUI, full live pane-capture support |
| `grok` | `hub` | **Not a visual TUI.** The installed npm `@vibe-kit/grok-cli` has a reproducible bug in its interactive/streaming tool-call handling (a delta-merge reducer keys by array position instead of the OpenAI-spec `index` field, corrupting arguments after the first tool call). Headless mode is unaffected and has been reliable across dozens of calls. So this spawns a persistent background polling loop instead — a genuinely live, continuously-registered worker that drains its inbox every ~25s, just with no pane to visually drill into (`pane_snapshot` reads `source: "none"` on the dashboard, same as any headless worker). |
| `grok` | `spoke` | Real tmux TUI (native/Rust grok CLI, no such bug), full live pane-capture support |
| `claude` | `spoke` | **Cannot be automated at all**, headless or interactive. Claude Code's subscription login lives in a keychain only reachable from an active GUI session — nothing spawned over SSH can reach it. `muster-spawn-tui` fails fast with this exact explanation rather than hanging. If Claude Code needs to run on the spoke, open a real Terminal there and run `claude` yourself. |

## Composing a fleet

There's no separate preset command — just call `muster-spawn-tui` once per
worker you want:

```
# grok-only fleet, one per machine
muster-spawn-tui grok hub    grok-a
muster-spawn-tui grok spoke  grok-b

# mixed fleet
muster-spawn-tui claude hub  claude-a
muster-spawn-tui grok   spoke grok-b

# claude-only (spoke leg needs you to open Terminal there manually — see table above)
muster-spawn-tui claude hub  claude-a
```

## Why grok over claude for token management

Claude Code and Grok CLI meter usage against separate accounts/budgets.
Routing routine or high-volume work through `grok`-backed workers keeps it
off the Claude token count while reserving Claude Code for whatever
actually needs it.

## Gotchas already found and built in (don't rediscover these)

- **Never trust bare `grok`/`tmux` + PATH ordering.** A native grok binary
  and an npm one can both resolve to `grok` depending on PATH order, and
  `tmux` may not be on PATH at all in a non-interactive SSH/launchd
  context even when it's on PATH interactively. The script resolves
  binaries explicitly rather than relying on PATH.
- **A project-level `.grok/settings.json` silently shadows the global
  muster MCP registration** — zero MCP tools, no warning, the agent just
  starts guessing shell commands instead of calling real tools. Every
  spawned worker `cd`s to `$HOME` first, where the correctly-configured
  global settings live.
- **Bypass-permission flags differ per CLI**: Claude Code wants
  `--dangerously-skip-permissions`; native grok CLI wants
  `--permission-mode bypassPermissions`; the npm grok-cli needs neither
  (and has none). The workspace-trust dialog (Claude Code, first run in a
  new directory) is a *separate* gate from the permission-check flag and
  isn't skipped by it — headless (`-p`) mode auto-skips it, but a fresh
  interactive TUI in a never-seen directory still needs one manual
  approval the first time.
