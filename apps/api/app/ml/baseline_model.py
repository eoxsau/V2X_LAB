from app.services.obstacle_blockage_layer import evaluate_obstacle_exposure
from app.services.v2x_network_layer import (
    calculate_base_station_congestion,
    calculate_distance_km,
    calculate_network_latency_ms,
    nearest_edge_node,
    rank_base_stations,
)


def analyze_routes(
    routes: list[dict[str, object]],
    vehicles: list[dict[str, object]] | None = None,
    roads: list[dict[str, object]] | None = None,
    base_stations: list[dict[str, object]] | None = None,
    edge_nodes: list[dict[str, object]] | None = None,
    obstacles: list[dict[str, object]] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, object]:
    """Deterministic route optimizer scaffold.

    This is intentionally not a production ML model. It gives the platform a
    stable comparison layer for shortest, traffic-aware, and network-aware AI
    route decisions until a real model is connected later.
    """
    selected_vehicle = _select_vehicle(vehicles or [], vehicle_id)
    scored_routes = [
        _evaluate_route(route, roads or [], base_stations or [], edge_nodes or [], obstacles or [], selected_vehicle)
        for route in routes
    ]
    _normalize_quality_scores(scored_routes)

    shortest_route = min(scored_routes, key=lambda item: float(item["distance_km"]))
    traffic_aware_route = min(
        scored_routes,
        key=lambda item: (
            float(item["score_breakdown"]["travel_time_score"]) * 0.25
            + float(item["score_breakdown"]["road_congestion_score"]) * 2.0
            + float(item["score_breakdown"]["obstacle_risk_score"])
        ),
    )
    network_aware_route = min(scored_routes, key=lambda item: float(item["total_score"]))

    for route in scored_routes:
        route["route_roles"] = []
        if route["id"] == shortest_route["id"]:
            route["route_roles"].append("shortest_route_baseline")
        if route["id"] == traffic_aware_route["id"]:
            route["route_roles"].append("traffic_aware_route")
        if route["id"] == network_aware_route["id"]:
            route["route_roles"].append("network_aware_ai_route")

    return {
        "mode": "deterministic_network_aware_route_optimizer",
        "score_direction": "lower_total_score_is_better",
        "selected_vehicle_id": selected_vehicle.get("id") if selected_vehicle else None,
        "shortest_route_baseline": shortest_route,
        "traffic_aware_route": traffic_aware_route,
        "recommended_route": network_aware_route,
        "routes": sorted(scored_routes, key=lambda item: float(item["total_score"])),
        "note": "Deterministic AI-assisted baseline only. No autonomous-driving control is connected.",
    }


def _select_vehicle(vehicles: list[dict[str, object]], vehicle_id: str | None) -> dict[str, object]:
    if vehicle_id:
        match = next((vehicle for vehicle in vehicles if vehicle.get("id") == vehicle_id), None)
        if match:
            return match
    return vehicles[0] if vehicles else {}


def _evaluate_route(
    route: dict[str, object],
    roads: list[dict[str, object]],
    base_stations: list[dict[str, object]],
    edge_nodes: list[dict[str, object]],
    obstacles: list[dict[str, object]],
    vehicle: dict[str, object],
) -> dict[str, object]:
    geometry = _route_geometry(route)
    road_stats = _road_stats(route, roads)
    network_stats = _network_stats(geometry, base_stations, edge_nodes, obstacles, road_stats["obstacle_risk"])
    speed_kmh = max(float(route.get("vehicle_speed_kmh", vehicle.get("speed", 40))), 5)
    congestion_multiplier = 1 + road_stats["road_congestion"] * 0.55
    travel_time_minutes = float(route["distance_km"]) / speed_kmh * 60 * congestion_multiplier

    score_breakdown = {
        "travel_time_score": travel_time_minutes * 4.2,
        "road_congestion_score": road_stats["road_congestion"] * 24,
        "predicted_network_latency_score": network_stats["expected_latency_ms"] * 0.7,
        "base_station_congestion_score": network_stats["expected_base_station_congestion"] * 0.2,
        "obstacle_risk_score": network_stats["expected_obstacle_risk"] * 30,
        "edge_latency_score": network_stats["expected_edge_latency_ms"] * 0.75,
    }
    total_score = sum(score_breakdown.values())
    quality_score = _clamp(100 - total_score * 0.72, 0, 100)

    rounded_breakdown = {key: round(value, 2) for key, value in score_breakdown.items()}
    rounded_breakdown["total_score"] = round(total_score, 2)
    rounded_breakdown["quality_score"] = round(quality_score, 1)

    return {
        **route,
        "travel_time_estimate_min": round(travel_time_minutes, 2),
        "expected_latency_ms": round(network_stats["expected_latency_ms"], 2),
        "expected_base_station_congestion": round(network_stats["expected_base_station_congestion"], 2),
        "expected_obstacle_risk": round(network_stats["expected_obstacle_risk"], 3),
        "expected_edge_latency_ms": round(network_stats["expected_edge_latency_ms"], 2),
        "score_breakdown": rounded_breakdown,
        "total_score": round(total_score, 2),
        "quality_score": round(quality_score, 1),
        "network_evaluation": network_stats,
        "risky_segments": _risky_segments(geometry, network_stats["risk_sample_indexes"]),
    }


