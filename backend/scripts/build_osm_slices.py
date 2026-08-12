"""build_osm_slices.py — 전국 OSM PBF를 시도별 '도로만' 조각으로 미리 잘라둔다.

왜 필요한가
-----------
전국 PBF에는 **위치로 찾아보는 색인이 없다.** 그래서 구역 하나를 뽑으려 해도 272MB를
처음부터 끝까지 훑어야 하고, 구역 크기와 무관하게 매번 1분 남짓이 걸린다
(파이썬 콜백을 없애기 전에는 4~5분이었다).

시도별로 미리 잘라두면 그 다음부터는 해당 시도 조각만 읽으면 된다 — 거친 색인을
직접 만들어 두는 셈이다. 한 번만 돌리면 되고, 이후 모든 구역 설정이 빨라진다.

만드는 것
---------
    backend/data/osm_slices/<osm_id>.osm.pbf   시도별 도로망 조각
    backend/data/osm_slices/index.json         조각별 경계 + 메타

사용법
------
    python scripts/build_osm_slices.py                # 전부 만들기(이미 있으면 건너뜀)
    python scripts/build_osm_slices.py --force        # 다시 만들기
    python scripts/build_osm_slices.py --only 경기도   # 이름에 포함되는 시도만
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.regions.region_service import (  # noqa: E402
    SLICE_DIR, SLICE_INDEX, _extract_with_pyosmium, get_sido_list, resolve_local_pbf,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="전국 OSM PBF → 시도별 도로 조각")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 만든다")
    ap.add_argument("--only", default=None, help="이름에 이 문자열이 들어간 시도만")
    args = ap.parse_args()

    pbf = resolve_local_pbf()
    if not pbf or not pbf.exists():
        print("ERROR: 전국 PBF를 찾지 못했습니다. LOCAL_PBF_PATH를 확인하세요.")
        return 1
    print(f"전국 파일: {pbf}  ({pbf.stat().st_size / 1e6:.0f} MB)")

    sido = get_sido_list()
    if args.only:
        sido = [r for r in sido if args.only in (r.get("name_ko") or "")]
    if not sido:
        print("ERROR: 대상 시도가 없습니다.")
        return 1

    SLICE_DIR.mkdir(parents=True, exist_ok=True)
    index: dict = {}
    if SLICE_INDEX.exists():
        try:
            index = json.loads(SLICE_INDEX.read_text(encoding="utf-8"))
        except Exception:
            index = {}

    total_t = time.time()
    for i, r in enumerate(sido, 1):
        name = r.get("name_ko") or str(r.get("osm_id"))
        key = str(r.get("osm_id"))
        out = SLICE_DIR / f"{key}.osm.pbf"
        s, n = float(r["min_lat"]), float(r["max_lat"])
        w, e = float(r["min_lon"]), float(r["max_lon"])

        if out.exists() and not args.force:
            print(f"[{i}/{len(sido)}] {name}: 이미 있음 — 건너뜀 ({out.stat().st_size/1e6:.1f} MB)")
            index[key] = {"name": name, "file": out.name, "s": s, "w": w, "n": n, "e": e,
                          "size_mb": round(out.stat().st_size / 1e6, 1)}
            continue

        print(f"[{i}/{len(sido)}] {name}: 자르는 중… (lat {s:.3f}~{n:.3f}, lon {w:.3f}~{e:.3f})", flush=True)
        t = time.time()
        try:
            # 도 하나는 구역이 넓어 도로를 통째로 들고 있으면 메모리가 터진다 — 저메모리 모드.
            _extract_with_pyosmium(pbf, out, s, w, n, e, low_memory=True)
        except Exception as exc:
            print(f"    실패: {exc}")
            continue
        mb = out.stat().st_size / 1e6
        print(f"    완료 {time.time()-t:.0f}초, {mb:.1f} MB", flush=True)
        index[key] = {"name": name, "file": out.name, "s": s, "w": w, "n": n, "e": e,
                      "size_mb": round(mb, 1)}
        SLICE_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    SLICE_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n전체 완료: {len(index)}개 조각, {time.time()-total_t:.0f}초")
    print(f"목록: {SLICE_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
