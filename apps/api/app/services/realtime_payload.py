from __future__ import annotations

import math
from statistics import mean
from typing import Any

from app.ai import build_ai_ready_analysis
from app.assistant.explainer import explain_current_recommendation
from app.llm import get_llm_status
from app.ml.baseline_model import analyze_routes
from app.rl import build_rl_ready_payload


def build_realtime_payload(state: dict[str, object]) -> dict[str, object]:
    traffic = state["traffic"]  # type: ignore[index]
    vehicles = list(traffic["vehicles"])  # type: ignore[index]
    roads = list(traffic["roads"])  # type: ignore[index]
    base_stations = list(traffic["base_stations"])  # type: ignore[index]
    edge_nodes = list(traffic["edge_nodes"])  # type: ignore[index]
    network_nodes = list(traffic.get("network_nodes", []))  # type: ignore[union-attr]
    obstacles = list(traffic["obstacles"])  # type: ignore[index]
    routes = list(traffic["routes"])  # type: ignore[index]
    selected_vehicle_id = str(vehicles[0]["id"]) if vehicles else None
    ai_analysis = analyze_routes(
        routes,
        vehicles=vehicles,
        roads=roads,
        base_stations=base_stations,
        edge_nodes=edge_nodes,
        obstacles=obstacles,
        vehicle_id=selected_vehicle_id,
    )
    assistant = explain_current_recommendation(selected_vehicle_id)
    connection_lines = _connection_lines(vehicles, base_stations)
    ego_context = _ego_vehicle_context(vehicles)
    display_vehicles = _display_vehicles(vehicles, ego_context)
    ai_ready = build_ai_ready_analysis(display_vehicles, roads, routes, edge_nodes)
    rl_ready = build_rl_ready_payload(display_vehicles, roads, network_nodes, routes)
    metrics = _metrics(vehicles, roads, base_stations, ai_analysis)
    route_geometry = _active_route_geometry(display_vehicles, routes)
    event_logs = _event_logs(state, vehicles, ai_analysis, metrics)

    return {
        "simulation_id": state["simulation_id"],
        "simulator_mode": state["simulator_mode"],
        "running": state["running"],
        "tick": traffic.get("tick", state["tick"]),  # type: ignore[union-attr]
        "updated_at": state["updated_at"],
        "vehicles": display_vehicles,
        "ego_vehicle": ego_context["ego_vehicle"],
        "surrounding_vehicles": ego_context["surrounding_vehicles"],
        "vehicle_relationships": ego_context["vehicle_relationships"],
        "roads": roads,
        "road_segments": roads,
        "base_stations": base_stations,
        "edge_nodes": edge_nodes,
        "network_nodes": network_nodes,
        "obstacles": obstacles,
        "current_routes": _current_routes(vehicles, routes),
        "recommended_routes": _recommended_routes(ai_analysis),
        "connection_lines": connection_lines,
        "route_geometry": route_geometry,
        "ai_analysis": ai_analysis,
        "ai_ready": ai_ready,
        "rl": rl_ready,
        "metrics": metrics,
        "assistant_messages": _assistant_messages(assistant),
        "llm_status": get_llm_status(),
        "event_logs": event_logs,
        "traffic": {
            "source": traffic.get("traffic_source", "synthetic"),
            "status": traffic.get("traffic_status", "fallback"),
            "last_sync_at": traffic.get("traffic_last_sync_at"),
        },
        "simulator": {
            "mode": traffic.get("mode", state["simulator_mode"]),
            "adapter": traffic.get("adapter", "mock"),
            "label": traffic.get("simulation_label", "Mock Simulation Mode"),
            "status": "sumo_connected" if traffic.get("adapter") == "traci" else "mock",
            "message": traffic.get("message"),
            "sumo_enabled": traffic.get("sumo_enabled", False),
            "sumo_gui_enabled": traffic.get("sumo_gui_enabled", False),
            "using_mock_fallback": traffic.get("adapter") != "traci",
        },
        "debug": _scenario_debug(traffic),
    }


def _current_routes(vehicles: list[dict[str, Any]], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_route_ids = {vehicle.get("current_route_id") for vehicle in vehicles}
    return [{**route, "role": "current_route"} for route in routes if route.get("id") in active_route_ids]


def _active_route_geometry(vehicles: list[dict[str, Any]], routes: list[dict[str, Any]]) -> list[dict[str, float]]:
    active_id = vehicles[0].get("current_route_id") if vehicles else None
    route = next((item for item in routes if item.get("id") == active_id), routes[0] if routes else {})
    return [{"lat": float(point[0]), "lng": float(point[1])} for point in route.get("geometry", [])]


def _recommended_routes(ai_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**ai_analysis["shortest_route_baseline"], "role": "shortest_route_baseline"},
        {**ai_analysis["traffic_aware_route"], "role": "traffic_aware_route"},
        {**ai_analysis["recommended_route"], "role": "network_aware_ai_route"},
    ]


