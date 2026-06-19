from __future__ import annotations

from collections.abc import Sequence
from math import atan2, cos, exp, radians, sin, sqrt
from typing import Any

LatencyThresholdMs = 30.0
DefaultCoverageRadiusM = 2200.0
DefaultScoreMax = 120.0


def haversine_distance_m(
    start_lat: float,
    start_lng: float,
    end_lat: float,
    end_lng: float,
) -> float:
    """Return the approximate ground distance in meters between two WGS84 points."""
    earth_radius_m = 6_371_000
    lat_1 = radians(start_lat)
    lat_2 = radians(end_lat)
    delta_lat = radians(end_lat - start_lat)
    delta_lng = radians(end_lng - start_lng)
    a = sin(delta_lat / 2) ** 2 + cos(lat_1) * cos(lat_2) * sin(delta_lng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return earth_radius_m * c


def build_route_segments(path_coords: Sequence[Sequence[float]]) -> tuple[list[dict[str, float]], float]:
    """Precompute route segment lengths so future positions can be interpolated cheaply."""
    if len(path_coords) < 2:
        return [], 0.0

    total_length_m = 0.0
    segments: list[dict[str, float]] = []

    for index in range(len(path_coords) - 1):
        start_lat, start_lng = float(path_coords[index][0]), float(path_coords[index][1])
        end_lat, end_lng = float(path_coords[index + 1][0]), float(path_coords[index + 1][1])
        segment_length_m = haversine_distance_m(start_lat, start_lng, end_lat, end_lng)
        segments.append(
            {
                "start_lat": start_lat,
                "start_lng": start_lng,
                "end_lat": end_lat,
                "end_lng": end_lng,
                "start_distance_m": total_length_m,
                "length_m": segment_length_m,
            }
        )
        total_length_m += segment_length_m

    return segments, total_length_m


def interpolate_position_on_route(
    path_coords: Sequence[Sequence[float]],
    route_segments: Sequence[dict[str, float]],
    distance_along_route_m: float,
) -> dict[str, float]:
    """Project a scalar route progress onto the route geometry and return a lat/lng point."""
    if not path_coords:
        return {"lat": 0.0, "lng": 0.0, "route_progress_m": 0.0}

    if len(path_coords) == 1 or not route_segments:
        return {
            "lat": float(path_coords[0][0]),
            "lng": float(path_coords[0][1]),
            "route_progress_m": 0.0,
        }

    clamped_distance_m = max(0.0, distance_along_route_m)
    last_segment = route_segments[-1]
    max_distance_m = last_segment["start_distance_m"] + last_segment["length_m"]
    if clamped_distance_m >= max_distance_m:
        end_point = path_coords[-1]
        return {
            "lat": float(end_point[0]),
            "lng": float(end_point[1]),
            "route_progress_m": max_distance_m,
        }

    for segment in route_segments:
        segment_start_m = segment["start_distance_m"]
        segment_end_m = segment_start_m + segment["length_m"]
        if clamped_distance_m > segment_end_m:
            continue

        if segment["length_m"] <= 0:
            ratio = 0.0
        else:
            ratio = (clamped_distance_m - segment_start_m) / segment["length_m"]

        return {
            "lat": segment["start_lat"] + (segment["end_lat"] - segment["start_lat"]) * ratio,
            "lng": segment["start_lng"] + (segment["end_lng"] - segment["start_lng"]) * ratio,
            "route_progress_m": clamped_distance_m,
        }

    end_point = path_coords[-1]
    return {
        "lat": float(end_point[0]),
        "lng": float(end_point[1]),
        "route_progress_m": max_distance_m,
    }


def predict_future_route_positions(
    current_progress_m: float,
    current_speed_kmh: float,
    path_coords: Sequence[Sequence[float]],
    *,
    horizon_seconds: int = 15,
    sample_count: int = 6,
) -> list[dict[str, float]]:
    """Generate future route points from the current progress using the supplied path geometry."""
    route_segments, route_length_m = build_route_segments(path_coords)
    if route_length_m <= 0:
        return []

    speed_mps = max(1.0, current_speed_kmh * (1000 / 3600))
    step_seconds = horizon_seconds / max(sample_count, 1)
    samples: list[dict[str, float]] = []

    for sample_index in range(1, sample_count + 1):
        seconds_ahead = step_seconds * sample_index
        progress_m = min(route_length_m, current_progress_m + speed_mps * seconds_ahead)
        projected = interpolate_position_on_route(path_coords, route_segments, progress_m)
        samples.append(
            {
                **projected,
                "sample_index": float(sample_index),
                "seconds_ahead": seconds_ahead,
            }
        )

    return samples


def analyze_candidates_light(
    lat: float,
    lng: float,
    base_stations: Sequence[dict[str, Any]],
    *,
    current_node_id: str | None = None,
    disconnect_latency_threshold_ms: float = LatencyThresholdMs,
) -> list[dict[str, Any]]:
    """Score connectable stations using a light-weight distance and congestion model."""
    candidates: list[dict[str, Any]] = []

    for index, station in enumerate(base_stations):
        station_id = str(station.get("id") or f"station-{index + 1}")
        station_lat = float(station.get("latitude") or 0.0)
        station_lng = float(station.get("longitude") or 0.0)
        distance_m = haversine_distance_m(lat, lng, station_lat, station_lng)
        capacity = max(float(station.get("capacity") or 1200.0), 1.0)
        coverage_radius_m = float(station.get("coverage_radius_m") or DefaultCoverageRadiusM)
        base_load = min(0.95, max(0.2, 1 - min(capacity, 2000.0) / 2500.0))
        congestion_score = min(
            1.0,
            max(
                0.0,
                base_load + min(distance_m / max(coverage_radius_m, 1.0), 1.0) * 0.25,
            ),
        )
        distance_penalty = distance_m / 120.0
        congestion_penalty = congestion_score * 18.0
        handover_penalty = 0.0 if current_node_id in (None, station_id) else 2.5
        predicted_latency_ms = round(10.0 + distance_penalty + congestion_penalty + handover_penalty, 2)
        node_score = round(distance_penalty + congestion_penalty + handover_penalty, 2)
        in_coverage = distance_m <= coverage_radius_m

        candidates.append(
            {
                "id": station_id,
                "name": station.get("name", station_id),
                "distance_m": round(distance_m, 1),
                "coverage_radius_m": coverage_radius_m,
                "congestion_score": round(congestion_score, 4),
                "predicted_latency_ms": predicted_latency_ms,
                "node_score": node_score,
                "disconnect_risk": predicted_latency_ms > disconnect_latency_threshold_ms,
                "in_coverage": in_coverage,
                "latitude": station_lat,
                "longitude": station_lng,
            }
        )

    candidates.sort(
        key=lambda candidate: (
            not candidate["in_coverage"],
            candidate["node_score"],
            candidate["predicted_latency_ms"],
        )
    )
    return candidates


def compute_future_connectivity_score(
    sampled_candidates: Sequence[dict[str, Any]],
    *,
    score_max: float = DefaultScoreMax,
) -> float:
    """Collapse sampled future node scores into a normalized [0, 1] connectivity score."""
    if not sampled_candidates:
        return 0.0

    weighted_score_sum = 0.0
    total_weight = 0.0

    for sample_index, sample in enumerate(sampled_candidates):
        weight = exp(-(sample_index) / 2.5)
        total_weight += weight

        best_candidate = sample.get("best_candidate")
        if not best_candidate:
            continue

        if best_candidate.get("disconnect_risk"):
            continue

        normalized_score = max(0.0, 1.0 - float(best_candidate["node_score"]) / score_max)
        weighted_score_sum += normalized_score * weight

    if total_weight <= 0:
        return 0.0

    return round(weighted_score_sum / total_weight, 4)


def compute_future_risk_penalty(
    *,
    future_avg_latency_ms: float,
    future_max_latency_ms: float,
    predicted_handover_count: int,
    disconnect_risk: float,
) -> float:
    """Convert forward-looking latency and handover risk into a route-cost penalty."""
    avg_penalty = max(0.0, future_avg_latency_ms - 20.0) * 0.35
    max_penalty = max(0.0, future_max_latency_ms - 28.0) * 0.45
    handover_penalty = predicted_handover_count * 3.5
    disconnect_penalty = disconnect_risk * 25.0
    return round(avg_penalty + max_penalty + handover_penalty + disconnect_penalty, 2)


def compute_lookahead_network_scan(
    vehicle_pos: dict[str, Any],
    path_coords: Sequence[Sequence[float]],
    base_stations: Sequence[dict[str, Any]],
    *,
    horizon_seconds: int = 15,
    sample_count: int = 6,
) -> dict[str, Any]:
    """Scan future route positions and summarize likely BS handovers and latency risk."""
    current_progress_m = float(vehicle_pos.get("route_progress_m") or 0.0)
    current_speed_kmh = float(vehicle_pos.get("speed_kmh") or 24.0)
    current_node_id = vehicle_pos.get("connected_base_station_id")

    future_positions = predict_future_route_positions(
        current_progress_m,
        current_speed_kmh,
        path_coords,
        horizon_seconds=horizon_seconds,
        sample_count=sample_count,
    )

    sampled_candidates: list[dict[str, Any]] = []
    best_node_sequence: list[str] = []
    latency_samples: list[float] = []
    disconnect_events = 0
    previous_selected_node_id = current_node_id
    predicted_handover_count = 0

    for future_position in future_positions:
        candidates = analyze_candidates_light(
            float(future_position["lat"]),
            float(future_position["lng"]),
            base_stations,
            current_node_id=previous_selected_node_id,
        )
        best_candidate = candidates[0] if candidates else None
        if best_candidate is None:
            disconnect_events += 1
        else:
            latency_samples.append(float(best_candidate["predicted_latency_ms"]))
            best_node_sequence.append(str(best_candidate["id"]))
            if best_candidate["disconnect_risk"]:
                disconnect_events += 1
            if (
                previous_selected_node_id
                and best_candidate["id"] != previous_selected_node_id
                and abs(float(best_candidate["node_score"]) - float(candidates[0]["node_score"])) >= 0.5
            ):
                predicted_handover_count += 1
            previous_selected_node_id = str(best_candidate["id"])

        sampled_candidates.append(
            {
                "seconds_ahead": future_position["seconds_ahead"],
                "lat": future_position["lat"],
                "lng": future_position["lng"],
                "best_candidate": best_candidate,
                "candidate_ids": [candidate["id"] for candidate in candidates[:3]],
            }
        )

    future_avg_latency_ms = round(sum(latency_samples) / len(latency_samples), 2) if latency_samples else 0.0
    future_max_latency_ms = round(max(latency_samples), 2) if latency_samples else 0.0
    disconnect_risk = round(disconnect_events / max(len(future_positions), 1), 4)
    future_connectivity_score = compute_future_connectivity_score(sampled_candidates)
    future_risk_penalty = compute_future_risk_penalty(
        future_avg_latency_ms=future_avg_latency_ms,
        future_max_latency_ms=future_max_latency_ms,
        predicted_handover_count=predicted_handover_count,
        disconnect_risk=disconnect_risk,
    )

    return {
        "horizon_seconds": horizon_seconds,
        "sample_count": sample_count,
        "future_candidate_sequence": best_node_sequence,
        "future_positions": future_positions,
        "future_avg_latency_ms": future_avg_latency_ms,
        "future_max_latency_ms": future_max_latency_ms,
        "predicted_handover_count": predicted_handover_count,
        "disconnect_risk": disconnect_risk,
        "future_connectivity_score": future_connectivity_score,
        "future_risk_penalty": future_risk_penalty,
        "assumptions": {
            "base_station_load_model": "current_state_frozen",
            "disconnect_latency_threshold_ms": LatencyThresholdMs,
        },
    }
