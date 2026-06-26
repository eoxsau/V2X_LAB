from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import psycopg2
from psycopg2.extras import Json, execute_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


def postgis_enabled() -> bool:
    return os.getenv("POSTGIS_ENABLED", "false").lower() == "true"


@lru_cache(maxsize=1)
def get_engine() -> Engine | None:
    url = database_url()
    if not url:
        return None
    return create_engine(url, future=True, pool_pre_ping=True)


def get_psycopg_connection():
    url = database_url()
    if not url:
        return None
    return psycopg2.connect(url)


def postgis_available() -> bool:
    return postgis_enabled() and bool(database_url())


def ensure_postgis_schema() -> bool:
    engine = get_engine()
    if engine is None or not postgis_enabled():
        return False
    ddl = """
    CREATE EXTENSION IF NOT EXISTS postgis;

    CREATE TABLE IF NOT EXISTS standard_links (
      id BIGSERIAL PRIMARY KEY,
      link_id TEXT UNIQUE NOT NULL,
      from_node TEXT,
      to_node TEXT,
      road_name TEXT,
      road_rank TEXT,
      road_type TEXT,
      max_speed DOUBLE PRECISION,
      length_m DOUBLE PRECISION,
      geom geometry(LineString, 4326),
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_standard_links_geom ON standard_links USING GIST (geom);
    CREATE INDEX IF NOT EXISTS idx_standard_links_link_id ON standard_links (link_id);

    CREATE TABLE IF NOT EXISTS standard_nodes (
      id BIGSERIAL PRIMARY KEY,
      node_id TEXT UNIQUE NOT NULL,
      node_type TEXT,
      node_name TEXT,
      geom geometry(Point, 4326),
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_standard_nodes_geom ON standard_nodes USING GIST (geom);
    CREATE INDEX IF NOT EXISTS idx_standard_nodes_node_id ON standard_nodes (node_id);

    CREATE TABLE IF NOT EXISTS buildings (
      id BIGSERIAL PRIMARY KEY,
      ufid TEXT UNIQUE NOT NULL,
      building_name TEXT,
      pnu TEXT,
      ground_floor DOUBLE PRECISION,
      underground_floor DOUBLE PRECISION,
      height_m DOUBLE PRECISION,
      height_source TEXT,
      height_confidence TEXT,
      region_code TEXT,
      geom geometry(Geometry, 4326),
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_buildings_geom ON buildings USING GIST (geom);
    CREATE INDEX IF NOT EXISTS idx_buildings_ufid ON buildings (ufid);
    CREATE INDEX IF NOT EXISTS idx_buildings_region_code ON buildings (region_code);
    CREATE INDEX IF NOT EXISTS idx_buildings_pnu ON buildings (pnu);

    CREATE TABLE IF NOT EXISTS traffic_snapshots (
      id BIGSERIAL PRIMARY KEY,
      link_id TEXT NOT NULL,
      speed_kph DOUBLE PRECISION,
      travel_time_s DOUBLE PRECISION,
      congestion_score DOUBLE PRECISION,
      collected_at TIMESTAMPTZ NOT NULL,
      raw_payload JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_traffic_snapshots_link_id ON traffic_snapshots (link_id);
    CREATE INDEX IF NOT EXISTS idx_traffic_snapshots_collected_at ON traffic_snapshots (collected_at DESC);

    CREATE TABLE IF NOT EXISTS edge_mappings (
      id BIGSERIAL PRIMARY KEY,
      standard_link_id TEXT,
      osm_edge_id TEXT,
      sumo_edge_id TEXT,
      confidence DOUBLE PRECISION,
      match_distance_m DOUBLE PRECISION,
      created_at TIMESTAMPTZ DEFAULT now(),
      UNIQUE (standard_link_id, osm_edge_id, sumo_edge_id)
    );

    CREATE TABLE IF NOT EXISTS network_nodes (
      id TEXT PRIMARY KEY,
      name TEXT,
      node_type TEXT,
      lat DOUBLE PRECISION,
      lng DOUBLE PRECISION,
      capacity DOUBLE PRECISION,
      load DOUBLE PRECISION,
      congestion_score DOUBLE PRECISION,
      edge_latency_ms DOUBLE PRECISION,
      coverage_radius_m DOUBLE PRECISION,
      source TEXT,
      geom geometry(Point, 4326),
      created_at TIMESTAMPTZ DEFAULT now(),
      updated_at TIMESTAMPTZ DEFAULT now()
    );
    ALTER TABLE network_nodes ADD COLUMN IF NOT EXISTS congestion_score DOUBLE PRECISION;
    ALTER TABLE network_nodes ADD COLUMN IF NOT EXISTS antenna_height_m DOUBLE PRECISION;
    ALTER TABLE network_nodes ADD COLUMN IF NOT EXISTS antenna_placement TEXT;
    ALTER TABLE network_nodes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE network_nodes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    CREATE INDEX IF NOT EXISTS idx_network_nodes_geom ON network_nodes USING GIST (geom);

    CREATE TABLE IF NOT EXISTS simulation_runs (
      id BIGSERIAL PRIMARY KEY,
      origin JSONB,
      destination JSONB,
      mode TEXT,
      started_at TIMESTAMPTZ DEFAULT now(),
      ended_at TIMESTAMPTZ,
      metrics_json JSONB
    );
    ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS scenario_id TEXT;
    ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS seed BIGINT;
    ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS batch_id TEXT;
    ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS sheet_id TEXT;
    ALTER TABLE simulation_runs ADD COLUMN IF NOT EXISTS sheet_name TEXT;
    CREATE INDEX IF NOT EXISTS idx_simulation_runs_batch_id ON simulation_runs (batch_id);
    CREATE INDEX IF NOT EXISTS idx_simulation_runs_started_at ON simulation_runs (started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_simulation_runs_sheet_id ON simulation_runs (sheet_id);
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
    return True


def read_postgis_gdf(sql: str, params: dict | None = None, geom_col: str = "geom") -> gpd.GeoDataFrame:
    engine = get_engine()
    if engine is None:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    try:
        gdf = gpd.read_postgis(sql, engine, geom_col=geom_col, params=params or {})
        # The file-based (parquet/gpkg) read paths name the active geometry column
        # "geometry"; PostGIS reads name it after geom_col ("geom"). Normalize here so
        # every consumer (matchers, bbox endpoints, itertuples access) is source-agnostic.
        # Rename by geom_col (not row data) so it also applies to zero-row results.
        if geom_col != "geometry" and geom_col in gdf.columns:
            gdf = gdf.rename_geometry("geometry")
        return gdf
    except Exception as exc:
        print(f"[db] read_postgis_gdf failed: {exc}", flush=True)
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def fetch_scalar(sql: str, params: dict | None = None):
    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    except Exception:
        return None


def fetch_one_dict(sql: str, params: dict | None = None) -> dict | None:
    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row else None
    except Exception:
        return None


def fetch_all_dicts(sql: str, params: dict | None = None) -> list[dict]:
    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(sql), params or {}).mappings().all()
            return [dict(row) for row in rows]
    except Exception:
        return []


def upsert_standard_links(gdf: gpd.GeoDataFrame) -> int:
    if not postgis_available() or gdf.empty:
        return 0
    ensure_postgis_schema()
    rows = [
        (
            str(row.link_id),
            str(row.from_node) if pd.notna(row.from_node) else None,
            str(row.to_node) if pd.notna(row.to_node) else None,
            row.road_name if pd.notna(row.road_name) else None,
            str(row.road_rank) if pd.notna(row.road_rank) else None,
            str(row.road_type) if pd.notna(row.road_type) else None,
            float(row.max_speed) if pd.notna(row.max_speed) else None,
            float(row.length_m) if pd.notna(row.length_m) else None,
            row.geometry.wkt if row.geometry is not None else None,
        )
        for row in gdf.itertuples()
    ]
    sql = """
    INSERT INTO standard_links
      (link_id, from_node, to_node, road_name, road_rank, road_type, max_speed, length_m, geom)
    VALUES %s
    ON CONFLICT (link_id) DO UPDATE SET
      from_node = EXCLUDED.from_node,
      to_node = EXCLUDED.to_node,
      road_name = EXCLUDED.road_name,
      road_rank = EXCLUDED.road_rank,
      road_type = EXCLUDED.road_type,
      max_speed = EXCLUDED.max_speed,
      length_m = EXCLUDED.length_m,
      geom = EXCLUDED.geom
    """
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))",
                page_size=5000,
            )
        conn.commit()
    return len(rows)


def upsert_standard_nodes(gdf: gpd.GeoDataFrame) -> int:
    if not postgis_available() or gdf.empty:
        return 0
    ensure_postgis_schema()
    rows = [
        (
            str(row.node_id),
            str(row.node_type) if pd.notna(row.node_type) else None,
            row.node_name if pd.notna(row.node_name) else None,
            row.geometry.wkt if row.geometry is not None else None,
        )
        for row in gdf.itertuples()
    ]
    sql = """
    INSERT INTO standard_nodes (node_id, node_type, node_name, geom)
    VALUES %s
    ON CONFLICT (node_id) DO UPDATE SET
      node_type = EXCLUDED.node_type,
      node_name = EXCLUDED.node_name,
      geom = EXCLUDED.geom
    """
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                sql,
                rows,
                template="(%s,%s,%s,ST_GeomFromText(%s,4326))",
                page_size=5000,
            )
        conn.commit()
    return len(rows)


def upsert_buildings(gdf: gpd.GeoDataFrame) -> int:
    if not postgis_available() or gdf.empty:
        return 0
    ensure_postgis_schema()
    # Source shapefiles occasionally contain duplicate UFIDs within a single
    # region; execute_values' ON CONFLICT batch fails ("cannot affect row a
    # second time") if the same key appears twice in one VALUES list, so keep
    # only the last occurrence per ufid.
    rows_by_ufid = {
        str(row.ufid): (
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
        for row in gdf.itertuples()
    }
    rows = list(rows_by_ufid.values())
    sql = """
    INSERT INTO buildings
      (ufid, building_name, pnu, ground_floor, underground_floor, height_m, height_source, height_confidence, region_code, geom)
    VALUES %s
    ON CONFLICT (ufid) DO UPDATE SET
      building_name = EXCLUDED.building_name,
      pnu = EXCLUDED.pnu,
      ground_floor = EXCLUDED.ground_floor,
      underground_floor = EXCLUDED.underground_floor,
      height_m = EXCLUDED.height_m,
      height_source = EXCLUDED.height_source,
      height_confidence = EXCLUDED.height_confidence,
      region_code = EXCLUDED.region_code,
      geom = EXCLUDED.geom
    """
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                sql,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_GeomFromText(%s,4326))",
                page_size=2000,
            )
        conn.commit()
    return len(rows)


def replace_traffic_snapshots(rows: list[dict]) -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    prepared = [
        (
            str(item["link_id"]),
            float(item["speed_kph"]) if item.get("speed_kph") is not None else None,
            float(item["travel_time_s"]) if item.get("travel_time_s") is not None else None,
            float(item["congestion_score"]) if item.get("congestion_score") is not None else None,
            item["collected_at"],
            Json(item.get("raw_payload") or {}),
        )
        for item in rows
    ]
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM traffic_snapshots WHERE collected_at < now() - interval '7 days'")
            execute_values(
                cur,
                """
                INSERT INTO traffic_snapshots
                  (link_id, speed_kph, travel_time_s, congestion_score, collected_at, raw_payload)
                VALUES %s
                """,
                prepared,
                template="(%s,%s,%s,%s,%s,%s)",
                page_size=5000,
            )
        conn.commit()
    return len(prepared)


def replace_edge_mappings(rows: list[dict]) -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    prepared = [
        (
            item.get("standard_link_id"),
            item.get("osm_edge_id"),
            item.get("sumo_edge_id"),
            float(item.get("confidence", 0.0)),
            float(item.get("match_distance_m", 0.0)) if item.get("match_distance_m") is not None else None,
        )
        for item in rows
    ]
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO edge_mappings
                  (standard_link_id, osm_edge_id, sumo_edge_id, confidence, match_distance_m)
                VALUES %s
                ON CONFLICT (standard_link_id, osm_edge_id, sumo_edge_id) DO UPDATE SET
                  confidence = EXCLUDED.confidence,
                  match_distance_m = EXCLUDED.match_distance_m,
                  created_at = now()
                """,
                prepared,
                template="(%s,%s,%s,%s,%s)",
                page_size=5000,
            )
        conn.commit()
    return len(prepared)


