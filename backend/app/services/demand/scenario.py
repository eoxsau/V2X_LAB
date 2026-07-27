"""교통 시나리오 — 앱이 부르는 **단일 진입점** (문서 §7 파이프라인 통합).

    net.xml + 수요 배율(%)  →  N* 산정 → 수요 생성 → 동적 SUMO → 경로파일 + 배치 수요

main.py는 이 모듈의 `build_traffic_scenario()` 하나만 알면 된다. 그 안에서
N* 캐시·시간 프로파일·od2trips 스테이징·교착 처리 같은 함정이 전부 처리된다.

설계 원칙 — **"교통 1회, 평가 여러 번"**:
    구역·배율당 한 번 만들어 캐시하고, 기지국/RSU 배치를 바꿔가며 같은 교통 위에서
    평가한다. 배치를 바꿀 때마다 교통이 달라지면 무엇 때문에 성능이 변했는지 알 수 없다.

수요 배율(`demand_scale`):
    UI의 "기준 교통량의 몇 %" 노브. 1.0 = N*(정체가 생겼다 풀리는 수준).
    `total_trips = N* × demand_scale`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .calibration import cached_nstar
from .pipeline import DemandResult, generate_demand
from .simulation import (SimResult, edge_loads_to_demand, pick_target_depart_time,
                         run_simulation)
from .time_profile import build_time_profile

# UI 노브 범위 — 2026-07-27 사용자 확정 (상·하한은 잠정, 실사용 보고 조정)
MIN_DEMAND_SCALE = 0.1     # 10%
MAX_DEMAND_SCALE = 3.0     # 300%

# 시뮬레이션 창 — §6 확정: 07:00~09:00 아침 첨두, 15분 8구간
DEFAULT_WINDOW = (7.0, 9.0)
DEFAULT_STEP_MIN = 15.0

# 타겟 차량 출발 시점을 고르는 상한 — 정지 비율이 이 값 이하인 가장 늦은 시각을 쓴다.
# 0.6은 실측에서 "진짜 막히는데 그래도 굴러가는" 구간(영등포 07:45, 221대 주행·정지 57%).
# 이보다 높이면 출발 엣지가 막혀 타겟이 삽입조차 안 된다(simulation.pick_target_depart_time).
TARGET_MAX_HALTING = 0.6


def _log_noop(_: str) -> None:
    pass


def clamp_demand_scale(value: float) -> float:
    """배율을 허용 범위로 자른다. UI/API 양쪽에서 같은 규칙을 쓰기 위한 단일 지점."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_DEMAND_SCALE, min(MAX_DEMAND_SCALE, v))


@dataclass
class TrafficScenario:
    """한 구역·한 배율의 교통 한 세트. 배치 평가와 실시간 주행이 이걸 공유한다."""
    net_file: str
    routes_file: str
    n_star: float
    demand_scale: float
    total_trips: float

    begin_s: float                     # 시뮬 시작 시각(초) — 창 시작
    depart_begin_s: float              # 첫 차 출발
    depart_end_s: float                # 마지막 차 출발
    warmup_until_s: float              # 이 시각까지 예열(화면 갱신 없이 스텝만) 후 타겟 출발
    peak_time_s: float                 # 가장 붐빈 시각 = 배치 스냅샷 시점

    n_vehicles: int = 0
    demand_points: list = field(default_factory=list)   # sa_placement.DemandPoint
    stats: dict = field(default_factory=dict)

    @property
    def peak_running(self) -> int:
        return int(self.stats.get("peak_running", 0))

    def to_summary(self) -> dict:
        """API 응답용 요약 — 프론트가 그대로 보여줄 수 있는 수치만."""
        return {
            "n_star": round(self.n_star),
            "demand_scale": self.demand_scale,
            "demand_scale_pct": round(self.demand_scale * 100),
            "total_trips": round(self.total_trips),
            "n_vehicles": self.n_vehicles,
            "peak_running": self.peak_running,
            "peak_time_h": round(self.peak_time_s / 3600, 2),
            "halting_ratio": self.stats.get("peak_halting_ratio"),
            "window_h": [round(self.begin_s / 3600, 2), round(self.depart_end_s / 3600, 2)],
            "warmup_min": round((self.warmup_until_s - self.begin_s) / 60),
            "n_demand_points": len(self.demand_points),
            "vehicle_count_error_pct": self.stats.get("vehicle_count_error_pct"),
        }


def _cache_path(out_dir: Path, net_file: str, demand_scale: float,
                window: tuple[float, float], step_min: float) -> Path:
    from .calibration import _cache_key
    key = _cache_key(net_file, None, 0.0, {"scale": round(demand_scale, 4),
                                           "window": list(window), "step": step_min})
    return out_dir / f"scenario_{key}.json"


