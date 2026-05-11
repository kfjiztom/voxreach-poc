#!/usr/bin/env bash
# Start the three services for the VoxReach POC (PersonaPlex variant) in tmux.
# Attach with:  tmux attach -t voxreach

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Source the persistent env file if it exists (sets PP_VENV, HF_HOME, etc.)
if [ -f "${WORKSPACE}/.voxreach.env" ]; then
  # shellcheck disable=SC1091
  source "${WORKSPACE}/.voxreach.env"
fi

PP_VENV="${PP_VENV:-${WORKSPACE}/.venv/voxreach-personaplex}"
PERSONAPLEX_REPO="${PERSONAPLEX_REPO:-${WORKSPACE}/personaplex}"
SIDECAR_VENV="${SIDECAR_VENV:-${POC_DIR}/sidecar/.venv}"

SESSION="voxreach"

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "==> tmux session '${SESSION}' already running. Attach with 'tmux attach -t ${SESSION}'."
  echo "    To restart, run: tmux kill-session -t ${SESSION} && bash $0"
  exit 0
fi

if [ ! -d "${PP_VENV}" ]; then
  echo "!! PersonaPlex venv not found at ${PP_VENV}. Run setup.sh first."
  exit 1
fi
if [ ! -d "${SIDECAR_VENV}" ]; then
  echo "!! Sidecar venv not found at ${SIDECAR_VENV}. Run setup.sh first."
  exit 1
fi
if [ ! -d "${PERSONAPLEX_REPO}" ]; then
  echo "!! NVIDIA personaplex repo not found at ${PERSONAPLEX_REPO}. Run setup.sh first."
  exit 1
fi

if [ -z "${HF_TOKEN:-}" ]; then
  echo "!! HF_TOKEN not set. Export it on this pod before running."
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

# Cache redirects
export HF_HOME="${HF_HOME:-${WORKSPACE}/.cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}}"

PERSONAPLEX_PORT="${PERSONAPLEX_PORT:-8998}"
SIDECAR_PORT="${SIDECAR_PORT:-8001}"
WEB_PORT="${WEB_PORT:-3001}"

# Kill RunPod's nginx placeholder if it's squatting on our ports.
# (It serves a README page on exposed ports until a real service binds.)
if ss -tlnp 2>/dev/null | grep -E ":${SIDECAR_PORT}|:${WEB_PORT}" | grep -q nginx; then
  echo "==> Killing RunPod's placeholder nginx ..."
  pkill -x nginx 2>/dev/null || true
  sleep 1
fi

# Start Ollama in the background if it's installed (powers the order extractor).
# Without Ollama, the sidecar falls back to the rule-based extractor (append-only).
if command -v ollama >/dev/null 2>&1; then
  if ! pgrep -x ollama >/dev/null; then
    echo "==> Starting Ollama daemon in the background ..."
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 2
  fi
  echo "==> Ollama: $(ollama --version 2>&1 | head -1)"
else
  echo "==> Ollama not installed — sidecar will use rule-based extractor (no cancel/modify support)."
  echo "    Run bash runpod/setup_ollama.sh to enable LLM-based extraction."
fi

echo "==> Starting tmux session '${SESSION}' with 3 windows."

# Window 1: PersonaPlex full-duplex speech server
tmux new-session -d -s "${SESSION}" -n personaplex "
  source ${PP_VENV}/bin/activate;
  cd ${PERSONAPLEX_REPO};
  export HF_HOME=${HF_HOME};
  export HUGGINGFACE_HUB_CACHE=${HUGGINGFACE_HUB_CACHE};
  export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE};
  export HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN};
  echo '[personaplex] starting on :${PERSONAPLEX_PORT}';
  python -m moshi.server --host 0.0.0.0 --port ${PERSONAPLEX_PORT}
"

# Window 2: Sidecar
tmux new-window -t "${SESSION}" -n sidecar "
  cd ${POC_DIR}/sidecar;
  echo '[sidecar] starting on :${SIDECAR_PORT}';
  export OLLAMA_URL=\${OLLAMA_URL:-http://localhost:11434/v1};
  export VOXREACH_ORDER_MODEL=\${VOXREACH_ORDER_MODEL:-gemma2:9b};
  export VOXREACH_EXTRACTOR=\${VOXREACH_EXTRACTOR:-auto};
  SIDECAR_PORT=${SIDECAR_PORT} bash run.sh
"

# Window 3: Web (Next.js)
tmux new-window -t "${SESSION}" -n web "
  cd ${POC_DIR}/web;
  echo '[web] building and starting on :${WEB_PORT}';
  export NEXT_PUBLIC_MOCK_MODE=\${NEXT_PUBLIC_MOCK_MODE:-true};
  export NEXT_PUBLIC_PERSONAPLEX_URL=\${NEXT_PUBLIC_PERSONAPLEX_URL:-};
  export SIDECAR_URL=http://localhost:${SIDECAR_PORT};
  npm run build && npm run start
"

echo ""
echo "==> Services launching in tmux session '${SESSION}'."
echo "    Attach:  tmux attach -t ${SESSION}"
echo "    Kill:    tmux kill-session -t ${SESSION}"
echo ""
echo "==> Public URLs (via RunPod proxy):"
echo "    Web demo:        https://<POD-ID>-${WEB_PORT}.proxy.runpod.net"
echo "    PersonaPlex UI:  https://<POD-ID>-${PERSONAPLEX_PORT}.proxy.runpod.net"
echo "    Sidecar:         https://<POD-ID>-${SIDECAR_PORT}.proxy.runpod.net/api/state"
echo ""
echo "==> If web should embed PersonaPlex iframe, export the PersonaPlex"
echo "    proxy URL BEFORE running start.sh (Next bakes it in at build time):"
echo "    export NEXT_PUBLIC_PERSONAPLEX_URL='https://<POD-ID>-${PERSONAPLEX_PORT}.proxy.runpod.net'"
