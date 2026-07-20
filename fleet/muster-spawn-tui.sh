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
#
# PERMISSIONS — never spawn a bare interactive CLI:
# Claude Code defaults to "manual" permission mode and WILL hang on the first
# MCP/tool call ("Do you want to proceed?") unless launched with BOTH
#   --dangerously-skip-permissions
#   --permission-mode bypassPermissions
# (skip-permissions alone can still land in manual mode via the TUI toggle).
# Grok uses --permission-mode bypassPermissions (native) / headless -p (npm).

set -eu

CLI="${1:?usage: muster-spawn-tui.sh <claude|grok> <hub|spoke> <alias> [role]}"
MACHINE="${2:?machine required: hub|spoke}"
ALIAS="${3:?alias required}"
ROLE="${4:-worker}"

# TUI workers used to "register then wait forever" — that made the fleet look
# dead: only the interactive operator agent did work. Prime prompt now demands
# continuous inbox drain. Pair with fleet-nudge-tui (muster nudge) so idle TUIs
# keep getting a kick every ~20s.
PRIME_PROMPT="You are muster worker alias=${ALIAS} role=${ROLE} model_type=grok_placeholder.
1) Call register_agent (or mcp__muster__register_agent) with alias=${ALIAS}, role=${ROLE}, model_type=grok_placeholder.
2) Immediately call get_inbox for alias ${ALIAS}.
3) For EVERY open/claimed task addressed to you: claim if open, get_thread, DO THE WORK, reply, task_transition completed (or needs_info/blocked with a note).
4) For unread action/reply-requested messages: get_thread, handle, reply.
5) When inbox has no actionable work, say idle. On the next user message (including muster nudges), drain again from step 2.
Stay registered. Prefer tools over chatter. Do not wait to be hand-fed tasks."

# Resolve tmux explicitly — non-interactive / launchd PATH is unreliable.
resolve_tmux() {
  if command -v tmux >/dev/null 2>&1; then
    command -v tmux
    return
  fi
  for c in /opt/homebrew/bin/tmux /usr/local/bin/tmux /usr/bin/tmux; do
    if [ -x "$c" ]; then
      printf '%s' "$c"
      return
    fi
  done
  echo "ERROR: tmux not found" >&2
  exit 1
}

# Pre-accept Claude Code's workspace trust dialog for $HOME (and any
# extra dirs passed as args). Without this, a brand-new interactive TUI
# hangs on "Is this a project you created or one you trust?" even when
# launched with --dangerously-skip-permissions. Trust lives in
# ~/.claude.json under projects[path].hasTrustDialogAccepted.
ensure_claude_workspace_trust() {
  python3 - "$@" <<'PY'
import json, sys
from pathlib import Path
p = Path.home() / ".claude.json"
if not p.exists():
    data = {"projects": {}}
else:
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        data = {"projects": {}}
projects = data.setdefault("projects", {})
paths = [str(Path.home())] + [str(Path(a).expanduser().resolve()) for a in sys.argv[1:]]
changed = False
for path in paths:
    entry = projects.get(path)
    if not isinstance(entry, dict):
        entry = {
            "allowedTools": [],
            "mcpContextUris": [],
            "mcpServers": {},
            "hasTrustDialogAccepted": True,
            "projectOnboardingSeenCount": 0,
            "hasClaudeMdExternalIncludesApproved": False,
            "hasClaudeMdExternalIncludesWarningShown": False,
        }
        projects[path] = entry
        changed = True
    if not entry.get("hasTrustDialogAccepted"):
        entry["hasTrustDialogAccepted"] = True
        projects[path] = entry
        changed = True
if changed:
    data["projects"] = projects
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"claude workspace trust pre-accepted for: {', '.join(paths)}", file=sys.stderr)
PY
}

