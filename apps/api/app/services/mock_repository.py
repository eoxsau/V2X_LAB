from copy import deepcopy

from app.services.v2x_network_layer import generate_synthetic_base_stations


VEHICLES: list[dict[str, object]] = [
    {
        "id": "AV-104",
        "name": "Autonomous Shuttle 104",
        "latitude": 37.5658,
        "longitude": 126.9821,
        "speed": 42,
        "heading": 38,
        "current_route_id": "route-c",
        "connected_base_station_id": "BS-01",
        "connected_edge_node_id": "EDGE-B",
        "current_latency_ms": 24,
        "obstacle_risk": 0.67,
        "route_status": "following_ai_route",
    },
    {
        "id": "AV-221",
        "name": "Autonomous Taxi 221",
        "latitude": 37.5615,
        "longitude": 126.9762,
        "speed": 36,
        "heading": 54,
        "current_route_id": "route-a",
        "connected_base_station_id": "BS-03",
        "connected_edge_node_id": "EDGE-A",
        "current_latency_ms": 31,
        "obstacle_risk": 0.41,
        "route_status": "evaluating",
    },
    {
        "id": "AV-309",
        "name": "Autonomous Delivery 309",
        "latitude": 37.5732,
        "longitude": 126.9796,
        "speed": 51,
        "heading": 124,
        "current_route_id": "route-b",
        "connected_base_station_id": "BS-04",
        "connected_edge_node_id": "EDGE-A",
        "current_latency_ms": 19,
        "obstacle_risk": 0.24,
        "route_status": "rule_baseline",
    },
]

ROADS: list[dict[str, object]] = [
    {
        "id": "road-1",
        "name": "Central corridor",
        "geometry": [[37.5752, 126.9734], [37.5714, 126.9781], [37.5667, 126.9828], [37.5621, 126.9862]],
        "average_speed": 34,
        "congestion_score": 0.72,
        "travel_time_estimate": 6.9,
        "obstacle_risk": 0.48,
    },
    {
        "id": "road-2",
        "name": "Myeongdong connector",
        "geometry": [[37.5591, 126.9737], [37.5631, 126.978], [37.5674, 126.9842], [37.5704, 126.991]],
        "average_speed": 41,
        "congestion_score": 0.48,
        "travel_time_estimate": 5.8,
        "obstacle_risk": 0.32,
    },
    {
        "id": "road-3",
        "name": "Gwanghwamun loop",
        "geometry": [[37.5584, 126.9722], [37.5623, 126.9768], [37.5663, 126.9814], [37.5704, 126.9866]],
        "average_speed": 46,
        "congestion_score": 0.39,
        "travel_time_estimate": 5.1,
        "obstacle_risk": 0.22,
    },
]

BASE_STATIONS: list[dict[str, object]] = generate_synthetic_base_stations()

EDGE_NODES: list[dict[str, object]] = [
    {
        "id": "EDGE-A",
        "name": "City Hall Edge",
        "latitude": 37.5707,
        "longitude": 126.9814,
        "capacity": 420,
        "compute_load": 0.42,
        "edge_latency_ms": 11.5,
        "source": "synthetic",
    },
    {
        "id": "EDGE-B",
        "name": "Myeongdong Edge",
        "latitude": 37.5619,
        "longitude": 126.9875,
        "capacity": 360,
        "compute_load": 0.51,
        "edge_latency_ms": 13.2,
        "source": "synthetic",
    },
    {
        "id": "EDGE-C",
        "name": "Jongno Research Edge",
        "latitude": 37.5738,
        "longitude": 126.9897,
        "capacity": 300,
        "compute_load": 0.36,
        "edge_latency_ms": 14.6,
        "source": "synthetic",
    },
]

