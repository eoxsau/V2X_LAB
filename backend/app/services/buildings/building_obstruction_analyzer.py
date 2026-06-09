from __future__ import annotations

from math import exp

import geopandas as gpd
from shapely.geometry import LineString

from .building_schema import BuildingObstructionResult


def _loss_from_intersections(count: int, max_height: float, crossed_length_m: float) -> tuple[float, str]:
    if count <= 0:
        return 0.0, "high"
    if max_height >= 40 or crossed_length_m >= 80:
        return min(30.0, 12.0 + count * 3.0 + max_height * 0.2), "medium"
    if max_height >= 15 or crossed_length_m >= 20:
        return min(20.0, 8.0 + count * 2.0 + max_height * 0.12), "medium"
    return min(10.0, 4.0 + count * 1.5 + max_height * 0.06), "low"


def analyze_vehicle_to_node(
    *,
    vehicle_id: str,
    vehicle_lat: float,
    vehicle_lng: float,
    network_node: dict,
    buildings_gdf: gpd.GeoDataFrame,
    vehicle_density_penalty: float = 0.0,
) -> BuildingObstructionResult:
    line = LineString([(vehicle_lng, vehicle_lat), (network_node["lng"], network_node["lat"])])
    line_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:4326").to_crs(3857)

    if buildings_gdf.empty:
        distance_m = float(line_gdf.length.iloc[0])
        return BuildingObstructionResult(
            vehicle_id=vehicle_id,
            network_node_id=network_node["id"],
            distance_m=distance_m,
            intersected_building_count=0,
            max_building_height_m=0.0,
            estimated_penetration_loss_db=0.0,
            latency_penalty_ms=vehicle_density_penalty,
            stability_score=max(0.0, 1.0 - vehicle_density_penalty / 50.0),
            confidence="high",
            highlighted_buildings=[],
        )

    search = buildings_gdf[buildings_gdf.geometry.intersects(line)].copy()
    search_3857 = search.to_crs(3857) if not search.empty else search
    distance_m = float(line_gdf.length.iloc[0])
    crossed_length_m = 0.0
    if not search_3857.empty:
        crossed_length_m = float(search_3857.geometry.intersection(line_gdf.iloc[0].geometry).length.sum())
    count = int(len(search))
    max_height = float(search["height_m"].fillna(0).max()) if count else 0.0
    loss_db, confidence = _loss_from_intersections(count, max_height, crossed_length_m)
    latency_penalty_ms = round(loss_db * 0.45 + vehicle_density_penalty, 2)
    stability_score = round(max(0.0, exp(-(loss_db / 18.0)) - vehicle_density_penalty / 100.0), 3)

    highlighted_buildings = []
    top = search.head(5)
    # Use positional access via the GeoDataFrame's active geometry column rather than
    # itertuples — when buildings come from PostGIS the geometry column is named "geom"
    # (not "geometry"), so `row.geometry` raises AttributeError on the namedtuple.
    for pos in range(len(top)):
        row = top.iloc[pos]
        geom = top.geometry.iloc[pos]
        coords = []
        if geom.geom_type == "Polygon":
            coords = [{"lat": lat, "lng": lng} for lng, lat in list(geom.exterior.coords)]
        elif geom.geom_type == "MultiPolygon":
            poly = max(list(geom.geoms), key=lambda g: g.area)
            coords = [{"lat": lat, "lng": lng} for lng, lat in list(poly.exterior.coords)]
        highlighted_buildings.append({
            "id": row.get("ufid") or row.get("pnu"),
            "height_m": float(row.get("height_m", 0.0) or 0.0),
            "height_confidence": row.get("height_confidence", "low"),
            "geometry": coords,
        })

    return BuildingObstructionResult(
        vehicle_id=vehicle_id,
        network_node_id=network_node["id"],
        distance_m=round(distance_m, 2),
        intersected_building_count=count,
        max_building_height_m=round(max_height, 2),
        estimated_penetration_loss_db=round(loss_db, 2),
        latency_penalty_ms=latency_penalty_ms,
        stability_score=stability_score,
        confidence=confidence,
        highlighted_buildings=highlighted_buildings,
    )


def analyze_candidates(
    *,
    vehicle_id: str,
    vehicle_lat: float,
    vehicle_lng: float,
    candidate_nodes: list[dict],
    buildings_gdf: gpd.GeoDataFrame,
    vehicle_density_penalty: float = 0.0,
) -> list[dict]:
    results = []
    for node in candidate_nodes:
        obs = analyze_vehicle_to_node(
            vehicle_id=vehicle_id,
            vehicle_lat=vehicle_lat,
            vehicle_lng=vehicle_lng,
            network_node=node,
            buildings_gdf=buildings_gdf,
            vehicle_density_penalty=vehicle_density_penalty,
        )
        congestion_penalty = float(node.get("congestion_penalty", 0.0))
        edge_latency = float(node.get("edge_latency_ms", 5.0))
        distance_penalty = obs.distance_m / max(float(node.get("coverage_radius_m", 400.0)), 1.0) * 15.0
        node_score = round(
            distance_penalty
            + congestion_penalty
            + obs.estimated_penetration_loss_db * 0.8
            + vehicle_density_penalty
            + edge_latency,
            2,
        )
        predicted_latency_ms = round(4.0 + distance_penalty + congestion_penalty + obs.latency_penalty_ms + edge_latency, 2)
        results.append({
            "node": node,
            "distance_m": obs.distance_m,
            "intersected_building_count": obs.intersected_building_count,
            "max_building_height_m": obs.max_building_height_m,
            "estimated_penetration_loss_db": obs.estimated_penetration_loss_db,
            "latency_penalty_ms": obs.latency_penalty_ms,
            "stability_score": obs.stability_score,
            "confidence": obs.confidence,
            "predicted_latency_ms": predicted_latency_ms,
            "node_score": node_score,
            "highlighted_buildings": obs.highlighted_buildings,
        })
    results.sort(key=lambda item: (item["node_score"], item["predicted_latency_ms"]))
    return results
