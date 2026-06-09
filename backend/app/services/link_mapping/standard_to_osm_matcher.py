from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import osmnx as ox

from .mapping_confidence import geometry_match_confidence


def build_osm_edges_from_xml(osm_file: Path) -> gpd.GeoDataFrame:
    graph = ox.graph_from_xml(osm_file, bidirectional=True, simplify=False, retain_all=True)
    edges = ox.graph_to_gdfs(graph, nodes=False, edges=True, fill_edge_geometry=True)
    edges = edges.reset_index()[["u", "v", "key", "name", "length", "geometry"]].copy()
    edges = edges.set_crs("EPSG:4326")
    return edges


def match_standard_links_to_osm_edges(standard_links: gpd.GeoDataFrame, osm_edges: gpd.GeoDataFrame, max_distance_m: float = 60.0) -> list[dict]:
    if standard_links.empty or osm_edges.empty:
        return []
    std_3857 = standard_links.to_crs(3857)
    osm_3857 = osm_edges.to_crs(3857)
    joined = gpd.sjoin_nearest(
        std_3857[["link_id", "road_name", "geometry"]],
        osm_3857[["u", "v", "key", "name", "length", "geometry"]],
        how="inner",
        distance_col="distance_m",
    )
    matches = []
    for row in joined.itertuples():
        if row.distance_m > max_distance_m:
            continue
        same_name = False
        if getattr(row, "road_name", None) and getattr(row, "name", None):
            same_name = str(row.road_name) in str(row.name)
        matches.append({
            "standard_link_id": str(row.link_id),
            "osm_edge_key": f"{row.u}:{row.v}:{row.key}",
            "distance_m": float(row.distance_m),
            "confidence": geometry_match_confidence(float(row.distance_m), same_name),
        })
    return matches
