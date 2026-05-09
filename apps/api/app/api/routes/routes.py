from fastapi import APIRouter

from app.ml.baseline_model import analyze_routes
from app.services.route_repository import get_candidate_routes
from app.services.mock_repository import get_base_stations, get_edge_nodes, get_obstacles, get_roads, get_vehicles

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("/candidates")
def candidates() -> list[dict[str, object]]:
    return get_candidate_routes()


@router.post("/analyze")
def analyze() -> dict[str, object]:
    routes = get_candidate_routes()
    return analyze_routes(
        routes,
        vehicles=get_vehicles(),
        roads=get_roads(),
        base_stations=get_base_stations(),
        edge_nodes=get_edge_nodes(),
        obstacles=get_obstacles(),
    )
