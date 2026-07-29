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
    # 이 교통을 만들 때 쓴 **그린 구역**(minlng, minlat, maxlng, maxlat).
    # 기록해두지 않으면 나중에 같은 조건으로 재현할 수 없다 — 2026-07-29에 생존율 31%짜리
    # 시나리오를 조사하려 했는데 bbox가 어디에도 없어서 재현을 못 했다(같은 net·같은 N*로
    # 다시 돌리면 98.3%가 나와, 무엇이 달랐는지 끝내 특정하지 못했다).
    area_bbox: Optional[tuple] = None
    # 통과 교통 비율 p (대수 기준). 0이면 구역 내부 통행만.
    through_ratio: float = 0.0
    demand_points: list = field(default_factory=list)   # sa_placement.DemandPoint
    # 피크 구간 엣지별 교통량 원본 — 배치 수요(demand_points)와 배경 차량 양쪽의 재료.
    # {edge_id, lat, lng, vehicle_count, mean_speed_kph} 형태로 직렬화해 캐시한다.
    peak_edge_loads: list = field(default_factory=list)
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
                window: tuple[float, float], step_min: float,
                area_bbox: Optional[tuple[float, float, float, float]] = None,
                through_ratio: float = 0.0) -> Path:
    from .calibration import _cache_key
    # area_bbox가 키에 **반드시** 들어가야 한다 — 같은 net이라도 수요 범위가 다르면
    # 완전히 다른 교통이다. 빠뜨리면 구역을 좁힌 뒤에도 예전(net 전체) 캐시가 적중한다.
    # through_ratio도 같은 이유로 키다 — p를 바꾸면 통행 구성 자체가 달라진다.
    key = _cache_key(net_file, None, 0.0, {"scale": round(demand_scale, 4),
                                           "window": list(window), "step": step_min,
                                           "through": round(through_ratio, 4),
                                           "bbox": [round(v, 6) for v in area_bbox] if area_bbox else None})
    return out_dir / f"scenario_{key}.json"


