#!/bin/sh
# muster-spawn-tui.sh — spawn a muster-registered worker on either machine,
# with an explicit CLI-type choice (Claude Code vs Grok CLI) so token/cost
# accounting can be steered deliberately.
#
# Usage:
#   muster-spawn-tui.sh <cli> <machine> <alias> [role]
#
#   <cli>     claude | grok        (which CLI backs this worker)
#   <machine> hub | spoke          (where it runs — spoke goes over SSH)
#   <alias>   muster alias for this worker
#   [role]    muster role (default: worker)
#
# Always launches with auto-approve flags baked in from the start (no
# manual trust/permission-prompt babysitting), and an initial prompt that
# makes register_agent the session's first action — deterministic, not
# reliant on the model choosing to follow a GROK.md/AGENTS.md suggestion.
#
# KNOWN LIMITATION — grok+hub is NOT a real tmux TUI:
# The npm @vibe-kit/grok-cli package (the build installed on the hub) has a
# reproducible bug in its INTERACTIVE/streaming tool-call handling — a
# delta-merging reducer keys by array position instead of the OpenAI-spec
# `index` field, corrupting tool-call arguments after the first tool call
# and producing "Invalid tool arguments... trailing characters" errors on
# every subsequent turn. This does NOT affect headless (-p) mode, which has
# been reliable across dozens of calls all session. So: grok+hub spawns a
# persistent headless polling loop instead (same pattern as
# grok-mbp-worker) — a real, continuously-live registered worker, just
# without a live tmux pane to drill into (source: "none" for pane_snapshot,
# same as any headless worker). grok+spoke uses the native/Rust grok CLI,
# which has no such bug — real tmux TUI, real pane capture, works cleanly.

set -eu

CLI="${1:?usage: muster-spawn-tui.sh <claude|grok> <hub|spoke> <alias> [role]}"
MACHINE="${2:?machine required: hub|spoke}"
ALIAS="${3:?alias required}"
ROLE="${4:-worker}"

PRIME_PROMPT="Call the register_agent (or mcp__muster__register_agent) tool with alias=${ALIAS}, role=${ROLE}, model_type=grok_placeholder. After registering, say ready and wait for instructions on the muster bus. Do nothing else until addressed."

spawn_tmux_tui() {
  # $1 = launch command (binary + flags), $2 = model_type for the prime prompt
  session="muster-tui-${MACHINE}-${ALIAS}"
  prompt=$(printf '%s' "$PRIME_PROMPT" | sed "s/grok_placeholder/$2/")
  # cd "$HOME" first: both grok-cli builds and Claude Code can pick up a
  # project-level config (.grok/settings.json, .claude/settings.json) that
  # silently shadows the global muster MCP registration if the launching
  # shell's cwd happens to be some other project directory. $HOME's own
  # config is the one guaranteed to have muster registered.
  cmd="cd \"\$HOME\" && tmux new-session -d -s '${session}' -x 220 -y 50 -- $1 \"${prompt}\""
  if [ "$MACHINE" = "hub" ]; then
    eval "$cmd"
  else
    # Non-interactive SSH sessions don't inherit the spoke's shell PATH —
    # neither ~/.local/bin (grok/claude) nor /opt/homebrew/bin (tmux) are on
    # it by default. Set both explicitly; SSH exec doesn't source .zshrc.
    ssh muster-remote "export PATH=\"\$HOME/.local/bin:/opt/homebrew/bin:\$PATH\"; $cmd"
  fi
  echo "spawned: alias=${ALIAS} cli=${CLI} machine=${MACHINE} mode=tmux-tui tmux-session=${session}"
}

spawn_hub_grok_loop() {
  # Workaround for the npm grok-cli interactive-mode bug (see header
  # comment): a persistent headless polling loop instead of a real TUI.
  #
  # Use the EXPLICIT npm-build binary path, not bare "grok" + PATH order —
  # a native grok binary has appeared at ~/.local/bin/grok on this machine
  # (symlinked to ~/.grok/bin/grok) at some point, which silently shadows
  # the npm build (~/.npm-global/bin/grok, the one already proven reliable
  # in headless mode all session) whenever ~/.local/bin precedes
  # ~/.npm-global/bin on PATH. Same lesson as the tmux/server.py PATH bug —
  # don't trust PATH ordering, name the binary explicitly.
  GROK_BIN="$HOME/.npm-global/bin/grok"
  loop_script="/tmp/muster-loop-${ALIAS}.sh"
  cat > "$loop_script" <<EOF
#!/bin/sh
# ALWAYS run from \$HOME. grok-cli reads a project-level .grok/settings.json
# relative to cwd when one exists, which silently SHADOWS the global
# ~/.grok/settings.json (and its muster MCP registration) with no warning
# or error — the agent just loses every MCP tool and starts guessing shell
# commands instead. \$HOME's own settings.json is the one with muster
# registered, so anchor here regardless of the launching shell's cwd.
cd "\$HOME"
"$GROK_BIN" -p "Call register_agent with alias=${ALIAS}, role=${ROLE}, model_type=grok. Just confirm registration, say nothing else." >/tmp/muster-loop-${ALIAS}.log 2>&1
while true; do
  "$GROK_BIN" -p "Call get_inbox for alias ${ALIAS}. If there are unread task or message threads, read each with get_thread, handle the request fully, task_transition/reply as appropriate. If nothing unread, just say idle — do nothing else." >>/tmp/muster-loop-${ALIAS}.log 2>&1
  sleep 25
done
EOF
  chmod +x "$loop_script"
  nohup sh "$loop_script" > "/tmp/muster-loop-${ALIAS}-nohup.log" 2>&1 < /dev/null &
  disown
  echo "spawned: alias=${ALIAS} cli=${CLI} machine=${MACHINE} mode=headless-loop (no live TUI pane — see script header) pid=$!"
}

case "$CLI" in
  claude)
    if [ "$MACHINE" = "spoke" ]; then
      echo "ERROR: claude+spoke can't be automated — Claude Code's subscription" >&2
      echo "login only works from an active GUI session; SSH-spawned sessions" >&2
      echo "(interactive or headless) can't reach the keychain credential." >&2
      echo "Open a real Terminal on the MacBook Pro yourself and run: claude" >&2
      exit 1
    fi
    spawn_tmux_tui "claude --dangerously-skip-permissions" "claude"
    ;;
  grok)
    if [ "$MACHINE" = "hub" ]; then
      spawn_hub_grok_loop
    else
      spawn_tmux_tui "grok --permission-mode bypassPermissions" "grok"
    fi
    ;;
  *)
    echo "unknown CLI '$CLI' — must be 'claude' or 'grok'" >&2
    exit 1
    ;;
esac
