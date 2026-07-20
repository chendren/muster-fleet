# AgentCore Runtime (Local Emulator)

## Overview
AgentCore provides a minimal local runtime emulator for agent execution, sessions, and memory. This is a process-level emulator (not a true microVM) that mimics the core AgentCore API surface for development and testing.

**Current Status:** Process emulator running on `127.0.0.1:8790`.  
**True microVM Path:** When `limactl` is present, Lima can be used to launch a lightweight VM. If `limactl` is missing, the emulator falls back to the local Python process.

## Running

### Using run.sh (recommended)
```bash
cd fleet/agentcore
./run.sh
```
- Starts the emulator detached with a PID file at `/tmp/agentcore-emulator.pid`
- Logs to `/tmp/agentcore-emulator.log`
- Idempotent: does not start a second instance if already running

### Direct Python
```bash
python3 fleet/agentcore/agentcore_emulator.py
```

### Health Check
```bash
curl -s http://127.0.0.1:8790/health
```

## API

| Method | Path                        | Description                              |
|--------|-----------------------------|------------------------------------------|
| GET    | /health                     | Health check + version                   |
| GET    | /agents                     | List registered agents                   |
| POST   | /invoke                     | Invoke an agent with input               |
| GET    | /sessions/{id}/memory       | Get session memory                       |
| PUT    | /sessions/{id}/memory       | Update session memory                    |

All responses include `X-Request-Id` header for tracing.

### Example invoke
```bash
curl -s -X POST http://127.0.0.1:8790/invoke \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"echo","session_id":"s1","input":"hi"}'
```

## Dashboard Proxy
The fleet dashboard server proxies these endpoints:
- `GET /api/agentcore/health`
- `POST /api/agentcore/invoke`

## Limitations / Future
- Emulator is a Python process (no isolation)
- For real isolation, integrate Lima (`limactl`) or container runtime
- Optional: shell out to local LLM (Ollama) for non-echo responses in invoke

## Files
- `fleet/agentcore/agentcore_emulator.py` — emulator server
- `fleet/agentcore/run.sh` — launcher with pidfile
- `dashboard/server.py` — includes proxy routes
