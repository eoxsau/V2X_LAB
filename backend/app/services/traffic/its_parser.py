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
