#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR/backend"

echo "[V2X Lab] Starting FastAPI backend on http://localhost:8001"
if [[ "${V2X_RELOAD:-0}" == "1" ]]; then
  RELOAD_FLAG="--reload"
else
  RELOAD_FLAG=""
fi
if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001 $RELOAD_FLAG
else
  exec python3 -m uvicorn main:app --host 127.0.0.1 --port 8001 $RELOAD_FLAG
fi
