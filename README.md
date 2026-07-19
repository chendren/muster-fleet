# muster-fleet

A two-machine, multi-CLI coding-agent fleet built on top of
[Court Schuett's `muster`](https://github.com/schuettc/muster) — a local
coordination bus that lets independent coding-agent sessions (Claude Code,
Grok CLI, Codex, ...) message and hand tasks to each other without
copy/paste.

`muster` itself is explicitly **local-only** by design: one unix-socket
daemon, one local SQLite file, no networking. This repo documents how we
bridged that across two Macs on the same LAN — one **hub** machine running
the real daemon, and a second **spoke** machine reaching it transparently
over a persistent SSH-forwarded unix socket — so agents on either machine
share one bus, and how we wired up both Claude Code and Grok CLI (two
different Grok CLI implementations, in our case) as peers on it.

This is the working setup, distilled. It skips every dead end we actually
hit (wrong SSH key, passphrase-locked identity, a mismatched key on the
remote's `authorized_keys`, a broken pipe of `pmset` output into an API-key
file, three rounds of "invalid API key", and two real upstream bugs in the
Grok CLI npm package) — see [`PITFALLS.md`](PITFALLS.md) if you want the
full story of what went wrong and why, but you shouldn't need it if you
follow this doc in order.

## Architecture

```
┌─────────────────────────┐         SSH reverse tunnel          ┌──────────────────────────┐
│   HUB (e.g. Mac Studio)  │  (unix-socket forward, persistent)  │  SPOKE (e.g. MacBook Pro) │
│                          │◄────────────────────────────────────│                           │
│  muster serve (daemon)   │                                      │  muster (client only,     │
│  ~/.local/share/muster/  │                                      │  MUSTER_NO_AUTOSPAWN=1)   │
│    sock  ◄── real socket │──── forwarded to ────────────────►  │  ~/.local/share/muster/   │
│    bus.db (SQLite)       │                                      │    sock (forwarded copy)  │
│                          │                                      │                           │
│  Claude Code  (MCP)      │                                      │  Claude Code  (MCP)       │
│  Grok CLI     (MCP)      │                                      │  Grok CLI     (MCP)       │
└─────────────────────────┘                                      └──────────────────────────┘
```

Only the hub ever runs `muster serve`. The spoke's `muster`/Claude
Code/Grok CLI processes dial what looks like their own local socket, but
that socket file is actually the remote end of an SSH `-R` (reverse)
forward whose target is the hub's real socket. Every register/send/task
call from the spoke round-trips over SSH to the one real daemon and its
one SQLite file — there is no second daemon, no split-brain, no sync to
reconcile.

`MUSTER_NO_AUTOSPAWN=1` is set on the spoke specifically so that if the
tunnel is ever down, its clients fail loudly instead of silently spinning
up their own local daemon (which would fork the bus into two disconnected
ones with no way to merge them back).

## Prerequisites

- Two Macs on the same LAN (this also works on Linux spokes/hubs with
  trivial path adjustments; Windows needs WSL2 per upstream `muster`'s own
  requirement).
- SSH access from the hub to the spoke, with a **passphrase-less** key
  dedicated to this bridge (details below — don't reuse a passphrase-locked
  personal key, it breaks the automated retry loop).
- Claude Code and/or Grok CLI already installed on whichever machines will
  run them.

## 1. Install `muster` on both machines

```bash
curl -fsSL https://muster.tools/install.sh | sh
```

Installs to `~/.local/bin/muster`. Do this on **both** the hub and the
spoke.

## 2. Set up passwordless SSH from the hub to the spoke

Generate a **dedicated, passphrase-less** key on the hub — don't reuse your
personal key even if it's convenient, because a passphrase-protected key
silently breaks any automated/background SSH call (`ssh-agent` won't have
it loaded, and non-interactive sessions can't prompt for it):

```bash
# on the hub
ssh-keygen -t ed25519 -f ~/.ssh/id_muster -N "" -C "muster-bridge"
cat ~/.ssh/id_muster.pub
```

On the **spoke**, add that public key to `authorized_keys` — replacing (not
appending to) anything already there under a fresh setup, so there's no
ambiguity about which key is in play:

```bash
# on the spoke
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA...output-from-above..." > ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Enable Remote Login on the spoke: **System Settings → General → Sharing →
Remote Login**. (The `systemsetup -setremotelogin` CLI equivalent needs
Full Disk Access granted to Terminal first — the GUI toggle is simpler.)

Add a convenience alias on the hub (`~/.ssh/config`):

```
Host muster-remote
    HostName <spoke-lan-ip>
    User <spoke-username>
    IdentityFile ~/.ssh/id_muster
    IdentitiesOnly yes
```

Verify:

```bash
ssh muster-remote 'echo OK'
```

## 3. Prep the spoke's socket path

```bash
ssh muster-remote 'mkdir -p ~/.local/share/muster && rm -f ~/.local/share/muster/sock'
```

## 4. Persistent reverse tunnel (launchd-supervised, self-healing)

On the **hub**, `~/.local/bin/muster-tunnel.sh`:

```sh
#!/bin/sh
# Persistent reverse tunnel: exposes this Mac's muster daemon socket
# at the default muster socket path on the spoke, so its
# muster/claude/grok clients transparently share this machine's bus.
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

The retry loop means: spoke reboots, sleeps, or the network blips → the
tunnel reconnects on its own within ~5s of the hub noticing the drop
(`ServerAliveInterval`/`ServerAliveCountMax` detect a dead connection so it
doesn't just hang forever).

## 5. Make the hub itself resilient

The single point of failure in this whole design is the hub. Everything
here is aimed at making sure the hub is always up, since the spoke can't
help if the hub is down (see [Failure modes](#failure-modes-and-limits)
below for what's genuinely still unfixable).

**Daemon supervision** — don't rely on lazy-spawn; run it as a proper
service (`~/Library/LaunchAgents/tools.muster.daemon.plist`):

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

**Prevent sleep** (no `sudo` needed — user-level `caffeinate`, also
launchd-supervised so it restarts if killed),
`~/Library/LaunchAgents/tools.muster.caffeinate.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>tools.muster.caffeinate</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-disu</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/tools.muster.daemon.plist
launchctl load ~/Library/LaunchAgents/tools.muster.caffeinate.plist
```

**Survive a power loss** (needs a password prompt, run it yourself):

```bash
sudo pmset -a autorestart 1
```

**Come back up after a reboot with nobody physically present:** enable
auto-login for the hub's account — **System Settings → Users & Groups →
Login Options (ⓘ) → Automatic login**. If FileVault is on, this only gets
you to the unlock screen automatically, not all the way to a logged-in
session — that's expected, and arguably correct (don't disable disk
encryption for this).

## 6. Register `muster` as an MCP server, on both machines, both CLIs

**Claude Code:**

```bash
claude mcp add muster -s user -- muster mcp
```

**Grok CLI (npm `@vibe-kit/grok-cli`):**

```bash
grok mcp add muster -c muster -a mcp
```

**Grok CLI (native/Rust `grok`, different syntax — check `grok mcp add
--help` on whichever build you have, flags vary by version):**

```bash
grok mcp add muster -- muster mcp
```

On the spoke specifically, also pass `MUSTER_NO_AUTOSPAWN=1` as an env var
on each registration (`-e MUSTER_NO_AUTOSPAWN=1` on both `claude mcp add`
and `grok mcp add`) — see [Architecture](#architecture) for why.

## 7. Auto-register sessions on start

**Claude Code** has real lifecycle hooks. Merge into
`~/.claude/settings.json` (merge, don't overwrite — see
[`config/claude-settings-hooks.json`](config/claude-settings-hooks.json)
for the block; if you already have hooks, append these entries to your
existing arrays rather than replacing the file):

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume",
      "hooks": [{"type": "command", "command": "muster hook SessionStart claude"}]
    }],
    "Stop": [{
      "hooks": [{"type": "command", "command": "muster hook Stop claude"}]
    }],
    "SessionEnd": [{
      "hooks": [{"type": "command", "command": "muster hook SessionEnd claude"}]
    }]
  }
}
```

Use the **absolute path** to `muster` in these commands (e.g.
`/Users/you/.local/bin/muster hook ...`) — hook commands don't always
inherit your shell's `PATH`.

**Grok CLI has no session-lifecycle hooks.** The closest equivalent is a
global instructions file the model reads at the start of every session —
`~/.grok/GROK.md` for the npm build, `~/.grok/AGENTS.md` for the native
build. See [`config/grok-coordination-instructions.md`](config/grok-coordination-instructions.md)
for the text we use — append it to whichever file your Grok CLI build
supports; don't overwrite it if you already have custom instructions
there.

## 8. Verify

```bash
muster agents                 # from either machine, should show every registered agent
muster send <alias> "hi" --from you
muster inbox <alias>
```

A real end-to-end smoke test: register two agents (any mix of
machine/CLI), have one `send_message` to the other, have the other
`get_inbox` / `get_thread` / `reply`. Then try `task_create` →
`task_claim` → `task_transition` across machines to confirm the full task
lifecycle, not just messaging.

## Failure modes and limits

Being straight about what this setup does and doesn't cover:

**Covered (self-healing, no manual intervention needed):**
- Hub sleeps → prevented outright (`caffeinate`).
- Hub daemon crashes → launchd restarts it within seconds.
- Tunnel drops (network blip, spoke reboots/sleeps) → retry loop
  reconnects within ~5s of detecting the drop.
- Hub reboots (crash, update, power restart) → `RunAtLoad` + auto-login
  bring the daemon and tunnel back with no one physically present.
- Hub loses power and comes back → `pmset autorestart` boots it, then the
  above reboot recovery kicks in.

**Not covered, and not fixable by configuration — this is the actual shape
of `muster`'s architecture, not a gap we didn't get to:**
- The hub is physically off, permanently disconnected from the network, or
  its disk fails: the bus is down until it's back, because there is
  exactly one daemon and exactly one SQLite file, with no replication.
  Closing this for real means building leader election and data
  replication into `muster` itself — a fundamentally different, much
  larger project than a deployment script can bolt on from outside.
- If the LAN/router itself goes down, both machines lose the ability to
  reach each other regardless of anything above.

If you need survival of a genuine hub-loss event, the honest options are:
run two fully independent `muster` buses (one per machine) and coordinate
handoffs manually, or contribute real HA/replication support upstream.

## Known upstream bugs we hit and patched (npm `@vibe-kit/grok-cli` only)

The **native/Rust** `grok` CLI didn't have these issues. If you're on the
npm `@vibe-kit/grok-cli` package, as of the version we tested
(`0.0.34`), headless (`-p`) mode silently never saw any MCP tools at all,
for two independent reasons:

1. `getAllGrokTools()` (`dist/grok/tools.js`) fired
   `manager.ensureServersInitialized()` without `await`-ing it — a
   "don't block" optimization that meant the tool list was captured
   before the MCP handshake had a chance to finish.
2. `mcp/client.js`'s `ensureServersInitialized()` did
   `await import('../mcp/config')` — **missing the `.js` extension**,
   which Node's strict ESM resolver rejects outright
   (`ERR_MODULE_NOT_FOUND`). This error was then silently swallowed by a
   `.catch(() => {})` one level up, so it failed with zero visible
   symptom other than "the model just doesn't have your tools."

Also unrelated to MCP: this version always sent xAI's `search_parameters`
field (even set to `"off"`), which now 410s since xAI deprecated Live
Search — see [`patches/`](patches/) for the diffs, which you can apply
directly to your installed copy under
`~/.npm-global/lib/node_modules/@vibe-kit/grok-cli/dist/`. These are
patches to *compiled output*, so they'll be wiped by the next
`npm install -g` upgrade — reapply as needed, or check whether upstream
has since fixed them.

## Credit

Bus, protocol, daemon, MCP server, CLI, hooks: all
[Court Schuett's `muster`](https://github.com/schuettc/muster) — this repo
is purely the multi-machine deployment/bridging layer on top, plus the two
Grok CLI bugfixes. If you just want a coordination bus on one machine, go
use the upstream project directly; you don't need any of this.
