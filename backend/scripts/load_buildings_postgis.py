"""One-off loader: processed building parquet files -> PostGIS `buildings` table.

Reuses the already-preprocessed parquet datasets in data/processed/buildings/
(no shapefile re-processing, no height re-estimation). Drops the heavy indexes
before bulk insert and recreates them afterwards so a 14.4M-row load stays fast.

Run from the backend/ directory with the project venv:
    .\.venv\Scripts\python.exe scripts\load_buildings_postgis.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db import ensure_postgis_schema, get_psycopg_connection, postgis_available

PROCESSED = Path(__file__).resolve().parents[2] / "data" / "processed" / "buildings"
CHUNK = 250_000

# Indexes dropped during bulk load and recreated afterwards. The UNIQUE
# constraint on ufid is left in place because ON CONFLICT relies on it.
DROP_INDEXES = [
    "idx_buildings_geom",
    "idx_buildings_region_code",
    "idx_buildings_pnu",
    "idx_buildings_ufid",
]
RECREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_buildings_geom ON buildings USING GIST (geom)",
    "CREATE INDEX IF NOT EXISTS idx_buildings_region_code ON buildings (region_code)",
    "CREATE INDEX IF NOT EXISTS idx_buildings_pnu ON buildings (pnu)",
    "CREATE INDEX IF NOT EXISTS idx_buildings_ufid ON buildings (ufid)",
]

INSERT_SQL = """
INSERT INTO buildings
  (ufid, building_name, pnu, ground_floor, underground_floor, height_m, height_source, height_confidence, region_code, geom)
VALUES %s
ON CONFLICT (ufid) DO NOTHING
"""
TEMPLATE = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))"


def run_sql(statements: list[str]) -> None:
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()


def insert_chunk(chunk: gpd.GeoDataFrame) -> int:
    rows = [
        (
            str(row.ufid),
            row.building_name if pd.notna(row.building_name) else None,
            row.pnu if pd.notna(row.pnu) else None,
            float(row.ground_floor) if pd.notna(row.ground_floor) else None,
            float(row.underground_floor) if pd.notna(row.underground_floor) else None,
            float(row.height_m) if pd.notna(row.height_m) else None,
            row.height_source if pd.notna(row.height_source) else None,
            row.height_confidence if pd.notna(row.height_confidence) else None,
            row.region_code if pd.notna(row.region_code) else None,
            row.geometry.wkt if row.geometry is not None else None,
        )
        for row in chunk.itertuples()
    ]
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_SQL, rows, template=TEMPLATE, page_size=2000)
        conn.commit()
    return len(rows)


def main() -> None:
    if not postgis_available():
        raise SystemExit("PostGIS is not available (check POSTGIS_ENABLED / DATABASE_URL / container).")
    ensure_postgis_schema()

    files = sorted(PROCESSED.glob("buildings_*.parquet"))
    print(f"[init] {len(files)} parquet files found in {PROCESSED}", flush=True)

    print("[prep] TRUNCATE buildings", flush=True)
    run_sql(["TRUNCATE buildings RESTART IDENTITY"])

    print(f"[prep] DROP indexes: {DROP_INDEXES}", flush=True)
    run_sql([f"DROP INDEX IF EXISTS {ix}" for ix in DROP_INDEXES])

    total = 0
    t0 = time.time()
    for f in files:
        gdf = gpd.read_parquet(f)
        gdf = gdf.drop_duplicates(subset="ufid", keep="last")
        n = len(gdf)
        for start in range(0, n, CHUNK):
            inserted = insert_chunk(gdf.iloc[start : start + CHUNK])
            total += inserted
            elapsed = time.time() - t0
            rate = total / elapsed if elapsed else 0
            print(
                f"[load] {f.name} {min(start + CHUNK, n)}/{n}  total={total:,}  "
                f"{elapsed:.0f}s  {rate:.0f} rows/s",
                flush=True,
            )
        del gdf

    print("[post] recreating indexes...", flush=True)
    run_sql(RECREATE_INDEXES)
    print("[post] ANALYZE buildings", flush=True)
    run_sql(["ANALYZE buildings"])

    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT region_code) FROM buildings")
            rows, regions = cur.fetchone()
    print(f"[done] buildings rows={rows:,}  regions={regions}  elapsed={time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
