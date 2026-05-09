from typing import Any

from fastapi import APIRouter, Body

from app.simulation.state import get_simulation_state, reset_simulation, start_simulation, stop_simulation

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/start")
def start(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, object]:
    mode = str((payload or {}).get("mode", "mock"))
    return start_simulation(mode)


@router.post("/stop")
def stop() -> dict[str, object]:
    return stop_simulation()


@router.post("/reset")
def reset() -> dict[str, object]:
    return reset_simulation()


@router.get("/{simulation_id}/state")
def state(simulation_id: str) -> dict[str, object]:
    return get_simulation_state(simulation_id)
