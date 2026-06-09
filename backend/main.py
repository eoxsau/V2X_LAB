"""
V2X AI Routing Lab — FastAPI Backend
"""
import asyncio
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from heapq import heappop, heappush
from pathlib import Path
from tempfile import gettempdir
from typing import Optional
from uuid import uuid4

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from app.db import (
    count_network_nodes,
    create_simulation_run,
    delete_network_node,
    delete_synthetic_network_nodes,
    delete_user_created_network_nodes,
    fetch_network_nodes,
    finish_simulation_run,
    insert_network_node,
    max_user_station_number,
    postgis_available,
    upsert_network_nodes,
)
from app.services.standard_link.standard_link_preprocessor import preprocess_standard_links
from app.services.standard_link.standard_link_repository import StandardLinkRepository
from app.services.buildings.building_preprocessor import preprocess_buildings
from app.services.buildings.building_repository import BuildingRepository
from app.services.buildings.building_obstruction_analyzer import analyze_candidates
from app.services.traffic.its_cache import ITS_CACHE
from app.services.traffic.traffic_fusion_engine import TRAFFIC_FUSION_ENGINE

load_dotenv(Path(__file__).parent / ".env")

# ── SUMO paths ──────────────────────────────────────────────────────────────
def candidate_sumo_homes() -> list[Path]:
    env_home = os.environ.get("SUMO_HOME")
    candidates: list[Path] = []
    if env_home:
        candidates.append(Path(env_home))

    system = platform.system()
    if system == "Windows":
        candidates.extend(
            [
                Path(r"C:\Program Files (x86)\Eclipse\Sumo"),
                Path(r"C:\Program Files\Eclipse\Sumo"),
            ]
        )
    elif system == "Darwin":
        candidates.extend(
            [
                Path("/opt/homebrew/opt/sumo/share/sumo"),
                Path("/usr/local/opt/sumo/share/sumo"),
                Path("/Applications/SUMO.app/Contents/Resources/sumo"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("/usr/share/sumo"),
                Path("/usr/local/share/sumo"),
                Path("/opt/sumo/share/sumo"),
            ]
        )
    return candidates


def resolve_sumo_home() -> Path | None:
    for candidate in candidate_sumo_homes():
        if (candidate / "tools").exists() and (
            (candidate / "bin" / "sumo").exists()
            or (candidate / "bin" / "sumo.exe").exists()
        ):
            return candidate
    return None


SUMO_HOME_PATH = resolve_sumo_home()
SUMO_HOME = str(SUMO_HOME_PATH) if SUMO_HOME_PATH else os.environ.get("SUMO_HOME", "")
SUMO_BIN = SUMO_HOME_PATH / "bin" if SUMO_HOME_PATH else Path()
SUMO_TOOLS = SUMO_HOME_PATH / "tools" if SUMO_HOME_PATH else Path()

# Add SUMO tools to Python path so traci/sumolib are importable
if SUMO_HOME_PATH and SUMO_TOOLS.exists() and str(SUMO_TOOLS) not in sys.path:
    sys.path.insert(0, str(SUMO_TOOLS))

try:
    import traci
    import sumolib
    TRACI_AVAILABLE = True
except ImportError:
    TRACI_AVAILABLE = False
    print("[WARN] traci/sumolib not found — check SUMO_HOME and tools/ path")


def resolve_binary(binary_name: str) -> str | None:
    exe_name = f"{binary_name}.exe" if platform.system() == "Windows" else binary_name
    if SUMO_HOME_PATH:
        bundled = SUMO_BIN / exe_name
        if bundled.exists():
            return str(bundled)
    return shutil.which(binary_name) or shutil.which(exe_name)


if SUMO_HOME:
    os.environ["SUMO_HOME"] = SUMO_HOME

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="V2X Routing Lab")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _cleanup_synthetic_nodes() -> None:
    """Remove any synthetic nodes left in the DB from previous sessions."""
    try:
        removed = delete_synthetic_network_nodes()
        if removed:
            print(f"[startup] Removed {removed} stale synthetic network node(s) from DB", flush=True)
    except Exception:
        pass

WORK_DIR = Path(__file__).parent / "networks"
WORK_DIR.mkdir(exist_ok=True)
MAX_SETUP_AREA_KM2 = 25.0
DEFAULT_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
OSM_MAP_API_URL = "https://api.openstreetmap.org/api/0.6/map"

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

# ── Global state ──────────────────────────────────────────────────────────────
_state = {
    "network_ready": False,
    "net_file": None,      # path to .net.xml
    "osm_file": None,      # path to downloaded .osm
    "mock_graph": None,    # parsed OSM road graph for fallback mode
    "sim_running": False,
    "vehicle_pos": None,   # {"lat": float, "lng": float, "progress": float}
    "route_edges": [],     # list of SUMO edge IDs
    "route_coords": [],    # list of [lat, lng] for the full route polyline
    "sim_mode": "idle",    # idle | sumo | mock
    "error": None,
    "warning": None,
    "current_bbox": None,
    "traffic_sync": None,
    "download_log": [],
    "network_nodes": [],
    "synthetic_network_nodes": [],
    "route_buildings": None,
    "network_telemetry": None,
    "building_debug": {"sample_links": [], "warnings": []},
    "simulation_run_id": None,
}
_ws_clients: list[WebSocket] = []
_sim_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_runtime_probe_cache: dict[str, dict] = {}
_network_lock = threading.Lock()


# ── Models ───────────────────────────────────────────────────────────────────
class BBox(BaseModel):
    s: float
    w: float
    n: float
    e: float

class SetupRequest(BaseModel):
    bbox: BBox

class SimStartRequest(BaseModel):
    origin: dict   # {"lat": float, "lng": float}
    dest:   dict   # {"lat": float, "lng": float}


class TrafficSyncRequest(BaseModel):
    bbox: dict
    type: str = "all"


class BuildingBBoxRequest(BaseModel):
    min_lng: float
    min_lat: float
    max_lng: float
    max_lat: float


BUILDING_REPOSITORY = BuildingRepository()


# ── Helpers ───────────────────────────────────────────────────────────────────
def run_cmd(args: list, cwd=None, extra_env: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd, env=env,
        encoding="utf-8", errors="replace"
    )
    return result.returncode, result.stdout, result.stderr


def expand_bbox(bbox: BBox, margin_deg: float = 0.0025) -> BBox:
    return BBox(
        s=max(-90.0, bbox.s - margin_deg),
        w=max(-180.0, bbox.w - margin_deg),
        n=min(90.0, bbox.n + margin_deg),
        e=min(180.0, bbox.e + margin_deg),
    )


