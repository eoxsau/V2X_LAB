#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_URL="${DATABASE_URL:-postgresql://v2x_lab:v2x_lab@localhost:5432/v2x_lab}"
DUMP_FILE="${1:-$ROOT_DIR/data/processed/demo/v2x_lab_demo.dump}"

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "[V2X Lab] Demo dump not found: $DUMP_FILE" >&2
  exit 1
fi

echo "[V2X Lab] Restoring demo DB from $DUMP_FILE"
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DB_URL" "$DUMP_FILE"
