from fastapi import APIRouter, HTTPException

from app.simulator.state import (
    get_simulation_state,
    start_simulation,
    stop_simulation,
)

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/start")
def start() -> dict[str, object]:
    return start_simulation()


@router.post("/stop")
def stop() -> dict[str, object]:
    return stop_simulation()


@router.get("/{simulation_id}/state")
def state(simulation_id: str) -> dict[str, object]:
    simulation_state = get_simulation_state(simulation_id)
    if simulation_state is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    return simulation_state
