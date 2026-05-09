from __future__ import annotations

import math

from app.services.sumo.sumo_network_loader import SumoEdge, SumoNetwork


def match_coordinate_to_edge(lat: float, lng: float, network: SumoNetwork) -> dict[str, object]:
    if not network.edges:
        raise RuntimeError("Could not match selected coordinates to SUMO road network.")
    best = min(network.edges, key=lambda edge: _edge_distance(lat, lng, edge))
    distance_m = _edge_distance(lat, lng, best) * 1000
    if distance_m > 5_000:
        raise RuntimeError("Could not match selected coordinates to SUMO road network.")
    return {
        "edge_id": best.id,
        "distance_m": round(distance_m, 1),
        "confidence": _confidence(distance_m),
        "edge": best,
    }


def _edge_distance(lat: float, lng: float, edge: SumoEdge) -> float:
    points = [(point["lat"], point["lng"]) for point in edge.shape_lat_lng]
    return min(_point_segment_distance((lat, lng), points[index], points[index + 1]) for index in range(len(points) - 1))


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    px, py = _to_km(point)
    ax, ay = _to_km(start)
    bx, by = _to_km(end)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    ratio = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    closest = (ax + ratio * dx, ay + ratio * dy)
    return math.hypot(px - closest[0], py - closest[1])


def _to_km(point: tuple[float, float]) -> tuple[float, float]:
    lat, lng = point
    return lng * 88.0, lat * 111.0


def _confidence(distance_m: float) -> str:
    if distance_m <= 30:
        return "high"
    if distance_m <= 150:
        return "medium"
    return "low"
