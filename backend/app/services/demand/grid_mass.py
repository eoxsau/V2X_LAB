"""격자 존 타일링 + 건물 질량 (radiation model 재료).

문서 traffic_demand_design_v2.md §3(격자 존)·§4(질량=건물 부피).

질량 정의: m_i = Σ (건물 바닥면적 × 층수)  = 연면적(gross floor area).
           인구·일자리의 프록시. 건축물대장/F_FAC_BUILDING의 ARCHAREA·GRND_FLR 또는
           폴리곤 면적 × 층수로 계산.

배정 규칙(§3-3): 대표점(representative_point) 배정 — 건물 대표점이 든 셀에 건물 전체를 배정.
                 (centroid는 ㄱ자 건물에서 폴리곤 밖으로 나갈 수 있어 금지 → 호출부에서
                  shapely representative_point 사용 권장.)

격자(§3-2·§3-4): 셀 크기 cell_size_m, 원점 origin은 파라미터(MAUP 민감도 분석용).
                 국소 등거리(equirectangular) 평면(m)으로 투영해 정사각 셀 인덱싱.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

_M_PER_DEG_LAT = 111_320.0


@dataclass
class Zone:
    """격자 셀 하나 = radiation model의 '존'."""
    ix: int
    iy: int
    center_lat: float
    center_lng: float
    mass: float          # Σ 연면적 (m²)
    n_buildings: int


def _projector(ref_lat: float):
    coslat = math.cos(math.radians(ref_lat)) or 1e-6

    def to_xy(lat: float, lng: float) -> tuple[float, float]:
        return (lng * coslat * _M_PER_DEG_LAT, lat * _M_PER_DEG_LAT)

    def to_ll(x: float, y: float) -> tuple[float, float]:
        return (y / _M_PER_DEG_LAT, x / (coslat * _M_PER_DEG_LAT))

    return to_xy, to_ll


def build_zones(
    buildings: Iterable[tuple[float, float, float]],
    cell_size_m: float = 300.0,
    ref_lat: Optional[float] = None,
    origin_shift_m: tuple[float, float] = (0.0, 0.0),
) -> list[Zone]:
    """건물들을 격자 셀로 묶어 존별 질량을 계산한다.

    Parameters
    ----------
    buildings : iterable of (rep_lat, rep_lng, mass)
        건물 대표점 위경도 + 질량(연면적). 호출부에서 representative_point·면적×층수로 준비.
    cell_size_m : 셀 한 변(m). 문서 §3-2 권장 200~500.
    ref_lat : 경도→미터 축척 기준 위도. None이면 첫 건물 위도.
    origin_shift_m : (dx, dy) 격자 원점 이동(m). MAUP 민감도 분석용(§3-4, 반 칸 이동 등).

    Returns
    -------
    list[Zone] — 질량 > 0 인 셀만.
    """
    blist = list(buildings)
    if not blist:
        return []
    if ref_lat is None:
        ref_lat = blist[0][0]
    to_xy, to_ll = _projector(ref_lat)
    cell = max(float(cell_size_m), 1.0)
    ox, oy = origin_shift_m

    agg: dict[tuple[int, int], list] = {}  # (ix,iy) -> [mass_sum, n]
    for lat, lng, mass in blist:
        if mass <= 0:
            continue
        x, y = to_xy(lat, lng)
        ix = int(math.floor((x - ox) / cell))
        iy = int(math.floor((y - oy) / cell))
        rec = agg.get((ix, iy))
        if rec is None:
            agg[(ix, iy)] = [mass, 1]
        else:
            rec[0] += mass
            rec[1] += 1

    zones: list[Zone] = []
    for (ix, iy), (m, n) in agg.items():
        cx = (ix + 0.5) * cell + ox
        cy = (iy + 0.5) * cell + oy
        clat, clng = to_ll(cx, cy)
        zones.append(Zone(ix=ix, iy=iy, center_lat=clat, center_lng=clng,
                          mass=m, n_buildings=n))
    return zones


def zone_stats(zones: list[Zone]) -> dict:
    """존 질량장 요약 통계 — 검증·품질 진단용."""
    if not zones:
        return {"n_zones": 0}
    masses = sorted(z.mass for z in zones)
    n = len(masses)
    total = sum(masses)
    nb = sorted(z.n_buildings for z in zones)
    return {
        "n_zones": n,
        "mass_total": total,
        "mass_min": masses[0],
        "mass_median": masses[n // 2],
        "mass_max": masses[-1],
        "mass_p90": masses[int(n * 0.9)],
        "max_over_median": masses[-1] / max(masses[n // 2], 1e-9),
        "buildings_median_per_zone": nb[n // 2],
        "buildings_min_per_zone": nb[0],
    }