def probe_runtime(binary_name: str) -> dict:
    cached = _runtime_probe_cache.get(binary_name)
    if cached:
        return cached

    path = resolve_binary(binary_name)
    if not path:
        status = {"ok": False, "path": None, "error": f"{binary_name} not found"}
        _runtime_probe_cache[binary_name] = status
        return status

    try:
        rc, out, err = run_cmd([path, "--help"])
        status = {
            "ok": rc == 0,
            "path": path,
            "error": None if rc == 0 else (err or out or f"{binary_name} exited with rc={rc}")[:400],
        }
    except Exception as exc:
        status = {"ok": False, "path": path, "error": str(exc)}

    _runtime_probe_cache[binary_name] = status
    return status


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bbox_area_km2(bbox: "BBox") -> float:
    return (
        (bbox.n - bbox.s) * 111 *
        (bbox.e - bbox.w) * 111 * abs((bbox.n + bbox.s) / 2 * 3.14159 / 180)
    )


def split_bbox_grid(bbox: "BBox", cols: int = 2, rows: int = 2) -> list["BBox"]:
    lat_step = (bbox.n - bbox.s) / rows
    lng_step = (bbox.e - bbox.w) / cols
    parts = []
    for row in range(rows):
        for col in range(cols):
            south = bbox.s + lat_step * row
            north = bbox.n if row == rows - 1 else bbox.s + lat_step * (row + 1)
            west = bbox.w + lng_step * col
            east = bbox.e if col == cols - 1 else bbox.w + lng_step * (col + 1)
            parts.append(BBox(s=south, w=west, n=north, e=east))
    return parts


def _merge_osm_xml(chunks: list[bytes]) -> bytes:
    if not chunks:
        raise RuntimeError("병합할 OSM 데이터가 없습니다.")

    base_root = ET.Element("osm", version="0.6", generator="V2X-Routing-Lab")
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        root = ET.fromstring(chunk)
        for child in list(root):
            obj_id = child.attrib.get("id")
            key = (child.tag, obj_id or "")
            if key in seen:
                continue
            seen.add(key)
            base_root.append(child)

    return ET.tostring(base_root, encoding="utf-8", xml_declaration=True)


def _validate_osm_content(content: bytes) -> None:
    if b"<remark>" in content and b"runtime error" in content:
        raise RuntimeError("Overpass 서버 오류 (메모리/타임아웃 초과).")
    if b"<way" not in content and b"<node" not in content:
        raise RuntimeError("응답에 도로 데이터가 없습니다.")


def _overpass_urls() -> list[str]:
    raw = os.getenv("OVERPASS_API_URLS", "").strip()
    if not raw:
        return DEFAULT_OVERPASS_URLS
    return [item.strip() for item in raw.split(",") if item.strip()]


