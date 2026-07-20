# Fleet Discovery

`discover.py` — one-shot JSON scan of tmux + processes + loop pids.
`daemon.sh` — background loop writing `~/.local/share/muster-fleet/discovery.json` every 20s.

Endpoints (see dashboard/server.py):
- GET /api/discovery
- POST /api/discovery/scan