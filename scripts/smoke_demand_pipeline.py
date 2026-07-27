# -*- coding: utf-8 -*-
"""수요 파이프라인 스모크 테스트 — 건물 → 존 → radiation OD → TAZ/OD → od2trips → duarouter.

리포에 오케스트레이터가 아직 없으므로, 이 스크립트가 임시 조립 라인 겸 회귀 확인용이다.
정식 오케스트레이터를 만들면 이 스크립트의 흐름을 모듈로 옮기고 여기서는 호출만 하면 된다.

사용법 (반드시 backend/.venv 로):
    backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py
    backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py area-1b5adb59

주의 (전부 2026-07-27 실측으로 확인된 함정 — 자세히는 traffic_demand_progress.md §4·§7):
  * PostGIS가 꺼져 있어도 postgis_available()이 True를 반환해 건물 0동이 나온다.
    → 이 스크립트는 import 전에 POSTGIS_ENABLED=false를 강제해 parquet 경로를 탄다.
  * od2trips는 경로에 한글이 있으면 파일을 못 연다(netconvert·duarouter는 정상).
    → od2trips 단계만 ASCII 임시 디렉터리에 스테이징한다.
  * 콘솔이 cp949면 출력이 깨진다. PYTHONIOENCODING=utf-8 권장.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── PostGIS 함정 회피: db.py의 load_dotenv(override=False)보다 먼저 박아야 한다 ──
os.environ["POSTGIS_ENABLED"] = "false"

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
SUMO_BIN = SUMO_HOME / "bin"
ASCII_DIR = Path(os.environ.get("SMOKE_ASCII_DIR", r"C:\v2x_smoke"))  # od2trips 전용

sys.path.insert(0, str(BACKEND))

AREA = sys.argv[1] if len(sys.argv) > 1 else "area-0baecbba"
OSM_SRC = BACKEND / "networks" / f"{AREA}.osm"

TOTAL_TRIPS = 5000.0   # 임시값 — N* 튜닝은 정체 관측 수단이 생긴 뒤
CELL_M = 300.0
LAM = 0.9999
MAX_REASSIGN_M = 900.0


def stage(msg: str) -> None:
    print(f"\n{'=' * 70}\n[{time.strftime('%H:%M:%S')}] {msg}\n{'=' * 70}", flush=True)


def run(name: str, args: list[str]) -> bool:
    t0 = time.time()
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "SUMO_HOME": str(SUMO_HOME)})
    ok = p.returncode == 0
    print(f"  {'OK  ' if ok else 'FAIL'} {name}  (rc={p.returncode}, {time.time() - t0:.1f}s)")
    if not ok:
        print(f"  ---- stderr ----\n{(p.stderr or p.stdout or '').strip()[:1200]}\n  ----------------")
    return ok


def main() -> int:
    if not OSM_SRC.exists():
        print(f"!! OSM 없음: {OSM_SRC}\n   backend/networks/ 의 *.osm 중 하나를 인자로 주세요.")
        return 1
    work = Path(os.environ.get("SMOKE_WORK_DIR", REPO / "backend" / "networks" / "_smoke"))
    work.mkdir(parents=True, exist_ok=True)

    # ── 1. netconvert (한글 경로 OK) ──────────────────────────────
    stage(f"1. netconvert — {AREA}.osm → net.xml  (main.py와 동일 플래그)")
    net = work / "smoke.net.xml"
    if not run("netconvert", [
        SUMO_BIN / "netconvert.exe",
        "--osm-files", OSM_SRC, "--output-file", net,
        "--geometry.remove", "--roundabouts.guess", "--ramps.guess", "--junctions.join",
        "--tls.guess", "--tls.guess-signals", "--tls.discard-loaded", "--tls.discard-simple",
        "--no-turnarounds.except-deadend", "--no-warnings",
    ]):
        return 1

    txt = net.read_text(encoding="utf-8")
    minlng, minlat, maxlng, maxlat = [
        float(x) for x in re.search(r'origBoundary="([^"]+)"', txt).group(1).split(",")]
    ref_lat = (minlat + maxlat) / 2
    print(f"  bbox {minlng:.5f},{minlat:.5f} ~ {maxlng:.5f},{maxlat:.5f} (ref_lat={ref_lat:.5f})")
    print(f"  신호등(tlLogic) = {txt.count('<tlLogic')}개")

    # ── 2. 건물 → 질량 ────────────────────────────────────────────
    stage("2. 건물 로드 → 질량(연면적 = 바닥면적 × 층수)")
    from app.services.buildings.building_repository import BuildingRepository

    gdf = BuildingRepository().query_by_bbox(minlng, minlat, maxlng, maxlat)
    print(f"  건물 {len(gdf)}동")
    if gdf.empty:
        print("!! 건물 0동 — PostGIS 함정(§4) 또는 해당 시도 parquet 미존재")
        return 1
    area_m2 = gdf.to_crs(5186).geometry.area
    floors = gdf["ground_floor"].fillna(1).clip(lower=1)
    rep = gdf.geometry.representative_point()   # centroid 금지(ㄱ자 건물) — v2 §3-3
    buildings = [(float(p.y), float(p.x), float(a * f))
                 for p, a, f in zip(rep, area_m2, floors)]
    print(f"  총 연면적 {sum(b[2] for b in buildings) / 1e6:.2f} km²")

    # ── 3. 격자 존 ────────────────────────────────────────────────
    stage("3. 격자 존 + 질량")
    from app.services.demand.grid_mass import build_zones, zone_stats

    zones = build_zones(buildings, cell_size_m=CELL_M, ref_lat=ref_lat)
    st = zone_stats(zones)
    print(f"  존 {st['n_zones']}개 | 질량 max/중앙 {st['max_over_median']:.1f}배 "
          f"| 존당 건물 중앙 {st['buildings_median_per_zone']}동")

    # ── 4. radiation OD ───────────────────────────────────────────
    stage("4. radiation OD")
    from app.services.demand.radiation import radiation_od_matrix, od_summary

    masses = [z.mass for z in zones]
    coords = [(z.center_lat, z.center_lng) for z in zones]
    flows = radiation_od_matrix(masses, coords, TOTAL_TRIPS, lam=LAM)
    for k, v in od_summary(flows, masses, coords).items():
        print(f"    {k:24s} {v}")

    # ── 5. TAZ + OD (최근접 도로존 재배정 적용) ───────────────────
    stage("5. TAZ + OD 파일  (도로 없는 존 → 최근접 도로존 재배정)")
    from app.services.demand.assignment import (build_taz, write_taz_xml, write_od_o_format,
                                                map_zones_to_taz, taz_mapping_summary)

    taz = build_taz(str(net), CELL_M, ref_lat)
    zone_taz = map_zones_to_taz(zones, taz, cell_size_m=CELL_M, max_reassign_m=MAX_REASSIGN_M)
    print(f"  TAZ(도로 있는 셀) {len(taz)}개")
    for k, v in taz_mapping_summary(zones, zone_taz, cell_size_m=CELL_M).items():
        print(f"    {k:24s} {v}")

    kept = sum(f.trips for f in flows
               if zone_taz.get(f.i) and zone_taz.get(f.j) and zone_taz[f.i] != zone_taz[f.j])
    intra = sum(f.trips for f in flows
                if zone_taz.get(f.i) and zone_taz.get(f.j) and zone_taz[f.i] == zone_taz[f.j])
    lost = sum(f.trips for f in flows
               if zone_taz.get(f.i) is None or zone_taz.get(f.j) is None)
    print(f"\n  통행량 {TOTAL_TRIPS:.0f} 중 → OD적재 {kept:.0f} ({kept / TOTAL_TRIPS * 100:.1f}%)"
          f" | 같은TAZ(o==d) 탈락 {intra:.0f} | 존 버려짐 {lost:.0f}")

    taz_f, od_f = work / "smoke.taz.xml", work / "smoke.od.txt"
    write_taz_xml(taz, str(taz_f))
    n_od = write_od_o_format(flows, zone_taz, str(od_f), begin_h=7.0, end_h=8.0)
    print(f"  OD 라인 {n_od}개")

    # ── 6. od2trips (ASCII 스테이징 필수) ─────────────────────────
    stage("6. od2trips — OD → trip  (한글 경로 불가 → ASCII 스테이징)")
    ASCII_DIR.mkdir(parents=True, exist_ok=True)
    a_taz, a_od = ASCII_DIR / "s.taz.xml", ASCII_DIR / "s.od.txt"
    a_trips, a_net = ASCII_DIR / "s.trips.xml", ASCII_DIR / "s.net.xml"
    shutil.copy(taz_f, a_taz); shutil.copy(od_f, a_od); shutil.copy(net, a_net)

    if not run("od2trips", [
        SUMO_BIN / "od2trips.exe", "--taz-files", a_taz, "--od-matrix-files", a_od,
        "-o", a_trips, "--spread.uniform",
    ]):
        return 1
    n_trips = a_trips.read_text(encoding="utf-8").count("<trip ")
    print(f"  trip {n_trips}개")

    # ── 7. duarouter ──────────────────────────────────────────────
    stage("7. duarouter — trip → 경로")
    a_rou = ASCII_DIR / "s.rou.xml"
    if not run("duarouter", [
        SUMO_BIN / "duarouter.exe", "--net-file", a_net, "--route-files", a_trips,
        "-o", a_rou, "--ignore-errors", "--no-warnings",
    ]):
        return 1
    n_veh = a_rou.read_text(encoding="utf-8").count("<vehicle ")
    print(f"  경로 배정 차량 {n_veh}개  (trip 대비 {n_veh / max(n_trips, 1) * 100:.1f}%)")
    print(f"\n  ★ 최종 생존율 = {n_veh / TOTAL_TRIPS * 100:.1f}%  "
          f"(total_trips {TOTAL_TRIPS:.0f} → 차량 {n_veh}개)")
    if n_veh / max(n_trips, 1) < 0.9:
        print("  ⚠️ 라우팅률 90% 미만 — 승용차 그래프 고립 성분 문제 의심(§5-A). "
              "duarouter에서 --no-warnings를 빼고 'No connection between edge'를 세어볼 것.")

    stage("스모크 테스트 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
