from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from app.core.config import settings
from app.services.mock_repository import get_roads
from app.services.sumo.sumo_coordinate_converter import SumoCoordinateConverter, parse_boundary


@dataclass(frozen=True)
class SumoEdge:
    id: str
    from_node: str | None
    to_node: str | None
    shape_xy: list[tuple[float, float]]
    shape_lat_lng: list[dict[str, float]]
    length: float
    speed: float
    source: str


@dataclass(frozen=True)
class SumoNetwork:
    edges: list[SumoEdge]
    converter: SumoCoordinateConverter
    source: str
    net_path: str | None
    conversion_status: str


def load_sumo_network() -> SumoNetwork:
    net_path = _resolve_net_path()
    if net_path and net_path.exists():
        return _load_net_xml(net_path)
    return _fallback_network()


def _resolve_net_path() -> Path | None:
    if settings.sumo_net_path:
        return Path(settings.sumo_net_path).expanduser()
    if not settings.sumo_config_path:
        return None
    config_path = Path(settings.sumo_config_path).expanduser()
    if config_path.suffix == ".net.xml":
        return config_path
    if not config_path.exists():
        return None
    root = ET.parse(config_path).getroot()
    net_file = root.find(".//net-file")
    value = net_file.get("value") if net_file is not None else None
    return (config_path.parent / value).resolve() if value else None


def _load_net_xml(path: Path) -> SumoNetwork:
    root = ET.parse(path).getroot()
    location = root.find("location")
    converter = SumoCoordinateConverter(
        conv_boundary=parse_boundary(location.get("convBoundary") if location is not None else None),
        orig_boundary=parse_boundary(location.get("origBoundary") if location is not None else None),
    )
    edges = []
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        lane = edge.find("lane")
        if lane is None:
            continue
        shape_xy = _parse_shape(lane.get("shape", ""))
        if len(shape_xy) < 2:
            continue
        shape_lat_lng = [
            {"lat": lat, "lng": lng}
            for lat, lng in (converter.xy_to_lat_lng(x, y) for x, y in shape_xy)
        ]
        edges.append(
            SumoEdge(
                id=edge_id,
                from_node=edge.get("from"),
                to_node=edge.get("to"),
                shape_xy=shape_xy,
                shape_lat_lng=shape_lat_lng,
                length=float(lane.get("length", "0") or 0),
                speed=float(lane.get("speed", "13.9") or 13.9),
                source="sumo_net",
            )
        )
    if not edges:
        return _fallback_network()
    return SumoNetwork(edges=edges, converter=converter, source="sumo_net", net_path=str(path), conversion_status=converter.status)


def _fallback_network() -> SumoNetwork:
    converter = SumoCoordinateConverter()
    edges = []
    for road in get_roads():
        shape_lat_lng = [{"lat": float(lat), "lng": float(lng)} for lat, lng in road["geometry"]]
        shape_xy = [converter.lat_lng_to_xy(point["lat"], point["lng"]) for point in shape_lat_lng]
        edges.append(
            SumoEdge(
                id=str(road["id"]),
                from_node=f"{road['id']}:from",
                to_node=f"{road['id']}:to",
                shape_xy=shape_xy,
                shape_lat_lng=shape_lat_lng,
                length=float(road.get("distance_km", 1)) * 1000,
                speed=float(road.get("average_speed", 40)) / 3.6,
                source="fallback_mock_network",
            )
        )
    return SumoNetwork(edges=edges, converter=converter, source="fallback_mock_network", net_path=None, conversion_status="fallback_known_map_bbox")


def _parse_shape(value: str) -> list[tuple[float, float]]:
    points = []
    for item in value.split():
        x, y = item.split(",")[:2]
        points.append((float(x), float(y)))
    return points
