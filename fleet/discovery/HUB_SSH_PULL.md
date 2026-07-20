# Hub pull of spoke discovery (EPIC-6)

Spoke owns scan + registration. Hub owns merge into fleet discovery state (EPIC-1 / `grok-hub-a`).

## Spoke (this machine)

```bash
# once or on a timer
~/muster-fleet-dashboard/fleet/discovery/spoke_scan.sh
# -> /tmp/muster-discovery-spoke.json
# machine=spoke on every local session/agent row
```

Re-register CLI sessions (alias = tmux session name, model_type=grok):

```bash
# MCP or CLI — example for spoke workers
muster register grok-spoke-a --role worker --model grok
muster register grok-spoke-b --role worker --model grok
# Grok MCP: register_agent(alias, role=worker, model_type=grok, session_name=alias)
```

Tunnel check (spoke):

```bash
test -S ~/.local/share/muster/sock && echo SOCK_OK
echo "MUSTER_NO_AUTOSPAWN=${MUSTER_NO_AUTOSPAWN:-unset}"  # expect 1 on multi-machine
muster agents   # must list hub + spoke peers on one bus
```

If sock is missing / `muster agents` fails: tunnel broken. Fix reverse-forward from Mac Studio (daemon host) before merging discovery.

## Hub (Mac Studio)

Suggested Host entry (`~/.ssh/config` on hub):

```sshconfig
Host muster-remote
  HostName 192.168.12.75
  User chadhendren
  IdentityFile ~/.ssh/id_ed25519
  # laptop may also resolve as mac.lan / Chads-MacBook-Pro-2.local
```

Pull + merge sketch:

```bash
#!/usr/bin/env bash
set -euo pipefail
OUT=/tmp/muster-discovery-spoke.json
ssh muster-remote "test -S ~/.local/share/muster/sock && ~/muster-fleet-dashboard/fleet/discovery/spoke_scan.sh" \
  > "$OUT" || ssh muster-remote "cat /tmp/muster-discovery-spoke.json" > "$OUT"

# EPIC-1 merge (example):
# python3 fleet/discovery/merge_spoke.py --spoke "$OUT" --hub /tmp/muster-discovery-hub.json
```

Copy spoke code into hub repo when ready:

```bash
# on hub
HUB=/Users/chad/muster-fleet-dashboard/fleet/discovery
mkdir -p "$HUB"
scp -r muster-remote:~/muster-fleet-dashboard/fleet/discovery/* "$HUB/"
```

## Contract fields for EPIC-1 merge

| Field | Meaning |
|-------|---------|
| `schema` | `muster.discovery.spoke/v1` |
| `machine` | always `spoke` for this document |
| `tmux_sessions[].machine` | `spoke` |
| `cli_processes[].machine` | `spoke` |
| `muster_agents[].machine` | `spoke` for local-named aliases (`grok-spoke*`), else `remote-or-hub` |
| `registration_proof` | alias live on shared bus |
| `tunnel` | sock path, owner, `MUSTER_NO_AUTOSPAWN`, soft-fail notes |

Hub must **not** relabel spoke rows as hub. Additive merge only.
