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
- **Interactive npm grok-cli corrupts multi-tool-call arguments.** The
  streaming delta-merge reducer keys tool-call fragments by **array
  position** instead of the OpenAI-spec `index` field. After the first
  tool call, subsequent argument JSON is garbage
  (`Invalid tool arguments… trailing characters`). **Headless `-p` is
  fine.** That is why hub Grok workers are drain loops, not tmux TUIs.
  Spoke uses native/Rust grok, which does not have this bug.

## Fleet drain (“only the operator agent does work”)

These three stacked bugs made a fully registered fleet look dead. Full
ops guide: [`docs/FLEET.md`](docs/FLEET.md).

- **Unquoted heredoc expanded `"$@"` when writing the loop script.**
  `spawn_hub_grok_loop` used `cat > loop <<EOF` (unquoted). The parent
  shell expanded `"$@"` to nothing at **write** time, so every cycle ran
  effectively `"" &` → `/tmp/muster-loop-….sh: line 17: : command not
  found` (rc=127) forever. Fix: quoted heredoc `<<'LOOP_EOF'` plus a
  small env preamble that exports `GROK_BIN` / `ALIAS` / `ROLE`.
- **`grok -p` does not exit after the model finishes.** Even with a
  correct loop, the node process hung after printing `idle`. A hard
  timeout of 180s meant workers only re-polled every ~3 minutes, so new
  tasks sat open while the operator’s interactive session looked like the
  only thing working. Fix: **early-kill** — watch only the bytes written
  this cycle; when `idle` or `FLEET_CYCLE_N_DONE` appears, kill the tree.
  Target cadence: ~10–15s idle cycles, 120s max safety timeout.
- **TUI workers do not poll.** Claude Code and spoke Grok TUIs only run a
  turn when something is typed into the pane. A prime prompt of
  “register, say ready, wait” guarantees they never claim tasks. Fix:
  drain-oriented prime prompt **plus** `fleet-nudge-tui` (local tmux for
  hub Claude; **SSH `tmux send-keys`** for spoke — see next point).
- **`muster nudge` is wrong for spoke aliases.** Spoke agents often store
  hub-side `socket_path` / pane ids in `bus.db`. Local `muster nudge
  grok-spoke-a` has been observed typing into the **Claude hub pane**.
  Always kick spoke TUIs over `ssh muster-remote 'tmux send-keys …'`.
- **`muster gc` tombstones headless workers.** No pane ⇒ “dead session.”
  History is kept; the next drain cycle re-registers. Don’t run gc in a
  tight loop against a headless fleet and then conclude the workers are
  gone. `LIVE ✗` on hub Groks is expected.
- **`pkill -f fleet-nudge-tui` from a restart script kills the restart
  script.** The shell wrapper’s argv contains the pattern. Kill by
  **pidfile** only (`/tmp/muster-fleet-nudge.pid`).
- **Bare `echo -n` under `/bin/sh` on macOS** can print the literal `-n`.
  Use `printf` in portable scripts.
- **Session name must equal the muster alias.** Spawning
  `muster-tui-hub-hub-tui-claude` while the prime prompt registers
  `hub-tui-claude` creates **two agents on one pane** and double-counts
  on the dashboard wall.

## Claude Code permissions / trust

- **`--dangerously-skip-permissions` alone is not enough.** Claude can
  still be in footer “manual mode,” which prompts on every tool. Always
  also pass `--permission-mode bypassPermissions` at launch.
- **Workspace trust is a separate gate.** Even with both permission
  flags, a new directory can hang on “Is this a project you trust?”
  Pre-set `projects[path].hasTrustDialogAccepted = true` in
  `~/.claude.json` for `$HOME` (spawn script does this).
- **Claude on spoke cannot be SSH-spawned** with a subscription login —
  keychain items need a GUI session. Fail fast; don’t hang.

## Dashboard / voice

- **`GET /api/voice/aliases` is not a bare map** — it returns
  `{aliases: {…}, display_names: {…}}`. Unwrap before speech lookup.
- **Pipeline audio field naming** — prefer `audio_wav_b64`; keep a
  fallback for `audio_b64` if older clients exist.
- **Never use macOS `say` as the primary TTS path** for the Computer
  panel; Kokoro-ONNX is the intended engine (local models under
  `~/.local/share/muster-voice/`).
- **Python 3.9 on some Macs** — avoid `str | None` syntax in dashboard
  code if the system Python is 3.9; use `Optional[str]` or run under a
  newer venv.
- **Agent count ≠ pane count.** Headless hub Groks are real workers with
  `pane_snapshot: null`. Don’t “fix accuracy” by inventing panes.
