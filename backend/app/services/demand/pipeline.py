"""교통 수요 생성 오케스트레이터 — net.xml 하나로 SUMO 경로파일까지 (문서 §7 파이프라인 통합).

    net.xml → 도로 bbox → 건물 질량 → 격자 존 → radiation OD → TAZ/OD → od2trips → duarouter
                                                                                  → routes.xml

이 모듈이 생기기 전엔 조립 순서가 `scripts/smoke_demand_pipeline.py`에만 있어서 앱에서
쓸 수 없었다. 스모크 스크립트는 이제 이 함수를 부르기만 한다(회귀 확인용으로 계속 유효).

설계 원칙 — **"교통 1회, 평가 여러 번"**:
    구역·시나리오당 한 번 생성해 캐시하고, 기지국/RSU 배치를 바꿔가며 같은 교통 위에서
    평가한다. 배치를 바꿀 때마다 수요를 다시 만들면 비교가 성립하지 않는다(교통이 달라지면
    무엇 때문에 성능이 변했는지 알 수 없다).

이 파이프라인이 흡수한 함정 (전부 실측으로 확인 — 자세히는 traffic_demand_progress.md §7):
    * 구역 범위는 net.xml의 origBoundary가 아니라 **엣지 형상**에서 잰다(`net_bbox`).
    * 건물은 PostGIS를 건너뛰고 parquet에서 직접 읽는다(`query_by_bbox_parquet`).
    * od2trips만 한글 경로에서 파일을 못 여니 ASCII 임시 디렉터리에 스테이징한다.
    * TAZ엔 최대 승용차 SCC 엣지만 담는다(고립 섬이 라우팅 실패를 만든다).
"""
from __future__ import annotations

import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .assignment import (
    build_taz,
    component_summary,
    excluded_major_roads,
    map_zones_to_taz,
    net_bbox,
    read_net,
    taz_mapping_summary,
    write_od_o_format,
    write_taz_xml,
)
from .grid_mass import Zone, build_zones, zone_stats
from .radiation import ODFlow, od_summary, radiation_od_matrix

# 기본값 — 근거는 traffic_demand_design_v2.md
DEFAULT_CELL_M = 300.0        # §3-2. BS 커버엔 충분, RSU는 엣지별 교통량을 쓰므로 무관

# 적응형 셀 크기 — **OD 셀이 통행보다 훨씬 많으면 수요가 망가진다** (2026-07-28 실측).
#
# od2trips는 OD 셀별 소수 통행을 정수로 올린다. 셀당 값이 1보다 한참 작으면 그 올림이
# 총량을 지배하고, 무엇보다 radiation이 만든 **분포가 균일에 가깝게 납작해진다** —
# 설계문서 §8-1이 "그러면 간선·교차로 집중이라는 결론이 나올 수 없다"고 경고한 그 상태다.
#
# 안양 net(그린 구역), 3,694통행 요청 실측:
#     셀300m · 8슬라이스 → OD셀 110,448 (통행당 29.9) → 차량 7,507대  **203%**
#     셀300m · 2슬라이스 → OD셀  27,612 (통행당  7.5) → 차량 4,601대   125%
#     셀500m · 8슬라이스 → OD셀  31,248 (통행당  8.5) → 차량 4,111대   111%
# 반면 잘 돌던 영등포 앵커는 OD셀 27,376 / 6,244통행 = 통행당 4.4 → 초과 +0.6%.
#
# 즉 "OD 라인 수의 1%"라는 예전 규칙은 **통행당 셀 4~5 구간에서 잰 값**이고, 통행당 30
# 구간으로 외삽하면 안 된다. 아래 비율을 넘지 않도록 셀을 키운다.
MAX_OD_CELLS_PER_TRIP = 5.0
MAX_CELL_M = 1000.0           # 이 이상 키우면 배치 최적화가 보는 공간 해상도가 무의미해진다
DEFAULT_LAMBDA = 0.9999       # §6-5. λ=0은 고밀도 도심에서 통행거리가 셀크기로 붕괴
DEFAULT_MAX_REASSIGN_M = 900.0  # 셀 3칸. "걸어서 큰길까지" 정도가 타당한 상한


