#!/usr/bin/env bash
# Apply VoxReach patches to NVIDIA's PersonaPlex install.
#
# Currently patches:
#   1. moshi/server.py — adds MOSHI_DEFAULT_TEXT_PROMPT_FILE env-var support
#      so the Vox/Hearth&Pass persona auto-loads when the client sends an
#      empty text_prompt.
#
# Idempotent — re-running is a no-op if patches are already applied.

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
"${PP_VENV}/bin/python" "${POC_DIR}/runpod/patches/inject_default_prompt.py" "${SERVER_PY}"
echo "==> Done."
