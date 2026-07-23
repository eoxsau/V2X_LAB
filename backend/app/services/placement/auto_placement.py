"""'적당히 랜덤' 자동 배치 — Best-Candidate(Mitchell) 블루노이즈 샘플링.

목적: 사용자가 공간 규모가 커질 때 BS/RSU를 일일이 손으로 찍는 수고를 덜기 위한 편의 배치.
      SA 최적화(sa_placement)와 달리 지연을 최소화하지 않는다. 목표는 "뭉치지도, 격자처럼
      딱딱하지도 않게" 도로 노드 위에 고르게 흩뿌리는 것.

알고리즘(Best-Candidate): 점을 하나 놓을 때마다 후보를 m개 무작위로 뽑아, 이미 놓인 점들에서
가장 먼 후보를 채택한다. m이 분산 강도(1=순수난수/뭉침, 클수록 균등). 이미 놓인 점들에 대한
최근접 거리 조회를 SpatialGrid로 O(1)에 하므로 전체 O(N·m) — 수천 개도 1초 내.

후보 풀:
  - BS : 모든 도로 노드
  - RSU: 차수(degree) ≥ 3 인 교차로 노드
(sa_placement.build_candidates_from_graph 와 같은 기준이나, 자동배치는 대량 배치를 위해
 후보 수 상한을 두지 않는다.)
"""
from __future__ import annotations

import math
import random as _rng_module
from dataclasses import dataclass
from typing import Optional

from app.services.geo.spatial_grid import SpatialGrid


@dataclass
class PlacePoint:
    """블루노이즈로 선택된 배치 지점(도로 노드)."""
    node_id: str
    lat: float
    lng: float


def _in_bbox(lat: float, lng: float, bbox: dict) -> bool:
    """(lat, lng)가 bbox({s,w,n,e}) 안에 있는지."""
    return bbox["s"] <= lat <= bbox["n"] and bbox["w"] <= lng <= bbox["e"]


def build_pool(graph: dict, node_type: str, bbox: Optional[dict] = None) -> list[PlacePoint]:
    """mock_graph 노드에서 후보 풀을 만든다(상한 없음).

    node_type == 'rsu' 이면 교차로(degree ≥ 3) 노드만.
    bbox({s,w,n,e})가 주어지면 그 안의 노드만 후보로 삼는다 — mock_graph는 클리핑되지
    않아(Overpass가 경계 걸친 도로의 전체 노드를 반환 + 다운로드 bbox 확장) 사용자가 그린
    구역 밖 노드까지 포함하므로, 사용자 원본 bbox로 걸러 구역 안에만 배치되게 한다.
    """
    nodes = graph.get("nodes", {}) or {}
    adjacency = graph.get("adjacency", {}) or {}
    want_rsu = node_type == "rsu"
    pool: list[PlacePoint] = []
    for nid, data in nodes.items():
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            continue
        if want_rsu and len(adjacency.get(nid, [])) < 3:
            continue
        if bbox is not None and not _in_bbox(float(lat), float(lng), bbox):
            continue
        pool.append(PlacePoint(node_id=str(nid), lat=float(lat), lng=float(lng)))
    return pool


def _pool_cell_size_m(pool: list[PlacePoint], n: int) -> float:
    """풀의 지리적 범위와 목표 개수 N에서 그리드 셀 크기(≈목표 간격)를 추정한다."""
    if len(pool) < 2 or n < 1:
        return 200.0
    lats = [p.lat for p in pool]
    lngs = [p.lng for p in pool]
    lat0 = sum(lats) / len(lats)
    coslat = math.cos(math.radians(lat0)) or 1e-6
    h_m = (max(lats) - min(lats)) * 111_320.0
    w_m = (max(lngs) - min(lngs)) * 111_320.0 * coslat
    area = max(h_m * w_m, 1.0)
    # 균일 분포 시 특성 간격 ≈ √(면적/N). 셀을 그 절반쯤으로 잡으면 조회가 몇 셀 안에 끝난다.
    spacing = math.sqrt(area / n)
    return max(spacing * 0.5, 20.0)


