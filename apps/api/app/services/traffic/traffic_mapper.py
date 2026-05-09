from __future__ import annotations

import hashlib
from typing import Any


def map_its_response_to_road_segments(
    payload: dict[str, Any],
    fallback_roads: list[dict[str, object]],
) -> list[dict[str, object]]:
    items = _extract_items(payload)
    if not items:
        return []

    mapped = []
    fallback_by_index = fallback_roads or []
    for index, item in enumerate(items):
        fallback = fallback_by_index[index % len(fallback_by_index)] if fallback_by_index else {}
        speed = _float(item.get("speed") or item.get("spd") or item.get("trafficSpeed"), fallback.get("average_speed", 0))
        travel_time = _float(item.get("travelTime") or item.get("trvlTime"), fallback.get("travel_time_estimate", 0))
        road_name = str(item.get("roadName") or item.get("routeName") or fallback.get("name") or "ITS road segment")
        link_id = str(item.get("linkId") or item.get("linkNo") or _stable_id(road_name, index))
        geometry = _geometry_from_item(item) or fallback.get("geometry") or []
        mapped.append(
            {
                "id": f"its-{link_id}",
                "name": road_name,
                "geometry": geometry,
                "average_speed": round(speed, 1),
                "congestion_score": _speed_to_congestion(speed),
                "travel_time_estimate": round(travel_time / 60, 2) if travel_time > 60 else round(travel_time, 2),
                "source": "its_api",
                "raw_payload": item,
            }
        )

    return mapped


def mark_fallback_roads(roads: list[dict[str, object]], source: str) -> list[dict[str, object]]:
    return [{**road, "source": road.get("source", source)} for road in roads]


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [
        payload.get("body", {}).get("items"),
        payload.get("body", {}).get("item"),
        payload.get("items"),
        payload.get("data"),
        payload.get("result"),
        payload.get("response", {}).get("body", {}).get("items"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            item = candidate.get("item") or candidate.get("items")
            if isinstance(item, list):
                return [entry for entry in item if isinstance(entry, dict)]
            if isinstance(item, dict):
                return [item]
            return [candidate]
        if isinstance(candidate, list):
            return [entry for entry in candidate if isinstance(entry, dict)]
    return []


def _geometry_from_item(item: dict[str, Any]) -> list[list[float]]:
    start_x = _optional_float(item.get("startX") or item.get("stX") or item.get("coordX"))
    start_y = _optional_float(item.get("startY") or item.get("stY") or item.get("coordY"))
    end_x = _optional_float(item.get("endX") or item.get("edX"))
    end_y = _optional_float(item.get("endY") or item.get("edY"))
    if start_x is None or start_y is None:
        return []
    if end_x is None or end_y is None:
        return [[start_y, start_x]]
    return [[start_y, start_x], [end_y, end_x]]


def _speed_to_congestion(speed: float) -> float:
    if speed <= 0:
        return 0.8
    return round(max(0.05, min(0.95, 1 - speed / 90)), 3)


def _float(value: Any, fallback: object) -> float:
    parsed = _optional_float(value)
    if parsed is not None:
        return parsed
    parsed_fallback = _optional_float(fallback)
    return parsed_fallback if parsed_fallback is not None else 0.0


def _optional_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _stable_id(value: str, index: int) -> str:
    digest = hashlib.sha1(f"{value}-{index}".encode("utf-8")).hexdigest()[:10]
    return digest
