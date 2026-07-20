# Brainstorm — brainstorm-sonnet

## Top 5 showcase ideas (ranked)

### 1. Bounty Race — atomic claim showdown

- **Muster primitives used:** `task_create` (broadcast to role), `task_claim` (atomic — first wins), `task_transition`, dashboard task board FLIP animation.
- **Why it demos power:** A single task broadcast to a role gets pounced on by every live worker simultaneously; only one `task_claim` call succeeds and every other agent's identical call *errors on the wire in real time*. That's not a scripted handoff — it's a live race condition resolved by the daemon, visible as one dashboard card snapping from `open` to `claimed` while rival panes print a rejected claim.
- **MVP implementable in <2 hours on this repo:** `mcp__muster__task_create` from `station` or a script, `to_kind: role`, `to_target: worker`. Existing FLIP animation in `dashboard/frontend/index.html` (task board) already renders claim transitions — just need a repeatable trigger script `fleet/acceptance/bounty.sh` that creates N identical bounty tasks in a loop and tails `muster events`/`muster watch` to show timestamps a few ms apart.
- **Demo script:**
  - Start 3+ live workers across hub and spoke (`fleet-restart-hub-workers`, `muster-spawn-tui`).
  - Open dashboard `:8787` Fleet + task board view, and `muster watch` in a terminal side-by-side.
  - Fire one `task_create --to_kind role --to_target worker` addressed to the whole worker role.
  - Watch `muster watch` show 3+ near-simultaneous `task_claim` attempts, only one `claimed`.
  - Point at the dashboard card snapping state, then rerun with only one worker alive — the "race" disappears, it's just... one claim, no drama. That contrast *is* the pitch.

### 2. Sealed-Bid Auction Router

- **Muster primitives used:** `send_message` (broadcast, `intent: reply-requested`), `reply` (bids on the thread), `kv_set`/`kv_get` (scratchpad for bid tally), `task_create` (direct award to winner).
- **Why it demos power:** Instead of a human or a hardcoded heuristic assigning work, the fleet *negotiates* — a goal is broadcast, independent agents on different machines and different model backends (Claude + Grok) each reply with a self-rated confidence/cost bid, and a synthesizer reads the thread and awards the task to the best bid. It looks like a market, not a queue.
- **MVP implementable in <2 hours on this repo:** Extend `fleet/router/router.py` (already does worker selection by role/machine heuristics) with a `--auction` mode: broadcast via `send_message(to_kind=role, intent=reply-requested)`, poll `get_thread` for replies within a timeout window, parse bid replies, `task_create` directly to the winning alias. `POST /api/router/route` in `dashboard/server.py` is the existing hook point to add the mode.
- **Demo script:**
  - Ensure 2+ heterogeneous workers online (one Claude, one Grok, ideally cross-machine).
  - `curl -X POST :8787/api/router/route -d '{"goal":"...", "mode":"auction"}'`.
  - Watch `muster station` thread view fill with bid replies from each worker in near real time.
  - Show `GET /api/router/requests` recording which bid won and why.
  - Kill all workers but one and rerun — the "auction" is just one silent bid with no competition, proving the negotiation needs a crowd.

### 3. Ghost Hands — cross-machine relay, one command

- **Muster primitives used:** `task_create` chained hub→spoke→hub, `muster nudge` (the only send-keys path), `get_thread` to trace the hop chain, `GET /api/mesh` for the machine→session→thread graph.
- **Why it demos power:** This isn't a mockup — `docs/PROOF.md` already shows this working live (today's proof run, `proof-20260719-233519`, stamp files that provably exist on one machine's disk and not the other's). Turning that ad hoc proof into a one-command, repeatable relay with a live-animated mesh graph makes "two physical Macs are actually coordinating" a 30-second visual instead of a README table someone has to trust.
- **MVP implementable in <2 hours on this repo:** New script `fleet/acceptance/relay.sh` that: `task_create`s a chain of tasks (hub-tui-claude → grok-spoke-a → grok-hub-b → back to origin), each task body says "stamp `/tmp/fleet-relay-<id>-<hop>.txt` with hostname+timestamp, then `task_create` the next hop." Add a thin poll in `dashboard/server.py`'s `/api/mesh` response (or reuse it as-is) and a CSS pulse on the frontend edge that lit up last, keyed off `get_thread` timestamps.
- **Demo script:**
  - `fleet/acceptance/relay.sh start` — creates the chained task on the hub.
  - Split terminal: hub pane, spoke pane (over `ssh muster-remote`), dashboard mesh view.
  - Watch the task visibly jump machines — each pane executes and stamps a file the *other* machine cannot see.
  - `ssh muster-remote cat /tmp/fleet-relay-*.txt` proves the spoke-only stamps exist remotely, not locally.
  - Pull the SSH tunnel (`launchctl unload tools.muster.tunnel`) and rerun — the relay dies at the hub→spoke hop, loudly, in `muster watch`, instead of silently completing on one machine.

### 4. Chaos Kill — self-healing swarm

