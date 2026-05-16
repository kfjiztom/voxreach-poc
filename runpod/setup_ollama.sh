#!/usr/bin/env bash
# Optional add-on for the VoxReach POC: install Ollama + pull the order
# extractor model used by the sidecar.
#
# Without this, the sidecar falls back to a rule-based extractor that only
# appends items (no cancel/modify support). With this, the LLM extractor
# handles the full conversation context — quantity changes, swaps, cancels.
#
# Model: qwen3.5:4b (~3.4 GB). Newer + smaller than the prior gemma2:9b
# choice, and stronger at the rigid JSON output we want.
#
# VRAM budget (typical):
#   PersonaPlex ~18 GB + qwen3.5:4b ~3.4 GB + faster-whisper ~1.5 GB = ~23 GB.
#   Fits comfortably on A100 80 GB, L40 48 GB, A40 48 GB.
#
# Override via env: VOXREACH_ORDER_MODEL=<other-ollama-tag> bash setup_ollama.sh

set -euo pipefail

# Source the per-GPU env file if detect_hw.py has run — it sets the right
# VOXREACH_ORDER_MODEL for whatever hardware we landed on.
WORKSPACE="${WORKSPACE:-/workspace}"
if [ -f "${WORKSPACE}/.voxreach-hw.env" ]; then
  # shellcheck disable=SC1091
  source "${WORKSPACE}/.voxreach-hw.env"
fi

OLLAMA_MODEL="${VOXREACH_ORDER_MODEL:-qwen3.5:4b}"

echo "==> Installing Ollama (one-shot script) ..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "    Ollama already installed: $(ollama --version 2>&1 | head -1)"
fi

# Start Ollama daemon in the background if not already running.
if ! pgrep -x ollama >/dev/null; then
  echo "==> Starting Ollama daemon ..."
  nohup ollama serve > /tmp/ollama.log 2>&1 &
  sleep 3
fi

# Wait for the daemon to respond
echo "==> Waiting for Ollama API on :11434 ..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fs http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "    Ollama API ready."
    break
  fi
  sleep 2
done

echo "==> Pulling model ${OLLAMA_MODEL} (the long step) ..."
ollama pull "${OLLAMA_MODEL}"

echo "==> Smoke test: extract a simple order ..."
RESPONSE=$(curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${OLLAMA_MODEL}\",
    \"messages\": [
      {\"role\": \"system\", \"content\": \"Reply with the JSON {\\\"ok\\\": true} and nothing else.\"},
      {\"role\": \"user\", \"content\": \"ping\"}
    ],
    \"stream\": false,
    \"format\": \"json\"
  }")
echo "${RESPONSE}" | head -c 500
echo ""

echo ""
echo "==> Ollama setup complete."
echo "==> The sidecar's order extractor will auto-detect Ollama on next start."
echo "==> Force a specific extractor by setting VOXREACH_EXTRACTOR=llm or =rule."
