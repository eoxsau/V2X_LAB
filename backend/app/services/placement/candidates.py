"""배치 후보 생성 — BS는 건물 옥상, RSU는 교차로 (배치설계 v2 §3-1).

두 후보 집합은 **서로소**다. 건물과 교차로는 겹치지 않는다.

후보 밀도는 **커버리지에서 유도**한다 (2026-07-27 결정):

    간격 = d_edge / K

⚠️ 예전 구현은 `_MAX_CANDIDATES = 300`이라는 **구역 크기와 무관한 상수**를 썼다.
   1 km² 구역에도 300개, 100 km² 구역에도 300개였다. 근거가 없다.
   커버가 1,000 m인데 후보를 50 m 간격으로 깔아봐야 기지국을 50 m 옮기는 것은 결과를
   거의 바꾸지 못한다 — 계산만 늘고 해는 안 좋아진다. 그래서 **커버리지 대비 상대 간격**을
   쓰고, 후보 수는 면적에 따라 자동으로 따라오게 한다.

   면적별 BS 후보 수 (5G, d_edge=1000 m):
       K=4  (250 m)   1 km² 16개 / 4.6 km² 74개 / 20 km² 320개
       K=6  (167 m)   1 km² 36개 / 4.6 km² 166개 / 20 km² 720개
       K=10 (100 m)   1 km² 100개 / 4.6 km² 460개 / 20 km² 2,000개

**K = 10 확정** (2026-07-27 실측, 영등포 2.33 km² / 수요 190점·1,104대 / N_BS=4·N_RSU=6):

    K    간격    BS후보   최적비용    직전 대비    SA 소요
    4    250m      55     36.28 ms       —        10.3 s
    6    167m      97     20.56 ms    -43.3%      13.4 s
   10    100m     244     15.22 ms    -26.0%      19.0 s   ← 무릎
   14     71m     423     14.92 ms     -2.0%      28.4 s
   20     50m     744     14.30 ms     -4.2%      49.4 s

K=10 이후로는 후보를 3배 늘려도(244→744) 비용이 6% 남짓 좋아질 뿐인데 SA 소요는 2.6배가 된다.
K=6은 아직 26% 손해라 부족하고, K=10이 "더 촘촘히 해도 별로 안 좋아지는" 첫 지점이다.

⚠️ 측정 방법 주의 — SA 반복을 후보 수에 **비례**시켜야 한다:
   고정 예산(800회)으로 재면 결과가 비단조로 나온다(K=10 15.22 → K=14 16.18 → K=20 16.68).
   후보가 늘면 최적해는 나빠질 수 없으므로 이는 탐색 부족이지 후보 밀도의 문제가 아니다.
   `n_iter = max(800, 4 × 후보수)`로 맞추자 단조로 수렴했다. K를 다시 잴 일이 있으면 이 조건을
   반드시 지킬 것.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.services.latency import formula_v31 as f31

_M_PER_DEG_LAT = 111_320.0

# 후보 격자 촘촘함 — 간격 = d_edge / K. 실측 근거는 모듈 docstring 참조.
DEFAULT_K = 10.0


@dataclass(frozen=True)
class Candidate:
    """설치 후보 한 자리."""
    id: str
    lat: float
    lng: float
    node_type: str            # "bs" | "rsu"
    height_m: float = 0.0     # BS: 건물 높이 + 마스트 / RSU: 폴 높이
    score: float = 0.0        # 칸 안에서 대표를 고른 근거 (BS=높이, RSU=차수)


def spacing_m(tech: str, node_type: str, k: float) -> float:
    """후보 격자 간격 = 그 유닛의 셀 반경 / K."""
    return f31.resolve_coverage_radius(tech, node_type) / max(k, 1e-6)


def _cell_of(lat: float, lng: float, cell_m: float, ref_lat: float) -> tuple[int, int]:
    coslat = math.cos(math.radians(ref_lat)) or 1e-6
    return (int(math.floor(lng * coslat * _M_PER_DEG_LAT / cell_m)),
            int(math.floor(lat * _M_PER_DEG_LAT / cell_m)))


def build_bs_candidates(
    buildings_gdf,
    tech: str = "5G",
    k: float = DEFAULT_K,
    mast_m: float = 3.0,
) -> list[Candidate]:
    """건물 → BS 후보. 격자 한 칸당 **가장 높은 건물 하나**.

    높은 건물을 고르는 이유: 옥상이 높을수록 3D LOS가 주변 건물에 덜 막혀 A_seg가 작다.
    같은 칸 안에서는 어차피 거리 차이가 커버리지 대비 미미하므로, 차폐로 대표를 고른다.

    buildings_gdf : `BuildingRepository.query_by_bbox_parquet` 결과 (EPSG:4326).
        `height_m` 또는 `ground_floor`로 높이를 잡는다.
    """
    if buildings_gdf is None or len(buildings_gdf) == 0:
        return []

    cell = spacing_m(tech, "bs", k)
    reps = buildings_gdf.geometry.representative_point()
    ref_lat = float(reps.y.mean())

    heights = None
    if "height_m" in buildings_gdf.columns:
        heights = buildings_gdf["height_m"]
    floors = buildings_gdf["ground_floor"] if "ground_floor" in buildings_gdf.columns else None

    best: dict[tuple[int, int], tuple[float, float, float]] = {}   # cell → (height, lat, lng)
    for i in range(len(buildings_gdf)):
        p = reps.iloc[i]
        lat, lng = float(p.y), float(p.x)
        h = 0.0
        if heights is not None:
            try:
                h = float(heights.iloc[i] or 0.0)
            except (TypeError, ValueError):
                h = 0.0
        if h <= 0.0 and floors is not None:
            try:
                h = float(floors.iloc[i] or 1.0) * 3.0     # 층당 3 m 근사
            except (TypeError, ValueError):
                h = 3.0
        key = _cell_of(lat, lng, cell, ref_lat)
        cur = best.get(key)
        if cur is None or h > cur[0]:
            best[key] = (h, lat, lng)

    return [
        Candidate(id=f"bsc_{ix}_{iy}", lat=lat, lng=lng, node_type="bs",
                  height_m=h + mast_m, score=h)
        for (ix, iy), (h, lat, lng) in best.items()
    ]


def build_rsu_candidates(
    net,
    tech: str = "5G",
    k: float = DEFAULT_K,
    min_degree: int = 3,
    pole_m: Optional[float] = None,
    routable_edge_ids: Optional[set] = None,
) -> list[Candidate]:
    """SUMO net 교차로 → RSU 후보. 격자 한 칸당 **차수가 가장 큰 교차로 하나**.

    설계 §3-5의 서사대로 RSU는 교차로에 놓인다 — 교차로가 spillback 병목이 생기는 자리다.
    차수가 클수록 합류가 많아 병목이 될 확률이 높으므로 대표로 고른다.

    routable_edge_ids : 주어지면 그 엣지에 접한 교차로만 후보로 삼는다(고립 성분 제외).
    """
    cell = spacing_m(tech, "rsu", k)
    nodes = list(net.getNodes())
    if not nodes:
        return []

    lls: list[tuple[str, float, float, int]] = []
    for n in nodes:
        try:
            edges = list(n.getIncoming()) + list(n.getOutgoing())
            if routable_edge_ids is not None:
                edges = [e for e in edges if e.getID() in routable_edge_ids]
            deg = len(edges)
            if deg < min_degree:
                continue
            x, y = n.getCoord()
            lng, lat = net.convertXY2LonLat(x, y)
            lls.append((n.getID(), lat, lng, deg))
        except Exception:
            continue
    if not lls:
        return []

    ref_lat = sum(t[1] for t in lls) / len(lls)
    best: dict[tuple[int, int], tuple[int, str, float, float]] = {}
    for nid, lat, lng, deg in lls:
        key = _cell_of(lat, lng, cell, ref_lat)
        cur = best.get(key)
        if cur is None or deg > cur[0]:
            best[key] = (deg, nid, lat, lng)

    h = pole_m if pole_m is not None else f31.H_RSU
    return [
        Candidate(id=f"rsuc_{nid}", lat=lat, lng=lng, node_type="rsu",
                  height_m=h, score=float(deg))
        for (deg, nid, lat, lng) in best.values()
    ]