def build_traffic_scenario(
    net_file: str,
    out_dir: str,
    *,
    demand_scale: float = 1.0,
    window: tuple[float, float] = DEFAULT_WINDOW,
    step_min: float = DEFAULT_STEP_MIN,
    n_star: Optional[float] = None,
    area_bbox: Optional[tuple[float, float, float, float]] = None,
    through_ratio: float = 0.0,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> TrafficScenario:
    """구역 하나의 교통을 통째로 만든다 (필요하면 N\\*부터).

    Parameters
    ----------
    demand_scale : 기준 교통량 대비 배율. UI의 % 노브 ÷ 100. 0.1~3.0으로 잘린다.
    n_star : 이미 아는 값이 있으면 넘긴다. None이면 `cached_nstar`로 산정(구역당 1회).
    area_bbox : **사용자가 그린 구역** (minlng, minlat, maxlng, maxlat). 수요를 여기에만
        깐다. None이면 net 전체 — net은 그린 구역보다 훨씬 넓으므로 비용이 몇 배로 뛴다
        (근거는 `pipeline.generate_demand`의 bbox docstring).

    Returns
    -------
    TrafficScenario — `routes_file`(실시간 주행용) + `demand_points`(배치 최적화용).
    """
    log = log or _log_noop
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scale = clamp_demand_scale(demand_scale)
    profile = build_time_profile(window[0], window[1], step_min)

    cache_f = _cache_path(out, net_file, scale, window, step_min, area_bbox, through_ratio)
    if cache_f.exists() and not force:
        try:
            data = json.loads(cache_f.read_text(encoding="utf-8"))
            if Path(data["routes_file"]).exists():
                log(f"교통 캐시 적중: {data['total_trips']:.0f}통행 ({cache_f.name})")
                sc = TrafficScenario(**{k: v for k, v in data.items() if k != "demand_points"})
                sc.demand_points = _rebuild_demand_points(data.get("demand_points") or [])
                # 캐시 키가 net **내용 해시**라 같은 내용의 다른 경로에도 적중한다.
                # 그때 net_file이 예전 경로로 남으면 호출부의 "이 구역 것이 맞나" 검사가
                # 어긋나 매번 다시 만들게 된다(2026-07-27 실측). 요청 경로로 갱신한다.
                sc.net_file = str(net_file)
                return sc
        except Exception as exc:
            log(f"교통 캐시 무시 (읽기 실패: {exc})")

    # 1. N* — 구역당 1회, net 해시 + 수요 범위로 캐시
    cell_m = None
    if n_star is None:
        ns = cached_nstar(net_file, str(out), profile, log=log,
                          bbox=area_bbox, cache_extra={"bbox": [round(v, 6) for v in area_bbox]
                                                       if area_bbox else None})
        n_star = ns.n_star
        # ⚠️ 보정이 쓴 격자 셀 크기를 **그대로** 이어받는다. 다른 크기로 수요를 만들면
        # 분포가 달라져 N*이 가리키던 운영점이 아니게 된다(calibration에서 실어 보낸다).
        cell_m = (ns.stats or {}).get("cell_size_m")
        if not ns.converged:
            log("⚠️ N* 보정이 수렴하지 않았습니다 — 잠정값을 씁니다(§5-C).")
    total_trips = n_star * scale
    log(f"N* {n_star:.0f} × {scale * 100:.0f}% = {total_trips:.0f}통행"
        + (f" (셀 {cell_m:.0f}m)" if cell_m else ""))

    # 2. 수요 생성
    _gen_kw = {"cell_size_m": cell_m} if cell_m else {}
    d: DemandResult = generate_demand(
        net_file=net_file, out_dir=str(out), total_trips=total_trips,
        time_profile=profile, prefix=f"scn{round(scale * 100)}", bbox=area_bbox,
        through_ratio=through_ratio, log=log, **_gen_kw)

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
        area_bbox=tuple(area_bbox) if area_bbox else None,
        through_ratio=float(through_ratio),
        demand_points=points,
        peak_edge_loads=[
            {"edge_id": ld.edge_id, "lat": ld.lat, "lng": ld.lng,
             "vehicle_count": round(ld.vehicle_count, 3),
             "mean_speed_kph": round(ld.mean_speed_kph, 1)}
            for ld in sim.peak_edges
        ],
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


def background_vehicles_from_scenario(sc: TrafficScenario,
                                      max_vehicles: int = 20000) -> list[dict]:
    """피크 스냅샷 → 배경 차량 목록 `[{id, lat, lng, speed_kmh}]`.

    분석·배치 경로(`main._prepare_simulation_run`)가 기지국별 접속 차량 수를 셀 때 쓰던
    `_generate_background_vehicles`(bbox 안 무작위 OD)를 대체한다.

    왜 바꾸나: 무작위 배치는 모든 도로에 차를 고르게 뿌린다. 그런데 실측에서 교통은
    **상위 10% 엣지에 75%** 가 몰린다. 균일하게 뿌리면 간선 옆 기지국과 골목 옆 기지국의
    부하가 비슷하게 나와, 배치를 바꿔도 성능 차이가 드러나지 않는다.

    엣지 하나가 실은 차량 수(`vehicle_count`, 그 구간 평균 동시 대수)만큼 그 엣지
    중점에 차를 놓는다. 속도는 그 엣지의 실측 평균을 쓴다 — 막힌 엣지의 차는 느리게,
    뚫린 엣지의 차는 빠르게 잡힌다.

    max_vehicles : 안전 상한. 배율 300%처럼 극단적인 설정에서 목록이 폭주하지 않게 한다.
    """
    out: list[dict] = []
    for ld in sorted(sc.peak_edge_loads or [],
                     key=lambda x: -float(x.get("vehicle_count") or 0)):
        n = int(round(float(ld.get("vehicle_count") or 0)))
        if n <= 0:
            continue
        spd = float(ld.get("mean_speed_kph") or 0.0)
        for k in range(n):
            if len(out) >= max_vehicles:
                return out
            out.append({
                "id": f"bg-{ld['edge_id']}-{k}",
                "lat": float(ld["lat"]),
                "lng": float(ld["lng"]),
                "speed_kmh": round(spd, 1),
            })
    return out


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
