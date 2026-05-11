#!/usr/bin/env bash
# RunPod one-time setup for the VoxReach POC.
# Run from /workspace/voxreach-poc/poc after cloning the repo.
#
# Critical: RunPod's container disk is only 20 GB. The HF + pip caches default
# to ~/.cache/X which lives on that disk. Without redirecting them to the
# network volume at /workspace, the model weight downloads will run out of
# space mid-stream. This script handles that automatically.

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# WORKSPACE = where the RunPod network volume is mounted. Convention is /workspace.
# Override only if your pod is configured differently.
WORKSPACE="${WORKSPACE:-/workspace}"
MOSHI_DIR="${WORKSPACE}/moshi-rag"

if [ ! -d "${WORKSPACE}" ]; then
  echo "!! ${WORKSPACE} does not exist. Either your network volume isn't mounted,"
  echo "   or you need to override WORKSPACE=<path> before running this script."
  exit 1
fi

# CUDA wheel index for the torch/torchaudio/torchvision realignment step.
# By default we DON'T set one — PyPI's torch wheels for linux_x86_64 already
# bundle CUDA via nvidia-* dependencies and pip will resolve a consistent
# torch + torchaudio + torchvision family (currently 2.9.x).
#
# Set to a specific PyTorch wheel index (e.g. https://download.pytorch.org/whl/cu128)
# only if your pod has unusual CUDA driver constraints. The cu124 index is
# stuck on torch 2.6 and will DOWNGRADE moshi-rag's torch 2.9.1 — do not use it.
CUDA_INDEX_URL="${CUDA_INDEX_URL:-}"

echo "==> POC dir:        ${POC_DIR}"
echo "==> Workspace dir:  ${WORKSPACE}"
echo "==> Moshi-RAG dir:  ${MOSHI_DIR}"
echo "==> CUDA wheel idx: ${CUDA_INDEX_URL}"

# ---------------------------------------------------------------------------
# Cache redirect: keep big caches on /workspace, NOT on the 20 GB container disk.
# ---------------------------------------------------------------------------
echo ""
echo "==> Redirecting HF + pip caches to /workspace ..."
export HF_HOME="${HF_HOME:-/workspace/.cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/workspace/.cache/pip}"
mkdir -p "${HF_HOME}" "${PIP_CACHE_DIR}"

# Move any existing caches off the container disk
mkdir -p /root/.cache
for sub in huggingface pip; do
  src="/root/.cache/${sub}"
  dst="/workspace/.cache/${sub}"
  if [ -d "${src}" ] && [ ! -L "${src}" ]; then
    echo "    moving ${src} → ${dst}"
    cp -a "${src}/." "${dst}/" 2>/dev/null || true
    rm -rf "${src}"
  fi
  ln -sfn "${dst}" "${src}"
done

# Persist for every future shell on this pod
if ! grep -q 'VoxReach POC cache redirects' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<'EOF'