OBSTACLES: list[dict[str, object]] = [
    {
        "id": "OBS-1",
        "type": "building_zone",
        "latitude": 37.5644,
        "longitude": 126.9844,
        "radius_m": 54,
        "severity": 0.74,
        "source": "synthetic",
    },
    {
        "id": "OBS-2",
        "type": "accident_zone",
        "latitude": 37.5711,
        "longitude": 126.9778,
        "radius_m": 42,
        "severity": 0.58,
        "source": "synthetic",
    },
    {
        "id": "OBS-3",
        "type": "construction_zone",
        "latitude": 37.5667,
        "longitude": 126.9894,
        "radius_m": 68,
        "severity": 0.63,
        "source": "synthetic",
    },
    {
        "id": "OBS-4",
        "type": "low_visibility_zone",
        "latitude": 37.5597,
        "longitude": 126.9748,
        "radius_m": 78,
        "severity": 0.49,
        "source": "synthetic",
    },
    {
        "id": "OBS-5",
        "type": "network_blockage_zone",
        "latitude": 37.5685,
        "longitude": 126.9828,
        "radius_m": 62,
        "severity": 0.82,
        "source": "synthetic",
    },
]

ROUTES: list[dict[str, object]] = [
    {
        "id": "route-a",
        "name": "Central fast route",
        "geometry": [[37.5752, 126.9734], [37.5714, 126.9781], [37.5667, 126.9828], [37.5621, 126.9862]],
        "road_segment_ids": ["road-1"],
        "distance_km": 4.8,
        "road_congestion": 0.52,
        "vehicle_speed_kmh": 42,
        "obstacle_risk": 0.18,
        "base_station_distance_km": 0.8,
        "base_station_congestion": 0.44,
        "network_latency_ms": 24,
        "edge_node_distance_km": 1.4,
        "v2x_stability": 0.88,
    },
    {
        "id": "route-b",
        "name": "Outer low-risk route",
        "geometry": [[37.5584, 126.9722], [37.5623, 126.9768], [37.5663, 126.9814], [37.5704, 126.9866]],
        "road_segment_ids": ["road-3"],
        "distance_km": 6.2,
        "road_congestion": 0.36,
        "vehicle_speed_kmh": 50,
        "obstacle_risk": 0.12,
        "base_station_distance_km": 1.6,
        "base_station_congestion": 0.58,
        "network_latency_ms": 31,
        "edge_node_distance_km": 2.2,
        "v2x_stability": 0.74,
    },
    {
        "id": "route-c",
        "name": "Edge-assisted route",
        "geometry": [[37.5591, 126.9737], [37.5631, 126.978], [37.5674, 126.9842], [37.5704, 126.991]],
        "road_segment_ids": ["road-2"],
        "distance_km": 5.4,
        "road_congestion": 0.41,
        "vehicle_speed_kmh": 47,
        "obstacle_risk": 0.16,
        "base_station_distance_km": 0.5,
        "base_station_congestion": 0.39,
        "network_latency_ms": 18,
        "edge_node_distance_km": 0.7,
        "v2x_stability": 0.93,
    },
]

ASSISTANT_MESSAGES: list[dict[str, str]] = [
    {
        "role": "assistant",
        "content": "AI optimization currently favors route-c because it improves V2X stability and edge proximity in mock data.",
    },
    {
        "role": "system",
        "content": "This is deterministic mock analysis. No LLM or production autonomous-driving control is connected.",
    },
]


def _copy(data: list[dict[str, object]]) -> list[dict[str, object]]:
    return deepcopy(data)


def get_vehicles() -> list[dict[str, object]]:
    return _copy(VEHICLES)


def get_roads() -> list[dict[str, object]]:
    return _copy(ROADS)


def get_base_stations() -> list[dict[str, object]]:
    return _copy(BASE_STATIONS)


def get_edge_nodes() -> list[dict[str, object]]:
    return _copy(EDGE_NODES)


def get_obstacles() -> list[dict[str, object]]:
    return _copy(OBSTACLES)


def get_routes() -> list[dict[str, object]]:
    return _copy(ROUTES)


def get_assistant_messages() -> list[dict[str, str]]:
    return deepcopy(ASSISTANT_MESSAGES)
