"""기지국 안테나 높이 및 설치 위치 결정 모듈.

사용자가 지도에서 기지국을 생성할 때 클릭 지점 근처의 건물을 탐색해
가장 가까운 건물 옥상(rooftop)에 설치하거나, 건물이 없으면 기본 철탑
높이를 사용한다. 옥상 설치 시 기지국 좌표를 해당 건물의 중심으로 이동한다.

반환값:
    BSPlacement(lat, lng, antenna_height_m, placement_type, building_name)
    placement_type: "rooftop" | "pole"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import Point

log = logging.getLogger(__name__)

_DEFAULT_POLE_HEIGHT_M = 30.0   # 건물 없는 경우 기본 철탑(pole) 높이
_ANTENNA_MAST_M = 3.0           # 옥상 위 안테나 마스트 여유고


@dataclass
class BSPlacement:
    lat: float
    lng: float
    antenna_height_m: float
    placement_type: str          # "rooftop" | "pole"
    building_name: str = ""      # 옥상 설치 시 건물명


def resolve_placement(
    lat: float,
    lng: float,
    buildings_repo,
    search_radius_m: float = 100.0,
) -> BSPlacement:
    """클릭 위치(lat, lng) 근처 건물을 탐색해 기지국 설치 위치와 높이를 결정한다.

    건물 footprint까지의 거리가 search_radius_m 이내이면:
      - 해당 건물 중심(centroid)으로 lat/lng를 이동한다.
      - 안테나 높이 = building.height_m + 마스트 여유고

    건물이 없거나 너무 멀면:
      - 클릭 위치 그대로, 높이는 기본 철탑 높이
    """
    try:
        deg = search_radius_m / 111_000  # 위도 1° ≈ 111 km
        gdf: gpd.GeoDataFrame = buildings_repo.query_by_bbox(
            min_lng=lng - deg, min_lat=lat - deg,
            max_lng=lng + deg, max_lat=lat + deg,
        )
    except Exception as exc:
        log.warning("bs_placement: building query failed: %s", exc)
        return BSPlacement(lat=lat, lng=lng, antenna_height_m=_DEFAULT_POLE_HEIGHT_M, placement_type="pole")

    if gdf is None or gdf.empty:
        return BSPlacement(lat=lat, lng=lng, antenna_height_m=_DEFAULT_POLE_HEIGHT_M, placement_type="pole")

    try:
        pt = gpd.GeoSeries([Point(lng, lat)], crs="EPSG:4326").to_crs(3857).iloc[0]
        gdf_3857 = gdf.to_crs(3857).copy()
        gdf_3857["_dist_m"] = gdf_3857.geometry.distance(pt)
        nearest_idx = gdf_3857["_dist_m"].idxmin()
        dist_m = float(gdf_3857.at[nearest_idx, "_dist_m"])
    except Exception as exc:
        log.warning("bs_placement: distance calc failed: %s", exc)
        return BSPlacement(lat=lat, lng=lng, antenna_height_m=_DEFAULT_POLE_HEIGHT_M, placement_type="pole")

    if dist_m > search_radius_m:
        return BSPlacement(lat=lat, lng=lng, antenna_height_m=_DEFAULT_POLE_HEIGHT_M, placement_type="pole")

    # 건물 centroid를 4326으로 역변환 → 기지국 마커 위치
    centroid_3857 = gdf_3857.geometry.loc[nearest_idx].centroid
    centroid_4326 = (
        gpd.GeoSeries([centroid_3857], crs="EPSG:3857")
        .to_crs(4326)
        .iloc[0]
    )
    install_lat = float(centroid_4326.y)
    install_lng = float(centroid_4326.x)

    bldg_row = gdf.loc[nearest_idx]
    bldg_height = float(bldg_row.get("height_m") or 0.0)
    if bldg_height <= 0:
        bldg_height = _DEFAULT_POLE_HEIGHT_M

    antenna_h = round(bldg_height + _ANTENNA_MAST_M, 2)
    bldg_name = str(bldg_row.get("building_name") or bldg_row.get("ufid") or "")

    log.info(
        "bs_placement: rooftop '%s' h=%.1fm → antenna=%.1fm "
        "moved (%.5f,%.5f)→(%.5f,%.5f) dist=%.0fm",
        bldg_name, bldg_height, antenna_h,
        lat, lng, install_lat, install_lng, dist_m,
    )
    return BSPlacement(
        lat=install_lat,
        lng=install_lng,
        antenna_height_m=antenna_h,
        placement_type="rooftop",
        building_name=bldg_name,
    )
