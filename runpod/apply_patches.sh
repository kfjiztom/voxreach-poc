#!/usr/bin/env bash
# Apply VoxReach patches to NVIDIA's PersonaPlex install.
#
# Patches applied (all idempotent — safe to re-run):
#   1. inject_default_prompt.py — MOSHI_DEFAULT_TEXT_PROMPT_FILE override so
#      the Vox/Hearth&Pass persona auto-loads on every connection (ignores
#      the PersonaPlex UI's auto-filled prompt).
#   2. inject_transcript_bridge.py — POST every call_start, call_end, and
#      Vox text token to the VoxReach sidecar at VOXREACH_SIDECAR_URL.
#      Enables real-time order extraction from the live conversation.

set -euo pipefail

POC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PP_VENV="${PP_VENV:-/workspace/.venv/voxreach-personaplex}"

if [ ! -d "${PP_VENV}" ]; then
  echo "!! PP_VENV not found at ${PP_VENV}. Run setup.sh first."
  exit 1
fi

# Find the installed moshi server.py (glob handles minor python version variance)
SERVER_PY=$(ls "${PP_VENV}"/lib/python*/site-packages/moshi/server.py 2>/dev/null | head -1)
if [ -z "${SERVER_PY}" ]; then
  echo "!! Could not locate moshi/server.py inside ${PP_VENV}"
  exit 1
fi

echo "==> Patching ${SERVER_PY}"
echo ""
echo "[1/2] Default text prompt override"
"${PP_VENV}/bin/python" "${POC_DIR}/runpod/patches/inject_default_prompt.py" "${SERVER_PY}"
echo ""
echo "[2/2] Transcript bridge to sidecar"
"${PP_VENV}/bin/python" "${POC_DIR}/runpod/patches/inject_transcript_bridge.py" "${SERVER_PY}"
echo ""
echo "==> All patches applied. Restart the personaplex tmux window to load the new code."
