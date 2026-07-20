# Computer voice stack

Local-only voice control for the fleet dashboard (“Computer, collaboration
sequence — on screen!”).

**Repo:** [github.com/chendren/muster-fleet](https://github.com/chendren/muster-fleet)

## Design constraints

| Do | Don’t |
|----|-------|
| Whisper STT (local) | Cloud STT as primary |
| Ollama LLM (`qwen2.5:3b`) for tool routing | Cloud LLM as primary |
| **Kokoro-ONNX** TTS | macOS `say` / AVSpeech / `window.speechSynthesis` as primary |
| Models under `~/.local/share/muster-voice/` | Ship multi-GB weights in git |

## Layout

```
dashboard/voice/
  README pointers → this doc + FLEET_PLAN.md
  aliases.json       spoken names → bus aliases + display names
  tools.json         UI tool catalog for the brain
  stt.py             Whisper wrapper
  tts_kokoro.py      Kokoro-ONNX wav synthesis
  brain.py           Ollama tool router
  computer.py        orchestration helpers
  personality.py     sarcastic / varied quips + refusal logging
  voice_log.py       phrase / command logging
  download_models.sh fetch onnx + voices; ensure ollama model
  .venv/             isolated Python deps (gitignored)
```

## One-time model setup (hub)

```bash
cd dashboard/voice
./download_models.sh
# expects: ollama with qwen2.5:3b; kokoro-v1.0.onnx + voices-v1.0.bin
```

Model directory: `~/.local/share/muster-voice/models/`

## HTTP API (via `dashboard/server.py`)

| Method | Path | Body / notes | Response |
|--------|------|--------------|----------|
| GET | `/api/voice/status` | — | models ready? |
| GET | `/api/voice/aliases` | — | `{aliases, display_names}` |
| GET | `/api/voice/help` | — | sayable commands / help text |
| POST | `/api/voice/stt` | multipart audio | `{text}` |
| POST | `/api/voice/command` | `{text}` | `{speech, tool_calls[], transcript}` |
| POST | `/api/voice/tts` | `{text}` | wav (or base64 wrapper) |
| POST | `/api/voice/pipeline` | audio in | STT + brain + TTS out |

**Contract pitfalls (already hit):**

- `/api/voice/aliases` returns `{aliases: {…}, display_names: {…}}` — not a
  bare map. Frontend must unwrap `.aliases`.
- Pipeline audio field may be `audio_wav_b64` (prefer) with fallbacks for
  older `audio_b64` naming.

## UI tools (brain → client executor)

| Tool | Effect |
|------|--------|
| `open_view` | fleet \| collab \| terminals |
| `open_terminal` | drill into live pane (speech name → alias) |
| `focus_agent` | highlight + sequence focus |
| `focus_thread` | sequence for thread id/subject |
| `set_filter` | all \| live \| grok \| claude \| hub \| spoke |
| `clear_focus` | clear collab focus |
| `list_fleet` | speak fleet roster |
| `report_status` | live/pane/task counts |
| `on_screen` | flash + open target (“On screen!”) |

Client executes `tool_calls` against existing dashboard JS — the brain does
not drive the DOM directly.

## Speech aliases

Dashes are poison for STT. Use spoken names; map in `aliases.json`:

| Spoken | Bus alias |
|--------|-----------|
| Claude / Number One | `hub-tui-claude` |
| Hub Alpha / Grok Hub A | `grok-hub-a` |
| Hub Bravo | `grok-hub-b` |
| Hub Charlie | `grok-hub-c` |
| Spoke A | `grok-spoke-a` |
| Spoke B | `grok-spoke-b` |
| Mac Studio Grok | `MacStudioGrok1` |

Headless workers: “show terminal for Hub Alpha” should explain that there is
no pane (headless drain loop), not invent one.

## Personality

`personality.py` varies quips via the local LLM and can log refusals. Keep
tone configurable; don’t hard-code a single catchphrase as the only path.

## Acceptance checklist

1. `./download_models.sh` succeeds; `ollama list` shows `qwen2.5:3b`
2. `POST /api/voice/command` with  
   `"Computer, open collaboration sequence on screen"` → `open_view(collab)`
3. `POST /api/voice/tts` returns playable wav (Kokoro)
4. Computer panel: mic → tools execute → Kokoro speaks
5. “Show me Grok Hub A terminal” opens pane **or** explains headless

See also [`dashboard/voice/FLEET_PLAN.md`](../dashboard/voice/FLEET_PLAN.md)
for the original fleet task breakdown used to build this stack.
