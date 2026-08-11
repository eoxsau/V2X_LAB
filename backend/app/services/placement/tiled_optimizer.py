"""타일 분할-병합 배치 최적화 오케스트레이터 (배치설계 PART B).

대규모 수요(500점+)를 K×K 타일로 분할해 각 타일을 독립적으로 최적화한 뒤
결과를 병합한다. A_seg 계산량이 O(D×C) → O(D×C/k²)로 줄어 k=2에서 ~4배 속도향상.

직접 sa_engine.optimize()를 호출하므로 optimizer.py와 순환 임포트가 없다.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from .a_seg_cache import build_a_seg_table
from .candidates import DEFAULT_K, build_bs_candidates, build_rsu_candidates
from .sa_engine import PlacementResult, optimize
from .scoring import DemandPoint
from .tile_utils import (
    bbox_from_points,
    deduplicate_placed,
    filter_by_tile,
    split_tiles,
)

_MIN_DEMAND_FOR_TILING = 500
_MIN_STATIONS_FOR_TILING = 5
_DEFAULT_TILE_K = 2


def _log_noop(_: str) -> None:
    pass


def tiled_optimize_placement(
    net,
    buildings_gdf,
    demand: Sequence[DemandPoint],
    n_bs: int,
    n_rsu: int,
    *,
    tech: str = "5G",
    k_cand: float = DEFAULT_K,
    tile_k: int = _DEFAULT_TILE_K,
    cache_dir: Optional[str] = None,
    routable_edge_ids: Optional[set] = None,
    sa_iter: Optional[int] = None,
    n_greedy: int = 1,
    n_random: int = 2,
    seed: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[float, str], None]] = None,
) -> PlacementResult:
    """K×K 타일 분할-병합 배치 최적화.

    각 타일에서 수요·후보를 독립적으로 추출하고 `sa_engine.optimize()`를 호출한다.
    타일 수 proportional BS/RSU 수 배정 → 병합 → 중복 제거 → 개수 트림.

    Parameters
    ----------
    tile_k   : 타일 격자 크기 (tile_k × tile_k)
    k_cand   : 후보 격자 K (candidates.py DEFAULT_K 기본값)
    """
    log = log or _log_noop
    demand = list(demand)

    # ── 전체 후보 생성 (한 번만) ──────────────────────────────────────────────
    bs_all = build_bs_candidates(buildings_gdf, tech, k_cand) if n_bs > 0 else []
    rsu_all = (build_rsu_candidates(net, tech, k_cand, routable_edge_ids=routable_edge_ids)
               if n_rsu > 0 else [])
    all_cands = list(bs_all) + list(rsu_all)

    if not all_cands:
        log("후보가 없어 타일 최적화를 건너뜁니다")
        return PlacementResult(tech=tech, n_bs=0, n_rsu=0)

    # ── bbox 분할 ─────────────────────────────────────────────────────────────
    lats = [d.lat for d in demand]
    lngs = [d.lng for d in demand]
    lat_min, lat_max, lng_min, lng_max = bbox_from_points(lats, lngs)
    tiles = split_tiles(lat_min, lat_max, lng_min, lng_max, tile_k)

    # 수요가 있는 타일만 남기기
    tile_data = []
    for tile in tiles:
        tile_d = filter_by_tile(demand, tile, lambda d: d.lat, lambda d: d.lng)
        if not tile_d:
            continue
        tile_bs = filter_by_tile(bs_all, tile, lambda c: c.lat, lambda c: c.lng)
        tile_rsu = filter_by_tile(rsu_all, tile, lambda c: c.lat, lambda c: c.lng)
        tile_data.append((tile, tile_d, tile_bs, tile_rsu))

    if len(tile_data) <= 1:
        # 수요가 한 타일에 몰려 있으면 단일 최적화로 진행
        log("[타일] 단일 타일로 폴백")
        a_seg = build_a_seg_table(demand, all_cands, buildings_gdf, tech,
                                   cache_dir=cache_dir, log=log)
        n_iter = sa_iter if sa_iter is not None else 0
        return optimize(bs_all, rsu_all, demand, n_bs, n_rsu,
                        tech=tech, a_seg=a_seg,
                        n_greedy=n_greedy, n_random=n_random,
                        sa_iter=n_iter, seed=seed)

    # ── 타일별 수요 비율에 따라 BS/RSU 개수 배분 ──────────────────────────────
    # DemandPoint의 수요 값은 `vehicle_count`다(scoring.DemandPoint) — `demand`가 아니다.
    total_demand_wt = sum(sum(d.vehicle_count for d in td) for _, td, _, _ in tile_data)

    all_placed: list[dict] = []
    n_evals_total = 0
    cost_initial_total = 0.0
    cost_final_total = 0.0
    uncovered_sum = 0.0
    outage_sum = 0.0

    log(f"[타일] {len(tile_data)}개 타일로 분할 (격자={tile_k}×{tile_k})")

    for ti, (tile, tile_d, tile_bs, tile_rsu) in enumerate(tile_data):
        tile_wt = sum(d.vehicle_count for d in tile_d) / max(total_demand_wt, 1e-9)
        tile_n_bs = max(1, round(n_bs * tile_wt)) if n_bs > 0 else 0
        tile_n_rsu = max(1, round(n_rsu * tile_wt)) if n_rsu > 0 else 0

        # 후보가 없는 타입은 0으로 처리
        if not tile_bs:
            tile_n_bs = 0
        if not tile_rsu:
            tile_n_rsu = 0
        if tile_n_bs == 0 and tile_n_rsu == 0:
            continue

        tile_cands = list(tile_bs) + list(tile_rsu)

        log(f"  타일 {ti+1}/{len(tile_data)} "
            f"(r{tile.row}c{tile.col}): "
            f"수요 {len(tile_d)}점 / BS후보 {len(tile_bs)} / RSU후보 {len(tile_rsu)} "
            f"→ BS {tile_n_bs}개 RSU {tile_n_rsu}개")

        def _tile_progress(frac: float, phase: str) -> None:
            if progress:
                overall = (ti + frac) / len(tile_data)
                progress(overall * 0.9, f"[타일 {ti+1}/{len(tile_data)}] {phase}")

        a_seg = build_a_seg_table(
            tile_d, tile_cands, buildings_gdf, tech,
            cache_dir=cache_dir, log=log, progress=_tile_progress,
        )

        n_iter = sa_iter if sa_iter is not None else 0
        tile_seed = (seed or 42) + ti
        res = optimize(
            tile_bs, tile_rsu, tile_d, tile_n_bs, tile_n_rsu,
            tech=tech, a_seg=a_seg,
            n_greedy=n_greedy, n_random=n_random,
            sa_iter=n_iter, seed=tile_seed,
        )
        all_placed.extend(res.placed)
        n_evals_total += res.n_evaluations
        cost_initial_total += res.cost_initial_ms
        cost_final_total += res.cost_final_ms
        uncovered_sum += res.uncovered_pct * tile_wt
        outage_sum += res.outage_pct * tile_wt

    if progress:
        progress(0.95, "중복 배치 제거")

    # ── 병합: 인접 중복 제거 → 개수 트림 ─────────────────────────────────────
    merged = deduplicate_placed(all_placed)

    bs_placed = [p for p in merged if (p.get("node_type") or "bs").lower() in ("bs", "5g", "4g")]
    rsu_placed = [p for p in merged if (p.get("node_type") or "").lower() == "rsu"]
    bs_placed = bs_placed[:n_bs]
    rsu_placed = rsu_placed[:n_rsu]
    final_placed = bs_placed + rsu_placed

    impr = ((cost_initial_total - cost_final_total) / max(cost_initial_total, 1e-9)) * 100.0

    log(f"[타일 완료] 배치 {len(final_placed)}개 "
        f"(BS {len(bs_placed)} / RSU {len(rsu_placed)}), "
        f"비용 {cost_initial_total:.2f}→{cost_final_total:.2f} ms ({impr:+.1f}%)")

    return PlacementResult(
        tech=tech,
        n_bs=len(bs_placed),
        n_rsu=len(rsu_placed),
        placed=final_placed,
        cost_initial_ms=cost_initial_total,
        cost_final_ms=cost_final_total,
        improvement_pct=impr,
        uncovered_pct=uncovered_sum,
        outage_pct=outage_sum,
        n_candidates_bs=len(bs_all),
        n_candidates_rsu=len(rsu_all),
        n_evaluations=n_evals_total,
    )
