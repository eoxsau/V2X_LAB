from collections.abc import Iterable
from hashlib import sha1

REASONABLE_DEFAULT_CAPACITY = 1200

mock_base_stations: list[dict[str, object]] = [
    {
        "id": "BS-SEO-01",
        "name": "City Hall Sector",
        "latitude": 37.5669,
        "longitude": 126.9782,
        "frequency": 3500,
        "tx_power": None,
        "antenna_height": 28,
        "capacity": 1400,
        "source": "mock",
    },
    {
        "id": "BS-SEO-02",
        "name": "Euljiro Edge",
        "latitude": 37.5661,
        "longitude": 126.9912,
        "frequency": 3500,
        "tx_power": None,
        "antenna_height": 31,
        "capacity": 1350,
        "source": "mock",
    },
    {
        "id": "BS-SEO-03",
        "name": "Namdaemun Relay",
        "latitude": 37.5598,
        "longitude": 126.9771,
        "frequency": None,
        "tx_power": None,
        "antenna_height": 24,
        "capacity": 1200,
        "source": "mock",
    },
    {
        "id": "BS-SEO-04",
        "name": "Gwanghwamun Node",
        "latitude": 37.5759,
        "longitude": 126.9768,
        "frequency": 2800,
        "tx_power": None,
        "antenna_height": None,
        "capacity": 1500,
        "source": "mock",
    },
    {
        "id": "BS-SEO-05",
        "name": "Myeongdong Microcell",
        "latitude": 37.5637,
        "longitude": 126.985,
        "frequency": 3500,
        "tx_power": None,
        "antenna_height": 18,
        "capacity": 950,
        "source": "mock",
    },
]


def map_public_base_stations(payload: object) -> list[dict[str, object]]:
    records = _extract_records(payload)
    mapped = [
        station
        for index, record in enumerate(records)
        if (station := map_public_base_station(record, index)) is not None
    ]
    return mapped or get_mock_base_stations()


def map_public_base_station(
    record: dict[str, object],
    index: int,
) -> dict[str, object] | None:
    latitude = _read_float(record, ("latitude", "lat", "y", "wgs84_lat", "station_latitude"))
    longitude = _read_float(record, ("longitude", "lng", "lon", "x", "wgs84_lng", "station_longitude"))

    if latitude is None or longitude is None:
        return None

    external_id = _read_string(record, ("id", "station_id", "base_station_id", "site_id", "cell_id"))
    name = _read_string(record, ("name", "station_name", "site_name", "base_station_name"))
    station_id = external_id or _stable_station_id(record, index)

    return {
        "id": station_id,
        "name": name or f"Public Station {index + 1}",
        "latitude": latitude,
        "longitude": longitude,
        "frequency": _read_float(record, ("frequency", "freq", "frequency_mhz", "band_frequency")),
        "tx_power": _read_float(record, ("tx_power", "transmit_power", "power", "eirp")),
        "antenna_height": _read_float(record, ("antenna_height", "height", "antenna_height_m")),
        "capacity": _read_int(record, ("capacity", "user_capacity", "max_users"))
        or _default_capacity(index),
        "source": "public_api",
        "raw_payload": record,
    }


def get_mock_base_stations() -> list[dict[str, object]]:
    return [station.copy() for station in mock_base_stations]


def _extract_records(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    direct_records = _find_first_list(
        payload,
        (
            "items",
            "item",
            "records",
            "data",
            "features",
            "base_stations",
            "stations",
        ),
    )
    if direct_records is not None:
        return [item for item in direct_records if isinstance(item, dict)]

    response = payload.get("response")
    if isinstance(response, dict):
        body = response.get("body")
        if isinstance(body, dict):
            return _extract_records(body)

    return []


def _find_first_list(payload: dict[str, object], keys: Iterable[str]) -> list[object] | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _find_first_list(value, keys)
            if nested is not None:
                return nested
    return None


def _read_string(record: dict[str, object], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _read_float(record: dict[str, object], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = record.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _read_int(record: dict[str, object], keys: Iterable[str]) -> int | None:
    value = _read_float(record, keys)
    return int(value) if value is not None else None


def _stable_station_id(record: dict[str, object], index: int) -> str:
    digest = sha1(repr(sorted(record.items())).encode("utf-8")).hexdigest()[:8]
    return f"public-bs-{index + 1}-{digest}"


def _default_capacity(index: int) -> int:
    return REASONABLE_DEFAULT_CAPACITY + (index % 4) * 150
