#!/usr/bin/env bash
# Bring up the VoxReach sidecar locally.
#   - Creates a venv on first run
#   - Installs deps from pyproject.toml
#   - Optionally installs sentence-transformers for the RAG layer
#     (set VOXREACH_RAG=0 to skip and run without semantic search)
#   - Launches uvicorn on :8001 with autoreload

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[sidecar] creating venv (.venv) ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import fastapi" 2>/dev/null; then
  echo "[sidecar] installing core deps ..."
  pip install --quiet --upgrade pip
  pip install --quiet \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" \
    "httpx>=0.27" \
    "sse-starlette>=2.1" \
    "python-multipart>=0.0.12"
fi

# RAG dependencies (sentence-transformers + torch CPU)
# Skip with VOXREACH_RAG=0 — sidecar still works, just without semantic retrieval.
if [ "${VOXREACH_RAG:-1}" = "1" ]; then
  if ! python -c "import sentence_transformers" 2>/dev/null; then
    echo "[sidecar] installing RAG deps (sentence-transformers, ~500MB CPU torch) ..."
    pip install --quiet \
      "sentence-transformers>=3.0" \
      "numpy<2.3"
  fi
fi

PORT="${SIDECAR_PORT:-8001}"
echo "[sidecar] starting on http://0.0.0.0:${PORT}"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT}" --reload
