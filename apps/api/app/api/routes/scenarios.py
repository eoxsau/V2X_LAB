from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.geocoding import geocode_vworld_address, normalize_coordinate
from app.services.sumo.sumo_edge_matcher import match_coordinate_to_edge
from app.services.sumo.sumo_network_loader import load_sumo_network
from app.services.sumo.sumo_route_builder import build_sumo_route
from app.services.traffic.traffic_cache import sync_its_traffic
from app.simulation.state import start_coordinate_scenario

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class CoordinatePayload(BaseModel):
    lat: float
    lng: float
    label: str | None = None


class ScenarioFromCoordinatesRequest(BaseModel):
    origin: CoordinatePayload
    destination: CoordinatePayload
    ego_vehicle_count: int = Field(default=1, ge=1, le=5)
    surrounding_vehicle_count: int = Field(default=20, ge=0, le=300)
    traffic_density: Literal["low", "medium", "high"] = "medium"
    mode: Literal["urban_mobility", "six_g_like_v2x"] = "urban_mobility"
    use_its_api: bool = False


class GeocodeRequest(BaseModel):
    query: str


@router.post("/geocode")
async def geocode(payload: GeocodeRequest) -> dict[str, object]:
    return await geocode_vworld_address(payload.query)


@router.post("/from-coordinates")
async def from_coordinates(payload: ScenarioFromCoordinatesRequest) -> dict[str, object]:
    origin = normalize_coordinate(payload.origin.model_dump())
    destination = normalize_coordinate(payload.destination.model_dump())
    network = load_sumo_network()
    try:
        origin_match = match_coordinate_to_edge(float(origin["lat"]), float(origin["lng"]), network)
        destination_match = match_coordinate_to_edge(float(destination["lat"]), float(destination["lng"]), network)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    route = build_sumo_route(origin_match, destination_match, network, origin, destination)
    traffic_source = "sumo"
    its_status = "not_requested"
    if payload.use_its_api:
        its_state = await sync_its_traffic()
        traffic_source = str(its_state.get("source", "sumo"))
        its_status = str(its_state.get("status", "fallback"))

    scenario = {
        **route,
        "origin": origin,
        "destination": destination,
        "ego_vehicle_count": payload.ego_vehicle_count,
        "surrounding_vehicle_count": payload.surrounding_vehicle_count,
        "traffic_density": payload.traffic_density,
        "mode": payload.mode,
        "use_its_api": payload.use_its_api,
        "traffic_source": traffic_source,
        "matched_origin_edge": {
            "edge_id": origin_match["edge_id"],
            "distance_m": origin_match["distance_m"],
            "confidence": origin_match["confidence"],
        },
        "matched_destination_edge": {
            "edge_id": destination_match["edge_id"],
            "distance_m": destination_match["distance_m"],
            "confidence": destination_match["confidence"],
        },
        "coordinate_conversion_status": network.conversion_status,
        "sumo_network_source": network.source,
        "sumo_net_path": network.net_path,
        "its_status": its_status,
    }
    state = start_coordinate_scenario(scenario)
    return {
        "status": "started",
        "message": "Coordinate scenario synchronized with SUMO adapter or mock fallback.",
        "debug": scenario,
        "simulation": state,
    }