def build_traffic_scenario(
    net_file: str,
    out_dir: str,
    *,
    demand_scale: float = 1.0,
    window: tuple[float, float] = DEFAULT_WINDOW,
    step_min: float = DEFAULT_STEP_MIN,
    n_star: Optional[float] = None,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> TrafficScenario:
    """구역 하나의 교통을 통째로 만든다 (필요하면 N\\*부터).

    Parameters
    ----------
    demand_scale : 기준 교통량 대비 배율. UI의 % 노브 ÷ 100. 0.1~3.0으로 잘린다.
    n_star : 이미 아는 값이 있으면 넘긴다. None이면 `cached_nstar`로 산정(구역당 1회).

    Returns
    -------
    TrafficScenario — `routes_file`(실시간 주행용) + `demand_points`(배치 최적화용).
    """
    log = log or _log_noop
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scale = clamp_demand_scale(demand_scale)
    profile = build_time_profile(window[0], window[1], step_min)

    cache_f = _cache_path(out, net_file, scale, window, step_min)
    if cache_f.exists() and not force:
        try:
            data = json.loads(cache_f.read_text(encoding="utf-8"))
            if Path(data["routes_file"]).exists():
                log(f"교통 캐시 적중: {data['total_trips']:.0f}통행 ({cache_f.name})")
                sc = TrafficScenario(**{k: v for k, v in data.items() if k != "demand_points"})
                sc.demand_points = _rebuild_demand_points(data.get("demand_points") or [])
                return sc
        except Exception as exc:
            log(f"교통 캐시 무시 (읽기 실패: {exc})")

    # 1. N* — 구역당 1회, net 해시로 캐시
    if n_star is None:
        ns = cached_nstar(net_file, str(out), profile, log=log)
        n_star = ns.n_star
        if not ns.converged:
            log("⚠️ N* 보정이 수렴하지 않았습니다 — 잠정값을 씁니다(§5-C).")
    total_trips = n_star * scale
    log(f"N* {n_star:.0f} × {scale * 100:.0f}% = {total_trips:.0f}통행")

    # 2. 수요 생성
    d: DemandResult = generate_demand(
        net_file=net_file, out_dir=str(out), total_trips=total_trips,
        time_profile=profile, prefix=f"scn{round(scale * 100)}", log=log)

    # 3. 동적 SUMO — 정체 만들고 피크 스냅샷
    sim: SimResult = run_simulation(
        net_file=net_file, routes_file=d.routes_file, out_dir=str(out),
        prefix=f"scn{round(scale * 100)}", log=log)

    points = edge_loads_to_demand(sim.peak_edges)

    # 요청한 통행 수와 실제 차량 수의 차이를 **실측 그대로** 보고한다.
    # 공식으로 추정하지 않는 이유: od2trips 셀별 반올림 편향은 "OD 라인 수의 1%"라는
    # 단순 규칙보다 복잡하다(슬라이스가 8개면 셀도 8배). 실제로 영등포 10% 배율에서
    # 공식 추정 6.1% vs 실측 +16.4%로 어긋났다. 어차피 정확한 값을 이미 알고 있으므로
    # 추정하지 말고 그대로 쓴다. (부호가 +면 반올림 초과, −면 수요 손실 우세.)
    n_lines = d.stats.get("n_od_lines") or 0
    err_pct = round((d.n_vehicles - total_trips) / max(total_trips, 1.0) * 100, 1)
    if abs(err_pct) >= 10.0:
        log(f"⚠️ 요청 {total_trips:.0f}통행 대비 실제 차량 {d.n_vehicles}대 "
            f"({err_pct:+.1f}%). 배율이 낮을수록 od2trips 반올림 편향이 커진다(§7) — "
            f"절대 대수를 논할 땐 감안할 것.")

    sc = TrafficScenario(
        net_file=str(net_file), routes_file=d.routes_file,
        n_star=float(n_star), demand_scale=scale, total_trips=total_trips,
        begin_s=sim.begin_s, depart_begin_s=sim.begin_s,
        depart_end_s=window[1] * 3600.0,
        # 예열 목표 = 타겟이 출발할 시각. 곡선에서 고른다(§2-6 "예열 → 출발 → 정체 뚫고 주행").
        #
        # 최소 예열(15분)만 하면 타겟이 07:15에 출발해 한산한 도로를 달려 정체를 못 만나고,
        # 반대로 피크에 맞추면 정지 88%라 **출발 엣지에 삽입조차 안 된다**(실측: 20분 대기 후 중단).
        # 그래서 "정지 비율이 TARGET_MAX_HALTING 이하인 가장 늦은 시각"을 쓴다.
        warmup_until_s=pick_target_depart_time(sim, max_halting=TARGET_MAX_HALTING),
        peak_time_s=sim.peak_time_s,
        n_vehicles=d.n_vehicles,
        demand_points=points,
        stats={
            **sim.stats,
            "survival_rate": round(d.survival_rate, 4),
            "routing_rate": round(d.routing_rate, 4),
            "n_od_lines": n_lines,
            "vehicle_count_error_pct": err_pct,
        },
    )
    _write_cache(cache_f, sc)
    log(f"교통 준비 완료 — 차량 {sc.n_vehicles}대, 피크 {sc.peak_running}대 "
        f"@{sc.peak_time_s / 3600:.2f}h, 배치 수요점 {len(points)}개")
    return sc


def _write_cache(path: Path, sc: TrafficScenario) -> None:
    data = {k: v for k, v in sc.__dict__.items() if k != "demand_points"}
    data["demand_points"] = [
        {"id": p.id, "lat": p.lat, "lng": p.lng, "vehicle_count": p.vehicle_count}
        for p in sc.demand_points
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _rebuild_demand_points(raw: list) -> list:
    from app.services.placement.sa_placement import DemandPoint
    return [DemandPoint(id=r["id"], lat=r["lat"], lng=r["lng"],
                        vehicle_count=r["vehicle_count"]) for r in raw]
