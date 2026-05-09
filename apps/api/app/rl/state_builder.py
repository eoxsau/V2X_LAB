from __future__ import annotations

from typing import Any


def build_rl_state(
    vehicle: dict[str, Any],
    roads: list[dict[str, Any]],
    network_nodes: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> dict[str, object]:
    return {
        "vehicle_state": {
            "id": vehicle.get("id"),
            "speed": vehicle.get("speed"),
            "heading": vehicle.get("heading"),
            "current_route_id": vehicle.get("current_route_id"),
            "current_latency_ms": vehicle.get("current_latency_ms"),
        },
        "road_congestion_state": [
            {"id": road["id"], "congestion_score": road.get("congestion_score"), "travel_time_estimate": road.get("travel_time_estimate")}
            for road in roads
        ],
        "network_node_load_state": [
            {"id": node["id"], "node_type": node["node_type"], "load": node["congestion_score"], "edge_latency_ms": node["edge_latency_ms"]}
            for node in network_nodes
        ],
        "nearby_vehicle_relationship_state": {
            "nearest_vehicle_distance": vehicle.get("nearest_vehicle_distance"),
            "collision_proximity_risk": vehicle.get("collision_proximity_risk"),
        },
        "route_candidate_state": [
            {"id": route["id"], "distance_km": route.get("distance_km"), "travel_time_estimate": route.get("travel_time_estimate_min", route.get("travel_time_estimate"))}
            for route in routes
        ],
        "obstacle_risk_state": {
            "obstacle_risk": vehicle.get("obstacle_risk"),
            "network_blockage_risk": vehicle.get("network_blockage_risk"),
        },
    }
