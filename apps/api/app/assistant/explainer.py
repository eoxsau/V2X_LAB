from typing import Any

from app.ml.baseline_model import analyze_routes
from app.services.mock_repository import (
    get_base_stations,
    get_edge_nodes,
    get_obstacles,
    get_roads,
    get_routes,
    get_vehicles,
)


def explain_current_recommendation(vehicle_id: str | None = None) -> dict[str, Any]:
    route_analysis = analyze_routes(
        get_routes(),
        vehicles=get_vehicles(),
        roads=get_roads(),
        base_stations=get_base_stations(),
        edge_nodes=get_edge_nodes(),
        obstacles=get_obstacles(),
        vehicle_id=vehicle_id,
    )
    vehicle = _selected_vehicle(get_vehicles(), str(route_analysis.get("selected_vehicle_id") or vehicle_id or ""))
    recommended = route_analysis["recommended_route"]
    shortest = route_analysis["shortest_route_baseline"]
    traffic = route_analysis["traffic_aware_route"]
    overloaded_station = _most_loaded_station(shortest)
    main_cause = _main_cause(shortest, recommended)
    expected_improvement = _expected_improvement(shortest, recommended)
    confidence = _confidence(shortest, recommended)

    return {
        "mode": "deterministic_structured_assistant",
        "assistant_recommendation": (
            f"Vehicle {vehicle.get('id', 'selected vehicle')} should switch to "
            f"{recommended['name']}. The shortest route is shorter, but the "
            f"network-aware route has a lower combined V2X routing score."
        ),
        "main_cause": main_cause,
        "expected_improvement": expected_improvement,
        "recommended_action": (
            f"Apply {recommended['id']} for this simulation tick, keep monitoring "
            f"{overloaded_station['base_station_id']} load, and re-evaluate if obstacle risk increases."
        ),
        "confidence": confidence,
        "answers": {
            "why_this_route_is_recommended": _why_recommended(shortest, recommended),
            "which_factor_caused_rerouting": main_cause,
            "which_base_station_is_overloaded": overloaded_station,
            "whether_obstacle_risk_is_high": _obstacle_risk_answer(vehicle, shortest, recommended),
            "whether_network_latency_or_road_congestion_is_more_important": _dominant_network_or_road_factor(
                shortest,
                recommended,
            ),
            "expected_improvement": expected_improvement,
        },
        "comparison": {
            "shortest_route": _route_summary(shortest),
            "traffic_aware_route": _route_summary(traffic),
            "network_aware_ai_route": _route_summary(recommended),
        },
        "note": "No external LLM is connected. This explanation is generated from structured route optimization outputs.",
    }


def _selected_vehicle(vehicles: list[dict[str, object]], vehicle_id: str) -> dict[str, object]:
    return next((vehicle for vehicle in vehicles if vehicle.get("id") == vehicle_id), vehicles[0] if vehicles else {})


def _route_summary(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": route["id"],
        "name": route["name"],
        "total_score": route["total_score"],
        "quality_score": route["quality_score"],
        "expected_latency_ms": route["expected_latency_ms"],
        "expected_obstacle_risk": route["expected_obstacle_risk"],
        "expected_base_station_congestion": route["expected_base_station_congestion"],
        "primary_edge_node_id": route["network_evaluation"]["primary_edge_node_id"],
    }


def _why_recommended(shortest: dict[str, Any], recommended: dict[str, Any]) -> str:
    latency_delta = float(shortest["expected_latency_ms"]) - float(recommended["expected_latency_ms"])
    obstacle_delta = float(shortest["expected_obstacle_risk"]) - float(recommended["expected_obstacle_risk"])
    score_delta = float(shortest["total_score"]) - float(recommended["total_score"])
    return (
        f"{recommended['name']} is recommended because it lowers the total route penalty by "
        f"{score_delta:.1f} points versus {shortest['name']}, with {latency_delta:.1f} ms lower "
        f"expected latency and {obstacle_delta:.2f} lower obstacle exposure in this mock scenario."
    )


