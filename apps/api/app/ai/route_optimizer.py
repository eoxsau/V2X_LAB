from __future__ import annotations

from typing import Any

from app.ai.blockage_predictor import predict_blockage_baseline
from app.ai.congestion_predictor import predict_congestion_baseline
from app.ai.feature_builder import build_vehicle_features
from app.ai.latency_predictor import predict_latency_baseline
from app.ai.model_registry import get_model_registry


def build_ai_ready_analysis(
    vehicles: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    edge_nodes: list[dict[str, Any]],
) -> dict[str, object]:
    features = build_vehicle_features(vehicles, roads, routes, edge_nodes)
    return {
        "status": "ai_ready_no_trained_models",
        "features": features,
        "latency_predictions": [predict_latency_baseline(feature) for feature in features],
        "congestion_predictions": [predict_congestion_baseline(feature) for feature in features],
        "blockage_predictions": [predict_blockage_baseline(feature) for feature in features],
        "model_registry": get_model_registry(),
        "note": "Structured AI interfaces only. No trained AI result is claimed.",
    }