def blue_noise_place(
    pool: list[PlacePoint],
    n: int,
    m: int = 10,
    seed: Optional[int] = None,
    anchors: Optional[list[tuple[float, float]]] = None,
) -> list[PlacePoint]:
    """풀에서 N개를 Best-Candidate 블루노이즈로 선택한다.

    Parameters
    ----------
    pool     : 후보 지점 목록.
    n        : 배치할 개수(풀보다 크면 풀 크기로 클램프).
    m        : 분산 강도(후보 표본 수). 1=순수난수, 클수록 균등.
    seed     : 재현용 시드.
    anchors  : 이미 배치된 지점(lat, lng) 목록 — 이들과도 멀어지게(기존 배치에 이어 붙일 때).
    """
    k = min(n, len(pool))
    if k <= 0:
        return []
    rng = _rng_module.Random(seed)

    ref_lat = sum(p.lat for p in pool) / len(pool)
    cell = _pool_cell_size_m(pool, k)
    # 이미 놓인 점(anchor 포함)들의 그리드 — add()로 증분 확장하며 최근접 조회.
    placed_grid = SpatialGrid(
        [], coords_fn=lambda t: (t[0], t[1]), cell_size_m=cell, ref_lat=ref_lat,
    )
    for a in (anchors or []):
        placed_grid.add((a[0], a[1]))
    have_placed = bool(anchors)

    result: list[PlacePoint] = []
    remaining = list(range(len(pool)))  # 남은 후보 인덱스 (swap-remove로 O(1) 제거)

    for _ in range(k):
        sample_n = min(m, len(remaining))
        sample_pos = rng.sample(range(len(remaining)), sample_n)
        best_pos: Optional[int] = None
        best_ci = -1
        best_d = -1.0
        for pos in sample_pos:
            ci = remaining[pos]
            p = pool[ci]
            if have_placed:
                _, d = placed_grid.nearest(p.lat, p.lng)
            else:
                d = float("inf")  # 첫 점: 아무거나(무작위)
            if d > best_d:
                best_d, best_pos, best_ci = d, pos, ci

        chosen = pool[best_ci]
        result.append(chosen)
        placed_grid.add((chosen.lat, chosen.lng))
        have_placed = True
        # swap-remove: remaining[best_pos] 를 마지막 원소로 덮고 pop
        remaining[best_pos] = remaining[-1]
        remaining.pop()

    return result


def nearest_neighbor_cv(points: list[PlacePoint]) -> float:
    """배치 균등성 지표: 최근접이웃 거리의 변동계수(CV).

    균일난수(뭉침) ≈ 0.52 / 블루노이즈 ≈ 0.25~0.35 / 완벽 격자 ≈ 0.
    "밀도 차이가 너무 크지 않은지"를 수치로 확인하는 용도.
    """
    if len(points) < 3:
        return 0.0
    grid = SpatialGrid(
        list(range(len(points))),
        coords_fn=lambda i: (points[i].lat, points[i].lng),
        cell_size_m=_pool_cell_size_m(points, len(points)),
    )
    dists: list[float] = []
    for i, p in enumerate(points):
        # 자기 자신을 제외한 최근접: 반경을 넓혀가며 2개 이상 나오면 두 번째가 최근접이웃
        found = grid.within(p.lat, p.lng, _pool_cell_size_m(points, len(points)) * 4)
        nd = min((d for (j, d) in found if j != i), default=None)
        if nd is not None:
            dists.append(nd)
    if len(dists) < 3:
        return 0.0
    mean = sum(dists) / len(dists)
    if mean <= 0:
        return 0.0
    var = sum((d - mean) ** 2 for d in dists) / len(dists)
    return math.sqrt(var) / mean