def upsert_network_nodes(rows: list[dict]) -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    prepared = [
        (
            item["id"],
            item.get("name") or item["id"],
            item.get("type"),
            float(item["lat"]),
            float(item["lng"]),
            float(item.get("capacity", 100.0)),
            float(item.get("load", 0.0)),
            float(item.get("congestion_score", 0.0)),
            float(item.get("edge_latency_ms", 0.0)),
            float(item.get("coverage_radius_m", 0.0)),
            item.get("source", "synthetic"),
            float(item["lng"]),
            float(item["lat"]),
        )
        for item in rows
    ]
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO network_nodes
                  (id, name, node_type, lat, lng, capacity, load, congestion_score, edge_latency_ms, coverage_radius_m, source, geom)
                VALUES %s
                ON CONFLICT (id) DO UPDATE SET
                  name = EXCLUDED.name,
                  node_type = EXCLUDED.node_type,
                  lat = EXCLUDED.lat,
                  lng = EXCLUDED.lng,
                  capacity = EXCLUDED.capacity,
                  load = EXCLUDED.load,
                  congestion_score = EXCLUDED.congestion_score,
                  edge_latency_ms = EXCLUDED.edge_latency_ms,
                  coverage_radius_m = EXCLUDED.coverage_radius_m,
                  source = EXCLUDED.source,
                  geom = EXCLUDED.geom,
                  updated_at = now()
                """,
                prepared,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326))",
                page_size=1000,
            )
        conn.commit()
    return len(prepared)


def fetch_network_nodes(source: str | None = None) -> list[dict]:
    if not postgis_available():
        return []
    ensure_postgis_schema()
    sql = """
        SELECT id, name, node_type, lat, lng, capacity, load, congestion_score,
               edge_latency_ms, coverage_radius_m, source,
               antenna_height_m, antenna_placement
        FROM network_nodes
    """
    params: dict = {}
    if source:
        sql += " WHERE source = :source"
        params["source"] = source
    sql += " ORDER BY created_at ASC"
    return fetch_all_dicts(sql, params)


def count_network_nodes(node_type: str, source: str) -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    return int(fetch_scalar(
        "SELECT count(*) FROM network_nodes WHERE node_type = :node_type AND source = :source",
        {"node_type": node_type, "source": source},
    ) or 0)


def max_user_station_number(prefix: str = "기지국") -> int:
    """Return the highest integer suffix used in user-created station names like '기지국 N'."""
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    result = fetch_scalar(
        "SELECT MAX(CAST(SUBSTRING(name FROM :pattern) AS INTEGER)) "
        "FROM network_nodes WHERE source = 'user_created' AND name ~ :regex",
        {"pattern": f"^{prefix} (\\d+)$", "regex": f"^{prefix} [0-9]+$"},
    )
    return int(result) if result is not None else 0


def insert_network_node(node: dict) -> dict | None:
    if not postgis_available():
        return None
    ensure_postgis_schema()
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO network_nodes
                  (id, name, node_type, lat, lng, capacity, load, congestion_score, edge_latency_ms,
                   coverage_radius_m, source, antenna_height_m, antenna_placement, geom)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))
                """,
                (
                    node["id"],
                    node["name"],
                    node["node_type"],
                    float(node["lat"]),
                    float(node["lng"]),
                    float(node.get("capacity", 100.0)),
                    float(node.get("load", 0.0)),
                    float(node.get("congestion_score", 0.0)),
                    float(node.get("edge_latency_ms", 5.0)),
                    float(node.get("coverage_radius_m", 500.0)),
                    node.get("source", "user_created"),
                    float(node["antenna_height_m"]) if node.get("antenna_height_m") is not None else None,
                    node.get("antenna_placement"),
                    float(node["lng"]),
                    float(node["lat"]),
                ),
            )
        conn.commit()
    return node


