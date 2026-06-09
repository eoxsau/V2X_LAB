from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

from .mapping_confidence import geometry_match_confidence


def build_sumo_edges_gdf(net) -> gpd.GeoDataFrame:
    rows = []
    for edge in net.getEdges():
        if edge.getFunction() == "internal":
            continue
        coords = []
        for x, y in edge.getShape():
            lon, lat = net.convertXY2LonLat(x, y)
            coords.append((lon, lat))
        if len(coords) < 2:
            continue
        rows.append({
            "sumo_edge_id": edge.getID(),
            "name": edge.getName(),
            "geometry": LineString(coords),
        })
    if not rows:
        return gpd.GeoDataFrame(columns=["sumo_edge_id", "name", "geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def match_osm_to_sumo_edges(osm_edges: gpd.GeoDataFrame, sumo_edges: gpd.GeoDataFrame, max_distance_m: float = 45.0) -> list[dict]:
    if osm_edges.empty or sumo_edges.empty:
        return []
    osm_3857 = osm_edges.to_crs(3857)
    sumo_3857 = sumo_edges.to_crs(3857)
    joined = gpd.sjoin_nearest(
        osm_3857[["u", "v", "key", "name", "geometry"]],
        sumo_3857[["sumo_edge_id", "name", "geometry"]],
        how="inner",
        distance_col="distance_m",
    )
    matches = []
    for row in joined.itertuples():
        if row.distance_m > max_distance_m:
            continue
        same_name = False
        if getattr(row, "name_left", None) and getattr(row, "name_right", None):
            same_name = str(row.name_left) in str(row.name_right) or str(row.name_right) in str(row.name_left)
        matches.append({
            "osm_edge_key": f"{row.u}:{row.v}:{row.key}",
            "sumo_edge_id": row.sumo_edge_id,
            "distance_m": float(row.distance_m),
            "confidence": geometry_match_confidence(float(row.distance_m), same_name),
        })
    return matches

