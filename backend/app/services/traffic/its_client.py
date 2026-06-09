from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
DEFAULT_ITS_BASE_URL = "https://openapi.its.go.kr:9443"


def load_its_env() -> tuple[str | None, str | None]:
    load_dotenv(ENV_PATH)
    return os.getenv("ITS_API_KEY"), os.getenv("ITS_API_BASE_URL") or DEFAULT_ITS_BASE_URL


def fetch_its_traffic_info(*, min_x: float, max_x: float, min_y: float, max_y: float, traffic_type: str = "all") -> str:
    api_key, base_url = load_its_env()
    if not api_key:
        raise RuntimeError("ITS_API_KEY is not configured in backend/.env")
    if not base_url:
        raise RuntimeError("ITS_API_BASE_URL is not configured in backend/.env")

    url = base_url.rstrip("/") + "/trafficInfo"
    resp = requests.get(
        url,
        params={
            "apiKey": api_key,
            "type": traffic_type,
            "minX": min_x,
            "maxX": max_x,
            "minY": min_y,
            "maxY": max_y,
            "getType": "xml",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text