def update_network_node_placement(
    node_id: str,
    lat: float,
    lng: float,
    antenna_height_m: float,
    antenna_placement: str,
) -> bool:
    """기지국의 좌표와 안테나 높이를 갱신한다."""
    if not postgis_available():
        return False
    ensure_postgis_schema()
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE network_nodes
                SET lat = %s, lng = %s,
                    antenna_height_m = %s, antenna_placement = %s,
                    geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    updated_at = now()
                WHERE id = %s
                """,
                (lat, lng, antenna_height_m, antenna_placement, lng, lat, node_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def delete_network_node(node_id: str) -> bool:
    if not postgis_available():
        return False
    ensure_postgis_schema()
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM network_nodes WHERE id = %s", (node_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def delete_user_created_network_nodes() -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM network_nodes WHERE source = 'user_created'")
            count = cur.rowcount
        conn.commit()
    return count


def delete_synthetic_network_nodes() -> int:
    if not postgis_available():
        return 0
    ensure_postgis_schema()
    with get_psycopg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM network_nodes WHERE source = 'synthetic'")
            count = cur.rowcount
        conn.commit()
    return count


def create_simulation_run(
    origin: dict,
    destination: dict,
    mode: str,
    *,
    scenario_id: str | None = None,
    seed: int | None = None,
    batch_id: str | None = None,
    sheet_id: str | None = None,
    sheet_name: str | None = None,
) -> int | None:
    if not postgis_available():
        return None
    ensure_postgis_schema()
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO simulation_runs (origin, destination, mode, scenario_id, seed, batch_id, sheet_id, sheet_name)
                VALUES (:origin, :destination, :mode, :scenario_id, :seed, :batch_id, :sheet_id, :sheet_name)
                RETURNING id
                """
            ),
            {
                "origin": json.dumps(origin),
                "destination": json.dumps(destination),
                "mode": mode,
                "scenario_id": scenario_id,
                "seed": seed,
                "batch_id": batch_id,
                "sheet_id": sheet_id,
                "sheet_name": sheet_name,
            },
        ).first()
    return int(row[0]) if row else None


