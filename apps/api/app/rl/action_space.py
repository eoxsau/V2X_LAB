from __future__ import annotations

from typing import Any


def build_action_candidates(vehicle: dict[str, Any], routes: list[dict[str, Any]]) -> list[dict[str, object]]:
    actions = [{"action": "keep_current_route", "route_id": vehicle.get("current_route_id")}]
    actions.extend(
        {
            "action": "switch_to_route_candidate",
            "route_id": route["id"],
        }
        for route in routes
        if route.get("id") != vehicle.get("current_route_id")
    )
    actions.append({"action": "switch_network_node", "candidate_nodes": vehicle.get("alternative_node_latency_estimates", [])[:3]})
    actions.append({"action": "reduce_speed_recommendation", "status": "placeholder_only"})
    return actions
