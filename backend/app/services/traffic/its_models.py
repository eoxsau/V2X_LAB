from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def classify_time_period(dt: datetime | None = None) -> str:
    """평일 07:00-09:00, 18:00-20:00(KST) = 첨두시(peak), 그 외 = 비첨두시(off_peak).

    naive datetime은 이미 KST라고 가정(호출부가 datetime.now(KST)를 넘기는 게 기본 경로),
    aware datetime은 KST로 변환한다.
    """
    if dt is None:
        dt = datetime.now(KST)
    elif dt.tzinfo is not None:
        dt = dt.astimezone(KST)
    if dt.weekday() >= 5:  # 토(5)/일(6)
        return "off_peak"
    hm = dt.hour * 60 + dt.minute
    am_peak = (7 * 60) <= hm < (9 * 60)
    pm_peak = (18 * 60) <= hm < (20 * 60)
    return "peak" if (am_peak or pm_peak) else "off_peak"


@dataclass
class ITSTrafficLink:
    road_name: str | None
    direction_type: str | None
    link_no: str | None
    link_id: str | None
    start_node_id: str | None
    end_node_id: str | None
    speed_kph: float | None
    travel_time_s: float | None
    created_date: str | None
    raw_payload: dict = field(default_factory=dict)


@dataclass
class MatchedTrafficLink:
    its_link_id: str | None
    standard_link_id: str | None
    road_name: str | None
    speed_kph: float | None
    travel_time_s: float | None
    congestion_score: float
    geometry_wkt: str | None
    match_method: str
    confidence: float

