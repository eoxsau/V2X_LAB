from fastapi import APIRouter

from app.ai import build_ai_ready_analysis
from app.ml.baseline_model import analyze_routes
from app.rl import build_rl_ready_payload
from app.services.v2x_network_layer import build_network_nodes
from app.services.mock_repository import (
    get_base_stations,
    get_edge_nodes,
    get_obstacles,
    get_roads,
    get_routes,
    get_vehicles,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/current")
def current_analysis() -> dict[str, object]:
    route_analysis = analyze_routes(
        get_routes(),
        vehicles=get_vehicles(),
        roads=get_roads(),
        base_stations=get_base_stations(),
        edge_nodes=get_edge_nodes(),
        obstacles=get_obstacles(),
    )
    network_nodes = build_network_nodes(get_base_stations(), get_edge_nodes())
    return {
        "mode": "ai_ready_structured_analysis",
        "summary": "Structured AI/RL preparation using in-memory simulation data. No trained AI result is claimed.",
        "selected_vehicle_id": "AV-104",
        "recommended_route": route_analysis["recommended_route"],
        "route_analysis": route_analysis,
        "ai_ready": build_ai_ready_analysis(get_vehicles(), get_roads(), get_routes(), get_edge_nodes()),
        "rl": build_rl_ready_payload(get_vehicles(), get_roads(), network_nodes, get_routes()),
        "metrics": {
            "vehicle_count": len(get_vehicles()),
            "base_station_count": len(get_base_stations()),
            "road_segments": len(get_roads()),
            "active_obstacles": len(get_obstacles()),
        },
    }