def _request_overpass(query: str, url: str, headers: dict, timeout_s: int) -> bytes:
    resp = requests.post(url, data={"data": query}, headers=headers, timeout=timeout_s)
    if resp.status_code == 406:
        resp = requests.get(url, params={"data": query}, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def _request_osm_map(bbox: "BBox", headers: dict, timeout_s: int) -> bytes:
    resp = requests.get(
        OSM_MAP_API_URL,
        params={"bbox": f"{bbox.w},{bbox.s},{bbox.e},{bbox.n}"},
        headers=headers,
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return resp.content


def route_bbox(route_coords: list[list[float]], padding_deg: float = 0.0015) -> dict | None:
    if not route_coords:
        return None
    lats = [lat for lat, _ in route_coords]
    lngs = [lng for _, lng in route_coords]
    return {
        "min_lat": min(lats) - padding_deg,
        "max_lat": max(lats) + padding_deg,
        "min_lng": min(lngs) - padding_deg,
        "max_lng": max(lngs) + padding_deg,
    }


def load_route_buildings(
    route_coords: list[list[float]],
    network_nodes: list[dict] | None = None,
    padding_deg: float = 0.0015,
) -> tuple[object | None, dict]:
    bbox = route_bbox(route_coords, padding_deg=padding_deg)
    if not bbox:
        return None, {"sample_links": [], "warnings": ["Route bbox is unavailable."]}
    if not BUILDING_REPOSITORY.processed_ready():
        return None, {"sample_links": [], "warnings": ["Processed building data is not ready."]}
    # Vehicle-to-node lines can run far outside the route's own bbox (network nodes
    # are placed relative to the loaded map area, not the route), so widen the query
    # bbox to also cover every node — otherwise those segments cross buildings the
    # repository never loaded and the obstruction analysis silently reports zero.
    for node in network_nodes or []:
        lat, lng = node.get("lat"), node.get("lng")
        if lat is None or lng is None:
            continue
        bbox["min_lat"] = min(bbox["min_lat"], lat - padding_deg)
        bbox["max_lat"] = max(bbox["max_lat"], lat + padding_deg)
        bbox["min_lng"] = min(bbox["min_lng"], lng - padding_deg)
        bbox["max_lng"] = max(bbox["max_lng"], lng + padding_deg)
    gdf = BUILDING_REPOSITORY.query_by_bbox(
        bbox["min_lng"], bbox["min_lat"], bbox["max_lng"], bbox["max_lat"]
    )
    height_available = int((gdf["height_source"] == "height_field").sum()) if not gdf.empty and "height_source" in gdf else 0
    height_estimated = int(len(gdf) - height_available) if not gdf.empty else 0
    return gdf, {
        "buildings_loaded": int(len(gdf)),
        "height_available_count": height_available,
        "height_estimated_count": height_estimated,
        "sample_links": [],
        "warnings": [],
    }


def generate_network_nodes_for_bbox(bbox: dict | None) -> list[dict]:
    if not bbox:
        return []
    center_lat = (bbox["s"] + bbox["n"]) / 2
    center_lng = (bbox["w"] + bbox["e"]) / 2
    lat_span = max((bbox["n"] - bbox["s"]) / 3, 0.0004)
    lng_span = max((bbox["e"] - bbox["w"]) / 3, 0.0004)
    return [
        {
            "id": "BS-01",
            "name": "BS-01",
            "type": "base_station",
            "lat": center_lat + lat_span,
            "lng": center_lng - lng_span,
            "edge_latency_ms": 4.0,
            "coverage_radius_m": 450.0,
            "congestion_penalty": 8.0,
            "capacity": 120.0,
            "load": 42.0,
            "source": "synthetic",
        },
        {
            "id": "RSU-01",
            "name": "RSU-01",
            "type": "roadside_unit",
            "lat": center_lat - lat_span * 0.2,
            "lng": center_lng + lng_span * 0.7,
            "edge_latency_ms": 2.5,
            "coverage_radius_m": 220.0,
            "congestion_penalty": 5.0,
            "capacity": 80.0,
            "load": 28.0,
            "source": "synthetic",
        },
        {
            "id": "EDGE-01",
            "name": "EDGE-01",
            "type": "edge_node",
            "lat": center_lat - lat_span,
            "lng": center_lng - lng_span * 0.3,
            "edge_latency_ms": 1.8,
            "coverage_radius_m": 320.0,
            "congestion_penalty": 3.5,
            "capacity": 150.0,
            "load": 18.0,
            "source": "synthetic",
        },
    ]


def db_node_to_candidate(row: dict) -> dict:
    """Convert a network_nodes DB row into the candidate-node shape used by the analyzer."""
    congestion = float(row.get("congestion_score") or 0.0)
    return {
        "id": row["id"],
        "name": row.get("name") or row["id"],
        "type": row.get("node_type"),
        "lat": float(row["lat"]),
        "lng": float(row["lng"]),
        "capacity": float(row.get("capacity") or 100.0),
        "load": float(row.get("load") or 0.0),
        "congestion_score": congestion,
        "congestion_penalty": congestion,
        "edge_latency_ms": float(row.get("edge_latency_ms") or 5.0),
        "coverage_radius_m": float(row.get("coverage_radius_m") or 500.0),
        "source": row.get("source", "user_created"),
    }


def merged_network_nodes() -> list[dict]:
    """User-created stations from DB; fall back to synthetic nodes only when none exist."""
    user_nodes = [db_node_to_candidate(row) for row in fetch_network_nodes(source="user_created")]
    if user_nodes:
        return user_nodes
    return list(_state.get("synthetic_network_nodes") or [])


def update_network_telemetry(vehicle_pos: dict | None) -> None:
    if not vehicle_pos:
        _state["network_telemetry"] = None
        return
    nodes = _state.get("network_nodes") or []
    if not nodes:
        _state["network_telemetry"] = None
        return
    buildings_gdf = _state.get("route_buildings")
    route_coords = _state.get("route_coords") or []
    density_penalty = round(max(len(route_coords) / 120.0, 1.0), 2)
    candidates = analyze_candidates(
        vehicle_id="veh0",
        vehicle_lat=vehicle_pos["lat"],
        vehicle_lng=vehicle_pos["lng"],
        candidate_nodes=nodes,
        buildings_gdf=buildings_gdf if buildings_gdf is not None else BUILDING_REPOSITORY.query_by_bbox(0, 0, 0, 0),
        vehicle_density_penalty=density_penalty,
    )
    if not candidates:
        _state["network_telemetry"] = None
        return
    best = candidates[0]
    selected_node = best["node"]
    selected_name = selected_node.get("name") or selected_node["id"]
    _state["network_telemetry"] = {
        "connected_node": {
            "id": selected_node["id"],
            "name": selected_name,
            "type": selected_node["type"],
            "lat": selected_node["lat"],
            "lng": selected_node["lng"],
            "congestion_score": selected_node.get("congestion_score", selected_node.get("congestion_penalty", 0.0)),
        },
        "network_nodes": [
            {
                "id": node["id"],
                "name": node.get("name") or node["id"],
                "node_type": node.get("type"),
                "lat": node["lat"],
                "lng": node["lng"],
                "source": node.get("source", "synthetic"),
                "congestion_score": node.get("congestion_score", node.get("congestion_penalty", 0.0)),
                "load": node.get("load", 0.0),
            }
            for node in nodes
        ],
        "ego_vehicle": {
            "connected_network_node_id": selected_node["id"],
            "connected_network_node_name": selected_name,
            "distance_to_network_node_m": best["distance_m"],
            "current_latency_ms": best["predicted_latency_ms"],
        },
        "connection_lines": [
            {
                "vehicle_id": "veh0",
                "network_node_id": selected_node["id"],
                "from": {"lat": vehicle_pos["lat"], "lng": vehicle_pos["lng"]},
                "to": {"lat": selected_node["lat"], "lng": selected_node["lng"]},
                "latency_ms": best["predicted_latency_ms"],
            }
        ],
        "candidate_nodes": [
            {
                "id": item["node"]["id"],
                "name": item["node"].get("name") or item["node"]["id"],
                "type": item["node"]["type"],
                "lat": item["node"]["lat"],
                "lng": item["node"]["lng"],
                "predicted_latency_ms": item["predicted_latency_ms"],
                "node_score": item["node_score"],
                "distance_m": item["distance_m"],
                "confidence": item["confidence"],
                "congestion_score": item["node"].get("congestion_score", item["node"].get("congestion_penalty", 0.0)),
            }
            for item in candidates[:3]
        ],
        "distance_m": best["distance_m"],
        "intersected_building_count": best["intersected_building_count"],
        "max_building_height_m": best["max_building_height_m"],
        "estimated_penetration_loss_db": best["estimated_penetration_loss_db"],
        "latency_penalty_ms": best["latency_penalty_ms"],
        "latency_ms": best["predicted_latency_ms"],
        "stability_score": best["stability_score"],
        "vehicle_density_penalty_ms": density_penalty,
        "connection_line": [
            {"lat": vehicle_pos["lat"], "lng": vehicle_pos["lng"]},
            {"lat": selected_node["lat"], "lng": selected_node["lng"]},
        ],
        "highlighted_buildings": best["highlighted_buildings"],
    }
    _state["building_debug"]["sample_links"] = [
        {
            "vehicle_id": "veh0",
            "network_node_id": item["node"]["id"],
            "distance_m": item["distance_m"],
            "intersected_buildings": item["intersected_building_count"],
            "loss_db": item["estimated_penetration_loss_db"],
            "latency_penalty_ms": item["latency_penalty_ms"],
        }
        for item in candidates[:3]
    ]


def load_mock_graph(osm_file: Path) -> dict:
    tree = ET.parse(osm_file)
    root = tree.getroot()

    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        nid = node.attrib.get("id")
        lat = node.attrib.get("lat")
        lon = node.attrib.get("lon")
        if nid and lat and lon:
            nodes[nid] = (float(lat), float(lon))

    graph_nodes: dict[str, dict] = {}
    adjacency: dict[str, list[tuple[str, float]]] = {}

    for way in root.findall("way"):
        tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in way.findall("tag")}
        if "highway" not in tags:
            continue

        refs = [nd.attrib.get("ref") for nd in way.findall("nd")]
        refs = [ref for ref in refs if ref in nodes]
        if len(refs) < 2:
            continue

        for ref in refs:
            lat, lng = nodes[ref]
            graph_nodes[ref] = {"lat": lat, "lng": lng}
            adjacency.setdefault(ref, [])

        for a, b in zip(refs, refs[1:]):
            alat, alng = nodes[a]
            blat, blng = nodes[b]
            dist = haversine_m(alat, alng, blat, blng)
            adjacency[a].append((b, dist))
            # Fallback mode favors route continuity over strict traffic legality.
            adjacency[b].append((a, dist))

    if not graph_nodes:
        raise RuntimeError("OSM fallback graph를 만들 수 없습니다. bbox를 더 작게 선택해주세요.")

    graph = {"nodes": graph_nodes, "adjacency": adjacency}
    stitch_mock_graph(graph)
    return graph


def nearest_mock_node(graph: dict, lat: float, lng: float) -> str:
    best_id = None
    best_dist = float("inf")
    for node_id, data in graph["nodes"].items():
        dist = haversine_m(lat, lng, data["lat"], data["lng"])
        if dist < best_dist:
            best_dist = dist
            best_id = node_id
    if not best_id:
        raise RuntimeError("가까운 OSM fallback road node를 찾을 수 없습니다.")
    return best_id


def graph_components(graph: dict) -> list[list[str]]:
    seen = set()
    components = []
    for node_id in graph["nodes"]:
        if node_id in seen:
            continue
        stack = [node_id]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            for nxt, _ in graph["adjacency"].get(cur, []):
                if nxt not in seen:
                    stack.append(nxt)
        components.append(comp)
    return components


def stitch_mock_graph(graph: dict, max_bridge_m: float = 250.0) -> None:
    components = graph_components(graph)
    if len(components) <= 1:
        return

    components.sort(key=len, reverse=True)
    main_set = set(components[0])

    for comp in components[1:]:
        best_pair = None
        best_dist = float("inf")
        for node_a in comp:
            a = graph["nodes"][node_a]
            for node_b in main_set:
                b = graph["nodes"][node_b]
                dist = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
                if dist < best_dist:
                    best_dist = dist
                    best_pair = (node_a, node_b)
        if best_pair and best_dist <= max_bridge_m:
            a, b = best_pair
            graph["adjacency"].setdefault(a, []).append((b, best_dist))
            graph["adjacency"].setdefault(b, []).append((a, best_dist))
            main_set.update(comp)


def shortest_mock_path(graph: dict, start_id: str, end_id: str) -> list[str]:
    pq = [(0.0, start_id)]
    dist = {start_id: 0.0}
    prev: dict[str, str | None] = {start_id: None}
    seen = set()

    while pq:
        cost, node_id = heappop(pq)
        if node_id in seen:
            continue
        seen.add(node_id)
        if node_id == end_id:
            break
        for nxt, weight in graph["adjacency"].get(node_id, []):
            new_cost = cost + weight
            if new_cost < dist.get(nxt, float("inf")):
                dist[nxt] = new_cost
                prev[nxt] = node_id
                heappush(pq, (new_cost, nxt))

    if end_id not in prev:
        raise RuntimeError("OSM fallback graph에서 경로를 찾을 수 없습니다. bbox를 더 작게 선택해주세요.")

    path = []
    cur: str | None = end_id
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return path


def mock_route_coords(graph: dict, path: list[str]) -> list[list[float]]:
    return [[graph["nodes"][node_id]["lat"], graph["nodes"][node_id]["lng"]] for node_id in path]


def mock_simulation_thread(route_coords: list[list[float]], stop_evt: threading.Event):
    global _state
    if len(route_coords) < 2:
        _state["error"] = "mock 경로 좌표가 충분하지 않습니다."
        _state["sim_running"] = False
        return

    segment_lengths = []
    total_dist = 0.0
    for a, b in zip(route_coords, route_coords[1:]):
        dist = haversine_m(a[0], a[1], b[0], b[1])
        segment_lengths.append(dist)
        total_dist += dist

    speed_mps = 9.0
    travelled = 0.0
    step = 0

    while not stop_evt.is_set():
        travelled = min(total_dist, travelled + speed_mps * 0.2)
        step += 1

        remaining = travelled
        lat = route_coords[-1][0]
        lng = route_coords[-1][1]

        for idx, seg_len in enumerate(segment_lengths):
            start = route_coords[idx]
            end = route_coords[idx + 1]
            if remaining <= seg_len or idx == len(segment_lengths) - 1:
                ratio = 0.0 if seg_len == 0 else min(1.0, remaining / seg_len)
                lat = start[0] + (end[0] - start[0]) * ratio
                lng = start[1] + (end[1] - start[1]) * ratio
                break
            remaining -= seg_len

        _state["vehicle_pos"] = {
            "lat": lat,
            "lng": lng,
            "speed": round(speed_mps * 3.6, 1),
            "progress": round(0.0 if total_dist == 0 else travelled / total_dist, 3),
            "step": step,
            "arrived": travelled >= total_dist,
        }
        update_network_telemetry(_state["vehicle_pos"])

        if travelled >= total_dist:
            _state["sim_running"] = False
            return

        time.sleep(0.1)


def overpass_download(bbox: BBox, out_file: Path):
    """Download OSM data for bbox via Overpass API with fallback sources and tiling."""
    query = (
        f"[out:xml][timeout:90];"
        f"(way[\"highway\"]({bbox.s},{bbox.w},{bbox.n},{bbox.e});"
        f">;);"
        f"out body;"
    )
    headers = {
        "User-Agent": "V2X-Routing-Lab/1.0 (research project)",
        "Accept": "application/xml, text/xml, */*",
    }
    log_entries: list[dict] = []
    _state["download_log"] = log_entries
    area_km2 = bbox_area_km2(bbox)
    print(
        f"[OSM] Downloading bbox: S={bbox.s:.4f} W={bbox.w:.4f} N={bbox.n:.4f} E={bbox.e:.4f} area={area_km2:.2f}km²",
        flush=True,
    )

    def attempt_full_download() -> bytes | None:
        last_error = None
        for url in _overpass_urls():
            start = time.perf_counter()
            try:
                content = _request_overpass(query, url, headers, 120)
                _validate_osm_content(content)
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                log_entries.append({"source": "overpass", "url": url, "status": "ok", "elapsed_ms": elapsed_ms})
                return content
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                last_error = str(exc)
                log_entries.append({"source": "overpass", "url": url, "status": "error", "elapsed_ms": elapsed_ms, "error": last_error[:300]})

        start = time.perf_counter()
        try:
            content = _request_osm_map(bbox, headers, 120)
            _validate_osm_content(content)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            log_entries.append({"source": "osm_map", "url": OSM_MAP_API_URL, "status": "ok", "elapsed_ms": elapsed_ms})
            return content
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            last_error = str(exc)
            log_entries.append({"source": "osm_map", "url": OSM_MAP_API_URL, "status": "error", "elapsed_ms": elapsed_ms, "error": last_error[:300]})
        if last_error:
            raise RuntimeError(last_error)
        return None

    try:
        content = attempt_full_download()
        if content is None:
            raise RuntimeError("OSM road data download returned no content.")
    except Exception as full_exc:
        log_entries.append({"source": "fallback", "status": "split_bbox", "reason": str(full_exc)[:300]})
        chunks: list[bytes] = []
        split_parts = split_bbox_grid(bbox, cols=2, rows=2)
        for idx, part in enumerate(split_parts, start=1):
            part_query = (
                f"[out:xml][timeout:90];"
                f"(way[\"highway\"]({part.s},{part.w},{part.n},{part.e});"
                f">;);"
                f"out body;"
            )
            part_content = None
            for url in _overpass_urls():
                start = time.perf_counter()
                try:
                    part_content = _request_overpass(part_query, url, headers, 90)
                    _validate_osm_content(part_content)
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                    log_entries.append({"source": "overpass_split", "part": idx, "url": url, "status": "ok", "elapsed_ms": elapsed_ms})
                    break
                except Exception as exc:
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                    log_entries.append({"source": "overpass_split", "part": idx, "url": url, "status": "error", "elapsed_ms": elapsed_ms, "error": str(exc)[:300]})
            if part_content is None:
                start = time.perf_counter()
                try:
                    part_content = _request_osm_map(part, headers, 90)
                    _validate_osm_content(part_content)
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                    log_entries.append({"source": "osm_map_split", "part": idx, "url": OSM_MAP_API_URL, "status": "ok", "elapsed_ms": elapsed_ms})
                except Exception as exc:
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                    log_entries.append({"source": "osm_map_split", "part": idx, "url": OSM_MAP_API_URL, "status": "error", "elapsed_ms": elapsed_ms, "error": str(exc)[:300]})
                    raise RuntimeError(
                        f"도로망 다운로드 실패: 전체 bbox와 분할 bbox 모두 실패했습니다. 마지막 오류: {exc}"
                    )
            chunks.append(part_content)
        content = _merge_osm_xml(chunks)
        _validate_osm_content(content)

    out_file.write_bytes(content)
    size_kb = len(content) / 1024
    print(f"[OSM] Downloaded {size_kb:.1f} KB → {out_file}", flush=True)


def netconvert(osm_file: Path, net_file: Path):
    """Convert OSM to SUMO network with netconvert."""
    netconvert_bin = resolve_binary("netconvert")
    if not netconvert_bin:
        raise RuntimeError(
            "netconvert를 찾을 수 없습니다. SUMO를 설치하고 SUMO_HOME을 설정하거나 PATH에 sumo/bin을 추가해주세요."
        )
    log_file = Path(gettempdir()) / f"netconvert-{net_file.stem}.log"
    args = [
        netconvert_bin,
        "--osm-files", str(osm_file),
        "--output-file", str(net_file),
        "--geometry.remove",
        "--roundabouts.guess",
        "--ramps.guess",
        "--junctions.join",
        "--tls.guess-signals",
        "--tls.discard-loaded",
        "--tls.discard-simple",
        "--no-turnarounds.except-deadend",
        "--no-warnings",
        "--log", str(log_file),
    ]
    print(f"[NET] Running netconvert: {osm_file.name} → {net_file.name}", flush=True)
    rc, out, err = run_cmd(args, extra_env={"SUMO_HOME": SUMO_HOME} if SUMO_HOME else None)
    if rc != 0:
        raise RuntimeError(f"netconvert 실패 (rc={rc}):\n{err[-2000:]}")
    print(f"[NET] netconvert done → {net_file}", flush=True)


def nearest_edge(net, lat: float, lng: float) -> str:
    """Find the nearest drivable edge to a geo coordinate."""
    # sumolib.net works in projected XY (metres), NOT lat/lon degrees
    x, y = net.convertLonLat2XY(lng, lat)
    edges = net.getNeighboringEdges(x, y, r=300, includeJunctions=False)
    if not edges:
        edges = net.getNeighboringEdges(x, y, r=800, includeJunctions=False)
    if not edges:
        raise RuntimeError(f"주변 300m 내 도로를 찾을 수 없습니다 (lat={lat:.5f}, lng={lng:.5f}). 도로 위를 클릭해주세요.")
    drivable = [e for e, _ in edges if e.allows("passenger")]
    if not drivable:
        drivable = [e for e, _ in edges]
    # pick closest by XY distance
    best = min(drivable, key=lambda e: min(
        ((e.getFromNode().getCoord()[0] - x)**2 + (e.getFromNode().getCoord()[1] - y)**2)**0.5,
        ((e.getToNode().getCoord()[0]   - x)**2 + (e.getToNode().getCoord()[1]   - y)**2)**0.5,
    ))
    return best.getID()


def edge_coords_geo(net, edge_id: str) -> list[list[float]]:
    """Get [lat, lng] pairs for an edge's shape."""
    edge = net.getEdge(edge_id)
    coords = []
    for node_x, node_y in edge.getShape():
        lon, lat = net.convertXY2LonLat(node_x, node_y)
        coords.append([lat, lon])
    return coords


async def broadcast(msg: dict):
    """Send JSON message to all connected WebSocket clients."""
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


def simulation_thread(net_file: str, origin: dict, dest: dict, stop_evt: threading.Event):
    """
    Runs in a background thread.
    Starts SUMO with TraCI, injects one vehicle on the Dijkstra path,
    and updates _state["vehicle_pos"] each step.
    """
    global _state
    sumo_bin = resolve_binary("sumo")
    if not sumo_bin:
        raise RuntimeError(
            "SUMO 실행 파일을 찾을 수 없습니다. SUMO를 설치하고 SUMO_HOME을 설정하거나 PATH에 sumo/bin을 추가해주세요."
        )

    print(f"[SIM] Starting simulation thread. net={net_file}", flush=True)
    print(f"[SIM] origin={origin}  dest={dest}", flush=True)

    try:
        # Load network with sumolib for edge lookup + route coords
        net = sumolib.net.readNet(net_file, withInternal=False)
        from_edge = nearest_edge(net, origin["lat"], origin["lng"])
        to_edge   = nearest_edge(net, dest["lat"],   dest["lng"])
        print(f"[SIM] from_edge={from_edge}  to_edge={to_edge}", flush=True)

        # Start SUMO headless
        traci.start([
            sumo_bin,
            "-n", net_file,
            "--no-warnings",
            "--no-step-log",
            "--collision.action", "none",
            "--time-to-teleport", "-1",
            "--step-length", "0.5",
            "--begin", "0",
            "--end", "86400",
        ])
        print("[SIM] SUMO started via TraCI", flush=True)

        for item in (_state.get("traffic_sync") or {}).get("sumo_edges", []):
            edge_id = item.get("sumo_edge_id")
            speed_kph = item.get("speed_kph")
            travel_time_s = item.get("travel_time_s")
            if not edge_id:
                continue
            try:
                if travel_time_s:
                    traci.edge.adaptTraveltime(edge_id, float(travel_time_s))
            except Exception:
                pass
            try:
                if speed_kph:
                    traci.edge.setMaxSpeed(edge_id, max(float(speed_kph) / 3.6, 0.1))
            except Exception:
                pass

        # Dijkstra route (vType="" = default, avoids missing-type errors)
        result = traci.simulation.findRoute(from_edge, to_edge, vType="")
        if not result.edges:
            raise RuntimeError(f"다익스트라 경로를 찾을 수 없습니다: {from_edge} → {to_edge}")

        edges = list(result.edges)
        print(f"[SIM] Route found: {len(edges)} edges", flush=True)
        _state["route_edges"] = edges

        # Build route polyline — TraCI lane shape is most reliable
        route_coords = []
        for eid in edges:
            try:
                lane_id = f"{eid}_0"
                shape = traci.lane.getShape(lane_id)
                for x, y in shape:
                    lon, lat_c = traci.simulation.convertGeo(x, y)
                    route_coords.append([lat_c, lon])
            except Exception:
                # fallback: use sumolib edge shape
                try:
                    edge_obj = net.getEdge(eid)
                    for nx, ny in edge_obj.getShape():
                        lon, lat_c = net.convertXY2LonLat(nx, ny)
                        route_coords.append([lat_c, lon])
                except Exception:
                    pass
        # Trim backward segments: SUMO edge shapes start at fromNode, but origin/dest
        # may be mid-edge, producing lines that extend behind the actual click point.
        if len(route_coords) >= 2:
            def _geo_dist2(a, b):
                return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

            orig_pt = [origin["lat"], origin["lng"]]
            dest_pt = [dest["lat"],   dest["lng"]]
            search  = max(2, len(route_coords) // 3)

            start_idx = min(range(search),
                            key=lambda i: _geo_dist2(route_coords[i], orig_pt))
            end_idx   = min(range(len(route_coords) - search, len(route_coords)),
                            key=lambda i: _geo_dist2(route_coords[i], dest_pt))

            route_coords = [orig_pt] + route_coords[start_idx: end_idx + 1] + [dest_pt]

        _state["route_coords"] = route_coords
        print(f"[SIM] Route coords: {len(route_coords)} points (trimmed)", flush=True)
        _state["route_buildings"], _state["building_debug"] = load_route_buildings(route_coords, _state.get("network_nodes"))

        # Define vehicle type and add vehicle
        # Use DEFAULT_VEHTYPE (always exists in SUMO)
        traci.route.add("route0", edges)
        traci.vehicle.add(
            vehID="veh0",
            routeID="route0",
            typeID="DEFAULT_VEHTYPE",
            depart="0",
            departLane="best",
            departSpeed="max",
        )
        print("[SIM] Vehicle veh0 added to simulation", flush=True)

        step = 0
        arrived = False
        max_steps = 100_000  # safety limit

        while not stop_evt.is_set() and not arrived and step < max_steps:
            traci.simulationStep()
            step += 1

            ids = traci.vehicle.getIDList()
            if "veh0" not in ids:
                # Vehicle arrived — keep last known position, just mark arrived
                if step > 10:
                    print(f"[SIM] veh0 not in sim at step {step} — arrived", flush=True)
                    arrived = True
                    if _state["vehicle_pos"] and _state["vehicle_pos"].get("lat"):
                        _state["vehicle_pos"] = {**_state["vehicle_pos"], "arrived": True}
                    else:
                        _state["vehicle_pos"] = {"arrived": True}
                continue

            x, y = traci.vehicle.getPosition("veh0")
            lon, lat = traci.simulation.convertGeo(x, y)
            speed = traci.vehicle.getSpeed("veh0")
            route_idx = traci.vehicle.getRouteIndex("veh0")
            progress = max(0.0, min(1.0, (route_idx + 1) / max(len(edges), 1)))

            _state["vehicle_pos"] = {
                "lat": lat,
                "lng": lon,
                "speed": round(speed * 3.6, 1),  # m/s → km/h
                "progress": round(progress, 3),
                "step": step,
                "arrived": False,
            }
            update_network_telemetry(_state["vehicle_pos"])
            time.sleep(0.1)  # ~10 fps

        print(f"[SIM] Simulation ended at step {step}", flush=True)
        traci.close()
        _state["sim_running"] = False

    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"[SIM ERROR]\n{msg}", flush=True)
        _state["error"] = str(e)
        _state["sim_running"] = False
        try:
            traci.close()
        except Exception:
            pass


def can_run_sumo() -> tuple[bool, str | None]:
    if not TRACI_AVAILABLE:
        return False, "traci 모듈을 찾을 수 없습니다."
    sumo_status = probe_runtime("sumo")
    if not sumo_status["ok"]:
        return False, f"sumo 실행 불가: {sumo_status['error']}"
    netconvert_status = probe_runtime("netconvert")
    if not netconvert_status["ok"]:
        return False, f"netconvert 실행 불가: {netconvert_status['error']}"
    return True, None


def reset_simulation_state() -> None:
    _state["network_ready"] = False
    _state["net_file"] = None
    _state["osm_file"] = None
    _state["mock_graph"] = None
    _state["sim_running"] = False
    _state["vehicle_pos"] = None
    _state["route_edges"] = []
    _state["route_coords"] = []
    _state["sim_mode"] = "idle"
    _state["error"] = None
    _state["warning"] = None
    _state["current_bbox"] = None
    _state["traffic_sync"] = None
    _state["download_log"] = []
    _state["network_nodes"] = []
    _state["synthetic_network_nodes"] = []
    _state["route_buildings"] = None
    _state["network_telemetry"] = None
    _state["building_debug"] = {"sample_links": [], "warnings": []}
    _state["simulation_run_id"] = None


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    sumo_status = probe_runtime("sumo")
    netconvert_status = probe_runtime("netconvert")
    return {
        "ok": True,
        "traci": TRACI_AVAILABLE,
        "sumo_home": SUMO_HOME or None,
        "sumo_binary": resolve_binary("sumo"),
        "netconvert_binary": resolve_binary("netconvert"),
        "sumo_runtime_ok": sumo_status["ok"],
        "sumo_runtime_error": sumo_status["error"],
        "netconvert_runtime_ok": netconvert_status["ok"],
        "netconvert_runtime_error": netconvert_status["error"],
        "platform": platform.system(),
    }


@app.get("/api/debug")
def debug():
    """현재 시뮬레이션 상태 확인용."""
    return {
        "network_ready": _state["network_ready"],
        "net_file": _state["net_file"],
        "sim_running": _state["sim_running"],
        "vehicle_pos": _state["vehicle_pos"],
        "route_edges_count": len(_state["route_edges"]),
        "route_coords_count": len(_state["route_coords"]),
        "sim_mode": _state["sim_mode"],
        "error": _state["error"],
        "warning": _state["warning"],
        "current_bbox": _state["current_bbox"],
        "traffic_sync": _state["traffic_sync"],
        "download_log": _state["download_log"],
        "network_nodes": _state["network_nodes"],
        "network_telemetry": _state["network_telemetry"],
        "ws_clients": len(_ws_clients),
    }


@app.post("/admin/standard-links/preprocess")
async def admin_preprocess_standard_links():
    return preprocess_standard_links()


@app.get("/admin/standard-links/status")
def admin_standard_links_status():
    return StandardLinkRepository().status()


@app.get("/debug/standard-link-status")
def debug_standard_link_status():
    return StandardLinkRepository().status()


@app.post("/admin/buildings/preprocess")
async def admin_preprocess_buildings():
    result = preprocess_buildings()
    BUILDING_REPOSITORY.reset_cache()
    return result


@app.get("/admin/buildings/status")
def admin_buildings_status():
    return BUILDING_REPOSITORY.status()


@app.post("/buildings/query-by-bbox")
def buildings_query_by_bbox(req: BuildingBBoxRequest):
    gdf = BUILDING_REPOSITORY.query_by_bbox(req.min_lng, req.min_lat, req.max_lng, req.max_lat)
    buildings = []
    for row in gdf.head(300).itertuples():
        geometry = []
        geom = row.geometry
        if geom.geom_type == "Polygon":
            geometry = [{"lat": lat, "lng": lng} for lng, lat in list(geom.exterior.coords)]
        elif geom.geom_type == "MultiPolygon":
            poly = max(list(geom.geoms), key=lambda g: g.area)
            geometry = [{"lat": lat, "lng": lng} for lng, lat in list(poly.exterior.coords)]
        buildings.append({
            "id": getattr(row, "ufid", None) or getattr(row, "pnu", None),
            "ufid": getattr(row, "ufid", None),
            "height_m": float(getattr(row, "height_m", 0.0) or 0.0),
            "height_confidence": getattr(row, "height_confidence", "low"),
            "geometry": geometry,
        })
    return {"buildings": buildings, "count": int(len(gdf))}


@app.post("/api/setup-network")
async def setup_network(req: SetupRequest):
    """Download OSM and convert to SUMO network for the given bbox."""
    bbox = req.bbox

    area_km2 = (
        (bbox.n - bbox.s) * 111 *
        (bbox.e - bbox.w) * 111 * abs((bbox.n + bbox.s) / 2 * 3.14159 / 180)
    )

    if area_km2 > MAX_SETUP_AREA_KM2:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택 구역이 너무 큽니다 ({area_km2:.2f} km²). "
                f"netconvert 안정성을 위해 {MAX_SETUP_AREA_KM2:.0f} km² 이하로 선택해주세요."
            ),
        )

    req_id = uuid4().hex[:8]
    osm_file = WORK_DIR / f"area-{req_id}.osm"
    net_file = WORK_DIR / f"area-{req_id}.net.xml"
    download_bbox = expand_bbox(bbox)

    with _network_lock:
        reset_simulation_state()

        try:
            # Step 1: Download OSM
            await asyncio.get_event_loop().run_in_executor(
                None, overpass_download, download_bbox, osm_file
            )

            # Step 2: build fallback OSM graph once so mock mode is always possible
            mock_graph = await asyncio.get_event_loop().run_in_executor(
                None, load_mock_graph, osm_file
            )

            fallback_warning = None
            chosen_net_file: str | None = None
            chosen_mode = "mock"
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, netconvert, osm_file, net_file
                )
                chosen_net_file = str(net_file)
                chosen_mode = "sumo"
            except Exception as exc:
                fallback_warning = (
                    "SUMO network conversion failed on this machine, so the app switched to "
                    f"OSM fallback mode. {exc}"
                )

            _state["osm_file"] = str(osm_file)
            _state["mock_graph"] = mock_graph
            _state["net_file"] = chosen_net_file
            _state["sim_mode"] = chosen_mode
            _state["network_ready"] = True
            _state["current_bbox"] = {"s": bbox.s, "w": bbox.w, "n": bbox.n, "e": bbox.e}
            _state["synthetic_network_nodes"] = generate_network_nodes_for_bbox(_state["current_bbox"])
            # Synthetic nodes are kept in-memory only — not persisted to DB — so they
            # never appear alongside user-created stations.
            _state["network_nodes"] = merged_network_nodes()

            mapping_stats = TRAFFIC_FUSION_ENGINE.prepare_current_network_mappings(
                osm_file=Path(_state["osm_file"]),
                net_file=Path(chosen_net_file) if chosen_net_file else None,
                bbox=_state["current_bbox"],
            )

            return {
                "ok": True,
                "net_file": chosen_net_file,
                "area_km2": round(area_km2, 2),
                "fallback": chosen_mode == "mock",
                "warning": fallback_warning,
                "mapping": mapping_stats,
            }

        except Exception as e:
            _state["error"] = str(e)
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/simulation/start")
async def start_simulation(req: SimStartRequest):
    """Start SUMO simulation with Dijkstra routing between origin and dest."""
    global _sim_thread, _stop_event

    if not _state["network_ready"]:
        raise HTTPException(status_code=400, detail="네트워크가 준비되지 않았습니다. 먼저 구역을 설정하세요.")

    # Stop existing simulation
    if _state["sim_running"] and _sim_thread and _sim_thread.is_alive():
        _stop_event.set()
        _sim_thread.join(timeout=5)

    # Reload network nodes from the DB so any user-created base stations
    # added since the last setup/run are included as connection candidates.
    _state["network_nodes"] = merged_network_nodes()

    _stop_event = threading.Event()
    _state["sim_running"] = True
    _state["vehicle_pos"] = None
    _state["error"] = None
    _state["warning"] = None
    _state["route_coords"] = []
    _state["route_edges"] = []
    _state["simulation_run_id"] = create_simulation_run(req.origin, req.dest, _state["sim_mode"])

    use_sumo, sumo_error = can_run_sumo()
    if use_sumo and _state["net_file"]:
        _state["sim_mode"] = "sumo"
        _sim_thread = threading.Thread(
            target=simulation_thread,
            args=(_state["net_file"], req.origin, req.dest, _stop_event),
            daemon=True,
        )
    else:
        if not _state["mock_graph"]:
            raise HTTPException(status_code=500, detail="Fallback OSM graph를 준비하지 못했습니다.")
        try:
            start_node = nearest_mock_node(_state["mock_graph"], req.origin["lat"], req.origin["lng"])
            end_node = nearest_mock_node(_state["mock_graph"], req.dest["lat"], req.dest["lng"])
            path = shortest_mock_path(_state["mock_graph"], start_node, end_node)
            route_coords = mock_route_coords(_state["mock_graph"], path)
        except RuntimeError as exc:
            _state["sim_running"] = False
            raise HTTPException(status_code=400, detail=str(exc))
        _state["route_edges"] = path
        _state["route_coords"] = route_coords
        _state["route_buildings"], _state["building_debug"] = load_route_buildings(route_coords, _state.get("network_nodes"))
        _state["sim_mode"] = "mock"
        _state["warning"] = (
            "SUMO runtime is unavailable on this machine, so the simulation is running in "
            f"OSM fallback mode. {sumo_error or ''}".strip()
        )
        _sim_thread = threading.Thread(
            target=mock_simulation_thread,
            args=(route_coords, _stop_event),
            daemon=True,
        )

    _sim_thread.start()
    return {"ok": True, "mode": _state["sim_mode"], "warning": _state["warning"]}


