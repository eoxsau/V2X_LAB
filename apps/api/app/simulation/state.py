from datetime import UTC, datetime

from app.simulation.mock_mobility_simulator import MockMobilitySimulator
from app.services.sumo import SumoTraCIAdapter
from app.services.traffic.traffic_cache import apply_traffic_priority

mock_simulator = MockMobilitySimulator()
sumo_adapter = SumoTraCIAdapter()
_running = False
_tick = 0
_active_simulation_id = "sim-local-001"
_simulator_mode = "mock"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _active_adapter() -> MockMobilitySimulator | SumoTraCIAdapter:
    if _simulator_mode == "sumo_traci":
        return sumo_adapter
    return mock_simulator


def get_state() -> dict[str, object]:
    global _tick
    if _running:
        _tick += 1
        traffic = _active_adapter().step()
    else:
        traffic = _active_adapter().snapshot()
    adapter_source = "sumo" if traffic.get("adapter") == "traci" else "synthetic"
    traffic = apply_traffic_priority(traffic, adapter_source)

    return {
        "simulation_id": _active_simulation_id,
        "simulator_mode": _simulator_mode,
        "running": _running,
        "tick": _tick,
        "updated_at": _now(),
        "traffic": traffic,
    }


def get_simulation_state(simulation_id: str) -> dict[str, object]:
    state = get_state()
    state["simulation_id"] = simulation_id
    return state


def start_simulation(mode: str = "mock") -> dict[str, object]:
    global _running, _simulator_mode
    _simulator_mode = "sumo_traci" if mode in {"sumo", "sumo_traci", "sumo_traci_placeholder"} else "mock"
    _running = True
    if _simulator_mode == "sumo_traci":
        sumo_adapter.connect()
    return get_state()


def start_coordinate_scenario(scenario: dict[str, object]) -> dict[str, object]:
    global _running, _simulator_mode, _tick
    _simulator_mode = "sumo_traci"
    _running = True
    _tick = 0
    sumo_adapter.load_coordinate_scenario(scenario)
    return get_state()


def stop_simulation() -> dict[str, object]:
    global _running
    _running = False
    return get_state()


def reset_simulation() -> dict[str, object]:
    global _running, _tick
    _running = False
    _tick = 0
    mock_simulator.reset()
    sumo_adapter.reset()
    return get_state()


def get_current_vehicles() -> list[dict[str, object]]:
    return list(get_state()["traffic"]["vehicles"])  # type: ignore[index]


def get_current_roads() -> list[dict[str, object]]:
    return list(get_state()["traffic"]["roads"])  # type: ignore[index]
