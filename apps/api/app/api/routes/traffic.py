from fastapi import APIRouter

from app.services.traffic.traffic_cache import get_traffic_state, sync_its_traffic
from app.simulation.state import get_state

router = APIRouter(tags=["traffic"])


@router.post("/traffic/sync-its")
async def sync_its() -> dict[str, object]:
    return await sync_its_traffic()


@router.get("/traffic/current")
def current_traffic() -> dict[str, object]:
    traffic = get_state()["traffic"]  # type: ignore[index]
    fallback_source = str(traffic.get("traffic_source", "synthetic"))  # type: ignore[union-attr]
    return get_traffic_state(list(traffic.get("roads", [])), fallback_source)  # type: ignore[union-attr]


@router.get("/road-segments")
def road_segments() -> list[dict[str, object]]:
    return list(current_traffic()["road_segments"])  # type: ignore[arg-type]
