#!/usr/bin/env bash
# RunPod one-time setup for the VoxReach POC.
# Run from /workspace/voxreach-poc/poc after cloning the repo.

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="$(cd "${POC_DIR}/../.." && pwd)"
MOSHI_DIR="${WORKSPACE}/moshi-rag"

echo "==> POC dir:        ${POC_DIR}"
echo "==> Workspace dir:  ${WORKSPACE}"
echo "==> Moshi-RAG dir:  ${MOSHI_DIR}"

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
echo ""
echo "==> Installing system packages (opus, ffmpeg, tmux, jq) ..."
apt-get update -qq
apt-get install -y -qq libopus-dev ffmpeg tmux jq curl git

# ---------------------------------------------------------------------------
# HF auth check
# ---------------------------------------------------------------------------
echo ""
if [ -z "${HF_TOKEN:-}" ]; then
  echo "!! HF_TOKEN is not set in this pod's environment."
  echo "   1. Generate a token at https://huggingface.co/settings/tokens (read access)."
  echo "   2. Accept the license on https://huggingface.co/kyutai/moshika-rag-pytorch-bf16"
  echo "   3. Accept the license on https://huggingface.co/google/gemma-3-12b-it"
  echo "   4. Set HF_TOKEN as a secret on this RunPod pod, then re-run this script."
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
echo "==> HF_TOKEN is set (length=${#HF_TOKEN})"

# ---------------------------------------------------------------------------
# Python venv + sidecar deps
# ---------------------------------------------------------------------------
echo ""
echo "==> Setting up Python venv for sidecar ..."
cd "${POC_DIR}/sidecar"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet \
  "fastapi>=0.115" \
  "uvicorn[standard]>=0.32" \
  "pydantic>=2.9" \
  "httpx>=0.27" \
  "sse-starlette>=2.1" \
  "python-multipart>=0.0.12"
deactivate

# ---------------------------------------------------------------------------
# Moshi-RAG clone + install
# ---------------------------------------------------------------------------
echo ""
if [ ! -d "${MOSHI_DIR}" ]; then
  echo "==> Cloning kyutai-labs/moshi-rag ..."
  git clone https://github.com/kyutai-labs/moshi-rag "${MOSHI_DIR}"
fi
echo "==> Installing moshi-rag Python package ..."
cd "${MOSHI_DIR}"
pip install --quiet -U "git+https://github.com/kyutai-labs/moshi-rag.git#egg=moshi&subdirectory=moshi"
pip install --quiet rustymimi

# ---------------------------------------------------------------------------
# vLLM (retrieval backend)
# ---------------------------------------------------------------------------
echo ""
echo "==> Installing vLLM ..."
pip install --quiet "vllm>=0.6.4"

# ---------------------------------------------------------------------------
# Pre-pull weights (so first start.sh is fast)
# ---------------------------------------------------------------------------
echo ""
echo "==> Pre-downloading model weights (this is the long step) ..."
python - <<'PY'
import os
from huggingface_hub import snapshot_download

token = os.environ["HUGGING_FACE_HUB_TOKEN"]
for repo in [
    "kyutai/moshika-rag-pytorch-bf16",
    "google/gemma-3-12b-it",
]:
    print(f"   pulling {repo} ...")
    snapshot_download(repo_id=repo, token=token)
print("   done.")
PY

# ---------------------------------------------------------------------------
# Node + web app
# ---------------------------------------------------------------------------
echo ""
if ! command -v node >/dev/null 2>&1; then
  echo "==> Installing Node 20 ..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi
echo "==> Installing web app deps ..."
cd "${POC_DIR}/web"
npm ci --silent

# ---------------------------------------------------------------------------
echo ""
echo "==> Setup complete. Next: bash runpod/start.sh"