# VoxReach POC cache redirects — keep big caches off the 20 GB container disk
export HF_HOME=/workspace/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=/workspace/.cache/huggingface
export TRANSFORMERS_CACHE=/workspace/.cache/huggingface
export PIP_CACHE_DIR=/workspace/.cache/pip
EOF
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
echo ""
echo "==> Installing system packages (opus, ffmpeg, tmux, jq) ..."
apt-get update -qq
apt-get install -y -qq libopus-dev ffmpeg tmux jq curl git
apt-get clean
rm -rf /var/lib/apt/lists/*

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
# Upgrade pip globally — base RunPod images often ship with pip from 2024.
# ---------------------------------------------------------------------------
echo ""
echo "==> Upgrading pip ..."
python3 -m pip install --quiet --upgrade pip

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
# Heavy ML venv on /workspace — moshi-rag + vLLM + their torch family.
#
# Without this, `pip install` as root with no venv would dump 6+ GB of
# packages into /usr/lib/python3.X/site-packages on the 20 GB container disk.
# Pinning the venv to /workspace keeps the container disk safe and survives
# pod destruction (the venv stays on the network volume).
# ---------------------------------------------------------------------------
ML_VENV="${WORKSPACE}/.venv/voxreach"
echo ""
echo "==> Setting up heavy ML venv at ${ML_VENV} ..."
mkdir -p "$(dirname "${ML_VENV}")"
if [ ! -d "${ML_VENV}" ]; then
  python3 -m venv "${ML_VENV}"
fi
# shellcheck disable=SC1091
source "${ML_VENV}/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet huggingface_hub

# ---------------------------------------------------------------------------
# Moshi-RAG clone + install (into the ML venv on /workspace)
# ---------------------------------------------------------------------------
echo ""
if [ ! -d "${MOSHI_DIR}" ]; then
  echo "==> Cloning kyutai-labs/moshi-rag ..."
  git clone https://github.com/kyutai-labs/moshi-rag "${MOSHI_DIR}"
fi
echo "==> Installing moshi-rag Python package into ${ML_VENV} ..."
cd "${MOSHI_DIR}"
pip install --quiet -U "git+https://github.com/kyutai-labs/moshi-rag.git#egg=moshi&subdirectory=moshi"
pip install --quiet rustymimi

# ---------------------------------------------------------------------------
# Realign torchaudio + torchvision to whatever torch moshi-rag pulled in.
# Without this you get a runtime error: torchaudio pinned to torch 2.4.1 but
# torch is now 2.9.x.
# ---------------------------------------------------------------------------
echo ""
echo "==> Realigning torchaudio + torchvision to installed torch ..."
INSTALLED_TORCH=$(python -c "import torch; print(torch.__version__.split('+')[0])")
TORCH_MM=$(echo "${INSTALLED_TORCH}" | cut -d. -f1-2)
# torchvision's minor offset from torch is +15 (torch 2.9 → torchvision 0.24).
# This convention has held since torch 1.0.
TORCH_MIN=$(echo "${INSTALLED_TORCH}" | cut -d. -f2)
TV_MIN=$((TORCH_MIN + 15))
TV_MIN_NEXT=$((TV_MIN + 1))
TORCH_MIN_NEXT=$((TORCH_MIN + 1))

echo "    torch is at ${INSTALLED_TORCH} — pinning audio to ${TORCH_MM}.x and vision to 0.${TV_MIN}.x"
PIP_ARGS=(
  --upgrade --force-reinstall
  "torch==${INSTALLED_TORCH}"
  "torchaudio>=${TORCH_MM},<2.${TORCH_MIN_NEXT}"
  "torchvision>=0.${TV_MIN},<0.${TV_MIN_NEXT}"
)
if [ -n "${CUDA_INDEX_URL}" ]; then
  echo "    using PyTorch wheel index: ${CUDA_INDEX_URL}"
  pip install --index-url "${CUDA_INDEX_URL}" "${PIP_ARGS[@]}"
else
  pip install "${PIP_ARGS[@]}"
fi

python - <<'PY'
import torch, torchaudio, torchvision
print(f"   torch       {torch.__version__}")
print(f"   torchaudio  {torchaudio.__version__}")
print(f"   torchvision {torchvision.__version__}")
print(f"   cuda        {torch.cuda.is_available()}")
assert torch.cuda.is_available(), "CUDA not available — check pod GPU and CUDA_INDEX_URL"
PY

# ---------------------------------------------------------------------------
# vLLM (retrieval backend) — also into the ML venv
# ---------------------------------------------------------------------------
echo ""
echo "==> Installing vLLM into ${ML_VENV} ..."
pip install --quiet "vllm>=0.6.4"

# vLLM pulls numpy 2.4 (latest) but moshi pins numpy<2.3. Pin numpy back
# to a range that satisfies both. Without this, `import moshi` warns and
# can fail at runtime depending on which numpy API surfaces.
echo "==> Realigning numpy to satisfy moshi's constraint (>=1.26,<2.3) ..."
pip install --quiet --upgrade "numpy>=1.26,<2.3"

# ---------------------------------------------------------------------------
# Pre-pull weights (so first start.sh is fast) — runs inside ML venv
# ---------------------------------------------------------------------------
echo ""
echo "==> Pre-downloading model weights to ${HF_HOME} (this is the long step) ..."
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

deactivate

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
echo "==> Disk usage after setup:"
df -h / /workspace 2>/dev/null | grep -E 'Filesystem|/$|/workspace'
echo ""
echo "==> Setup complete. Next: bash runpod/start.sh"
