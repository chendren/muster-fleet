## muster bus coordination

This session may have muster MCP tools available (register_agent,
send_message, get_inbox, get_thread, reply, list_agents, task_create,
task_claim, task_transition, kv_set, kv_get). If it does, you are a peer on
the muster bus - a coordination channel other coding-agent sessions
(Claude Code, Codex, other Grok sessions, on this machine or another one on
the local network) use to hand you messages and tasks, and to receive them
from you.

Grok CLI has no session-lifecycle hooks, so registration and inbox-draining
here are manual, not automatic:

- At the start of a session, call register_agent(alias, role, "grok") once.
  Default alias is your tmux session name. Check with list_agents first if
  unsure whether the muster tools are present.
- Periodically, and whenever you are about to end a turn, call get_inbox()
  to check for unread mail. If there are unread threads, call get_thread(id)
  on each, handle the request, and reply - do not wait to be asked.
- Addressing: alias (globally unique), label (resolved within your
  project), or proj:label (cross-project). Use a task when someone must act
  on something with a lifecycle; use a message for FYI or discussion. Reply
  on the thread you were addressed on. Be concise.

Append this block to `~/.grok/GROK.md` (npm `@vibe-kit/grok-cli`) or
`~/.grok/AGENTS.md` (native/Rust `grok`) — don't overwrite the file if you
already have other global instructions there.
