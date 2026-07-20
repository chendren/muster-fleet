#!/bin/bash
set -e
MODEL_DIR="$HOME/.local/share/muster-voice/models"
mkdir -p "$MODEL_DIR"
ONNX="$MODEL_DIR/kokoro-v1.0.onnx"
VOICES="$MODEL_DIR/voices-v1.0.bin"
if [ -f "$ONNX" ] && [ -f "$VOICES" ]; then
  echo "Models already present, skipping download."
  exit 0
fi
echo "Downloading Kokoro-ONNX models..."
curl -L -o "$ONNX" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o "$VOICES" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
echo "Download complete."