from __future__ import annotations

from typing import Any


def predict_congestion_baseline(feature: dict[str, Any]) -> dict[str, object]:
    value = (
        float(feature["road_congestion_score"]) * 0.5
        + float(feature["network_node_congestion"]) / 100 * 0.35
        + float(feature["nearby_vehicle_density"]) * 0.15
    )
    return {
        "vehicle_id": feature["vehicle_id"],
        "predicted_congestion_score": round(max(0, min(1, value)), 3),
        "model_status": "baseline_formula_not_trained_ai",
    }
