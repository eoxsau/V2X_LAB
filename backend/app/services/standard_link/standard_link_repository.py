from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd

from app.db import fetch_scalar, postgis_available, read_postgis_gdf

from .standard_link_file_checker import check_standard_link_files, default_raw_dir


def default_processed_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "processed" / "standard_link"


class StandardLinkRepository:
    def __init__(self, processed_dir: Path | None = None):
        self.processed_dir = processed_dir or default_processed_dir()
        self.links_path = self.processed_dir / "standard_links.gpkg"
        self.nodes_path = self.processed_dir / "standard_nodes.gpkg"
        self.links_parquet = self.processed_dir / "standard_links.parquet"
        self.nodes_parquet = self.processed_dir / "standard_nodes.parquet"
        self.metadata_path = self.processed_dir / "metadata.json"
        self._links_cache: gpd.GeoDataFrame | None = None
        self._nodes_cache: gpd.GeoDataFrame | None = None

    def metadata(self) -> dict:
        if self.metadata_path.exists():
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return {}

    def processed_ready(self) -> bool:
        if postgis_available():
            count = fetch_scalar("SELECT count(*) FROM information_schema.tables WHERE table_name = 'standard_links'")
            if count:
                return True
        return self.links_path.exists() and self.nodes_path.exists()

    def load_links(self, force: bool = False) -> gpd.GeoDataFrame:
        if postgis_available():
            return read_postgis_gdf("SELECT link_id, from_node, to_node, road_name, road_rank, road_type, max_speed, length_m, geom FROM standard_links")
        if self._links_cache is None or force:
            if self.links_path.exists():
                self._links_cache = gpd.read_file(self.links_path)
            elif self.links_parquet.exists():
                self._links_cache = gpd.read_parquet(self.links_parquet)
            else:
                raise FileNotFoundError("Processed standard link file not found.")
        return self._links_cache.copy()

    def load_nodes(self, force: bool = False) -> gpd.GeoDataFrame:
        if postgis_available():
            return read_postgis_gdf("SELECT node_id, node_type, node_name, geom FROM standard_nodes")
        if self._nodes_cache is None or force:
            if self.nodes_path.exists():
                self._nodes_cache = gpd.read_file(self.nodes_path)
            elif self.nodes_parquet.exists():
                self._nodes_cache = gpd.read_parquet(self.nodes_parquet)
            else:
                raise FileNotFoundError("Processed standard node file not found.")
        return self._nodes_cache.copy()

    def links_in_bbox(self, minx: float, miny: float, maxx: float, maxy: float) -> gpd.GeoDataFrame:
        if postgis_available():
            return read_postgis_gdf(
                """
                SELECT link_id, from_node, to_node, road_name, road_rank, road_type, max_speed, length_m, geom
                FROM standard_links
                WHERE geom && ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, 4326)
                  AND ST_Intersects(geom, ST_MakeEnvelope(%(minx)s, %(miny)s, %(maxx)s, %(maxy)s, 4326))
                """,
                {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
            )
        gdf = self.load_links()
        return gdf.cx[minx:maxx, miny:maxy].copy()

    def status(self) -> dict:
        raw_status = check_standard_link_files(default_raw_dir())
        meta = self.metadata()
        if postgis_available():
            link_count = int(fetch_scalar("SELECT count(*) FROM standard_links") or 0)
            node_count = int(fetch_scalar("SELECT count(*) FROM standard_nodes") or 0)
        else:
            link_count = meta.get("link_count", 0)
            node_count = meta.get("node_count", 0)
        return {
            "link_files_ready": raw_status["link_files_ready"],
            "node_files_ready": raw_status["node_files_ready"],
            "processed_ready": self.processed_ready(),
            "link_count": link_count,
            "node_count": node_count,
            "crs": meta.get("target_crs"),
            "converted_to_epsg4326": meta.get("target_crs") == "EPSG:4326",
            "postgis_ready": meta.get("postgis_ready", False),
            "warnings": raw_status["warnings"] + meta.get("warnings", []),
            "processed_dir": str(self.processed_dir),
        }
