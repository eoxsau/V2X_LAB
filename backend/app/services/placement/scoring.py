"""배치 채점 — 배치 하나를 점수 하나로 (배치설계 v2 §5).

    배치 → 연결(best-SINR) → 머릿수 → ρ → 차량별 지연 → 가중 합산

load-coupling **OFF** 기준이라 되먹임 고리가 없다. 한 방향으로 한 번만 훑으면 끝난다
(§5-2). ON이면 "혼잡하니 딴 데로 도망 → 머릿수 변화 → 다시 혼잡 변화"가 물고 돌아
고정점 반복이 필요하지만, OFF에서는 ρ가 오직 머릿수로만 정해져 그 고리가 끊겨 있다.

⚠️ 이 모듈은 `latency.formula_v31`을 **직접** 쓴다 — 별도 근사식을 두지 않는다.
   2026-07-27 이전 구현(`sa_placement._L_total_sa`)은 런타임과 다른 구식 근사식이었고,
   같은 조건에서 0.39~16.8배 어긋났다. 더 나쁜 건 **방향이 뒤집혔다**는 점이다:
       100m  : 근사 15.31 ms vs 실제  2.47 ms  (6.2배 과대)
       1000m : 근사 16.75 ms vs 실제 42.92 ms  (0.39배 과소)
   즉 근사식은 거리에 거의 무감각(15.3→16.8, +9%)한데 실제 모델은 거리가 지배한다
   (2.5→42.9, 17배). 배치 최적화의 본질이 거리인데 그걸 못 보는 목적함수였다.
   → **최적화 대상과 측정 대상은 같아야 한다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from app.services.latency import formula_v31 as f31

# 어떤 스테이션의 커버 안에도 안 드는 수요에 물리는 값.
# outage와 같은 유한 페널티를 쓴다 — ∞는 비용 차분을 깨고, 작은 값은 음영지역을
# 안 무서워하게 만든다(설계 v2 §2-3).
UNCOVERED_PENALTY_MS = f31.L_OUTAGE_MS


@dataclass(frozen=True)
class Station:
    """배치된 유닛 하나. BS와 RSU가 같은 자료형을 쓰되 `node_type`으로 갈린다."""
    id: str
    lat: float
    lng: float
    node_type: str          # "bs" | "rsu"


@dataclass(frozen=True)
class DemandPoint:
    """수요 지점 하나 — 도로 조각의 중점과 그 위 차량 수.

    차량 한 대씩이 아니라 **조각 단위**로 뭉치는 이유(진행문서 §5-C 논의):
      * A_seg 캐시 키가 좌표라, 지점이 고정이어야 캐시가 재사용된다. 차량 위치 스냅샷은
        실행마다 달라져 매번 3D ray-casting을 다시 해야 한다.
      * edgeData의 대수는 이미 구간 시간평균이라 순간 스냅샷보다 노이즈가 적다.
    """
    id: str
    lat: float
    lng: float
    vehicle_count: float


@dataclass
class ScoreResult:
    cost_ms: float                       # 수요 가중 평균 지연 (이 배치의 점수)
    uncovered_pct: float                 # 미커버 수요 비율 (0~100)
    outage_pct: float                    # outage 수요 비율 (0~100)
    station_load: dict[str, float] = field(default_factory=dict)   # station_id → 접속 대수
    assignment: dict[str, Optional[str]] = field(default_factory=dict)  # demand_id → station_id


# A_seg 조회 콜백: (demand_id, station_id) → dB. 없으면 0(차폐 무시).
ASegLookup = Callable[[str, str], float]


def _no_a_seg(_d: str, _s: str) -> float:
    return 0.0


def _dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """평면 근사 거리. 구역이 수 km라 haversine과 차이가 무시할 만하고 훨씬 빠르다."""
    import math
    dlat = (lat2 - lat1) * 111_320.0
    dlng = (lng2 - lng1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) * 0.5))
    return math.hypot(dlat, dlng)


def score_placement(
    stations: Sequence[Station],
    demand: Sequence[DemandPoint],
    tech: str = "5G",
    a_seg: ASegLookup = _no_a_seg,
    *,
    want_detail: bool = False,
) -> ScoreResult:
    """배치 하나를 채점한다 (§5-2의 4단계를 그대로).

    (1) 연결: 각 수요점이 **SINR 최대** 스테이션에 붙는다. 큐는 이 단계에서 안 본다.
        타입 간 비교는 formula_v31이 타입별 α로 정규화해 처리한다(설계 §3-2).
    (2) 머릿수 → ρ: 스테이션별 접속 대수 ÷ 자기 C_tech.
    (3) 차량별 지연: L_base + L_transmission(그 지점 SINR) + L_queue(붙은 스테이션 ρ).
    (4) 수요 가중 합산.

    ⚠️ (1)과 (3)에서 **같은 SINR**을 써야 한다. 연결은 신호로 정하고 지연은 다른 값으로
       계산하면 "왜 이 스테이션에 붙었는지"와 "얼마나 걸리는지"가 어긋난다.
    """
    total_veh = sum(d.vehicle_count for d in demand) or 1.0
    if not stations:
        return ScoreResult(cost_ms=UNCOVERED_PENALTY_MS, uncovered_pct=100.0, outage_pct=100.0)

    # ── (1) 연결 + 각 수요점의 SINR·거리 기록 ────────────────────────────────
    best_station: list[Optional[int]] = [None] * len(demand)
    best_sinr: list[float] = [0.0] * len(demand)
    best_dist: list[float] = [0.0] * len(demand)
    load: list[float] = [0.0] * len(stations)

    cover = [f31.resolve_coverage_radius(tech, s.node_type) for s in stations]

    for di, dp in enumerate(demand):
        top_si, top_sinr, top_d = None, float("-inf"), 0.0
        for si, st in enumerate(stations):
            d = _dist_m(dp.lat, dp.lng, st.lat, st.lng)
            if d > cover[si]:
                continue                       # 커버 밖은 후보에서 제외
            sinr = f31.compute_sinr_db(tech, d, a_seg(dp.id, st.id), st.node_type)
            if sinr > top_sinr:
                top_si, top_sinr, top_d = si, sinr, d
        best_station[di] = top_si
        best_sinr[di] = top_sinr
        best_dist[di] = top_d
        if top_si is not None:
            # ── (2) 머릿수 누적
            load[top_si] += dp.vehicle_count

    # ── (3)(4) 지연 계산 + 가중 합산 ─────────────────────────────────────────
    weighted = 0.0
    uncovered_veh = 0.0
    outage_veh = 0.0
    for di, dp in enumerate(demand):
        si = best_station[di]
        if si is None:
            uncovered_veh += dp.vehicle_count
            weighted += dp.vehicle_count * UNCOVERED_PENALTY_MS
            continue
        st = stations[si]
        r = f31.compute_latency(tech, best_dist[di], load[si],
                                a_seg(dp.id, st.id), node_type=st.node_type)
        if r["outage"]:
            outage_veh += dp.vehicle_count
        weighted += dp.vehicle_count * r["l_total_ms"]

    res = ScoreResult(
        cost_ms=weighted / total_veh,
        uncovered_pct=uncovered_veh / total_veh * 100.0,
        outage_pct=outage_veh / total_veh * 100.0,
    )
    if want_detail:
        res.station_load = {stations[i].id: load[i] for i in range(len(stations))}
        res.assignment = {
            demand[di].id: (stations[si].id if si is not None else None)
            for di, si in enumerate(best_station)
        }
    return res
