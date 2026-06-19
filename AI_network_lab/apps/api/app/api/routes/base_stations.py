from fastapi import APIRouter

from app.services.public_data_client import (
    get_base_stations,
    sync_base_stations_from_public_api,
)

router = APIRouter(tags=["base-stations"])


@router.post("/base-stations/sync-public-data")
def sync_public_data() -> list[dict[str, object]]:
    return sync_base_stations_from_public_api()


@router.get("/base-stations")
def list_base_stations() -> list[dict[str, object]]:
    return get_base_stations()
