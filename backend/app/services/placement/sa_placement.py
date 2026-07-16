"""
SA(Simulated Annealing) 기반 기지국 배치 최적화 엔진.

설계 문서 base_station_SA_design.pdf 의 골격을 그대로 구현한다:
  - greedy forward selection warm-start  (M/M/1 비선형식 직접 사용)
  - random initialization                (multi-start 보험)
  - SA 본체 (swap 이웃, 온도 기하 감소)
  - delta evaluation                     (swap 영향 노드만 부분 재계산)
  - multi-start                          (greedy + random 병렬 → 최선 채택)

첨두(peak) / 비첨두(off_peak) 는 각각 독립적인 문제로 실행한다(해석 A).
ITS 교통량이 없으면 mock_graph 엣지 밀도를 균일 수요 폴백으로 사용한다.
"""
from __future__ import annotations

import math
import random as _rng_module
from dataclasses import dataclass, field
from typing import Optional

# ── 기술 파라미터 — SA 최적화 전용 근사 모델의 사본 ────────────────────────────
# 주의: L_base/P_tx/alpha/beta 등은 formula_v31(설계문서 v3.1)과 다른 구식 근사식
# (_L_total_sa)의 파라미터다 — SA 반복 성능을 위해 유지. 단 coverage_radius_m은
# 런타임 규칙(formula_v31.resolve_coverage_radius: BS=d_edge)과 반드시 일치시킨다.
# 여기가 어긋나면 배치 최적화가 실제 시뮬레이션과 다른 커버리지로 계산된다.
_TECH_PARAMS: dict[str, dict] = {
    "4G": dict(L_base=25.0, P_tx=43.0, alpha=45.0, beta=3.5,
               RSRP_thresh=-85.0, RSRP_range=25.0, N_max=6, T_retx=8.0,
               C_tech=100, coverage_radius_m=2000.0),
    "5G": dict(L_base=15.0, P_tx=46.0, alpha=55.0, beta=3.0,
               RSRP_thresh=-90.0, RSRP_range=25.0, N_max=4, T_retx=1.0,
               C_tech=500, coverage_radius_m=1000.0),
    "6G": dict(L_base= 1.0, P_tx=48.0, alpha=68.0, beta=2.5,
               RSRP_thresh=-95.0, RSRP_range=25.0, N_max=3, T_retx=0.1,
               C_tech=2000, coverage_radius_m=500.0),
}
_RSU_COVERAGE_RADIUS_M = {"4G": 100.0, "5G": 150.0, "6G": 250.0}

# 커버리지 밖 수요점에 부과하는 미커버 패널티 (ms)
_UNCOVERED_PENALTY_MS = 200.0
# 후보 최대 개수 — mock_graph가 클 경우 탐색 공간 제한
_MAX_CANDIDATES = 300
# 수요점 당 최대 차량 수 (ITS congestion_score=1.0 기준)
_MAX_VEHICLES_PER_LINK = 40


# ──────────────────────────────────────────────────────────────────────────────
# 내부 데이터 구조
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """기지국(또는 RSU) 설치 후보 위치."""
    id: str
    lat: float
    lng: float
    degree: int = 1       # mock_graph 연결 차수 — 클수록 교차로에 가까움

@dataclass
class DemandPoint:
    """ITS 링크 또는 도로 엣지에서 추출한 차량 수요점."""
    id: str
    lat: float
    lng: float
    vehicle_count: float  # 해당 위치의 추정 차량 대수


@dataclass
class PlacementResult:
    """최적화 결과 한 건."""
    time_period: str              # "peak" | "off_peak"
    network_mode: str
    node_type: str                # "bs" | "rsu"
    placed: list[dict]            # [{"id","lat","lng","degree"}]  배치된 후보 목록
    cost_initial: float           # warm-start 초기 비용 (ms, 가중 평균 지연)
    cost_final: float             # SA 수렴 후 비용
    improvement_pct: float        # (initial-final)/initial × 100
    uncovered_demand_pct: float   # 미커버 수요 비율 (0~100)
    n_candidates: int
    n_demand_points: int
    n_iterations: int


# ──────────────────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2.0 * math.asin(math.sqrt(min(a, 1.0)))