@app.post("/api/simulation/stop")
async def stop_simulation():
    global _stop_event
    _stop_event.set()
    _state["sim_running"] = False
    finish_simulation_run(_state.get("simulation_run_id"), {
        "vehicle_pos": _state.get("vehicle_pos"),
        "network_telemetry": _state.get("network_telemetry"),
        "sim_mode": _state.get("sim_mode"),
    })
    _state["sim_mode"] = "idle"
    return {"ok": True}


@app.post("/api/simulation/reset")
async def reset_simulation():
    global _stop_event, _sim_thread
    _stop_event.set()
    if _sim_thread and _sim_thread.is_alive():
        _sim_thread.join(timeout=5)
    finish_simulation_run(_state.get("simulation_run_id"), {
        "vehicle_pos": _state.get("vehicle_pos"),
        "network_telemetry": _state.get("network_telemetry"),
        "sim_mode": _state.get("sim_mode"),
    })
    _sim_thread = None
    _stop_event = threading.Event()
    reset_simulation_state()
    return {"ok": True}


@app.get("/api/simulation/route")
async def get_route():
    """Return the computed route polyline once simulation starts."""
    return {
        "route_coords": _state["route_coords"],
        "route_edges": _state["route_edges"],
        "mode": _state["sim_mode"],
        "warning": _state["warning"],
    }


