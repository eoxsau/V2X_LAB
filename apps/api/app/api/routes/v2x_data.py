from fastapi import APIRouter

from app.services.mock_repository import (
    get_base_stations,
    get_edge_nodes,
    get_obstacles,
    get_routes,
)
from app.simulation.state import get_current_roads, get_current_vehicles, get_state

router = APIRouter(tags=["v2x-data"])


@router.get("/vehicles")
def vehicles() -> list[dict[str, object]]:
    return get_current_vehicles()


@router.get("/roads")
def roads() -> list[dict[str, object]]:
    return get_current_roads()


@router.get("/base-stations")
def base_stations() -> list[dict[str, object]]:
    return list(get_state()["traffic"].get("base_stations", get_base_stations()))  # type: ignore[union-attr]


@router.get("/edge-nodes")
def edge_nodes() -> list[dict[str, object]]:
    return list(get_state()["traffic"].get("edge_nodes", get_edge_nodes()))  # type: ignore[union-attr]


@router.get("/obstacles")
def obstacles() -> list[dict[str, object]]:
    return list(get_state()["traffic"].get("obstacles", get_obstacles()))  # type: ignore[union-attr]


@router.get("/routes")
def routes() -> list[dict[str, object]]:
    return list(get_state()["traffic"].get("routes", get_routes()))  # type: ignore[union-attr]
