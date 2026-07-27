"""24시간 교통량 곡선 → 시뮬레이션 창의 시간대 슬라이스 (문서 §5 6단계).

왜 필요한가 (v2 §5-1):
    통행을 창 전체에 **균등하게** 깔면 정체가 아예 안 생긴다. 수도꼭지에서 물이 일정하게
    나오면 하수구가 안 넘치는 것과 같다. 실제 출근 교통은 몰리고, 그 몰림이 도로 용량을
    넘는 순간 정체가 생겼다가 풀린다. 기지국·RSU 배치의 우열은 **바로 그 구간에서만**
    드러나므로, 창 안에 굴곡을 넣는 것이 6단계의 핵심이다.

곡선 출처: `data/processed/traffic_survey/시간대_프로파일.csv`
    (2010 TVOS 전국 상시조사 → `scripts/preprocess_traffic_survey.py`. 24행 × 등급별 열.)
    아침 첨두 08~09시 6.76%, 저녁 18~19시 6.59%, 새벽 저점 03~04시 0.72% — 약 9배 굴곡.

시간 해상도:
    원본은 **1시간** 단위인데 슬라이스는 보통 15분이다. 시간 단위를 그대로 4등분하면
    07:00~08:00이 전부 같은 값이라 창 안이 사실상 두 개의 평평한 계단이 된다
    (07~08 6.32% vs 08~09 6.76% — 7% 차이뿐). 그래서 기본은 **선형 보간**:
    각 시간대 값을 그 시간의 **중앙**(07~08 → 07:30)에 놓고 잇는다.
    07:00~09:00 창에서 5.00% → 6.76%(08:30 정점) → 6.23%로 이어지는 진짜 상승·하강이 생긴다.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parents[4]

DEFAULT_COLUMN = "전체"


def default_profile_path() -> Path:
    """24h 곡선 CSV 경로. 환경변수 `TRAFFIC_PROFILE_CSV`로 재정의 가능."""
    override = os.getenv("TRAFFIC_PROFILE_CSV")
    if override:
        p = Path(override)
        if p.is_absolute():
            return p
        return (ROOT_DIR / p).resolve()
    return (ROOT_DIR / "data/processed/traffic_survey/시간대_프로파일.csv").resolve()


def load_hourly_profile(
    path: Optional[str] = None,
    column: str = DEFAULT_COLUMN,
) -> list[float]:
    """CSV → 24개 시간대 비율(0시부터). 합은 1에 가깝다.

    column : "전체"(기본) 또는 등급별 열("시도·간선", "기타도로" 등). 구역 성격에 맞는
        등급을 고르면 곡선이 조금 달라지지만, 배치 비교가 목적이라 보통 전체로 충분하다.
    """
    p = Path(path) if path else default_profile_path()
    if not p.exists():
        raise FileNotFoundError(
            f"시간대 프로파일이 없습니다: {p}\n"
            f"  scripts/preprocess_traffic_survey.py 로 생성하세요."
        )
    with open(p, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if column not in (rows[0].keys() if rows else {}):
        raise KeyError(f"열 '{column}' 없음. 사용 가능: {list(rows[0].keys()) if rows else []}")
    shares = [float(r[column]) for r in rows]
    if len(shares) != 24:
        raise ValueError(f"24행이어야 합니다 (현재 {len(shares)}행): {p}")
    return shares


def _rate_at(hour: float, hourly: Sequence[float], interpolate: bool) -> float:
    """시각 `hour`에서의 순간 교통량 비율(시간당).

    interpolate=True : 시간대 값을 그 시간의 **중앙**에 놓고 선형 보간(하루를 순환으로
        이어 자정 경계도 매끄럽게). 0.5시 이전·23.5시 이후는 전날/다음날 값과 잇는다.
    interpolate=False : 계단(그 시간대 값 그대로).
    """
    h = hour % 24.0
    if not interpolate:
        return hourly[int(h) % 24]
    # 시간대 i의 대표 시각 = i + 0.5
    pos = h - 0.5
    i = int(pos // 1.0)
    frac = pos - i
    a = hourly[i % 24]
    b = hourly[(i + 1) % 24]
    return a + (b - a) * frac


def build_time_profile(
    begin_h: float,
    end_h: float,
    step_min: float = 15.0,
    hourly: Optional[Sequence[float]] = None,
    interpolate: bool = True,
    column: str = DEFAULT_COLUMN,
) -> list[tuple[float, float, float]]:
    """시뮬레이션 창을 슬라이스로 쪼개고 각 구간의 통행 비중을 매긴다.

    Returns: [(begin_h, end_h, share)] — share 합 = 1.0. 그대로
        `pipeline.generate_demand(time_profile=...)`에 넘기면 된다.

    비중 계산: 슬라이스 **중앙 시각의 곡선 값 × 슬라이스 길이**. 곡선이 조각별 선형이고
        마디(각 시간의 중앙 = 정시 30분)가 15·30분 슬라이스 경계와 겹치지 않으므로
        중점법이 곧 정확한 적분이다(근사 아님).

    Example
    -------
    >>> build_time_profile(7.0, 9.0, 15)   # 07:00~09:00, 15분 8구간
    [(7.0, 7.25, 0.1046), (7.25, 7.5, 0.1129), ...]
    """
    if end_h <= begin_h:
        raise ValueError(f"end_h({end_h})는 begin_h({begin_h})보다 커야 합니다")
    if step_min <= 0:
        raise ValueError(f"step_min은 양수여야 합니다: {step_min}")
    curve = list(hourly) if hourly is not None else load_hourly_profile(column=column)

    step_h = step_min / 60.0
    slices: list[tuple[float, float, float]] = []
    t = begin_h
    while t < end_h - 1e-9:
        t_end = min(t + step_h, end_h)
        mid = (t + t_end) / 2.0
        weight = _rate_at(mid, curve, interpolate) * (t_end - t)
        slices.append((t, t_end, weight))
        t = t_end

    total = sum(w for _, _, w in slices) or 1.0
    return [(b, e, w / total) for b, e, w in slices]


def profile_summary(slices: Sequence[tuple[float, float, float]], total_trips: float) -> str:
    """슬라이스 분포를 한눈에 — 굴곡이 실제로 생겼는지 확인용.

    최대/최소 비가 1에 가까우면 사실상 균등이라 정체가 안 생긴다(§5-1).
    """
    if not slices:
        return "(빈 프로파일)"
    trips = [s * total_trips for _, _, s in slices]
    peak, low = max(trips), min(trips)
    lines = [f"  슬라이스 {len(slices)}구간 | 최대 {peak:.0f}대 / 최소 {low:.0f}대 "
             f"= {peak / max(low, 1e-9):.2f}배"]
    for (b, e, _), n in zip(slices, trips):
        bar = "#" * int(round(n / max(peak, 1e-9) * 40))
        lines.append(f"    {int(b):02d}:{int(round((b % 1) * 60)):02d}~"
                     f"{int(e):02d}:{int(round((e % 1) * 60)):02d}  {n:6.0f}대  {bar}")
    return "\n".join(lines)
