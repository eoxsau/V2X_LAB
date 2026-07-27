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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .assignment import read_net, routable_edges
from .pipeline import generate_demand
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


def _log_noop(_: str) -> None:
    pass


# ── 재료 ──────────────────────────────────────────────────────────────────────

def network_lane_km(net_or_file, vclass: str = "passenger",
                    largest_component_only: bool = True) -> float:
    """통행 가능한 도로의 **차로연장**(lane-km).

    엣지 연장이 아니라 차로 수를 곱한 값이다 — 2차로 도로는 차를 두 배 담는다.
    TAZ와 같은 기준(최대 SCC)으로 재야 실제로 쓰이는 도로만 센다.
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
        total += e.getLength() * max(e.getLaneNumber(), 1)
    return total / 1000.0


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
    lane_km = network_lane_km(net_file)
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


def calibrate_nstar(
    net_file: str,
    out_dir: str,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
    *,
    seed: Optional[float] = None,
    k_target: float = K_TARGET,
    max_iterations: int = 6,
    up_factor: float = 1.35,
    down_factor: float = 0.7,
    seed_safety: float = 0.85,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    step_length: float = DEFAULT_STEP_LENGTH,
    log: Optional[Callable[[str], None]] = None,
    **demand_kwargs,
) -> NStarResult:
    """시드에서 출발해 "생겼다 풀린다" 구간으로 좁힌다.

    **아래에서 위로 브래킷한다.** 교착된 실행은 느리고(차가 안 빠져 끝까지 굴러감) 얻는
    정보도 없다. 그래서 시드가 이미 'high'로 나오면 곱하기로 빠르게 내려온 뒤,
    lo/hi가 잡히면 이분한다.

    Returns: NStarResult — `n_star`는 **통과한 것 중 가장 높은 값**. 하나도 통과하지
        못하면 마지막 'low'(없으면 시드)를 돌려주고 `converged=False`.
    """
    log = log or _log_noop
    lane_km = network_lane_km(net_file)
    r_peak = peak_rate_per_hour(time_profile)

    # seed를 직접 넘겨도 T_ff는 계산한다 — 진단(k_freeflow)에 필요하고, 시뮬 없이 ~15초면 된다.
    computed_seed, info = estimate_nstar_seed(net_file, out_dir, time_profile,
                                              k_target=k_target, log=log, **demand_kwargs)
    if seed is None:
        seed = computed_seed
    else:
        log(f"시드 지정값 {seed:.0f} 사용 (계산값 {computed_seed:.0f})")
    t_ff_s = info.get("freeflow_travel_s", 0.0)

    res = NStarResult(n_star=seed, seed=seed, lane_km=lane_km,
                      freeflow_travel_s=t_ff_s, peak_rate_per_h=r_peak)
    lo: Optional[float] = None      # 확인된 '너무 낮음'
    hi: Optional[float] = None      # 확인된 '너무 높음'
    best: Optional[float] = None    # 통과한 것 중 최대
    # 시드보다 살짝 낮게 시작한다 — 교착된 실행은 3~7배 느리고(차가 안 빠져 끝까지 굴러감)
    # 얻는 정보도 적다. 실측: 교착 113초 vs 정상 16초.
    level = seed * seed_safety

    for it in range(max_iterations):
        t0 = time.time()
        d = generate_demand(net_file=net_file, out_dir=out_dir, total_trips=level,
                            time_profile=time_profile, prefix="nstar", **demand_kwargs)
        # ⚠️ step_length는 **운영 시뮬과 같은 값**이어야 한다 (2026-07-27 실측).
        # 보정을 빠르게 하려고 1.0으로 키웠더니 영등포 N*가 5,000 → 4,094로 18% 낮게 나왔다.
        # 스텝이 거칠면 차들이 충돌을 못 피해 교착이 더 낮은 수요에서 터진다.
        # 보정값은 운영 조건에서만 의미가 있으므로 속도보다 일치가 우선이다.
        sim = run_simulation(net_file=net_file, routes_file=d.routes_file,
                             out_dir=out_dir, prefix="nstar", step_length=step_length)
        v = _verdict(sim)
        rec = {
            "iteration": it + 1,
            "trips": round(level),
            "vehicles": d.n_vehicles,
            "peak_running": sim.peak_running,
            "halting_ratio": sim.congestion_ratio,
            "leftover": sim.stats.get("still_running_at_end"),
            "teleports": sim.stats.get("teleports"),
            "verdict": v,
            "seconds": round(time.time() - t0, 1),
        }
        res.iterations.append(rec)
        arrived = sim.stats.get("arrived", 0) or 1
        flags = []
        if sim.stats.get("teleports", 0) > arrived * WARN_TELEPORT_RATIO:
            flags.append("텔포많음")
        if sim.congestion_ratio > HIGH_HALTING_NOTE:
            flags.append("매우빡빡")
        log(f"  [{it + 1}] {level:.0f}통행 → 피크 {sim.peak_running}대 "
            f"정지 {sim.congestion_ratio * 100:.0f}% 잔류 {rec['leftover']} "
            f"텔포 {rec['teleports']} → **{v}**"
            f"{' [' + '·'.join(flags) + ']' if flags else ''} ({rec['seconds']}s)")

        if v == "ok":
            best = level if best is None else max(best, level)
            lo = level if lo is None else max(lo, level)
        elif v == "low":
            lo = level if lo is None else max(lo, level)
        else:
            hi = level if hi is None else min(hi, level)

        # 다음 레벨
        if lo is not None and hi is not None:
            if hi - lo < max(lo * 0.05, 100):     # 충분히 좁혀짐
                break
            level = (lo + hi) / 2.0
        elif hi is not None:                      # 아직 위쪽만 앎 → 내린다
            level = hi * down_factor
        else:                                     # 아직 아래쪽만 앎 → 올린다
            level = lo * up_factor if lo else level * up_factor

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
        "k_freeflow_at_nstar": round(res.k_freeflow, 3) if t_ff_s else None,
    }
    if res.converged:
        log(f"N* = {res.n_star:.0f}통행  (통과 최대 {raw_best:.0f} × 안전계수 {safety_factor}, "
            f"시드 {seed:.0f}, {len(res.iterations)}회, 자유류밀도 {res.k_freeflow:.2f} veh/lane-km)")
    else:
        log(f"⚠️ 수렴 실패 — 반복 {max_iterations}회 안에 '생겼다 풀린다' 구간을 못 찾았습니다. "
            f"잠정 {res.n_star:.0f}통행. max_iterations를 늘리거나 창·신호 설정을 보세요(§5).")
    return res


# ── 3단계: 캐시 ───────────────────────────────────────────────────────────────

def _cache_key(net_file: str, time_profile, k_target: float, extra: Optional[dict]) -> str:
    h = hashlib.sha256()
    h.update(Path(net_file).read_bytes())
    h.update(json.dumps(
        {"profile": [list(map(float, s)) for s in (time_profile or [])],
         "k_target": k_target, "extra": extra or {}},
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

    캐시 키 = net.xml 내용 해시 + 시간 프로파일 + k_target. net이 바뀌면(구역 변경,
    netconvert 플래그 변경) 자동으로 새로 계산된다.
    """
    log = log or _log_noop
    cdir = Path(cache_dir) if cache_dir else Path(out_dir) / "_nstar_cache"
    cdir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(net_file, time_profile, k_target, kwargs.get("cache_extra"))
    cfile = cdir / f"nstar_{key}.json"

    if cfile.exists() and not force:
        data = json.loads(cfile.read_text(encoding="utf-8"))
        log(f"N* 캐시 적중: {data['n_star']:.0f}통행 ({cfile.name})")
        return NStarResult(**data)

    kwargs.pop("cache_extra", None)
    res = calibrate_nstar(net_file, out_dir, time_profile, k_target=k_target, log=log, **kwargs)
    cfile.write_text(json.dumps({
        "n_star": res.n_star, "seed": res.seed, "lane_km": res.lane_km,
        "freeflow_travel_s": res.freeflow_travel_s, "peak_rate_per_h": res.peak_rate_per_h,
        "converged": res.converged, "iterations": res.iterations, "stats": res.stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"N* 캐시 저장: {cfile}")
    return res
