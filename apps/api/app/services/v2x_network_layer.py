import math
from copy import deepcopy

from app.services.obstacle_blockage_layer import evaluate_obstacle_exposure

LatLng = list[float]

BASE_LATENCY_MS = 8.0


def build_network_nodes(
    base_stations: list[dict[str, object]],
    edge_nodes: list[dict[str, object]],
) -> list[dict[str, object]]:
    nodes = []
    for station in base_stations:
        is_rsu = "RSU" in str(station["name"]).upper()
        nodes.append(
            {
                "id": station["id"],
                "name": station["name"],
                "node_type": "roadside_unit" if is_rsu else "base_station",
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "capacity": station["capacity"],
                "current_connected_vehicles": station["current_connected_vehicles"],
                "congestion_score": round(calculate_base_station_congestion(station), 2),
                "edge_latency_ms": 9.5 if is_rsu else 14.0,
                "coverage_radius_m": 650 if is_rsu else 950,
                "source": station["source"],
            }
        )

    for edge in edge_nodes:
        compute_load = float(edge["compute_load"])
        nodes.append(
            {
                "id": edge["id"],
                "name": edge["name"],
                "node_type": "edge_node",
                "latitude": edge["latitude"],
                "longitude": edge["longitude"],
                "capacity": edge["capacity"],
                "current_connected_vehicles": round(compute_load * float(edge["capacity"]) / 20),
                "congestion_score": round(compute_load * 100, 2),
                "edge_latency_ms": edge["edge_latency_ms"],
                "coverage_radius_m": 480,
                "source": edge["source"],
            }
        )
    return nodes


def generate_synthetic_base_stations() -> list[dict[str, object]]:
    """Synthetic fallback stations until public base-station data is connected."""
    stations = [
        ("BS-01", "City Hall RSU", 37.5669, 126.9782, 3500, 43, 1200, 18, 44),
        ("BS-02", "Euljiro Edge Cell", 37.5661, 126.9912, 3500, 41, 980, 34, 68),
        ("BS-03", "Namdaemun Relay", 37.5598, 126.9771, 2800, 39, 850, 41, 82),
        ("BS-04", "Gwanghwamun Node", 37.5759, 126.9768, None, None, 1350, 14, 37),
        ("BS-05", "Jongno RSU", 37.5702, 126.9831, 3500, 40, 1040, 29, 55),
        ("BS-06", "Seoul Station V2X Cell", 37.5547, 126.9706, 2800, 42, 1180, 22, 48),
        ("BS-07", "Myeongdong RSU", 37.5636, 126.9858, 3500, 39, 920, 38, 73),
        ("BS-08", "Chungmuro Relay", 37.5611, 126.9943, 2800, 38, 760, 31, 64),
        ("BS-09", "Anguk Research Cell", 37.5768, 126.9872, None, 37, 700, 12, 34),
        ("BS-10", "Sogong Edge RSU", 37.5639, 126.9738, 3500, None, 880, 26, 51),
        ("BS-11", "Dongdaemun Corridor Cell", 37.5708, 127.0095, 3500, 44, 1240, 45, 79),
        ("BS-12", "Mapo Gateway RSU", 37.5569, 126.9455, 2800, 41, 970, 17, 43),
    ]

    return [
        {
            "id": item[0],
            "name": item[1],
            "latitude": item[2],
            "longitude": item[3],
            "frequency": item[4],
            "tx_power": item[5],
            "capacity": item[6],
            "current_connected_vehicles": item[7],
            "congestion_score": item[8],
            "source": "synthetic",
        }
        for item in stations
    ]


def calculate_distance_km(a: LatLng, b: LatLng) -> float:
    lat_km = (a[0] - b[0]) * 111.0
    lng_km = (a[1] - b[1]) * 88.0
    return math.hypot(lat_km, lng_km)


def nearest_edge_node(position: LatLng, edge_nodes: list[dict[str, object]]) -> dict[str, object]:
    return min(
        edge_nodes,
        key=lambda node: calculate_distance_km(position, [float(node["latitude"]), float(node["longitude"])]),
    )


def rank_network_nodes(
    position: LatLng,
    network_nodes: list[dict[str, object]],
    limit: int = 5,
) -> list[dict[str, object]]:
    ranked = []
    for node in network_nodes:
        distance_km = calculate_distance_km(position, [float(node["latitude"]), float(node["longitude"])])
        latency = _node_latency_estimate(distance_km, node, 0)
        score = 100 - distance_km * 18 - float(node["congestion_score"]) * 0.42 - float(node["edge_latency_ms"]) * 0.6
        ranked.append(
            {
                "id": node["id"],
                "name": node["name"],
                "node_type": node["node_type"],
                "distance_km": round(distance_km, 3),
                "load": round(float(node["congestion_score"]), 2),
                "estimated_latency_ms": round(latency, 2),
                "baseline_score": round(score, 2),
                "recommendation_status": "current_candidate" if score >= 55 else "fallback_candidate",
            }
        )
    return sorted(ranked, key=lambda item: item["baseline_score"], reverse=True)[:limit]


