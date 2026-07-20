# Muster Distributed-Systems Arcade

**Why this exists:** Stamp proofs show the bus works — skeptics still say
“job queue.” These demos are **canonical multi-agent phenomena** that are
**meaningless with one participant**.

Brainstorm sources: `docs/brainstorm/` (Opus max, Sonnet high, Grok-4).

---

## Quick operator entry

```bash
# Fleet ready?
fleet/fleet-status.sh          # or: fleet-status after install

# Run the full suite (needs live hub workers)
sh fleet/showcase/run_all.sh

# Dashboard
open http://localhost:8787/    # Showcase tab
curl -s localhost:8787/api/showcase | python3 -m json.tool | head
```

---

## Demos (all under `fleet/showcase/`)

| Demo | Command | Fails with 1 agent? |
|------|---------|---------------------|
| **Bounty race** | `python3 fleet/showcase/bounty_race.py -n 3 --wait` | No race / one claimer |
| **Swarm** | `python3 fleet/showcase/swarm.py -n 12 --wait` | Serial for-loop |
| **Quorum** | `python3 fleet/showcase/quorum.py propose -k 2 -n 3` | Cannot form multi-voter quorum |
| **Barrier** | `python3 fleet/showcase/barrier.py --alias X -n 3 --gen g1` | Hang forever if anyone missing |
| **Leader lease** | `python3 fleet/showcase/leader_lease.py --alias X` | Uncontested crown |
| **Failover** | `sh fleet/showcase/failover_demo.sh` | No second node to take crown |
| **Ghost Hands relay** | `sh fleet/showcase/relay.sh start` then `verify` | No spoke hop |
| **Run all** | `sh fleet/showcase/run_all.sh` | — |

### 1. Bounty race — atomic claim showdown

N identical tasks to `role:worker`. First `task_claim` wins each.

**Proof (live):** 3 bounties → claimers `grok-hub-a`, `grok-hub-b`, `grok-hub-c`.

### 2. Competitive swarm

Dump many tasks; leaderboard shows work-stealing distribution.

### 3. Quorum commit

K-of-N votes in `kv`; below K → ABORT. COORD writes `quorum.<id>.status`.

### 4. Fleet barrier

Per-agent markers `barrier.<gen>.by.<alias>`; release when count ≥ N.

### 5. Leader lease + failover

`kv leader.*` lease; election via claimable term tasks.
`failover_demo.sh` starts two electors, kills leader, shows crown move.

### 6. Ghost Hands relay

`relay.sh start` creates hop1 (hub) → hop2 (spoke) → hop3 (hub).
`relay.sh verify` checks stamps + hop2 **not** on hub disk.

---

## Dashboard

- Tab **Showcase** → polls `GET /api/showcase`
- Fields: `leader`, `bounties`, `swarms`, `quorums`, `barrier`

---

## Fleet health

```bash
fleet/fleet-status.sh
# loops a–d, nudge, :8787, :8790, llm mode, hub/spoke tmux, tunnel sock, smoke
```

`fleet-restart-hub-workers` restarts **a–d** + nudge.

---

## Backlog (stretch)

Byzantine vote, dining philosophers, voice→router auction, split-brain tunnel cut.
