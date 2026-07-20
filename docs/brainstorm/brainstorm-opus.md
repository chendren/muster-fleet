# Brainstorm — brainstorm-opus

**Framing (the pitch the docs are missing):** Today's proof (`docs/PROOF.md`) is a *static fan‑out/fan‑in* — a producer hands one task to each worker and they stamp a file. Impressive that it's cross‑machine, but a skeptic shrugs: "that's just a job queue with SSH." The way to make someone say *"holy shit, coordination is real"* is to run **canonical distributed‑systems phenomena** on top of the fleet — election, agreement, mutual exclusion, load‑stealing, barriers — the exact things that are **mathematically meaningless with one participant**. Muster already ships every primitive needed: `task_claim` is a genuine atomic first‑writer‑wins mutex across two Macs (41 real claims in the journal), `kv` is a shared blackboard (`key,value,updated_by,updated_at`), and the dashboard is a live read‑only mirror of `bus.db` + `kv` with a clean "add an `/api/*` branch + clone `renderMesh()`" extension path. Call the collection **the Muster Distributed‑Systems Arcade.** Every demo below dies on contact with a single agent.

---

## Top 5 showcase ideas (ranked)

### 1. muster‑raft — live leader election with kill‑to‑failover
- **Muster primitives used:** `task_claim` (the election — atomic first‑writer‑wins on an "term N" task), `kv_set`/`kv_get` (`leader.holder`, `leader.term`, `leader.lease_until` lease), `send_message --broadcast` (heartbeat / "I am term N leader"), `task_transition` + `events(kind=claim)` (auto‑renders in the Collaboration sequence view).
- **Why it demos power:** A crown appears on one agent; you close its terminal on the dashboard's own terminal wall and within one lease (~15 s) the crown **jumps to a different physical Mac** as the survivors race a new election — you are watching consensus heal in real time. With one agent it's a farce: the sole node is leader forever, there is no election and nothing to fail over to. Uncontested leadership isn't a demo.
- **MVP implementable in <2 hours on this repo:** New `fleet/consensus/raftlite.py` (~130 lines) each participant runs in its pane: loop → read `kv leader.lease_until`; if expired, `task_claim` an `election term=N+1` task (only one wins, by construction), winner `kv_set leader.holder=self / lease_until=now+15s` and broadcasts. Leader renews each cycle. Dashboard: add `build_consensus()` (mirror `build_mesh()` at server.py:115) reading the three `kv` keys + last N `claim`/`transition` events, wire `if path == "/api/consensus"` beside `/api/mesh` (server.py:1030). Frontend: add `<button data-view="consensus">` (index.html:1997) + `renderConsensus()` cloned from `renderMesh()` (index.html:4127) — a crown badge, term counter, lease countdown bar, election log; runs on the existing 8 s "fleet extras" poll. Write path is confirmed via muster MCP (`task_claim`/`kv_set`); headless workers can use a thin `~/.local/share/muster/sock` client (the one integration risk — fallback is `muster debug` raw ops or driving from an operator agent).
- **Demo script:**
  - `python3 fleet/consensus/raftlite.py` in ≥3 panes across both Macs (hub Claude + 2 spoke Grok), open dashboard → **Consensus** tab.
  - Watch a leader get elected; note the crown alias + machine and the lease countdown ticking.
  - On the **Terminals** wall, open the leader's card, then `tmux kill-session -t <leader>` (or Ctrl‑C its loop).
  - Watch the lease expire, the survivors fire an `election term=N+1`, and the crown reappear **on the other machine** — the sequence view shows the claim race.
  - Kill it again to show repeatability; bring the dead node back and watch it rejoin as a follower, not steal the crown.

### 2. Quorum commit that physically cannot commit below quorum
- **Muster primitives used:** `task_create` to `--role replica` (prepare), `reply`/`kv_set` (per‑replica votes), `kv_get` (tally), `task_transition` (`completed`=commit / `declined`=abort), `send_message --broadcast` (proposal + decision).
- **Why it demos power:** A coordinator proposes a value and needs **K‑of‑N** replicas to vote yes before it commits; kill replicas until you drop below K and the commit bar visibly stalls under the quorum line and flips to **ABORT** — the fleet refuses to lie about durability. One agent can never form a quorum of its peers, so the safety property it's demonstrating doesn't exist.
- **MVP implementable in <2 hours on this repo:** `fleet/consensus/quorum.py` — coordinator role + replica role in one file (~120 lines). Replicas watch their inbox for a `prepare` task, `kv_set vote.<term>.<alias>=yes`, coordinator polls `kv` for ≥K yes then commits `kv_set commit.value=…` and `task_transition completed`. Dashboard: `build_quorum()` reads `vote.*` + `commit.*` keys; `/api/quorum` branch (beside server.py:1030); `renderQuorum()` cloned from `renderRequests()` (index.html:4188) drawing N replica lights, a fill‑to‑quorum bar with a threshold line, and a big COMMIT/ABORT stamp.
- **Demo script:**
  - Start 1 coordinator + 4 replicas across both Macs; open the **Quorum** tab (K=3).
  - Fire a proposal; watch votes stream in and the bar cross the line → **COMMIT** with the value written to `kv commit.value`.
  - Kill 2 replicas, propose again; watch the bar stall at 2/3, hang, then flash **ABORT** — no value written.
  - Revive one replica, re‑propose; quorum returns and it commits again.
  - Point at the Collaboration sequence view: the vote arrows and the transition are all real bus traffic, not a mock.