# ── User-created network nodes (base stations) ───────────────────────────────
class NetworkNodeCreateRequest(BaseModel):
    lat: float
    lng: float
    node_type: str = "base_station"


def _network_node_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name"),
        "node_type": row.get("node_type"),
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "capacity": row.get("capacity"),
        "load": row.get("load"),
        "congestion_score": row.get("congestion_score"),
        "edge_latency_ms": row.get("edge_latency_ms"),
        "coverage_radius_m": row.get("coverage_radius_m"),
        "source": row.get("source"),
    }


def _refresh_active_network_nodes() -> None:
    if _state.get("network_ready"):
        _state["network_nodes"] = merged_network_nodes()


@app.get("/network-nodes")
async def list_network_nodes():
    return {"nodes": [_network_node_response(row) for row in fetch_network_nodes()]}


@app.post("/network-nodes")
async def create_network_node(req: NetworkNodeCreateRequest):
    if not postgis_available():
        raise HTTPException(status_code=400, detail="PostGIS가 활성화되어 있지 않아 기지국을 저장할 수 없습니다.")

    name = f"기지국 {max_user_station_number() + 1}"
    node = {
        "id": f"user-bs-{uuid4().hex[:10]}",
        "name": name,
        "node_type": req.node_type,
        "lat": req.lat,
        "lng": req.lng,
        "capacity": 100.0,
        "load": 0.0,
        "congestion_score": 0.0,
        "edge_latency_ms": 5.0,
        "coverage_radius_m": 500.0,
        "source": "user_created",
    }
    saved = insert_network_node(node)
    if saved is None:
        raise HTTPException(status_code=500, detail="기지국 저장에 실패했습니다.")
    _refresh_active_network_nodes()
    return _network_node_response(saved)


