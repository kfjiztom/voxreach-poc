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
#   3. inject_customer_stt.py — embed Kyutai STT (kyutai/stt-2.6b-en) in
#      moshi.server so the customer's spoken audio is transcribed and POSTed
#      to the sidecar with role=customer. VAD-triggered (500ms tail).
#      Set VOXREACH_CUSTOMER_STT_ENABLED=0 to disable without unpatching.

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
echo "[1/3] Default text prompt override"
"${PP_VENV}/bin/python" "${POC_DIR}/runpod/patches/inject_default_prompt.py" "${SERVER_PY}"
echo ""
echo "[2/3] Transcript bridge to sidecar"
"${PP_VENV}/bin/python" "${POC_DIR}/runpod/patches/inject_transcript_bridge.py" "${SERVER_PY}"
echo ""
echo "[3/3] Customer STT (Kyutai) bridge to sidecar"
# Run from the patches dir so customer_stt_helpers.py is importable
( cd "${POC_DIR}/runpod/patches" && \
  "${PP_VENV}/bin/python" inject_customer_stt.py "${SERVER_PY}" )
echo ""
echo "==> All patches applied. Restart the personaplex tmux window to load the new code."
