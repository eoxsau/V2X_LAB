from __future__ import annotations

from typing import Any


def select_baseline_action(vehicle: dict[str, Any], routes: list[dict[str, Any]]) -> dict[str, object]:
    if float(vehicle.get("obstacle_risk", 0)) >= 0.65:
        candidate = next((route for route in routes if route.get("id") != vehicle.get("current_route_id")), routes[0] if routes else {})
        return {"policy": "baseline_policy", "action": "switch_to_route_candidate", "route_id": candidate.get("id"), "reason": "high_obstacle_risk"}
    if float(vehicle.get("network_node_congestion_score", 0)) >= 70:
        return {"policy": "baseline_policy", "action": "switch_network_node", "reason": "high_network_node_load"}
    return {"policy": "baseline_policy", "action": "keep_current_route", "route_id": vehicle.get("current_route_id"), "reason": "stable_context"}
