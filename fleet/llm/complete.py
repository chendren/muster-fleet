#!/usr/bin/env python3
"""LLM complete abstraction: local (Ollama) or cloud (claude subscription)."""
import json
import os
import subprocess
import urllib.request

MODE_FILE = os.path.expanduser("~/.local/share/muster-fleet/llm-mode.json")
DEFAULT_MODE = "local"

def get_mode():
    try:
        with open(MODE_FILE) as f:
            return json.load(f).get("mode", DEFAULT_MODE)
    except Exception:
        return DEFAULT_MODE

def set_mode(mode):
    os.makedirs(os.path.dirname(MODE_FILE), exist_ok=True)
    with open(MODE_FILE, "w") as f:
        json.dump({"mode": mode}, f)

def complete_local(prompt):
    url = "http://localhost:11434/api/generate"
    data = json.dumps({"model": "qwen2.5:3b", "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"]

def complete_cloud(prompt):
    # Uses claude subscription (no API key). Model from docs/LLM_TOGGLE.md
    cmd = ["claude", "-p", "--model", "claude-haiku-4-5-20251001", prompt]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=120)
    return out.decode().strip()

def complete(prompt):
    mode = get_mode()
    if mode == "cloud":
        return complete_cloud(prompt)
    return complete_local(prompt)

if __name__ == "__main__":
    import sys
    print(complete(" ".join(sys.argv[1:])))