"""정규화된 교통량조사 parquet 조회 — 지점 검색 + 등급별 기준표 집계.

저장은 **지점 × 방향 × 시각**의 원본 그대로이고, 합산·평균은 전부 여기서 한다.
집계 규칙(양방향 합산 여부, 평균 vs 중앙값)이 바뀔 때 데이터를 다시 만들지 않도록
"뷰"의 역할을 함수로 둔 것이다.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[4]

# xlsx car1..car10의 의미 (원본 데이터 딕셔너리 순서 그대로 보존한다)
CAR_COLUMNS = ["car1", "car2", "car3", "car4", "car5",
               "car6", "car7", "car8", "car9", "car10"]
CAR_LABELS = {
    "car1": "일반승용차", "car2": "승합차", "car3": "택시", "car4": "중형버스",
    "car5": "대형버스", "car6": "이륜차", "car7": "소형화물차", "car8": "중형화물차",
    "car9": "대형화물차", "car10": "컨테이너트레일러",
}
# SUMO vType 구성에 쓸 묶음 — 차종 10개를 그대로 넘기기엔 잘고, 총대수는 너무 뭉갠다
CAR_GROUPS = {
    "passenger": ["car1", "car3"],                        # 승용 + 택시
    "van": ["car2"],
    "bus": ["car4", "car5"],
    "truck": ["car7", "car8", "car9", "car10"],
    "motorcycle": ["car6"],
}

# 표준노드링크 등급 코드 → 이름
ROAD_RANK_LABELS = {
    101: "고속국도", 102: "도시고속국도", 103: "일반국도", 104: "특별·광역시도",
    105: "국가지원지방도", 106: "지방도", 107: "시군도", 108: "구도",
}

# 표준링크 스냅 허용 거리(m). 넘으면 등급을 NULL로 남긴다 — 잘못된 등급이
# 기준표를 오염시키는 것이 지점 몇 개를 잃는 것보다 나쁘다.
MAX_SNAP_DIST_M = 100.0

# 조사지점 표본 편향 — **반드시 함께 읽혀야 하는 경고**다 (2026-07-30 실측).
# 조사지점이 주요 도로 위주라 하위 등급은 "그 등급 중 붐비는 곳"만 잡혀 있다.
# 시군도(107) 중앙값이 지방도(106)의 2배로 나오는 것이 그 증거다 — 전국 시군도 연장이
# 216,000km 중 절반인데 이 값을 그대로 곱하면 교통량이 크게 과대추정된다.
_BIAS_NOTE_MAJOR = "조사지점이 이 등급을 대표한다고 볼 수 있음"
_BIAS_NOTE_MINOR = ("⚠️ 표본 편향 — 조사지점이 주요 도로 위주라 이 등급 중 "
                    "붐비는 곳만 잡혀 있다. 등급 전체에 곱하면 과대추정된다.")
_MINOR_RANKS = (106, 107, 108)


def default_raw_dir() -> Path:
    return ROOT_DIR / "data" / "raw" / "traffic_survey"


def default_processed_dir() -> Path:
    return ROOT_DIR / "data" / "processed" / "traffic_survey"


def standard_links_path() -> Path:
    return ROOT_DIR / "data" / "processed" / "standard_link" / "standard_links.parquet"


class TrafficSurveyRepository:
    """지점·시간대 parquet 조회. 549지점 규모라 통째로 메모리에 올린다."""

    def __init__(self, processed_dir: Optional[Path] = None):
        self.dir = Path(processed_dir) if processed_dir else default_processed_dir()

    @property
    def points_file(self) -> Path:
        return self.dir / "survey_points.parquet"

    @property
    def hourly_file(self) -> Path:
        return self.dir / "survey_hourly.parquet"

    def available(self) -> bool:
        return self.points_file.exists() and self.hourly_file.exists()

    @functools.cached_property
    def points(self) -> gpd.GeoDataFrame:
        if not self.points_file.exists():
            raise FileNotFoundError(
                f"지점 parquet이 없습니다: {self.points_file}\n"
                f"  scripts/preprocess_traffic_survey_gis.py 를 먼저 실행하세요.")
        return gpd.read_parquet(self.points_file)

    @functools.cached_property
    def hourly(self) -> pd.DataFrame:
        if not self.hourly_file.exists():
            raise FileNotFoundError(f"시간대 parquet이 없습니다: {self.hourly_file}")
        return pd.read_parquet(self.hourly_file)

    # ── 조회 ──────────────────────────────────────────────────────────────────

    def points_in_bbox(self, minlng: float, minlat: float,
                       maxlng: float, maxlat: float) -> gpd.GeoDataFrame:
        """bbox 안의 조사지점.

        ⚠️ 대개 **0개가 나온다.** 전국 549지점이라 밀도가 0.01개/km² 수준이고,
        20km² 구역에서 실측 0개 / 최근접 5.7km였다(안양). 구역 안 지점으로 교통량을
        앵커하는 방식은 성립하지 않으니 `nearest_points`나 `rank_baseline`을 쓸 것.
        """
        p = self.points
        return p.cx[minlng:maxlng, minlat:maxlat]

    def nearest_points(self, lat: float, lng: float, k: int = 5) -> gpd.GeoDataFrame:
        """주어진 점에서 가까운 조사지점 k개 (거리 `dist_km` 포함)."""
        p = self.points.to_crs(5186)
        target = gpd.GeoSeries.from_xy([lng], [lat], crs="EPSG:4326").to_crs(5186).iloc[0]
        out = self.points.copy()
        out["dist_km"] = p.distance(target).to_numpy() / 1000.0
        return out.nsmallest(k, "dist_km")

    def daily_by_point(self, combine_directions: bool = True) -> pd.DataFrame:
        """지점별 일교통량(24시간 합) + 차종 묶음.

        combine_directions : True면 양방향 합산(도로 링크가 나르는 총량 — 기본).
            False면 방향별로 남긴다. 저장은 방향별이므로 어느 쪽도 손실 없이 만든다.
        """
        keys = ["survey_code"] + ([] if combine_directions else ["direction"])
        agg = self.hourly.groupby(keys, as_index=False)[CAR_COLUMNS].sum()
        agg["total"] = agg[CAR_COLUMNS].sum(axis=1)
        for group, cols in CAR_GROUPS.items():
            agg[f"share_{group}"] = agg[cols].sum(axis=1) / agg["total"].replace(0, pd.NA)
        return agg

    def peak_hour_by_point(self, combine_directions: bool = True) -> pd.DataFrame:
        """지점별 **첨두 1시간 교통량**(대/시)과 그 시각.

        N* 앵커가 필요한 값은 일교통량이 아니라 이것이다 — 시뮬레이션 창이 첨두이므로.
        """
        keys = ["survey_code", "hour"] + ([] if combine_directions else ["direction"])
        h = self.hourly.groupby(keys, as_index=False)[CAR_COLUMNS].sum()
        h["total"] = h[CAR_COLUMNS].sum(axis=1)
        idx = h.groupby("survey_code")["total"].idxmax()
        return (h.loc[idx, ["survey_code", "hour", "total"]]
                .rename(columns={"hour": "peak_hour", "total": "peak_veh_per_h"})
                .reset_index(drop=True))


# ── 등급별 기준표 ("뷰") ──────────────────────────────────────────────────────

def rank_baseline(repo: Optional[TrafficSurveyRepository] = None,
                  combine_directions: bool = True) -> pd.DataFrame:
    """`road_rank`별 기준 교통량 — N\\* 앵커의 재료.

    저장된 원본에서 매번 계산한다(물리 테이블로 굳히지 않는다). 집계 규칙을 바꿀 때
    데이터를 다시 만들지 않아도 되고, 무엇보다 **표본 편향 경고를 함께 실어 보낼** 수 있다.

    Returns 컬럼:
        road_rank, rank_label, n_points,
        daily_mean, daily_median, peak_mean, peak_median,
        share_passenger/van/bus/truck/motorcycle, sample_bias_note

    ⚠️ `sample_bias_note`를 무시하지 말 것. 하위 등급(지방도·시군도)은 조사지점이
       "그 등급 중 붐비는 곳"만 잡고 있어, 등급 전체 연장에 곱하면 크게 과대추정된다.
    """
    repo = repo or TrafficSurveyRepository()
    pts = repo.points[["survey_code", "road_rank"]].dropna(subset=["road_rank"])
    if pts.empty:
        return pd.DataFrame()
    pts = pts.assign(road_rank=pts["road_rank"].astype(int))

    daily = repo.daily_by_point(combine_directions).merge(pts, on="survey_code")
    peak = repo.peak_hour_by_point(combine_directions).merge(pts, on="survey_code")

    share_cols = [f"share_{g}" for g in CAR_GROUPS]
    g = daily.groupby("road_rank")
    out = pd.DataFrame({
        "n_points": g["survey_code"].size(),
        "daily_mean": g["total"].mean().round(0),
        "daily_median": g["total"].median().round(0),
    })
    for c in share_cols:
        out[c] = g[c].mean().round(3)
    p = peak.groupby("road_rank")["peak_veh_per_h"]
    out["peak_mean"] = p.mean().round(0)
    out["peak_median"] = p.median().round(0)

    out = out.reset_index()
    out["rank_label"] = out["road_rank"].map(ROAD_RANK_LABELS).fillna("?")
    out["sample_bias_note"] = [
        _BIAS_NOTE_MINOR if r in _MINOR_RANKS else _BIAS_NOTE_MAJOR
        for r in out["road_rank"]
    ]
    cols = ["road_rank", "rank_label", "n_points", "daily_mean", "daily_median",
            "peak_mean", "peak_median", *share_cols, "sample_bias_note"]
    return out[cols].sort_values("road_rank").reset_index(drop=True)