def _L_total_sa(distance_m: float, n_vehicles: float, network_mode: str) -> float:
    """M/M/1 포함 단방향 지연 (ms). 건물 차폐 없음(A_seg=0) — 배치 최적화용 근사."""
    p = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])
    L_base = p["L_base"]
    RSRP = p["P_tx"] - p["alpha"] - 10.0 * p["beta"] * math.log10(max(distance_m, 1.0))
    N_retx = p["N_max"] * max(0.0, min(1.0, (p["RSRP_thresh"] - RSRP) / p["RSRP_range"]))
    L_signal = N_retx * p["T_retx"]
    rho = min(n_vehicles / p["C_tech"], 0.99)
    L_queue = L_base * rho / (1.0 - rho)
    return L_base + L_signal + L_queue


def _coverage_radius(network_mode: str, node_type: str) -> float:
    if node_type == "rsu":
        return _RSU_COVERAGE_RADIUS_M.get(network_mode, 150.0)
    return _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])["coverage_radius_m"]


# ──────────────────────────────────────────────────────────────────────────────
# 후보·수요점 추출
# ──────────────────────────────────────────────────────────────────────────────

def build_candidates_from_graph(
    graph: dict,
    node_type: str = "bs",
) -> list[Candidate]:
    """mock_graph 노드에서 후보 집합을 생성한다.

    BS: 모든 노드 (최대 _MAX_CANDIDATES개 샘플)
    RSU: 차수(degree) >= 3 인 교차로 노드만 (더 엄격한 입지 조건)
    """
    nodes = graph.get("nodes", {})
    adjacency = graph.get("adjacency", {})
    candidates: list[Candidate] = []

    for nid, data in nodes.items():
        lat = data.get("lat")
        lng = data.get("lng")
        if lat is None or lng is None:
            continue
        degree = len(adjacency.get(nid, []))
        if node_type == "rsu" and degree < 3:
            continue
        candidates.append(Candidate(id=nid, lat=lat, lng=lng, degree=degree))

    # 후보가 너무 많으면 차수 내림차순 정렬 후 상위만 사용
    candidates.sort(key=lambda c: -c.degree)
    if len(candidates) > _MAX_CANDIDATES:
        candidates = candidates[:_MAX_CANDIDATES]

    return candidates


