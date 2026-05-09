from __future__ import annotations

from typing import Any


def predict_blockage_baseline(feature: dict[str, Any]) -> dict[str, object]:
    value = float(feature["obstacle_risk"]) * 0.72 + float(feature["network_node_distance"]) / 2.5 * 0.28
    return {
        "vehicle_id": feature["vehicle_id"],
        "predicted_blockage_risk": round(max(0, min(1, value)), 3),
        "model_status": "baseline_formula_not_trained_ai",
    }
