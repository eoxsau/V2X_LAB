import math

LatLng = list[float]

BLOCKAGE_TYPE_WEIGHT = {
    "building_zone": 1.0,
    "accident_zone": 0.42,
    "construction_zone": 0.62,
    "low_visibility_zone": 0.78,
    "network_blockage_zone": 1.15,
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def distance_km(a: LatLng, b: LatLng) -> float:
    lat_km = (a[0] - b[0]) * 111.0
    lng_km = (a[1] - b[1]) * 88.0
    return math.hypot(lat_km, lng_km)


def _point_to_segment_distance_km(point: LatLng, start: LatLng, end: LatLng) -> float:
    px = point[1] * 88.0
    py = point[0] * 111.0
    sx = start[1] * 88.0
    sy = start[0] * 111.0
    ex = end[1] * 88.0
    ey = end[0] * 111.0
    dx = ex - sx
    dy = ey - sy

    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)

    t = _clamp(((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy), 0, 1)
    nearest_x = sx + t * dx
    nearest_y = sy + t * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def evaluate_obstacle_exposure(
    vehicle_position: LatLng,
    base_station_position: LatLng,
    obstacles: list[dict[str, object]],
    road_obstacle_risk: float,
) -> dict[str, object]:
    nearby_obstacles = []
    blocking_obstacles = []

    for obstacle in obstacles:
        obstacle_position = [float(obstacle["latitude"]), float(obstacle["longitude"])]
        severity = float(obstacle["severity"])
        radius_km = float(obstacle["radius_m"]) / 1000
        proximity_distance = distance_km(vehicle_position, obstacle_position)
        path_distance = _point_to_segment_distance_km(
            obstacle_position,
            vehicle_position,
            base_station_position,
        )
        proximity_score = _clamp(1 - proximity_distance / max(radius_km * 2.5, 0.05), 0, 1)
        path_score = _clamp(1 - path_distance / max(radius_km * 2.0, 0.05), 0, 1)

        if proximity_score > 0:
            nearby_obstacles.append(
                {
                    "id": obstacle["id"],
                    "type": obstacle["type"],
                    "distance_km": round(proximity_distance, 3),
                    "severity": round(severity, 3),
                    "proximity_score": round(proximity_score, 3),
                }
            )

        blockage_weight = BLOCKAGE_TYPE_WEIGHT[str(obstacle["type"])]
        if path_score > 0:
            blocking_obstacles.append(
                {
                    "id": obstacle["id"],
                    "type": obstacle["type"],
                    "path_distance_km": round(path_distance, 3),
                    "severity": round(severity, 3),
                    "blockage_score": round(_clamp(path_score * severity * blockage_weight, 0, 1), 3),
                }
            )

    proximity_risk = max(
        [item["proximity_score"] * item["severity"] for item in nearby_obstacles],
        default=0,
    )
    blockage_risk = max(
        [item["blockage_score"] for item in blocking_obstacles],
        default=0,
    )
    obstacle_risk = _clamp(road_obstacle_risk * 0.35 + proximity_risk * 0.4 + blockage_risk * 0.25, 0, 1)

    return {
        "obstacle_risk": round(obstacle_risk, 3),
        "network_blockage_risk": round(blockage_risk, 3),
        "network_blockage_penalty_ms": round(blockage_risk * 18 + proximity_risk * 6, 2),
        "nearby_obstacles": nearby_obstacles[:4],
        "blocking_obstacles": blocking_obstacles[:4],
    }
