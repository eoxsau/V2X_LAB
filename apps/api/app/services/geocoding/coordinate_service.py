from __future__ import annotations

from typing import Any


def normalize_coordinate(payload: dict[str, Any]) -> dict[str, object]:
    lat = float(payload.get("lat"))
    lng = float(payload.get("lng"))
    label = str(payload.get("label") or f"{lat:.6f}, {lng:.6f}")
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError("Coordinate is outside valid latitude/longitude bounds.")
    return {"lat": lat, "lng": lng, "label": label}
