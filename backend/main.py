"""
V2X AI Routing Lab — FastAPI Backend
SUMO path: C:\\Program Files (x86)\\Eclipse\\Sumo
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── SUMO paths ──────────────────────────────────────────────────────────────
SUMO_HOME = os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo")
SUMO_BIN  = Path(SUMO_HOME) / "bin"
SUMO_TOOLS = Path(SUMO_HOME) / "tools"

# Add SUMO tools to Python path so traci/sumolib are importable
if str(SUMO_TOOLS) not in sys.path:
    sys.path.insert(0, str(SUMO_TOOLS))

try:
    import traci
    import sumolib
    TRACI_AVAILABLE = True
except ImportError:
    TRACI_AVAILABLE = False
    print("[WARN] traci/sumolib not found — check SUMO_HOME and tools/ path")

# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="V2X Routing Lab")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WORK_DIR = Path(__file__).parent / "networks"
WORK_DIR.mkdir(exist_ok=True)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")

# ── Global state ──────────────────────────────────────────────────────────────
_state = {
    "network_ready": False,
    "net_file": None,      # path to .net.xml
    "sim_running": False,
    "vehicle_pos": None,   # {"lat": float, "lng": float, "progress": float}
    "route_edges": [],     # list of SUMO edge IDs
    "route_coords": [],    # list of [lat, lng] for the full route polyline
    "error": None,
}
_ws_clients: list[WebSocket] = []
_sim_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


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


# ── Helpers ───────────────────────────────────────────────────────────────────
def run_cmd(args: list, cwd=None) -> tuple[int, str, str]:
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd,
        encoding="utf-8", errors="replace"
    )
    return result.returncode, result.stdout, result.stderr


def overpass_download(bbox: BBox, out_file: Path):
    """Download OSM data for bbox via Overpass API."""
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
    url = "https://overpass-api.de/api/interpreter"
    print(f"[OSM] Downloading bbox: S={bbox.s:.4f} W={bbox.w:.4f} N={bbox.n:.4f} E={bbox.e:.4f}", flush=True)

    try:
        resp = requests.post(url, data={"data": query}, headers=headers, timeout=120)
        if resp.status_code == 406:
            resp = requests.get(url, params={"data": query}, headers=headers, timeout=120)
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Overpass API HTTP 오류 {e.response.status_code}: {e.response.text[:300]}")

    content = resp.content

    # Overpass sometimes returns 200 with an error message inside the XML
    if b"<remark>" in content and b"runtime error" in content:
        raise RuntimeError("Overpass 서버 오류 (메모리/타임아웃 초과). 구역을 더 작게 그려주세요.")
    if b"<way" not in content and b"<node" not in content:
        raise RuntimeError("Overpass 응답에 도로 데이터가 없습니다. 구역 안에 도로가 있는지 확인해주세요.")

    out_file.write_bytes(content)
    size_kb = len(content) / 1024
    print(f"[OSM] Downloaded {size_kb:.1f} KB → {out_file}", flush=True)


def netconvert(osm_file: Path, net_file: Path):
    """Convert OSM to SUMO network with netconvert."""
    nc = SUMO_BIN / "netconvert.exe"
    if not nc.exists():
        nc = SUMO_BIN / "netconvert"
    args = [
        str(nc),
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
        "--log", str(WORK_DIR / "netconvert.log"),
    ]
    print(f"[NET] Running netconvert: {osm_file.name} → {net_file.name}", flush=True)
    rc, out, err = run_cmd(args)
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
    sumo_bin = str(SUMO_BIN / "sumo.exe")
    if not Path(sumo_bin).exists():
        sumo_bin = str(SUMO_BIN / "sumo")

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
        _state["route_coords"] = route_coords
        print(f"[SIM] Route coords: {len(route_coords)} points", flush=True)

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


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True, "traci": TRACI_AVAILABLE, "sumo_home": SUMO_HOME}


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
        "error": _state["error"],
        "ws_clients": len(_ws_clients),
    }


@app.post("/api/setup-network")
async def setup_network(req: SetupRequest):
    """Download OSM and convert to SUMO network for the given bbox."""
    _state["network_ready"] = False
    _state["error"] = None
    bbox = req.bbox

    area_km2 = (
        (bbox.n - bbox.s) * 111 *
        (bbox.e - bbox.w) * 111 * abs((bbox.n + bbox.s) / 2 * 3.14159 / 180)
    )

    osm_file = WORK_DIR / "area.osm"
    net_file = WORK_DIR / "area.net.xml"

    try:
        # Step 1: Download OSM
        await asyncio.get_event_loop().run_in_executor(
            None, overpass_download, bbox, osm_file
        )

        # Step 2: netconvert
        await asyncio.get_event_loop().run_in_executor(
            None, netconvert, osm_file, net_file
        )

        _state["network_ready"] = True
        _state["net_file"] = str(net_file)
        return {"ok": True, "net_file": str(net_file), "area_km2": round(area_km2, 2)}

    except Exception as e:
        _state["error"] = str(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/simulation/start")
async def start_simulation(req: SimStartRequest):
    """Start SUMO simulation with Dijkstra routing between origin and dest."""
    global _sim_thread, _stop_event

    if not _state["network_ready"] or not _state["net_file"]:
        raise HTTPException(status_code=400, detail="네트워크가 준비되지 않았습니다. 먼저 구역을 설정하세요.")
    if not TRACI_AVAILABLE:
        raise HTTPException(status_code=500, detail="traci 모듈을 찾을 수 없습니다. SUMO_HOME을 확인하세요.")

    # Stop existing simulation
    if _state["sim_running"] and _sim_thread and _sim_thread.is_alive():
        _stop_event.set()
        _sim_thread.join(timeout=5)

    _stop_event = threading.Event()
    _state["sim_running"] = True
    _state["vehicle_pos"] = None
    _state["error"] = None
    _state["route_coords"] = []

    _sim_thread = threading.Thread(
        target=simulation_thread,
        args=(_state["net_file"], req.origin, req.dest, _stop_event),
        daemon=True,
    )
    _sim_thread.start()
    return {"ok": True}


@app.post("/api/simulation/stop")
async def stop_simulation():
    global _stop_event
    _stop_event.set()
    _state["sim_running"] = False
    return {"ok": True}


@app.get("/api/simulation/route")
async def get_route():
    """Return the computed route polyline once simulation starts."""
    return {
        "route_coords": _state["route_coords"],
        "route_edges": _state["route_edges"],
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket — sends vehicle position at ~10 fps."""
    await ws.accept()
    _ws_clients.append(ws)
    try:
        last_pos = None
        while True:
            pos = _state.get("vehicle_pos")
            err = _state.get("error")

            if err:
                await ws.send_json({"type": "error", "message": err})
                _state["error"] = None

            elif pos and pos != last_pos:
                await ws.send_json({"type": "position", **pos})
                last_pos = pos

                if pos.get("arrived"):
                    await ws.send_json({"type": "arrived"})

            elif _state["route_coords"] and last_pos is None:
                # Send route polyline once it's available
                await ws.send_json({
                    "type": "route",
                    "coords": _state["route_coords"],
                })
                last_pos = "route_sent"  # prevent re-sending

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
