"""용도지역 공간조인 — V-World Data API 폴리곤을 건물 centroid에 매핑한다.

사용:
    gdf = enrich_zone_types(gdf, api_key="VWORLD_API_KEY")

결과:
    gdf["zone_type"] 컬럼에
    "저밀도주거" | "일반주거" | "상업" | "업무중심" | "산업" | "혼합" 중 하나.
    API 실패 또는 폴리곤 미포함 건물은 None 유지 → height estimator Tier 6으로 낙하.
"""
from __future__ import annotations

import logging

import geopandas as gpd

from .vworld_zone_client import VWorldZoneClient

log = logging.getLogger(__name__)

_BBOX_MARGIN = 0.01   # degrees (~1 km)


def enrich_zone_types(
    gdf: gpd.GeoDataFrame,
    api_key: str,
) -> gpd.GeoDataFrame:
    """Return copy of gdf with zone_type column filled from V-World Data API."""
    if not api_key:
        return gdf

    # ── 건물 centroid (EPSG:4326) ────────────────────────────────────────────
    centroid_gdf = gdf[["geometry"]].copy()
    centroid_gdf = centroid_gdf.copy()
    centroid_gdf["geometry"] = gdf.to_crs(3857).centroid.to_crs(4326)

    bounds = centroid_gdf.total_bounds   # [minx, miny, maxx, maxy]
    minx = bounds[0] - _BBOX_MARGIN
    miny = bounds[1] - _BBOX_MARGIN
    maxx = bounds[2] + _BBOX_MARGIN
    maxy = bounds[3] + _BBOX_MARGIN

    # ── V-World LT_C_UQ111 조회 (Data API, EPSG:4326) ────────────────────────
    client   = VWorldZoneClient(api_key)
    zone_gdf = client.fetch_zones(minx, miny, maxx, maxy)

    if zone_gdf is None or zone_gdf.empty:
        log.warning("V-World zone polygons unavailable — zone_type stays None")
        return gdf

    log.info("V-World: %d zone polygons fetched", len(zone_gdf))

    # ── 공간조인: building centroid ∈ zone polygon ───────────────────────────
    joined = gpd.sjoin(
        centroid_gdf,
        zone_gdf[["geometry", "zone_type"]],
        how="left",
        predicate="within",
    )
    # 하나의 centroid가 복수 폴리곤에 걸칠 경우 첫 번째만 사용
    joined = joined[~joined.index.duplicated(keep="first")]

    gdf = gdf.copy()
    gdf["zone_type"] = joined["zone_type"].reindex(gdf.index)

    filled = int(gdf["zone_type"].notna().sum())
    log.info(
        "Zone enrichment complete: %d/%d buildings assigned zone_type",
        filled, len(gdf),
    )
    return gdf
