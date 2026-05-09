from __future__ import annotations

from typing import Any


def build_vehicle_features(
    vehicles: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    edge_nodes: list[dict[str, Any]],
) -> list[dict[str, object]]:
    road_by_route = _road_by_route(routes, roads)
    edge_by_id = {str(edge["id"]): edge for edge in edge_nodes}
    features = []

    for vehicle in vehicles:
        road = road_by_route.get(str(vehicle.get("current_route_id")), {})
        edge = edge_by_id.get(str(vehicle.get("connected_edge_node_id")), {})
        features.append(
            {
                "vehicle_id": vehicle.get("id"),
                "speed": round(float(vehicle.get("speed", 0)), 3),
                "heading": round(float(vehicle.get("heading", 0)), 3),
                "road_congestion_score": round(float(road.get("congestion_score", 0)), 3),
                "network_node_distance": round(float(vehicle.get("distance_to_connected_node", 0)), 3),
                "network_node_congestion": round(float(vehicle.get("network_node_congestion_score", 0)), 3),
                "current_latency_ms": round(float(vehicle.get("current_latency_ms", 0)), 3),
                "obstacle_risk": round(float(vehicle.get("obstacle_risk", 0)), 3),
                "nearby_vehicle_density": _nearby_vehicle_density(vehicle),
                "nearest_vehicle_distance": vehicle.get("nearest_vehicle_distance"),
                "route_travel_time": round(float(road.get("travel_time_estimate", 0)), 3),
                "edge_latency_ms": round(float(edge.get("edge_latency_ms", vehicle.get("latency_breakdown", {}).get("edge_latency_penalty", 0))), 3),
            }
        )
    return features


def _road_by_route(routes: list[dict[str, Any]], roads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    road_by_id = {str(road["id"]): road for road in roads}
    mapped = {}
    for route in routes:
        road_ids = route.get("road_segment_ids", [])
        if road_ids:
            mapped[str(route["id"])] = road_by_id.get(str(road_ids[0]), {})
    return mapped


def _nearby_vehicle_density(vehicle: dict[str, Any]) -> float:
    nearest = vehicle.get("nearest_vehicle_distance")
    if nearest is None:
        return 0.0
    return round(max(0.0, min(1.0, 1 - float(nearest) / 1.2)), 3)
