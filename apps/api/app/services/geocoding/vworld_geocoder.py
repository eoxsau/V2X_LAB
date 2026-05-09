from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

VWORLD_GEOCODER_URL = "https://api.vworld.kr/req/address"


async def geocode_vworld_address(query: str) -> dict[str, object]:
    if not settings.vworld_api_key:
        raise RuntimeError("VWORLD_API_KEY is not configured.")

    params: dict[str, Any] = {
        "service": "address",
        "request": "getcoord",
        "format": "json",
        "crs": "epsg:4326",
        "type": "ROAD",
        "address": query,
        "key": settings.vworld_api_key,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(VWORLD_GEOCODER_URL, params=params)
        response.raise_for_status()
        data = response.json()

    point = data.get("response", {}).get("result", {}).get("point")
    if not point:
        raise RuntimeError("VWorld geocoder returned no coordinate.")
    return {
        "lat": float(point["y"]),
        "lng": float(point["x"]),
        "label": query,
        "source": "vworld_geocoder",
    }
