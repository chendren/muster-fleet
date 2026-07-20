#!/usr/bin/env python3
"""Whisper STT wrapper for Computer voice stack."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

WHISPER_CANDIDATES = (
    os.environ.get("MUSTER_WHISPER_BIN"),
    "/opt/homebrew/bin/whisper",
    "/usr/local/bin/whisper",
    "whisper",
)


def find_whisper() -> str:
    for c in WHISPER_CANDIDATES:
        if not c:
            continue
        if c == "whisper" or os.path.isfile(c):
            return c
    return "whisper"


def transcribe(audio_path: str) -> str:
    """Transcribe audio file using openai-whisper CLI. Returns plain text."""
    whisper = find_whisper()
    audio_path = str(Path(audio_path).resolve())
    out_dir = tempfile.mkdtemp(prefix="muster-stt-")
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    # tiny = faster for short commands; language=en reduces garbage
    cmd = [
        whisper,
        audio_path,
        "--model", os.environ.get("MUSTER_WHISPER_MODEL", "tiny"),
        "--language", "en",
        "--task", "transcribe",
        "--output_format", "txt",
        "--output_dir", out_dir,
        "--fp16", "False",
        "--verbose", "False",
        "--condition_on_previous_text", "False",
        "--initial_prompt",
        "Computer show Chad Court Chris Alex Scout Rio Nova Morgan Sam Claude Grok "
        "open collaboration on screen show fleet terminals focus filter live list fleet report status",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, env=env,
        )
        # whisper writes <stem>.txt into output_dir
        stem = Path(audio_path).stem
        txt = Path(out_dir) / (stem + ".txt")
        if txt.is_file():
            text = txt.read_text(encoding="utf-8", errors="replace").strip()
        else:
            # any txt in dir
            texts = list(Path(out_dir).glob("*.txt"))
            text = texts[0].read_text(encoding="utf-8", errors="replace").strip() if texts else ""
            if not text:
                text = (result.stdout or "").strip()
        if not text:
            err = (result.stderr or "")[-400:]
            return f"[STT empty] {err}" if err else "[no transcript]"
        return text
    except Exception as e:
        return f"[STT error: {e}]"
    finally:
        try:
            for p in Path(out_dir).glob("*"):
                p.unlink(missing_ok=True)
            Path(out_dir).rmdir()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: stt.py <audio.wav>", file=sys.stderr)
        sys.exit(1)
    print(transcribe(sys.argv[1]))