def _log_noop(_: str) -> None:
    pass


@dataclass
class DemandContext:
    """통행량과 **무관한** 준비 결과. 레벨을 여러 개 돌릴 때 한 번만 만들어 공유한다.

    N* 보정은 통행량만 바꿔가며 같은 구역을 반복해서 돈다. 그런데 아래 것들은 통행량이
    바뀌어도 **결과가 완전히 같다** — net 읽기, 건물 질량, 격자 존, TAZ, 그리고 radiation
    OD의 *분포*까지. `radiation_od_matrix`의 docstring이 명시하듯 total_trips는 O_i 스케일에만
    영향을 주고, 코드도 `o_i = total_trips × (mass_i / total_mass)` 한 줄에서만 쓴다.

    그래서 흐름을 **합이 1인 단위 스케일**로 한 번 계산해두고, 레벨마다 곱하기만 한다.
    안양 실측: 레벨 하나당 준비가 ~13초였고 4개 병렬이면 GIL 경합으로 2분 15초까지 늘었다
    (스레드를 늘려도 이 구간은 안 빨라진다 — 오히려 나빠진다).

    ⚠️ 만든 뒤에는 **읽기 전용으로만** 쓸 것. 여러 스레드가 동시에 참조한다.
    """
    net_file: str
    net: object
    bbox: tuple[float, float, float, float]
    ref_lat: float
    cell_size_m: float
    zones: list[Zone] = field(default_factory=list)
    n_buildings: int = 0
    flows_unit: list = field(default_factory=list)   # Σ trips == 1.0
    taz: dict = field(default_factory=dict)
    zone_taz: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def build_demand_context(
    net_file: str,
    *,
    cell_size_m: Optional[float] = None,
    design_trips: Optional[float] = None,
    n_slices: int = 1,
    lam: float = DEFAULT_LAMBDA,
    max_reassign_m: float = DEFAULT_MAX_REASSIGN_M,
    bbox_margin_m: Optional[float] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> DemandContext:
    """`generate_demand`의 통행량-무관 앞단을 한 번만 수행한다 (DemandContext 참조).

    cell_size_m : 격자 셀 한 변(m). None이고 `design_trips`를 주면 `fit_cell_size`로
        자동 결정한다(둘 다 없으면 DEFAULT_CELL_M).
    design_trips : 셀 크기를 맞출 기준 통행량. N* 보정이면 **가장 낮은 레벨**을 줄 것.

    건물 로딩 margin은 셀 크기와 무관하게 `DEFAULT_CELL_M`으로 고정한다. margin의 목적은
    "경계에 걸친 건물이 잘려 가장자리 셀 질량이 과소평가되는 것"을 막는 것뿐이라 셀 크기를
    따라갈 이유가 없다. 오히려 따라가게 두면 **셀을 정할 때와 쓸 때 건물 집합이 달라져**
    맞춰놓은 셀 크기가 빗나간다(2026-07-28: 48개로 맞췄는데 실제로는 존 66개가 됐다).
    """
    log = log or _log_noop
    net_path = str(net_file)
    net = read_net(net_path)
    margin = DEFAULT_CELL_M if bbox_margin_m is None else bbox_margin_m
    area = net_bbox(net, margin_m=margin) if bbox is None else _expand_bbox_m(bbox, margin)
    ref_lat = (area[1] + area[3]) / 2
    comp = component_summary(net)
    log(f"수요 bbox {area[0]:.5f},{area[1]:.5f} ~ {area[2]:.5f},{area[3]:.5f} (ref_lat={ref_lat:.5f})")
    log(f"승용차 그래프: 엣지 {comp['vclass_edges']} / 최대성분 {comp['largest_component']} "
        f"({comp['largest_pct']}%) / 고립 {comp['isolated_edges']}")

    # 주요 도로가 통행 배정에서 빠지면 그 축의 교통량이 0이 되고, 배치까지 그 지역을
    # 통째로 비운다. 예전에는 이 사실이 아무 데도 안 드러나 결과만 보고는 원인을 알 수
    # 없었다(2026-07-29: 고속도로 8개가 전부 빠져 우상단 교통량이 0이었다).
    _excluded = excluded_major_roads(net)
    if _excluded:
        _detail = ", ".join(f"{t} {v['count']}개({v['lane_km']:.1f} lane-km)"
                            for t, v in sorted(_excluded.items(), key=lambda kv: -kv[1]["lane_km"]))
        log(f"⚠️ 통행에 못 쓰이는 주요 도로: {_detail} — 구역 안에 진입·진출이 모두 있지 "
            f"않아 통행이 배정되지 않습니다. 이 축의 교통량은 0이 되고 배치도 비게 됩니다.")

    buildings, n_buildings = load_building_mass(area)
    if not buildings:
        raise RuntimeError(
            "bbox 안에서 건물을 찾지 못했습니다. data/processed/buildings 전처리 여부와 "
            "해당 시도 parquet 존재를 확인하세요."
        )
    log(f"건물 {n_buildings}동 / 연면적 {sum(b[2] for b in buildings) / 1e6:.2f} km²")

    if cell_size_m is None:
        cell_size_m = (fit_cell_size(buildings, ref_lat, design_trips, n_slices, log=log)[0]
                       if design_trips else DEFAULT_CELL_M)

    zones = build_zones(buildings, cell_size_m=cell_size_m, ref_lat=ref_lat)
    zstats = zone_stats(zones)
    log(f"존 {zstats['n_zones']}개 / 질량 max·중앙 {zstats['max_over_median']:.1f}배")

    masses = [z.mass for z in zones]
    coords = [(z.center_lat, z.center_lng) for z in zones]
    flows = radiation_od_matrix(masses, coords, 1.0, lam=lam)      # 단위 스케일
    ostats = od_summary(flows, masses, coords)
    log(f"OD 흐름 {ostats['n_flows']}개 / 통행거리 중앙 {ostats['trip_len_median_m']}m")

    taz = build_taz(net, cell_size_m, ref_lat, largest_component_only=True)
    zone_taz = map_zones_to_taz(zones, taz, cell_size_m=cell_size_m, max_reassign_m=max_reassign_m)
    mstats = taz_mapping_summary(zones, zone_taz, cell_size_m=cell_size_m)
    log(f"TAZ {len(taz)}개 / 자기셀 {mstats['zones_own_cell']} · 재배정 "
        f"{mstats['zones_reassigned']} · 버림 {mstats['zones_dropped']}"
        f"(질량 {mstats['mass_dropped_pct']}%)")

    return DemandContext(
        net_file=net_path, net=net, bbox=area, ref_lat=ref_lat, cell_size_m=cell_size_m,
        zones=zones, n_buildings=n_buildings, flows_unit=flows, taz=taz, zone_taz=zone_taz,
        stats={"component": comp, "zone": zstats, "od": ostats, "taz_mapping": mstats},
    )


def fit_cell_size(
    buildings,
    ref_lat: float,
    design_trips: float,
    n_slices: int,
    *,
    start_cell_m: float = DEFAULT_CELL_M,
    max_cell_m: float = MAX_CELL_M,
    max_cells_per_trip: float = MAX_OD_CELLS_PER_TRIP,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[float, int]:
    """OD 셀이 통행 대비 너무 잘게 쪼개지지 않는 셀 크기를 고른다 (근거는 위 상수 주석).

    design_trips : **어떤 통행량 기준으로 맞출지.** N* 보정처럼 여러 레벨을 돌린다면
        가장 낮은 레벨을 넣어야 한다 — 왜곡은 통행이 적을수록 심하기 때문이다.

        ⚠️ 보정 중에 레벨마다 셀 크기를 바꾸면 **안 된다.** 크기가 바뀌면 수요 분포가
        바뀌어 레벨 간 비교가 성립하지 않는다(같은 교통 위에서 비교한다는 전제가 깨진다).
        한 번 정해서 보정 전체와 이후 시나리오 생성까지 같은 값을 쓸 것.

    Returns: (cell_size_m, n_zones)
    """
    log = log or _log_noop
    blist = list(buildings)
    cell = float(start_cell_m)
    n_zones = 0
    for _ in range(8):
        zones = build_zones(blist, cell_size_m=cell, ref_lat=ref_lat)
        n_zones = len(zones)
        if n_zones < 2:
            break
        n_cells = n_zones * (n_zones - 1) * max(n_slices, 1)
        budget = max_cells_per_trip * max(design_trips, 1.0)
        if n_cells <= budget or cell >= max_cell_m:
            break
        # 셀 수는 존 수의 제곱 → 존을 sqrt(초과분)만큼 줄이면 되고,
        # 존 수는 셀 면적에 반비례하므로 셀 한 변은 그 제곱근만큼 키운다.
        cell = min(max_cell_m, cell * (n_cells / budget) ** 0.25 * 1.02)
    if abs(cell - start_cell_m) > 1.0:
        log(f"셀 크기 {start_cell_m:.0f}m → {cell:.0f}m (존 {n_zones}개, "
            f"기준 통행 {design_trips:.0f} · 슬라이스 {n_slices})")
    return cell, n_zones


def _expand_bbox_m(
    bbox: tuple[float, float, float, float], margin_m: float,
) -> tuple[float, float, float, float]:
    """(minlng, minlat, maxlng, maxlat)를 사방으로 margin_m만큼 넓힌다.

    경계에 걸친 건물이 잘려 나가면 가장자리 셀의 질량이 과소평가된다 — `net_bbox`가
    margin을 두는 것과 같은 이유다.
    """
    minlng, minlat, maxlng, maxlat = bbox
    dlat = margin_m / 111_320.0
    dlng = margin_m / (111_320.0 * max(math.cos(math.radians((minlat + maxlat) / 2)), 0.1))
    return (minlng - dlng, minlat - dlat, maxlng + dlng, maxlat + dlat)


# ── SUMO 바이너리 / ASCII 경로 ────────────────────────────────────────────────

def sumo_binary(name: str) -> str:
    """SUMO 실행파일 경로. main.py의 resolve_binary와 같은 규칙(서비스층 독립용 사본)."""
    exe = f"{name}.exe" if platform.system() == "Windows" else name
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        bundled = Path(sumo_home) / "bin" / exe
        if bundled.exists():
            return str(bundled)
    found = shutil.which(name) or shutil.which(exe)
    if not found:
        raise RuntimeError(
            f"{name}을(를) 찾을 수 없습니다. SUMO를 설치하고 SUMO_HOME을 설정하거나 "
            f"PATH에 sumo/bin을 추가해주세요."
        )
    return found


def _is_ascii(p: Path) -> bool:
    return str(p).isascii()


def ascii_temp_base() -> Path:
    """ASCII 전용 임시 디렉터리 루트.

    od2trips는 경로에 비ASCII 문자가 있으면 입력 파일을 열지 못한다(netconvert·duarouter는
    정상 — 도구마다 다르다). 그런데 Windows 기본 임시 경로가 `C:\\Users\\최동혁\\AppData\\...`
    처럼 사용자 이름을 포함해 그 자체로 비ASCII인 경우가 많다. 그래서 시스템 임시 경로가
    ASCII면 그대로 쓰고, 아니면 드라이브 루트 아래 ASCII 폴더를 쓴다.
    환경변수 `V2X_ASCII_TMP`로 덮어쓸 수 있다.
    """
    override = os.environ.get("V2X_ASCII_TMP")
    if override:
        return Path(override)
    sys_tmp = Path(tempfile.gettempdir())
    if _is_ascii(sys_tmp):
        return sys_tmp
    anchor = Path.cwd().anchor or "C:\\"
    return Path(anchor) / "v2x_tmp"


def _run(name: str, args: Sequence, log: Callable[[str], None]) -> None:
    p = subprocess.run(
        [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(f"{name} 실패 (rc={p.returncode}):\n{(p.stderr or p.stdout or '')[-2000:]}")
    log(f"{name} OK")


# ── 결과 ──────────────────────────────────────────────────────────────────────

@dataclass
class DemandResult:
    """생성된 교통 수요 한 세트. 파일 경로 + 재현·진단에 필요한 수치 전부."""
    net_file: str
    taz_file: str
    od_files: list[str]
    trips_file: str
    routes_file: str

    bbox: tuple[float, float, float, float]   # (minlng, minlat, maxlng, maxlat) — 도로 기준
    ref_lat: float
    cell_size_m: float

    zones: list[Zone] = field(default_factory=list)
    n_buildings: int = 0
    total_trips: float = 0.0
    n_trips: int = 0        # od2trips가 만든 trip 수
    n_vehicles: int = 0     # duarouter가 경로를 찾아준 차량 수

    stats: dict = field(default_factory=dict)

    @property
    def survival_rate(self) -> float:
        """total_trips 대비 최종 차량 수. 1.0 근처여야 정상(§5-A).

        od2trips가 OD 라인마다 소수 통행을 반올림하므로 1.0을 살짝 넘을 수 있다.
        """
        return self.n_vehicles / self.total_trips if self.total_trips else 0.0

    @property
    def routing_rate(self) -> float:
        """trip 대비 경로 배정률. 1.0 미만이면 승용차 그래프 고립 성분을 의심(§5-A)."""
        return self.n_vehicles / self.n_trips if self.n_trips else 0.0


# ── 단계별 조각 (개별 재사용 가능) ────────────────────────────────────────────

def load_building_mass(
    bbox: tuple[float, float, float, float],
) -> tuple[list[tuple[float, float, float]], int]:
    """bbox 안 건물 → [(대표점 위도, 경도, 질량)]. 질량 = 바닥면적 × 층수 = 연면적(m²).

    대표점은 centroid가 아니라 `representative_point()` — ㄱ자 건물은 centroid가 폴리곤
    밖으로 나가 엉뚱한 셀에 배정된다(v2 §3-3).
    """
    from app.services.buildings.building_repository import BuildingRepository

    minlng, minlat, maxlng, maxlat = bbox
    gdf = BuildingRepository().query_by_bbox_parquet(minlng, minlat, maxlng, maxlat)
    if gdf.empty:
        return [], 0

    area_m2 = gdf.to_crs(5186).geometry.area   # 4326에서 면적을 재면 안 된다(도 단위)
    floors = gdf["ground_floor"].fillna(1).clip(lower=1)
    rep = gdf.geometry.representative_point()
    buildings = [
        (float(p.y), float(p.x), float(a * f))
        for p, a, f in zip(rep, area_m2, floors)
    ]
    return buildings, len(gdf)


def od_time_slices(
    total_trips: float,
    profile: Optional[Sequence[tuple[float, float, float]]] = None,
    begin_h: float = 7.0,
    end_h: float = 8.0,
) -> list[tuple[float, float, float]]:
    """시간대 슬라이스 → [(begin_h, end_h, 그 구간 통행량)].

    profile: [(begin_h, end_h, share)] — share는 창 전체 대비 비율. 합이 1이 아니어도
        내부에서 정규화한다(24h 곡선에서 일부 구간만 떼어 쓰는 경우가 흔하므로).
        None이면 begin_h~end_h 한 구간에 전량.

    창을 쪼개는 이유(§5-1): 통행이 균등하게 깔리면 정체가 아예 안 생긴다. 정체는
    "몰릴 때" 생기고, 배치의 우열은 **정체가 생겼다 풀리는** 구간에서만 드러난다.
    """
    if not profile:
        return [(begin_h, end_h, total_trips)]
    total_share = sum(s for _, _, s in profile) or 1.0
    return [(b, e, total_trips * s / total_share) for b, e, s in profile]


# ── 메인 오케스트레이터 ───────────────────────────────────────────────────────

def generate_demand(
    net_file: str,
    out_dir: str,
    total_trips: float,
    *,
    cell_size_m: float = DEFAULT_CELL_M,
    lam: float = DEFAULT_LAMBDA,
    max_reassign_m: float = DEFAULT_MAX_REASSIGN_M,
    bbox_margin_m: Optional[float] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
    begin_h: float = 7.0,
    end_h: float = 8.0,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
    context: Optional["DemandContext"] = None,
    prefix: str = "demand",
    log: Optional[Callable[[str], None]] = None,
) -> DemandResult:
    """net.xml 하나로 SUMO에 바로 넣을 수 있는 경로파일까지 만든다.

    Parameters
    ----------
    net_file : netconvert 산출 net.xml **경로**. duarouter에 원본 파일이 필요하므로
        net 객체가 아니라 경로를 받는다.
    out_dir : TAZ·OD·trips·routes를 쓸 디렉터리. 한글 경로여도 된다
        (od2trips 단계만 내부적으로 ASCII로 스테이징한다).
    total_trips : **시뮬레이션 창 전체의 총 통행 수**(통행/창). 동시 주행 대수가 아니다 —
        그건 결과다(Little's Law). 이 값이 N* × 사용자 배율 n에 해당한다(v2 §5-2).
    bbox_margin_m : 수요 범위 밖으로 건물을 얼마나 더 담을지. 기본 = 셀 한 칸.
    bbox : **수요를 만들 범위** (minlng, minlat, maxlng, maxlat). None이면 net 전체 범위.

        ⚠️ 이걸 안 주면 **사용자가 그린 구역이 아니라 net 전체**에 수요를 깐다 (2026-07-28).
        netconvert의 `--keep-edges.in-geo-boundary`는 경계에 걸친 도로를 통째로 살리므로
        net은 그린 구역보다 훨씬 넓어진다. 안양 실측 — 그린 구역 5.45km² → net 19.16km²:

            존 119개 (OD 상한 14,042)  →  존 293개 (OD 상한 85,556)   **6.1배**

        radiation OD가 존 수의 **제곱**이라 넓이 3.5배가 비용 6배가 된다. 정확성 문제이기도
        하다 — 구역 밖에서 나고 드는 통행은 "이 구역의 통신 수요"가 아니다.

        net을 자르지 않고 수요만 좁히는 이유: 도로를 경계에서 자르면 인위적인 막다른 길이
        생겨 그리로 배정된 차가 갇힌다. 주변 도로는 남겨두는 편이 현실적이다(실제 교통도
        구역 밖에서 들어와 빠져나간다).
    time_profile : [(begin_h, end_h, share)] 시간대 슬라이스. None이면 begin_h~end_h 단일 구간.
        24h 곡선(`data/processed/traffic_survey/시간대_프로파일.csv`)에서 창에 해당하는
        구간만 떼어 넘기면 된다.
    context : `build_demand_context()`로 미리 만든 통행량-무관 앞단. 통행량만 바꿔 여러 번
        부를 때(N* 보정) 넘기면 net 읽기·건물·존·radiation·TAZ를 통째로 건너뛴다.
        주면 `cell_size_m`·`lam`·`bbox` 등 앞단 인자는 **context 쪽이 이긴다.**

    Returns
    -------
    DemandResult — 파일 경로 + `survival_rate`·`routing_rate`·`stats` 진단.
    """
    log = log or _log_noop
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1~5. 통행량과 무관한 앞단 — context를 주면 통째로 재사용한다(DemandContext 주석 참조)
    if context is None:
        if bbox is None:
            log("⚠️ 수요 범위를 net 전체로 잡습니다 — 그린 구역을 넘겨주면 훨씬 싸집니다(docstring 참조)")
        context = build_demand_context(
            net_file, cell_size_m=cell_size_m, lam=lam, max_reassign_m=max_reassign_m,
            bbox_margin_m=bbox_margin_m, bbox=bbox, log=log)

    net_path = context.net_file
    net = context.net
    bbox = context.bbox
    ref_lat = context.ref_lat
    cell_size_m = context.cell_size_m
    zones, n_buildings = context.zones, context.n_buildings
    taz, zone_taz = context.taz, context.zone_taz
    comp = context.stats["component"]
    zstats, mstats = context.stats["zone"], context.stats["taz_mapping"]
    # od 진단은 대부분 스케일 무관(비율·거리·개수)이지만 total_trips만 단위 스케일(1.0)이라
    # 이번 통행량으로 되돌려 놓는다. 원본을 건드리면 안 된다 — 스레드 간 공유 객체다.
    ostats = {**context.stats["od"], "total_trips": total_trips}

    # 흐름은 **합이 1인 단위 스케일**로 저장돼 있다 — 이번 통행량으로 곱하기만 하면 된다.
    # (radiation은 total_trips에 정확히 선형이다. radiation.py의 o_i 계산 참조.)
    flows = [ODFlow(i=f.i, j=f.j, trips=f.trips * total_trips) for f in context.flows_unit]

    taz_file = out / f"{prefix}.taz.xml"
    write_taz_xml(taz, str(taz_file))

    slices = od_time_slices(total_trips, time_profile, begin_h, end_h)
    od_files: list[Path] = []
    n_od_lines = 0
    for k, (b_h, e_h, slice_trips) in enumerate(slices):
        # 슬라이스는 factor로 총량을 나눈다 — OD 분포(어디↔어디)는 시간대와 무관하게
        # 같고 총량만 곡선을 탄다는 v2 §5의 "총량과 분포를 분리" 원칙 그대로.
        od_file = out / (f"{prefix}.od.txt" if len(slices) == 1 else f"{prefix}.od.{k:02d}.txt")
        n_od_lines = write_od_o_format(
            flows, zone_taz, str(od_file),
            begin_h=b_h, end_h=e_h,
            factor=slice_trips / total_trips if total_trips else 1.0,
            keep_intra_zone=True,
        )
        od_files.append(od_file)
    log(f"OD 파일 {len(od_files)}개 (슬라이스 {len(slices)}구간, OD 라인 {n_od_lines}개)")

    # od2trips 셀별 반올림 편향 — 초과 차량 ≈ OD 라인 수의 1%로, **총 통행량과 무관한 상수**다
    # (2026-07-27 실측: 3422라인 +37.6대 / 1729라인 +19.1대. scale 0.1로 줄여도 초과는 그대로).
    # 통행량이 라인 수보다 적으면 이 상수가 결과를 지배한다 — N* 튜닝 때 특히 조심(§7).
    if total_trips < n_od_lines:
        log(f"⚠️ total_trips({total_trips:.0f})가 OD 라인 수({n_od_lines})보다 적습니다. "
            f"od2trips 반올림 편향(+{n_od_lines * 0.01:.0f}대 내외)이 "
            f"{n_od_lines * 0.01 / max(total_trips, 1) * 100:.0f}% 수준으로 커집니다.")

    # 수요 손실 내역 — 어디서 새는지 매 실행 확인용(§5-A)
    loaded = sum(f.trips for f in flows if zone_taz.get(f.i) and zone_taz.get(f.j))
    intra = sum(f.trips for f in flows
                if zone_taz.get(f.i) and zone_taz.get(f.j) and zone_taz[f.i] == zone_taz[f.j])
    dropped = sum(f.trips for f in flows
                  if zone_taz.get(f.i) is None or zone_taz.get(f.j) is None)

    # 6. od2trips — **ASCII 스테이징 필수**
    trips_file = out / f"{prefix}.trips.xml"
    routes_file = out / f"{prefix}.rou.xml"
    _od2trips_and_duarouter(net_path, taz_file, od_files, trips_file, routes_file, log)

    n_trips = trips_file.read_text(encoding="utf-8").count("<trip ")
    routes_txt = routes_file.read_text(encoding="utf-8")
    n_vehicles = routes_txt.count("<vehicle ")
    log(f"trip {n_trips}개 → 차량 {n_vehicles}개 "
        f"(라우팅률 {n_vehicles / max(n_trips, 1) * 100:.1f}%, "
        f"생존율 {n_vehicles / total_trips * 100:.1f}%)")

    ff_cost_s, ff_len_m = _freeflow_means(routes_txt)
    log(f"자유류 통행시간 평균 {ff_cost_s:.1f}초 | 경로길이 평균 {ff_len_m:.0f}m")

    result = DemandResult(
        net_file=net_path,
        taz_file=str(taz_file),
        od_files=[str(p) for p in od_files],
        trips_file=str(trips_file),
        routes_file=str(routes_file),
        bbox=bbox,
        ref_lat=ref_lat,
        cell_size_m=cell_size_m,
        zones=zones,
        n_buildings=n_buildings,
        total_trips=total_trips,
        n_trips=n_trips,
        n_vehicles=n_vehicles,
        stats={
            "component": comp,
            "zone": zstats,
            "od": ostats,
            "taz_mapping": mstats,
            "trips_loaded": round(loaded, 1),
            "trips_intra_taz": round(intra, 1),
            "trips_dropped": round(dropped, 1),
            "n_slices": len(slices),
            "n_od_lines": n_od_lines,
            # 자유류(= 다른 차가 없다고 가정) 평균. N* 해석적 시드의 재료(§5-C).
            "freeflow_travel_s": round(ff_cost_s, 2),
            "freeflow_route_m": round(ff_len_m, 1),
        },
    )
    if result.routing_rate < 0.9:
        log("⚠️ 라우팅률 90% 미만 — 승용차 그래프 고립 성분을 의심할 것(§5-A). "
            "component_summary의 isolated_edges 확인.")
    return result


def _freeflow_means(routes_txt: str) -> tuple[float, float]:
    """경로파일 → (평균 자유류 통행시간 s, 평균 경로길이 m).

    duarouter가 `--write-costs --route-length`로 넣어준 `<route cost=".." routeLength="..">`를
    읽는다. **자유류** 값이라 다른 차의 영향이 없고, 그래서 통행량 수준과 무관하게 일정하다 —
    N* 시드를 아무 기준 통행량에서 뽑아도 되는 이유다(§5-C 1단계).
    """
    costs = [float(m) for m in re.findall(r'<route[^>]*\scost="([\d.]+)"', routes_txt)]
    lens = [float(m) for m in re.findall(r'<route[^>]*\srouteLength="([\d.]+)"', routes_txt)]
    return (sum(costs) / len(costs) if costs else 0.0,
            sum(lens) / len(lens) if lens else 0.0)


def _od2trips_and_duarouter(
    net_path: str,
    taz_file: Path,
    od_files: list[Path],
    trips_file: Path,
    routes_file: Path,
    log: Callable[[str], None],
) -> None:
    """od2trips → duarouter. od2trips만 ASCII 경로가 필요해 임시 디렉터리로 스테이징한다.

    duarouter는 한글 경로에서도 정상이므로 굳이 옮기지 않지만, 입력(trips)이 스테이징
    디렉터리에 있으므로 거기서 함께 돌리고 결과만 되돌려 복사한다.
    """
    base = ascii_temp_base()
    base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="demand-", dir=str(base)))
    try:
        s_taz = stage / "s.taz.xml"
        s_net = stage / "s.net.xml"
        s_trips = stage / "s.trips.xml"
        s_rou = stage / "s.rou.xml"
        shutil.copy(taz_file, s_taz)
        shutil.copy(net_path, s_net)
        s_ods = []
        for k, od in enumerate(od_files):
            s_od = stage / f"s.od.{k:02d}.txt"
            shutil.copy(od, s_od)
            s_ods.append(s_od)

        _run("od2trips", [
            sumo_binary("od2trips"),
            "--taz-files", s_taz,
            "--od-matrix-files", ",".join(str(p) for p in s_ods),
            "-o", s_trips,
            # ⚠️ `--spread.uniform`은 쓰지 않는다 (2026-07-27 실측):
            # 이름과 달리 시간축이 아니라 **OD 셀별로** 균등 배치하는 옵션이라, 통행 1대짜리
            # 셀은 전부 구간 **정중앙**에 찍힌다. radiation OD는 작은 셀이 대부분이라
            # 15분 슬라이스 552대 중 331대가 한 분에 몰렸다(옵션 없으면 분당 28~46대로 평탄).
            # 그 인위적 플래툰이 교착의 큰 원인이었다.
            # 같은 TAZ 안(o==d) 통행에서 출발=도착 엣지가 뽑히면 주행이 성립하지 않는다.
            # o==d는 재배정이 두 존을 합쳐 생긴 실재 수요라 살려 쓴다(§5-A).
            "--different-source-sink",
        ], log)

        _run("duarouter", [
            sumo_binary("duarouter"),
            "--net-file", s_net,
            "--route-files", s_trips,
            "-o", s_rou,
            "--ignore-errors",
            "--no-warnings",
            # 경로마다 자유류 통행시간(cost, 초)·경로길이(routeLength, m)를 함께 기록.
            # N* 해석적 시드가 이 값을 쓴다 — 시뮬레이션 0회로 T_ff를 얻는 근거(§5-C).
            "--write-costs", "--route-length",
        ], log)

        shutil.copy(s_trips, trips_file)
        shutil.copy(s_rou, routes_file)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
