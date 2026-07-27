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

import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from .assignment import (
    build_taz,
    component_summary,
    map_zones_to_taz,
    net_bbox,
    read_net,
    taz_mapping_summary,
    write_od_o_format,
    write_taz_xml,
)
from .grid_mass import Zone, build_zones, zone_stats
from .radiation import od_summary, radiation_od_matrix

# 기본값 — 근거는 traffic_demand_design_v2.md
DEFAULT_CELL_M = 300.0        # §3-2. BS 커버엔 충분, RSU는 엣지별 교통량을 쓰므로 무관
DEFAULT_LAMBDA = 0.9999       # §6-5. λ=0은 고밀도 도심에서 통행거리가 셀크기로 붕괴
DEFAULT_MAX_REASSIGN_M = 900.0  # 셀 3칸. "걸어서 큰길까지" 정도가 타당한 상한


def _log_noop(_: str) -> None:
    pass


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
    begin_h: float = 7.0,
    end_h: float = 8.0,
    time_profile: Optional[Sequence[tuple[float, float, float]]] = None,
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
    bbox_margin_m : 도로 범위 밖으로 건물을 얼마나 더 담을지. 기본 = 셀 한 칸.
    time_profile : [(begin_h, end_h, share)] 시간대 슬라이스. None이면 begin_h~end_h 단일 구간.
        24h 곡선(`data/processed/traffic_survey/시간대_프로파일.csv`)에서 창에 해당하는
        구간만 떼어 넘기면 된다.

    Returns
    -------
    DemandResult — 파일 경로 + `survival_rate`·`routing_rate`·`stats` 진단.
    """
    log = log or _log_noop
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. net 로드 + **실제 도로 범위** bbox
    net_path = str(net_file)
    net = read_net(net_path)   # 한 번 읽어 bbox·연결성·TAZ에 재사용
    margin = cell_size_m if bbox_margin_m is None else bbox_margin_m
    bbox = net_bbox(net, margin_m=margin)
    ref_lat = (bbox[1] + bbox[3]) / 2
    comp = component_summary(net)
    log(f"도로 bbox {bbox[0]:.5f},{bbox[1]:.5f} ~ {bbox[2]:.5f},{bbox[3]:.5f} (ref_lat={ref_lat:.5f})")
    log(f"승용차 그래프: 엣지 {comp['vclass_edges']} / 최대성분 {comp['largest_component']} "
        f"({comp['largest_pct']}%) / 고립 {comp['isolated_edges']}")

    # 2. 건물 → 질량
    buildings, n_buildings = load_building_mass(bbox)
    if not buildings:
        raise RuntimeError(
            "bbox 안에서 건물을 찾지 못했습니다. data/processed/buildings 전처리 여부와 "
            "해당 시도 parquet 존재를 확인하세요."
        )
    log(f"건물 {n_buildings}동 / 연면적 {sum(b[2] for b in buildings) / 1e6:.2f} km²")

    # 3. 격자 존
    zones = build_zones(buildings, cell_size_m=cell_size_m, ref_lat=ref_lat)
    zstats = zone_stats(zones)
    log(f"존 {zstats['n_zones']}개 / 질량 max·중앙 {zstats['max_over_median']:.1f}배")

    # 4. radiation OD
    masses = [z.mass for z in zones]
    coords = [(z.center_lat, z.center_lng) for z in zones]
    flows = radiation_od_matrix(masses, coords, total_trips, lam=lam)
    ostats = od_summary(flows, masses, coords)
    log(f"OD 흐름 {ostats['n_flows']}개 / 통행거리 중앙 {ostats['trip_len_median_m']}m")

    # 5. TAZ (최대 SCC만) + 존 재배정 + OD 파일(시간대별)
    taz = build_taz(net, cell_size_m, ref_lat, largest_component_only=True)
    zone_taz = map_zones_to_taz(zones, taz, cell_size_m=cell_size_m, max_reassign_m=max_reassign_m)
    mstats = taz_mapping_summary(zones, zone_taz, cell_size_m=cell_size_m)
    log(f"TAZ {len(taz)}개 / 자기셀 {mstats['zones_own_cell']} · 재배정 "
        f"{mstats['zones_reassigned']} · 버림 {mstats['zones_dropped']}"
        f"(질량 {mstats['mass_dropped_pct']}%)")

    taz_file = out / f"{prefix}.taz.xml"
    write_taz_xml(taz, str(taz_file))

    slices = od_time_slices(total_trips, time_profile, begin_h, end_h)
    od_files: list[Path] = []
    for k, (b_h, e_h, slice_trips) in enumerate(slices):
        # 슬라이스는 factor로 총량을 나눈다 — OD 분포(어디↔어디)는 시간대와 무관하게
        # 같고 총량만 곡선을 탄다는 v2 §5의 "총량과 분포를 분리" 원칙 그대로.
        od_file = out / (f"{prefix}.od.txt" if len(slices) == 1 else f"{prefix}.od.{k:02d}.txt")
        write_od_o_format(
            flows, zone_taz, str(od_file),
            begin_h=b_h, end_h=e_h,
            factor=slice_trips / total_trips if total_trips else 1.0,
            keep_intra_zone=True,
        )
        od_files.append(od_file)
    log(f"OD 파일 {len(od_files)}개 (슬라이스 {len(slices)}구간)")

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
    n_vehicles = routes_file.read_text(encoding="utf-8").count("<vehicle ")
    log(f"trip {n_trips}개 → 차량 {n_vehicles}개 "
        f"(라우팅률 {n_vehicles / max(n_trips, 1) * 100:.1f}%, "
        f"생존율 {n_vehicles / total_trips * 100:.1f}%)")

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
        },
    )
    if result.routing_rate < 0.9:
        log("⚠️ 라우팅률 90% 미만 — 승용차 그래프 고립 성분을 의심할 것(§5-A). "
            "component_summary의 isolated_edges 확인.")
    return result


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
            "--spread.uniform",
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
        ], log)

        shutil.copy(s_trips, trips_file)
        shutil.copy(s_rou, routes_file)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
