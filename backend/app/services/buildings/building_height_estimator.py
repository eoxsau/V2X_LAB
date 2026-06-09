from __future__ import annotations

import pandas as pd


def estimate_building_height(row: pd.Series) -> tuple[float, str, str]:
    raw_height = row.get("HEIGHT")
    if pd.notna(raw_height) and float(raw_height) > 0:
        return float(raw_height), "height_field", "high"

    ground_floor = row.get("GRND_FLR")
    if pd.notna(ground_floor) and float(ground_floor) > 0:
        return float(ground_floor) * 3.0, "floor_estimated", "medium"

    return 10.0, "default_estimated", "low"
