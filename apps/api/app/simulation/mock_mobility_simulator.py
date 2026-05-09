from __future__ import annotations

import math
from copy import deepcopy

from app.services.mock_repository import (
    get_base_stations,
    get_edge_nodes,
    get_obstacles,
    get_roads,
    get_routes,
    get_vehicles,
)
from app.services.v2x_network_layer import attach_v2x_network_state, build_network_nodes

LatLng = list[float]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _distance_km(a: LatLng, b: LatLng) -> float:
    lat_km = (a[0] - b[0]) * 111.0
    lng_km = (a[1] - b[1]) * 88.0
    return math.hypot(lat_km, lng_km)


def _heading_degrees(a: LatLng, b: LatLng) -> float:
    dy = b[0] - a[0]
    dx = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return round((math.degrees(math.atan2(dx, dy)) + 360) % 360, 1)


def _polyline_length(points: list[LatLng]) -> float:
    return sum(_distance_km(points[index], points[index + 1]) for index in range(len(points) - 1))


def _interpolate(points: list[LatLng], progress: float) -> tuple[LatLng, float]:
    if len(points) < 2:
        return points[0], 0

    total = _polyline_length(points)
    target = (progress % 1.0) * total
    covered = 0.0

    for index in range(len(points) - 1):
        start = points[index]
        end = points[index + 1]
        segment_length = _distance_km(start, end)
        if covered + segment_length >= target:
            ratio = 0 if segment_length == 0 else (target - covered) / segment_length
            position = [
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            ]
            return position, _heading_degrees(start, end)
        covered += segment_length

    return points[-1], _heading_degrees(points[-2], points[-1])