### 3. Dining philosophers — `task_claim` as a cross‑machine lock you can watch deadlock
- **Muster primitives used:** `task_claim` (each "fork" is a claimable lock; exactly one holder, enforced by the bus), `task_transition cancelled`/re‑`open` (release), `kv_set` (philosopher state: thinking/hungry/eating), `events(kind=claim)`.
- **Why it demos power:** Put 5 forks and 5 hungry agents in a ring across two Macs; each needs two adjacent forks to eat. Naïve grab‑order **deadlocks live** — every agent holds one fork, waits forever for its neighbor's — and you watch the ring freeze, then break it by switching on ordered acquisition. It's the most direct possible proof that `task_claim` is a true mutex spanning machines: a single agent trivially grabs every fork, there is zero contention, and the entire phenomenon (the reason locks exist) vanishes.
- **MVP implementable in <2 hours on this repo:** Model forks as five long‑lived tasks `fork-0..4` (or `kv fork.N.holder`); `fleet/consensus/philosophers.py` (~110 lines): think → try `task_claim` left then right → if both, eat 3 s → release. Dashboard: `build_forks()` returns the fork ring + holders + a deadlock flag (all 5 held, all hungry); `/api/forks` branch; `renderForks()` (clone `renderMesh()`) draws a pentagon of forks colored free/held/contested with a red DEADLOCK banner. Toggle a `?policy=ordered` query the script reads from `kv` to show the fix live.
- **Demo script:**
  - Launch 5 philosophers across hub+spoke in naïve mode; open the **Forks** tab.
  - Watch forks flip held/free as agents eat, contention rising as they get hungry.
  - It seizes: all five hold one fork, DEADLOCK banner lights, eating count = 0 — the fleet is frozen and the sequence view shows claims with no completions.
  - `kv_set policy=ordered` (or hit the toggle); watch the deadlock break and eating resume within seconds.
  - Kill one philosopher mid‑deadlock to show even partial liveness returns — contention is real, not scripted.

### 4. Competitive work‑stealing swarm with kill‑and‑heal
- **Muster primitives used:** `task_create` (dump N tasks to `--role worker`), `task_claim` (competitive, first‑wins = zero central assignment), `task_transition completed`, `events(kind=claim/transition)`, `kv` throughput counters.
- **Why it demos power:** Dump 60 tasks at a role and every worker on both Macs races to claim — work distributes itself with **no scheduler**; then kill half the swarm mid‑drain and the survivors absorb the backlog, throughput dips and recovers on a live chart. With one agent there's no race, no stealing, no resilience — it degenerates into a serial for‑loop and every property worth showing (parallelism, self‑balancing, fault tolerance) is invisible.
- **MVP implementable in <2 hours on this repo:** Reuses the *already‑proven* claim mechanic (journal shows claim/transition working). `fleet/swarm/worker.py` (~70 lines): loop → claim any open `role:worker` task → sleep(rand) → complete → `kv` increment `claims.<alias>`. Producer one‑liner dumps tasks via `mcp__muster__task_create`. Dashboard: `build_swarm()` aggregates claims‑per‑agent + completions/sec from `events`; `/api/swarm` branch; `renderSwarm()` (clone `renderRequests()`) = a live leaderboard bar chart + a throughput sparkline + remaining‑queue depth. Lowest‑risk build of the five.
- **Demo script:**
  - Start 6 workers across both machines; open the **Swarm** tab.
  - Dump 60 tasks to `role:worker`; watch the leaderboard fill and queue depth drain in parallel across hubs and spokes.
  - Mid‑drain, `tmux kill-session` half the workers; watch throughput dip, then the survivors' bars accelerate to absorb the backlog.
  - Queue still hits zero — no task dropped, no coordinator reassigned anything.
  - Contrast: stop all but one worker and re‑dump — the leaderboard flatlines to a single serial bar, proving the parallelism was real.

