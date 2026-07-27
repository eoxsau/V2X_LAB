"""배치 최적화 오케스트레이터 — 앱이 부르는 **단일 진입점** (배치설계 v2 §6-1).

    net + 건물 + 생성 교통  →  후보 생성 → A_seg 사전계산(캐시) → 타입 보존형 joint SA
                                                                    → 배치 N_BS + N_RSU

main.py는 `optimize_placement_for_area()` 하나만 알면 된다. K·A_seg 캐시·좌표 정합 같은
세부는 전부 안에서 처리한다.

설계 §6-2의 joint 원칙: BS와 RSU를 **한 SA로 동시에** 최적화한다. staged(BS 먼저 → RSU)는
BS 위치가 RSU 가치를 바꾸는 상호작용을 못 본다 — greedy가 근시안인 것과 같은 약점이다.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Optional, Sequence

from .a_seg_cache import build_a_seg_table
from .candidates import DEFAULT_K, build_bs_candidates, build_rsu_candidates
from .sa_engine import PlacementResult, optimize
from .scoring import DemandPoint

# 수요점 하나가 대표하는 도로 조각의 최대 길이(m).
# RSU 셀 반경(5G 156m)보다 충분히 짧아야 조각 하나가 여러 RSU에 걸치지 않는다.
# 영등포 실측: 엣지 길이 중앙 35m·p90 97m라 대부분은 쪼갤 필요가 없고,
# 150m를 넘는 3%(총 연장의 20%)만 분할된다.
MAX_SEGMENT_M = 75.0


def _log_noop(_: str) -> None:
    pass


def demand_from_edge_loads(
    edge_loads: Sequence[dict],
    net=None,
    max_segment_m: float = MAX_SEGMENT_M,
) -> list[DemandPoint]:
    """`TrafficScenario.peak_edge_loads` → 채점용 수요점.

    긴 엣지는 조각으로 쪼개고 차량 수를 길이 비례로 나눈다. 엣지 하나를 중점 한 점으로
    뭉개면 RSU(반경 156m)를 배치할 공간 해상도가 사라진다.

    net이 없으면 분할하지 못하므로 엣지 중점을 그대로 쓴다(짧은 엣지만 있는 net이면 충분).
    """
    out: list[DemandPoint] = []
    for ld in edge_loads:
        eid = str(ld.get("edge_id") or "")
        veh = float(ld.get("vehicle_count") or 0.0)
        if veh <= 0.0:
            continue
        shape = None
        if net is not None:
            try:
                shape = net.getEdge(eid).getShape()
            except Exception:
                shape = None
        if not shape or len(shape) < 2:
            out.append(DemandPoint(eid, float(ld["lat"]), float(ld["lng"]), veh))
            continue

        # 엣지 전체 길이 → 필요한 조각 수
        total = sum(math.dist(shape[i], shape[i + 1]) for i in range(len(shape) - 1))
        n_seg = max(1, int(math.ceil(total / max_segment_m)))
        if n_seg == 1:
            out.append(DemandPoint(eid, float(ld["lat"]), float(ld["lng"]), veh))
            continue

        # 각 조각의 중점을 형상 위에서 뽑는다
        step = total / n_seg
        for s in range(n_seg):
            target = step * (s + 0.5)
            acc = 0.0
            px, py = shape[-1]
            for i in range(len(shape) - 1):
                seg = math.dist(shape[i], shape[i + 1])
                if acc + seg >= target:
                    t = (target - acc) / seg if seg > 0 else 0.0
                    px = shape[i][0] + t * (shape[i + 1][0] - shape[i][0])
                    py = shape[i][1] + t * (shape[i + 1][1] - shape[i][1])
                    break
                acc += seg
            lng, lat = net.convertXY2LonLat(px, py)
            out.append(DemandPoint(f"{eid}#s{s}", lat, lng, veh / n_seg))
    return out


def optimize_placement_for_area(
    net,
    buildings_gdf,
    demand: Sequence[DemandPoint],
    n_bs: int,
    n_rsu: int,
    *,
    tech: str = "5G",
    k: float = DEFAULT_K,
    cache_dir: Optional[str] = None,
    routable_edge_ids: Optional[set] = None,
    sa_iter: Optional[int] = None,
    n_greedy: int = 1,
    n_random: int = 2,
    seed: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
) -> PlacementResult:
    """구역 하나에 대해 BS N_BS개 + RSU N_RSU개를 **함께** 최적화한다.

    sa_iter : None이면 후보 수에 비례해 자동 결정한다.
        ⚠️ 고정값을 쓰면 후보가 많을 때 탐색이 덜 되어 "후보를 늘렸는데 결과가 나빠지는"
        비단조 현상이 나온다(K 실측에서 확인 — candidates.py 주석 참조).
    """
    log = log or _log_noop
    bs = build_bs_candidates(buildings_gdf, tech, k) if n_bs > 0 else []
    rsu = (build_rsu_candidates(net, tech, k, routable_edge_ids=routable_edge_ids)
           if n_rsu > 0 else [])
    if not bs and not rsu:
        log("후보가 없어 최적화를 건너뜁니다")
        return PlacementResult(tech=tech, n_bs=0, n_rsu=0)
    log(f"후보 BS {len(bs)}개 / RSU {len(rsu)}개 (K={k:g}), 수요 {len(demand)}점")

    a_seg = build_a_seg_table(demand, list(bs) + list(rsu), buildings_gdf, tech,
                              cache_dir=cache_dir, log=log)

    n_iter = sa_iter if sa_iter is not None else max(800, 4 * (len(bs) + len(rsu)))
    res = optimize(bs, rsu, demand, n_bs, n_rsu, tech=tech, a_seg=a_seg,
                   n_greedy=n_greedy, n_random=n_random, sa_iter=n_iter, seed=seed)
    log(f"배치 완료 — 비용 {res.cost_initial_ms:.2f} → {res.cost_final_ms:.2f} ms "
        f"({res.improvement_pct:+.1f}%), 미커버 {res.uncovered_pct:.1f}%, "
        f"outage {res.outage_pct:.1f}%, 채점 {res.n_evaluations}회")
    return res
