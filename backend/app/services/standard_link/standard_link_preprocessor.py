from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd
from dotenv import load_dotenv

from app.db import ensure_postgis_schema, postgis_available, upsert_standard_links, upsert_standard_nodes

from .coordinate_transformer import to_epsg4326
from .standard_link_file_checker import check_standard_link_files, default_raw_dir
from .standard_link_loader import load_raw_links
from .standard_link_repository import StandardLinkRepository, default_processed_dir
from .standard_node_loader import load_raw_nodes


def _postgis_hint() -> tuple[bool, list[str]]:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    warnings: list[str] = []
    if not postgis_available():
        warnings.append("DATABASE_URL is not configured. PostGIS export skipped.")
        return False, warnings
    ensure_postgis_schema()
    return True, warnings


def preprocess_standard_links(raw_dir: Path | None = None, processed_dir: Path | None = None) -> dict:
    raw_dir = raw_dir or default_raw_dir()
    processed_dir = processed_dir or default_processed_dir()
    processed_dir.mkdir(parents=True, exist_ok=True)

    check = check_standard_link_files(raw_dir)
    if not check["link_files_ready"] or not check["node_files_ready"]:
        raise FileNotFoundError("MOCT_LINK/MOCT_NODE file set is incomplete.")

    raw_links = load_raw_links(raw_dir)
    raw_nodes = load_raw_nodes(raw_dir)
    source_crs = str(raw_links.crs)

    links = raw_links[[
        "LINK_ID", "F_NODE", "T_NODE", "LANES", "ROAD_RANK", "ROAD_TYPE",
        "ROAD_NO", "ROAD_NAME", "MAX_SPD", "LENGTH", "geometry",
    ]].rename(columns={
        "LINK_ID": "link_id",
        "F_NODE": "from_node",
        "T_NODE": "to_node",
        "LANES": "lanes",
        "ROAD_RANK": "road_rank",
        "ROAD_TYPE": "road_type",
        "ROAD_NO": "road_no",
        "ROAD_NAME": "road_name",
        "MAX_SPD": "max_speed",
        "LENGTH": "length_m",
    })
    nodes = raw_nodes[[
        "NODE_ID", "NODE_TYPE", "NODE_NAME", "geometry",
    ]].rename(columns={
        "NODE_ID": "node_id",
        "NODE_TYPE": "node_type",
        "NODE_NAME": "node_name",
    })

    links = to_epsg4326(links)
    nodes = to_epsg4326(nodes)

    links["created_at"] = "preprocessed"
    nodes["created_at"] = "preprocessed"

    links_gpkg = processed_dir / "standard_links.gpkg"
    nodes_gpkg = processed_dir / "standard_nodes.gpkg"
    links_parquet = processed_dir / "standard_links.parquet"
    nodes_parquet = processed_dir / "standard_nodes.parquet"

    links.to_file(links_gpkg, driver="GPKG")
    nodes.to_file(nodes_gpkg, driver="GPKG")
    links.to_parquet(links_parquet)
    nodes.to_parquet(nodes_parquet)

    postgis_ready, warnings = _postgis_hint()
    if postgis_ready:
        upsert_standard_links(links)
        upsert_standard_nodes(nodes)
    meta = {
        "raw_dir": str(raw_dir),
        "processed_dir": str(processed_dir),
        "source_crs": source_crs,
        "target_crs": "EPSG:4326",
        "link_count": int(len(links)),
        "node_count": int(len(nodes)),
        "postgis_ready": postgis_ready,
        "warnings": warnings,
    }
    (processed_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    meta = preprocess_standard_links()
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
