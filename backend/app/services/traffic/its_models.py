from __future__ import annotations

from dataclasses import dataclass, field


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

