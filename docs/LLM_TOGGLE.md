# LLM Toggle — Local Ollama vs Cloud Claude Haiku 4.5

Shared mode for the dashboard (`/api/llm/*`), Computer voice routing hooks,
and any fleet service that calls `fleet/llm/complete.py`.

## Modes

| Mode | Backend | Model ID / name |
|------|---------|-----------------|
| `local` | Ollama HTTP | `qwen2.5:3b` (env `FLEET_LOCAL_MODEL` overrides) |
| `cloud` | Claude Code CLI (`claude -p`) on the **hub** | **`claude-haiku-4-5-20251001`** (Claude Haiku 4.5) |

**Cloud is subscription-backed**, not a raw Anthropic API key in the repo.
It uses the same Claude Code login as interactive sessions on the hub
(`claude login` / keychain). Alias `haiku` also resolves to the same
Haiku 4.5 id in current Claude Code builds.

## Persistence

```
~/.local/share/muster-fleet/llm-mode.json
→ {"mode":"local"} | {"mode":"cloud"}
```

## Endpoints (`dashboard/server.py`)

| Method | Path | Body / notes |
|--------|------|----------------|
| GET | `/api/llm/mode` | → `{"mode":"…"}` |
| POST | `/api/llm/mode` | `{"mode":"local"\|"cloud"}` |
| POST | `/api/llm/complete` | `{"prompt":"…"}` → `{"text","mode","latency_ms"}` |

## Implementation

- `fleet/llm/complete.py` — `get_mode` / `set_mode` / `complete_local` / `complete_cloud` / `complete`
- Cloud command (verified):

  ```bash
  claude -p --model claude-haiku-4-5-20251001 "<prompt>"
  ```

## Prove cloud is really Haiku 4.5

```bash
# 1) Set mode
curl -s -X POST http://127.0.0.1:8787/api/llm/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"cloud"}'
# → {"mode":"cloud"}

# 2) Dashboard complete
curl -s -X POST http://127.0.0.1:8787/api/llm/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Reply with exactly: FLEET_CLOUD_TEST_OK"}'
# → {"text":"FLEET_CLOUD_TEST_OK","mode":"cloud","latency_ms":…}

# 3) Claude Code reports the runtime model id in modelUsage
claude -p --model claude-haiku-4-5-20251001 --output-format json \
  'Reply with exactly: DIRECT_HAIKU_OK' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('modelUsage'))"
# → must include key "claude-haiku-4-5-20251001"
```

Verified on hub (Claude Code 2.1.x): `modelUsage` keys
`['claude-haiku-4-5-20251001']` with non-zero `costUSD` — i.e. real
subscription inference, not Ollama.

## Notes

- Default mode if the file is missing: **`local`** (cheap, offline-friendly).
- Interactive `hub-tui-claude` sessions may still use Claude Code’s own
  default (e.g. `sonnet` in `~/.claude/settings.json`) — that is **separate**
  from this fleet LLM toggle.
- Do not commit API keys. Cloud auth stays on the machine via Claude Code.