write_launch_script() {
  # $1 = path to write, $2 = model_type for prime prompt, $3 = CLI argv
  # (space-separated binary + flags, word-split intentionally at exec time)
  launch_path="$1"
  model_type="$2"
  cli_argv="$3"
  prompt=$(printf '%s' "$PRIME_PROMPT" | sed "s/grok_placeholder/${model_type}/")
  prompt_file="/tmp/muster-prime-${ALIAS}.txt"
  printf '%s' "$prompt" > "$prompt_file"

  # Launch script: always cd $HOME (so project-level settings can't shadow
  # the global muster MCP registration), then exec the CLI with the prime
  # prompt. Prompt lives in a file so quoting can't break it.
  cat > "$launch_path" <<EOF
#!/bin/sh
set -eu
cd "\$HOME" || exit 1
# Keep hook SessionStart + explicit register_agent on the same bus alias.
export MUSTER_ALIAS="${ALIAS}"
PROMPT=\$(cat "$prompt_file")
# Intentional word-split of cli_argv (binary + permission flags).
# shellcheck disable=SC2086
exec $cli_argv "\$PROMPT"
EOF
  chmod +x "$launch_path"
}

spawn_tmux_tui() {
  # $1 = CLI argv string (binary + flags), $2 = model_type for prime prompt
  cli_argv="$1"
  model_type="$2"
  # Session name MUST equal the intended muster alias. SessionStart hooks
  # register under the tmux session name (when MUSTER_ALIAS is unset). If
  # the session is "muster-tui-hub-…" and the prime prompt then registers
  # as hub-tui-claude, the bus gets TWO agents on the same pane and the
  # dashboard wall double-counts one TUI.
  session="${ALIAS}"
  legacy_session="muster-tui-${MACHINE}-${ALIAS}"
  launch="/tmp/muster-launch-${ALIAS}.sh"
  prompt_file="/tmp/muster-prime-${ALIAS}.txt"

  # Replace any leftover session with the same name so re-spawns are clean.
  if [ "$MACHINE" = "hub" ]; then
    TMUX_BIN=$(resolve_tmux)
    write_launch_script "$launch" "$model_type" "$cli_argv"
    "$TMUX_BIN" has-session -t "$session" 2>/dev/null && "$TMUX_BIN" kill-session -t "$session" || true
    "$TMUX_BIN" has-session -t "$legacy_session" 2>/dev/null && "$TMUX_BIN" kill-session -t "$legacy_session" || true
    "$TMUX_BIN" new-session -d -s "$session" -x 220 -y 50 -- "$launch"
  else
    # Spoke: write launch + prime files locally, ship them over, then start.
    write_launch_script "$launch" "$model_type" "$cli_argv"
    scp -q "$launch" "$prompt_file" "muster-remote:/tmp/"
    ssh muster-remote \
      "export PATH=\"\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH\"; \
       chmod +x '$launch'; \
       tmux has-session -t '$session' 2>/dev/null && tmux kill-session -t '$session' || true; \
       tmux has-session -t '$legacy_session' 2>/dev/null && tmux kill-session -t '$legacy_session' || true; \
       tmux new-session -d -s '$session' -x 220 -y 50 -- '$launch'"
  fi
  echo "spawned: alias=${ALIAS} cli=${CLI} machine=${MACHINE} mode=tmux-tui tmux-session=${session} permissions=bypass"
}

