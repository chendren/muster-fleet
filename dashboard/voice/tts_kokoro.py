#!/usr/bin/env python3
"""Kokoro-ONNX TTS CLI - no macOS say."""
import argparse
import os
from kokoro_onnx import Kokoro

MODEL_DIR = os.path.expanduser("~/.local/share/muster-voice/models")
ONNX = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
VOICES = os.path.join(MODEL_DIR, "voices-v1.0.bin")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voice", default="af_bella")
    args = p.parse_args()

    kokoro = Kokoro(ONNX, VOICES)
    samples, sample_rate = kokoro.create(args.text, voice=args.voice, speed=1.0, lang="en-us")
    import soundfile as sf
    sf.write(args.out, samples, sample_rate)
    print(f"Wrote {args.out}")

if __name__ == "__main__":
    main()