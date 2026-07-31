"""타일 분할 유틸리티 — 배치 분할-병합 최적화 (배치설계 PART B).

K×K 격자로 bbox를 나누고, 각 타일의 수요점·후보를 걸러낸 뒤 병합 단계에서
중복 배치를 거리 기반으로 제거한다.

수요 500점 이상, N_BS+N_RSU 5개 이상인 경우 분할을 적용하면 A_seg 계산이
O(D×C) → O(D×C/k²) 로 줄어 k=2에서 약 4배, k=3에서 약 9배 속도가 향상된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence, TypeVar

_M_PER_DEG_LAT = 111_320.0

T = TypeVar("T")


@dataclass
class Tile:
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    row: int
    col: int

    def contains(self, lat: float, lng: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lng_min <= lng <= self.lng_max


def bbox_from_points(lats: Sequence[float], lngs: Sequence[float]) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lng_min, lng_max) from coordinate lists."""
    return min(lats), max(lats), min(lngs), max(lngs)


def split_tiles(
    lat_min: float,
    lat_max: float,
    lng_min: float,
    lng_max: float,
    k: int,
    overlap: float = 0.15,
) -> list[Tile]:
    """bbox를 k×k 타일로 분할 (경계 중복 허용).

    overlap : 인접 타일 경계를 양쪽으로 dlat*overlap 만큼 확장.
    경계 근처 후보가 두 타일 모두에 포함되므로 병합 시 dedup이 필요하다.
    """
    dlat = (lat_max - lat_min) / max(k, 1)
    dlng = (lng_max - lng_min) / max(k, 1)
    buf_lat = dlat * overlap
    buf_lng = dlng * overlap
    tiles = []
    for r in range(k):
        for c in range(k):
            tiles.append(Tile(
                lat_min=lat_min + r * dlat - buf_lat,
                lat_max=lat_min + (r + 1) * dlat + buf_lat,
                lng_min=lng_min + c * dlng - buf_lng,
                lng_max=lng_min + (c + 1) * dlng + buf_lng,
                row=r,
                col=c,
            ))
    return tiles


def filter_by_tile(items: Sequence[T], tile: Tile, lat_fn, lng_fn) -> list[T]:
    """tile 범위 안에 있는 항목만 걸러낸다."""
    return [it for it in items if tile.contains(lat_fn(it), lng_fn(it))]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


def deduplicate_placed(placed_all: list[dict], radius_m: float = 200.0) -> list[dict]:
    """인접 배치 중복 제거 — 같은 node_type 안에서 radius_m 이내는 첫 번째만 남긴다."""
    out: list[dict] = []
    for p in placed_all:
        p_type = (p.get("node_type") or "bs").lower()
        p_lat = float(p.get("lat", 0))
        p_lng = float(p.get("lng", 0))
        too_close = any(
            (q.get("node_type") or "bs").lower() == p_type
            and haversine_m(p_lat, p_lng, float(q.get("lat", 0)), float(q.get("lng", 0))) < radius_m
            for q in out
        )
        if not too_close:
            out.append(p)
    return out
