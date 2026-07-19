# Dashboard data contract

Every collector emits JSON matching this shape. The aggregator server merges
multiple collector outputs (one per machine) into a single fleet-wide
payload served at `/api/status`.

## Collector output (one per machine)

```json
{
  "machine": "hub",
  "collected_at": "2026-07-19T18:40:00Z",
  "agents": [
    {
      "alias": "chad-mac",
      "activity": {
        "source": "claude_transcript",
        "session_path": "/Users/chad/.claude/projects/-Users-chad/xxxx.jsonl",
        "session_id": "xxxx",
        "cwd": "/Users/chad",
        "last_message_preview": "first ~200 chars of the last assistant text block",
        "last_tool": "Bash",
        "turn_count": 42,
        "model": "claude-sonnet-5",
        "updated_at": "2026-07-19T18:39:55Z",
        "tokens": {
          "input_tokens": 2,
          "output_tokens": 264,
          "cache_creation_input_tokens": 591,
          "cache_read_input_tokens": 256078
        }
      },
      "pane_snapshot": null
    }
  ]
}
```

Rules:

- `activity.source` is one of `"claude_transcript"`, `"grok_native_session"`,
  `"grok_npm"`, or `"none"`. Use `"none"` (and omit every other `activity`
  field except `source`) when nothing was found — **never fabricate a
  value**. A missing/quiet CLI is real information, not a bug to paper
  over.
- `tokens` is present **only** when `source == "claude_transcript"` — Grok
  CLI's local logs (both the npm and native builds) do not expose token
  counts. Do not estimate or backfill this; omit the key entirely for Grok
  agents.
- `pane_snapshot` is a raw string (last ~4000 chars of `tmux capture-pane
  -p -t <pane_id>` output) when `tmux` is installed on that machine AND the
  agent has a live `pane_id`. Otherwise `null`. Only the hub currently has
  `tmux` installed — expect `null` from the spoke for now; the field
  exists so this fills in automatically once that changes.
- **Alias-to-session mapping is a best-effort heuristic**, since muster
  itself doesn't record which local file backs a given alias:
  - Claude Code: the most-recently-modified `*.jsonl` file under
    `~/.claude/projects/**` on that machine, but only if its mtime is
    within the last 15 minutes (otherwise there's no "current" session to
    report — use `source: "none"`). If more than one alias exists on the
    same machine, this heuristic can't disambiguate between them
    perfectly — pick the most recently modified file per machine and
    attach it to whichever local `claude`-type alias has the most recent
    `last_seen` in the muster agents list you're given. Document this
    limitation in a code comment; don't silently pretend it's exact.
  - Native Grok CLI (`~/.grok/sessions/**/summary.json` +
    `chat_history.jsonl` in the same directory): same
    most-recently-modified-within-15-minutes heuristic over `summary.json`
    files, keyed by `updated_at` inside the file itself, not just mtime.
  - npm `@vibe-kit/grok-cli`: headless `-p` runs are stateless and don't
    persist a session log on disk (confirmed: no `~/.grok/sessions/`
    equivalent exists for it). Always emit `source: "none"` for these —
    this is accurate, not a gap to work around.

## Aggregator's merged output (`GET /api/status`)

The aggregator (`dashboard/server.py`, built by the integrator — not part
of this task) merges each machine's collector JSON with muster's own bus
data (`agents`, `threads`, `events` tables in `bus.db`, queried directly by
the aggregator) into one payload. Collector authors don't need to build
this part — just make sure your collector's JSON matches the shape above
exactly so the merge is mechanical.

## How your collector will be invoked

- **Hub collector**: run as a local Python process, imported or subprocess
  by the aggregator, on the hub machine directly.
- **Spoke collector**: invoked by the aggregator over SSH
  (`ssh muster-remote python3 /path/to/spoke_collector.py`), must print its
  JSON output to **stdout only** (diagnostics/errors to stderr) so the
  aggregator can parse it cleanly — same "stdout is sacred" rule muster
  itself follows for its MCP mode.

## Testing your collector standalone

```bash
python3 your_collector.py | python3 -m json.tool
```

Should print valid JSON matching the shape above with no stray output on
stdout.