def _connection_lines(
    vehicles: list[dict[str, Any]],
    base_stations: list[dict[str, Any]],
) -> list[dict[str, object]]:
    station_by_id = {station["id"]: station for station in base_stations}
    lines = []
    for vehicle in vehicles:
        station = station_by_id.get(vehicle.get("connected_base_station_id"))
        if not station:
            continue
        lines.append(
            {
                "id": f"{vehicle['id']}->{station['id']}",
                "vehicle_id": vehicle["id"],
                "base_station_id": station["id"],
                "latency_ms": vehicle.get("current_latency_ms"),
                "points": [
                    [vehicle["latitude"], vehicle["longitude"]],
                    [station["latitude"], station["longitude"]],
                ],
            }
        )
    return lines


def _ego_vehicle_context(vehicles: list[dict[str, Any]], radius_km: float = 1.2) -> dict[str, object]:
    if not vehicles:
        return {
            "ego_vehicle": None,
            "ego_vehicle_id": None,
            "surrounding_vehicles": [],
            "vehicle_relationships": [],
        }

    ego = {
        **vehicles[0],
        "vehicle_type": "ego_vehicle",
        "lat": vehicles[0].get("latitude"),
        "lng": vehicles[0].get("longitude"),
        "route_id": vehicles[0].get("current_route_id"),
    }
    relationships = []
    surrounding = []

    for vehicle in vehicles[1:]:
        distance_km = _distance_km(ego, vehicle)
        heading_difference = _heading_difference(float(ego.get("heading", 0)), float(vehicle.get("heading", 0)))
        relation = {
            "id": f"{ego['id']}->{vehicle['id']}",
            "ego_vehicle_id": ego["id"],
            "vehicle_id": vehicle["id"],
            "distance_to_ego": round(distance_km, 3),
            "relative_speed": round(float(vehicle.get("speed", 0)) - float(ego.get("speed", 0)), 1),
            "heading_difference": round(heading_difference, 1),
            "direction_relation": _direction_relation(heading_difference),
            "proximity_risk": _proximity_risk(distance_km, heading_difference),
            "risk_level": _risk_level(_proximity_risk(distance_km, heading_difference)),
            "points": [
                [ego["latitude"], ego["longitude"]],
                [vehicle["latitude"], vehicle["longitude"]],
            ],
        }
        if distance_km <= radius_km:
            relationships.append(relation)

        surrounding.append(
            {
                **vehicle,
                "vehicle_type": _vehicle_type(vehicle),
                "distance_to_ego": relation["distance_to_ego"],
                "relative_speed": relation["relative_speed"],
                "heading_difference": relation["heading_difference"],
                "direction_relation": relation["direction_relation"],
                "proximity_risk": relation["proximity_risk"],
                "risk_level": relation["risk_level"],
            }
        )

    surrounding.sort(key=lambda item: float(item["distance_to_ego"]))
    nearest = surrounding[0] if surrounding else None
    ego["nearest_vehicle_distance"] = nearest["distance_to_ego"] if nearest else None
    ego["collision_proximity_risk"] = nearest["proximity_risk"] if nearest else 0
    ego["network_stability_score"] = _network_stability_score(ego)

    return {
        "ego_vehicle": ego,
        "ego_vehicle_id": ego["id"],
        "surrounding_vehicles": surrounding,
        "vehicle_relationships": relationships,
    }


def _scenario_debug(traffic: dict[str, Any]) -> dict[str, object] | None:
    scenario = traffic.get("selected_scenario")
    if not isinstance(scenario, dict):
        return None
    return {
        "selected_origin_coordinate": scenario.get("origin"),
        "selected_destination_coordinate": scenario.get("destination"),
        "matched_sumo_origin_edge": scenario.get("matched_origin_edge"),
        "matched_sumo_destination_edge": scenario.get("matched_destination_edge"),
        "route_length_m": scenario.get("route_length_m"),
        "coordinate_conversion_status": scenario.get("coordinate_conversion_status"),
        "sumo_connection_status": traffic.get("adapter", "mock_fallback"),
        "its_api_status": scenario.get("its_status", traffic.get("traffic_status", "fallback")),
        "sumo_network_source": scenario.get("sumo_network_source"),
    }


def _display_vehicles(vehicles: list[dict[str, Any]], ego_context: dict[str, object]) -> list[dict[str, Any]]:
    ego = ego_context.get("ego_vehicle")
    surrounding = ego_context.get("surrounding_vehicles")
    if not isinstance(ego, dict) or not isinstance(surrounding, list):
        return vehicles
    surrounding_by_id = {str(vehicle["id"]): vehicle for vehicle in surrounding if isinstance(vehicle, dict)}
    return [
        ego if index == 0 else surrounding_by_id.get(str(vehicle.get("id")), {**vehicle, "vehicle_type": "surrounding_vehicle"})
        for index, vehicle in enumerate(vehicles)
    ]


