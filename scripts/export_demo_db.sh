#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/data/processed/demo"

DB_URL="${DATABASE_URL:-postgresql://v2x_lab:v2x_lab@localhost:5432/v2x_lab}"
OUT_FILE="${1:-$ROOT_DIR/data/processed/demo/v2x_lab_demo.dump}"

echo "[V2X Lab] Exporting demo DB to $OUT_FILE"
pg_dump --format=custom --no-owner --no-privileges --dbname="$DB_URL" --file="$OUT_FILE"
