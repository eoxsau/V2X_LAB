from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.mock_repository import get_roads
from app.core.config import settings
from app.services.traffic.its_client import fetch_realtime_traffic
from app.services.traffic.traffic_mapper import map_its_response_to_road_segments, mark_fallback_roads

_CACHE_TTL = timedelta(minutes=5)
_cached_roads: list[dict[str, object]] = []
_last_sync_at: datetime | None = None
_last_error: str | None = None
_last_source = "synthetic"


async def sync_its_traffic() -> dict[str, object]:
    global _cached_roads, _last_error, _last_source, _last_sync_at
    fallback_roads = get_roads()
    try:
        payload = await fetch_realtime_traffic()
        mapped = map_its_response_to_road_segments(payload, fallback_roads)
        if not mapped:
            raise RuntimeError("ITS API returned no road segment items for the configured request.")
        _cached_roads = mapped
        _last_sync_at = datetime.now(UTC)
        _last_error = None
        _last_source = "its_api"
    except Exception as exc:
        _last_error = _safe_error(exc)
        if not _cached_roads:
            _cached_roads = mark_fallback_roads(fallback_roads, "synthetic")
            _last_source = "synthetic"
    return get_traffic_state(mark_fallback_roads(fallback_roads, "synthetic"))


def get_traffic_state(fallback_roads: list[dict[str, object]] | None = None, fallback_source: str = "synthetic") -> dict[str, object]:
    fallback = mark_fallback_roads(fallback_roads or get_roads(), fallback_source)
    cache_fresh = _last_sync_at is not None and datetime.now(UTC) - _last_sync_at <= _CACHE_TTL
    roads = deepcopy(_cached_roads if _cached_roads and (_last_source == "its_api" or cache_fresh) else fallback)
    source = _last_source if _cached_roads and (_last_source == "its_api" or cache_fresh) else fallback_source
    return {
        "source": source,
        "status": "connected" if source == "its_api" and _last_error is None else "fallback",
        "last_sync_at": _last_sync_at.isoformat() if _last_sync_at else None,
        "last_error": _last_error,
        "road_segments": roads,
    }


def apply_traffic_priority(
    traffic_state: dict[str, object],
    adapter_source: str,
) -> dict[str, object]:
    state = get_traffic_state(
        fallback_roads=list(traffic_state.get("roads", [])),
        fallback_source=adapter_source,
    )
    traffic_state["roads"] = state["road_segments"]
    traffic_state["traffic_source"] = state["source"]
    traffic_state["traffic_status"] = state["status"]
    traffic_state["traffic_last_sync_at"] = state["last_sync_at"]
    traffic_state["traffic_last_error"] = state["last_error"]
    return traffic_state


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    if settings.its_api_key:
        message = message.replace(settings.its_api_key, "[redacted]")
    return message
