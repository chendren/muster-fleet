# Computer Voice Stack — Fleet Plan

Star Trek style: Captain says "Computer, collaboration sequence — on screen!"  
Local only: **no macOS `say`**. Stack = **Whisper STT + Ollama LLM + Kokoro-ONNX TTS**.

## Repo root
`/Users/chad/muster-fleet-dashboard`

## Target layout
```
dashboard/
  voice/
    FLEET_PLAN.md          # this file
    aliases.json           # speech names → bus aliases
    tools.json             # UI tool catalog
    stt.py                 # Whisper STT wrapper
    tts_kokoro.py          # Kokoro-ONNX TTS (no macOS say)
    brain.py               # Ollama qwen2.5 tool router
    computer.py            # command orchestration
    download_models.sh     # fetch kokoro onnx + voices + ensure ollama model
    .venv/                 # isolated deps
  server.py                # + /api/voice/* endpoints
  frontend/index.html      # Computer panel + tool executor
```

## Models (local, downloaded — not system TTS)
| Role | Choice | Why |
|------|--------|-----|
| LLM  | `qwen2.5:3b` via Ollama | Small, fast, excellent instruction/JSON tool routing for voice UI commands |
| TTS  | **Kokoro-82M ONNX** (`kokoro-v1.0.onnx` + `voices-v1.0.bin`) | Best small high-quality OSS TTS; CPU/M-series friendly; no Apple `say` |
| STT  | Homebrew `whisper` / openai-whisper small or base | Already on hub; low latency for short commands |

Model files live under: `~/.local/share/muster-voice/models/`

## Speech aliases (dashes are poison for STT)
Use spoken names only in prompts; map in `aliases.json`:
- "Claude" / "Number One" / "the Claude session" → `hub-tui-claude`
- "Grok Hub A" / "Hub Alpha" → `grok-hub-a`
- "Grok Hub B" / "Hub Bravo" → `grok-hub-b`
- "Grok Hub C" / "Hub Charlie" → `grok-hub-c`
- "Spoke A" / "Grok Spoke A" → `grok-spoke-a`
- "Spoke B" / "Grok Spoke B" → `grok-spoke-b`
- "Mac Studio Grok" → `MacStudioGrok1`

## UI tools (must implement all)
| Tool | Effect |
|------|--------|
| `open_view` | fleet \| collab \| terminals |
| `open_terminal` | drill into live pane for agent (speech name) |
| `focus_agent` | highlight + sequence focus |
| `focus_thread` | sequence for thread id/subject |
| `set_filter` | all\|live\|grok\|claude\|hub\|spoke |
| `clear_focus` | clear collab focus |
| `list_fleet` | speak/status of workers |
| `report_status` | counts live/panes/tasks |
| `on_screen` | flash + open target view ("On screen!") |

## HTTP API (server.py)
- `GET  /api/voice/status` — models ready?
- `POST /api/voice/stt` — multipart audio → `{text}`
- `POST /api/voice/command` — `{text}` → `{speech, tool_calls[], transcript}`
- `POST /api/voice/tts` — `{text}` → audio/wav (Kokoro)
- `POST /api/voice/pipeline` — audio in → stt+brain+tts out

## UX
- Floating **COMPUTER** panel (LCARS-ish)
- Hold-to-talk mic
- Phrase strip: "WORKING…" / "ON SCREEN" / "ACKNOWLEDGED"
- Client executes tool_calls against existing JS (`setView`, `openModal`, `setFocusAlias`, …)
- TTS plays returned wav — never `window.speechSynthesis` as primary

## Constraints
- NO macOS `say` / AVSpeech / system voices as primary path
- NO cloud STT/LLM/TTS as primary (local only)
- Keep dashboard stdlib server; voice subprocess uses `dashboard/voice/.venv`

## Acceptance
1. `./download_models.sh` succeeds; `ollama list` shows `qwen2.5:3b`; kokoro onnx+voices present
2. `POST /api/voice/command` with text "Computer, open collaboration sequence on screen" returns tool open_view(collab) + speech
3. `POST /api/voice/tts` returns playable wav (Kokoro)
4. UI Computer panel: mic → tools execute → Kokoro speaks "On screen."
5. "Show me Grok Hub A terminal" opens terminal for `grok-hub-a` (or explains no pane if headless)