class MockMobilitySimulator:
    """Deterministic in-memory vehicle mobility simulation for local demos."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.tick = 0
        self.scenario_debug: dict[str, object] | None = None
        self.vehicles = get_vehicles()
        self.roads = get_roads()
        self.routes = get_routes()
        self.base_stations = get_base_stations()
        self.edge_nodes = get_edge_nodes()
        self.obstacles = get_obstacles()
        self._base_station_loads = {
            station["id"]: int(station["current_connected_vehicles"]) for station in self.base_stations
        }
        self._route_progress = {
            vehicle["id"]: (index * 0.23) % 1.0 for index, vehicle in enumerate(self.vehicles)
        }
        self._refresh_vehicle_network_state()
        self._update_obstacle_affected_vehicles()

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": "mock",
            "tick": self.tick,
            "vehicles": deepcopy(self.vehicles),
            "roads": deepcopy(self.roads),
            "base_stations": deepcopy(self.base_stations),
            "edge_nodes": deepcopy(self.edge_nodes),
            "network_nodes": build_network_nodes(self.base_stations, self.edge_nodes),
            "obstacles": deepcopy(self.obstacles),
            "routes": deepcopy(self.routes),
            "selected_scenario": deepcopy(self.scenario_debug),
        }

    def step(self) -> dict[str, object]:
        self.tick += 1
        self._update_roads()
        self._update_vehicles()
        return self.snapshot()

    def load_coordinate_scenario(self, scenario: dict[str, object]) -> None:
        route_points = [[float(point["lat"]), float(point["lng"])] for point in scenario["route_geometry"]]  # type: ignore[index]
        if len(route_points) < 2:
            raise ValueError("Coordinate scenario route requires at least two points.")
        route_id = str(scenario.get("route_id", "sumo-route-user-selected"))
        road_id = "road-user-selected"
        route_length_km = _polyline_length(route_points)
        speed = 42 if scenario.get("traffic_density") != "high" else 30
        congestion = {"low": 0.22, "medium": 0.48, "high": 0.76}.get(str(scenario.get("traffic_density", "medium")), 0.48)
        self.routes = [
            {
                "id": route_id,
                "name": "User-selected SUMO-synchronized route",
                "geometry": route_points,
                "road_segment_ids": [road_id],
                "distance_km": round(route_length_km, 3),
                "road_congestion": congestion,
                "vehicle_speed_kmh": speed,
                "obstacle_risk": 0.18,
                "base_station_distance_km": 0.5,
                "base_station_congestion": 0.39,
                "network_latency_ms": 18,
                "edge_node_distance_km": 0.7,
                "v2x_stability": 0.9,
                "sumo_edge_ids": scenario.get("sumo_edge_ids", []),
            }
        ]
        self.roads = [
            {
                "id": road_id,
                "name": "User-selected SUMO road corridor",
                "geometry": route_points,
                "average_speed": speed,
                "congestion_score": congestion,
                "travel_time_estimate": round(route_length_km / max(speed, 1) * 60, 2),
                "obstacle_risk": 0.18,
                "source": scenario.get("traffic_source", "synthetic"),
                "sumo_edge_ids": scenario.get("sumo_edge_ids", []),
            }
        ]
        surrounding_count = min(int(scenario.get("surrounding_vehicle_count", 20)), 300)
        self.vehicles = []
        for index in range(surrounding_count + 1):
            vehicle_id = "EGO-001" if index == 0 else f"SV-{index:03d}"
            progress = 0.0 if index == 0 else (index / max(surrounding_count, 1)) % 1.0
            position, heading = _interpolate(route_points, progress)
            self.vehicles.append(
                {
                    "id": vehicle_id,
                    "name": "Ego vehicle" if index == 0 else f"Surrounding vehicle {index:03d}",
                    "latitude": round(position[0], 6),
                    "longitude": round(position[1], 6),
                    "speed": speed + math.sin(index) * 5,
                    "heading": heading,
                    "current_route_id": route_id,
                    "connected_base_station_id": "BS-01",
                    "connected_edge_node_id": "EDGE-A",
                    "current_latency_ms": 24,
                    "obstacle_risk": 0.2,
                    "route_status": "moving_mock_simulation",
                    "current_edge_id": (scenario.get("sumo_edge_ids") or ["fallback-edge"])[0],
                }
            )
        self._route_progress = {vehicle["id"]: (index / max(len(self.vehicles), 1)) % 1.0 for index, vehicle in enumerate(self.vehicles)}
        self._route_progress["EGO-001"] = 0.0
        self.scenario_debug = deepcopy(scenario)
        self.tick = 0
        self._refresh_vehicle_network_state()
        self._update_obstacle_affected_vehicles()

    def _update_roads(self) -> None:
        for index, road in enumerate(self.roads):
            wave = math.sin(self.tick / 5 + index * 0.9)
            congestion = _clamp(float(road["congestion_score"]) + wave * 0.035, 0.08, 0.96)
            obstacle_risk = _clamp(float(road["obstacle_risk"]) + math.cos(self.tick / 7 + index) * 0.025, 0.04, 0.92)
            average_speed = _clamp(62 - congestion * 38 - obstacle_risk * 8, 8, 64)
            road["congestion_score"] = round(congestion, 3)
            road["obstacle_risk"] = round(obstacle_risk, 3)
            road["average_speed"] = round(average_speed, 1)
            road["travel_time_estimate"] = round(_polyline_length(road["geometry"]) / average_speed * 60, 2)

    def _update_vehicles(self) -> None:
        route_by_id = {route["id"]: route for route in self.routes}
        road_by_id = {road["id"]: road for road in self.roads}
        station_by_id = {station["id"]: station for station in self.base_stations}

        for station in self.base_stations:
            station["current_connected_vehicles"] = self._base_station_loads[station["id"]]

        for index, vehicle in enumerate(self.vehicles):
            route = route_by_id[str(vehicle["current_route_id"])]
            road = road_by_id[str(route["road_segment_ids"][0])]
            vehicle_id = str(vehicle["id"])
            speed = _clamp(
                float(road["average_speed"]) + math.sin(self.tick / 3 + index) * 4,
                6,
                72,
            )
            self._route_progress[vehicle_id] = (self._route_progress[vehicle_id] + speed / 1800) % 1.0
            position, heading = _interpolate(route["geometry"], self._route_progress[vehicle_id])
            vehicle.update(
                {
                    "latitude": round(position[0], 6),
                    "longitude": round(position[1], 6),
                    "speed": round(speed, 1),
                    "heading": heading,
                    "current_edge_id": (route.get("sumo_edge_ids") or [road["id"]])[0],
                    "route_status": "moving_mock_simulation",
                }
            )
            networked_vehicle = attach_v2x_network_state(
                vehicle,
                self.base_stations,
                self.edge_nodes,
                self.obstacles,
                float(road["obstacle_risk"]),
            )
            vehicle.update(networked_vehicle)
            station = station_by_id.get(vehicle["connected_base_station_id"])
            if station:
                station["current_connected_vehicles"] += 1

        self._update_obstacle_affected_vehicles()

    def _refresh_vehicle_network_state(self) -> None:
        route_by_id = {route["id"]: route for route in self.routes}
        road_by_id = {road["id"]: road for road in self.roads}
        station_by_id = {station["id"]: station for station in self.base_stations}

        for station in self.base_stations:
            station["current_connected_vehicles"] = self._base_station_loads[station["id"]]

        for vehicle in self.vehicles:
            route = route_by_id[str(vehicle["current_route_id"])]
            road = road_by_id[str(route["road_segment_ids"][0])]
            networked_vehicle = attach_v2x_network_state(
                vehicle,
                self.base_stations,
                self.edge_nodes,
                self.obstacles,
                float(road["obstacle_risk"]),
            )
            vehicle.update(networked_vehicle)
            station = station_by_id.get(vehicle["connected_base_station_id"])
            if station:
                station["current_connected_vehicles"] += 1

    def _nearest(self, items: list[dict[str, object]], position: LatLng) -> dict[str, object]:
        return min(
            items,
            key=lambda item: _distance_km(position, [float(item["latitude"]), float(item["longitude"])]),
        )

    def _obstacle_risk(self, position: LatLng, road_risk: float) -> float:
        nearest = min(
            _distance_km(position, [float(obstacle["latitude"]), float(obstacle["longitude"])])
            for obstacle in self.obstacles
        )
        proximity_risk = _clamp(1 - nearest / 0.9, 0, 1)
        return _clamp(road_risk * 0.55 + proximity_risk * 0.45, 0, 1)

    def _update_obstacle_affected_vehicles(self) -> None:
        for obstacle in self.obstacles:
            obstacle_position = [float(obstacle["latitude"]), float(obstacle["longitude"])]
            radius_km = float(obstacle["radius_m"]) / 1000
            affected = []
            for vehicle in self.vehicles:
                vehicle_position = [float(vehicle["latitude"]), float(vehicle["longitude"])]
                near_zone = _distance_km(vehicle_position, obstacle_position) <= radius_km * 2.5
                blocks_link = any(item["id"] == obstacle["id"] for item in vehicle.get("blocking_obstacles", []))
                if near_zone or blocks_link:
                    affected.append(vehicle["id"])
            obstacle["affected_vehicle_ids"] = affected