spawn_hub_grok_loop() {
  # Workaround for the npm grok-cli interactive-mode bug (see header
  # comment): a persistent headless polling loop instead of a real TUI.
  #
  # CRITICAL: every `grok -p` invocation MUST be hard-timeout-killed.
  # Without a timeout, npm grok-cli can hang forever after the first
  # register_agent call (observed: loops stuck 90+ minutes on the initial
  # register, never reaching the drain cycle). That is why the fleet
  # "wasn't picking up tasks" — workers were zombie-stuck, not idle.
  #
  # Use the EXPLICIT npm-build binary path, not bare "grok" + PATH order.
  GROK_BIN="$HOME/.npm-global/bin/grok"
  if [ ! -x "$GROK_BIN" ]; then
    echo "ERROR: npm grok-cli not found at $GROK_BIN" >&2
    exit 1
  fi
  loop_script="/tmp/muster-loop-${ALIAS}.sh"
  # Kill any previous loop + orphaned grok for this alias
  if [ -f "/tmp/muster-loop-${ALIAS}.pid" ]; then
    oldpid=$(cat "/tmp/muster-loop-${ALIAS}.pid" 2>/dev/null || true)
    if [ -n "${oldpid:-}" ]; then
      kill "$oldpid" 2>/dev/null || true
      # kill process group children
      pkill -P "$oldpid" 2>/dev/null || true
    fi
  fi
  pkill -f "muster-loop-${ALIAS}" 2>/dev/null || true
  pkill -f "alias=${ALIAS}, role=${ROLE}" 2>/dev/null || true

  # Write loop with a quoted heredoc so shell metacharacters ($@, $1, etc.)
  # are NOT expanded by THIS shell at write time (that bug produced `"" &`
  # and every cycle died with ": command not found").
  #
  # SECOND CRITICAL BUG: npm grok-cli `-p` often does NOT exit after the model
  # says "idle" / finishes tools — the node process sits forever. A plain
  # hard-timeout of 180s means workers only re-poll every ~3 minutes after
  # finishing, so the fleet looks dead and only the operator agent works.
  # Fix: hard-timeout + EARLY-KILL when the cycle log shows the done marker
  # or a terminal "idle" assistant message after this cycle's byte offset.
  cat > "$loop_script" <<'LOOP_EOF'
#!/bin/sh
# Headless muster worker. Args injected below as env.
set -u
cd "$HOME" || exit 1
# GROK_BIN / ALIAS / ROLE exported by wrapper footer
LOG="/tmp/muster-loop-${ALIAS}.log"
CYCLE=0
# Max wall time per drain cycle (safety net only; early-kill is the real exit).
MAX_SECS=120
# Seconds between re-polls when idle.
IDLE_SLEEP=8

# Kill pid tree (node + any children).
kill_tree() {
  _root="$1"
  if [ -z "${_root:-}" ]; then
    return 0
  fi
  # children first
  for _c in $(pgrep -P "$_root" 2>/dev/null); do
    kill_tree "$_c"
  done
  kill "$_root" 2>/dev/null || true
  sleep 1
  kill -9 "$_root" 2>/dev/null || true
}

# Run command with hard timeout + early kill when cycle finishes.
# npm grok -p hangs after "idle"; we detect completion via log markers.
run_drain_cycle() {
  _secs="$1"; shift
  _start_bytes=0
  if [ -f "$LOG" ]; then
    _start_bytes=$(wc -c < "$LOG" | tr -d ' ')
  fi
  _marker="FLEET_CYCLE_${CYCLE}_DONE"

  "$@" &
  _pid=$!
  (
    _i=0
    while [ "$_i" -lt "$_secs" ]; do
      sleep 2
      _i=$((_i + 2))
      if ! kill -0 "$_pid" 2>/dev/null; then
        exit 0
      fi
      # Only inspect bytes written this cycle
      if [ -f "$LOG" ]; then
        _chunk=$(tail -c +"$((_start_bytes + 1))" "$LOG" 2>/dev/null || true)
        # Explicit done marker from the prompt, or terminal idle JSON, or
        # task_transition completed as last meaningful action + idle.
        case "$_chunk" in
          *"$_marker"*|*'{"role":"assistant","content":"idle"}'*|*'{"role":"assistant","content":"idle\n"}'*)
            sleep 1
            kill "$_pid" 2>/dev/null || true
            sleep 1
            kill -9 "$_pid" 2>/dev/null || true
            exit 0
            ;;
        esac
        # Also match multi-line idle content variants
        if printf '%s' "$_chunk" | grep -q "\"content\":\"idle\"" 2>/dev/null; then
          sleep 1
          kill "$_pid" 2>/dev/null || true
          sleep 1
          kill -9 "$_pid" 2>/dev/null || true
          exit 0
        fi
      fi
    done
    kill "$_pid" 2>/dev/null || true
    sleep 2
    kill -9 "$_pid" 2>/dev/null || true
  ) &
  _watch=$!
  wait "$_pid" 2>/dev/null
  _rc=$?
  kill "$_watch" 2>/dev/null || true
  wait "$_watch" 2>/dev/null
  # Ensure no orphan node left behind
  kill_tree "$_pid" 2>/dev/null || true
  return $_rc
}

echo "[$(date -Iseconds 2>/dev/null || date)] loop start alias=$ALIAS bin=$GROK_BIN early-kill=1 max=${MAX_SECS}s" >>"$LOG"

