#!/usr/bin/env python3
"""교통량조사 원본(xlsx + 지점 GIS) → 정규화 parquet.

기존 `preprocess_traffic_survey.py`(전국 등급 평균 CSV + 시간대 프로파일)를 대체하지
않는다. 그쪽 산출물은 지금도 쓰이고 있고, 이 스크립트는 **지점 위치**와 그로부터 얻는
**실제 도로 등급(road_rank)**을 추가한다.

    python scripts/preprocess_traffic_survey_gis.py
"""
from pathlib import Path
import sys

# 로그에 한글과 em-dash가 섞여 있어 Windows 기본 콘솔(cp949)에서 죽는다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.traffic_survey import preprocess_traffic_survey, rank_baseline  # noqa: E402


if __name__ == "__main__":
    meta = preprocess_traffic_survey()
    print(f"\n지점 {meta['n_points']}개 (등급 확보 {meta['n_points_with_rank']}개) / "
          f"시간대 행 {meta['n_hourly_rows']:,}개")

    base = rank_baseline()
    if base.empty:
        print("등급별 기준표를 만들 수 없습니다 — 표준노드링크 전처리를 먼저 하세요.")
        sys.exit(0)
    print("\n=== 등급별 기준 교통량 (양방향 합산, 관측일 기준) ===")
    print(f"{'rank':>5} {'등급':<14}{'지점':>5}{'일교통량(중앙)':>14}{'첨두(중앙)':>12}"
          f"{'승용':>7}{'버스':>7}{'화물':>7}")
    for _, r in base.iterrows():
        print(f"{r['road_rank']:>5} {r['rank_label']:<14}{r['n_points']:>5}"
              f"{r['daily_median']:>14,.0f}{r['peak_median']:>12,.0f}"
              f"{r['share_passenger']:>7.0%}{r['share_bus']:>7.0%}{r['share_truck']:>7.0%}")
    flagged = base[base["sample_bias_note"].str.startswith("⚠️")]
    if not flagged.empty:
        print(f"\n⚠️ 표본 편향 주의 등급: "
              f"{', '.join(f'{int(r.road_rank)}({r.rank_label})' for r in flagged.itertuples())}")
        print("   조사지점이 주요 도로 위주라 이 등급 중 붐비는 곳만 잡혀 있습니다.")
        print("   등급 전체 연장에 그대로 곱하면 교통량이 크게 과대추정됩니다.")