- **Muster primitives used:** `task_claim` (orphaned task recovery), `muster gc` (reaps dead agents), `list_agents`, dashboard "live vs departed" agent cards, `fleet-nudge-tui.sh`.
- **Why it demos power:** Kill the tmux session of a worker mid-task. Nothing coordinates the recovery — no supervisor process "reassigns" the task. It just sits `claimed` by a now-dead agent until `muster gc` tombstones them, at which point it's addressable again and a *different* live agent picks it up and finishes it. Watching a task survive its original owner's death is the single most convincing "this isn't a script, it's a system" moment.
- **MVP implementable in <2 hours on this repo:** No new code — this is entirely composable from existing primitives. Script `fleet/acceptance/chaos.sh`: `task_create` a real task with a filesystem-verifiable outcome, `tmux kill-session -t <victim>` after it claims but before it stamps, `muster gc`, then either re-`task_create` the same body addressed to role (simplest) or watch `fleet-nudge-tui` naturally route to a survivor next cycle.
- **Demo script:**
  - Assign a task to a specific worker, confirm `claimed` in the dashboard.
  - `tmux kill-session -t <that worker>` mid-flight — dashboard card greys out (departed/tombstoned), task stays stuck `claimed`.
  - Run `muster gc`, re-broadcast the task to the role.
  - A different live worker claims and completes it — stamp file has a *different* alias than the one that first claimed it.
  - Do it again with exactly one worker running: the task just... dies. No recovery. Nobody left to catch it.

### 5. Parallel Incident War Room

- **Muster primitives used:** `send_message`(broadcast, `intent: action-requested`) to trigger triage, `reply` (each agent posts findings to the same thread), `get_thread` (synthesizer reads all replies), `kv_set` (shared incident scratchpad), `muster station`/dashboard Collab view.
- **Why it demos power:** Drop a synthetic broken build (bad test + a stack trace + a misleading log line) and broadcast "prod incident, triage now" to the whole fleet. Instead of one agent doing serial diagnosis, 3+ agents across machines independently read different angles (logs, code diff, user-facing comms) *in parallel* and post to the same thread — then a synthesizer stitches the replies into a coherent postmortem live, visible scrolling in the dashboard's Collab view. It reads like a real incident channel, not a demo script.
- **MVP implementable in <2 hours on this repo:** Plant a broken fixture (e.g. a failing test in a scratch dir + a decoy log file), `send_message(to_kind=role, to_target=worker, intent=action-requested)` with the incident brief and explicit sub-roles ("logs", "code", "comms") baked into the body per-agent via 3 separate `send_message` calls. No new backend code — the Collab view in `dashboard/frontend/index.html` already renders thread sequences; just need the trigger script.
- **Demo script:**
  - Plant the broken fixture and a `send_message` broadcast describing the incident.
  - Open dashboard Collab view, watch replies land from different aliases within seconds of each other.
  - `get_thread` (or `muster station`) shows logs-agent, code-agent, comms-agent replies interleaved, not sequential.
  - A designated synthesizer agent posts the final postmortem as a `reply` on the same thread.
  - Rerun solo — one agent trying to cover logs+code+comms serially takes visibly longer and reads like one voice, not a room.

## One "hero" idea to ship tonight

**#3, Ghost Hands cross-machine relay**, because the fleet already proved it works *today* (`docs/PROOF.md`, `proof-20260719-233519`, timestamped hours ago) — this is the lowest-risk, highest-credibility pick since it's turning a real, already-verified capability into a repeatable one-command show instead of building something new and hoping it works live. It needs no new services, no LLM calls beyond what workers already do, and it fails in the most self-evidently correct way possible: pull the SSH tunnel and the relay visibly dies at the hub→spoke boundary in `muster watch`, which is exactly the "only fails with multi-machine" property the brief asked for. Build `fleet/acceptance/relay.sh`, wire a pulse animation onto `/api/mesh` edges in `dashboard/frontend/index.html`, and the demo is: run script, watch two Macs light up, `ssh` in to prove the spoke-only stamp file, pull the tunnel, watch it break.

## Stretch ideas

- **Voice-to-fleet goal routing.** Hold-to-talk into the Computer panel ("fix the flaky auth test and tell me when it's green"), STT → `router.py` in auction or heuristic mode → cross-machine `task_create` → TTS reads back the completing agent's reply. End-to-end: a spoken sentence becomes work done on a machine you never touched, narrated back in a voice. Ties together `docs/VOICE.md`, `docs/ROUTER.md`, and the bus in one flow — the single most "sci-fi" demo available, but riskiest tonight (STT/TTS model load, multi-machine, and NLU-to-goal parsing all have to work live).
- **Adversarial cross-model code review tournament.** Claude and Grok workers on opposite machines each independently review the *same* diff via `task_create`, post structured findings as `reply`s on one thread, then a third agent scores agreement/disagreement and flags where the two model families caught different bugs. Demonstrates that heterogeneous model backends on the bus aren't redundant — they're diverse reviewers, and the disagreements are the interesting output.
- **Manual failover drill.** The README states plainly that hub loss is a single point of failure ("no HA"). Deliberately kill `muster serve` on the hub, promote the spoke to a fresh local daemon, re-register survivors against it, and measure how much bus history (and in-flight tasks) is unrecoverable. This is the most ambitious/multi-session demo — not a "look what works" showcase but a "here's exactly where the architecture's promise ends," which is its own kind of credibility.