@app.delete("/network-nodes/{node_id}")
async def remove_network_node(node_id: str):
    deleted = delete_network_node(node_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="기지국을 찾을 수 없습니다.")
    _refresh_active_network_nodes()
    return {"ok": True, "id": node_id}


@app.post("/network-nodes/reset-user-created")
async def reset_user_created_nodes():
    deleted = delete_user_created_network_nodes()
    _refresh_active_network_nodes()
    return {"ok": True, "deleted": deleted}


@app.post("/traffic/sync-its")
async def sync_its_traffic(req: TrafficSyncRequest):
    result = TRAFFIC_FUSION_ENGINE.sync_its(bbox=req.bbox)
    _state["traffic_sync"] = {
        "last_sync_time": result["last_sync_time"],
        "records_count": result["records_count"],
        "matched_standard_links": result["matched_standard_links"],
        "matched_osm_edges": result["matched_osm_edges"],
        "unmatched_records": result["unmatched_records"],
        "sumo_edges": [
            {
                "sumo_edge_id": item.get("sumo_edge_id"),
                "speed_kph": item.get("speed_kph"),
                "travel_time_s": item.get("travel_time_s"),
                "congestion_score": item.get("congestion_score"),
                "standard_link_id": item.get("standard_link_id"),
                "its_link_id": item.get("its_link_id"),
            }
            for item in ITS_CACHE.enriched_links
            if item.get("sumo_edge_id")
        ],
    }
    return result