while true; do
  CYCLE=$((CYCLE + 1))
  echo "[$(date -Iseconds 2>/dev/null || date)] cycle=$CYCLE begin" >>"$LOG"
  _marker="FLEET_CYCLE_${CYCLE}_DONE"

  PROMPT="You are muster worker alias=${ALIAS} role=${ROLE} model_type=grok.
1) Call register_agent (or mcp__muster__register_agent) with alias=${ALIAS}, role=${ROLE}, model_type=grok.
2) Call get_inbox for alias ${ALIAS}.
3) For EVERY thread that is kind=task with status open or claimed addressed to you: claim if open, read full body with get_thread, DO THE WORK with tools, reply on the thread, task_transition to completed (or needs_info/blocked with a note).
4) For unread message threads: get_thread, handle if action/reply requested, reply.
5) When fully done (no open/claimed tasks left for you): print exactly these two lines and STOP:
idle
${_marker}
Do not hang. Do not keep calling get_inbox in a loop. One drain pass then stop."

  if [ ! -x "$GROK_BIN" ]; then
    echo "GROK_BIN missing: $GROK_BIN" >>"$LOG"
    sleep 30
    continue
  fi

  run_drain_cycle "$MAX_SECS" "$GROK_BIN" -p "$PROMPT" >>"$LOG" 2>&1
  _rc=$?
  echo "[$(date -Iseconds 2>/dev/null || date)] cycle=$CYCLE end rc=$_rc" >>"$LOG"

  if [ -f "$LOG" ]; then
    _lines=$(wc -l < "$LOG" | tr -d ' ')
    if [ "$_lines" -gt 4000 ]; then
      tail -n 1500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
  fi

  sleep "$IDLE_SLEEP"
done
LOOP_EOF

  # Prepend env exports so the quoted body can use ALIAS/ROLE/GROK_BIN
  {
    echo "#!/bin/sh"
    echo "export GROK_BIN=\"$GROK_BIN\""
    echo "export ALIAS=\"$ALIAS\""
    echo "export ROLE=\"$ROLE\""
    # skip the first shebang line of the body
    tail -n +2 "$loop_script"
  } > "${loop_script}.new"
  mv "${loop_script}.new" "$loop_script"
  chmod +x "$loop_script"
  nohup sh "$loop_script" > "/tmp/muster-loop-${ALIAS}-nohup.log" 2>&1 < /dev/null &
  echo $! > "/tmp/muster-loop-${ALIAS}.pid"
  disown 2>/dev/null || true
  echo "spawned: alias=${ALIAS} cli=${CLI} machine=${MACHINE} mode=headless-loop (early-kill+120s max) pid=$(cat /tmp/muster-loop-${ALIAS}.pid)"
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
    # BOTH flags required:
    #   --dangerously-skip-permissions  bypass tool/MCP permission checks
    #   --permission-mode bypassPermissions  start out of "manual mode"
    #     (the footer "manual mode on" toggle still forces prompts even
    #     when skip-permissions was set at launch if mode is manual)
    # PLUS pre-accept workspace trust for $HOME so the "Is this a project
    # you trust?" dialog never blocks the TUI on first launch.
    CLAUDE_BIN="$(command -v claude || true)"
    if [ -z "$CLAUDE_BIN" ]; then
      for c in "$HOME/.local/bin/claude" /usr/local/bin/claude; do
        [ -x "$c" ] && CLAUDE_BIN="$c" && break
      done
    fi
    if [ -z "$CLAUDE_BIN" ]; then
      echo "ERROR: claude binary not found" >&2
      exit 1
    fi
    ensure_claude_workspace_trust
    spawn_tmux_tui \
      "$CLAUDE_BIN --dangerously-skip-permissions --permission-mode bypassPermissions" \
      "claude"
    ;;
  grok)
    if [ "$MACHINE" = "hub" ]; then
      spawn_hub_grok_loop
    else
      # Prefer native/Rust grok on the spoke (PATH set on remote in spawn).
      spawn_tmux_tui "grok --permission-mode bypassPermissions" "grok"
    fi
    ;;
  *)
    echo "unknown CLI '$CLI' — must be 'claude' or 'grok'" >&2
    exit 1
    ;;
esac
