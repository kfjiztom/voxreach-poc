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

# ---- Hardware autodetect ------------------------------------------------
# Run detect_hw.py to write per-GPU tuning into ${WORKSPACE}/.voxreach-hw.env
# (TORCH_CUDA_ARCH_LIST, PYTORCH_CUDA_ALLOC_CONF, VOXREACH_EXTRACT_TIMEOUT,
# VOXREACH_ORDER_MODEL). Aborts here if the GPU is in the blocked list so
# we fail loudly rather than spin up services that won't meet real-time.
HW_ENV="${WORKSPACE}/.voxreach-hw.env"
if ! python3 "$(dirname "$0")/detect_hw.py" --out "${HW_ENV}"; then
  echo "!! Hardware detection failed — refusing to launch services." >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${HW_ENV}"

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
  echo "!! HF_TOKEN not set in this shell."
  if [ -n "${SUDO_USER:-}" ]; then
    echo ""
    echo "   You ran this with sudo, which strips environment variables by default."
    echo "   Fix it one of these ways:"
    echo ""
    echo "     # Preserve env across sudo (simplest):"
    echo "     sudo -E bash $0"
    echo ""
    echo "     # OR pass tokens through explicitly:"
    echo "     sudo HF_TOKEN=\"\$HF_TOKEN\" \\"
    echo "          NEXT_PUBLIC_PERSONAPLEX_URL=\"\$NEXT_PUBLIC_PERSONAPLEX_URL\" \\"
    echo "          NEXT_PUBLIC_MOCK_MODE=\"\$NEXT_PUBLIC_MOCK_MODE\" \\"
    echo "          bash $0"
    echo ""
    echo "     # OR don't sudo at all — services start as your user:"
    echo "     bash $0"
  else
    echo "   Export it: export HF_TOKEN=hf_<your-token>"
  fi
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
#
# IMPORTANT: pass OLLAMA_MODELS so ollama looks at our persistent model dir.
# If we don't, ollama defaults to ~/.ollama/models for whichever user is
# running it, which after a pod migration / user swap will be empty — even
# though /workspace/ollama-models holds 5+ GB of pulled models.
export OLLAMA_MODELS="${OLLAMA_MODELS:-${WORKSPACE}/ollama-models}"
if command -v ollama >/dev/null 2>&1; then
  if ! pgrep -x ollama >/dev/null; then
    echo "==> Starting Ollama daemon (OLLAMA_MODELS=${OLLAMA_MODELS}) ..."
    nohup env OLLAMA_MODELS="${OLLAMA_MODELS}" ollama serve > /tmp/ollama.log 2>&1 &
    sleep 3
  else
    # Already running — confirm it can see our models. If not, the daemon
    # was started without OLLAMA_MODELS and we need to restart it.
    RUNNING_OLLAMA_PID=$(pgrep -x ollama | head -1)
    RUNNING_OLLAMA_MODELS=$(cat /proc/${RUNNING_OLLAMA_PID}/environ 2>/dev/null | tr '\0' '\n' | grep '^OLLAMA_MODELS=' | cut -d= -f2-)
    if [ "${RUNNING_OLLAMA_MODELS}" != "${OLLAMA_MODELS}" ]; then
      echo "==> Existing Ollama uses '${RUNNING_OLLAMA_MODELS:-<default>}' — restarting it to use ${OLLAMA_MODELS}"
      pkill -x ollama 2>/dev/null || true
      sleep 2
      nohup env OLLAMA_MODELS="${OLLAMA_MODELS}" ollama serve > /tmp/ollama.log 2>&1 &
      sleep 3
    fi
  fi
  echo "==> Ollama: $(ollama --version 2>&1 | head -1)"
  echo "==> Models available: $(OLLAMA_HOST=http://localhost:11434 ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | xargs echo)"
else
  echo "==> Ollama not installed — sidecar will use rule-based extractor (no cancel/modify support)."
  echo "    Run bash runpod/setup_ollama.sh to enable LLM-based extraction."
fi

echo "==> Starting tmux session '${SESSION}' with 3 windows."

# Default persona prompt — auto-loaded when the client sends empty text_prompt.
# Requires the server.py patch applied by setup.sh (see runpod/patches/).
# Uses _short.txt by default (Option 1 — slim persona with fillers + escalation).
DEFAULT_PROMPT_FILE="${DEFAULT_PROMPT_FILE:-${POC_DIR}/persona/vox_personaplex_prompt_short.txt}"

# Window 1: PersonaPlex full-duplex speech server
tmux new-session -d -s "${SESSION}" -n personaplex "
  source ${PP_VENV}/bin/activate;
  cd ${PERSONAPLEX_REPO};
  export HF_HOME=${HF_HOME};
  export HUGGINGFACE_HUB_CACHE=${HUGGINGFACE_HUB_CACHE};
  export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE};
  export HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN};
  export MOSHI_DEFAULT_TEXT_PROMPT_FILE=${DEFAULT_PROMPT_FILE};
  export VOXREACH_SIDECAR_URL=http://localhost:${SIDECAR_PORT};
  export VOXREACH_CUSTOMER_STT_ENABLED=\${VOXREACH_CUSTOMER_STT_ENABLED:-0};
  export TORCH_CUDA_ARCH_LIST=\${TORCH_CUDA_ARCH_LIST:-};
  export PYTORCH_CUDA_ALLOC_CONF=\${PYTORCH_CUDA_ALLOC_CONF:-};
  echo '[personaplex] default prompt: ${DEFAULT_PROMPT_FILE}';
  echo '[personaplex] sidecar URL:    http://localhost:${SIDECAR_PORT}';
  echo '[personaplex] STT enabled:    '\${VOXREACH_CUSTOMER_STT_ENABLED};
  echo '[personaplex] torch arch:     '\${TORCH_CUDA_ARCH_LIST};
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
echo "==> Local URLs (this pod):"
echo "    Web demo:        http://localhost:${WEB_PORT}"
echo "    PersonaPlex UI:  http://localhost:${PERSONAPLEX_PORT}"
echo "    Sidecar:         http://localhost:${SIDECAR_PORT}/api/state"
echo ""
echo "==> To reach them from your laptop:"
echo "    Thunder: re-launch your tunnel with these ports forwarded:"
echo "      tnr connect <instance-id> -t ${WEB_PORT} -t ${SIDECAR_PORT} -t ${PERSONAPLEX_PORT}"
echo "    RunPod : use the *.proxy.runpod.net URLs from the pod dashboard."
echo ""
echo "==> If web should embed PersonaPlex iframe, set BEFORE running start.sh:"
echo "    Thunder: export NEXT_PUBLIC_PERSONAPLEX_URL='http://localhost:${PERSONAPLEX_PORT}'"
echo "    RunPod : export NEXT_PUBLIC_PERSONAPLEX_URL='https://<POD-ID>-${PERSONAPLEX_PORT}.proxy.runpod.net'"
