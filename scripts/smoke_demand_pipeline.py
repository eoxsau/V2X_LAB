# -*- coding: utf-8 -*-
"""수요 파이프라인 스모크 테스트 — netconvert + `demand.pipeline.generate_demand()` 회귀 확인.

조립 순서는 이제 `backend/app/services/demand/pipeline.py`에 있다. 이 스크립트는
netconvert(main.py와 동일 플래그)만 직접 돌리고, 그 뒤는 오케스트레이터를 호출해
**수치가 기대값에서 벗어나지 않는지** 확인한다.

사용법 (반드시 backend/.venv 로):
    backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py
    backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py area-1b5adb59

**두 구역 다 돌릴 것.** 한 구역만 보면 놓친다 — origBoundary 버그가 영등포에서는
배율이 1에 가까워 보이지 않다가 안양·의왕에서 생존율 2.0%로 드러났다(진행문서 §5-A).

기대값 (2026-07-27):
    area-0baecbba(영등포)   존 61 / 라우팅률 100% / 생존율 96.2%
    area-1b5adb59(안양·의왕) 존 49 / 라우팅률 100% / 생존율 100.6%

주의:
  * PostGIS가 꺼져 있어도 postgis_available()이 True를 반환한다(환경변수만 검사).
    → 이 스크립트는 import 전에 POSTGIS_ENABLED=false를 강제하고, 파이프라인은
      애초에 query_by_bbox_parquet로 PostGIS를 건너뛴다.
  * 콘솔이 cp949면 출력이 깨진다. PYTHONIOENCODING=utf-8 권장.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# ── PostGIS 함정 회피: db.py의 load_dotenv(override=False)보다 먼저 박아야 한다 ──
os.environ["POSTGIS_ENABLED"] = "false"

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
os.environ.setdefault("SUMO_HOME", str(SUMO_HOME))

sys.path.insert(0, str(BACKEND))

AREA = sys.argv[1] if len(sys.argv) > 1 else "area-0baecbba"
OSM_SRC = BACKEND / "networks" / f"{AREA}.osm"

TOTAL_TRIPS = 5000.0   # 임시값 — N* 튜닝은 정체 관측 수단이 생긴 뒤(6단계)


def stage(msg: str) -> None:
    print(f"\n{'=' * 70}\n[{time.strftime('%H:%M:%S')}] {msg}\n{'=' * 70}", flush=True)


def main() -> int:
    if not OSM_SRC.exists():
        print(f"!! OSM 없음: {OSM_SRC}\n   backend/networks/ 의 *.osm 중 하나를 인자로 주세요.")
        return 1
    work = Path(os.environ.get("SMOKE_WORK_DIR", BACKEND / "networks" / "_smoke"))
    work.mkdir(parents=True, exist_ok=True)

    # ── 1. netconvert (main.py와 동일 플래그. 한글 경로 OK) ───────────
    stage(f"1. netconvert — {AREA}.osm → net.xml")
    net = work / "smoke.net.xml"
    p = subprocess.run([
        str(SUMO_HOME / "bin" / "netconvert.exe"),
        "--osm-files", str(OSM_SRC), "--output-file", str(net),
        "--geometry.remove", "--roundabouts.guess", "--ramps.guess", "--junctions.join",
        "--tls.guess", "--tls.guess-signals", "--tls.discard-loaded", "--tls.discard-simple",
        "--no-turnarounds.except-deadend", "--no-warnings",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(f"  FAIL netconvert (rc={p.returncode})\n{(p.stderr or p.stdout)[:1200]}")
        return 1
    n_tls = net.read_text(encoding="utf-8").count("<tlLogic")
    print(f"  OK  netconvert | 신호등(tlLogic) {n_tls}개")

    # ── 2. 수요 생성 (오케스트레이터) ─────────────────────────────────
    stage("2. generate_demand — 건물 → 존 → OD → TAZ/OD → od2trips → duarouter")
    from app.services.demand.pipeline import generate_demand

    t0 = time.time()
    res = generate_demand(
        net_file=str(net),
        out_dir=str(work),
        total_trips=TOTAL_TRIPS,
        prefix="smoke",
        log=lambda m: print(f"  {m}", flush=True),
    )
    print(f"\n  ({time.time() - t0:.1f}s)")

    # ── 3. 결과 판정 ──────────────────────────────────────────────────
    stage("3. 결과")
    m = res.stats["taz_mapping"]
    print(f"  존 {m['n_zones']}개 (자기셀 {m['zones_own_cell']} / 재배정 {m['zones_reassigned']}"
          f" / 버림 {m['zones_dropped']})")
    print(f"  수요 손실: 적재 {res.stats['trips_loaded']:.0f}"
          f" (그중 같은TAZ {res.stats['trips_intra_taz']:.0f})"
          f" | 존 버려짐 {res.stats['trips_dropped']:.0f}")
    print(f"  trip {res.n_trips} → 차량 {res.n_vehicles}")
    print(f"\n  ★ 라우팅률 {res.routing_rate * 100:.1f}%  |  "
          f"최종 생존율 {res.survival_rate * 100:.1f}%")

    ok = res.routing_rate >= 0.9 and res.survival_rate >= 0.9
    print(f"  → {'PASS' if ok else 'FAIL — 진행문서 §5-A 확인'}")
    print(f"\n  산출물: {res.routes_file}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
