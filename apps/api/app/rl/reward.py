from __future__ import annotations

from typing import Any


def calculate_reward_components(vehicle: dict[str, Any], route: dict[str, Any]) -> dict[str, object]:
    components = {
        "travel_time_penalty": -float(route.get("travel_time_estimate_min", route.get("travel_time_estimate", 0))) * 0.8,
        "network_latency_penalty": -float(vehicle.get("current_latency_ms", 0)) * 0.12,
        "congestion_penalty": -float(vehicle.get("network_node_congestion_score", 0)) * 0.04,
        "obstacle_risk_penalty": -float(vehicle.get("obstacle_risk", 0)) * 18,
        "unsafe_proximity_penalty": -float(vehicle.get("collision_proximity_risk", 0)) * 24,
        "route_stability_bonus": 4.0 if route.get("id") == vehicle.get("current_route_id") else 1.0,
    }
    reward = sum(components.values())
    return {
        "formula": "-travel_time -network_latency -congestion -obstacle_risk -unsafe_proximity +route_stability",
        "components": {key: round(value, 3) for key, value in components.items()},
        "reward": round(reward, 3),
    }