@app.get("/traffic/current")
def traffic_current():
    return TRAFFIC_FUSION_ENGINE.current_traffic()


@app.get("/debug/its-link-match")
def debug_its_link_match():
    stats = ITS_CACHE.stats.copy()
    stats.update({
        "its_records_count": len(ITS_CACHE.records),
        "sample": ITS_CACHE.enriched_links[:5],
        "last_sync_time": ITS_CACHE.last_sync_time,
    })
    return stats


@app.get("/debug/building-obstruction")
def debug_building_obstruction():
    return _state.get("building_debug") or {
        "buildings_loaded": 0,
        "height_available_count": 0,
        "height_estimated_count": 0,
        "sample_links": [],
        "warnings": [],
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket — sends vehicle position at ~10 fps."""
    await ws.accept()
    _ws_clients.append(ws)
    try:
        last_pos = None
        last_route = None
        last_telemetry = None
        while True:
            pos = _state.get("vehicle_pos")
            err = _state.get("error")
            route = _state.get("route_coords")
            telemetry = _state.get("network_telemetry")

            if err:
                await ws.send_json({"type": "error", "message": err})
                _state["error"] = None

            warn = _state.get("warning")
            if warn:
                await ws.send_json({"type": "warning", "message": warn, "mode": _state.get("sim_mode")})
                _state["warning"] = None

            if route and route is not last_route:
                await ws.send_json({
                    "type": "route",
                    "coords": route,
                    "mode": _state.get("sim_mode"),
                })
                last_route = route

            if pos and pos != last_pos:
                await ws.send_json({"type": "position", **pos})
                last_pos = pos

                if pos.get("arrived"):
                    await ws.send_json({"type": "arrived"})

            if telemetry and telemetry != last_telemetry:
                await ws.send_json({"type": "telemetry", **telemetry})
                last_telemetry = telemetry

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
