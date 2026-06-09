from __future__ import annotations


def geometry_match_confidence(distance_m: float, same_name: bool = False) -> float:
    base = max(0.0, 1.0 - min(distance_m, 120.0) / 120.0)
    if same_name:
        base = min(1.0, base + 0.15)
    return round(base, 4)

