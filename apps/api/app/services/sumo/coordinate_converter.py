from __future__ import annotations

from app.services.sumo.traci_client import TraCIClient


class CoordinateConverter:
    def __init__(self, traci_client: TraCIClient) -> None:
        self._traci_client = traci_client

    def sumo_to_lat_lng(self, x: float, y: float) -> tuple[float, float]:
        try:
            return self._traci_client.convert_geo(float(x), float(y))
        except Exception:
            center_lat = 37.5667
            center_lng = 126.9784
            return center_lat + float(y) / 111_000, center_lng + float(x) / 88_000
