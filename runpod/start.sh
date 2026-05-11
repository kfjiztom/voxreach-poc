#!/usr/bin/env bash
# Start the three services for the VoxReach POC (PersonaPlex variant) in tmux.
# Attach with:  tmux attach -t voxreach

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PP_VENV="${PP_VENV:-/opt/voxreach-personaplex}"
SIDECAR_VENV="${POC_DIR}/sidecar/.venv"

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

if [ -z "${HF_TOKEN:-}" ]; then
  echo "!! HF_TOKEN not set. Run setup.sh first or export HF_TOKEN."
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

# Cache redirects (in case shell wasn't sourced from .bashrc)
export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}}"

PERSONAPLEX_PORT="${PERSONAPLEX_PORT:-8998}"
SIDECAR_PORT="${SIDECAR_PORT:-8001}"
WEB_PORT="${WEB_PORT:-3001}"

echo "==> Starting tmux session '${SESSION}' with 3 windows."

# Window 1: PersonaPlex full-duplex speech server
tmux new-session -d -s "${SESSION}" -n personaplex "
  source ${PP_VENV}/bin/activate;
  export HF_HOME=${HF_HOME};
  export HUGGINGFACE_HUB_CACHE=${HUGGINGFACE_HUB_CACHE};
  export TRANSFORMERS_CACHE=${TRANSFORMERS_CACHE};
  export HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN};
  echo '[personaplex] starting nvidia/personaplex-7b-v1 on :${PERSONAPLEX_PORT}';
  SSL_DIR=\$(mktemp -d);
  python -m moshi.server --hf-repo nvidia/personaplex-7b-v1 --ssl \"\$SSL_DIR\" --port ${PERSONAPLEX_PORT}
"

# Window 2: Sidecar (transcript watcher, intent extraction, POS stub)
tmux new-window -t "${SESSION}" -n sidecar "
  cd ${POC_DIR}/sidecar;
  echo '[sidecar] starting on :${SIDECAR_PORT}';
  SIDECAR_PORT=${SIDECAR_PORT} bash run.sh
"

# Window 3: Web (Next.js)
tmux new-window -t "${SESSION}" -n web "
  cd ${POC_DIR}/web;
  echo '[web] building and starting on :${WEB_PORT}';
  export NEXT_PUBLIC_MOCK_MODE=false;
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
echo "    PersonaPlex UI:  https://<POD-ID>-${PERSONAPLEX_PORT}.proxy.runpod.net (raw fallback)"
echo "    Sidecar:         https://<POD-ID>-${SIDECAR_PORT}.proxy.runpod.net/api/state"
