from fastapi import APIRouter
from fastapi import Body

from app.simulation.state import get_state, start_simulation, stop_simulation

router = APIRouter(prefix="/simulation", tags=["simulation"])


@router.get("/state")
def state() -> dict[str, object]:
    return get_state()


@router.post("/start")
def start(payload: dict[str, object] | None = Body(default=None)) -> dict[str, object]:
    mode = str((payload or {}).get("mode", "mock"))
    return start_simulation(mode)


@router.post("/stop")
def stop() -> dict[str, object]:
    return stop_simulation()
