#!/usr/bin/env bash
set -euo pipefail

URL="http://localhost:8001/app/index.html"
echo "[V2X Lab] Opening frontend at $URL"

if command -v open >/dev/null 2>&1; then
  open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL"
else
  echo "Open this URL in your browser: $URL"
fi
