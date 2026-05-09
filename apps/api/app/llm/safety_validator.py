from __future__ import annotations

from typing import Any

ALLOWED_MODES = {"urban_mobility", "six_g_like_v2x"}
ALLOWED_OBSTACLES = {
    "accident_zone",
    "building_zone",
    "construction_zone",
    "low_visibility_zone",
    "network_blockage_zone",
}


def validate_scenario(raw: dict[str, Any]) -> dict[str, object]:
    corrections = []
    rejected_fields = []

    area = str(raw.get("area") or "Seoul downtown").strip()[:80]
    ego_count = _clamp_int(raw.get("ego_vehicle_count", 1), 1, 5)
    surrounding_count = _clamp_int(raw.get("surrounding_vehicle_count", 20), 0, 300)
    mode = str(raw.get("mode") or "urban_mobility")
    if mode not in ALLOWED_MODES:
        corrections.append({"field": "mode", "from": mode, "to": "urban_mobility"})
        mode = "urban_mobility"

    obstacles = []
    for item in raw.get("obstacles", []):
        obstacle_type = str(item.get("type", "accident_zone")) if isinstance(item, dict) else str(item)
        if obstacle_type not in ALLOWED_OBSTACLES:
            rejected_fields.append({"field": "obstacles.type", "value": obstacle_type})
            continue
        obstacles.append({"type": obstacle_type})

    return {
        "valid": True,
        "scenario": {
            "area": area,
            "ego_vehicle_count": ego_count,
            "surrounding_vehicle_count": surrounding_count,
            "obstacles": obstacles[:10],
            "mode": mode,
        },
        "corrections": corrections,
        "rejected_fields": rejected_fields,
        "control_policy": "validated_setup_only_no_direct_simulation_control",
    }


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except Exception:
        return minimum
