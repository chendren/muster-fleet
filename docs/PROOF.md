# Fleet proof: two machines + multiple TUI sessions

**Status:** Verified live  
**Proof ID:** `proof-20260719-233519`  
**When:** 2026-07-19 ~23:35–23:37 CDT  
**Repo:** [chendren/muster-fleet](https://github.com/chendren/muster-fleet)

This is a **real end-to-end run**, not a unit mock: tasks were created on the
shared muster bus, claimed and completed by workers on both machines, and
stamp files were written on each machine’s local filesystem.

---

## Topology under test

| Role | Hostname | User | Role in bus |
|------|----------|------|-------------|
| **HUB** | `Chads-Mac-Studio.local` | `chad` | `muster serve`, single `bus.db` |
| **SPOKE** | `Mac.lan` | `chadhendren` | client via SSH reverse-tunnelled unix socket |

- Hub `bus.db`: `~/.local/share/muster/bus.db`
- Spoke sock: `~/.local/share/muster/sock` (forwarded to hub; not a second daemon)

---

## Live TUI sessions (tmux)

| Machine | tmux session | CLI |
|---------|--------------|-----|
| HUB | `hub-tui-claude` | Claude Code (interactive TUI) |
| SPOKE | `grok-spoke-a` | Grok (interactive TUI) |
| SPOKE | `grok-spoke-b` | Grok (interactive TUI) |

Also on the same bus: headless hub Grok workers `grok-hub-a`, `grok-hub-b`
(and peers `grok-hub-c` / `grok-hub-d` which replied on the broadcast).

---

## Protocol

1. Producer `MacStudioGrok1` created one **CROSS-PROOF-*** task per worker
   with a unique proof id and a machine-specific stamp path.
2. Workers drained the bus (headless loops + TUI nudge / send-keys).
3. Each worker: `register` → claim task → write stamp **on its own machine**
   → reply on the thread → `task_transition completed`.
4. Negative checks: spoke stamps absent on hub; hub stamps absent on spoke.

---

## Results: 5/5 assigned tasks completed

| Thread | Assignee | Status | Stamp location |
|--------|----------|--------|----------------|
| **#64** | `grok-hub-a` | completed | **HUB** `/tmp/fleet-cross-proof-hub-a.txt` |
| **#62** | `grok-hub-b` | completed | **HUB** `/tmp/fleet-cross-proof-hub-b.txt` |
| **#66** | `hub-tui-claude` | completed | **HUB** `/tmp/fleet-cross-proof-claude.txt` |
| **#63** | `grok-spoke-a` | completed | **SPOKE** `/tmp/fleet-cross-proof-spoke-a.txt` |
| **#65** | `grok-spoke-b` | completed | **SPOKE** `/tmp/fleet-cross-proof-spoke-b.txt` |

### Hub stamp contents (`Chads-Mac-Studio.local`)

```text
proof=proof-20260719-233519 machine=hub alias=grok-hub-a ts=1784522158 ok
proof=proof-20260719-233519 machine=hub alias=grok-hub-b ts=1784522154 ok
proof=proof-20260719-233519 machine=hub alias=hub-tui-claude ts=1784522158 ok
```

### Spoke stamp contents (`Mac.lan`, read over SSH)

```text
proof=proof-20260719-233519 machine=spoke alias=grok-spoke-a ts=1784522231 ok
proof=proof-20260719-233519 machine=spoke alias=grok-spoke-b ts=1784522156 ok
```

Hostname files on spoke: `Mac.lan` / `Mac.lan`.

### Cross-machine controls

| Check | Result |
|-------|--------|
| Spoke stamp paths on hub filesystem? | **No** (not present) |
| Hub stamp paths on spoke filesystem? | **No** (not present) |
| Spoke hostnames | **`Mac.lan`** |
| Hub Claude reply hostname | **`Chads-Mac-Studio.local`** |
| Completions on shared bus | Claim + reply + completed entries from each assignee |

---

## What this proves

1. **Two physical machines** share one muster bus over the SSH socket bridge.
2. **Multiple TUI sessions** (1× Claude hub + 2× Grok spoke) claim and finish
   tasks without the producer doing the work.
3. **Headless hub workers** also drain the same bus in parallel.
4. Work is **not simulated on one host**: filesystem stamps + hostnames are
   machine-local.

---

## How to re-run

```bash
# On hub — after fleet is up (see README / docs/FLEET.md)
PROOF_ID="proof-$(date +%Y%m%d-%H%M%S)"

# Create tasks via muster MCP (or operator agent), e.g.:
#   to hub-tui-claude, grok-spoke-a, grok-spoke-b, grok-hub-a, grok-hub-b
# Body: write /tmp/fleet-cross-proof-<alias>.txt with proof=$PROOF_ID machine=...

fleet-nudge-tui once   # kick TUIs
# wait for headless cycles (~15–60s)

# Verify
muster agents
ls -la /tmp/fleet-cross-proof-*.txt
ssh muster-remote 'ls -la /tmp/fleet-cross-proof-*.txt; cat /tmp/fleet-cross-proof-*.host'
# Negative checks
ls /tmp/fleet-cross-proof-spoke-*.txt 2>/dev/null || echo 'OK: no spoke stamps on hub'
ssh muster-remote 'ls /tmp/fleet-cross-proof-hub-*.txt 2>/dev/null || echo OK: no hub stamps on spoke'
```

Related ops: [`docs/FLEET.md`](FLEET.md) · smoke APIs: `fleet/acceptance/smoke.sh`.
