from __future__ import annotations

from copy import deepcopy
from typing import Any

from loguru import logger

from app.services.sumo.coordinate_converter import CoordinateConverter
from app.services.sumo.mock_sumo_adapter import MockSumoAdapter
from app.services.sumo.sumo_config import SumoRuntimeConfig
from app.services.sumo.sumo_runner import SumoProcessRunner, SumoUnavailableError
from app.services.sumo.traci_client import TraCIClient
from app.services.v2x_network_layer import attach_v2x_network_state


class SumoTraCIAdapter:
    """Real SUMO/TraCI adapter with a non-fatal mock fallback."""

    def __init__(self) -> None:
        self.connected = False
        self.using_fallback = True
        self.last_error: str | None = None
        self.mock = MockSumoAdapter()
        self.runner = SumoProcessRunner()
        self.traci = TraCIClient()
        self.converter = CoordinateConverter(self.traci)
        self._base_state = self.mock.simulator.snapshot()

    def connect(self) -> bool:
        config = SumoRuntimeConfig.from_settings()
        try:
            self.runner.start(config)
            self.traci.connect(config.host, config.port)
            self.connected = True
            self.using_fallback = False
            self.last_error = None
            return True
        except (SumoUnavailableError, Exception) as exc:  # pragma: no cover - depends on local SUMO installation.
            return self._activate_fallback(str(exc))

    def reset(self) -> None:
        self._disconnect()
        self.mock.reset()
        self._base_state = self.mock.simulator.snapshot()
        self.connected = False
        self.using_fallback = True
        self.last_error = None

    def load_coordinate_scenario(self, scenario: dict[str, object]) -> dict[str, object]:
        self.mock.simulator.load_coordinate_scenario(scenario)
        self._base_state = self.mock.simulator.snapshot()
        if not self.connected and not self.connect():
            return self._fallback_snapshot()

        edge_ids = [str(edge_id) for edge_id in scenario.get("sumo_edge_ids", [])]
        route_id = str(scenario.get("route_id", "sumo-route-user-selected"))
        if edge_ids:
            try:
                self.traci.add_route(route_id, edge_ids)
                self.traci.add_vehicle("EGO-001", route_id)
            except Exception as exc:  # pragma: no cover - depends on live SUMO network state.
                self._activate_fallback(f"SUMO route insertion failed: {exc}")
        return self.snapshot()

    def step(self) -> dict[str, object]:
        if not self.connected and self.using_fallback and self.last_error:
            return self._fallback_step()

        if not self.connected and not self.connect():
            return self._fallback_step()

        try:
            self.traci.simulation_step()
            return self._sumo_snapshot()
        except Exception as exc:  # pragma: no cover - depends on live TraCI.
            self._activate_fallback(f"SUMO step failed: {exc}")
            return self._fallback_step()

    def snapshot(self) -> dict[str, object]:
        if self.connected:
            try:
                return self._sumo_snapshot()
            except Exception as exc:  # pragma: no cover - depends on live TraCI.
                self._activate_fallback(f"SUMO snapshot failed: {exc}")

        if not self.using_fallback or not self.last_error:
            self.connect()

        return self._fallback_snapshot()

    def _sumo_snapshot(self) -> dict[str, object]:
        base = deepcopy(self._base_state)
        vehicles = self._read_vehicles(base)
        roads = self._read_road_congestion(base)
        base_stations = base["base_stations"]
        edge_nodes = base["edge_nodes"]
        obstacles = base["obstacles"]
        network_nodes = base.get("network_nodes", [])

        road_by_id = {str(road["id"]): road for road in roads}
        road_by_route_id = {
            str(route["id"]): road_by_id[str(route["road_segment_ids"][0])]
            for route in base["routes"]
            if route["road_segment_ids"] and str(route["road_segment_ids"][0]) in road_by_id
        }

        enriched_vehicles = []
        for vehicle in vehicles:
            route_id = str(vehicle.get("current_route_id") or base["routes"][0]["id"])
            road = road_by_route_id.get(route_id, roads[0])
            enriched_vehicles.append(
                attach_v2x_network_state(vehicle, base_stations, edge_nodes, obstacles, float(road["obstacle_risk"]))
            )

        return {
            **base,
            "mode": "sumo_traci",
            "adapter": "traci",
            "simulation_label": "SUMO TraCI Simulation",
            "message": "SUMO/TraCI stream is active.",
            "vehicles": enriched_vehicles,
            "roads": roads,
            "network_nodes": network_nodes,
        }

    def _read_vehicles(self, base: dict[str, object]) -> list[dict[str, object]]:
        vehicle_ids = self.traci.vehicle_ids()
        fallback_vehicles = {str(vehicle["id"]): vehicle for vehicle in base["vehicles"]}  # type: ignore[index]
        routes = base["routes"]  # type: ignore[index]
        vehicles = []

        for index, vehicle_id in enumerate(vehicle_ids):
            fallback = deepcopy(fallback_vehicles.get(vehicle_id) or list(fallback_vehicles.values())[index % len(fallback_vehicles)])
            x, y = self.traci.vehicle_position(vehicle_id)
            lat, lng = self.converter.sumo_to_lat_lng(x, y)
            route_edges = self.traci.vehicle_route(vehicle_id)
            fallback.update(
                {
                    "id": vehicle_id,
                    "name": fallback.get("name", f"SUMO Vehicle {vehicle_id}"),
                    "latitude": round(lat, 6),
                    "longitude": round(lng, 6),
                    "speed": round(self.traci.vehicle_speed_kmh(vehicle_id), 1),
                    "heading": round(self.traci.vehicle_heading(vehicle_id), 1),
                    "current_edge_id": self.traci.vehicle_road_id(vehicle_id),
                    "current_route_id": self._route_id_from_edges(route_edges, routes),
                    "sumo_route_edges": route_edges,
                    "route_status": "moving_sumo_traci",
                }
            )
            vehicles.append(fallback)

        return vehicles or deepcopy(base["vehicles"])  # type: ignore[index]

    def _read_road_congestion(self, base: dict[str, object]) -> list[dict[str, object]]:
        roads = deepcopy(base["roads"])  # type: ignore[index]
        edge_ids = self.traci.edge_ids()
        lane_ids = self.traci.lane_ids()

        for road in roads:
            road_edges = road.get("sumo_edge_ids") or [road.get("externalId"), road.get("id")]
            matched_edges = [edge for edge in road_edges if edge in edge_ids]
            road_lanes = road.get("sumo_lane_ids") or []
            matched_lanes = [lane for lane in road_lanes if lane in lane_ids]

            if matched_edges:
                vehicle_counts = [self.traci.edge_vehicle_count(edge) for edge in matched_edges]
                speeds = [self.traci.edge_mean_speed_kmh(edge) for edge in matched_edges]
                capacity_hint = len(matched_edges) * 18
            elif matched_lanes:
                vehicle_counts = [self.traci.lane_vehicle_count(lane) for lane in matched_lanes]
                speeds = [self.traci.lane_mean_speed_kmh(lane) for lane in matched_lanes]
                capacity_hint = len(matched_lanes) * 12
            else:
                continue

            avg_speed = sum(speeds) / len(speeds) if speeds else float(road["average_speed"])
            congestion = min(0.98, sum(vehicle_counts) / max(capacity_hint, 1))
            road["average_speed"] = round(avg_speed, 1)
            road["congestion_score"] = round(congestion, 3)
            road["source"] = "sumo"

        return roads

    def _route_id_from_edges(self, route_edges: list[str], routes: list[dict[str, object]]) -> str:
        if not route_edges:
            return str(routes[0]["id"])

        for route in routes:
            sumo_edges = route.get("sumo_edge_ids")
            if sumo_edges and any(edge in sumo_edges for edge in route_edges):
                return str(route["id"])
        return str(routes[0]["id"])

    def _fallback_step(self) -> dict[str, object]:
        return self.mock.step(self.last_error or "SUMO unavailable; mock simulator is active.")

    def _fallback_snapshot(self) -> dict[str, object]:
        return self.mock.snapshot(self.last_error or "SUMO unavailable; using mock mobility state.")

    def _activate_fallback(self, reason: str) -> bool:
        self._disconnect()
        self.connected = False
        self.using_fallback = True
        should_log = self.last_error != reason
        self.last_error = reason
        if should_log:
            logger.warning(reason)
        return False

    def _disconnect(self) -> None:
        self.traci.close()
        self.runner.stop()
