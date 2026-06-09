from __future__ import annotations

from dataclasses import asdict

import geopandas as gpd

from .its_models import ITSTrafficLink, MatchedTrafficLink


def _safe_speed_ratio(speed_kph: float | None, max_speed: float | None, road_type: str | None) -> float:
    default_speed = 50.0
    if road_type == "001":
        default_speed = 80.0
    if road_type == "101":
        default_speed = 30.0
    base = max_speed or default_speed
    speed = speed_kph or base
    if base <= 0:
        return 1.0
    return max(0.0, min(speed / base, 1.5))


def _congestion_score(speed_kph: float | None, travel_time_s: float | None, link_row: dict) -> float:
    ratio = _safe_speed_ratio(speed_kph, link_row.get("max_speed"), link_row.get("road_type"))
    score = max(0.0, min(1.0, 1 - ratio))
    if travel_time_s and link_row.get("length_m") and speed_kph:
        expected = link_row["length_m"] / max(speed_kph / 3.6, 0.1)
        if travel_time_s > expected * 1.2:
            score = min(1.0, score + 0.15)
    return round(score, 4)


def match_its_to_standard_links(records: list[ITSTrafficLink], links_gdf: gpd.GeoDataFrame) -> tuple[list[MatchedTrafficLink], dict]:
    link_by_id = {str(row.link_id): row for row in links_gdf.itertuples()}
    link_by_nodes = {(str(row.from_node), str(row.to_node)): row for row in links_gdf.itertuples()}
    road_name_groups: dict[str, list] = {}
    for row in links_gdf.itertuples():
        if row.road_name:
            road_name_groups.setdefault(str(row.road_name), []).append(row)

    matched: list[MatchedTrafficLink] = []
    stats = {
        "matched_by_link_id": 0,
        "matched_by_node_pair": 0,
        "matched_by_road_name": 0,
        "unmatched_count": 0,
    }

    for record in records:
        link_row = None
        method = None
        confidence = 0.0

        if record.link_id and str(record.link_id) in link_by_id:
            link_row = link_by_id[str(record.link_id)]
            method = "link_id"
            confidence = 1.0
            stats["matched_by_link_id"] += 1
        elif record.start_node_id and record.end_node_id and (str(record.start_node_id), str(record.end_node_id)) in link_by_nodes:
            link_row = link_by_nodes[(str(record.start_node_id), str(record.end_node_id))]
            method = "node_pair"
            confidence = 0.92
            stats["matched_by_node_pair"] += 1
        elif record.road_name and str(record.road_name) in road_name_groups:
            link_row = road_name_groups[str(record.road_name)][0]
            method = "road_name"
            confidence = 0.45
            stats["matched_by_road_name"] += 1

        if link_row is None:
            stats["unmatched_count"] += 1
            continue

        link_dict = link_row._asdict()
        matched.append(
            MatchedTrafficLink(
                its_link_id=record.link_id,
                standard_link_id=str(link_dict["link_id"]),
                road_name=record.road_name or link_dict.get("road_name"),
                speed_kph=record.speed_kph,
                travel_time_s=record.travel_time_s,
                congestion_score=_congestion_score(record.speed_kph, record.travel_time_s, link_dict),
                geometry_wkt=link_dict["geometry"].wkt if link_dict.get("geometry") is not None else None,
                match_method=method,
                confidence=confidence,
            )
        )

    stats["its_records_count"] = len(records)
    return matched, stats


def matched_to_dict(match: MatchedTrafficLink) -> dict:
    return asdict(match)