def _distance_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat_km = (float(a["latitude"]) - float(b["latitude"])) * 111.0
    lng_km = (float(a["longitude"]) - float(b["longitude"])) * 88.0
    return math.hypot(lat_km, lng_km)


def _heading_difference(a: float, b: float) -> float:
    diff = abs((a - b + 180) % 360 - 180)
    return min(diff, 180.0)


def _direction_relation(heading_difference: float) -> str:
    if heading_difference <= 35:
        return "same_direction"
    if heading_difference >= 135:
        return "opposite_direction"
    return "crossing"


def _proximity_risk(distance_km: float, heading_difference: float) -> float:
    distance_risk = max(0.0, min(1.0, 1 - distance_km / 0.75))
    crossing_factor = 1.15 if 45 <= heading_difference <= 135 else 1.0
    return round(max(0.0, min(1.0, distance_risk * crossing_factor)), 3)


def _risk_level(risk: float) -> str:
    if risk >= 0.66:
        return "high"
    if risk >= 0.34:
        return "medium"
    return "low"


def _vehicle_type(vehicle: dict[str, Any]) -> str:
    route_status = str(vehicle.get("route_status", ""))
    vehicle_id = str(vehicle.get("id", ""))
    if "emergency" in route_status or vehicle_id.endswith("9"):
        return "emergency_vehicle"
    if vehicle_id.endswith("8"):
        return "heavy_vehicle"
    return "surrounding_vehicle"


def _network_stability_score(vehicle: dict[str, Any]) -> int:
    latency = float(vehicle.get("current_latency_ms", 0))
    blockage = float(vehicle.get("network_blockage_risk", 0))
    station_congestion = float(vehicle.get("base_station_congestion_score", 0)) / 100
    score = 100 - latency * 0.9 - blockage * 28 - station_congestion * 18
    return round(max(0, min(100, score)))


def _metrics(
    vehicles: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    base_stations: list[dict[str, Any]],
    ai_analysis: dict[str, Any],
) -> dict[str, object]:
    recommended_routes = ai_analysis.get("routes", [])
    return {
        "avg_vehicle_latency": round(_avg(vehicle.get("current_latency_ms", 0) for vehicle in vehicles), 2),
        "current_latency_ms": round(float(vehicles[0].get("current_latency_ms", 0)), 2) if vehicles else 0,
        "avg_predicted_latency": round(
            _avg(route.get("expected_latency_ms", 0) for route in recommended_routes),
            2,
        ),
        "avg_road_congestion": round(_avg(float(road.get("congestion_score", 0)) * 100 for road in roads), 2),
        "road_congestion_score": round(float(roads[0].get("congestion_score", 0)) * 100, 2) if roads else 0,
        "network_congestion_score": round(float(base_stations[0].get("congestion_score", 0)), 2) if base_stations else 0,
        "avg_base_station_congestion": round(
            _avg(float(station.get("congestion_score", 0)) for station in base_stations),
            2,
        ),
        "avg_obstacle_risk": round(_avg(float(vehicle.get("obstacle_risk", 0)) for vehicle in vehicles), 3),
        "route_change_count": sum(
            1 for vehicle in vehicles if vehicle.get("current_route_id") == ai_analysis["recommended_route"]["id"]
        ),
        "base_station_handoff_count": sum(
            int(route.get("network_evaluation", {}).get("handoff_count", 0)) for route in recommended_routes
        ),
        "route_score": ai_analysis["recommended_route"]["total_score"],
    }


def _assistant_messages(assistant: dict[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "role": "assistant",
            "title": "Assistant Recommendation",
            "content": assistant["assistant_recommendation"],
        },
        {"role": "assistant", "title": "Main cause", "content": assistant["main_cause"]},
        {"role": "assistant", "title": "Expected improvement", "content": assistant["expected_improvement"]},
        {"role": "assistant", "title": "Recommended action", "content": assistant["recommended_action"]},
        {"role": "system", "title": "Confidence", "content": assistant["confidence"]},
    ]


def _event_logs(
    state: dict[str, object],
    vehicles: list[dict[str, Any]],
    ai_analysis: dict[str, Any],
    metrics: dict[str, object],
) -> list[str]:
    vehicle = vehicles[0] if vehicles else {}
    return [
        (
            f"tick {state['tick']}: {vehicle.get('id', 'vehicle')} latency "
            f"{vehicle.get('current_latency_ms', 'n/a')} ms via {vehicle.get('connected_base_station_id', 'n/a')}"
        ),
        f"AI-ready route candidate {ai_analysis['recommended_route']['id']} scored {ai_analysis['recommended_route']['total_score']}",
        f"avg road congestion {metrics['avg_road_congestion']}%, avg obstacle risk {metrics['avg_obstacle_risk']}",
        f"handoff opportunities observed: {metrics['base_station_handoff_count']}",
    ]


def _avg(values: object) -> float:
    items = [float(value) for value in values]
    return mean(items) if items else 0.0
