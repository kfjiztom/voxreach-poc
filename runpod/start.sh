#!/usr/bin/env bash
# Start the four services for the VoxReach POC inside a tmux session.
# Attach with:  tmux attach -t voxreach

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(cd "${POC_DIR}/../.." && pwd)"
MOSHI_DIR="${WORKSPACE}/moshi-rag"

SESSION="voxreach"

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "==> tmux session '${SESSION}' already running. Attach with 'tmux attach -t ${SESSION}'."
  echo "    To restart, run: tmux kill-session -t ${SESSION} && bash $0"
  exit 0
fi

if [ -z "${HF_TOKEN:-}" ]; then
  echo "!! HF_TOKEN not set. Run setup.sh first or export HF_TOKEN."
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

VLLM_PORT="${VLLM_PORT:-8002}"
MOSHI_PORT="${MOSHI_PORT:-8998}"
SIDECAR_PORT="${SIDECAR_PORT:-8001}"
WEB_PORT="${WEB_PORT:-3001}"

echo "==> Starting tmux session '${SESSION}' with 4 windows."

# Window 1: vLLM retrieval backend
tmux new-session -d -s "${SESSION}" -n vllm "
  echo '[vllm] starting Gemma-3-12B on :${VLLM_PORT}';
  python -m vllm.entrypoints.openai.api_server \
    --model google/gemma-3-12b-it \
    --port ${VLLM_PORT} \
    --gpu-memory-utilization 0.55 \
    --max-model-len 4096
"

# Window 2: Moshi-RAG full-duplex speech server
tmux new-window -t "${SESSION}" -n moshi "
  cd ${MOSHI_DIR};
  echo '[moshi] waiting for vLLM ...';
  until curl -fs http://localhost:${VLLM_PORT}/v1/models >/dev/null; do sleep 2; done;
  echo '[moshi] vLLM ready, launching moshi server';
  export LLM_BASE_URL=http://localhost:${VLLM_PORT}/v1;
  export SYSTEM_PROMPT_FILE=${POC_DIR}/persona/vox_system_prompt.md;
  export KNOWLEDGE_FILE=${POC_DIR}/knowledge/hearth_and_pass.json;
  export SIDECAR_URL=http://localhost:${SIDECAR_PORT};
  SSL_DIR=\$(mktemp -d);
  python -m moshi.moshi.server --hf-repo kyutai/moshika-rag-pytorch-bf16 --ssl \"\$SSL_DIR\" --port ${MOSHI_PORT}
"

# Window 3: Sidecar
tmux new-window -t "${SESSION}" -n sidecar "
  cd ${POC_DIR}/sidecar;
  source .venv/bin/activate;
  echo '[sidecar] starting on :${SIDECAR_PORT}';
  SIDECAR_PORT=${SIDECAR_PORT} bash run.sh
"

# Window 4: Web
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
echo "    Web demo:   https://<POD-ID>-${WEB_PORT}.proxy.runpod.net"
echo "    Moshi UI:   https://<POD-ID>-${MOSHI_PORT}.proxy.runpod.net (fallback, raw client)"
echo "    Sidecar:    https://<POD-ID>-${SIDECAR_PORT}.proxy.runpod.net/api/state"
