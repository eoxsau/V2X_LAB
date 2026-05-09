from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

DEFAULT_SEOUL_BOUNDS = {
    "minX": 126.90,
    "maxX": 127.10,
    "minY": 37.48,
    "maxY": 37.62,
}


class ITSClientError(RuntimeError):
    pass


async def fetch_realtime_traffic(
    bounds: dict[str, float] | None = None,
    road_type: str = "all",
) -> dict[str, Any]:
    if not settings.its_api_key:
        raise ITSClientError("ITS_API_KEY is not configured.")
    if not settings.its_api_base_url:
        raise ITSClientError("ITS_API_BASE_URL is not configured.")

    params: dict[str, Any] = {
        "apiKey": settings.its_api_key,
        "type": road_type,
        "getType": "json",
    }
    if road_type == "all":
        params.update({"drcType": "all"})
        params.update(bounds or DEFAULT_SEOUL_BOUNDS)

    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(settings.its_api_base_url, params=params)
        response.raise_for_status()
        payload = response.json()

    result_code = str(payload.get("resultCode") or payload.get("response", {}).get("header", {}).get("resultCode") or "0")
    if result_code not in {"0", "00", "000", "SUCCESS"}:
        raise ITSClientError("ITS API returned a non-success response.")

    return payload