def _main_cause(shortest: dict[str, Any], recommended: dict[str, Any]) -> str:
    labels = {
        "travel_time_score": "travel time",
        "road_congestion_score": "road congestion",
        "predicted_network_latency_score": "network latency",
        "base_station_congestion_score": "base-station congestion",
        "obstacle_risk_score": "obstacle risk",
        "edge_latency_score": "edge latency",
    }
    improvements = {
        key: float(shortest["score_breakdown"][key]) - float(recommended["score_breakdown"][key])
        for key in labels
    }
    cause_key = max(improvements, key=improvements.get)
    return f"{labels[cause_key]} caused rerouting, improving that component by {improvements[cause_key]:.1f} points."


def _expected_improvement(shortest: dict[str, Any], recommended: dict[str, Any]) -> str:
    score_delta = float(shortest["total_score"]) - float(recommended["total_score"])
    quality_delta = float(recommended["quality_score"]) - float(shortest["quality_score"])
    latency_delta = float(shortest["expected_latency_ms"]) - float(recommended["expected_latency_ms"])
    return (
        f"Expected improvement: {score_delta:.1f} lower total penalty, "
        f"{quality_delta:.1f} higher display quality score, and {latency_delta:.1f} ms lower expected V2X latency."
    )


def _most_loaded_station(route: dict[str, Any]) -> dict[str, Any]:
    samples = route["network_evaluation"].get("connectivity_samples", [])
    if not samples:
        return {"base_station_id": None, "congestion": None, "status": "unknown"}
    station = max(samples, key=lambda sample: float(sample["base_station_congestion"]))
    congestion = float(station["base_station_congestion"])
    return {
        "base_station_id": station["base_station_id"],
        "congestion": round(congestion, 1),
        "status": "overloaded" if congestion >= 60 else "highest observed load in candidate route",
    }


def _obstacle_risk_answer(
    vehicle: dict[str, object],
    shortest: dict[str, Any],
    recommended: dict[str, Any],
) -> str:
    current_risk = float(vehicle.get("obstacle_risk", 0))
    shortest_risk = float(shortest["expected_obstacle_risk"])
    recommended_risk = float(recommended["expected_obstacle_risk"])
    if current_risk >= 0.6 or shortest_risk >= 0.55:
        return (
            f"Yes. Current/shortest-route obstacle risk is high "
            f"({current_risk:.2f}/{shortest_risk:.2f}), while the recommended route lowers expected risk to "
            f"{recommended_risk:.2f}."
        )
    return f"No. Obstacle risk is moderate, and the recommended route keeps it near {recommended_risk:.2f}."


def _dominant_network_or_road_factor(shortest: dict[str, Any], recommended: dict[str, Any]) -> str:
    network_gain = float(shortest["score_breakdown"]["predicted_network_latency_score"]) - float(
        recommended["score_breakdown"]["predicted_network_latency_score"]
    )
    road_gain = float(shortest["score_breakdown"]["road_congestion_score"]) - float(
        recommended["score_breakdown"]["road_congestion_score"]
    )
    if network_gain >= road_gain:
        return f"Network latency is more important in this reroute ({network_gain:.1f} vs {road_gain:.1f} score gain)."
    return f"Road congestion is more important in this reroute ({road_gain:.1f} vs {network_gain:.1f} score gain)."


def _confidence(shortest: dict[str, Any], recommended: dict[str, Any]) -> dict[str, Any]:
    score_delta = float(shortest["total_score"]) - float(recommended["total_score"])
    confidence = min(0.94, max(0.55, 0.62 + score_delta / 120))
    return {
        "label": "medium-high" if confidence >= 0.72 else "medium",
        "value": round(confidence, 2),
        "reason": "Confidence is based on deterministic score separation between the shortest and recommended routes.",
    }
