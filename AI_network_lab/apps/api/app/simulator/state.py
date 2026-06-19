from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

from app.services.public_data_client import get_base_stations
from app.services.lookahead_scan import (
    analyze_candidates_light,
    build_route_segments,
    compute_lookahead_network_scan,
    interpolate_position_on_route,
)

_current_simulation: dict[str, object] | None = None
_state_lock = Lock()
_lookahead_stop_event = Event()
_lookahead_thread: Thread | None = None

_DEFAULT_ROUTE_COORDS: list[tuple[float, float]] = [
    (37.5669, 126.9782),
    (37.5684, 126.9804),
    (37.5702, 126.9831),
    (37.5721, 126.9854),
    (37.5742, 126.9877),
    (37.5759, 126.9896),
]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _build_initial_route_context() -> dict[str, object]:
    route_segments, route_length_m = build_route_segments(_DEFAULT_ROUTE_COORDS)
    initial_position = interpolate_position_on_route(_DEFAULT_ROUTE_COORDS, route_segments, 0.0)
    return {
        "route_coords": [list(coord) for coord in _DEFAULT_ROUTE_COORDS],
        "_route_segments": route_segments,
        "route_length_m": route_length_m,
        "position": {
            "lat": initial_position["lat"],
            "lng": initial_position["lng"],
            "route_progress_m": 0.0,
            "speed_kmh": 26.0,
            "connected_base_station_id": None,
        },
    }


def _serialize_simulation_state(simulation: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in simulation.items()
        if not key.startswith("_")
    }


def _compute_current_connectivity(
    simulation: dict[str, object],
    base_stations: list[dict[str, object]],
) -> dict[str, object]:
    position = simulation["position"]
    current_node_id = position.get("connected_base_station_id")
    candidates = analyze_candidates_light(
        float(position["lat"]),
        float(position["lng"]),
        base_stations,
        current_node_id=str(current_node_id) if current_node_id else None,
    )
    best_candidate = candidates[0] if candidates else None

    if best_candidate is None:
        return {
            "connected_base_station_id": None,
            "current_latency_ms": 0.0,
            "candidate_nodes": [],
        }

    return {
        "connected_base_station_id": best_candidate["id"],
        "current_latency_ms": best_candidate["predicted_latency_ms"],
        "candidate_nodes": candidates[:3],
        "connected_node": best_candidate,
    }


def _compute_route_costs(
    simulation: dict[str, object],
    connectivity: dict[str, object],
) -> dict[str, object]:
    route_length_m = float(simulation.get("route_length_m") or 0.0)
    speed_kmh = float(simulation["position"].get("speed_kmh") or 1.0)
    speed_mps = max(speed_kmh * (1000 / 3600), 1.0)
    travel_time_cost = round(route_length_m / speed_mps, 2)
    current_latency_cost = float(connectivity.get("current_latency_ms") or 0.0)
    future_risk_penalty = float(
        (simulation.get("network_telemetry") or {}).get("lookahead", {}).get("future_risk_penalty", 0.0)
    )
    network_aware_cost = round(travel_time_cost + current_latency_cost + future_risk_penalty, 2)
    return {
        "travel_time_cost": travel_time_cost,
        "current_latency_cost": current_latency_cost,
        "future_risk_penalty": future_risk_penalty,
        "network_aware_cost": network_aware_cost,
    }


def _refresh_runtime_state_locked(base_stations: list[dict[str, object]]) -> None:
    if _current_simulation is None or _current_simulation.get("status") != "running":
        return

    route_segments = _current_simulation["_route_segments"]
    route_length_m = float(_current_simulation["route_length_m"])
    started_at_monotonic = float(_current_simulation["_started_at_monotonic"])
    elapsed_seconds = max(0.0, monotonic() - started_at_monotonic)
    speed_kmh = float(_current_simulation["position"].get("speed_kmh") or 26.0)
    speed_mps = speed_kmh * (1000 / 3600)
    progress_m = min(route_length_m, elapsed_seconds * speed_mps)
    projected_position = interpolate_position_on_route(
        _current_simulation["route_coords"],
        route_segments,
        progress_m,
    )

    _current_simulation["position"] = {
        **_current_simulation["position"],
        "lat": projected_position["lat"],
        "lng": projected_position["lng"],
        "route_progress_m": projected_position["route_progress_m"],
        "speed_kmh": speed_kmh,
    }

    connectivity = _compute_current_connectivity(_current_simulation, base_stations)
    _current_simulation["position"]["connected_base_station_id"] = connectivity["connected_base_station_id"]

    current_telemetry = {
        "connected_node": connectivity.get("connected_node"),
        "candidate_nodes": connectivity.get("candidate_nodes", []),
        "current_latency_ms": connectivity.get("current_latency_ms", 0.0),
    }
    previous_lookahead = (_current_simulation.get("network_telemetry") or {}).get("lookahead")
    if previous_lookahead is not None:
        current_telemetry["lookahead"] = previous_lookahead

    _current_simulation["network_telemetry"] = current_telemetry
    _current_simulation["route_costs"] = _compute_route_costs(_current_simulation, connectivity)


