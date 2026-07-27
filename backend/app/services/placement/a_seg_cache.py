"""A_seg(건물 차폐) 일괄 사전계산 + 캐시 — 배치 최적화의 최대 비용 항목.

A_seg = **3D LOS를 가로막는 건물 수 × 12 dB** (latency v3.1 §7).
판정 의미론은 `buildings.building_obstruction_analyzer._is_blocked_3d`와 동일하다:
건물 중심까지의 진행률 t에서 빔 높이가 건물 높이보다 낮으면 차단.

왜 별도 모듈인가 (2026-07-27 실측):
    런타임 분석기 `analyze_vehicle_to_node`는 1회 **10.43 ms**다. 배치 최적화는
    (수요 × 후보) 쌍 전부가 필요해서 191×300이면 **10분**, 1,104×300이면 **58분**이다.
    프로파일링해보니 병목이 계산 자체가 아니라 **호출당 오버헤드**였다:
        · 1행짜리 GeoDataFrame 생성 + to_crs   2.03 ms  → Transformer 직접이면 0.02 ms (91배)
        · 3,014동 전수 intersects              2.97 ms  → STRtree면 1.31 ms
    런타임 분석기는 대시보드 표시용 부가정보(하이라이트 건물, 신뢰도 등)까지 만들므로
    그대로 두고, 배치용으로 **A_seg 숫자만** 뽑는 경로를 따로 둔다.

⚠️ 거리 컷오프가 확장성을 가른다:
    수요점 반경 d_edge 밖의 후보는 어차피 연결되지 않으므로 계산할 필요가 없다.
    컷오프가 없으면 비용이 **면적의 제곱**으로 늘어(20 km²에서 2시간 초과), 있으면
    한 수요점이 보는 후보 수가 면적과 무관해져 **면적에 선형**이 된다.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Optional, Sequence

A_SEG_PER_BUILDING_DB = 12.0     # latency v3.1 §7
VEHICLE_HEIGHT_M = 1.5           # building_obstruction_analyzer._VEHICLE_HEIGHT_M와 동일


def _log_noop(_: str) -> None:
    pass


class ASegTable:
    """(수요점 id, 후보 id) → A_seg dB. 없으면 0(가시선)."""

    def __init__(self, table: Optional[dict[tuple[str, str], float]] = None):
        self._t: dict[tuple[str, str], float] = table or {}

    def __call__(self, demand_id: str, station_id: str) -> float:
        return self._t.get((demand_id, station_id), 0.0)

    def __len__(self) -> int:
        return len(self._t)

    @property
    def blocked_pairs(self) -> int:
        return sum(1 for v in self._t.values() if v > 0.0)


def _cache_key(demand, candidates, buildings_n: int, tech: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(
        [[d.id, round(d.lat, 6), round(d.lng, 6)] for d in demand], sort_keys=True).encode())
    h.update(json.dumps(
        [[c.id, round(c.lat, 6), round(c.lng, 6), c.node_type, round(c.height_m, 1)]
         for c in candidates], sort_keys=True).encode())
    h.update(f"{buildings_n}|{tech}".encode())
    return h.hexdigest()[:16]


def build_a_seg_table(
    demand: Sequence,
    candidates: Sequence,
    buildings_gdf,
    tech: str = "5G",
    *,
    cache_dir: Optional[str] = None,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> ASegTable:
    """(수요 × 후보) 쌍의 A_seg를 한 번에 계산하고 캐시한다.

    커버리지 밖 쌍은 건너뛴다 — 연결될 수 없으므로 값이 필요 없다.
    """
    log = log or _log_noop
    if buildings_gdf is None or len(buildings_gdf) == 0:
        log("건물이 없어 A_seg를 0으로 둡니다 (차폐 없음)")
        return ASegTable()

    key = _cache_key(demand, candidates, len(buildings_gdf), tech)
    cfile = None
    if cache_dir:
        cdir = Path(cache_dir)
        cdir.mkdir(parents=True, exist_ok=True)
        cfile = cdir / f"aseg_{key}.json"
        if cfile.exists() and not force:
            raw = json.loads(cfile.read_text(encoding="utf-8"))
            t = {(r[0], r[1]): float(r[2]) for r in raw}
            log(f"A_seg 캐시 적중: 차폐 쌍 {len(t)}개 ({cfile.name})")
            return ASegTable(t)

    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    from app.services.latency import formula_v31 as f31

    # ── 건물을 미터 좌표로 한 번만 투영하고 STRtree를 한 번만 만든다 ──────────
    fwd = Transformer.from_crs(4326, 3857, always_xy=True)
    geoms_m = list(buildings_gdf.to_crs(3857).geometry)
    if "height_m" in buildings_gdf.columns:
        heights = [float(v or 0.0) for v in buildings_gdf["height_m"].fillna(0.0)]
    else:
        heights = [0.0] * len(geoms_m)
    # 높이 미상은 런타임 분석기와 같이 **보수적으로 차단**으로 본다
    tree = STRtree(geoms_m)
    cents = [g.centroid for g in geoms_m]

    dxy = [fwd.transform(d.lng, d.lat) for d in demand]
    cxy = [fwd.transform(c.lng, c.lat) for c in candidates]
    cover = [f31.resolve_coverage_radius(tech, c.node_type) for c in candidates]

    table: dict[tuple[str, str], float] = {}
    n_pairs = 0
    for di, dp in enumerate(demand):
        dx, dy = dxy[di]
        for ci, cd in enumerate(candidates):
            sx, sy = cxy[ci]
            # 3857은 위도에 따라 거리가 늘어나므로 근사 보정 후 컷오프 판정
            scale = math.cos(math.radians(dp.lat)) or 1.0
            if math.hypot(sx - dx, sy - dy) * scale > cover[ci]:
                continue
            n_pairs += 1
            line = LineString([(dx, dy), (sx, sy)])
            blocked = 0
            for bi in tree.query(line, predicate="intersects"):
                bi = int(bi)
                bh = heights[bi]
                if bh <= 0.0:
                    blocked += 1            # 높이 미상 → 보수적 차단
                    continue
                t = max(0.0, min(1.0, line.project(cents[bi], normalized=True)))
                h_beam = VEHICLE_HEIGHT_M + t * (cd.height_m - VEHICLE_HEIGHT_M)
                if h_beam < bh:
                    blocked += 1
            if blocked:
                table[(dp.id, cd.id)] = blocked * A_SEG_PER_BUILDING_DB

    log(f"A_seg 계산: 쌍 {n_pairs}개(커버 안) 중 차폐 {len(table)}개 "
        f"| 후보 {len(candidates)} × 수요 {len(demand)}")
    if cfile is not None:
        cfile.write_text(json.dumps([[k[0], k[1], v] for k, v in table.items()]),
                         encoding="utf-8")
        log(f"A_seg 캐시 저장: {cfile}")
    return ASegTable(table)