def _normalize_quality_scores(scored_routes: list[dict[str, object]]) -> None:
    totals = [float(route["total_score"]) for route in scored_routes]
    if not totals:
        return

    best = min(totals)
    worst = max(totals)
    spread = max(worst - best, 0.01)
    for route in scored_routes:
        normalized = (float(route["total_score"]) - best) / spread
        quality_score = round(_clamp(90 - normalized * 22, 0, 100), 1)
        route["quality_score"] = quality_score
        route["score_breakdown"]["quality_score"] = quality_score


def _road_stats(route: dict[str, object], roads: list[dict[str, object]]) -> dict[str, float]:
    ids = set(route.get("road_segment_ids", []))
    matched = [road for road in roads if road.get("id") in ids]
    if not matched:
        return {
            "road_congestion": float(route.get("road_congestion", 0.4)),
            "obstacle_risk": float(route.get("obstacle_risk", 0.15)),
        }

    return {
        "road_congestion": _average(float(road["congestion_score"]) for road in matched),
        "obstacle_risk": _average(float(road["obstacle_risk"]) for road in matched),
    }


def _network_stats(
    geometry: list[list[float]],
    base_stations: list[dict[str, object]],
    edge_nodes: list[dict[str, object]],
    obstacles: list[dict[str, object]],
    road_obstacle_risk: float,
) -> dict[str, object]:
    if not base_stations or not edge_nodes:
        return {
            "expected_latency_ms": 40.0,
            "expected_base_station_congestion": 55.0,
            "expected_obstacle_risk": road_obstacle_risk,
            "expected_edge_latency_ms": 14.0,
            "connected_base_station_ids": [],
            "primary_edge_node_id": None,
            "handoff_count": 0,
            "connectivity_samples": [],
            "risk_sample_indexes": [],
        }

    samples = _sample_route_points(geometry)
    connected_station_ids: list[str] = []
    latency_values: list[float] = []
    congestion_values: list[float] = []
    obstacle_values: list[float] = []
    edge_latency_values: list[float] = []
    connectivity_samples = []
    risk_sample_indexes: list[int] = []

    for index, point in enumerate(samples):
        nearby = rank_base_stations(point, base_stations, limit=1)
        selected = next(station for station in base_stations if station["id"] == nearby[0]["id"])
        edge_node = nearest_edge_node(point, edge_nodes)
        exposure = evaluate_obstacle_exposure(
            point,
            [float(selected["latitude"]), float(selected["longitude"])],
            obstacles,
            road_obstacle_risk,
        )
        station_congestion = calculate_base_station_congestion(selected)
        latency = calculate_network_latency_ms(
            float(nearby[0]["distance_km"]),
            station_congestion,
            edge_node,
            float(exposure["network_blockage_penalty_ms"]),
        )

        connected_station_ids.append(str(selected["id"]))
        latency_values.append(float(latency["total_latency_ms"]))
        congestion_values.append(station_congestion)
        obstacle_values.append(float(exposure["obstacle_risk"]))
        edge_latency_values.append(float(edge_node["edge_latency_ms"]))
        if float(exposure["obstacle_risk"]) >= 0.45 or float(exposure["network_blockage_risk"]) >= 0.28:
            risk_sample_indexes.append(index)
        connectivity_samples.append(
            {
                "index": index,
                "latitude": round(point[0], 6),
                "longitude": round(point[1], 6),
                "base_station_id": selected["id"],
                "base_station_distance_km": nearby[0]["distance_km"],
                "base_station_congestion": round(station_congestion, 2),
                "edge_node_id": edge_node["id"],
                "expected_latency_ms": latency["total_latency_ms"],
                "obstacle_risk": exposure["obstacle_risk"],
                "network_blockage_risk": exposure["network_blockage_risk"],
            }
        )

    handoff_count = max(len(_dedupe_adjacent(connected_station_ids)) - 1, 0)

    return {
        "expected_latency_ms": _average(latency_values),
        "expected_base_station_congestion": _average(congestion_values),
        "expected_obstacle_risk": max(obstacle_values) if obstacle_values else road_obstacle_risk,
        "expected_edge_latency_ms": _average(edge_latency_values),
        "connected_base_station_ids": sorted(set(connected_station_ids)),
        "primary_edge_node_id": _most_common([str(sample["edge_node_id"]) for sample in connectivity_samples]),
        "handoff_count": handoff_count,
        "connectivity_samples": connectivity_samples,
        "risk_sample_indexes": risk_sample_indexes,
    }


def _route_geometry(route: dict[str, object]) -> list[list[float]]:
    return [[float(point[0]), float(point[1])] for point in route.get("geometry", [])]


def _sample_route_points(points: list[list[float]]) -> list[list[float]]:
    if len(points) <= 3:
        return points

    samples = [points[0]]
    for start, end in zip(points, points[1:]):
        midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]
        samples.extend([midpoint, end])
    return samples


def _risky_segments(points: list[list[float]], sample_indexes: list[int]) -> list[list[list[float]]]:
    if not sample_indexes or len(points) < 2:
        return []

    segments = []
    for index in sample_indexes:
        segment_index = min(index // 2, len(points) - 2)
        segment = [points[segment_index], points[segment_index + 1]]
        if segment not in segments:
            segments.append(segment)
    return segments


def _average(values: object) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _dedupe_adjacent(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    return deduped


def _most_common(items: list[str]) -> str | None:
    if not items:
        return None
    return max(set(items), key=items.count)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