def _lookahead_worker_loop() -> None:
    while not _lookahead_stop_event.is_set():
        with _state_lock:
            if _current_simulation is None or _current_simulation.get("status") != "running":
                break

            base_stations = get_base_stations()
            _refresh_runtime_state_locked(base_stations)
            simulation_id = _current_simulation["id"]
            vehicle_pos = dict(_current_simulation["position"])
            route_coords = [list(point) for point in _current_simulation["route_coords"]]

        lookahead_result = compute_lookahead_network_scan(
            vehicle_pos,
            route_coords,
            base_stations,
        )

        with _state_lock:
            if _current_simulation is None or _current_simulation.get("id") != simulation_id:
                continue

            _current_simulation["prev_lookahead"] = (_current_simulation.get("network_telemetry") or {}).get("lookahead")
            current_telemetry = dict(_current_simulation.get("network_telemetry") or {})
            updated_telemetry = {
                **current_telemetry,
                "lookahead": lookahead_result,
            }
            _current_simulation["network_telemetry"] = updated_telemetry
            _current_simulation["route_costs"] = {
                **(_current_simulation.get("route_costs") or {}),
                "future_risk_penalty": lookahead_result["future_risk_penalty"],
                "future_connectivity_score": lookahead_result["future_connectivity_score"],
                "network_aware_cost": round(
                    float((_current_simulation.get("route_costs") or {}).get("travel_time_cost", 0.0))
                    + float((_current_simulation.get("route_costs") or {}).get("current_latency_cost", 0.0))
                    + float(lookahead_result["future_risk_penalty"]),
                    2,
                ),
            }

        _lookahead_stop_event.wait(0.75)


def _start_lookahead_worker() -> None:
    global _lookahead_thread

    _lookahead_stop_event.clear()
    _lookahead_thread = Thread(
        target=_lookahead_worker_loop,
        name="lookahead-scan-worker",
        daemon=True,
    )
    _lookahead_thread.start()


def _stop_lookahead_worker() -> None:
    global _lookahead_thread

    _lookahead_stop_event.set()
    if _lookahead_thread is not None and _lookahead_thread.is_alive():
        _lookahead_thread.join(timeout=1.0)
    _lookahead_thread = None


def start_simulation() -> dict[str, object]:
    global _current_simulation

    _stop_lookahead_worker()
    with _state_lock:
        base_stations = get_base_stations()
        route_context = _build_initial_route_context()
        _current_simulation = {
            "id": f"sim-{uuid4().hex[:8]}",
            "status": "running",
            "started_at": _timestamp(),
            "stopped_at": None,
            "base_station_count": len(base_stations),
            "position": route_context["position"],
            "route_coords": route_context["route_coords"],
            "_route_segments": route_context["_route_segments"],
            "route_length_m": route_context["route_length_m"],
            "network_telemetry": {},
            "prev_lookahead": None,
            "route_costs": {},
            "_started_at_monotonic": monotonic(),
        }
        _refresh_runtime_state_locked(base_stations)

    _start_lookahead_worker()

    with _state_lock:
        return _serialize_simulation_state(_current_simulation)


def stop_simulation() -> dict[str, object]:
    global _current_simulation

    _stop_lookahead_worker()
    with _state_lock:
        base_stations = get_base_stations()
        if _current_simulation is None:
            _current_simulation = {
                "id": "sim-idle",
                "status": "stopped",
                "started_at": None,
                "stopped_at": _timestamp(),
                "base_station_count": len(base_stations),
            }
            return _serialize_simulation_state(_current_simulation)

        _current_simulation = {
            **_current_simulation,
            "status": "stopped",
            "stopped_at": _timestamp(),
        }
        return _serialize_simulation_state(_current_simulation)


def get_simulation_state(simulation_id: str) -> dict[str, object] | None:
    with _state_lock:
        if _current_simulation is None or _current_simulation["id"] != simulation_id:
            return None

        base_stations = get_base_stations()
        if _current_simulation.get("status") == "running":
            _refresh_runtime_state_locked(base_stations)

        serialized_state = _serialize_simulation_state(_current_simulation)

    return {
        **serialized_state,
        "base_stations": base_stations,
    }