def finish_simulation_run(run_id: int | None, metrics_json: dict) -> None:
    """Merge (not overwrite) metrics_json and stamp ended_at.

    This can be called more than once for the same run — e.g. once right at
    vehicle-arrival time with sheet-tagged sim_logs/sim_history (frontend-only
    data the backend has no other way to see), and again later from a generic
    reset/stop path with whatever _state still holds. JSONB `||` concatenation
    merges keys (new call's keys win on overlap) instead of clobbering whatever
    an earlier, more specific call already saved.
    """
    if not postgis_available() or not run_id:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE simulation_runs
                SET ended_at = now(),
                    metrics_json = COALESCE(metrics_json, '{}'::jsonb) || CAST(:metrics_json AS jsonb)
                WHERE id = :id
                """
            ),
            {"id": run_id, "metrics_json": json.dumps(metrics_json)},
        )


def list_simulation_runs(limit: int = 50, offset: int = 0) -> list[dict]:
    """Most-recent-first page of past simulation runs (for team-shared run history).

    Returns [] (not an error) when the DB isn't connected — callers should
    check postgis_available() first to distinguish "no DB" from "no runs yet".
    """
    if not postgis_available():
        return []
    ensure_postgis_schema()
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, origin, destination, mode, scenario_id, seed, batch_id,
                       sheet_id, sheet_name, started_at, ended_at, metrics_json
                FROM simulation_runs
                ORDER BY started_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        ).mappings().all()
    return [dict(r) for r in rows]


def list_simulation_runs_by_sheet(sheet_id: str, limit: int = 50) -> list[dict]:
    """All past runs tagged with a given sheet_id, most-recent-first — lets the
    Dashboard/Report tabs pull a sheet's durable DB history independent of
    whatever happens to still be in this browser's localStorage."""
    if not postgis_available():
        return []
    ensure_postgis_schema()
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, origin, destination, mode, scenario_id, seed, batch_id,
                       sheet_id, sheet_name, started_at, ended_at, metrics_json
                FROM simulation_runs
                WHERE sheet_id = :sheet_id
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            {"sheet_id": sheet_id, "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]
