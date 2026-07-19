# Pitfalls we actually hit

If you follow [`README.md`](README.md) in order, you shouldn't hit any of
these — they're recorded here so if something in your own setup goes
sideways, you can jump straight to the likely cause instead of re-deriving
it from scratch.

## SSH auth

- **`ssh-copy-id` "succeeded" but key auth still failed.** Turned out the
  password prompt during copy had actually failed silently, or the wrong
  key ended up in `authorized_keys` (see next point) — verify with
  `ssh -o BatchMode=yes ...` after any key setup; if it prompts for a
  password or fails outright, don't trust that the copy worked just
  because the command exited 0.
- **Wrong key in `authorized_keys`.** At one point the spoke's
  `authorized_keys` contained a *different* keypair entirely (comment
  `chad.hendren@gmail.com`) than the one actually being offered by the
  hub. `ssh -v` shows exactly which key is offered
  (`debug1: Offering public key: ...`) — compare that fingerprint against
  `ssh-keygen -lf ~/.ssh/authorized_keys` on the remote side rather than
  assuming the file is right.
- **Passphrase-protected private key breaks non-interactive SSH.**
  `BatchMode=yes` (or any unattended context) will not prompt for a
  passphrase — it just silently skips that key and moves on, giving a
  generic "Permission denied" with no hint about *why*. The verbose tell:
  `debug1: Server accepts key: ...` followed immediately by more key
  attempts and eventual failure, instead of "Authentication succeeded."
  Either load the key into `ssh-agent` (`ssh-add --apple-use-keychain`) or
  — simpler for an automation-only bridge — generate a dedicated,
  passphrase-less keypair just for this purpose and don't reuse your
  personal key.
- **`sudo systemsetup -setremotelogin on` fails with "requires Full Disk
  Access."** Terminal itself needs Full Disk Access granted in System
  Settings → Privacy & Security before it can toggle Remote Login via CLI.
  Simpler: just use the GUI toggle (System Settings → General → Sharing →
  Remote Login) instead of fighting the CLI permission.
- **`cat -A` doesn't exist on macOS.** That's a GNU coreutils flag; BSD
  `cat` (what ships on macOS) only supports `-e -t -v` individually —
  the closest equivalent is `cat -evt`.

## The unix-socket bridge

- **`muster` has no multi-host mode, on purpose.** The daemon is
  `net.Listen("unix", socketPath)` — full stop. Don't go looking for a TCP
  flag or a config option; there isn't one, by design. The bridge has to
  be built outside the tool (SSH forwarding, as documented in the main
  README), not inside it.
- **Client auto-spawn creates split-brain if you're not careful.** If a
  spoke's `muster` client can't reach a socket, it auto-spawns its own
  `muster serve` — which means a second, completely independent daemon
  with its own SQLite file, invisible to the first. `MUSTER_NO_AUTOSPAWN=1`
  on the spoke is what prevents this; without it, a dropped tunnel doesn't
  fail loudly, it silently forks your bus in two.

## API keys and credentials

- **Never paste a live password or API key into a chat/agent
  conversation.** If it happens, treat the credential as burned — rotate
  it — regardless of whether it "worked." This came up twice in our setup
  (once with an account password, once by nearly reusing a pasted key) and
  both times the right move was: don't use it, ask for it to be entered
  directly at the machine instead.
- **Headless CLI auth and subscription logins don't mix over SSH.**
  Claude Code's OAuth/subscription credential lives in the macOS login
  keychain, and certain keychain items are only accessible from a session
  with an active GUI/window-server context — a bare non-interactive SSH
  `exec` doesn't have one, even for the same already-logged-in user, even
  though `security find-generic-password` can still *see* the item exists.
  The practical options: (a) don't try to automate that specific
  CLI+auth combination non-interactively — verify it manually once
  instead, or (b) use an API key for that one call, which is separate
  billing from a subscription — don't assume it's free just because the
  subscription is already paid for.
- **If you do use a temp API-key file: verify the content, not just that
  the file exists.** We had one round where the "key" file actually
  contained the *usage/help text* of an unrelated command (`pmset`) that
  got redirected into the wrong file by mistake — `wc -c` against the
  expected length of a real key (a real Anthropic key is 100+ characters)
  catches this in one command, before wasting a round-trip on a doomed API
  call.

## Grok CLI (npm `@vibe-kit/grok-cli` specifically)

- **MCP tools silently never load in headless (`-p`) mode**, for two
  independent, stacked reasons — both are real upstream bugs, not
  environment issues. See [`patches/`](patches/) for the fixes:
  1. The tool-list builder fires MCP server init but doesn't `await` it.
  2. The MCP client's own init function has a broken ESM import (missing
     `.js` extension) that Node's strict resolver rejects — silently
     swallowed by an outer `.catch()`.
  The standalone `grok mcp test` / `grok mcp list` commands report the
  server as healthy the whole time, which is actively misleading — they
  apparently go through a different code path than the actual chat/agent
  loop, so "the MCP test passes" does **not** mean "the agent can see the
  tools."
- **A cold-start MCP handshake can take 15-20+ seconds.** Don't assume a
  headless run is hung just because it's quiet for a while — check for an
  actual stuck process (`ps aux | grep ...`) before killing it or
  concluding something's wrong.
- **Different `grok` binaries have different config formats and CLI
  syntax.** We ended up with two different Grok CLIs across our two
  machines — the npm `@vibe-kit/grok-cli` package (config at
  `~/.grok/user-settings.json`, custom instructions at `~/.grok/GROK.md`,
  `grok mcp add <name> -c <cmd> -a <args...>`) and a separate native/Rust
  `grok` build (config at `~/.grok/config.toml`, custom instructions at
  `~/.grok/AGENTS.md`, `grok mcp add <name> -- <cmd> <args...>`). Check
  `grok --version` and `grok mcp add --help` on each machine rather than
  assuming they match.
