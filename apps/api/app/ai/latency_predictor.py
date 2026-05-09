from __future__ import annotations

from typing import Any


def predict_latency_baseline(feature: dict[str, Any]) -> dict[str, object]:
    value = (
        8
        + float(feature["network_node_distance"]) * 14
        + float(feature["network_node_congestion"]) * 0.16
        + float(feature["edge_latency_ms"])
        + float(feature["obstacle_risk"]) * 18
    )
    return {
        "vehicle_id": feature["vehicle_id"],
        "predicted_latency_ms": round(value, 2),
        "model_status": "baseline_formula_not_trained_ai",
    }