### 5. Fleet barrier — the synchronized salute that hangs forever if anyone is missing
- **Muster primitives used:** `kv_set`/`kv_get` (barrier counter `barrier.<gen>.arrived` + `barrier.<gen>.N`), `send_message --broadcast` (release signal), `register_agent`/`list_agents` (the expected roster).
- **Why it demos power:** Every agent arrives at the barrier, increments the counter, and **blocks** until arrivals == N — then all panes flip in the same instant (print a banner / change title). It's the purest single‑agent‑impossible primitive: leave one agent offline and the barrier **never releases** — the entire fleet sits visibly waiting on the one that isn't there, which is itself the most legible possible proof that they're genuinely waiting on *each other*.
- **MVP implementable in <2 hours on this repo:** Cheapest of the five. `fleet/consensus/barrier.py` (~60 lines): `kv` compare‑and‑increment arrived, spin on `kv_get` until arrived==N, then act. Dashboard: `build_barrier()` returns arrived/N + per‑agent arrived flags; `/api/barrier` branch; `renderBarrier()` = N dots filling + a countdown "waiting on: <aliases>" line that names the stragglers. The lockstep flip is best watched on the existing **Terminals** wall (`renderTerminalWall`, index.html:3058) — all panes change together.
- **Demo script:**
  - Start N‑1 of N agents at the barrier; open the **Barrier** tab and the Terminals wall side by side.
  - Watch the dots fill to N‑1 and stall — "waiting on: grok‑spoke‑b" — every pane frozen at the barrier.
  - Start the last agent; the moment it arrives, **every pane on both Macs flips in the same tick**.
  - Re‑run, but never start the last agent — show the fleet hanging indefinitely; the demo's failure *is* the proof.
  - Bump N to include a dead alias to show the barrier correctly refuses to release on a phantom.

---

## One "hero" idea to ship tonight

**#1 muster‑raft — leader election with kill‑to‑failover.** It's the single most iconic distributed‑systems image (a leader dies, a new one rises) and it maps perfectly onto what this codebase already has: the dashboard's **terminal wall is the murder weapon** (open the leader's card, kill its session) and the **`kv` blackboard + `events` journal are the read model** the new Consensus tab renders — no new services, no cloud, entirely local. It fails hardest with one agent (uncontested leader = no demo), and the payoff — *the crown physically relocating from the Mac Studio to the MacBook Pro in ~15 seconds because you closed a terminal* — is the exact visceral moment that reframes muster from "task bus" to "a substrate real distributed systems run on."

**Tonight's build order (≈90 min, de‑risked):** (1) `fleet/consensus/raftlite.py` with the lease/election loop driving `kv` via muster MCP — get failover working headless first, verify with `muster events` + `sqlite3 bus.db "select * from kv where key like 'leader%'"`. (2) `build_consensus()` + `/api/consensus` in `server.py` (paste‑adjacent to `build_mesh`/`/api/mesh`). (3) `renderConsensus()` + Consensus tab in `index.html` (clone `renderMesh()`, 8 s poll). Ship in that order so even if the UI runs long, the failover itself is demonstrable from the CLI. **Fast fallback if time collapses:** ship **#4 swarm** instead — it reuses the already‑working claim path and the leaderboard is a one‑file `renderRequests()` clone.

---

## Stretch ideas

1. **Split‑brain chaos drill (partition + heal).** Layer on the hero: with raft or quorum running, sever the spoke's SSH socket tunnel (`~/Library/LaunchAgents/tools.muster.tunnel.plist`) to partition the fleet along its real network seam. The hub side re‑elects; the spoke side goes stale/leaderless; the dashboard visibly splits into two clusters. Reconnect and watch terms reconcile and the fleet heal. Multi‑session, multi‑machine, and it dramatizes the CAP tradeoff on actual hardware — impossible to even stage without ≥2 machines.

2. **Byzantine agreement — spot the traitor.** Run quorum (#2) with one agent deliberately `kv_set`‑ing a *disagreeing* value or double‑voting. Honest majority still commits the correct value; the dashboard cross‑checks votes and highlights the liar in red. Uses the Claude+Grok heterogeneity for real (different model families, one instructed to defect) and shows fault‑tolerant consensus, not just consensus — a genuine "the system is robust to a bad actor" moment.

3. **Cross‑model relay pipeline with a shared AgentCore session (Grok→Claude→Grok).** Chain a task across models where each stage reads/writes the same `:8790` AgentCore session memory (`PUT/GET /sessions/{id}/memory`, proxied via `/api/agentcore/invoke`) and hands off on the bus: Grok drafts → Claude critiques → Grok revises, with the shared session as the only carrier of state. The sequence view shows the baton crossing model families and machines; kill the middle stage and the pipeline stalls at exactly that lifeline — proving the handoff is real state transfer, not one agent talking to itself.
