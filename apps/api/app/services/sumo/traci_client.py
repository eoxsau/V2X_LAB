from __future__ import annotations

import time
from typing import Any


class TraCIConnectionError(RuntimeError):
    pass


class TraCIClient:
    def __init__(self) -> None:
        self._traci: Any | None = None

    def connect(self, host: str, port: int, attempts: int = 20) -> None:
        try:
            import traci  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on local package installation.
            raise TraCIConnectionError(f"Python TraCI package is unavailable: {exc}") from exc

        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                traci.init(port=port, host=host)
                self._traci = traci
                return
            except Exception as exc:  # pragma: no cover - depends on local SUMO process startup timing.
                last_error = exc
                time.sleep(0.15)

        raise TraCIConnectionError(f"TraCI connection failed: {last_error}")

    def close(self) -> None:
        if self._traci is not None:
            try:
                self._traci.close(False)
            except Exception:
                pass
        self._traci = None

    def simulation_step(self) -> None:
        self._require().simulationStep()

    def vehicle_ids(self) -> list[str]:
        return list(self._require().vehicle.getIDList())

    def vehicle_position(self, vehicle_id: str) -> tuple[float, float]:
        x, y = self._require().vehicle.getPosition(vehicle_id)
        return float(x), float(y)

    def vehicle_speed_kmh(self, vehicle_id: str) -> float:
        return float(self._require().vehicle.getSpeed(vehicle_id)) * 3.6

    def vehicle_heading(self, vehicle_id: str) -> float:
        return float(self._require().vehicle.getAngle(vehicle_id))

    def vehicle_route(self, vehicle_id: str) -> list[str]:
        return list(self._require().vehicle.getRoute(vehicle_id))

    def vehicle_road_id(self, vehicle_id: str) -> str:
        return str(self._require().vehicle.getRoadID(vehicle_id))

    def add_route(self, route_id: str, edge_ids: list[str]) -> None:
        self._require().route.add(route_id, edge_ids)

    def add_vehicle(self, vehicle_id: str, route_id: str, depart: str = "now") -> None:
        self._require().vehicle.add(vehicle_id, route_id, depart=depart)

    def set_vehicle_route(self, vehicle_id: str, edge_ids: list[str]) -> None:
        self._require().vehicle.setRoute(vehicle_id, edge_ids)

    def edge_ids(self) -> set[str]:
        return set(self._require().edge.getIDList())

    def lane_ids(self) -> set[str]:
        return set(self._require().lane.getIDList())

    def edge_vehicle_count(self, edge_id: str) -> float:
        return float(self._require().edge.getLastStepVehicleNumber(edge_id))

    def edge_mean_speed_kmh(self, edge_id: str) -> float:
        return float(self._require().edge.getLastStepMeanSpeed(edge_id)) * 3.6

    def lane_vehicle_count(self, lane_id: str) -> float:
        return float(self._require().lane.getLastStepVehicleNumber(lane_id))

    def lane_mean_speed_kmh(self, lane_id: str) -> float:
        return float(self._require().lane.getLastStepMeanSpeed(lane_id)) * 3.6

    def convert_geo(self, x: float, y: float) -> tuple[float, float]:
        lng, lat = self._require().simulation.convertGeo(float(x), float(y))
        return float(lat), float(lng)

    def _require(self) -> Any:
        if self._traci is None:
            raise TraCIConnectionError("TraCI is not connected.")
        return self._traci