def build_demand_from_its(its_links: list[dict]) -> list[DemandPoint]:
    """ITS 링크 데이터를 수요점 목록으로 변환한다.

    각 링크의 geometry 포인트를 독립 수요점으로 취급하고,
    vehicle_count = congestion_score * _MAX_VEHICLES_PER_LINK 으로 추정한다.
    """
    demand: list[DemandPoint] = []
    for i, link in enumerate(its_links):
        score = float(link.get("congestion_score") or 0.0)
        if score <= 0:
            continue
        geometry = link.get("geometry") or []
        if not geometry:
            continue
        # 링크 중간점 하나만 사용(밀집 포인트 중복 방지)
        mid_pt = geometry[len(geometry) // 2]
        lat = mid_pt.get("lat")
        lng = mid_pt.get("lng")
        if lat is None or lng is None:
            continue
        demand.append(DemandPoint(
            id=f"its-{i}",
            lat=lat,
            lng=lng,
            vehicle_count=score * _MAX_VEHICLES_PER_LINK,
        ))
    return demand


def build_demand_from_graph(graph: dict) -> list[DemandPoint]:
    """ITS 없을 때 폴백: mock_graph 엣지 중점을 균일 수요점으로 사용."""
    nodes = graph.get("nodes", {})
    adjacency = graph.get("adjacency", {})
    seen: set[frozenset] = set()
    demand: list[DemandPoint] = []
    for nid, neighbors in adjacency.items():
        a = nodes.get(nid)
        if not a:
            continue
        for nb in neighbors:
            key = frozenset([nid, nb])
            if key in seen:
                continue
            seen.add(key)
            b = nodes.get(nb)
            if not b:
                continue
            mid_lat = (a["lat"] + b["lat"]) / 2.0
            mid_lng = (a["lng"] + b["lng"]) / 2.0
            demand.append(DemandPoint(
                id=f"edge-{nid}-{nb}",
                lat=mid_lat,
                lng=mid_lng,
                vehicle_count=5.0,  # 균일 기본 부하
            ))
    return demand


# ──────────────────────────────────────────────────────────────────────────────
# 비용 함수 (전체 / 부분)
# ──────────────────────────────────────────────────────────────────────────────

def _assign_demand(
    placement: list[Candidate],
    demand: list[DemandPoint],
    coverage_m: float,
) -> tuple[dict[str, list[int]], list[int]]:
    """각 수요점을 가장 가까운 배치 BS에 할당.

    Returns:
        assignment: {candidate_idx: [demand_point_indices]}
        uncovered: demand point indices not within any BS's coverage
    """
    assignment: dict[str, list[int]] = {str(i): [] for i in range(len(placement))}
    uncovered: list[int] = []
    for di, dp in enumerate(demand):
        best_dist = float("inf")
        best_ci: Optional[int] = None
        for ci, cand in enumerate(placement):
            d = _haversine_m(dp.lat, dp.lng, cand.lat, cand.lng)
            if d <= coverage_m and d < best_dist:
                best_dist = d
                best_ci = ci
        if best_ci is not None:
            assignment[str(best_ci)].append(di)
        else:
            uncovered.append(di)
    return assignment, uncovered


def _full_cost(
    placement: list[Candidate],
    demand: list[DemandPoint],
    network_mode: str,
    node_type: str,
) -> tuple[float, float]:
    """배치 전체의 가중평균 지연 비용 (ms) 와 미커버 수요 비율 을 반환한다.

    미커버 수요점은 _UNCOVERED_PENALTY_MS 를 적용해 비용에 포함한다.
    """
    coverage_m = _coverage_radius(network_mode, node_type)
    assignment, uncovered = _assign_demand(placement, demand, coverage_m)

    total_veh = sum(dp.vehicle_count for dp in demand) or 1.0
    weighted_sum = 0.0

    for ci, indices in assignment.items():
        if not indices:
            continue
        n_veh_bs = sum(demand[di].vehicle_count for di in indices)
        for di in indices:
            dp = demand[di]
            dist = _haversine_m(dp.lat, dp.lng, placement[int(ci)].lat, placement[int(ci)].lng)
            lat_ms = _L_total_sa(dist, n_veh_bs, network_mode)
            weighted_sum += dp.vehicle_count * lat_ms

    uncovered_veh = sum(demand[di].vehicle_count for di in uncovered)
    weighted_sum += uncovered_veh * _UNCOVERED_PENALTY_MS

    return weighted_sum / total_veh, uncovered_veh / total_veh * 100.0


# ──────────────────────────────────────────────────────────────────────────────
# 초기화 전략
# ──────────────────────────────────────────────────────────────────────────────

def greedy_forward_init(
    candidates: list[Candidate],
    N: int,
    demand: list[DemandPoint],
    network_mode: str,
    node_type: str,
) -> list[int]:
    """Greedy forward selection warm-start.

    빈 배치에서 시작해, 매 단계마다 비용을 가장 많이 낮추는 후보를 하나씩 추가한다.
    M/M/1 비선형식을 그대로 사용한다 (proxy greedy와 달리).
    """
    placed_idx: list[int] = []
    remaining = list(range(len(candidates)))

    for _ in range(min(N, len(candidates))):
        best_cost = float("inf")
        best_ci: Optional[int] = None
        for ci in remaining:
            trial = [candidates[i] for i in placed_idx] + [candidates[ci]]
            cost, _ = _full_cost(trial, demand, network_mode, node_type)
            if cost < best_cost:
                best_cost = cost
                best_ci = ci
        if best_ci is None:
            break
        placed_idx.append(best_ci)
        remaining.remove(best_ci)

    return placed_idx


def random_init(
    candidates: list[Candidate],
    N: int,
    rng: _rng_module.Random,
) -> list[int]:
    """N개를 후보에서 무작위 선택."""
    k = min(N, len(candidates))
    return rng.sample(range(len(candidates)), k)


# ──────────────────────────────────────────────────────────────────────────────
# SA 본체 + delta evaluation
# ──────────────────────────────────────────────────────────────────────────────

def _delta_cost(
    placement: list[Candidate],
    demand: list[DemandPoint],
    network_mode: str,
    node_type: str,
    out_ci: int,
    in_cand: Candidate,
) -> float:
    """Delta evaluation: swap placement[out_ci] ↔ in_cand 의 비용 변화량.

    coverage 반경 내 영향받는 수요점만 재계산한다.
    전체 재계산 대비 규모가 클수록 속도 이득이 크다.
    """
    coverage_m = _coverage_radius(network_mode, node_type)
    removed = placement[out_ci]

    # 영향받는 수요점: 제거된 BS 또는 추가될 BS의 커버리지 안에 있는 것들
    affected_di: set[int] = set()
    for di, dp in enumerate(demand):
        if (_haversine_m(dp.lat, dp.lng, removed.lat, removed.lng) <= coverage_m or
                _haversine_m(dp.lat, dp.lng, in_cand.lat, in_cand.lng) <= coverage_m):
            affected_di.add(di)

    if not affected_di:
        return 0.0  # swap이 어떤 수요점도 건드리지 않음

    # 영향권 수요점에 대해 현재 비용 계산 (old)
    def _partial_cost(cur_placement: list[Candidate]) -> float:
        sub_demand = [demand[di] for di in affected_di]
        sub_total_veh = sum(dp.vehicle_count for dp in sub_demand) or 1.0
        assignment, uncovered = _assign_demand(cur_placement, sub_demand, coverage_m)
        ws = 0.0
        for ci_str, indices in assignment.items():
            if not indices:
                continue
            n_veh_bs = sum(sub_demand[i].vehicle_count for i in indices)
            for i in indices:
                dp = sub_demand[i]
                dist = _haversine_m(dp.lat, dp.lng, cur_placement[int(ci_str)].lat, cur_placement[int(ci_str)].lng)
                ws += dp.vehicle_count * _L_total_sa(dist, n_veh_bs, network_mode)
        ws += sum(sub_demand[i].vehicle_count for i in uncovered) * _UNCOVERED_PENALTY_MS
        return ws / sub_total_veh

    old_partial = _partial_cost(placement)
    new_placement = placement[:out_ci] + [in_cand] + placement[out_ci + 1:]
    new_partial = _partial_cost(new_placement)
    return new_partial - old_partial


def sa_run(
    candidates: list[Candidate],
    demand: list[DemandPoint],
    network_mode: str,
    node_type: str,
    init_indices: list[int],
    T0: float = 30.0,
    alpha: float = 0.995,
    n_iter: int = 2000,
    rng: Optional[_rng_module.Random] = None,
) -> tuple[list[int], float, int]:
    """SA 단일 궤적.

    Returns:
        best_indices: 최종 배치 후보 인덱스 목록
        best_cost: 최종 비용 (ms)
        n_accepted: 수락된 이동 횟수
    """
    if rng is None:
        rng = _rng_module.Random()

    N = len(init_indices)
    placed_set = set(init_indices)
    current = list(init_indices)
    placement = [candidates[i] for i in current]
    current_cost, _ = _full_cost(placement, demand, network_mode, node_type)

    best_indices = list(current)
    best_cost = current_cost
    T = T0
    n_accepted = 0

    not_placed = [i for i in range(len(candidates)) if i not in placed_set]

    for step in range(n_iter):
        if not not_placed:
            break
        # swap: 배치에서 하나 제거, 미배치에서 하나 추가
        out_pos = rng.randrange(N)
        out_ci = current[out_pos]
        in_ci = rng.choice(not_placed)
        in_cand = candidates[in_ci]

        delta = _delta_cost(placement, demand, network_mode, node_type, out_pos, in_cand)

        if delta < 0 or rng.random() < math.exp(-delta / max(T, 1e-6)):
            # 수락
            not_placed.remove(in_ci)
            not_placed.append(out_ci)
            placed_set.discard(out_ci)
            placed_set.add(in_ci)
            current[out_pos] = in_ci
            placement[out_pos] = in_cand
            current_cost += delta
            n_accepted += 1
            if current_cost < best_cost:
                best_cost = current_cost
                best_indices = list(current)

        T *= alpha

    return best_indices, best_cost, n_accepted


# ──────────────────────────────────────────────────────────────────────────────
# Multi-start 진입점
# ──────────────────────────────────────────────────────────────────────────────

def multi_start_optimize(
    candidates: list[Candidate],
    demand: list[DemandPoint],
    network_mode: str,
    node_type: str,
    N: int,
    n_greedy: int = 2,
    n_random: int = 2,
    sa_iter: int = 2000,
    T0: float = 30.0,
    alpha: float = 0.995,
    seed: Optional[int] = None,
) -> tuple[list[int], float, float, float]:
    """multi-start SA (greedy + random 병렬 초기화).

    Returns:
        best_indices, best_cost, initial_cost_of_winner, uncovered_pct
    """
    if len(candidates) < N:
        N = len(candidates)

    rng = _rng_module.Random(seed)
    all_results: list[tuple[list[int], float, float]] = []  # (indices, final_cost, initial_cost)

    # ── greedy warm-start 궤적들 ──────────────────────────────────────────────
    greedy_idx = greedy_forward_init(candidates, N, demand, network_mode, node_type)
    greedy_cost_init, _ = _full_cost([candidates[i] for i in greedy_idx], demand, network_mode, node_type)
    for g in range(n_greedy):
        r = _rng_module.Random(rng.randint(0, 99999))
        final_idx, final_cost, _ = sa_run(
            candidates, demand, network_mode, node_type,
            greedy_idx, T0=T0, alpha=alpha, n_iter=sa_iter, rng=r,
        )
        all_results.append((final_idx, final_cost, greedy_cost_init))

    # ── random 궤적들 ─────────────────────────────────────────────────────────
    for _ in range(n_random):
        rand_idx = random_init(candidates, N, rng)
        rand_cost_init, _ = _full_cost([candidates[i] for i in rand_idx], demand, network_mode, node_type)
        r = _rng_module.Random(rng.randint(0, 99999))
        final_idx, final_cost, _ = sa_run(
            candidates, demand, network_mode, node_type,
            rand_idx, T0=T0, alpha=alpha, n_iter=sa_iter, rng=r,
        )
        all_results.append((final_idx, final_cost, rand_cost_init))

    # ── 최선 선택 ─────────────────────────────────────────────────────────────
    best_indices, best_cost, init_cost = min(all_results, key=lambda x: x[1])
    _, uncovered_pct = _full_cost([candidates[i] for i in best_indices], demand, network_mode, node_type)
    return best_indices, best_cost, init_cost, uncovered_pct


# ──────────────────────────────────────────────────────────────────────────────
# 공개 최적화 함수
# ──────────────────────────────────────────────────────────────────────────────

def optimize_placement(
    *,
    graph: dict,
    its_links: list[dict],
    N: int,
    network_mode: str,
    node_type: str = "bs",
    time_period: str = "peak",
    n_greedy: int = 2,
    n_random: int = 2,
    sa_iter: int = 2000,
    seed: Optional[int] = None,
) -> PlacementResult:
    """배치 최적화 최상위 진입점.

    graph: mock_graph (nodes/adjacency)
    its_links: TRAFFIC_FUSION_ENGINE.current_traffic(time_period)["links"]
    N: 설치할 기지국 수
    """
    candidates = build_candidates_from_graph(graph, node_type=node_type)
    if not candidates:
        return PlacementResult(
            time_period=time_period, network_mode=network_mode, node_type=node_type,
            placed=[], cost_initial=0.0, cost_final=0.0, improvement_pct=0.0,
            uncovered_demand_pct=100.0, n_candidates=0, n_demand_points=0, n_iterations=0,
        )

    demand = build_demand_from_its(its_links)
    if not demand:
        demand = build_demand_from_graph(graph)

    best_indices, best_cost, init_cost, uncovered_pct = multi_start_optimize(
        candidates=candidates,
        demand=demand,
        network_mode=network_mode,
        node_type=node_type,
        N=N,
        n_greedy=n_greedy,
        n_random=n_random,
        sa_iter=sa_iter,
        seed=seed,
    )

    improvement_pct = ((init_cost - best_cost) / max(init_cost, 1e-6)) * 100.0

    placed = [
        {"id": candidates[i].id, "lat": candidates[i].lat, "lng": candidates[i].lng, "degree": candidates[i].degree}
        for i in best_indices
    ]

    return PlacementResult(
        time_period=time_period,
        network_mode=network_mode,
        node_type=node_type,
        placed=placed,
        cost_initial=round(init_cost, 3),
        cost_final=round(best_cost, 3),
        improvement_pct=round(improvement_pct, 2),
        uncovered_demand_pct=round(uncovered_pct, 2),
        n_candidates=len(candidates),
        n_demand_points=len(demand),
        n_iterations=sa_iter * (n_greedy + n_random),
    )
