"""N\\* 산정 — 구역마다 "정체가 생겼다 풀리는" 총 통행량을 자동으로 정한다 (문서 §5-C).

UI의 "기준 교통량의 몇 배(n)" 노브가 의미를 가지려면 기준값 N\\*가 구역마다 자동으로
나와야 한다. 스윕은 한 점당 파이프라인+시뮬 풀 실행이라 구역이 바뀔 때마다 돌릴 수 없다.

왜 단순 비례가 안 되나 (2026-07-27 실측):
    영등포 26.0 lane-km / 안양·의왕 23.5 lane-km 로 도로 규모는 비슷한데,
    같은 5,000통행에서 피크 동시주행이 503대 vs 68대로 **7배** 갈렸다.
    차이를 만드는 건 도로 길이가 아니라 **통행거리**(= 차가 도로 위에 머무는 시간)다.

핵심 아이디어 — 자유류 Little's Law로 시드, 시뮬로 보정:

    V_peak = q_peak × T_ff
        q_peak : 피크 슬라이스의 시간당 출발률 = N* × r_peak
        T_ff   : 평균 자유류 통행시간 — duarouter가 `--write-costs`로 그냥 준다

    자유류 값이라 **시뮬레이션 0회**로 얻고, 통행량 수준과 무관하게 일정하다(전부 선형).
    검산: 안양·의왕(한산)은 예측 ~60대 vs 실측 68대로 잘 맞고, 영등포(정체)는 예측 73대
    vs 실측 503대 — 즉 **자유류 모델은 "아직 안 막힐 때"만 맞고, 그 이탈이 시작되는
    지점이 곧 N\\***다.

한계 (정직하게):
    * `K_TARGET`은 영등포 **한 구역** 앵커다. 다른 성격의 구역(간선 많은 도심 등)에서는
      다시 봐야 한다. 영등포 net엔 primary/secondary가 아예 없다(residential+tertiary뿐).
    * 2단계 보정은 확률적이다 — 교착 발생이 random이라 경계 근처는 run-to-run 변동이 있다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .assignment import read_net, routable_edges
from .pipeline import DEFAULT_CELL_M, build_demand_context, generate_demand
from .simulation import DEFAULT_STEP_LENGTH, run_simulation

# 시드용 목표 자유류 밀도 (veh / lane-km).
#
# 교과서 임계밀도(~25)보다 한 자릿수 낮다. 정체가 **상위 10% 엣지에 75% 몰리기** 때문에
# 네트워크 평균으로는 훨씬 낮은 값에서 이미 병목이 터진다(§5-B).
#
# ⚠️ **보편 상수가 아니다** (2026-07-27, 2구역 실측). 실제 전이가 일어난 자유류 밀도:
#     영등포   `0baecbba`  3.52   (통과최대 6,244 / 26.0 lane-km)
#     안양·의왕 `1b5adb59`  2.49   (통과최대 6,135 / 23.5 lane-km)
# 1.4배 차이 난다 — 네트워크 위상(병목 집중도)에 달렸다. 그래서 이 값은 **정답이 아니라
# 이분 탐색의 출발점**일 뿐이고, 최종값은 항상 시뮬이 정한다.
#
# 낮은 쪽(2.8)에 맞춰둔 이유: 첫 실행이 'low'로 빠지면 12~20초에 끝나지만 교착이면
# 130~230초가 걸린다(실측). 어차피 위로 올려가며 찾으므로 낮게 시작하는 편이 싸다.
K_TARGET = 2.8

# 보정 판정 기준 — 목표는 딱 하나, **정체가 생겼다 풀리는 가장 높은 수준**(v2 §5-1).
#
# ⚠️ 2026-07-27 실측으로 한 번 완화했다. 처음엔 "정지 비율 0.40~0.85 + 텔포 3% 이하"로
# 잡았는데 안양·의왕에서 **밴드가 사실상 비어 있었다**: 5,901통행 정지 7% → 6,421통행 정지 82%로
# 건너뛴다(그 사이가 없다). 이 네트워크들의 상전이는 거의 불연속이다.
# 게다가 6,421은 **잔류 0으로 완전히 해소**됐는데 텔포 3.5%만으로 탈락했다 —
# 정체가 심하면 5분 넘게 서 있는 차가 자연히 생기므로 텔포 3%는 정체 구간에서 너무 빡빡하다.
MIN_HALTING_RATIO = 0.40     # 이 아래면 "정체가 안 생겼다" → 더 올린다
MAX_LEFTOVER_RATIO = 0.05    # 종료시 잔류 / 도착 — 넘으면 "안 풀렸다" → 내린다 (**핵심 기준**)
MAX_TELEPORT_RATIO = 0.10    # 이 위면 교착이 시뮬을 지배 → 수치를 못 믿는다
WARN_TELEPORT_RATIO = 0.03   # 이 위는 경고만 (탈락시키지 않음)
HIGH_HALTING_NOTE = 0.85     # 이 위는 "매우 빡빡" 표시만 — 해소되면 유효한 운영점이다

# 안전 계수 — **경계가 확률적이라 통과 최댓값을 그대로 쓰면 안 된다** (2026-07-27 실측).
# 영등포에서 같은 조건인데도 5,500 실패 / 5,742 통과 / 5,993 통과 / 6,244 실패로 뒤섞인다.
# 매 실행마다 OD 추첨이 달라 교착 발생이 코인토스이기 때문. 통과 최댓값에 그대로 앉으면
# 다음 실행에서 교착될 확률이 절반쯤 된다. 조금 낮게 잡는 쪽이 훨씬 싸다 —
# 너무 낮으면 정체가 덜할 뿐이지만, 너무 높으면 시뮬 자체가 무의미해진다.
DEFAULT_SAFETY_FACTOR = 0.9

# 1라운드에서 굴려볼 레벨 — 시드 대비 비율. **확실히 아래에서 출발한다**(2026-07-28).
#
# 시드 공식의 T_ff는 *자유류* 통행시간이라, 정체가 실제로 생기는 수준에서는 통행시간이
# 3~4배로 늘어 밀도가 그만큼 과소평가된다. 즉 시드는 항상 실제 N*보다 크게 나온다.
# 안양 실측: 시드 23,907 → 20,321통행에서 순간이동이 도착 대수를 넘는 완전 교착.
# 실제 N*는 시드의 0.2~0.5배 어딘가다. 빗나가더라도 'low' 실행은 수십 초에 끝나지만
# 'high'(교착) 실행은 수십 분이 걸리므로, 낮게 시작하는 쪽이 압도적으로 싸다.
FIRST_ROUND_FRACS = (0.20, 0.35, 0.50, 0.70)

# 조기 종료가 성급해지지 않게 하는 하한 — 도착이 이보다 적으면 순간이동 비율을 안 본다.
# (초반 몇십 대 구간에서는 텔포 2~3건만으로도 비율이 튄다)
MIN_ARRIVED_FOR_ABORT = 200

# 탐색 종료 폭 — (hi − lo)가 lo의 이 비율 아래로 좁혀지면 멈춘다.
#
# 예전 5%는 **노이즈보다 작은 값을 쫓고 있었다.** 위 DEFAULT_SAFETY_FACTOR 주석의 실측이
# 그대로 근거다 — 같은 조건에서 5,500 실패 / 5,742 통과 / 5,993 통과 / 6,244 실패로
# 뒤섞인다(경계가 확률적이라 run-to-run 변동이 ±10% 수준). 그보다 좁게 좁히려고
# 라운드를 하나 더 쓰는 건 순수한 낭비다. 어차피 뒤에 안전계수 0.9를 곱한다.
DEFAULT_TOLERANCE = 0.15

# N* 캐시를 재사용해도 되는 도로망 변화 한계 (차로연장 상대차). 근거는 `_nstar_cache_key`.
# 같은 bbox를 다시 그렸을 때 netconvert 흔들림은 1% 안이고, N* 자체의 run-to-run 변동이
# ±10%라 5%는 그 노이즈 안에 묻힌다. 이보다 크게 달라졌으면 도로가 실제로 바뀐 것으로 본다.
LANE_KM_TOLERANCE = 0.05


def _log_noop(_: str) -> None:
    pass


# ── 재료 ──────────────────────────────────────────────────────────────────────

def network_lane_km(net_or_file, vclass: str = "passenger",
                    largest_component_only: bool = True,
                    bbox: Optional[tuple[float, float, float, float]] = None) -> float:
    """통행 가능한 도로의 **차로연장**(lane-km).

    엣지 연장이 아니라 차로 수를 곱한 값이다 — 2차로 도로는 차를 두 배 담는다.
    TAZ와 같은 기준(최대 SCC)으로 재야 실제로 쓰이는 도로만 센다.

    bbox : (minlng, minlat, maxlng, maxlat). 주면 **이 범위에 걸친 도로만** 센다.
        수요를 그린 구역으로 좁혔으면(pipeline.generate_demand의 bbox) 여기도 같이 좁혀야
        한다 — 수요가 안 깔리는 도로까지 분모에 넣으면 시드가 그만큼 부풀기 때문이다.
        안양 실측: net 전체 211 lane-km vs 그린 구역에 걸친 것만 177 lane-km.
    """
    net = read_net(str(net_or_file)) if isinstance(net_or_file, (str, Path)) else net_or_file
    keep = routable_edges(net, vclass) if largest_component_only else None
    total = 0.0
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        if keep is not None and e.getID() not in keep:
            continue
        if bbox is not None and not _edge_touches_bbox(net, e, bbox):
            continue
        total += e.getLength() * max(e.getLaneNumber(), 1)
    return total / 1000.0


def _edge_touches_bbox(net, edge, bbox: tuple[float, float, float, float]) -> bool:
    """엣지 형상이 bbox에 조금이라도 걸치는가.

    '완전히 안'이 아니라 '걸치면 포함'이다 — 구역 경계를 가로지르는 도로는 구역 안쪽
    절반이 실제로 쓰이므로 빼면 오히려 과소평가된다.
    """
    minlng, minlat, maxlng, maxlat = bbox
    try:
        shape = edge.getShape()
    except Exception:
        return True
    for x, y in shape or ():
        try:
            lng, lat = net.convertXY2LonLat(x, y)
        except Exception:
            return True
        if minlat <= lat <= maxlat and minlng <= lng <= maxlng:
            return True
    return False


def peak_rate_per_hour(time_profile: Optional[Sequence[tuple[float, float, float]]]) -> float:
    """시간 프로파일에서 **가장 붐비는 슬라이스의 시간당 출발률 비율**.

    07~09시 15분 8구간이면 피크 슬라이스가 창 통행의 13.28%를 15분에 쏟아내므로
    시간당 환산 0.531. `q_peak = N* × 이 값`.
    """
    if not time_profile:
        return 1.0
    total = sum(s for _, _, s in time_profile) or 1.0
    return max((s / total) / max(e - b, 1e-9) for b, e, s in time_profile)


# ── 결과 ──────────────────────────────────────────────────────────────────────

@dataclass
class NStarResult:
    n_star: float
    seed: float
    lane_km: float
    freeflow_travel_s: float
    peak_rate_per_h: float
    converged: bool = False
    iterations: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def k_freeflow(self) -> float:
        """채택된 N*에서의 자유류 밀도(veh/lane-km). K_TARGET과 비교용."""
        if not self.lane_km:
            return 0.0
        v = self.n_star * self.peak_rate_per_h * (self.freeflow_travel_s / 3600.0)
        return v / self.lane_km


# ── 1단계: 해석적 시드 (시뮬 0회) ─────────────────────────────────────────────

def estimate_nstar_seed(
    net_file: str,
    out_dir: str,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
    *,
    ref_trips: float = 2000.0,
    k_target: float = K_TARGET,
    log: Optional[Callable[[str], None]] = None,
    **demand_kwargs,
) -> tuple[float, dict]:
    """시뮬레이션 없이 N\\* 초기값을 계산한다.

        N*_seed = k_target × L_lane / (r_peak × T_ff)

    ref_trips : T_ff를 재기 위한 기준 통행량. 자유류는 선형이라 어떤 값이든 같은 T_ff가
        나오지만, **OD 라인 수보다 크게** 잡아야 od2trips 반올림 편향을 피한다(§7).
        기본 2,000은 영등포(3,422라인) 기준으로도 T_ff 측정엔 충분하다 —
        T_ff는 개별 경로의 평균이라 편향된 몇십 대가 섞여도 흔들리지 않는다.

    Returns: (seed, info dict)
    """
    log = log or _log_noop
    # 수요를 그린 구역으로 좁혔으면 분모(lane_km)도 같이 좁힌다 — 안 그러면 시드가 부푼다
    lane_km = _lane_km(net_file, demand_kwargs.get("bbox"), log)
    r_peak = peak_rate_per_hour(time_profile)

    d = generate_demand(net_file=net_file, out_dir=out_dir, total_trips=ref_trips,
                        time_profile=time_profile, prefix="nstar_ref", **demand_kwargs)
    t_ff_s = d.stats.get("freeflow_travel_s", 0.0)
    if t_ff_s <= 0:
        raise RuntimeError("자유류 통행시간을 얻지 못했습니다 — duarouter --write-costs 확인")

    seed = k_target * lane_km / (r_peak * (t_ff_s / 3600.0))
    info = {
        "lane_km": round(lane_km, 2),
        "peak_rate_per_h": round(r_peak, 4),
        "freeflow_travel_s": round(t_ff_s, 2),
        "freeflow_route_m": d.stats.get("freeflow_route_m"),
        "k_target": k_target,
        "n_od_lines": d.stats.get("n_od_lines"),
        "seed": round(seed),
    }
    log(f"시드 {seed:.0f}통행  (차로연장 {lane_km:.1f} lane-km, 자유류 {t_ff_s:.1f}초, "
        f"피크율 {r_peak:.3f}/h, k_target {k_target})")
    if seed < (d.stats.get("n_od_lines") or 0):
        log(f"⚠️ 시드가 OD 라인 수({d.stats.get('n_od_lines')})보다 작습니다 — "
            f"od2trips 반올림 편향이 커집니다(§7).")
    return seed, info


# ── 2단계: γ 보정 (시뮬 반복) ─────────────────────────────────────────────────

def _verdict(sim) -> str:
    """시뮬 1회 → 'low' | 'ok' | 'high'.

    판정 순서가 중요하다: **해소 여부가 최우선**이다. 안 풀렸으면 정지 비율이 아무리
    좋아 보여도 너무 높은 것이다(교착이 지표를 부풀린다).
    반대로 **풀리기만 하면 정지 비율이 아무리 높아도 유효한 운영점**이다 —
    우리가 찾는 건 "생겼다 풀리는 가장 높은 수준"이지 적당히 막히는 지점이 아니다.
    """
    # 교착이 확정돼 중간에 끊은 실행 — 끝까지 굴려도 결론은 'high'다(simulation.py §조기 종료).
    if getattr(sim, "gridlocked", False):
        return "high"
    arrived = sim.stats.get("arrived", 0)
    leftover = sim.stats.get("still_running_at_end", 0)
    teleports = sim.stats.get("teleports", 0)
    if leftover > max(arrived * MAX_LEFTOVER_RATIO, 10):
        return "high"
    if arrived and teleports > arrived * MAX_TELEPORT_RATIO:
        return "high"
    if sim.congestion_ratio < MIN_HALTING_RATIO:
        return "low"
    return "ok"


def _lane_km(net_file, bbox, log) -> float:
    """그린 구역에 걸치는 lane-km. 0이 나오면 net 전체로 폴백한다.

    ⚠️ bbox를 넓혀서 재면 안 된다 (2026-07-28 회귀). `_edge_touches_bbox`는 형상점이
    **하나라도** 들어오면 포함시키므로, 구역을 300m만 넓혀도 구역을 스치는 긴 엣지가 전부
    걸려 필터가 무의미해진다. 안양 실측: 원본 139.9 → +300m 확장 211.0(= net 전체와 동일).
    그러면 시드가 51% 부푼다.

    0 폴백이 필요한 이유: 구역이 도로를 하나도 안 품으면 lane_km=0 → 시드 0 → N* 0 →
    od2trips "No vehicles loaded"로 죽는데, 원인이 어디에도 안 드러난다. 애초에 그런 구역은
    `MIN_SETUP_AREA_KM2`가 막지만, 도로가 없는 산·바다 구역은 크기와 무관하게 나올 수 있다.
    """
    lane_km = network_lane_km(net_file, bbox=bbox)
    if lane_km > 0 or bbox is None:
        return lane_km
    lane_km = network_lane_km(net_file)
    log(f"⚠️ 그린 구역에 걸치는 도로가 없어 net 전체로 잽니다 ({lane_km:.1f} lane-km) — "
        f"구역 안에 도로가 있는지 확인하세요.")
    return lane_km


def _gridlock_abort(snap: dict) -> Optional[str]:
    """돌아가는 중인 실행이 '이미 탈락'인지 판정. `run_simulation(abort_check=...)`에 넘긴다.

    순간이동 비율이 `MAX_TELEPORT_RATIO`를 넘으면 `_verdict`가 어차피 'high'를 주므로,
    끝까지 굴릴 이유가 없다. `MIN_ARRIVED_FOR_ABORT`는 초반 표본이 적을 때 몇 대의
    순간이동으로 성급히 끊는 것을 막는 하한이다.
    """
    arrived = snap.get("arrived") or snap.get("ended") or 0
    teleports = snap.get("teleports", 0)
    if arrived >= MIN_ARRIVED_FOR_ABORT and teleports > arrived * MAX_TELEPORT_RATIO:
        return (f"gridlock: t={snap['time'] / 3600:.2f}h에 순간이동 {teleports} > "
                f"도착 {arrived}×{MAX_TELEPORT_RATIO}")
    return None


def calibrate_nstar(
    net_file: str,
    out_dir: str,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
    *,
    seed: Optional[float] = None,
    k_target: float = K_TARGET,
    max_rounds: int = 3,
    n_parallel: Optional[int] = None,
    first_round_fracs: Sequence[float] = FIRST_ROUND_FRACS,
    lane_km: Optional[float] = None,
    up_factor: float = 1.35,
    down_factor: float = 0.7,
    tolerance: float = DEFAULT_TOLERANCE,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    step_length: float = DEFAULT_STEP_LENGTH,
    log: Optional[Callable[[str], None]] = None,
    **demand_kwargs,
) -> NStarResult:
    """"생겼다 풀린다" 구간을 **라운드마다 여러 레벨을 동시에** 굴려 좁힌다.

    2026-07-28 이전에는 시드×0.85에서 시작해 한 번에 하나씩 이분했다. 두 가지가 틀렸다:

    1. **시드는 원리적으로 과대추정이다.** 시드 공식의 T_ff는 *자유류* 통행시간인데
       실제 정체 상태의 통행시간은 그 3~4배다. 그래서 시드×0.85는 "살짝 아래"가 아니라
       교착 구간 한복판이었다. 주석엔 "아래에서 위로 브래킷한다"고 적혀 있었지만 실제
       동작은 정반대였고, 가장 비싼 실행(교착)만 골라 밟았다.
       → `first_round_fracs`로 **확실히 아래에서** 출발한다. 빗나가도 'low' 실행은 빠르다.

    2. **순차 이분은 코어를 1개만 쓴다.** 12코어 기기에서 6회를 줄세워 돌렸다.
       → 라운드당 `n_parallel`개를 동시에 굴린다. 각 실행은 별도 sumo 프로세스라
         선형에 가깝게 확장된다(SUMO `--threads`보다 훨씬 잘 붙는다).

    실측(안양 5.5km²): 3~5시간 → 5~10분.

    Returns: NStarResult — `n_star`는 **통과한 것 중 가장 높은 값** × `safety_factor`.
        하나도 통과하지 못하면 마지막 'low'(없으면 시드)를 돌려주고 `converged=False`.
    """
    log = log or _log_noop
    # 호출부(cached_nstar)가 캐시 검증용으로 이미 쟀으면 그 값을 쓴다 — net 재파싱 수 초 절약
    if lane_km is None:
        lane_km = _lane_km(net_file, demand_kwargs.get("bbox"), log)
    r_peak = peak_rate_per_hour(time_profile)
    workers = n_parallel or max(1, min(4, (os.cpu_count() or 4) // 2))

    # seed를 직접 넘겨도 T_ff는 계산한다 — 진단(k_freeflow)에 필요하고, 시뮬 없이 ~15초면 된다.
    seed_supplied = seed is not None
    computed_seed, info = estimate_nstar_seed(net_file, out_dir, time_profile,
                                              k_target=k_target, log=log, **demand_kwargs)
    if not seed_supplied:
        seed = computed_seed
    else:
        log(f"시드 지정값 {seed:.0f} 사용 (계산값 {computed_seed:.0f})")

    # 통행량-무관 앞단(net·건물·존·radiation·TAZ)을 **한 번만** 만들어 모든 레벨이 공유한다.
    # 없으면 레벨마다 처음부터 다시 하고, 4개 병렬에서는 GIL 경합으로 오히려 더 느려진다
    # (안양 실측: 레벨당 13초 → 4병렬 2분 15초 → 공유 후 레벨당 2.6~3.9초).
    #
    # 격자 셀 크기도 여기서 함께 정해진다 — 기준은 **가장 낮은 레벨**(왜곡은 통행이 적을수록
    # 심하다). 한 번 정하면 모든 레벨이 같은 값을 쓴다. 레벨마다 다르면 수요 분포가 달라져
    # 레벨 간 비교가 성립하지 않는다(pipeline.fit_cell_size 주석).
    if demand_kwargs.get("context") is None:
        t_ctx = time.time()
        demand_kwargs["context"] = build_demand_context(
            net_file,
            cell_size_m=demand_kwargs.get("cell_size_m"),
            design_trips=seed * min(first_round_fracs),
            n_slices=len(time_profile) if time_profile else 1,
            bbox=demand_kwargs.get("bbox"),
            log=log,
        )
        log(f"수요 앞단 준비 완료 — 이후 라운드가 공유합니다 ({time.time() - t_ctx:.1f}s)")

    ctx = demand_kwargs["context"]
    demand_kwargs.pop("cell_size_m", None)      # context가 이긴다(중복 전달 방지)
    if abs(ctx.cell_size_m - DEFAULT_CELL_M) > 1.0:
        # 셀이 바뀌면 존이 달라져 **통행 거리 분포가 달라지고**, 따라서 T_ff도 달라진다.
        # 시드는 T_ff로 계산되므로 여기서 다시 재지 않으면 시드·진단값(n_od_lines,
        # freeflow_travel_s)이 실제 레벨이 쓰는 분포와 어긋난다
        # (2026-07-28: 300m로 잰 T_ff를 490m 실행 결과에 붙여 보고하고 있었다).
        computed_seed, info = estimate_nstar_seed(
            net_file, out_dir, time_profile, k_target=k_target, log=log, **demand_kwargs)
        if not seed_supplied:
            log(f"셀 확정({ctx.cell_size_m:.0f}m) 후 시드 재산정: {seed:.0f} → {computed_seed:.0f}")
            seed = computed_seed
    t_ff_s = info.get("freeflow_travel_s", 0.0)

    res = NStarResult(n_star=seed, seed=seed, lane_km=lane_km,
                      freeflow_travel_s=t_ff_s, peak_rate_per_h=r_peak)

    lo: Optional[float] = None      # 확인된 '너무 낮음'
    hi: Optional[float] = None      # 확인된 '너무 높음'
    best: Optional[float] = None    # 통과한 것 중 최대
    tested: list[float] = []

    def _evaluate(level: float, tag: str) -> tuple[float, str, dict]:
        """레벨 하나를 굴려 판정. **스레드로 병렬 호출되므로 prefix가 겹치면 안 된다.**"""
        t0 = time.time()
        prefix = f"nstar_{tag}"
        d = generate_demand(net_file=net_file, out_dir=out_dir, total_trips=level,
                            time_profile=time_profile, prefix=prefix, **demand_kwargs)
        # ⚠️ step_length는 **운영 시뮬과 같은 값**이어야 한다 (2026-07-27 실측).
        # 보정을 빠르게 하려고 1.0으로 키웠더니 영등포 N*가 5,000 → 4,094로 18% 낮게 나왔다.
        # 스텝이 거칠면 차들이 충돌을 못 피해 교착이 더 낮은 수요에서 터진다.
        # 보정값은 운영 조건에서만 의미가 있으므로 속도보다 일치가 우선이다.
        sim = run_simulation(net_file=net_file, routes_file=d.routes_file,
                             out_dir=out_dir, prefix=prefix, step_length=step_length,
                             abort_check=_gridlock_abort)
        v = _verdict(sim)
        rec = {
            "tag": tag,
            "trips": round(level),
            "vehicles": d.n_vehicles,
            "peak_running": sim.peak_running,
            "halting_ratio": sim.congestion_ratio,
            "leftover": sim.stats.get("still_running_at_end"),
            "teleports": sim.stats.get("teleports"),
            "verdict": v,
            "aborted": sim.abort_reason or None,
            "seconds": round(time.time() - t0, 1),
        }
        arrived = sim.stats.get("arrived", 0) or 1
        flags = []
        if sim.stats.get("teleports", 0) > arrived * WARN_TELEPORT_RATIO:
            flags.append("텔포많음")
        if sim.congestion_ratio > HIGH_HALTING_NOTE:
            flags.append("매우빡빡")
        if sim.aborted:
            flags.append(sim.abort_reason.split(":")[0])
        log(f"  [{tag}] {level:.0f}통행 → 피크 {sim.peak_running}대 "
            f"정지 {sim.congestion_ratio * 100:.0f}% 잔류 {rec['leftover']} "
            f"텔포 {rec['teleports']} → **{v}**"
            f"{' [' + '·'.join(flags) + ']' if flags else ''} ({rec['seconds']}s)")
        return level, v, rec

    levels = [seed * f for f in first_round_fracs][:workers]
    for rnd in range(max_rounds):
        levels = [lv for lv in levels
                  if lv >= 1.0 and not any(abs(lv - t) < max(t * 0.02, 1.0) for t in tested)]
        if not levels:
            break
        tested.extend(levels)
        log(f"[라운드 {rnd + 1}] {len(levels)}개 동시 실행: "
            f"{', '.join(f'{lv:.0f}' for lv in sorted(levels))}통행")
        with ThreadPoolExecutor(max_workers=len(levels)) as pool:
            futs = [pool.submit(_evaluate, lv, f"r{rnd + 1}c{i + 1}")
                    for i, lv in enumerate(levels)]
            outcomes = [f.result() for f in futs]

        for level, v, rec in sorted(outcomes, key=lambda x: x[0]):
            res.iterations.append(rec)
            if v == "ok":
                best = level if best is None else max(best, level)
                lo = level if lo is None else max(lo, level)
            elif v == "low":
                lo = level if lo is None else max(lo, level)
            else:
                hi = level if hi is None else min(hi, level)

        # 다음 라운드 레벨
        if lo is not None and hi is not None:
            if hi - lo < max(lo * tolerance, 100):     # 충분히 좁혀짐
                break
            k = max(1, min(workers, 3))
            gap = (hi - lo) / (k + 1)
            levels = [lo + gap * (i + 1) for i in range(k)]
        elif hi is not None:                      # 전부 너무 높음 → 더 내린다
            levels = [hi * (down_factor ** (i + 1)) for i in range(workers)]
        else:                                     # 전부 너무 낮음 → 올린다
            levels = [lo * (up_factor ** (i + 1)) for i in range(workers)]

    _cleanup_round_files(out_dir)
    raw_best = best if best is not None else (lo or seed)
    res.n_star = raw_best * safety_factor       # 확률적 경계 → 안전 계수(위 설명)
    res.converged = best is not None
    res.stats = {
        **info,
        "bracket_low": lo,
        "bracket_high": hi,
        "highest_passing": raw_best,
        "safety_factor": safety_factor,
        "n_iterations": len(res.iterations),
        "n_parallel": workers,
        # 이 N*은 **이 셀 크기의 수요 분포에서만** 유효하다. 시나리오 생성이 같은 값을
        # 써야 하므로 결과에 실어 보낸다(scenario.build_traffic_scenario가 읽는다).
        "cell_size_m": ctx.cell_size_m,
        "k_freeflow_at_nstar": round(res.k_freeflow, 3) if t_ff_s else None,
    }
    if res.converged:
        log(f"N* = {res.n_star:.0f}통행  (통과 최대 {raw_best:.0f} × 안전계수 {safety_factor}, "
            f"시드 {seed:.0f}, {len(res.iterations)}회, 자유류밀도 {res.k_freeflow:.2f} veh/lane-km)")
    else:
        log(f"⚠️ 수렴 실패 — {max_rounds}라운드({len(res.iterations)}회) 안에 "
            f"'생겼다 풀린다' 구간을 못 찾았습니다. 잠정 {res.n_star:.0f}통행. "
            f"max_rounds를 늘리거나 창·신호 설정을 보세요(§5).")
    return res


def _cleanup_round_files(out_dir: str) -> None:
    """라운드별 중간 산출물(`nstar_r*`) 정리.

    레벨 하나당 rou/trips/edgedata만 20MB를 넘고 라운드마다 4개씩 쌓인다. 보정이 끝나면
    쓸 일이 없다(N* 값만 캐시에 남는다). `nstar_ref.*`(시드 산정용)는 진단에 쓰이므로 남긴다.
    """
    try:
        for f in Path(out_dir).glob("nstar_r*"):
            f.unlink(missing_ok=True)
    except OSError:
        pass


# ── 3단계: 캐시 ───────────────────────────────────────────────────────────────

def _cache_key(net_file: str, time_profile, k_target: float, extra: Optional[dict]) -> str:
    """net **내용 해시** 기반 키. 산출물이 그 net의 엣지를 직접 참조하는 캐시용
    (예: 시나리오의 routes 파일 — net이 조금만 달라져도 없는 엣지를 가리키게 된다).
    """
    h = hashlib.sha256()
    h.update(Path(net_file).read_bytes())
    h.update(json.dumps(
        {"profile": [list(map(float, s)) for s in (time_profile or [])],
         "k_target": k_target, "extra": extra or {}},
        sort_keys=True).encode())
    return h.hexdigest()[:16]


def _nstar_cache_key(net_file: str, time_profile, k_target: float,
                     extra: Optional[dict]) -> str:
    """N* 캐시 키 — **net 파일 내용을 일부러 넣지 않는다** (2026-07-28).

    예전엔 `_cache_key`(net 바이트 해시)를 썼는데 **한 번도 적중하지 않았다.** 같은 bbox를
    다시 그려도 netconvert 결과가 매번 미세하게 달라지기 때문이다. 안양 구역 실측 —
    같은 bbox로 만든 두 net:

        공통 엣지 6,370 / 앞에만 25 / 뒤에만 11   (99.7% 동일)
        lane_km  138.81 vs 139.87                 (0.77% 차이)

    차이의 정체는 같은 OSM way의 **분할 인덱스**(`#4` vs `#1`)와 `--ramps.guess`가 만든
    램프 엣지였다. 도로망은 사실상 같은데 바이트 해시는 그 0.3%에 완전히 반응했다.
    게다가 파일 머리에 생성 시각 주석까지 들어간다.

    → 키는 **bbox + 시간프로파일 + k_target**으로 잡고, 적중 여부는 `cached_nstar`가
      **lane_km 5% 이내**인지로 검증한다. 미세한 흔들림은 흡수하고 진짜 도로망 변화는 잡는다.
      (N*는 애초에 run-to-run ±10% 변동이 있고 안전계수 0.9로 흡수하는 값이라,
       5% 흔들림은 그 노이즈 안에 이미 묻힌다.)

    ⚠️ bbox가 없으면(net 전체를 수요 범위로 쓰는 옛 경로) 구역을 구분할 근거가 없으므로
       net 내용 해시로 되돌아간다 — 안 그러면 **모든 구역이 같은 키를 쓴다.**
    """
    if not (extra or {}).get("bbox"):
        return _cache_key(net_file, time_profile, k_target, extra)
    h = hashlib.sha256()
    h.update(json.dumps(
        {"profile": [list(map(float, s)) for s in (time_profile or [])],
         "k_target": k_target, "extra": extra},
        sort_keys=True).encode())
    return h.hexdigest()[:16]


def cached_nstar(
    net_file: str,
    out_dir: str,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
    *,
    cache_dir: Optional[str] = None,
    k_target: float = K_TARGET,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> NStarResult:
    """구역·창 설정당 1회만 산정하고 재사용한다 ("교통 1회, 평가 여러 번").

    캐시 키 = **그린 구역(bbox) + 시간 프로파일 + k_target** (`_nstar_cache_key` 참조).
    적중 판정은 키만으로 하지 않고 **도로 차로연장(lane_km)이 `LANE_KM_TOLERANCE` 이내**
    인지 함께 본다 — 같은 구역이라도 도로가 실제로 늘거나 줄었으면 N*이 달라져야 하므로.

    검증에 net을 한 번 읽어야 해서 수 초가 들지만, 놓치면 보정 전체(수 분)를 다시 돈다.
    잰 값은 `calibrate_nstar`로 넘겨 중복 계산을 없앤다.
    """
    log = log or _log_noop
    cdir = Path(cache_dir) if cache_dir else Path(out_dir) / "_nstar_cache"
    cdir.mkdir(parents=True, exist_ok=True)
    key = _nstar_cache_key(net_file, time_profile, k_target, kwargs.get("cache_extra"))
    cfile = cdir / f"nstar_{key}.json"

    lane_km: Optional[float] = None
    if cfile.exists() and not force:
        data = json.loads(cfile.read_text(encoding="utf-8"))
        lane_km = _lane_km(net_file, kwargs.get("bbox"), log)
        cached_km = float(data.get("lane_km") or 0.0)
        rel = abs(lane_km - cached_km) / max(cached_km, 1e-9)
        if rel <= LANE_KM_TOLERANCE:
            log(f"N* 캐시 적중: {data['n_star']:.0f}통행 — 차로연장 {cached_km:.1f} vs "
                f"현재 {lane_km:.1f} lane-km ({rel * 100:.1f}% 차이, 허용 "
                f"{LANE_KM_TOLERANCE * 100:.0f}%)")
            return NStarResult(**data)
        log(f"⚠️ N* 캐시 무시 — 도로망이 {rel * 100:.1f}% 달라졌습니다 "
            f"({cached_km:.1f} → {lane_km:.1f} lane-km). 다시 보정합니다.")

    kwargs.pop("cache_extra", None)
    res = calibrate_nstar(net_file, out_dir, time_profile, k_target=k_target, log=log,
                          lane_km=lane_km, **kwargs)
    cfile.write_text(json.dumps({
        "n_star": res.n_star, "seed": res.seed, "lane_km": res.lane_km,
        "freeflow_travel_s": res.freeflow_travel_s, "peak_rate_per_h": res.peak_rate_per_h,
        "converged": res.converged, "iterations": res.iterations, "stats": res.stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"N* 캐시 저장: {cfile}")
    return res
