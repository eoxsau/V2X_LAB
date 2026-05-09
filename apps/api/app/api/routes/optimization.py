from typing import Any

from fastapi import APIRouter, Body

from app.ml.baseline_model import analyze_routes
from app.services.mock_repository import (
    get_base_stations,
    get_edge_nodes,
    get_obstacles,
    get_roads,
    get_routes,
    get_vehicles,
)

router = APIRouter(prefix="/optimization", tags=["optimization"])


@router.post("/recommend-route")
def recommend_route(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, object]:
    payload = payload or {}
    route_analysis = analyze_routes(
        get_routes(),
        vehicles=get_vehicles(),
        roads=get_roads(),
        base_stations=get_base_stations(),
        edge_nodes=get_edge_nodes(),
        obstacles=get_obstacles(),
        vehicle_id=payload.get("vehicle_id"),
    )
    return {
        "mode": "deterministic_network_aware_ai_optimization",
        "input": payload,
        "shortest_route_baseline": route_analysis["shortest_route_baseline"],
        "traffic_aware_route": route_analysis["traffic_aware_route"],
        "recommended_route": route_analysis["recommended_route"],
        "candidates": route_analysis["routes"],
        "score_direction": route_analysis["score_direction"],
        "note": "Deterministic AI-assisted optimization scaffold only. No production AI model or autonomous-driving control is connected.",
    }
