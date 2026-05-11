#!/usr/bin/env bash
# RunPod one-time setup for the VoxReach POC (PersonaPlex variant).
#
# Simpler than the parked MoshiRAG variant — single AI service, no vLLM,
# no separate retrieval LLM, no torch/vllm/transformers dep cascade.
#
# Storage: onboard (container disk + /root) for speed. PersonaPlex weights
# (~14 GB) + venvs (~5 GB) + base image (~12 GB) fit comfortably on RunPod's
# default 30 GB container disk.

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Onboard install paths (no network volume dependency)
PP_VENV="${PP_VENV:-/opt/voxreach-personaplex}"
SIDECAR_VENV="${POC_DIR}/sidecar/.venv"
PERSONAPLEX_REPO="${PERSONAPLEX_REPO:-/opt/personaplex}"

export HF_HOME="${HF_HOME:-/root/.cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/root/.cache/pip}"

echo "==> POC dir:           ${POC_DIR}"
echo "==> PersonaPlex repo:  ${PERSONAPLEX_REPO}"
echo "==> PersonaPlex venv:  ${PP_VENV}"
echo "==> Sidecar venv:      ${SIDECAR_VENV}"
echo "==> HF cache:          ${HF_HOME}"
echo "==> pip cache:         ${PIP_CACHE_DIR}"

mkdir -p "${HF_HOME}" "${PIP_CACHE_DIR}"

# Persist cache locations for every shell on this pod
if ! grep -q 'VoxReach POC cache redirects' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<'EOF'

# VoxReach POC cache redirects (PersonaPlex variant — onboard storage)
export HF_HOME=/root/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface
export TRANSFORMERS_CACHE=/root/.cache/huggingface
export PIP_CACHE_DIR=/root/.cache/pip
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
  echo "   1. Generate a token at https://huggingface.co/settings/tokens"
  echo "   2. Accept the license at https://huggingface.co/nvidia/personaplex-7b-v1"
  echo "   3. Set HF_TOKEN as a secret on this RunPod pod, then re-run this script."
  exit 1
fi
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
echo "==> HF_TOKEN is set (length=${#HF_TOKEN})"

# ---------------------------------------------------------------------------
# Upgrade pip globally
# ---------------------------------------------------------------------------
echo ""
echo "==> Upgrading pip ..."
python3 -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# Sidecar venv (lightweight — FastAPI + uvicorn + sse_starlette)
# ---------------------------------------------------------------------------
echo ""
echo "==> Setting up sidecar venv at ${SIDECAR_VENV} ..."
mkdir -p "$(dirname "${SIDECAR_VENV}")"
if [ ! -d "${SIDECAR_VENV}" ]; then
  python3 -m venv "${SIDECAR_VENV}"
fi
# shellcheck disable=SC1091
source "${SIDECAR_VENV}/bin/activate"
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
# Clone NVIDIA/personaplex repo (the moshi fork with PersonaPlex defaults)
# ---------------------------------------------------------------------------
echo ""
if [ ! -d "${PERSONAPLEX_REPO}" ]; then
  echo "==> Cloning NVIDIA/personaplex to ${PERSONAPLEX_REPO} ..."
  mkdir -p "$(dirname "${PERSONAPLEX_REPO}")"
  git clone https://github.com/NVIDIA/personaplex "${PERSONAPLEX_REPO}"
else
  echo "==> NVIDIA/personaplex already cloned at ${PERSONAPLEX_REPO}, pulling latest ..."
  (cd "${PERSONAPLEX_REPO}" && git pull --ff-only) || true
fi

# ---------------------------------------------------------------------------
# PersonaPlex venv — install NVIDIA's fork of moshi (NOT PyPI's vanilla moshi)
# ---------------------------------------------------------------------------
echo ""
echo "==> Setting up PersonaPlex venv at ${PP_VENV} ..."
mkdir -p "$(dirname "${PP_VENV}")"
if [ ! -d "${PP_VENV}" ]; then
  python3 -m venv "${PP_VENV}"
fi
# shellcheck disable=SC1091
source "${PP_VENV}/bin/activate"
pip install --quiet --upgrade pip
# Install NVIDIA's moshi fork from the cloned repo. The trailing /. matters —
# it tells pip "install from the local moshi/ subdirectory of the repo".
echo "==> Installing NVIDIA moshi fork from ${PERSONAPLEX_REPO}/moshi ..."
(cd "${PERSONAPLEX_REPO}" && pip install --quiet "moshi/.")
# Companion deps for weight downloads + optional CPU offload
pip install --quiet huggingface_hub hf_transfer accelerate

# Quick CUDA sanity — fail fast if torch can't talk to the GPU
python - <<'PY'
import torch
print(f"   torch       {torch.__version__}")
print(f"   cuda        {torch.cuda.is_available()}")
if not torch.cuda.is_available():
    raise SystemExit("ERROR: torch.cuda.is_available() is False — wrong CUDA wheels for this pod's driver")
print(f"   GPU         {torch.cuda.get_device_name(0)}")
print(f"   VRAM        {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
PY

# ---------------------------------------------------------------------------
# Pre-pull PersonaPlex weights
# ---------------------------------------------------------------------------
echo ""
echo "==> Pre-downloading PersonaPlex weights to ${HF_HOME} (~14 GB) ..."
python - <<'PY'
import os
from huggingface_hub import snapshot_download
print("   pulling nvidia/personaplex-7b-v1 ...")
snapshot_download(repo_id="nvidia/personaplex-7b-v1", token=os.environ["HUGGING_FACE_HUB_TOKEN"])
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
df -h / 2>/dev/null | head -2
echo ""
echo "==> Setup complete. Next: bash runpod/start.sh"
