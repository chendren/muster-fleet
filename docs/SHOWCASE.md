# Muster Distributed-Systems Arcade

**Why this exists:** Static fan-out proofs (one task per agent, stamp a file)
prove the bus works — but a skeptic can still say “that’s a job queue.” The
demos below are **canonical multi-agent phenomena**: they are **mathematically
meaningless with one participant**. That is the pitch muster docs were missing.

Synthesized from three top-model brainstorm agents:

| Agent | Model | Output |
|-------|--------|--------|
| `brainstorm-opus` | Claude Opus (`--effort max`) | `docs/brainstorm/brainstorm-opus.md` |
| `brainstorm-sonnet` | Claude Sonnet (`--effort high`) | `docs/brainstorm/brainstorm-sonnet.md` |
| `brainstorm-grok` | Grok-4 (re-run / partial) | `docs/brainstorm/` |

Opus framed the collection as the **Muster Distributed-Systems Arcade**.
Sonnet ranked **Bounty Race** (atomic claim) as the purest instant demo.

---

## Shipped tonight (runnable)

### 1. Bounty Race — atomic claim showdown

Multiple identical tasks hit `role:worker`. Every live worker tries
`task_claim`; **exactly one wins per bounty**. Multi-claimer leaderboard
proves a real race.

```bash
# Fleet must be draining (hub loops + TUI nudge)
python3 fleet/showcase/bounty_race.py -n 3 --wait
# → ~/.local/share/muster-fleet/showcase/bounty-*.json
curl -s localhost:8787/api/showcase | python3 -m json.tool | head -80
```

**One agent?** One claimer, no drama. **Many agents?** Distinct claimers on
the leaderboard — that *is* the demo.

### 2. Leader lease (raft-lite MVP)

```bash
# Terminal A
python3 fleet/showcase/leader_lease.py --alias elect-a --lease 15
# Terminal B (or another host with bus access)
python3 fleet/showcase/leader_lease.py --alias elect-b --lease 15
# Kill the leader process; watch crown move via:
python3 fleet/showcase/leader_lease.py --alias watch --once
curl -s localhost:8787/api/showcase | jq .leader
```

### 3. Fleet barrier

```bash
# Need -n participants. With n=3 and only 2 arrive, it hangs — intentional.
python3 fleet/showcase/barrier.py --alias a -n 3 --gen demo1 &
python3 fleet/showcase/barrier.py --alias b -n 3 --gen demo1 &
# wait… then start the third and watch simultaneous release
python3 fleet/showcase/barrier.py --alias c -n 3 --gen demo1
```

### Dashboard

Open **Showcase** tab on `http://localhost:8787/` — polls `GET /api/showcase`
for leader lease, latest bounty races, barrier kv.

---

## Ideas still on the backlog (from brainstorms)

| Idea | Source | Why it hits hard |
|------|--------|------------------|
| Full muster-raft kill-to-failover crown on Terminals wall | Opus #1 | Crown jumps Mac when you kill a pane |
| Quorum commit (K-of-N) | Opus #2 | Abort below quorum — fleet refuses to lie |
| Dining philosophers deadlock | Opus #3 | `task_claim` as cross-machine forks |
| Work-stealing swarm + kill-and-heal | Opus #4 / Sonnet chaos | Survivors absorb backlog |
| Ghost Hands relay one-command | Sonnet #3 | Proof.md productized |
| Sealed-bid auction router | Sonnet #2 | Fleet negotiates assignment |
| Parallel incident war room | Sonnet #5 | Multi-voice triage thread |

---

## The framing to put on the homepage

> Muster isn’t a chat multipass. It’s a **coordination substrate**: atomic
> claims, shared kv, roles, broadcasts, and a live event journal. The Arcade
> demos **election, barriers, and races** that **cannot exist with one agent**.
> Close a terminal on one Mac; watch the bus heal on another.
