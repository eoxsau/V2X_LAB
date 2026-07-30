from __future__ import annotations

import xml.etree.ElementTree as ET

from .its_models import ITSTrafficLink


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(elem: ET.Element, name: str) -> str | None:
    for child in list(elem):
        if _local_name(child.tag) == name:
            return child.text.strip() if child.text is not None else None
    return None


def _text_any(elem: ET.Element, *names: str) -> str | None:
    """Try multiple candidate field names (API version differences)."""
    for name in names:
        v = _text(elem, name)
        if v is not None:
            return v
    return None


def parse_vds_traffic_xml(xml_text: str) -> list[ITSTrafficLink]:
    """VDS 검지기 교통량·점유율 파싱 (openapi.its.go.kr /vdsInfo).

    linkId 필드로 표준링크와 연계. volume(대/시), occupancy(%) 포함.
    API 버전에 따라 필드명이 다를 수 있으므로 여러 후보명을 시도.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    records: list[ITSTrafficLink] = []
    for elem in root.iter():
        # VDS 레코드는 vdsId 또는 detectorId 자식 요소로 식별
        children_names = {_local_name(c.tag) for c in list(elem)}
        if not (children_names & {"vdsId", "detectorId", "vds_id"}):
            continue
        try:
            link_id = _text_any(elem, "linkId", "link_id", "linkNo")
            if not link_id:
                continue
            volume_raw = _text_any(elem, "volume", "vehicleCount", "veh_count", "trafficVolume")
            occupancy_raw = _text_any(elem, "occupancy", "occRate", "occ", "occupancyRate")
            speed_raw = _text_any(elem, "avgSpeed", "speed", "avg_speed")
            payload = {_local_name(c.tag): (c.text or "").strip() for c in list(elem)}
            records.append(ITSTrafficLink(
                road_name=_text_any(elem, "roadName", "road_name"),
                direction_type=_text_any(elem, "direction", "drcType"),
                link_no=_text_any(elem, "vdsId", "detectorId"),
                link_id=link_id,
                start_node_id=None,
                end_node_id=None,
                speed_kph=float(speed_raw) if speed_raw not in (None, "") else None,
                travel_time_s=None,
                created_date=_text_any(elem, "collectedAt", "createdDate", "measureTime"),
                volume_veh_per_h=float(volume_raw) if volume_raw not in (None, "") else None,
                occupancy_pct=float(occupancy_raw) if occupancy_raw not in (None, "") else None,
                raw_payload=payload,
            ))
        except (ValueError, TypeError):
            continue
    return records


def parse_its_traffic_xml(xml_text: str) -> list[ITSTrafficLink]:
    root = ET.fromstring(xml_text)
    candidates = []
    for elem in root.iter():
        if any(_local_name(child.tag) == "linkId" for child in list(elem)):
            candidates.append(elem)

    records: list[ITSTrafficLink] = []
    for elem in candidates:
        try:
            speed = _text(elem, "speed")
            travel_time = _text(elem, "travelTime")
            payload = {_local_name(child.tag): (child.text.strip() if child.text else None) for child in list(elem)}
            records.append(
                ITSTrafficLink(
                    road_name=_text(elem, "roadName"),
                    direction_type=_text(elem, "drcType"),
                    link_no=_text(elem, "linkNo"),
                    link_id=_text(elem, "linkId"),
                    start_node_id=_text(elem, "startNodeId"),
                    end_node_id=_text(elem, "endNodeId"),
                    speed_kph=float(speed) if speed not in (None, "") else None,
                    travel_time_s=float(travel_time) if travel_time not in (None, "") else None,
                    created_date=_text(elem, "createdDate"),
                    raw_payload=payload,
                )
            )
        except ValueError:
            continue
    return records