def rank_base_stations(
    position: LatLng,
    base_stations: list[dict[str, object]],
    limit: int = 4,
) -> list[dict[str, object]]:
    ranked = []
    for station in base_stations:
        distance_km = calculate_distance_km(position, [float(station["latitude"]), float(station["longitude"])])
        congestion_score = calculate_base_station_congestion(station)
        score = baseline_connection_score(distance_km, congestion_score, station)
        ranked.append(
            {
                "id": station["id"],
                "name": station["name"],
                "distance_km": round(distance_km, 3),
                "congestion_score": round(congestion_score, 2),
                "baseline_score": round(score, 2),
            }
        )

    return sorted(ranked, key=lambda item: item["baseline_score"], reverse=True)[:limit]


def calculate_base_station_congestion(station: dict[str, object]) -> float:
    static_score = float(station["congestion_score"])
    capacity = max(float(station["capacity"]), 1)
    connected_ratio = float(station["current_connected_vehicles"]) / capacity * 100
    return min(100.0, static_score * 0.75 + connected_ratio * 0.25)


def baseline_connection_score(
    distance_km: float,
    congestion_score: float,
    station: dict[str, object],
) -> float:
    tx_power_bonus = 0 if station["tx_power"] is None else (float(station["tx_power"]) - 38) * 0.8
    return 100 - distance_km * 22 - congestion_score * 0.58 + tx_power_bonus


def calculate_network_latency_ms(
    distance_km: float,
    station_congestion: float,
    edge_node: dict[str, object],
    obstacle_blockage_penalty_ms: float,
) -> dict[str, float]:
    distance_penalty = distance_km * 15.5
    base_station_congestion_penalty = station_congestion * 0.19
    edge_latency_penalty = float(edge_node["edge_latency_ms"])
    obstacle_blockage_penalty = obstacle_blockage_penalty_ms
    total = (
        BASE_LATENCY_MS
        + distance_penalty
        + base_station_congestion_penalty
        + edge_latency_penalty
        + obstacle_blockage_penalty
    )

    return {
        "base_latency": round(BASE_LATENCY_MS, 2),
        "distance_penalty": round(distance_penalty, 2),
        "base_station_congestion_penalty": round(base_station_congestion_penalty, 2),
        "edge_latency_penalty": round(edge_latency_penalty, 2),
        "obstacle_blockage_penalty": round(obstacle_blockage_penalty, 2),
        "total_latency_ms": round(total, 2),
    }


def _node_latency_estimate(
    distance_km: float,
    node: dict[str, object],
    obstacle_blockage_penalty_ms: float,
) -> float:
    type_penalty = {"roadside_unit": 3.0, "edge_node": 1.5, "base_station": 5.0}.get(str(node["node_type"]), 4.0)
    return (
        BASE_LATENCY_MS
        + distance_km * 14.0
        + float(node["congestion_score"]) * 0.16
        + float(node["edge_latency_ms"])
        + type_penalty
        + obstacle_blockage_penalty_ms
    )


def attach_v2x_network_state(
    vehicle: dict[str, object],
    base_stations: list[dict[str, object]],
    edge_nodes: list[dict[str, object]],
    obstacles: list[dict[str, object]],
    road_obstacle_risk: float,
) -> dict[str, object]:
    updated = deepcopy(vehicle)
    position = [float(updated["latitude"]), float(updated["longitude"])]
    nearby = rank_base_stations(position, base_stations)
    selected = next(station for station in base_stations if station["id"] == nearby[0]["id"])
    edge_node = nearest_edge_node(position, edge_nodes)
    network_nodes = build_network_nodes(base_stations, edge_nodes)
    nearby_nodes = rank_network_nodes(position, network_nodes)
    connected_node = next(node for node in network_nodes if node["id"] == nearby_nodes[0]["id"])
    distance_km = float(nearby[0]["distance_km"])
    node_distance_km = float(nearby_nodes[0]["distance_km"])
    station_congestion = float(nearby[0]["congestion_score"])
    obstacle_exposure = evaluate_obstacle_exposure(
        position,
        [float(connected_node["latitude"]), float(connected_node["longitude"])],
        obstacles,
        road_obstacle_risk,
    )
    latency = calculate_network_latency_ms(
        distance_km,
        station_congestion,
        edge_node,
        float(obstacle_exposure["network_blockage_penalty_ms"]),
    )

    updated.update(
        {
            "obstacle_risk": obstacle_exposure["obstacle_risk"],
            "network_blockage_risk": obstacle_exposure["network_blockage_risk"],
            "nearby_base_stations": nearby,
            "nearby_obstacles": obstacle_exposure["nearby_obstacles"],
            "blocking_obstacles": obstacle_exposure["blocking_obstacles"],
            "connected_base_station_id": selected["id"],
            "connected_base_station_name": selected["name"],
            "connected_edge_node_id": edge_node["id"],
            "connected_edge_node_name": edge_node["name"],
            "distance_to_connected_base_station_km": round(distance_km, 3),
            "base_station_congestion_score": round(station_congestion, 2),
            "connected_network_node_id": connected_node["id"],
            "connected_network_node_name": connected_node["name"],
            "connected_network_node_type": connected_node["node_type"],
            "distance_to_connected_node": round(node_distance_km, 3),
            "distance_to_nearest_node": round(float(nearby_nodes[0]["distance_km"]), 3),
            "network_node_congestion_score": round(float(connected_node["congestion_score"]), 2),
            "nearest_network_nodes": nearby_nodes,
            "alternative_node_latency_estimates": nearby_nodes[1:],
            "network_node_recommendation_status": nearby_nodes[0]["recommendation_status"],
            "current_latency_ms": latency["total_latency_ms"],
            "latency_breakdown": latency,
        }
    )
    return updated
