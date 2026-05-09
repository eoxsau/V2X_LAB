from __future__ import annotations


def get_model_registry() -> dict[str, object]:
    return {
        "latency_predictor": {
            "status": "interface_ready",
            "trained_model_loaded": False,
            "implementation": "baseline_formula",
        },
        "congestion_predictor": {
            "status": "interface_ready",
            "trained_model_loaded": False,
            "implementation": "baseline_formula",
        },
        "blockage_predictor": {
            "status": "interface_ready",
            "trained_model_loaded": False,
            "implementation": "baseline_formula",
        },
        "route_optimizer": {
            "status": "ai_ready_scaffold",
            "trained_model_loaded": False,
            "implementation": "deterministic_score_interface",
        },
    }
