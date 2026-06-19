import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from app.core.config import settings
from app.services.base_station_mapper import (
    get_mock_base_stations,
    map_public_base_stations,
)

_cached_base_stations: list[dict[str, object]] | None = None


def sync_base_stations_from_public_api() -> list[dict[str, object]]:
    global _cached_base_stations

    if not settings.public_data_api_key or not settings.public_data_api_base_url:
        _cached_base_stations = get_mock_base_stations()
        return _cached_base_stations

    try:
        payload = _fetch_public_data()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        _cached_base_stations = get_mock_base_stations()
        return _cached_base_stations

    _cached_base_stations = map_public_base_stations(payload)
    return _cached_base_stations


def get_base_stations() -> list[dict[str, object]]:
    if _cached_base_stations is None:
        return sync_base_stations_from_public_api()

    return _cached_base_stations


def _fetch_public_data() -> object:
    url = _build_public_data_url()
    request = Request(url, headers={"Accept": "application/json"})

    with urlopen(request, timeout=8) as response:
        body = response.read().decode("utf-8")

    return json.loads(body)


def _build_public_data_url() -> str:
    base_url = settings.public_data_api_base_url or ""
    separator = "&" if urlparse(base_url).query else "?"
    query = urlencode({"serviceKey": settings.public_data_api_key, "type": "json"})
    return f"{base_url}{separator}{query}"
