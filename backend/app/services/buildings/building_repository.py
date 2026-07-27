from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from app.db import fetch_scalar, postgis_available, read_postgis_gdf

from .building_file_scanner import building_scan_status, default_processed_dir, default_raw_dir


class BuildingRepository:
    def __init__(self, processed_dir: Path | None = None):
        self.processed_dir = processed_dir or default_processed_dir()
        self.index_path = self.processed_dir / "index.json"
        self._index_cache: dict | None = None
        self._region_cache: dict[str, gpd.GeoDataFrame] = {}

    def reset_cache(self) -> None:
        self._index_cache = None
        self._region_cache = {}

    def processed_ready(self) -> bool:
        if postgis_available():
            count = fetch_scalar("SELECT count(*) FROM information_schema.tables WHERE table_name = 'buildings'")
            if count:
                return True
        return self.index_path.exists()

    def metadata(self) -> dict:
        if self._index_cache is None:
            if self.index_path.exists():
                self._index_cache = json.loads(self.index_path.read_text(encoding="utf-8"))
            else:
                self._index_cache = {}
        return self._index_cache

    def _load_region(self, region_code: str) -> gpd.GeoDataFrame:
        if region_code in self._region_cache:
            return self._region_cache[region_code]
        meta = self.metadata()
        region_info = next((item for item in meta.get("regions", []) if item["region_code"] == region_code), None)
        if region_info is None:
            raise FileNotFoundError(f"Processed building region not found: {region_code}")
        path = self.processed_dir / region_info["file"]
        gdf = gpd.read_parquet(path) if path.suffix == ".parquet" else gpd.read_file(path)
        self._region_cache[region_code] = gdf
        return gdf

    def query_by_bbox(self, min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> gpd.GeoDataFrame:
        if postgis_available():
            return read_postgis_gdf(
                """
                SELECT ufid, building_name, pnu, ground_floor, underground_floor, height_m, height_source,
                       height_confidence, region_code, geom
                FROM buildings
                WHERE geom && ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326)
                  AND ST_Intersects(geom, ST_MakeEnvelope(%(min_lng)s, %(min_lat)s, %(max_lng)s, %(max_lat)s, 4326))
                """,
                {"min_lng": min_lng, "min_lat": min_lat, "max_lng": max_lng, "max_lat": max_lat},
            )
        return self.query_by_bbox_parquet(min_lng, min_lat, max_lng, max_lat)

    def query_by_bbox_parquet(
        self, min_lng: float, min_lat: float, max_lng: float, max_lat: float
    ) -> gpd.GeoDataFrame:
        """PostGIS를 건너뛰고 processed parquet에서 직접 조회.

        `postgis_available()`은 실제 연결이 아니라 **환경변수만** 검사하므로, 서버가 꺼져
        있어도 True가 되어 `query_by_bbox`가 PostGIS 분기로 들어가고 빈 결과를 돌려준다
        (2026-07-27 확인). 교통 수요 파이프라인은 parquet만 있으면 되고 DB 상태와 무관하게
        동작해야 하므로 이 경로를 직접 부른다. `postgis_available()` 자체를 고치는 건
        앱 전체에 영향이 커서 보류 중.
        """
        # processed_ready()는 postgis_available()을 먼저 타므로 여기서는 쓰지 않는다.
        if not self.index_path.exists():
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        search_geom = box(min_lng, min_lat, max_lng, max_lat)
        frames: list[gpd.GeoDataFrame] = []
        for region in self.metadata().get("regions", []):
            rb = region["bounds"]
            if rb["max_lng"] < min_lng or rb["min_lng"] > max_lng or rb["max_lat"] < min_lat or rb["min_lat"] > max_lat:
                continue
            gdf = self._load_region(region["region_code"])
            subset = gdf[gdf.geometry.intersects(search_geom)].copy()
            if not subset.empty:
                frames.append(subset)
        if not frames:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")

    def status(self) -> dict:
        raw = building_scan_status(default_raw_dir())
        meta = self.metadata()
        if postgis_available():
            building_count = int(fetch_scalar("SELECT count(*) FROM buildings") or 0)
            height_available_count = int(fetch_scalar("SELECT count(*) FROM buildings WHERE height_source = 'height_field'") or 0)
            height_estimated_count = int(fetch_scalar("SELECT count(*) FROM buildings WHERE height_source <> 'height_field'") or 0)
        else:
            building_count = meta.get("building_count", 0)
            height_available_count = meta.get("height_available_count", 0)
            height_estimated_count = meta.get("height_estimated_count", 0)
        return {
            "raw_folders_count": raw["raw_folders_count"],
            "processed_ready": self.processed_ready(),
            "building_count": building_count,
            "height_available_count": height_available_count,
            "height_estimated_count": height_estimated_count,
            "postgis_ready": postgis_available() and self.processed_ready(),
            "last_preprocess_time": meta.get("last_preprocess_time"),
            "warnings": raw["warnings"] + meta.get("warnings", []),
            "processed_dir": str(self.processed_dir),
        }
