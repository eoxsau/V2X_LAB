from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SumoCoordinateConverter:
    conv_boundary: tuple[float, float, float, float] | None = None
    orig_boundary: tuple[float, float, float, float] | None = None

    @property
    def status(self) -> str:
        return "projection_metadata" if self.conv_boundary and self.orig_boundary else "fallback_bbox"

    def xy_to_lat_lng(self, x: float, y: float) -> tuple[float, float]:
        if self.conv_boundary and self.orig_boundary:
            min_x, min_y, max_x, max_y = self.conv_boundary
            min_lng, min_lat, max_lng, max_lat = self.orig_boundary
            lng = _lerp(min_lng, max_lng, _ratio(x, min_x, max_x))
            lat = _lerp(min_lat, max_lat, _ratio(y, min_y, max_y))
            return lat, lng
        return 37.5667 + y / 111_000, 126.9784 + x / 88_000

    def lat_lng_to_xy(self, lat: float, lng: float) -> tuple[float, float]:
        if self.conv_boundary and self.orig_boundary:
            min_x, min_y, max_x, max_y = self.conv_boundary
            min_lng, min_lat, max_lng, max_lat = self.orig_boundary
            x = _lerp(min_x, max_x, _ratio(lng, min_lng, max_lng))
            y = _lerp(min_y, max_y, _ratio(lat, min_lat, max_lat))
            return x, y
        return (lng - 126.9784) * 88_000, (lat - 37.5667) * 111_000


def parse_boundary(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = [float(item) for item in value.split(",")]
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 else None


def _ratio(value: float, minimum: float, maximum: float) -> float:
    return 0 if maximum == minimum else max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


def _lerp(start: float, end: float, ratio: float) -> float:
    return start + (end - start) * ratio
