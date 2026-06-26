"""
V2X AI Routing Lab — FastAPI Backend
"""
import asyncio
import json
import math
import os
import random
import platform
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from heapq import heappop, heappush
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Optional
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
    list_simulation_runs,
    list_simulation_runs_by_sheet,
    max_user_station_number,
    postgis_available,
    update_network_node_placement,
    upsert_network_nodes,
)
from app.services.standard_link.standard_link_preprocessor import preprocess_standard_links
from app.services.standard_link.standard_link_repository import StandardLinkRepository
from app.services.buildings.building_preprocessor import preprocess_buildings
from app.services.buildings.building_repository import BuildingRepository
from app.services.buildings.building_obstruction_analyzer import analyze_candidates
try:
    from app.services.routing.route_cost_function import (
        CostWeights,
        NormScales,
        KPathCandidate,
        compute_edge_network_cost,
        evaluate_path,
        evaluate_k_candidates,
        _find_best_bs_light,
    )
    _route_cost_weights = CostWeights()
    _norm_scales = NormScales()
    ROUTE_COST_AVAILABLE = True
except ImportError:
    ROUTE_COST_AVAILABLE = False

try:
    from app.services.metrics.route_metrics import (
        RouteMetrics,
        from_path_cost,
        from_k_candidates,
        compare_algorithms,
    )
    ROUTE_METRICS_AVAILABLE = True
except ImportError:
    ROUTE_METRICS_AVAILABLE = False

try:
    from app.services.analysis.simulation_summary import build_summary, SummaryThresholds
    SUMMARY_AVAILABLE = True
except ImportError:
    SUMMARY_AVAILABLE = False

try:
    from app.services.routing.look_ahead_scan import look_ahead_bs_scan
    LOOK_AHEAD_AVAILABLE = True
except ImportError:
    LOOK_AHEAD_AVAILABLE = False

try:
    from app.services.rl.v2x_routing_env import V2XRoutingEnv, DEFAULT_REWARD_WEIGHTS
    from app.services.rl.rl_trainer import run_episode, run_episodes, SUPPORTED_POLICIES
    RL_AVAILABLE = True
except ImportError:
    RL_AVAILABLE = False

try:
    from app.services.latency import LATENCY_REGISTRY
    LATENCY_AVAILABLE = True
except ImportError:
    LATENCY_AVAILABLE = False

try:
    from app.services.custom_policy.engine import (
        parse_custom_policy,
        validate_custom_policy,
        run_custom_weighted_policy,
        POLICY_KEYS as _CUSTOM_POLICY_KEYS,
        COST_FEATURES as _CUSTOM_COST_FEATURES,
        BS_FEATURES as _CUSTOM_BS_FEATURES,
        RESOURCE_FEATURES as _CUSTOM_RESOURCE_FEATURES,
    )
    CUSTOM_POLICY_AVAILABLE = True
except ImportError:
    CUSTOM_POLICY_AVAILABLE = False

try:
    from app.services.resources import (
        build_resource_demand_map,
        ALLOCATION_REGISTRY,
        AllocationInput,
        AllocationConfig,
        apply_allocation_to_network_nodes,
    )
    from app.services.resources.demand_calculator import BSResourceDemand as _BSResourceDemand
    RESOURCE_DEMAND_AVAILABLE = True
except ImportError:
    RESOURCE_DEMAND_AVAILABLE = False

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

VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "")
_ROAD_NAME_CACHE_FILE = Path(__file__).parent.parent / "data" / "road_name_cache.json"
_road_name_cache: dict[str, str] = {}


def _load_road_name_cache() -> None:
    global _road_name_cache
    try:
        if _ROAD_NAME_CACHE_FILE.exists():
            _road_name_cache = json.loads(_ROAD_NAME_CACHE_FILE.read_text(encoding="utf-8"))
            print(f"[VWORLD] Loaded {len(_road_name_cache)} cached road names", flush=True)
    except Exception:
        _road_name_cache = {}


def _save_road_name_cache() -> None:
    try:
        _ROAD_NAME_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ROAD_NAME_CACHE_FILE.write_text(
            json.dumps(_road_name_cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[VWORLD] Cache save failed: {exc}", flush=True)


_ROAD_NAME_RE = __import__("re").compile(r"[가-힣][가-힣0-9]*(?:대로|로|길)[0-9가-힣\-]*")


def _vworld_road_name(lat: float, lng: float) -> str:
    """Reverse-geocode a coordinate to a Korean road name via V-World API."""
    if not VWORLD_API_KEY:
        return ""
    dong_fallback = ""
    for qtype in ("road", "both"):
        try:
            url = (
                "https://api.vworld.kr/req/address"
                f"?service=address&request=getAddress&version=2.0"
                f"&crs=epsg:4326&point={lng},{lat}"
                f"&format=json&type={qtype}&key={VWORLD_API_KEY}"
            )
            resp = requests.get(url, timeout=5)
            data = resp.json()
            for item in data.get("response", {}).get("result", []):
                struct = item.get("structure", {})
                # Primary: level3 = 도로명
                road = struct.get("level3", "")
                if road:
                    return road
                # Fallback: extract road name pattern from full text
                text = item.get("text", "")
                m = _ROAD_NAME_RE.search(text)
                if m:
                    return m.group(0)
                # Save dong name as last resort
                if not dong_fallback:
                    dong = struct.get("level4A", "") or struct.get("level4L", "")
                    gu  = struct.get("level2", "")
                    if dong:
                        dong_fallback = f"{gu} {dong}".strip() if gu else dong
        except Exception as exc:
            print(f"[VWORLD] API error ({lat:.5f},{lng:.5f}): {exc}", flush=True)
            break
    return dong_fallback  # "" if nothing at all; 동 이름이라도 반환


def _enrich_edge_names_vworld(
    edge_midpoints: dict[str, tuple[float, float]],
    existing: dict[str, str],
) -> None:
    """
    Background: query V-World for edges still missing a road name,
    update _state['route_edge_names'] and global cache in place.
    edge_midpoints: {edge_id: (lat, lng)}
    existing: the same dict object as _state['route_edge_names']
    """
    # Edges without a name: includes those never queried AND those previously cached as ""
    missing = [eid for eid in edge_midpoints if not existing.get(eid) and not _road_name_cache.get(eid)]
    if not missing:
        # Apply any cached names not yet in existing
        for eid in edge_midpoints:
            if not existing.get(eid) and _road_name_cache.get(eid):
                existing[eid] = _road_name_cache[eid]
        return
    print(f"[VWORLD] Fetching road names for {len(missing)} edges …", flush=True)
    fetched = 0
    for eid in missing:
        lat, lng = edge_midpoints[eid]
        name = _vworld_road_name(lat, lng)
        if name:
            _road_name_cache[eid] = name
            existing[eid] = name
            fetched += 1
        time.sleep(0.05)  # ~20 req/s max
    _save_road_name_cache()
    print(f"[VWORLD] Road name enrichment done: {fetched}/{len(missing)} matched", flush=True)
    if fetched > 0:
        # Bump version so WS handler resends route_cost with updated edge names
        _state["route_cost_version"] = _state.get("route_cost_version", 0) + 1


_load_road_name_cache()

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
    "background_vehicles": [],  # 다중차량 실험군 — 배경 차량 [{"id","lat","lng","route_coords",...}]
    "background_vehicle_ids": [],  # SUMO 모드 — TraCI에 주입된 배경 차량 vehID 목록
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
    "route_cost_result": None,
    "route_cost_edge_data": None,
    "route_cost_version": 0,
    "route_edge_names": {},
    "edge_telemetry": [],
    "edge_avg_speeds": {},   # {edge_id: avg_speed_kmh} measured while vehicle traversed
    "edge_history": [],      # completed edge_ids in traversal order
    "k_path_candidates": None,
    "k_path_edge_data": None,  # cached (path_ids, edge_data) tuples — reused to re-score K-path costs periodically without re-running Yen's
    "algorithm_metrics": {},
    "simulation_summary": None,
    "selected_algorithms": {},
    "latency_algorithm": "full_composite_latency",
    "allocation_algorithm": "traffic_aware_allocation",
    "last_allocation_result": None,
    "algorithm_comparison": {"status": "idle"},
    "simulation_config": None,   # Stage-1: persisted user config
    "policy_options": None,      # Stage-1: policy options from last applied config
    "custom_policies": {},       # Stage-2: active custom policies keyed by policy_key
    "custom_policy_debug": {},   # Stage-2: debug info from last registration
}
_ws_clients: list[WebSocket] = []
_sim_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_pause_event = threading.Event()  # set=일시정지, clear=실행
_runtime_probe_cache: dict[str, dict] = {}
_network_lock = threading.Lock()
# Phase 2: 시나리오 배치 러너 상태. _state를 공유하는 단일 순차 실행이므로 동시에 하나만
# 돈다 — _active_batch_id가 None이 아니면 실시간 /api/simulation/start도 막는다(위 참고).
_batch_runs: dict[str, dict] = {}
_active_batch_id: Optional[str] = None


# ── Models ───────────────────────────────────────────────────────────────────
class BBox(BaseModel):
    s: float
    w: float
    n: float
    e: float

class SetupRequest(BaseModel):
    bbox: BBox

# ── Stage-1 Simulation Config ─────────────────────────────────────────────────

class SimConfigCostWeights(BaseModel):
    w_distance: float = 1.0
    w_time:     float = 2.0
    w_latency:  float = 3.0
    w_load:     float = 1.5
    w_resource: float = 1.0   # → CostWeights.w_resource_deficit
    w_handover: float = 1.0
    w_blockage: float = 1.5
    w_future:   float = 2.5   # → CostWeights.w_coverage_risk

class SimConfigAlgorithmSelection(BaseModel):
    route_algorithm:                  str = "dijkstra"
    latency_algorithm:                str = "full_composite_latency"
    base_station_selection_algorithm: str = "lowest_latency_bs"
    resource_allocation_algorithm:    str = "traffic_aware_allocation"

class SimConfigPolicyOptions(BaseModel):
    lookahead_k:          int   = 3
    lookahead_time:       float = 10.0
    max_handover_allowed: int   = 10
    prefer_low_latency:   bool  = True
    prefer_load_balance:  bool  = False
    avoid_disconnection:  bool  = True
    traffic_lambda:       float = 5.0   # background vehicle density (vehicles/km²)
    other_device_lambda:  float = 300.0 # 차량 외 기기(폰/IoT) 밀도 (기기/km²) — _L_total의 n_vehicles에 가산
    network_mode:         str   = "5G"  # "4G" / "5G" / "6G" — drives latency model

class SimulationConfigModel(BaseModel):
    cost_weights:        SimConfigCostWeights        = SimConfigCostWeights()
    algorithm_selection: SimConfigAlgorithmSelection = SimConfigAlgorithmSelection()
    policy_options:      SimConfigPolicyOptions      = SimConfigPolicyOptions()

class SimulationConfigRequest(BaseModel):
    simulation_config: dict

_VALID_ROUTE_ALGORITHMS = frozenset({
    "dijkstra", "astar", "k_shortest_path",
    "network_aware", "lookahead", "rl_routing",
    # frontend aliases
    "network_aware_routing", "look_ahead_routing",
})

def validate_simulation_config(raw: dict) -> SimulationConfigModel:
    """Parse user config dict; invalid individual fields fall back to per-field defaults."""
    section_map = {
        "cost_weights":        SimConfigCostWeights,
        "algorithm_selection": SimConfigAlgorithmSelection,
        "policy_options":      SimConfigPolicyOptions,
    }
    safe: dict[str, Any] = {}
    for key, cls in section_map.items():
        if key in raw and isinstance(raw[key], dict):
            defaults = cls()
            field_data: dict[str, Any] = {}
            for fname in cls.model_fields:
                raw_val = raw[key].get(fname, getattr(defaults, fname))
                try:
                    cls.model_validate({fname: raw_val})
                    # clamp float weight fields to a safe upper bound
                    if key == "cost_weights" and isinstance(raw_val, (int, float)):
                        raw_val = min(float(raw_val), 20.0)
                    field_data[fname] = raw_val
                except Exception:
                    field_data[fname] = getattr(defaults, fname)
            try:
                safe[key] = cls.model_validate(field_data)
            except Exception:
                safe[key] = defaults
    try:
        return SimulationConfigModel(**safe)
    except Exception:
        return SimulationConfigModel()


def merge_with_default_config(user_config: Optional[dict]) -> SimulationConfigModel:
    """Merge user-supplied config with defaults. None returns pure defaults."""
    if not user_config or not isinstance(user_config, dict):
        return SimulationConfigModel()
    return validate_simulation_config(user_config)


def sanitize_algorithm_selection(cfg: SimulationConfigModel) -> SimConfigAlgorithmSelection:
    """Validate algorithm IDs against registered algorithms; unknown IDs fall back to defaults."""
    algo = cfg.algorithm_selection
    defaults = SimConfigAlgorithmSelection()
    if algo.route_algorithm not in _VALID_ROUTE_ALGORITHMS:
        algo = algo.model_copy(update={"route_algorithm": defaults.route_algorithm})
    if LATENCY_AVAILABLE:
        try:
            valid_lat = {a["id"] for a in LATENCY_REGISTRY.list_algorithms()}
            if algo.latency_algorithm not in valid_lat:
                algo = algo.model_copy(update={"latency_algorithm": defaults.latency_algorithm})
        except Exception:
            pass
    if RESOURCE_DEMAND_AVAILABLE:
        try:
            valid_alloc = {a["id"] for a in ALLOCATION_REGISTRY.list_algorithms()}
            if algo.resource_allocation_algorithm not in valid_alloc:
                algo = algo.model_copy(update={
                    "resource_allocation_algorithm": defaults.resource_allocation_algorithm,
                })
        except Exception:
            pass
    return algo


def _apply_simulation_config(cfg: SimulationConfigModel) -> None:
    """Write validated config values to global routing weights and algorithm registries."""
    global _route_cost_weights
    cw = cfg.cost_weights
    _route_cost_weights = CostWeights(
        w_distance=        max(0.0, cw.w_distance),
        w_time=            max(0.0, cw.w_time),
        w_latency=         max(0.0, cw.w_latency),
        w_load=            max(0.0, cw.w_load),
        w_handover=        max(0.0, cw.w_handover),
        w_blockage=        max(0.0, cw.w_blockage),
        w_coverage_risk=   max(0.0, cw.w_future),
        w_resource_deficit=max(0.0, cw.w_resource),
    )
    algo = sanitize_algorithm_selection(cfg)
    if LATENCY_AVAILABLE:
        try:
            LATENCY_REGISTRY.set_algorithm(algo.latency_algorithm)
            _state["latency_algorithm"] = algo.latency_algorithm
        except Exception:
            pass
    if RESOURCE_DEMAND_AVAILABLE:
        try:
            ALLOCATION_REGISTRY.set_algorithm(algo.resource_allocation_algorithm)
        except Exception:
            pass
    _state["allocation_algorithm"] = algo.resource_allocation_algorithm
    if ROUTE_COST_AVAILABLE:
        try:
            from app.services.routing.route_cost_function import set_bs_selection_algorithm
            set_bs_selection_algorithm(algo.base_station_selection_algorithm)
        except Exception:
            pass
    _state["bs_selection_algorithm"] = algo.base_station_selection_algorithm
    pol = cfg.policy_options
    _state["policy_options"] = {
        "lookahead_k":          max(1, min(pol.lookahead_k, 10)),
        "lookahead_time":       max(1.0, min(pol.lookahead_time, 120.0)),
        "max_handover_allowed": max(0, min(pol.max_handover_allowed, 50)),
        "prefer_low_latency":   bool(pol.prefer_low_latency),
        "prefer_load_balance":  bool(pol.prefer_load_balance),
        "avoid_disconnection":  bool(pol.avoid_disconnection),
        "traffic_lambda":       max(0.1, min(float(pol.traffic_lambda), 200.0)),
        "other_device_lambda":  max(0.0, min(float(pol.other_device_lambda), 2000.0)),
        "network_mode":         pol.network_mode if pol.network_mode in ("4G", "5G", "6G") else "5G",
    }
    _state["simulation_config"] = cfg.model_dump()


class SimStartRequest(BaseModel):
    origin: dict   # {"lat": float, "lng": float}
    dest:   dict   # {"lat": float, "lng": float}
    use_network_routing: bool = False
    algorithm_config: dict = {}
    simulation_config: Optional[dict] = None  # Stage-1 user config
    vehicle_count: int = 1  # 타겟 차량 1대 + 배경 차량 (vehicle_count - 1)대
    seed: Optional[int] = None  # 재현성: 기타기기/배경차량 Poisson 샘플링에 사용. None=비결정적(기존 동작)
    scenario_id: Optional[str] = None  # 배치 러너(Phase 2)가 simulation_runs에 태깅. 단독 실행 시 None
    batch_id: Optional[str] = None     # 배치 러너(Phase 2)가 simulation_runs에 태깅. 단독 실행 시 None
    sheet_id: Optional[str] = None     # 시뮬레이션 탭의 "시트"(Phase 5)가 simulation_runs에 태깅. 시트별로 DB에서 분리 조회 가능
    sheet_name: Optional[str] = None


class CostWeightsRequest(BaseModel):
    w_distance: float = 1.0
    w_time: float = 2.0
    w_latency: float = 3.0
    w_load: float = 1.5
    w_handover: float = 1.0
    w_blockage: float = 1.5
    w_coverage_risk: float = 2.5


class NormScalesRequest(BaseModel):
    distance_km: float = 1.0
    time_min: float = 1.0
    latency_ms: float = 20.0
    loss_db: float = 30.0


class RLEpisodeRequest(BaseModel):
    origin_id: str
    dest_id: str
    policy: str = "greedy"
    max_steps: int = 200
    n_episodes: int = 1
    seed: Optional[int] = None
    record_trajectory: bool = True


class ScenarioSpec(BaseModel):
    """
    Phase 2: one scenario inside a batch. mode="route_metrics" evaluates the
    classic routing/allocation pipeline (origin/dest, lat/lng) via
    _evaluate_mock_route(synchronous=True); mode="rl_episode" runs baseline
    RL policies (origin_id/dest_id, road-graph node IDs) via run_episode(s) —
    these two modes use disjoint field sets because the two evaluators take
    different coordinate spaces (lat/lng vs. graph node ID).
    """
    id: Optional[str] = None
    label: Optional[str] = None
    mode: str = "route_metrics"  # "route_metrics" | "rl_episode"
    source: str = "manual"  # "manual" | "llm_generated" | "param_batch" — provenance only, not read by the evaluator

    # route_metrics fields
    origin: Optional[dict] = None
    dest: Optional[dict] = None
    vehicle_count: int = 1
    algorithm_config: dict = {}
    simulation_config: Optional[dict] = None
    origin_node_id: Optional[str] = None  # snap-to-road 결과(있으면) — 평가 로직은 안 읽음, 프런트 표시용
    dest_node_id: Optional[str] = None

    # rl_episode fields
    origin_id: Optional[str] = None
    dest_id: Optional[str] = None
    policy: str = "greedy"
    max_steps: int = 200
    n_episodes: int = 1
    record_trajectory: bool = True

    seed: Optional[int] = None  # shared by both modes


class ScenarioBatchRequest(BaseModel):
    scenarios: list[ScenarioSpec]
    label: Optional[str] = None


class ScenarioGenerateRequest(BaseModel):
    description: str           # 자연어로 원하는 시나리오 묶음을 설명 (예: "퇴근시간 혼잡 + 차량수 다양화")
    count: int = 5
    seed_base: Optional[int] = None  # 지정 시 생성된 시나리오 i번째의 seed = seed_base + i (재현성)
    provider: Optional[str] = None   # "vertex" | "azure" | "bedrock" | None=auto


class LatencyAlgorithmRequest(BaseModel):
    algorithm_id: str


class AllocationAlgorithmRequest(BaseModel):
    algorithm_id: str


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


def _poisson_sample(lam: float, rng: random.Random = random) -> int:
    """Knuth algorithm for Poisson(lam) sampling.

    rng : a random.Random instance, or the global `random` module itself
          (both expose .random() with the same signature). Pass a seeded
          random.Random(seed) instance for reproducible runs; defaults to
          the global module to preserve existing non-deterministic callers.
    """
    if lam <= 0:
        return 0
    L = math.exp(-min(lam, 700.0))
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def _seed_other_device_load(nodes: list[dict], other_device_lambda: float, rng: random.Random = random) -> None:
    """차량 외 기기(보행자 폰, 고정 IoT 센서) 부하를 노드별 커버리지 반경 기준
    Poisson(λ × 면적) 샘플로 추정해 node["n_other_devices"]에 저장한다.

    _L_total의 큐잉 모델(rho = n_vehicles / C_tech)은 원래 차량 수만 분자에 넣었는데,
    실제로는 같은 기지국 capacity를 폰/IoT 기기도 같이 나눠 쓴다 — 이 함수가 그 항을
    채워서 analyze_candidates가 "차량 + 기타 기기"를 합산해 더 현실적인 부하를 계산하게 한다.
    합성 노드(synthetic)뿐 아니라 사용자가 직접 배치한 기지국(user_created)에도 동일하게
    적용된다. 보행자/IoT는 이 시뮬레이션에서 개별 위치를 추적하지 않으므로, 배경 차량처럼
    매 틱 갱신하지 않고 시뮬레이션 시작 시 1회만 추정해 런 전체에서 고정값으로 둔다.
    """
    for node in nodes:
        radius_m = float(node.get("coverage_radius_m") or 400.0)
        area_km2 = math.pi * (radius_m / 1000.0) ** 2
        node["n_other_devices"] = _poisson_sample(other_device_lambda * area_km2, rng)


def _seed_its_congestion_load(nodes: list[dict]) -> None:
    """동기화된 실시간 ITS 교통량(/traffic/sync-its)을 기지국별 부하로 환산해
    node["n_its_load"]에 저장한다.

    기존에는 ITS congestion_score가 mock 모드 배경 차량의 출발/도착지 샘플링 가중치로만
    쓰였고 latency 큐잉 모델(_L_total)에는 전혀 반영되지 않았다 — 이 함수가 그 간극을
    메운다. 각 노드의 coverage_radius_m 반경 안에 들어오는 ITS 링크 geometry 포인트들의
    congestion_score(0~1) 중 최댓값을 그 노드의 혼잡도로 보고, "혼잡도 1.0 = 모델에 안
    잡히는 차량/기기 최대 50대 상당이 추가로 깔려있다"는 가정으로 결정론적 가산 부하를
    만든다. ITS는 Poisson이 아니라 실측값이므로 시뮬레이션 시작 시 1회만 계산하고 런
    내내 고정 — _seed_other_device_load와 같은 설계 철학.
    ITS가 동기화되지 않은 상태(링크 없음)에서는 n_its_load=0으로 기존 동작과 동일하다.
    """
    try:
        traffic = TRAFFIC_FUSION_ENGINE.current_traffic()
    except Exception:
        traffic = {}
    links = traffic.get("links") or []
    hot_points = [
        (pt.get("lat"), pt.get("lng"), float(link.get("congestion_score") or 0.0))
        for link in links
        if link.get("congestion_score") and link.get("geometry")
        for pt in link["geometry"]
    ]
    for node in nodes:
        node["its_congestion_score"] = 0.0
        node["n_its_load"] = 0
        if not hot_points:
            continue
        radius_m = float(node.get("coverage_radius_m") or 400.0)
        nlat, nlng = node.get("lat"), node.get("lng")
        if nlat is None or nlng is None:
            continue
        best_score = 0.0
        for hlat, hlng, score in hot_points:
            if hlat is None or hlng is None:
                continue
            if haversine_m(nlat, nlng, hlat, hlng) <= radius_m:
                best_score = max(best_score, score)
        node["its_congestion_score"] = round(best_score, 3)
        node["n_its_load"] = round(best_score * 50)


def generate_network_nodes_for_bbox(bbox: dict | None, traffic_lambda: float = 5.0) -> list[dict]:
    """Generate synthetic network nodes for the route bounding box.

    Background vehicle load at each node is sampled from Poisson(λ × coverage_area_km²)
    where λ = traffic_lambda (vehicles/km², configurable by the user).
    """
    if not bbox:
        return []
    center_lat = (bbox["s"] + bbox["n"]) / 2
    center_lng = (bbox["w"] + bbox["e"]) / 2
    lat_span = max((bbox["n"] - bbox["s"]) / 3, 0.0004)
    lng_span = max((bbox["e"] - bbox["w"]) / 3, 0.0004)

    _RB_PER_BG_VEHICLE = 2.5   # average resource blocks consumed per background vehicle

    def _node_load(radius_m: float, capacity_rb: float) -> tuple[float, float, int]:
        """Return (load_rb, congestion_0_1, n_bg_vehicles)."""
        area_km2 = math.pi * (radius_m / 1000.0) ** 2
        n_bg = _poisson_sample(traffic_lambda * area_km2)
        load_rb = min(n_bg * _RB_PER_BG_VEHICLE, capacity_rb * 0.95)
        congestion = round(load_rb / max(capacity_rb, 1.0), 3)
        return round(load_rb, 1), congestion, n_bg

    cap_bs, cap_rsu, cap_edge = 120.0, 80.0, 150.0
    load_bs,   cong_bs,   n_bg_bs   = _node_load(450.0, cap_bs)
    load_rsu,  cong_rsu,  n_bg_rsu  = _node_load(220.0, cap_rsu)
    load_edge, cong_edge, n_bg_edge = _node_load(320.0, cap_edge)

    return [
        {
            "id": "BS-01",
            "name": "BS-01",
            "type": "base_station",
            "lat": center_lat + lat_span,
            "lng": center_lng - lng_span,
            "edge_latency_ms": 4.0,
            "coverage_radius_m": 450.0,
            "congestion_penalty": cong_bs,
            "congestion_score": cong_bs,
            "capacity": cap_bs,
            "load": load_bs,
            "n_background_vehicles": n_bg_bs,
            "source": "synthetic",
            "antenna_height_m": 25.0,
            "antenna_placement": "rooftop",
        },
        {
            "id": "RSU-01",
            "name": "RSU-01",
            "type": "roadside_unit",
            "lat": center_lat - lat_span * 0.2,
            "lng": center_lng + lng_span * 0.7,
            "edge_latency_ms": 2.5,
            "coverage_radius_m": 220.0,
            "congestion_penalty": cong_rsu,
            "congestion_score": cong_rsu,
            "capacity": cap_rsu,
            "load": load_rsu,
            "n_background_vehicles": n_bg_rsu,
            "source": "synthetic",
            "antenna_height_m": 8.0,
            "antenna_placement": "pole",
        },
        {
            "id": "EDGE-01",
            "name": "EDGE-01",
            "type": "edge_node",
            "lat": center_lat - lat_span,
            "lng": center_lng - lng_span * 0.3,
            "edge_latency_ms": 1.8,
            "coverage_radius_m": 320.0,
            "congestion_penalty": cong_edge,
            "congestion_score": cong_edge,
            "capacity": cap_edge,
            "load": load_edge,
            "n_background_vehicles": n_bg_edge,
            "source": "synthetic",
            "antenna_height_m": 15.0,
            "antenna_placement": "pole",
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
        "antenna_height_m": float(row["antenna_height_m"]) if row.get("antenna_height_m") is not None else None,
        "antenna_placement": row.get("antenna_placement"),
    }


def merged_network_nodes() -> list[dict]:
    """User-created stations from DB; fall back to synthetic nodes only when none exist."""
    user_nodes = [db_node_to_candidate(row) for row in fetch_network_nodes(source="user_created")]
    if user_nodes:
        return user_nodes
    return list(_state.get("synthetic_network_nodes") or [])


def _get_ego_allocated_rb(connected_node: Optional[dict]) -> Optional[float]:
    """
    Return the RBs allocated to the EGO vehicle's connected BS from the last
    allocation result, or None if allocation data is not available.
    """
    if not connected_node:
        return None
    alloc = _state.get("last_allocation_result")
    if not alloc:
        return None
    bs_id = str(connected_node.get("id") or connected_node.get("name") or "")
    for a in alloc.get("base_station_allocations", []):
        if str(a.get("bs_id", "")) == bs_id:
            demand = float(a.get("demand_rb", 0.0))
            bg_load = 0.0
            for node in (_state.get("network_nodes") or []):
                if str(node.get("id") or node.get("name") or "") == bs_id:
                    bg_load = float(node.get("load", 0.0))
                    break
            # EGO vehicle's share = total demand − background load
            ego_demand = max(0.0, demand - bg_load)
            allocated = float(a.get("allocated_rb", 0.0))
            # Apportion EGO's share by demand fraction
            if demand > 0:
                return round(allocated * (ego_demand / demand), 2)
            return round(min(5.0, allocated), 2)  # default: 5 RB minimum
    return None


def _refresh_realtime_bs_vehicle_counts(nodes: list[dict]) -> None:
    """다중차량 실험군 — 배경 차량이 있으면 각 차량을 _find_best_bs_light로 정확히 하나의
    기지국에 배정해서(커버리지 중첩 구간 중복 집계 방지) 실시간 연결 차량 수를
    node["n_background_vehicles"]에 덮어쓴다. route-cost 평가에서 쓰는 것과 동일한
    기지국 선택 알고리즘(_active_bs_selection)을 그대로 재사용한다.

    배경 차량이 없으면(vehicle_count=1) 아무것도 하지 않아 구역 설정 시 뽑힌 정적
    Poisson 샘플값이 그대로 유지된다 — 기존 동작과 100% 동일.
    """
    bg = _state.get("background_vehicles")
    if not bg:
        return
    counts: dict[str, int] = {}
    for v in bg:
        node, *_ = _find_best_bs_light(v["lat"], v["lng"], nodes)
        if node is not None:
            nid = str(node.get("id") or node.get("name") or "")
            counts[nid] = counts.get(nid, 0) + 1
    for node in nodes:
        nid = str(node.get("id") or node.get("name") or "")
        node["n_background_vehicles"] = counts.get(nid, 0)


def update_network_telemetry(vehicle_pos: dict | None) -> None:
    if not vehicle_pos:
        _state["network_telemetry"] = None
        return
    nodes = _state.get("network_nodes") or []
    if not nodes:
        _state["network_telemetry"] = None
        return
    _refresh_realtime_bs_vehicle_counts(nodes)
    buildings_gdf = _state.get("route_buildings")
    route_coords = _state.get("route_coords") or []
    density_penalty = round(max(len(route_coords) / 120.0, 1.0), 2)
    _policy = _state.get("policy_options") or {}
    candidates = analyze_candidates(
        vehicle_id="veh0",
        vehicle_lat=vehicle_pos["lat"],
        vehicle_lng=vehicle_pos["lng"],
        candidate_nodes=nodes,
        buildings_gdf=buildings_gdf if buildings_gdf is not None else BUILDING_REPOSITORY.query_by_bbox(0, 0, 0, 0),
        vehicle_density_penalty=density_penalty,
        network_mode=_policy.get("network_mode", "5G"),
    )
    if not candidates:
        _state["network_telemetry"] = None
        return
    best = candidates[0]
    selected_node = best["node"]
    selected_name = selected_node.get("name") or selected_node["id"]

    def _live_congestion(node: dict) -> float:
        """
        실시간 BS 혼잡도 = 현재 할당된 load / capacity. 정적인 congestion_score/
        congestion_penalty 필드(구역 설정 시 1회 Poisson 샘플, 이후 갱신 안 됨) 대신
        _run_resource_allocation()이 매 주기 갱신하는 node["load"]를 직접 반영해
        실시간으로 변하는 값을 보여준다. DB에서 로드된 실제 기지국(load/capacity 필드가
        없을 수 있음)에도 동일하게 동작 — capacity 기본값 100.0.
        """
        cap = float(node.get("capacity") or 100.0)
        load = float(node.get("load") or 0.0)
        return round(min(1.0, load / max(cap, 1.0)), 4)

    _state["network_telemetry"] = {
        "connected_node": {
            "id": selected_node["id"],
            "name": selected_name,
            "type": selected_node["type"],
            "lat": selected_node["lat"],
            "lng": selected_node["lng"],
            "congestion_score": _live_congestion(selected_node),
        },
        "network_nodes": [
            {
                "id": node["id"],
                "name": node.get("name") or node["id"],
                "node_type": node.get("type"),
                "lat": node["lat"],
                "lng": node["lng"],
                "source": node.get("source", "synthetic"),
                "congestion_score": _live_congestion(node),
                "load": node.get("load", 0.0),
                "antenna_height_m": node.get("antenna_height_m"),
                "antenna_placement": node.get("antenna_placement"),
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
                "congestion_score": _live_congestion(item["node"]),
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
        "l_base_ms": best["l_base_ms"],
        "l_signal_ms": best["l_signal_ms"],
        "l_queue_ms": best["l_queue_ms"],
        "connection_line": [
            {"lat": vehicle_pos["lat"], "lng": vehicle_pos["lng"]},
            {"lat": selected_node["lat"], "lng": selected_node["lng"]},
        ],
        "highlighted_buildings": best["highlighted_buildings"],
        "edge_stats": list(_state.get("edge_telemetry", [])),
        "route_edge_names": _state.get("route_edge_names", {}),
        "edge_avg_speeds": dict(_state.get("edge_avg_speeds", {})),
        "edge_history": list(_state.get("edge_history", [])),
        "custom_policy_debug": dict(_state.get("custom_policy_debug") or {}),
        "routing_mode": (_state.get("route_cost_result") or {}).get("routing_mode", ""),
        "ego_allocated_rb": _get_ego_allocated_rb(selected_node),
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


def load_osm_way_names(osm_file: Path) -> dict[str, str]:
    """Parse OSM XML and return {way_id_str: road_name} for named highway ways."""
    import re as _re
    way_names: dict[str, str] = {}
    try:
        tree = ET.parse(osm_file)
        root = tree.getroot()
        for way in root.findall("way"):
            tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
            if "highway" not in tags:
                continue
            name = tags.get("name:ko") or tags.get("name") or ""
            if name:
                way_names[way.get("id", "")] = name
    except Exception as exc:
        print(f"[OSM] Failed to load way names: {exc}", flush=True)
    return way_names


def sumo_edge_to_way_id(edge_id: str) -> str:
    """Extract OSM way ID from a SUMO edge ID (e.g. '-123456789#2' → '123456789')."""
    import re as _re
    m = _re.match(r"^-?(\d+)(?:#\d+)?$", edge_id.strip())
    return m.group(1) if m else ""


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
    way_names: dict[tuple[str, str], str] = {}

    for way in root.findall("way"):
        tags = {tag.attrib.get("k"): tag.attrib.get("v") for tag in way.findall("tag")}
        if "highway" not in tags:
            continue

        refs = [nd.attrib.get("ref") for nd in way.findall("nd")]
        refs = [ref for ref in refs if ref in nodes]
        if len(refs) < 2:
            continue

        way_name = tags.get("name", "") or tags.get("name:ko", "") or ""

        for ref in refs:
            lat, lng = nodes[ref]
            graph_nodes[ref] = {"lat": lat, "lng": lng}
            adjacency.setdefault(ref, [])

        for a, b in zip(refs, refs[1:]):
            alat, alng = nodes[a]
            blat, blng = nodes[b]
            dist = haversine_m(alat, alng, blat, blng)
            adjacency[a].append((b, dist))
            adjacency[b].append((a, dist))
            if way_name:
                way_names[(a, b)] = way_name
                way_names[(b, a)] = way_name

    if not graph_nodes:
        raise RuntimeError("OSM fallback graph를 만들 수 없습니다. bbox를 더 작게 선택해주세요.")

    graph = {"nodes": graph_nodes, "adjacency": adjacency, "way_names": way_names}
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


def astar_mock_path(graph: dict, start_id: str, end_id: str) -> list[str]:
    """
    A* search on mock OSM graph using Haversine heuristic.
    h(n) = straight-line distance from n to goal (admissible: never overestimates road distance).
    """
    end_nd = graph["nodes"].get(end_id)
    if end_nd is None:
        raise RuntimeError("A*: 도착 노드를 찾을 수 없습니다.")
    end_lat, end_lng = end_nd["lat"], end_nd["lng"]

    def h(node_id: str) -> float:
        nd = graph["nodes"].get(node_id, {})
        return haversine_m(nd.get("lat", 0.0), nd.get("lng", 0.0), end_lat, end_lng)

    # (f=g+h, g, node_id)
    open_set: list[tuple[float, float, str]] = [(h(start_id), 0.0, start_id)]
    g_score: dict[str, float] = {start_id: 0.0}
    prev: dict[str, str | None] = {start_id: None}
    seen: set[str] = set()

    while open_set:
        _, g, current = heappop(open_set)
        if current in seen:
            continue
        seen.add(current)
        if current == end_id:
            break
        for neighbor, weight in graph["adjacency"].get(current, []):
            ng = g + weight
            if ng < g_score.get(neighbor, float("inf")):
                g_score[neighbor] = ng
                prev[neighbor] = current
                heappush(open_set, (ng + h(neighbor), ng, neighbor))

    if end_id not in prev:
        raise RuntimeError("A*: 경로를 찾을 수 없습니다. bbox를 더 작게 선택해주세요.")

    path: list[str] = []
    cur: str | None = end_id
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return path


def mock_route_coords(graph: dict, path: list[str]) -> list[list[float]]:
    return [[graph["nodes"][node_id]["lat"], graph["nodes"][node_id]["lng"]] for node_id in path]


def _generate_background_vehicles(graph: dict, bbox: dict, count: int, rng: random.Random = random) -> list[dict]:
    """다중차량 실험군 — bbox 안에서 무작위 출발/도착으로 배경 차량 `count`대의 경로를 생성한다.

    ITS 실시간 교통 동기화 데이터(`/traffic/sync-its`)가 해당 구역에 있으면 혼잡 도로
    근처 노드를 더 자주 출발/도착지로 뽑도록 가중치를 주고, 없으면 균등 무작위로 폴백한다.
    시뮬레이션 시작 시 1회만 호출되므로 N=1000이어도 매 틱 비용은 없다.

    rng : random.Random(seed) 인스턴스를 넘기면 재현 가능, 기본값(전역 random 모듈)이면
          기존과 동일한 비결정적 동작.
    """
    if count <= 0:
        return []

    nodes = graph.get("nodes", {})
    candidate_ids = [
        nid for nid, d in nodes.items()
        if bbox["s"] <= d["lat"] <= bbox["n"] and bbox["w"] <= d["lng"] <= bbox["e"]
    ]
    if len(candidate_ids) < 2:
        return []

    weights = None
    try:
        traffic = TRAFFIC_FUSION_ENGINE.current_traffic()
        hot_points = [
            (pt.get("lat"), pt.get("lng"), float(link.get("congestion_score") or 0.0))
            for link in (traffic.get("links") or [])
            if link.get("congestion_score") and link.get("geometry")
            for pt in link["geometry"]
        ]
    except Exception:
        hot_points = []

    if hot_points:
        weights = []
        for nid in candidate_ids:
            nlat, nlng = nodes[nid]["lat"], nodes[nid]["lng"]
            best_score = 0.0
            for hlat, hlng, score in hot_points:
                if hlat is None or hlng is None:
                    continue
                if haversine_m(nlat, nlng, hlat, hlng) <= 150.0:
                    best_score = max(best_score, score)
            weights.append(1.0 + best_score * 4.0)

    background_vehicles: list[dict] = []
    attempts = 0
    max_attempts = count * 4
    while len(background_vehicles) < count and attempts < max_attempts:
        attempts += 1
        if weights:
            a, b = rng.choices(candidate_ids, weights=weights, k=2)
        else:
            a, b = rng.sample(candidate_ids, 2)
        if a == b:
            continue
        try:
            path = shortest_mock_path(graph, a, b)
        except RuntimeError:
            continue
        if len(path) < 2:
            continue
        coords = mock_route_coords(graph, path)
        background_vehicles.append({
            "id": f"bg{len(background_vehicles)}",
            "route_coords": coords,
            "progress": 0.0,
            "lat": coords[0][0],
            "lng": coords[0][1],
            "speed_kmh": round(rng.uniform(25.0, 50.0), 1),
        })
    return background_vehicles


# ── Route cost helpers ────────────────────────────────────────────────────────
def build_sumo_edge_data(net, edges: list[str]) -> list[dict]:
    """Extract midpoint, length, and travel_time from sumolib edges."""
    result = []
    for eid in edges:
        try:
            e = net.getEdge(eid)
            shape = e.getShape()
            if not shape:
                continue
            mid_x = sum(p[0] for p in shape) / len(shape)
            mid_y = sum(p[1] for p in shape) / len(shape)
            lon, lat = net.convertXY2LonLat(mid_x, mid_y)
            length_m = e.getLength()
            lanes = e.getLanes()
            speed_mps = lanes[0].getSpeed() if lanes else 13.89
            result.append({
                "edge_id": eid,
                "midpoint_lat": lat,
                "midpoint_lng": lon,
                "distance_m": round(length_m, 2),
                "travel_time_s": round(length_m / max(speed_mps, 0.1), 2),
            })
        except Exception:
            continue
    return result


def build_mock_edge_data(graph: dict, path: list[str]) -> list[dict]:
    """Extract midpoint, length, and travel_time from mock graph node pairs."""
    result = []
    for a_id, b_id in zip(path, path[1:]):
        a = graph["nodes"].get(a_id)
        b = graph["nodes"].get(b_id)
        if not a or not b:
            continue
        dist_m = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        result.append({
            "edge_id": f"{a_id}_{b_id}",
            "midpoint_lat": (a["lat"] + b["lat"]) / 2,
            "midpoint_lng": (a["lng"] + b["lng"]) / 2,
            "distance_m": round(dist_m, 2),
            "travel_time_s": round(dist_m / 9.0, 2),
        })
    return result


def _weights_to_dict() -> dict:
    return {
        "w_distance":      _route_cost_weights.w_distance,
        "w_time":          _route_cost_weights.w_time,
        "w_latency":       _route_cost_weights.w_latency,
        "w_load":          _route_cost_weights.w_load,
        "w_handover":      _route_cost_weights.w_handover,
        "w_blockage":      _route_cost_weights.w_blockage,
        "w_coverage_risk": _route_cost_weights.w_coverage_risk,
    }


def _norms_to_dict() -> dict:
    return {
        "distance_km": _norm_scales.distance_km,
        "time_min":    _norm_scales.time_min,
        "latency_ms":  _norm_scales.latency_ms,
        "loss_db":     _norm_scales.loss_db,
    }


def _path_cost_to_dict(result: "PathCostResult", routing_mode: str) -> dict:
    """Serialise a PathCostResult to JSON-compatible dict."""
    return {
        "available":           True,
        "routing_mode":        routing_mode,
        "edge_count":          result.edge_count,
        "total_cost":          result.total_cost,
        "distance_time_cost":  result.distance_time_cost,
        "total_distance_m":    result.total_distance_m,
        "total_travel_time_s": result.total_travel_time_s,
        "avg_latency_ms":      result.avg_latency_ms,
        "max_latency_ms":      result.max_latency_ms,
        "handover_count":      result.handover_count,
        "coverage_risk":       result.coverage_risk,
        "avg_loss_db":         result.avg_loss_db,
        "covered_pct":         result.covered_pct,
        "summary":             result.summary,
        "weights":             _weights_to_dict(),
        "norm_scales":         _norms_to_dict(),
        "per_edge": [
            {
                "edge_id":         r.edge_id,
                "distance_m":      r.distance_m,
                "latency_ms":      r.latency_ms,
                "best_node_id":    r.best_node_id,
                "best_node_name":  r.best_node_name,
                "load_ratio":      r.load_ratio,
                "midpoint_lat":    r.midpoint_lat,
                "midpoint_lng":    r.midpoint_lng,
                "handover":        r.handover_occurred,
                "within_coverage": r.within_coverage,
                "loss_db":         r.loss_db,
                "total_cost":      r.total_cost,
                "components":      r.components,
            }
            for r in result.edge_results
        ],
    }


def _run_resource_allocation(
    origin: dict,
    raw_k_paths: Optional[list],
    algo_id: str,
    background_vehicles: Optional[list] = None,
) -> Optional[Any]:
    """
    Run resource allocation and apply results to _state["network_nodes"] in-place.

    Fixes applied:
      P1 — uses origin coords as the initial vehicle position (not None vehicle_pos)
      P3 — called BEFORE path selection so Dijkstra/K-path uses updated loads
      P4 — runs look_ahead_bs_scan from path start node and passes result to
            build_resource_demand_map so lookahead_resource_allocation works correctly

    `background_vehicles` (다중차량 실험군) are folded into the vehicle list so the
    allocation reflects the full fleet's demand, not just the target vehicle.

    Returns AllocationOutput object, or None if allocation is unavailable/failed.
    """
    if not RESOURCE_DEMAND_AVAILABLE:
        return None
    bs_nodes = _state.get("network_nodes") or []
    if not bs_nodes:
        return None

    graph = _state.get("mock_graph") or {}
    road_nodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}

    # P1: use origin as initial vehicle (vehicle_pos is None at simulation start)
    vehicles = [{"lat": float(origin["lat"]), "lng": float(origin["lng"]), "speed_mps": 0.0}]
    for bv in (background_vehicles or []):
        vehicles.append({
            "lat": float(bv["lat"]),
            "lng": float(bv["lng"]),
            "speed_mps": round(bv.get("speed_kmh", 30.0) / 3.6, 2),
        })

    # Simple path candidates for demand estimation (no KPathCandidate needed here)
    from types import SimpleNamespace
    simple_candidates = [
        SimpleNamespace(path=p, rank=i) for i, p in enumerate(raw_k_paths or [])
    ]

    # P4: look-ahead from first path node so lookahead_resource_allocation is live
    la_result = None
    if LOOK_AHEAD_AVAILABLE and road_nodes and raw_k_paths:
        try:
            start_nid = raw_k_paths[0][0] if raw_k_paths[0] else None
            if start_nid and start_nid in road_nodes:
                from app.services.routing.look_ahead_scan import look_ahead_bs_scan as _las
                _policy = _state.get("policy_options") or {}
                la_result = _las(
                    current_node_id=start_nid,
                    graph=graph,
                    road_nodes=road_nodes,
                    bs_nodes=bs_nodes,
                    lookahead_hops=_policy.get("lookahead_k", 3),
                )
        except Exception as _la_e:
            print(f"[ALLOC] Look-ahead failed: {_la_e}", flush=True)

    try:
        demand_map = build_resource_demand_map(
            base_stations=bs_nodes,
            vehicles=vehicles,
            road_graph=graph,
            lookahead_results=la_result,
            route_candidates=simple_candidates,
        )

        # Merge background load: each BS's current load represents background
        # traffic that is independent of the EGO vehicle.  Without this step,
        # demand_rb ≈ 0 (single-vehicle) and every allocation algorithm sees
        # an empty network, producing identical meaningless results.
        _DEFAULT_RB = 100.0
        bs_load_map = {
            str(bs.get("id") or bs.get("name") or ""): (
                float(bs.get("load", 0.0)),
                float(bs.get("capacity") or _DEFAULT_RB),
            )
            for bs in bs_nodes
        }
        for bs_id, d in demand_map.items():
            bg_load, cap = bs_load_map.get(bs_id, (0.0, 100.0))
            if bg_load > 0:
                combined = d.estimated_resource_demand + bg_load
                demand_map[bs_id] = _BSResourceDemand(
                    base_station_id=d.base_station_id,
                    nearby_vehicle_count=d.nearby_vehicle_count,
                    expected_connected_vehicle_count=d.expected_connected_vehicle_count,
                    nearby_traffic_density=d.nearby_traffic_density,
                    average_speed=d.average_speed,
                    estimated_resource_demand=round(combined, 4),
                    capacity=cap,
                    demand_capacity_ratio=round(min(combined / max(cap, 1.0), 9.99), 4),
                )

        alloc_inp = AllocationInput(
            base_stations=bs_nodes,
            vehicles=vehicles,
            resource_demand_map={k: v.to_dict() for k, v in demand_map.items()},
            config=AllocationConfig(),
        )
        alloc_out = ALLOCATION_REGISTRY.compute(alloc_inp, algorithm_id=algo_id)
        apply_allocation_to_network_nodes(alloc_out, _state["network_nodes"])
        _state["allocation_algorithm"] = alloc_out.algorithm_id
        _state["last_allocation_result"] = alloc_out.to_dict()
        print(
            f"[ALLOC] {alloc_out.algorithm_id}: "
            f"util={alloc_out.allocation_result.get('total_utilization', 0):.1%}, "
            f"overloaded={alloc_out.allocation_result.get('overloaded_bs_count', 0)}",
            flush=True,
        )
        return alloc_out
    except Exception as exc:
        print(f"[ALLOC] Resource allocation failed: {exc}", flush=True)
        return None


def _run_algorithm_comparison() -> None:
    """
    Re-evaluate the already-computed route under every latency algorithm,
    every BS-selection algorithm, and every resource-allocation algorithm —
    so the Comparison tab can show how DIFFERENT algorithm *settings* perform
    on the same route, instead of only comparing K-path route alternatives.

    Route-finding (dijkstra vs astar) is intentionally NOT included: A* has no
    SUMO-mode implementation, so it would just duplicate the dijkstra path.

    Runs in a background thread (mirrors the existing _bg_cost_eval pattern)
    since each evaluate_path() re-run repeats the building-intersection
    analysis and can take several seconds.
    """
    edge_data = _state.get("route_cost_edge_data")
    nodes = _state.get("network_nodes") or []
    if not edge_data or not nodes or not ROUTE_COST_AVAILABLE:
        _state["algorithm_comparison"] = {
            "status": "error",
            "reason": "시뮬레이션을 먼저 실행하세요.",
        }
        return

    buildings = _state.get("route_buildings")

    # Resource-allocation sweep mutates _state["last_allocation_result"] /
    # ["allocation_algorithm"] / network_nodes[].load on every call — snapshot
    # the algorithm so we can restore live state afterwards (finally block).
    _prev_alloc_algo = _state.get("allocation_algorithm") or "traffic_aware_allocation"

    _state["algorithm_comparison"] = {
        "status": "running",
        "by_latency": {},
        "by_bs_selection": {},
        "by_allocation": {},
    }

    try:
        for meta in LATENCY_REGISTRY.list_algorithms():
            algo_id = meta["id"]
            try:
                result = evaluate_path(
                    edge_data, nodes, buildings,
                    _route_cost_weights, _norm_scales,
                    latency_algorithm_id=algo_id,
                )
                _state["algorithm_comparison"]["by_latency"][algo_id] = {
                    "avg_latency_ms": result.avg_latency_ms,
                    "max_latency_ms": result.max_latency_ms,
                    "total_cost": result.total_cost,
                    "handover_count": result.handover_count,
                    "coverage_risk": result.coverage_risk,
                }
            except Exception as exc:
                print(f"[CMP] latency algo {algo_id} failed: {exc}", flush=True)

        for algo_id in ("lowest_latency_bs", "nearest_bs", "load_balanced_bs"):
            try:
                result = evaluate_path(
                    edge_data, nodes, buildings,
                    _route_cost_weights, _norm_scales,
                    bs_selection_algo=algo_id,
                )
                _state["algorithm_comparison"]["by_bs_selection"][algo_id] = {
                    "avg_latency_ms": result.avg_latency_ms,
                    "total_cost": result.total_cost,
                    "handover_count": result.handover_count,
                    "coverage_risk": result.coverage_risk,
                }
            except Exception as exc:
                print(f"[CMP] bs-selection algo {algo_id} failed: {exc}", flush=True)

        if RESOURCE_DEMAND_AVAILABLE:
            _live_origin = _state.get("vehicle_pos")
            _bg_vehicles = _state.get("background_vehicles")
            _alias_ids = {"proportional_allocation", "custom_allocation_algorithm"}
            if _live_origin and _live_origin.get("lat") is not None:
                for meta in ALLOCATION_REGISTRY.list_algorithms():
                    algo_id = meta["id"]
                    if algo_id in _alias_ids:
                        continue
                    try:
                        alloc_out = _run_resource_allocation(
                            _live_origin, [], algo_id, _bg_vehicles,
                        )
                        if alloc_out:
                            ar = alloc_out.allocation_result
                            _state["algorithm_comparison"]["by_allocation"][algo_id] = {
                                "total_utilization": ar.get("total_utilization", 0.0),
                                "overloaded_bs_count": ar.get("overloaded_bs_count", 0),
                                "total_deficit_rb": ar.get("total_deficit_rb", 0.0),
                            }
                    except Exception as exc:
                        print(f"[CMP] allocation algo {algo_id} failed: {exc}", flush=True)

        _state["algorithm_comparison"]["status"] = "done"
        _state["algorithm_comparison"]["generated_at"] = time.time()
    except Exception as exc:
        print(f"[CMP] algorithm comparison failed: {exc}", flush=True)
        _state["algorithm_comparison"] = {"status": "error", "reason": str(exc)}
    finally:
        # Restore live resource-allocation state to the user's actual config —
        # the sweep above left it pointing at whichever algorithm ran last.
        if RESOURCE_DEMAND_AVAILABLE:
            _live_origin = _state.get("vehicle_pos")
            if _live_origin and _live_origin.get("lat") is not None:
                try:
                    _run_resource_allocation(
                        _live_origin, [], _prev_alloc_algo, _state.get("background_vehicles"),
                    )
                except Exception as exc:
                    print(f"[CMP] failed to restore allocation state: {exc}", flush=True)


def _store_route_cost(edge_data: list[dict], routing_mode: str) -> None:
    """Evaluate the selected route with full network cost and persist to _state."""
    import time as _time
    nodes = _state.get("network_nodes") or []
    if not edge_data or not nodes or not ROUTE_COST_AVAILABLE:
        return
    try:
        _t0 = _time.perf_counter()
        result = evaluate_path(
            edge_data, nodes,
            _state.get("route_buildings"),
            _route_cost_weights,
            _norm_scales,
        )
        exec_ms = (_time.perf_counter() - _t0) * 1000.0

        _state["route_cost_result"] = _path_cost_to_dict(result, routing_mode)
        _state["route_cost_result"]["street_names"] = _resolve_path_street_names(
            [e.edge_id for e in result.edge_results]
        )
        _state["route_cost_version"] = _state.get("route_cost_version", 0) + 1
        # route_cost_result["per_edge"] is the OUTPUT shape (EdgeCostResult fields) and is
        # missing travel_time_s, so the algorithm-comparison sweep needs the raw INPUT
        # edge_data (with travel_time_s) cached separately to re-run evaluate_path().
        _state["route_cost_edge_data"] = edge_data

        if ROUTE_METRICS_AVAILABLE:
            try:
                m = from_path_cost(routing_mode, result, nodes, exec_ms)
                _state["algorithm_metrics"][routing_mode] = m.to_dict()
            except Exception as _m_exc:
                print(f"[METRICS] route metrics failed: {_m_exc}", flush=True)

        print(
            f"[COST] Route evaluated: total={result.total_cost:.3f} ({routing_mode}), "
            f"avg_lat={result.avg_latency_ms:.1f}ms, coverage_risk={result.coverage_risk:.1%}, "
            f"eval={exec_ms:.1f}ms",
            flush=True,
        )
        _store_simulation_summary()
    except Exception as exc:
        print(f"[COST] Route evaluation failed: {exc}", flush=True)


def _resolve_path_street_names(edge_ids: list[str]) -> list[str]:
    """
    Resolve a deduplicated, ordered street-name sequence for any edge-id path (the
    main driven route or a K-path candidate), so the comparison UI can show *which
    road* each algorithm/rank actually represents instead of an opaque label. Reuses
    the main route's name map first (K-path alternates share most edges with the
    selected route via Yen's algorithm), then falls back to the same OSM-way/mock-way
    lookups the main route uses, with forward/backward name propagation for edges that
    resolve to nothing — same approach as the Report tab's structured summary
    (_fill_edge_names in simulation_summary.py).
    """
    from types import SimpleNamespace
    from app.services.analysis.simulation_summary import _fill_edge_names

    raw_names: dict[str, str] = dict(_state.get("route_edge_names") or {})
    missing = [eid for eid in edge_ids if eid not in raw_names]

    if missing:
        if _state.get("sim_mode") == "mock":
            way_names = (_state.get("mock_graph") or {}).get("way_names", {})
            for eid in missing:
                parts = eid.split("_")
                if len(parts) == 2:
                    name = way_names.get((parts[0], parts[1])) or way_names.get((parts[1], parts[0]))
                    if name:
                        raw_names[eid] = name
        else:
            osm_file = _state.get("osm_file")
            if osm_file:
                try:
                    osm_names = load_osm_way_names(Path(osm_file))
                    for eid in missing:
                        name = osm_names.get(sumo_edge_to_way_id(eid))
                        if name:
                            raw_names[eid] = name
                except Exception:
                    pass

    filled = _fill_edge_names([SimpleNamespace(edge_id=eid) for eid in edge_ids], raw_names)

    ordered: list[str] = []
    for eid in edge_ids:
        name = filled.get(eid) or eid
        if not ordered or ordered[-1] != name:
            ordered.append(name)
    return ordered


def _store_k_candidates(
    k_edge_data: list[tuple[list[str], list[dict]]],
    allocation_output: Optional[dict] = None,
) -> list:
    """
    Evaluate K candidate paths with optional allocation impact and persist to _state.

    When allocation_output is provided:
    - resource_deficit_cost is computed per candidate (BSes visited)
    - resource_deficit_cost × w_resource_deficit is added to total_cost
    - Sorting and selection use the allocation-adjusted total_cost

    Returns the sorted list[KPathCandidate] (rank 0 = best allocation-aware path).
    """
    import time as _time
    nodes = _state.get("network_nodes") or []
    if not k_edge_data or not nodes or not ROUTE_COST_AVAILABLE:
        return []
    # Cache so the periodic allocation-refresh loop can re-score these same K-paths
    # under updated load/congestion without re-running Yen's K-shortest-paths search.
    _state["k_path_edge_data"] = k_edge_data
    try:
        _t0 = _time.perf_counter()
        k_results = evaluate_k_candidates(
            k_edge_data, nodes,
            _state.get("route_buildings"),
            _route_cost_weights,
            _norm_scales,
            allocation_output=allocation_output,
        )
        exec_ms = (_time.perf_counter() - _t0) * 1000.0

        if ROUTE_METRICS_AVAILABLE and k_results:
            try:
                km_list = from_k_candidates(k_results, nodes, exec_ms)
                k_metrics_objs: dict[str, "RouteMetrics"] = {}
                for km in km_list:
                    _state["algorithm_metrics"][km.algorithm] = km.to_dict()
                    k_metrics_objs[km.algorithm] = km
                # Include existing route metric in comparison if available
                for algo, md in list(_state["algorithm_metrics"].items()):
                    if algo.startswith("_") or algo in k_metrics_objs:
                        continue
                    try:
                        k_metrics_objs[algo] = RouteMetrics(**md)
                    except Exception:
                        pass
                if len(k_metrics_objs) > 1:
                    _state["algorithm_metrics"]["_comparison"] = compare_algorithms(
                        k_metrics_objs
                    )
            except Exception as _m_exc:
                print(f"[METRICS] K-path metrics failed: {_m_exc}", flush=True)

        _state["k_path_candidates"] = {
            "available":  True,
            "k":          len(k_results),
            "weights":    _weights_to_dict(),
            "norm_scales": _norms_to_dict(),
            "candidates": [
                {
                    "rank":                  r.rank,
                    "selected":              r.selected,
                    "path":                  r.path,
                    "total_cost":            r.total_cost,
                    "distance_time_cost":    r.distance_time_cost,
                    "total_distance_m":      r.total_distance_m,
                    "total_travel_time_s":   r.total_travel_time_s,
                    "avg_latency_ms":        r.avg_latency_ms,
                    "max_latency_ms":        r.max_latency_ms,
                    "handover_count":        r.handover_count,
                    "coverage_risk":         r.coverage_risk,
                    "avg_loss_db":           r.avg_loss_db,
                    "covered_pct":           r.covered_pct,
                    "selected_bs_sequence":     r.selected_bs_sequence,
                    "resource_deficit_cost":    r.resource_deficit_cost,
                    "expected_latency_impact":  r.expected_latency_impact,
                    "street_names": _resolve_path_street_names(
                        [e.edge_id for e in (r.path_cost_result.edge_results if r.path_cost_result else [])]
                    ),
                    "per_edge": [
                        {
                            "edge_id":         e.edge_id,
                            "distance_m":      e.distance_m,
                            "latency_ms":      e.latency_ms,
                            "best_node_id":    e.best_node_id,
                            "best_node_name":  e.best_node_name,
                            "load_ratio":      e.load_ratio,
                            "midpoint_lat":    e.midpoint_lat,
                            "midpoint_lng":    e.midpoint_lng,
                            "handover":        e.handover_occurred,
                            "within_coverage": e.within_coverage,
                            "loss_db":         e.loss_db,
                            "total_cost":      e.total_cost,
                            "components":      e.components,
                        }
                        for e in (r.path_cost_result.edge_results if r.path_cost_result else [])
                    ],
                }
                for r in k_results
            ],
            "allocation_algorithm": (
                allocation_output.get("algorithm_id", "") if allocation_output else ""
            ),
        }
        best = k_results[0] if k_results else None
        print(
            f"[COST] K-path candidates: {len(k_results)} paths evaluated. "
            + (f"Best: rank=0, total_cost={best.total_cost:.3f}" if best else "")
            + (f", alloc={allocation_output.get('algorithm_id','')}" if allocation_output else ""),
            flush=True,
        )
        _store_simulation_summary()   # rebuild with K-path data
        return k_results
    except Exception as exc:
        print(f"[COST] K-path evaluation failed: {exc}", flush=True)
        return []


_k_refresh_running = threading.Lock()


def _refresh_k_candidates_async(allocation_output: Optional[dict]) -> None:
    """
    Periodic (~2s) background re-score of the cached K-path candidates under the
    latest allocation output, so "경로 대안 비교" doesn't stay frozen at simulation-
    start costs for the whole run. Skips if a previous refresh is still running
    (full re-score includes building-intersection analysis and can take longer than
    one 2s cycle) rather than letting threads pile up out of order.
    """
    edge_data = _state.get("k_path_edge_data")
    if not edge_data or not _k_refresh_running.acquire(blocking=False):
        return
    try:
        _store_k_candidates(edge_data, allocation_output=allocation_output)
    finally:
        _k_refresh_running.release()


# ── Yen's K-shortest paths ────────────────────────────────────────────────────
def _dijkstra_blocked_mock(
    graph: dict,
    start_id: str,
    end_id: str,
    blocked_nodes: frozenset,
    blocked_edges: frozenset,
) -> tuple[float, list[str]]:
    """Distance-only Dijkstra on mock graph supporting Yen's blocked sets."""
    pq = [(0.0, start_id)]
    dist: dict[str, float] = {start_id: 0.0}
    prev: dict[str, Optional[str]] = {start_id: None}

    while pq:
        cost, node = heappop(pq)
        if node == end_id:
            break
        if cost > dist.get(node, float("inf")) + 1e-9:
            continue
        for nxt, d in graph["adjacency"].get(node, []):
            if nxt in blocked_nodes:
                continue
            if (node, nxt) in blocked_edges:
                continue
            new_cost = cost + d
            if new_cost < dist.get(nxt, float("inf")):
                dist[nxt] = new_cost
                prev[nxt] = node
                heappush(pq, (new_cost, nxt))

    if end_id not in prev:
        return float("inf"), []
    path: list[str] = []
    cur: Optional[str] = end_id
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return dist.get(end_id, float("inf")), path


def _mock_edge_dist(graph: dict, a: str, b: str) -> float:
    """Look up distance of edge a→b in mock adjacency list."""
    for nxt, d in graph["adjacency"].get(a, []):
        if nxt == b:
            return d
    return 0.0


def yen_k_paths_mock(
    graph: dict,
    start_id: str,
    end_id: str,
    k: int = 5,
) -> list[list[str]]:
    """
    Yen's K-shortest simple paths on the mock OSM graph (distance-only).
    Returns up to K node-ID paths, sorted shortest-distance first.
    Path generation uses distance only for diversity; network cost is
    applied separately in evaluate_k_candidates().
    """
    cost0, path0 = _dijkstra_blocked_mock(graph, start_id, end_id, frozenset(), frozenset())
    if not path0:
        return []

    A: list[tuple[float, list[str]]] = [(cost0, path0)]
    B: list[tuple[float, list[str]]] = []

    while len(A) < k:
        _, prev_path = A[-1]

        for i in range(len(prev_path) - 1):
            spur_node = prev_path[i]
            root_path = prev_path[: i + 1]
            root_cost = sum(
                _mock_edge_dist(graph, root_path[j], root_path[j + 1])
                for j in range(len(root_path) - 1)
            )

            blocked_edges: set = set()
            blocked_nodes: set = set(root_path[:-1])

            for _, a_path in A:
                if len(a_path) > i and a_path[: i + 1] == root_path and i + 1 < len(a_path):
                    blocked_edges.add((a_path[i], a_path[i + 1]))

            spur_cost, spur_path = _dijkstra_blocked_mock(
                graph, spur_node, end_id,
                frozenset(blocked_nodes), frozenset(blocked_edges),
            )
            if spur_path:
                total_path = root_path[:-1] + spur_path
                total_cost = root_cost + spur_cost
                if (
                    not any(p == total_path for _, p in B)
                    and not any(p == total_path for _, p in A)
                ):
                    heappush(B, (total_cost, total_path))

        if not B:
            break
        cost_b, path_b = heappop(B)
        if any(p == path_b for _, p in A):
            break
        A.append((cost_b, path_b))

    return [p for _, p in A]


def _dijkstra_blocked_sumo(
    net,
    from_edge: str,
    to_edge: str,
    blocked_nodes: frozenset,
    blocked_edges: frozenset,
) -> tuple[float, list[str]]:
    """
    Distance-only Dijkstra on sumolib edge graph supporting Yen's blocked sets.
    'Nodes' in this graph are sumolib edge IDs.
    """
    pq = [(0.0, from_edge)]
    dist: dict[str, float] = {from_edge: 0.0}
    prev: dict[str, Optional[str]] = {from_edge: None}

    while pq:
        cost, cur_id = heappop(pq)
        if cur_id == to_edge:
            break
        if cost > dist.get(cur_id, float("inf")) + 1e-9:
            continue
        try:
            cur_edge = net.getEdge(cur_id)
        except Exception:
            continue
        for nxt_edge in cur_edge.getOutgoing().keys():
            nxt_id = nxt_edge.getID()
            if nxt_id.startswith(":"):
                continue
            if nxt_id in blocked_nodes:
                continue
            if (cur_id, nxt_id) in blocked_edges:
                continue
            new_cost = cost + nxt_edge.getLength()
            if new_cost < dist.get(nxt_id, float("inf")):
                dist[nxt_id] = new_cost
                prev[nxt_id] = cur_id
                heappush(pq, (new_cost, nxt_id))

    if to_edge not in prev:
        return float("inf"), []
    path: list[str] = []
    cur: Optional[str] = to_edge
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return dist.get(to_edge, float("inf")), path


def yen_k_paths_sumo(
    net,
    from_edge: str,
    to_edge: str,
    k: int = 5,
) -> list[list[str]]:
    """
    Yen's K-shortest simple paths on the sumolib edge graph (distance-only).
    'Nodes' are sumolib edge IDs.  Returns up to K paths, shortest first.
    """
    cost0, path0 = _dijkstra_blocked_sumo(net, from_edge, to_edge, frozenset(), frozenset())
    if not path0:
        return []

    A: list[tuple[float, list[str]]] = [(cost0, path0)]
    B: list[tuple[float, list[str]]] = []

    while len(A) < k:
        _, prev_path = A[-1]

        for i in range(len(prev_path) - 1):
            spur_edge = prev_path[i]
            root_path = prev_path[: i + 1]
            root_cost = 0.0
            for j in range(len(root_path) - 1):
                try:
                    root_cost += net.getEdge(root_path[j]).getLength()
                except Exception:
                    pass

            blocked_edges: set = set()
            blocked_nodes: set = set(root_path[:-1])

            for _, a_path in A:
                if len(a_path) > i and a_path[: i + 1] == root_path and i + 1 < len(a_path):
                    blocked_edges.add((a_path[i], a_path[i + 1]))

            spur_cost, spur_path = _dijkstra_blocked_sumo(
                net, spur_edge, to_edge,
                frozenset(blocked_nodes), frozenset(blocked_edges),
            )
            if spur_path:
                total_path = root_path[:-1] + spur_path
                total_cost = root_cost + spur_cost
                if (
                    not any(p == total_path for _, p in B)
                    and not any(p == total_path for _, p in A)
                ):
                    heappush(B, (total_cost, total_path))

        if not B:
            break
        cost_b, path_b = heappop(B)
        if any(p == path_b for _, p in A):
            break
        A.append((cost_b, path_b))

    return [p for _, p in A]


def build_sumo_k_edge_data(
    net,
    paths: list[list[str]],
) -> list[tuple[list[str], list[dict]]]:
    """Convert K sumolib edge paths to (path_ids, edge_data_list) tuples."""
    return [(path, build_sumo_edge_data(net, path)) for path in paths]


def build_mock_k_edge_data(
    graph: dict,
    paths: list[list[str]],
) -> list[tuple[list[str], list[dict]]]:
    """Convert K mock node paths to (path_ids, edge_data_list) tuples."""
    return [(path, build_mock_edge_data(graph, path)) for path in paths]


def astar_sumo_path(net, from_edge: str, to_edge: str) -> list[str]:
    """
    A* search on the sumolib edge graph using a Haversine heuristic.
    'Nodes' in this graph are sumolib edge IDs (see _dijkstra_blocked_sumo).
    Cost is distance-only — the same metric as baseline Dijkstra — so this is a
    different *search strategy* reaching an equivalent-or-better distance-shortest
    path, mirroring astar_mock_path's role in the mock-graph mode.
    """
    try:
        end_edge_obj = net.getEdge(to_edge)
        end_shape = end_edge_obj.getShape()
        end_mx = sum(p[0] for p in end_shape) / len(end_shape)
        end_my = sum(p[1] for p in end_shape) / len(end_shape)
        end_lon, end_lat = net.convertXY2LonLat(end_mx, end_my)
    except Exception:
        raise RuntimeError("A*(SUMO): 도착 엣지를 찾을 수 없습니다.")

    heuristic_cache: dict[str, float] = {}

    def h(edge_id: str) -> float:
        if edge_id in heuristic_cache:
            return heuristic_cache[edge_id]
        try:
            e = net.getEdge(edge_id)
            shape = e.getShape()
            mx = sum(p[0] for p in shape) / len(shape)
            my = sum(p[1] for p in shape) / len(shape)
            lon, lat = net.convertXY2LonLat(mx, my)
            val = haversine_m(lat, lon, end_lat, end_lon)
        except Exception:
            val = 0.0
        heuristic_cache[edge_id] = val
        return val

    open_set: list[tuple[float, float, str]] = [(h(from_edge), 0.0, from_edge)]
    g_score: dict[str, float] = {from_edge: 0.0}
    prev: dict[str, Optional[str]] = {from_edge: None}
    seen: set[str] = set()

    while open_set:
        _, g, current = heappop(open_set)
        if current in seen:
            continue
        seen.add(current)
        if current == to_edge:
            break
        try:
            cur_edge = net.getEdge(current)
        except Exception:
            continue
        for nxt_edge in cur_edge.getOutgoing().keys():
            nxt_id = nxt_edge.getID()
            if nxt_id.startswith(":"):
                continue
            ng = g + nxt_edge.getLength()
            if ng < g_score.get(nxt_id, float("inf")):
                g_score[nxt_id] = ng
                prev[nxt_id] = current
                heappush(open_set, (ng + h(nxt_id), ng, nxt_id))

    if to_edge not in prev:
        raise RuntimeError(f"A*(SUMO): 경로를 찾을 수 없습니다: {from_edge} → {to_edge}")

    path: list[str] = []
    cur: Optional[str] = to_edge
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()
    return path


def network_weighted_sumo_path(
    net,
    from_edge: str,
    to_edge: str,
    nodes: list[dict],
    weights: "CostWeights",
    stop_evt: threading.Event | None = None,
) -> list[str]:
    """
    Dijkstra on sumolib graph weighted by network cost (skip_buildings=True for speed).
    Returns ordered list of SUMO edge IDs.
    """
    edge_cost_cache: dict[str, tuple[float, Optional[str]]] = {}

    def _edge_cost(edge_id: str) -> tuple[float, Optional[str]]:
        if edge_id in edge_cost_cache:
            return edge_cost_cache[edge_id]
        try:
            e = net.getEdge(edge_id)
            shape = e.getShape()
            if not shape:
                edge_cost_cache[edge_id] = (1.0, None)
                return (1.0, None)
            mid_x = sum(p[0] for p in shape) / len(shape)
            mid_y = sum(p[1] for p in shape) / len(shape)
            lon, lat = net.convertXY2LonLat(mid_x, mid_y)
            length_m = e.getLength()
            lanes = e.getLanes()
            speed_mps = lanes[0].getSpeed() if lanes else 13.89
            r = compute_edge_network_cost(
                edge_id=edge_id,
                midpoint_lat=lat,
                midpoint_lng=lon,
                distance_m=length_m,
                travel_time_s=length_m / max(speed_mps, 0.1),
                nodes=nodes,
                buildings_gdf=None,
                prev_best_node_id=None,
                weights=weights,
                norm_scales=_norm_scales,
                skip_buildings=True,
            )
            val: tuple[float, Optional[str]] = (r.total_cost, r.best_node_id)
        except Exception:
            val = (1.0, None)
        edge_cost_cache[edge_id] = val
        return val

    pq: list[tuple[float, str, Optional[str]]] = [(0.0, from_edge, None)]
    cost_map: dict[str, float] = {from_edge: 0.0}
    prev_edge: dict[str, Optional[str]] = {from_edge: None}

    while pq:
        if stop_evt and stop_evt.is_set():
            break
        cost, cur_id, cur_best_node = heappop(pq)
        if cur_id == to_edge:
            break
        if cost > cost_map.get(cur_id, float("inf")) + 1e-9:
            continue
        try:
            cur_edge_obj = net.getEdge(cur_id)
        except Exception:
            continue
        _, cur_bn = _edge_cost(cur_id)
        for next_edge_obj in cur_edge_obj.getOutgoing().keys():
            next_id = next_edge_obj.getID()
            if next_id.startswith(":"):
                continue
            base_cost, next_bn = _edge_cost(next_id)
            handover_extra = (
                weights.w_handover
                if (cur_bn and next_bn and cur_bn != next_bn)
                else 0.0
            )
            new_cost = cost + base_cost + handover_extra
            if new_cost < cost_map.get(next_id, float("inf")):
                cost_map[next_id] = new_cost
                prev_edge[next_id] = cur_id
                heappush(pq, (new_cost, next_id, cur_bn))

    if to_edge not in prev_edge:
        raise RuntimeError(f"네트워크 가중치 경로를 찾을 수 없습니다: {from_edge} → {to_edge}")

    path: list[str] = []
    cur: Optional[str] = to_edge
    while cur is not None:
        path.append(cur)
        cur = prev_edge.get(cur)
    path.reverse()
    return path


def network_weighted_mock_path(
    graph: dict,
    start_id: str,
    end_id: str,
    nodes: list[dict],
    weights: "CostWeights",
) -> list[str]:
    """Dijkstra on mock OSM graph weighted by network cost (skip_buildings=True)."""
    edge_cost_cache: dict[tuple[str, str], tuple[float, Optional[str]]] = {}

    def _edge_cost(a_id: str, b_id: str, dist_m: float) -> tuple[float, Optional[str]]:
        key = (a_id, b_id)
        if key in edge_cost_cache:
            return edge_cost_cache[key]
        a = graph["nodes"].get(a_id)
        b = graph["nodes"].get(b_id)
        mid_lat = (a["lat"] + b["lat"]) / 2 if (a and b) else 0.0
        mid_lng = (a["lng"] + b["lng"]) / 2 if (a and b) else 0.0
        r = compute_edge_network_cost(
            edge_id=f"{a_id}_{b_id}",
            midpoint_lat=mid_lat,
            midpoint_lng=mid_lng,
            distance_m=dist_m,
            travel_time_s=dist_m / 9.0,
            nodes=nodes,
            buildings_gdf=None,
            prev_best_node_id=None,
            weights=weights,
            norm_scales=_norm_scales,
            skip_buildings=True,
        )
        val: tuple[float, Optional[str]] = (r.total_cost, r.best_node_id)
        edge_cost_cache[key] = val
        return val

    pq: list[tuple[float, str, Optional[str]]] = [(0.0, start_id, None)]
    cost_map: dict[str, float] = {start_id: 0.0}
    prev_node: dict[str, Optional[str]] = {start_id: None}

    while pq:
        cost, node_id, cur_best = heappop(pq)
        if node_id == end_id:
            break
        if cost > cost_map.get(node_id, float("inf")) + 1e-9:
            continue
        for nxt, dist in graph["adjacency"].get(node_id, []):
            ec, next_best = _edge_cost(node_id, nxt, dist)
            handover_extra = (
                weights.w_handover
                if (cur_best and next_best and cur_best != next_best)
                else 0.0
            )
            new_cost = cost + ec + handover_extra
            if new_cost < cost_map.get(nxt, float("inf")):
                cost_map[nxt] = new_cost
                prev_node[nxt] = node_id
                heappush(pq, (new_cost, nxt, next_best))

    if end_id not in prev_node:
        raise RuntimeError("네트워크 가중치 mock 경로를 찾을 수 없습니다.")

    path: list[str] = []
    cur: Optional[str] = end_id
    while cur is not None:
        path.append(cur)
        cur = prev_node.get(cur)
    path.reverse()
    return path


def best_of_k_path(
    k_edge_data: list[tuple[list[str], list[dict]]],
    nodes: list[dict],
    weights: "CostWeights",
) -> Optional[list[str]]:
    """
    Pick the lowest full-network-cost candidate among Yen's K topologically-shortest
    paths. This IS the "k_shortest_path" routing algorithm: an approximation of
    network-cost-optimal routing that re-scores only the K cheapest-by-distance
    alternatives instead of searching the full cost-weighted graph (compare with
    network_weighted_sumo_path/network_weighted_mock_path, which search exactly).
    """
    if not k_edge_data or not ROUTE_COST_AVAILABLE:
        return None
    best_path: Optional[list[str]] = None
    best_cost = float("inf")
    for path_ids, edge_data in k_edge_data:
        if not edge_data:
            continue
        try:
            result = evaluate_path(edge_data, nodes, weights=weights, norm_scales=_norm_scales)
        except Exception:
            continue
        if result.total_cost < best_cost:
            best_cost = result.total_cost
            best_path = path_ids
    return best_path


def _build_sumo_road_graph_view(net) -> tuple[dict, dict]:
    """
    Translate the sumolib net into the {road_nodes, adjacency} shape that
    look_ahead_bs_scan() expects — the same shape the mock graph already uses
    natively. Cheap to build (single pass over nodes/edges); call once per route
    and reuse, not once per Dijkstra expansion.
    """
    road_nodes: dict[str, dict] = {}
    for node in net.getNodes():
        try:
            x, y = node.getCoord()
            lon, lat = net.convertXY2LonLat(x, y)
            road_nodes[node.getID()] = {"lat": lat, "lng": lon}
        except Exception:
            continue

    adjacency: dict[str, list[tuple]] = {}
    for edge in net.getEdges():
        eid = edge.getID()
        if eid.startswith(":"):
            continue
        try:
            from_id = edge.getFromNode().getID()
            to_id = edge.getToNode().getID()
            length_m = edge.getLength()
            lanes = edge.getLanes()
            speed_mps = lanes[0].getSpeed() if lanes else 13.89
            adjacency.setdefault(from_id, []).append((to_id, eid, length_m, speed_mps))
        except Exception:
            continue
    return road_nodes, adjacency


LOOKAHEAD_PENALTY = 2.0  # cost multiplier weight for look-ahead coverage risk


def _lookahead_risk_by_edge(
    start_node_id: Optional[str],
    road_nodes: dict,
    adjacency: dict,
    bs_nodes: list[dict],
    lookahead_hops: int,
) -> dict[str, float]:
    """
    Run look_ahead_bs_scan() once from the route's starting node and convert its
    per-hop uncovered-edge lists into a per-edge risk score (0-1, nearer hops
    weighted higher — same "near hops matter more" convention the scan itself uses)
    usable as a Dijkstra edge-cost multiplier.
    """
    if not start_node_id:
        return {}
    try:
        from app.services.routing.look_ahead_scan import look_ahead_bs_scan
    except ImportError:
        return {}
    try:
        result = look_ahead_bs_scan(
            start_node_id, {"adjacency": adjacency}, road_nodes, bs_nodes, lookahead_hops,
        )
    except Exception:
        return {}
    risk: dict[str, float] = {}
    n = len(result.per_hop)
    for i, hop_scan in enumerate(result.per_hop):
        weight = (n - i) / n if n else 0.0
        for edge_id in hop_scan.uncovered_edge_ids:
            risk[edge_id] = max(risk.get(edge_id, 0.0), weight)
    return risk


def lookahead_weighted_sumo_path(
    net,
    from_edge: str,
    to_edge: str,
    nodes: list[dict],
    weights: "CostWeights",
    lookahead_hops: int = 3,
) -> list[str]:
    """
    Network-cost-weighted Dijkstra (same base cost as network_weighted_sumo_path)
    with an added BFS look-ahead coverage-risk penalty: edges that look_ahead_bs_scan()
    predicts will be uncovered within the next `lookahead_hops` hops cost more, so the
    router prefers paths that route around soon-to-be-uncovered segments.
    """
    road_nodes, adjacency = _build_sumo_road_graph_view(net)
    try:
        start_node_id = net.getEdge(from_edge).getFromNode().getID()
    except Exception:
        start_node_id = None
    risk_by_edge = _lookahead_risk_by_edge(start_node_id, road_nodes, adjacency, nodes, lookahead_hops)

    edge_cost_cache: dict[str, tuple[float, Optional[str]]] = {}

    def _edge_cost(edge_id: str) -> tuple[float, Optional[str]]:
        if edge_id in edge_cost_cache:
            return edge_cost_cache[edge_id]
        try:
            e = net.getEdge(edge_id)
            shape = e.getShape()
            if not shape:
                edge_cost_cache[edge_id] = (1.0, None)
                return (1.0, None)
            mid_x = sum(p[0] for p in shape) / len(shape)
            mid_y = sum(p[1] for p in shape) / len(shape)
            lon, lat = net.convertXY2LonLat(mid_x, mid_y)
            length_m = e.getLength()
            lanes = e.getLanes()
            speed_mps = lanes[0].getSpeed() if lanes else 13.89
            r = compute_edge_network_cost(
                edge_id=edge_id,
                midpoint_lat=lat,
                midpoint_lng=lon,
                distance_m=length_m,
                travel_time_s=length_m / max(speed_mps, 0.1),
                nodes=nodes,
                buildings_gdf=None,
                prev_best_node_id=None,
                weights=weights,
                norm_scales=_norm_scales,
                skip_buildings=True,
            )
            base_cost = r.total_cost * (1.0 + LOOKAHEAD_PENALTY * risk_by_edge.get(edge_id, 0.0))
            val: tuple[float, Optional[str]] = (base_cost, r.best_node_id)
        except Exception:
            val = (1.0, None)
        edge_cost_cache[edge_id] = val
        return val

    pq: list[tuple[float, str, Optional[str]]] = [(0.0, from_edge, None)]
    cost_map: dict[str, float] = {from_edge: 0.0}
    prev_edge: dict[str, Optional[str]] = {from_edge: None}

    while pq:
        cost, cur_id, cur_best_node = heappop(pq)
        if cur_id == to_edge:
            break
        if cost > cost_map.get(cur_id, float("inf")) + 1e-9:
            continue
        try:
            cur_edge_obj = net.getEdge(cur_id)
        except Exception:
            continue
        _, cur_bn = _edge_cost(cur_id)
        for next_edge_obj in cur_edge_obj.getOutgoing().keys():
            next_id = next_edge_obj.getID()
            if next_id.startswith(":"):
                continue
            base_cost, next_bn = _edge_cost(next_id)
            handover_extra = (
                weights.w_handover
                if (cur_bn and next_bn and cur_bn != next_bn)
                else 0.0
            )
            new_cost = cost + base_cost + handover_extra
            if new_cost < cost_map.get(next_id, float("inf")):
                cost_map[next_id] = new_cost
                prev_edge[next_id] = cur_id
                heappush(pq, (new_cost, next_id, cur_bn))

    if to_edge not in prev_edge:
        raise RuntimeError(f"Look-ahead 가중치 경로를 찾을 수 없습니다: {from_edge} → {to_edge}")

    path: list[str] = []
    cur: Optional[str] = to_edge
    while cur is not None:
        path.append(cur)
        cur = prev_edge.get(cur)
    path.reverse()
    return path


def lookahead_weighted_mock_path(
    graph: dict,
    start_id: str,
    end_id: str,
    nodes: list[dict],
    weights: "CostWeights",
    lookahead_hops: int = 3,
) -> list[str]:
    """
    Mock-graph counterpart of lookahead_weighted_sumo_path. No graph-view
    translation is needed — graph["adjacency"] is already in the shape
    look_ahead_bs_scan() expects.
    """
    risk_by_edge = _lookahead_risk_by_edge(
        start_id, graph["nodes"], graph["adjacency"], nodes, lookahead_hops,
    )

    edge_cost_cache: dict[tuple[str, str], tuple[float, Optional[str]]] = {}

    def _edge_cost(a_id: str, b_id: str, dist_m: float) -> tuple[float, Optional[str]]:
        key = (a_id, b_id)
        if key in edge_cost_cache:
            return edge_cost_cache[key]
        a = graph["nodes"].get(a_id)
        b = graph["nodes"].get(b_id)
        mid_lat = (a["lat"] + b["lat"]) / 2 if (a and b) else 0.0
        mid_lng = (a["lng"] + b["lng"]) / 2 if (a and b) else 0.0
        edge_id = f"{a_id}_{b_id}"
        r = compute_edge_network_cost(
            edge_id=edge_id,
            midpoint_lat=mid_lat,
            midpoint_lng=mid_lng,
            distance_m=dist_m,
            travel_time_s=dist_m / 9.0,
            nodes=nodes,
            buildings_gdf=None,
            prev_best_node_id=None,
            weights=weights,
            norm_scales=_norm_scales,
            skip_buildings=True,
        )
        base_cost = r.total_cost * (1.0 + LOOKAHEAD_PENALTY * risk_by_edge.get(edge_id, 0.0))
        val: tuple[float, Optional[str]] = (base_cost, r.best_node_id)
        edge_cost_cache[key] = val
        return val

    pq: list[tuple[float, str, Optional[str]]] = [(0.0, start_id, None)]
    cost_map: dict[str, float] = {start_id: 0.0}
    prev_node: dict[str, Optional[str]] = {start_id: None}

    while pq:
        cost, node_id, cur_best = heappop(pq)
        if node_id == end_id:
            break
        if cost > cost_map.get(node_id, float("inf")) + 1e-9:
            continue
        for nxt, dist in graph["adjacency"].get(node_id, []):
            ec, next_best = _edge_cost(node_id, nxt, dist)
            handover_extra = (
                weights.w_handover
                if (cur_best and next_best and cur_best != next_best)
                else 0.0
            )
            new_cost = cost + ec + handover_extra
            if new_cost < cost_map.get(nxt, float("inf")):
                cost_map[nxt] = new_cost
                prev_node[nxt] = node_id
                heappush(pq, (new_cost, nxt, next_best))

    if end_id not in prev_node:
        raise RuntimeError("Look-ahead 가중치 mock 경로를 찾을 수 없습니다.")

    path: list[str] = []
    cur: Optional[str] = end_id
    while cur is not None:
        path.append(cur)
        cur = prev_node.get(cur)
    path.reverse()
    return path


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
    _mock_spd_acc: dict[str, list] = {}
    _mock_prev_cur = -1

    # 다중차량 실험군 — 배경 차량별 진행 상태 (자기 route_coords를 따라 단순 선형보간만 수행)
    _bg_state = []
    for bv in (_state.get("background_vehicles") or []):
        bv_coords = bv["route_coords"]
        bv_seg_lengths = []
        bv_total = 0.0
        for a, b in zip(bv_coords, bv_coords[1:]):
            d = haversine_m(a[0], a[1], b[0], b[1])
            bv_seg_lengths.append(d)
            bv_total += d
        _bg_state.append({
            "vehicle": bv,
            "coords": bv_coords,
            "seg_lengths": bv_seg_lengths,
            "total_dist": bv_total,
            "travelled": 0.0,
            "speed_mps": bv.get("speed_kmh", 30.0) / 3.6,
        })

    while not stop_evt.is_set():
        # 일시정지 대기
        while _pause_event.is_set() and not stop_evt.is_set():
            time.sleep(0.1)
        if stop_evt.is_set():
            break
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

        _progress = round(0.0 if total_dist == 0 else travelled / total_dist, 3)
        _state["vehicle_pos"] = {
            "lat": lat,
            "lng": lng,
            "speed": round(speed_mps * 3.6, 1),
            "progress": _progress,
            "step": step,
            "arrived": travelled >= total_dist,
        }
        # Simulate edge stats from vehicle progress
        _rc = _state.get("route_cost_result")
        if _rc:
            _pe = _rc.get("per_edge", [])
        else:
            # Fallback: build edge list directly from route path (no cost module needed)
            _path_nodes = _state.get("route_edges", [])
            _pe = [{"edge_id": f"{_a}_{_b}"} for _a, _b in zip(_path_nodes, _path_nodes[1:])] if len(_path_nodes) >= 2 else []
        if _pe:
            _n = len(_pe)
            _cur = min(int(_progress * _n), _n - 1)
            _estats = []
            for _i, _e in enumerate(_pe):
                _d = abs(_i - _cur)
                _occ = round(max(0.0, 1.0 - _d * 0.4), 3)
                _spd = round(max(5.0, 32.4 * (1.0 - _occ * 0.7)), 1)
                _estats.append({
                    "edge_id": _e["edge_id"],
                    "speed_kmh": _spd,
                    "occupancy": _occ,
                    "vehicle_count": 1 if _d == 0 else 0,
                })
            _state["edge_telemetry"] = _estats
            if 0 <= _cur < len(_pe):
                _mock_eid = _pe[_cur]["edge_id"]
                _mock_spd_acc.setdefault(_mock_eid, []).append(round(speed_mps * 3.6, 1))
                if _cur != _mock_prev_cur and _mock_prev_cur >= 0 and _mock_prev_cur < len(_pe):
                    _prev_mock_eid = _pe[_mock_prev_cur]["edge_id"]
                    _samples = _mock_spd_acc.get(_prev_mock_eid, [])
                    if _samples:
                        _state["edge_avg_speeds"][_prev_mock_eid] = round(sum(_samples) / len(_samples), 1)
                    if _prev_mock_eid not in _state["edge_history"]:
                        _state["edge_history"].append(_prev_mock_eid)
                _mock_prev_cur = _cur
                if _state.get("vehicle_pos") is not None:
                    _state["vehicle_pos"]["current_edge_id"] = _mock_eid

        # 배경 차량 위치 전진 (타겟과 같은 루프 안에서 처리 — 스레드 N개 생성 안 함)
        for bg in _bg_state:
            if bg["total_dist"] <= 0:
                continue
            bg["travelled"] += bg["speed_mps"] * 0.2
            if bg["travelled"] >= bg["total_dist"]:
                bg["travelled"] = 0.0  # 끝에 도달하면 처음부터 다시 (배경 트래픽은 계속 돌아다님)
            _remaining = bg["travelled"]
            _lat = bg["coords"][-1][0]
            _lng = bg["coords"][-1][1]
            for _idx, _seg_len in enumerate(bg["seg_lengths"]):
                _start = bg["coords"][_idx]
                _end = bg["coords"][_idx + 1]
                if _remaining <= _seg_len or _idx == len(bg["seg_lengths"]) - 1:
                    _ratio = 0.0 if _seg_len == 0 else min(1.0, _remaining / _seg_len)
                    _lat = _start[0] + (_end[0] - _start[0]) * _ratio
                    _lng = _start[1] + (_end[1] - _start[1]) * _ratio
                    break
                _remaining -= _seg_len
            bg["vehicle"]["lat"] = _lat
            bg["vehicle"]["lng"] = _lng
            bg["vehicle"]["progress"] = round(bg["travelled"] / bg["total_dist"], 3)

        # 자원할당 주기적 재계산(~2초마다) — SUMO 모드와 동일한 이유/패턴
        # (simulation_thread 참고): last_allocation_result가 시작 시 1회 값으로 굳지 않게 한다.
        if RESOURCE_DEMAND_AVAILABLE and step % 20 == 0 and _state.get("network_nodes"):
            _live_origin = _state.get("vehicle_pos")
            if _live_origin and _live_origin.get("lat") is not None:
                _periodic_alloc_algo = _state.get("allocation_algorithm") or "traffic_aware_allocation"
                _periodic_alloc_out = _run_resource_allocation(
                    _live_origin, [], _periodic_alloc_algo, _state.get("background_vehicles"),
                )
                # "경로 대안 비교"(K-path) 후보들도 최신 부하/혼잡 조건으로 다시 점수 매김
                # (SUMO 모드와 동일한 이유 — Yen's 재탐색 없이 가볍게). 건물 교차 분석을
                # 포함해 무거울 수 있어 메인 루프를 막지 않게 백그라운드 스레드로 돌린다.
                if _periodic_alloc_out:
                    threading.Thread(
                        target=_refresh_k_candidates_async,
                        args=(_periodic_alloc_out.to_dict(),),
                        daemon=True,
                    ).start()

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


def netconvert(osm_file: Path, net_file: Path, bbox: Optional[BBox] = None):
    """Convert OSM to SUMO network with netconvert.

    Overpass의 way[...](bbox); >; 쿼리는 매칭된 way의 "전체" 지오메트리를 가져오므로,
    bbox 경계를 살짝 걸친 긴 도로(예: 간선도로)가 통째로 포함되어 실제 선택 구역을
    훨씬 벗어나는 edge가 net.xml에 들어갈 수 있다. bbox가 주어지면
    --keep-edges.in-geo-boundary로 해당 영역(다운로드 시 사용한 확장 bbox와 동일)
    밖으로 뻗어나가는 edge를 잘라내, 경로가 구역 밖으로 크게 우회하는 것을 막는다.
    """
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
    if bbox is not None:
        args += ["--keep-edges.in-geo-boundary", f"{bbox.w},{bbox.s},{bbox.e},{bbox.n}"]
    print(f"[NET] Running netconvert: {osm_file.name} → {net_file.name}", flush=True)
    rc, out, err = run_cmd(args, extra_env={"SUMO_HOME": SUMO_HOME} if SUMO_HOME else None)
    if rc != 0:
        raise RuntimeError(f"netconvert 실패 (rc={rc}):\n{err[-2000:]}")
    print(f"[NET] netconvert done → {net_file}", flush=True)


def nearest_edge(net, lat: float, lng: float) -> str:
    """Find the nearest drivable edge to a geo coordinate."""
    return nearest_edge_candidates(net, lat, lng, k=1)[0]


def nearest_edge_candidates(net, lat: float, lng: float, k: int = 8) -> list[str]:
    """Return up to k nearest drivable edge IDs ordered by distance from the point."""
    x, y = net.convertLonLat2XY(lng, lat)
    edges: list = []
    for radius in (300, 800, 1500, 3000):
        edges = net.getNeighboringEdges(x, y, r=radius, includeJunctions=False)
        if edges:
            break
    if not edges:
        raise RuntimeError(
            f"주변 도로를 찾을 수 없습니다 (lat={lat:.5f}, lng={lng:.5f}). 도로 위를 클릭해주세요."
        )
    drivable = [(e, d) for e, d in edges if e.allows("passenger")]
    if not drivable:
        drivable = list(edges)
    drivable.sort(key=lambda ed: ed[1])
    return [e.getID() for e, _ in drivable[:k]]


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


def simulation_thread(
    net_file: str,
    origin: dict,
    dest: dict,
    stop_evt: threading.Event,
    route_algorithm: str = "dijkstra",
    vehicle_count: int = 1,
):
    """
    Runs in a background thread.
    Starts SUMO with TraCI, injects one vehicle on the baseline Dijkstra path,
    and updates _state["vehicle_pos"] each step.
    `route_algorithm` selects how the baseline path is replaced before the vehicle
    is injected: "astar" / "k_shortest_path" / "network_aware"(_routing) /
    "lookahead"(look_ahead_routing) each run a real, distinct search — see
    astar_sumo_path / best_of_k_path / network_weighted_sumo_path /
    lookahead_weighted_sumo_path. "rl_routing" and unknown values fall through to
    baseline Dijkstra (no trained RL agent exists yet — intentionally deferred).
    다중차량 실험군 — vehicle_count > 1이면 무작위 경로의 배경 차량 (vehicle_count - 1)대를
    TraCI로 추가 주입하고, 매 틱 위치를 _state["background_vehicles"]에 갱신한다.
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
        from_candidates = nearest_edge_candidates(net, origin["lat"], origin["lng"], k=8)
        to_candidates   = nearest_edge_candidates(net, dest["lat"],   dest["lng"],   k=8)
        print(f"[SIM] from_candidates={from_candidates[:3]}…  to_candidates={to_candidates[:3]}…", flush=True)

        # Close any stale TraCI connection before starting a fresh one
        try:
            traci.close()
        except Exception:
            pass

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

        # Try candidate edge pairs until findRoute succeeds
        result = None
        from_edge, to_edge = from_candidates[0], to_candidates[0]
        for _fc in from_candidates:
            for _tc in to_candidates:
                if _fc == _tc:
                    continue
                _r = traci.simulation.findRoute(_fc, _tc, vType="")
                if _r.edges:
                    result = _r
                    from_edge, to_edge = _fc, _tc
                    break
            if result and result.edges:
                break

        print(f"[SIM] from_edge={from_edge}  to_edge={to_edge}", flush=True)
        if not result or not result.edges:
            tried = len(from_candidates) * len(to_candidates)
            raise RuntimeError(
                f"SUMO 경로 탐색 실패: 주변 엣지 {tried}개 조합을 모두 시도했으나 연결 가능한 경로가 없습니다. "
                f"출발지/목적지를 더 큰 도로 위로 이동해 주세요."
            )

        dijkstra_edges = list(result.edges)
        edges = dijkstra_edges
        print(f"[SIM] Baseline Dijkstra route: {len(edges)} edges", flush=True)

        # Replace baseline Dijkstra with the selected route_algorithm's real path,
        # if implemented. Each candidate is validated for edge-to-edge connectivity
        # before being trusted; any failure/gap falls back to baseline Dijkstra.
        def _edges_connected(e1_id, e2_id):
            try:
                return any(
                    out.getID() == e2_id
                    for out in net.getEdge(e1_id).getOutgoing().keys()
                )
            except Exception:
                return False

        def _try_use_candidate(candidate, label: str) -> bool:
            nonlocal edges
            if not candidate:
                return False
            gaps = [
                (candidate[i], candidate[i + 1])
                for i in range(len(candidate) - 1)
                if not _edges_connected(candidate[i], candidate[i + 1])
            ]
            if gaps:
                print(
                    f"[SIM] {label} route has {len(gaps)} disconnected edge(s) "
                    f"— falling back to Dijkstra",
                    flush=True,
                )
                _state["warning"] = f"{label} 경로에 불연속 구간이 있어 기본 Dijkstra 경로를 사용합니다."
                return False
            edges = candidate
            print(f"[SIM] {label} route: {len(edges)} edges", flush=True)
            return True

        _sumo_routing_mode = "baseline_dijkstra"
        nodes_for_routing = _state.get("network_nodes") or []

        if route_algorithm == "astar":
            try:
                if _try_use_candidate(astar_sumo_path(net, from_edge, to_edge), "A*"):
                    _sumo_routing_mode = "astar"
            except Exception as _astar_exc:
                print(f"[SIM] A* routing failed: {_astar_exc} — using Dijkstra baseline", flush=True)
                _state["warning"] = "A* 경로 계산 실패 — 기본 Dijkstra 경로를 사용합니다."

        elif route_algorithm == "k_shortest_path" and ROUTE_COST_AVAILABLE and nodes_for_routing:
            try:
                _k_paths_pre = yen_k_paths_sumo(net, from_edge, to_edge, k=5)
                candidate = best_of_k_path(
                    build_sumo_k_edge_data(net, _k_paths_pre), nodes_for_routing, _route_cost_weights,
                ) if _k_paths_pre else None
                if _try_use_candidate(candidate, "K-shortest-path"):
                    _sumo_routing_mode = "k_shortest_path"
            except Exception as _ksp_exc:
                print(f"[SIM] K-shortest-path routing failed: {_ksp_exc} — using Dijkstra baseline", flush=True)
                _state["warning"] = "K-shortest-path 경로 계산 실패 — 기본 Dijkstra 경로를 사용합니다."

        elif route_algorithm in ("network_aware", "network_aware_routing") and ROUTE_COST_AVAILABLE and nodes_for_routing:
            try:
                candidate = network_weighted_sumo_path(
                    net, from_edge, to_edge, nodes_for_routing, _route_cost_weights, stop_evt,
                )
                if _try_use_candidate(candidate, "Network-weighted"):
                    _sumo_routing_mode = "network_aware"
            except Exception as _net_exc:
                print(f"[SIM] Network routing failed: {_net_exc} — using Dijkstra baseline", flush=True)
                _state["warning"] = "네트워크 가중치 경로 계산 실패 — 기본 Dijkstra 경로를 사용합니다."

        elif route_algorithm in ("lookahead", "look_ahead_routing") and ROUTE_COST_AVAILABLE and nodes_for_routing:
            try:
                _lookahead_hops = (
                    (_state.get("simulation_config") or {}).get("policy_options", {}).get("lookahead_k", 3)
                )
                candidate = lookahead_weighted_sumo_path(
                    net, from_edge, to_edge, nodes_for_routing, _route_cost_weights, _lookahead_hops,
                )
                if _try_use_candidate(candidate, "Look-ahead"):
                    _sumo_routing_mode = "lookahead"
            except Exception as _la_exc:
                print(f"[SIM] Look-ahead routing failed: {_la_exc} — using Dijkstra baseline", flush=True)
                _state["warning"] = "Look-ahead 경로 계산 실패 — 기본 Dijkstra 경로를 사용합니다."

        # rl_routing and any unknown value: no dispatch — stays on baseline Dijkstra.
        # No trained RL agent exists yet (app/services/rl/rl_trainer.py only has
        # random/greedy/coverage baseline policies) — intentionally deferred.

        _state["route_edges"] = edges

        # Collect street names: TraCI → OSM way_id fallback (must run in SUMO thread)
        _osm_way_names = load_osm_way_names(Path(_state["osm_file"])) if _state.get("osm_file") else {}
        _edge_names: dict[str, str] = {}
        _sumo_edge_midpoints: dict[str, tuple[float, float]] = {}
        for _eid in edges:
            # TraCI name
            try:
                _name = traci.edge.getStreetName(_eid)
            except Exception:
                _name = ""
            # OSM way_id fallback
            if not _name:
                _name = _osm_way_names.get(sumo_edge_to_way_id(_eid), "")
            if _name:
                _edge_names[_eid] = _name
            # Collect midpoint for V-World enrichment
            try:
                _shape = net.getEdge(_eid).getShape()
                _mx, _my = _shape[len(_shape) // 2]
                _mlon, _mlat = net.convertXY2LonLat(_mx, _my)
                _sumo_edge_midpoints[_eid] = (_mlat, _mlon)
            except Exception:
                pass
        _state["route_edge_names"] = _edge_names
        # V-World enrichment for edges still missing names (background)
        if VWORLD_API_KEY:
            threading.Thread(
                target=_enrich_edge_names_vworld,
                args=(_sumo_edge_midpoints, _state["route_edge_names"]),
                daemon=True,
            ).start()

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

        # Pre-build edge data (cheap — no building analysis yet)
        _sumo_edge_data = build_sumo_edge_data(net, edges) if ROUTE_COST_AVAILABLE else []
        # _sumo_routing_mode was already set by the route_algorithm dispatch above
        _sumo_from_edge = from_edge
        _sumo_to_edge = to_edge
        _sumo_net_ref = net  # sumolib only — safe to share across threads

        # Bail out cleanly if stop was requested during setup phase
        if stop_evt.is_set():
            print("[SIM] Stop requested during setup — aborting before vehicle add", flush=True)
            traci.close()
            _state["sim_running"] = False
            return

        # Define vehicle type and add vehicle
        # Use DEFAULT_VEHTYPE (always exists in SUMO)
        traci.route.add("route0", edges)

        # 목적지 좌표를 도착 엣지에 투영하여 arrivalPos 계산
        # (미지정 시 엣지 끝까지 과주행)
        try:
            _dx, _dy = net.convertLonLat2XY(dest["lng"], dest["lat"])
            _lane = net.getEdge(to_edge).getLane(0)
            _shape = _lane.getShape()
            _arrival_pos = 0.0
            _cum = 0.0
            _best_d = float("inf")
            for _si in range(len(_shape) - 1):
                _x1, _y1 = _shape[_si]
                _x2, _y2 = _shape[_si + 1]
                _sl = math.hypot(_x2 - _x1, _y2 - _y1)
                if _sl > 0:
                    _t = max(0.0, min(1.0, ((_dx - _x1) * (_x2 - _x1) + (_dy - _y1) * (_y2 - _y1)) / (_sl * _sl)))
                    _d = math.hypot(_dx - (_x1 + _t * (_x2 - _x1)), _dy - (_y1 + _t * (_y2 - _y1)))
                    if _d < _best_d:
                        _best_d = _d
                        _arrival_pos = _cum + _t * _sl
                _cum += _sl
            _edge_len = net.getEdge(to_edge).getLength()
            _arrival_pos = max(1.0, min(float(_arrival_pos), _edge_len - 0.5))
            print(f"[SIM] arrivalPos on {to_edge}: {_arrival_pos:.1f}m / {_edge_len:.1f}m", flush=True)
        except Exception as _ae:
            _arrival_pos = "max"
            print(f"[SIM] arrivalPos fallback to max: {_ae}", flush=True)

        traci.vehicle.add(
            vehID="veh0",
            routeID="route0",
            typeID="DEFAULT_VEHTYPE",
            depart="0",
            departLane="best",
            departSpeed="0",
            arrivalPos=_arrival_pos,
        )
        print("[SIM] Vehicle veh0 added to simulation", flush=True)

        # ── 다중차량 실험군 — 배경 차량 주입 (vehicle_count > 1일 때만, SUMO 모드) ──────
        _bg_vehicle_ids: list[str] = []
        _bg_drivable_edges: list = []  # sumolib Edge 객체 리스트 (오프라인 경로탐색용)
        # 막 주입했지만 아직 getIDList()에 안 보이는(삽입 대기 중) 차량을 추적 — 값=대기 틱 수.
        # 너무 오래(10틱~1초) 안 보이면 도착했거나 영구히 막힌 것으로 보고 새 id로 교체한다.
        _bg_pending: dict[str, int] = {}

        def _inject_bg_vehicle(drivable_edges: list, max_attempts: int = 5) -> Optional[str]:
            """무작위 출발/도착 엣지로 SUMO에 배경 차량 1대를 새 vehID로 주입한다.
            매번 새 id(uuid)를 생성하므로 "이미 존재함" 충돌이 구조적으로 생기지 않는다.
            경로탐색은 sumolib의 오프라인 getShortestPath()로 수행 — TraCI 왕복 없이
            클라이언트 측에서 계산되므로 대량 주입 시 findRoute() 대비 훨씬 빠르다.
            유효한 경로를 못 찾으면 max_attempts번까지 재시도하고 실패하면 None을 반환한다."""
            for _ in range(max_attempts):
                from_edge = random.choice(drivable_edges)
                to_edge = random.choice(drivable_edges)
                if from_edge is to_edge:
                    continue
                try:
                    _path, _cost = net.getShortestPath(from_edge, to_edge)
                except Exception:
                    continue
                if not _path:
                    continue
                edge_ids = [e.getID() for e in _path]
                veh_id = f"bg{uuid4().hex[:8]}"
                try:
                    traci.route.add(f"route_{veh_id}", edge_ids)
                    traci.vehicle.add(
                        vehID=veh_id,
                        routeID=f"route_{veh_id}",
                        typeID="DEFAULT_VEHTYPE",
                        depart="now",
                        departLane="best",
                        departSpeed="random",
                        departPos="random_free",  # 무작위지만 충돌 없는 빈 자리를 SUMO가 재시도해서 찾음
                    )
                    return veh_id
                except Exception as _bg_exc:
                    print(f"[BG-VEH] inject failed: {_bg_exc}", flush=True)
                    continue
            return None

        if vehicle_count > 1:
            _bg_drivable_edges = [e for e in net.getEdges() if e.allows("passenger")]
            if len(_bg_drivable_edges) >= 2:
                # 면적 기준 어림 용량 — 도로 엣지 총길이는 bbox 밖 연결 엣지까지 포함돼
                # 항상 과대평가되므로(임계값이 거의 안 걸림), 실제 구역 면적 × 현실적인
                # 도심 교통 밀도(흐르는 교통 기준 ~150대/km², 정체 직전 어림치)로 추정한다.
                _bbox = _state.get("current_bbox")
                _capacity_hint = None
                if _bbox:
                    _w_m = haversine_m(_bbox["s"], _bbox["w"], _bbox["s"], _bbox["e"])
                    _h_m = haversine_m(_bbox["s"], _bbox["w"], _bbox["n"], _bbox["w"])
                    _area_km2 = max((_w_m * _h_m) / 1_000_000.0, 0.01)
                    _capacity_hint = max(1, round(_area_km2 * 150))
                if _capacity_hint and vehicle_count - 1 > _capacity_hint:
                    _state["warning"] = (
                        f"요청한 배경 차량 {vehicle_count - 1}대는 현재 시뮬레이션 구역 규모"
                        f"(약 {_area_km2:.2f}km²)에 비해 비현실적으로 많을 수 있습니다 "
                        f"(어림 용량 ~{_capacity_hint}대). 시뮬레이션은 계속 진행됩니다."
                    )
                for _ in range(vehicle_count - 1):
                    _new_id = _inject_bg_vehicle(_bg_drivable_edges)
                    if _new_id:
                        _bg_vehicle_ids.append(_new_id)
                        _bg_pending[_new_id] = 0
            _state["background_vehicle_ids"] = _bg_vehicle_ids
            print(f"[BG-VEH] injected {len(_bg_vehicle_ids)}/{vehicle_count - 1} background vehicles", flush=True)

            if _bg_vehicle_ids:
                # 초기 위치를 확보하려면 1틱 진행이 필요 — mock 모드와 동일한 패턴으로
                # 자원할당을 1회 호출해 route-cost/K-path 쪽 부하 반영을 SUMO 모드에도 맞춘다.
                traci.simulationStep()
                _live_ids0 = set(traci.vehicle.getIDList())
                _bg_snapshot = []
                for _bg_id in _bg_vehicle_ids:
                    if _bg_id in _live_ids0:
                        _bg_pending.pop(_bg_id, None)
                        _bx, _by = traci.vehicle.getPosition(_bg_id)
                        _blon, _blat = traci.simulation.convertGeo(_bx, _by)
                        _bspd = round(traci.vehicle.getSpeed(_bg_id) * 3.6, 1)
                        _bg_snapshot.append({"id": _bg_id, "lat": _blat, "lng": _blon, "speed": _bspd})
                _state["background_vehicles"] = _bg_snapshot
                if RESOURCE_DEMAND_AVAILABLE and _state.get("network_nodes"):
                    _sumo_alloc_algo = _state.get("allocation_algorithm") or "traffic_aware_allocation"
                    _run_resource_allocation(origin, [], _sumo_alloc_algo, _bg_snapshot)

        # Run cost evaluation and K-path analysis in background so the
        # simulation loop (and vehicle marker) starts immediately.
        # These only write to different _state keys than the loop, so it is safe.
        def _bg_cost_eval():
            if _sumo_edge_data:
                _store_route_cost(_sumo_edge_data, _sumo_routing_mode)
            if ROUTE_COST_AVAILABLE:
                try:
                    _k_paths = yen_k_paths_sumo(_sumo_net_ref, _sumo_from_edge, _sumo_to_edge, k=5)
                    if _k_paths:
                        _k_candidates = build_sumo_k_edge_data(_sumo_net_ref, _k_paths)
                        _store_k_candidates(_k_candidates)
                except Exception as _k_exc:
                    print(f"[COST] K-path generation failed: {_k_exc}", flush=True)

        threading.Thread(target=_bg_cost_eval, daemon=True).start()

        step = 0
        arrived = False
        max_steps = 100_000  # safety limit
        _spd_acc: dict[str, list] = {}   # edge_id -> [speed_kmh, ...]
        _prev_ridx = -1

        while not stop_evt.is_set() and not arrived and step < max_steps:
            # 일시정지 대기 (TraCI 연결 유지)
            while _pause_event.is_set() and not stop_evt.is_set():
                time.sleep(0.1)
            if stop_evt.is_set():
                break
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

            _spd_kmh = round(speed * 3.6, 1)
            _state["vehicle_pos"] = {
                "lat": lat,
                "lng": lon,
                "speed": _spd_kmh,
                "progress": round(progress, 3),
                "step": step,
                "arrived": False,
                "current_edge_id": edges[route_idx] if 0 <= route_idx < len(edges) else None,
            }

            # Track per-edge average speed (actual vehicle speed while traversing)
            if 0 <= route_idx < len(edges):
                _cur_eid = edges[route_idx]
                _spd_acc.setdefault(_cur_eid, []).append(_spd_kmh)
                if route_idx != _prev_ridx and _prev_ridx >= 0 and _prev_ridx < len(edges):
                    _prev_eid = edges[_prev_ridx]
                    _samples = _spd_acc.get(_prev_eid, [])
                    if _samples:
                        _state["edge_avg_speeds"][_prev_eid] = round(sum(_samples) / len(_samples), 1)
                    if _prev_eid not in _state["edge_history"]:
                        _state["edge_history"].append(_prev_eid)
                _prev_ridx = route_idx

            # Collect per-edge stats from TraCI
            _route_eids = _state.get("route_edges", [])
            if _route_eids:
                _estats = []
                for _eid in _route_eids:
                    try:
                        _spd_ms = traci.edge.getLastStepMeanSpeed(_eid)
                        _occ = traci.edge.getLastStepOccupancy(_eid) / 100.0
                        _vc = traci.edge.getLastStepVehicleNumber(_eid)
                        # 차량이 없는 엣지는 SUMO가 자유류속도(~100km/h)를 반환하므로 0으로 처리
                        _spd_kmh = round(max(0.0, _spd_ms) * 3.6, 1) if _vc > 0 else 0.0
                        _estats.append({
                            "edge_id": _eid,
                            "speed_kmh": _spd_kmh,
                            "occupancy": round(max(0.0, min(1.0, _occ)), 3),
                            "vehicle_count": int(_vc),
                        })
                    except Exception:
                        pass
                if _estats:
                    _state["edge_telemetry"] = _estats

            # 다중차량 실험군 — 배경 차량 위치 갱신, 도착/소멸한 차량은 새 id로 즉시 교체 주입.
            # 도착 감지(아이디 집합 비교)는 매 틱 수행하지만, 무거운 getPosition() 호출은
            # WS background_positions 브로드캐스트와 같은 주기(4틱)로만 수행해 대수가 많을 때
            # 틱당 TraCI 비용을 1/4로 줄인다.
            if _bg_vehicle_ids:
                _live_ids = set(traci.vehicle.getIDList())
                _new_bg_vehicle_ids = []
                _fetch_bg_positions = (step % 4 == 0)
                _bg_snapshot = [] if _fetch_bg_positions else None
                for _bg_id in _bg_vehicle_ids:
                    if _bg_id in _live_ids:
                        _bg_pending.pop(_bg_id, None)
                        _new_bg_vehicle_ids.append(_bg_id)
                        if _fetch_bg_positions:
                            _bx, _by = traci.vehicle.getPosition(_bg_id)
                            _blon, _blat = traci.simulation.convertGeo(_bx, _by)
                            _bspd = round(traci.vehicle.getSpeed(_bg_id) * 3.6, 1)
                            _bg_snapshot.append({"id": _bg_id, "lat": _blat, "lng": _blon, "speed": _bspd})
                        continue
                    # 아직 안 보임 — 막 주입돼서 삽입 대기 중일 수 있으니 잠깐 봐줌
                    _waited = _bg_pending.get(_bg_id, 0)
                    if _waited < 10:
                        _bg_pending[_bg_id] = _waited + 1
                        _new_bg_vehicle_ids.append(_bg_id)
                        continue
                    # 너무 오래 안 보임 — 도착했거나 영구히 막힌 것으로 보고 새 id로 교체
                    _bg_pending.pop(_bg_id, None)
                    _new_id = _inject_bg_vehicle(_bg_drivable_edges)
                    if _new_id:
                        _bg_pending[_new_id] = 0
                        _new_bg_vehicle_ids.append(_new_id)
                _bg_vehicle_ids = _new_bg_vehicle_ids
                if _fetch_bg_positions:
                    _state["background_vehicles"] = _bg_snapshot

            # 자원할당 주기적 재계산(~2초마다) — 시작 시 1회 계산된 last_allocation_result가
            # 핸드오버/배경 차량 이동 후에도 그대로 굳어있어 대시보드 "자원 할당" 패널이
            # 갱신되지 않던 문제를 해결한다. K-path 재탐색 없이(빈 리스트) ego의 현재
            # 위치와 최신 배경 차량 스냅샷만으로 가볍게 재계산한다.
            if RESOURCE_DEMAND_AVAILABLE and step % 20 == 0 and _state.get("network_nodes"):
                _live_origin = _state.get("vehicle_pos") or origin
                if _live_origin and _live_origin.get("lat") is not None:
                    _periodic_alloc_algo = _state.get("allocation_algorithm") or "traffic_aware_allocation"
                    _periodic_alloc_out = _run_resource_allocation(
                        _live_origin, [], _periodic_alloc_algo, _state.get("background_vehicles"),
                    )
                    # "경로 대안 비교"(K-path) 후보들이 시작 시 1회 비용으로 고정되지 않도록,
                    # 같은 K-path 토폴로지를 최신 부하/혼잡 조건으로 다시 점수만 매긴다
                    # (Yen's 재탐색은 안 함 — 가볍게). 건물 교차 분석 포함이라 무거울 수 있어
                    # SUMO TraCI 루프를 막지 않게 백그라운드 스레드로 돌린다.
                    if _periodic_alloc_out:
                        threading.Thread(
                            target=_refresh_k_candidates_async,
                            args=(_periodic_alloc_out.to_dict(),),
                            daemon=True,
                        ).start()

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
    _state["route_cost_result"] = None
    _state["route_cost_edge_data"] = None
    _state["route_cost_version"] = 0
    _state["route_edge_names"] = {}
    _state["edge_telemetry"] = []
    _state["edge_avg_speeds"] = {}
    _state["edge_history"] = []
    _state["k_path_candidates"] = None
    _state["k_path_edge_data"] = None
    _state["algorithm_metrics"] = {}
    _state["simulation_summary"] = None
    _state["selected_algorithms"] = {}
    _state["last_allocation_result"] = None
    _state["background_vehicles"] = []
    _state["background_vehicle_ids"] = []
    _state["algorithm_comparison"] = {"status": "idle"}
    # Stage-1: keep simulation_config across resets (user's saved config persists)
    _state["policy_options"] = None


def _store_simulation_summary() -> None:
    """Build and persist SimulationSummary from current _state."""
    if not SUMMARY_AVAILABLE:
        return
    try:
        scenario_id = str(_state.get("simulation_run_id") or "unknown")
        summary = build_summary(
            route_cost=_state.get("route_cost_result"),
            k_candidates=_state.get("k_path_candidates"),
            algorithm_metrics=_state.get("algorithm_metrics") or {},
            bs_nodes=_state.get("network_nodes") or [],
            scenario_id=scenario_id,
            edge_names=_state.get("route_edge_names") or {},
        )
        summary_dict = summary.to_dict()
        summary_dict["used_config"] = _state.get("simulation_config") or SimulationConfigModel().model_dump()
        summary_dict["policy_options"] = _state.get("policy_options")
        summary_dict["custom_policies"] = dict(_state.get("custom_policies") or {})
        summary_dict["custom_policy_debug"] = dict(_state.get("custom_policy_debug") or {})
        _state["simulation_summary"] = summary_dict
    except Exception as _exc:
        print(f"[SUMMARY] build failed: {_exc}", flush=True)


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
                    None, netconvert, osm_file, net_file, download_bbox
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
            _state["synthetic_network_nodes"] = generate_network_nodes_for_bbox(
                _state["current_bbox"],
                traffic_lambda=(_state.get("policy_options") or {}).get("traffic_lambda", 5.0),
            )
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


def _prepare_simulation_run(req: SimStartRequest) -> random.Random:
    """
    Per-run setup shared by both the live /api/simulation/start endpoint and
    the headless batch runner (Phase 2): reloads network nodes, resets the
    per-run _state keys, creates the persistent simulation_runs row, seeds
    device/ITS load, and applies the requested algorithm selection.

    Does NOT touch _sim_thread/_stop_event/_pause_event (live-endpoint-only
    concerns — a previous real-time thread to clean up, irrelevant to a
    headless batch run that never starts one) and does NOT decide SUMO vs
    mock-graph mode (caller's responsibility).

    Returns the random.Random instance seeded from req.seed (or
    self-seeded from OS entropy if req.seed is None) — pass this into
    _evaluate_mock_route() and any other per-run sampling.
    """
    # Reload network nodes from the DB so any user-created base stations
    # added since the last setup/run are included as connection candidates.
    _state["network_nodes"] = merged_network_nodes()

    _state["sim_running"] = True
    _state["vehicle_pos"] = None
    _state["error"] = None
    _state["warning"] = None
    _state["route_coords"] = []
    _state["route_edges"] = []
    _state["background_vehicles"] = []
    _state["background_vehicle_ids"] = []
    # 이전 런의 엣지 기록 초기화 (다음 런에서 "현재" 고정 버그 방지)
    _state["edge_history"] = []
    _state["edge_avg_speeds"] = {}
    _state["edge_telemetry"] = []
    _state["selected_algorithms"] = req.algorithm_config or {}
    _state["simulation_run_id"] = create_simulation_run(
        req.origin, req.dest, _state["sim_mode"],
        seed=req.seed, scenario_id=req.scenario_id, batch_id=req.batch_id,
        sheet_id=req.sheet_id, sheet_name=req.sheet_name,
    )
    # 재현성: seed가 주어지면 이 런의 모든 Poisson/배경차량 샘플링을 격리된 random.Random
    # 인스턴스로 고정한다(전역 random 모듈은 건드리지 않음 — 다른 코드의 randomness와 분리).
    _state["sim_seed"] = req.seed
    _rng = random.Random(req.seed)

    # Stage-1: apply simulation config (per-request overrides persistent state)
    _raw_cfg = req.simulation_config or _state.get("simulation_config")
    _sim_cfg = merge_with_default_config(_raw_cfg)
    _apply_simulation_config(_sim_cfg)
    _seed_other_device_load(
        _state.get("network_nodes") or [],
        (_state.get("policy_options") or {}).get("other_device_lambda", 300.0),
        _rng,
    )
    _seed_its_congestion_load(_state.get("network_nodes") or [])
    # algorithm_config uses frontend key names (latency, resource_allocation) and
    # backend key names (latency_algorithm, allocation_algorithm) — accept both.
    _lat_alg = (
        req.algorithm_config.get("latency_algorithm")
        or req.algorithm_config.get("latency")
    )
    _alloc_alg_cfg = (
        req.algorithm_config.get("allocation_algorithm")
        or req.algorithm_config.get("resource_allocation")
    )
    if _lat_alg and LATENCY_AVAILABLE:
        try:
            LATENCY_REGISTRY.set_algorithm(_lat_alg)
            _state["latency_algorithm"] = _lat_alg
        except Exception:
            pass
    if _alloc_alg_cfg:
        _state["allocation_algorithm"] = _alloc_alg_cfg
    _bs_alg_cfg = req.algorithm_config.get("base_station_selection")
    if _bs_alg_cfg and ROUTE_COST_AVAILABLE:
        try:
            from app.services.routing.route_cost_function import set_bs_selection_algorithm
            set_bs_selection_algorithm(_bs_alg_cfg)
            _state["bs_selection_algorithm"] = _bs_alg_cfg
        except Exception:
            pass

    return _rng


def _evaluate_mock_route(req: SimStartRequest, _rng: random.Random, *, synchronous: bool = False) -> dict:
    """
    OSM-fallback (mock-graph) route search + K-path + resource-allocation +
    cost evaluation — STEP 1 through STEP 4+5, extracted verbatim from the
    mock-graph branch of /api/simulation/start so the headless batch runner
    (Phase 2) can call exactly the same evaluation the live Simulation tab
    uses, without spawning the real-time mock_simulation_thread.

    Mutates _state exactly as before (route_coords, route_edges, route_edge_names,
    route_buildings, background_vehicles, algorithm_metrics, k_path_candidates,
    route_cost_result, simulation_summary, ...) — callers read those back from
    _state after this returns, same as the live endpoint always has.

    synchronous : False (default, live-endpoint behaviour) — cost evaluation
                  runs in a fire-and-forget background thread so the vehicle
                  appears on the map immediately while metrics catch up a beat
                  later. True (batch runner) — runs inline so algorithm_metrics
                  /route_cost_result/k_path_candidates/simulation_summary are
                  guaranteed populated by the time this function returns.

    Returns {"path": [...], "route_coords": [...]} (the rest is in _state).
    """
    _route_algo = req.algorithm_config.get("route", "dijkstra")
    if not _state["mock_graph"]:
        raise HTTPException(status_code=500, detail="Fallback OSM graph를 준비하지 못했습니다.")
    try:
        start_node = nearest_mock_node(_state["mock_graph"], req.origin["lat"], req.origin["lng"])
        end_node = nearest_mock_node(_state["mock_graph"], req.dest["lat"], req.dest["lng"])
        if _route_algo == "astar":
            path = astar_mock_path(_state["mock_graph"], start_node, end_node)
        else:
            path = shortest_mock_path(_state["mock_graph"], start_node, end_node)
        route_coords = mock_route_coords(_state["mock_graph"], path)
    except RuntimeError as exc:
        _state["sim_running"] = False
        raise HTTPException(status_code=400, detail=str(exc))

    # ── STEP 1: Generate K topology paths (before allocation) ──────────────
    _raw_k_paths: list = []
    _k_candidates_data: list = []
    if ROUTE_COST_AVAILABLE:
        try:
            _raw_k_paths = yen_k_paths_mock(_state["mock_graph"], start_node, end_node, k=5)
            if _raw_k_paths:
                _k_candidates_data = build_mock_k_edge_data(_state["mock_graph"], _raw_k_paths)
        except Exception as _k_gen_exc:
            print(f"[COST] K-path generation failed: {_k_gen_exc}", flush=True)

    # ── STEP 1.5: 다중차량 실험군 — 배경 차량 생성 (vehicle_count > 1일 때만) ──
    _bg_vehicles: list = []
    if req.vehicle_count and req.vehicle_count > 1 and _state.get("current_bbox"):
        try:
            _bg_vehicles = _generate_background_vehicles(
                _state["mock_graph"], _state["current_bbox"], req.vehicle_count - 1, _rng
            )
        except Exception as _bg_exc:
            print(f"[BG-VEHICLES] generation failed: {_bg_exc}", flush=True)
    _state["background_vehicles"] = _bg_vehicles

    # ── STEP 2: Resource allocation (P1+P3+P4 fixes) ──────────────────────
    # Runs BEFORE path selection so Dijkstra/K-path use allocation-updated loads.
    # Uses origin as initial vehicle (P1), passes K paths + look-ahead (P4).
    # Background vehicles (실험군) are folded into the demand so the target's
    # latency reflects the full fleet, not just itself.
    _alloc_out = None
    _alloc_dict: Optional[dict] = None
    if RESOURCE_DEMAND_AVAILABLE and _state.get("network_nodes"):
        _alloc_algo = (
            req.algorithm_config.get("allocation_algorithm")
            or req.algorithm_config.get("resource_allocation")
            or _state.get("allocation_algorithm")
            or "traffic_aware_allocation"
        )
        _alloc_out = _run_resource_allocation(req.origin, _raw_k_paths, _alloc_algo, _bg_vehicles)
        _alloc_dict = _alloc_out.to_dict() if _alloc_out else None

    # ── STEP 3: Path selection using allocation-updated loads ──────────────
    # Each route_algorithm value runs a real, distinct search — see
    # astar_mock_path / best_of_k_path / network_weighted_mock_path /
    # lookahead_weighted_mock_path. rl_routing and unknown values stay on the
    # baseline path chosen above (no trained RL agent exists yet).
    _mock_routing_mode = "astar" if _route_algo == "astar" else "baseline_dijkstra"

    if _route_algo == "k_shortest_path" and ROUTE_COST_AVAILABLE and _k_candidates_data:
        try:
            # Best K-path candidate re-evaluated with allocation costs
            _k_results = _store_k_candidates(_k_candidates_data, allocation_output=_alloc_dict)
            if _k_results:
                path = _k_results[0].path  # rank 0 = lowest allocation-adjusted cost
                route_coords = mock_route_coords(_state["mock_graph"], path)
                _mock_routing_mode = "k_shortest_path"
                print(
                    f"[SIM] Selected path: rank=0 of {len(_k_results)}, "
                    f"total_cost={_k_results[0].total_cost:.3f}, "
                    f"deficit={_k_results[0].resource_deficit_cost:.4f}",
                    flush=True,
                )
                _k_candidates_data = []  # already stored — skip duplicate call below
        except Exception as _ksp_exc:
            print(f"[SIM] K-shortest-path routing failed: {_ksp_exc} — Dijkstra baseline", flush=True)

    elif _route_algo in ("network_aware", "network_aware_routing") and ROUTE_COST_AVAILABLE and _state.get("network_nodes"):
        try:
            net_path = network_weighted_mock_path(
                _state["mock_graph"], start_node, end_node,
                _state["network_nodes"], _route_cost_weights,
            )
            path = net_path
            route_coords = mock_route_coords(_state["mock_graph"], path)
            _mock_routing_mode = "network_aware"
            print(f"[SIM] Network-weighted route: {len(path)} nodes", flush=True)
        except Exception as _net_exc:
            print(f"[SIM] Network routing failed: {_net_exc} — Dijkstra baseline", flush=True)

    elif _route_algo in ("lookahead", "look_ahead_routing") and ROUTE_COST_AVAILABLE and _state.get("network_nodes"):
        try:
            _lookahead_hops = (
                (_state.get("simulation_config") or {}).get("policy_options", {}).get("lookahead_k", 3)
            )
            la_path = lookahead_weighted_mock_path(
                _state["mock_graph"], start_node, end_node,
                _state["network_nodes"], _route_cost_weights, _lookahead_hops,
            )
            path = la_path
            route_coords = mock_route_coords(_state["mock_graph"], path)
            _mock_routing_mode = "lookahead"
            print(f"[SIM] Look-ahead route: {len(path)} nodes", flush=True)
        except Exception as _la_exc:
            print(f"[SIM] Look-ahead routing failed: {_la_exc} — Dijkstra baseline", flush=True)

    _state["route_edges"] = path

    # Prepend exact origin so vehicle starts from user-selected point, not nearest OSM node
    origin_pt = [req.origin["lat"], req.origin["lng"]]
    if route_coords and haversine_m(origin_pt[0], origin_pt[1], route_coords[0][0], route_coords[0][1]) > 5.0:
        route_coords = [origin_pt] + route_coords

    _state["route_coords"] = route_coords

    # Collect street names from OSM way_names stored in mock graph
    _mock_way_names = _state["mock_graph"].get("way_names", {})
    _mock_graph_nodes = _state["mock_graph"].get("nodes", {})
    _mock_edge_names: dict[str, str] = {}
    _mock_edge_midpoints: dict[str, tuple[float, float]] = {}
    for _a, _b in zip(path, path[1:]):
        _eid = f"{_a}_{_b}"
        _mock_edge_names[_eid] = _mock_way_names.get((_a, _b)) or _mock_way_names.get((_b, _a)) or ""
        # Collect midpoint for V-World enrichment
        _na = _mock_graph_nodes.get(_a)
        _nb = _mock_graph_nodes.get(_b)
        if _na and _nb:
            _mock_edge_midpoints[_eid] = (
                (_na["lat"] + _nb["lat"]) / 2,
                (_na["lng"] + _nb["lng"]) / 2,
            )
    _state["route_edge_names"] = _mock_edge_names
    # V-World enrichment for edges still missing names (background) — skip entirely
    # in headless/batch mode (synchronous=True): firing N background threads per
    # scenario in a sweep is pure waste, nothing renders street names anywhere.
    if VWORLD_API_KEY and not synchronous:
        threading.Thread(
            target=_enrich_edge_names_vworld,
            args=(_mock_edge_midpoints, _state["route_edge_names"]),
            daemon=True,
        ).start()

    _state["route_buildings"], _state["building_debug"] = load_route_buildings(
        route_coords, _state.get("network_nodes")
    )

    # ── STEP 4+5: cost evaluation ────────────────────────────────────────
    # Live endpoint (synchronous=False): fire-and-forget background thread so
    # the vehicle appears on the map immediately while metrics catch up a beat
    # later. Batch runner (synchronous=True): run inline so the caller can read
    # _state["algorithm_metrics"]/["route_cost_result"]/["k_path_candidates"]/
    # ["simulation_summary"] immediately after this function returns.
    if ROUTE_COST_AVAILABLE:
        _mock_edge_data = build_mock_edge_data(_state["mock_graph"], path)
        # _mock_routing_mode was already set by the route_algorithm dispatch above
        _mock_k_data = list(_k_candidates_data)  # snapshot before clearing
        _mock_alloc = _alloc_dict

        def _bg_mock_cost():
            _store_route_cost(_mock_edge_data, _mock_routing_mode)
            if _mock_k_data:
                _store_k_candidates(_mock_k_data, allocation_output=_mock_alloc)

        if synchronous:
            _bg_mock_cost()
        else:
            threading.Thread(target=_bg_mock_cost, daemon=True).start()

    return {"path": path, "route_coords": route_coords}


@app.post("/api/simulation/start")
async def start_simulation(req: SimStartRequest):
    """Start SUMO simulation with Dijkstra routing between origin and dest."""
    global _sim_thread, _stop_event, _pause_event

    if not _state["network_ready"]:
        raise HTTPException(status_code=400, detail="네트워크가 준비되지 않았습니다. 먼저 구역을 설정하세요.")
    if _active_batch_id is not None:
        raise HTTPException(
            status_code=409,
            detail="시나리오 배치가 실행 중입니다(같은 _state를 공유하므로 동시 실행 불가). 배치가 끝난 후 다시 시도하세요.",
        )

    # 기존 스레드 정리 (실행 중이거나 일시정지 중인 경우 모두) — 실시간 엔드포인트 전용 관심사,
    # _prepare_simulation_run()에는 포함하지 않는다(헤드리스 배치 러너는 _sim_thread를 안 씀).
    if _sim_thread and _sim_thread.is_alive():
        _pause_event.clear()  # 일시정지 해제 후 종료 신호
        _stop_event.set()
        _sim_thread.join(timeout=5)
    _stop_event = threading.Event()
    _pause_event = threading.Event()

    _rng = _prepare_simulation_run(req)
    _route_algo = req.algorithm_config.get("route", "dijkstra")

    use_sumo, sumo_error = can_run_sumo()
    if use_sumo and _state["net_file"]:
        _state["sim_mode"] = "sumo"
        _sim_thread = threading.Thread(
            target=simulation_thread,
            args=(_state["net_file"], req.origin, req.dest, _stop_event),
            kwargs={"route_algorithm": _route_algo, "vehicle_count": req.vehicle_count},
            daemon=True,
        )
    else:
        _evaluate_mock_route(req, _rng, synchronous=False)
        _state["sim_mode"] = "mock"
        _state["warning"] = (
            "SUMO runtime is unavailable on this machine, so the simulation is running in "
            f"OSM fallback mode. {sumo_error or ''}".strip()
        )
        _sim_thread = threading.Thread(
            target=mock_simulation_thread,
            args=(_state["route_coords"], _stop_event),
            daemon=True,
        )

    _sim_thread.start()
    return {"ok": True, "mode": _state["sim_mode"], "warning": _state["warning"], "run_id": _state.get("simulation_run_id")}


def _evaluate_route_scenario(spec: ScenarioSpec, scenario_id: str, batch_id: str) -> dict:
    """route_metrics 모드: 실시간 스레드 없이 _prepare_simulation_run + _evaluate_mock_route(synchronous=True)로 즉시 평가."""
    if not _state["network_ready"]:
        raise RuntimeError("네트워크가 준비되지 않았습니다. 먼저 구역을 설정하세요.")
    if not spec.origin or not spec.dest:
        raise RuntimeError("route_metrics 모드는 origin/dest가 필요합니다.")

    req = SimStartRequest(
        origin=spec.origin,
        dest=spec.dest,
        vehicle_count=spec.vehicle_count,
        seed=spec.seed,
        algorithm_config=spec.algorithm_config or {},
        simulation_config=spec.simulation_config,
        scenario_id=scenario_id,
        batch_id=batch_id,
    )
    _state["sim_mode"] = "mock"  # 배치는 항상 mock-graph 즉시평가만 사용(SUMO 실시간 스레드 없음)
    _rng = _prepare_simulation_run(req)
    _evaluate_mock_route(req, _rng, synchronous=True)

    # 대시보드가 실시간으로 보여주는 network_telemetry(L_base/L_signal/L_queue 분해,
    # 연결 기지국, 후보 기지국 비교, RB deficit 등)는 원래 vehicle_pos가 틱마다 갱신될 때만
    # 채워지는데, 배치 평가에는 움직이는 차량이 없다 — origin을 대표 위치로 한 번 계산해서
    # 같이 묶어야 배치 결과로도 대시보드와 동일한 축으로 비교할 수 있다(2026-06-24 사용자 피드백).
    update_network_telemetry(spec.origin)
    telemetry = _state.get("network_telemetry")

    metrics = _state.get("algorithm_metrics") or {}
    route_cost = _state.get("route_cost_result")
    summary = _state.get("simulation_summary")
    if _state.get("simulation_run_id"):
        finish_simulation_run(_state["simulation_run_id"], {
            "algorithm_metrics": metrics,
            "route_cost_result": route_cost,
            "simulation_summary": summary,
            "network_telemetry": telemetry,
        })
    return {
        "mode": "route_metrics",
        "route_cost_result": route_cost,
        "algorithm_metrics": metrics,
        "simulation_summary": summary,
        "network_telemetry": telemetry,
    }


def _evaluate_rl_scenario(spec: ScenarioSpec) -> dict:
    """rl_episode 모드: 도로 그래프 + 기지국 목록만 읽는 순수 평가 — /api/rl/episode와 동일한 로직이며
    이미 헤드리스(실시간 스레드/_state 변경 없음)이므로 Phase 1 같은 분리 작업이 필요 없다."""
    if not RL_AVAILABLE:
        raise RuntimeError("RL 모듈을 사용할 수 없습니다.")
    graph = _state.get("mock_graph")
    if not graph:
        raise RuntimeError("도로 그래프가 아직 로드되지 않았습니다. 시뮬레이션을 먼저 설정하세요.")
    road_nodes = graph.get("nodes", {})
    bs_nodes = _state.get("network_nodes") or []
    if not spec.origin_id or spec.origin_id not in road_nodes:
        raise RuntimeError(f"출발 노드 '{spec.origin_id}'를 도로 그래프에서 찾을 수 없습니다.")
    if not spec.dest_id or spec.dest_id not in road_nodes:
        raise RuntimeError(f"목적지 노드 '{spec.dest_id}'를 도로 그래프에서 찾을 수 없습니다.")
    if spec.policy not in SUPPORTED_POLICIES:
        raise RuntimeError(f"지원하지 않는 정책입니다. 선택 가능: {SUPPORTED_POLICIES}")

    env = V2XRoutingEnv(
        graph=graph,
        road_nodes=road_nodes,
        bs_nodes=bs_nodes,
        origin_id=spec.origin_id,
        dest_id=spec.dest_id,
        max_steps=spec.max_steps,
        allocation_output=_state.get("last_allocation_result"),
    )
    if spec.n_episodes == 1:
        result = run_episode(
            env, policy=spec.policy, seed=spec.seed, record_trajectory=spec.record_trajectory,
        )
        payload = result.to_dict()
    else:
        payload = run_episodes(
            env, n_episodes=spec.n_episodes, policy=spec.policy,
            seed=spec.seed, record_trajectory=spec.record_trajectory,
        )
    return {"mode": "rl_episode", **payload}


def _run_scenario_batch(batch_id: str, scenarios: list[ScenarioSpec]) -> None:
    """배치 워커(백그라운드 스레드). _state를 시나리오마다 재사용/덮어쓰므로 반드시 순차 실행."""
    global _active_batch_id
    run = _batch_runs[batch_id]
    for i, spec in enumerate(scenarios):
        item_id = spec.id or spec.label or str(i)
        # 평가 함수(_evaluate_route_scenario/_evaluate_rl_scenario)는 결과만 반환하므로,
        # 원본 요청 파라미터(vehicle_count/seed/origin 등)는 여기서 같이 기록해야 프런트의
        # 시나리오 비교 카드가 결과만 보고도 "무엇을 입력해서 나온 결과인지" 알 수 있다.
        base_info = {
            "index": i, "id": item_id, "label": spec.label, "mode": spec.mode,
            "vehicle_count": spec.vehicle_count, "seed": spec.seed,
            "origin": spec.origin, "dest": spec.dest,
            "origin_id": spec.origin_id, "dest_id": spec.dest_id,
        }
        try:
            if spec.mode == "rl_episode":
                result = _evaluate_rl_scenario(spec)
            else:
                result = _evaluate_route_scenario(spec, scenario_id=item_id, batch_id=batch_id)
            run["results"].append({**base_info, "status": "done", **result})
        except Exception as exc:
            run["results"].append({**base_info, "status": "error", "error": str(exc)})
        run["completed"] = i + 1
    run["status"] = "completed"
    run["ended_at"] = datetime.now(timezone.utc).isoformat()
    _active_batch_id = None


@app.post("/api/scenarios/batch")
def start_scenario_batch(req: ScenarioBatchRequest):
    """
    Phase 2: 여러 시나리오를 순차적으로 헤드리스 평가(실시간 애니메이션 스레드 없음).
    각 시나리오는 mode="route_metrics"(경로/자원할당/기지국선택 비교, lat/lng 기반) 또는
    mode="rl_episode"(RL 베이스라인 정책 평가, 도로그래프 노드ID 기반) 중 하나.

    _state를 공유하는 단일 순차 실행이므로 동시에 배치 하나만 돌 수 있고, 실행 중에는
    실시간 /api/simulation/start도 막힌다(서로 같은 _state를 덮어써서 충돌하기 때문).

    즉시 batch_id를 반환하고 백그라운드 스레드에서 순차 실행 — 진행 상황과 결과는
    GET /api/scenarios/batch/{batch_id}로 폴링.
    """
    global _active_batch_id
    if not req.scenarios:
        raise HTTPException(status_code=400, detail="scenarios가 비어 있습니다.")
    if len(req.scenarios) > 100:
        raise HTTPException(status_code=400, detail="배치당 최대 100개 시나리오까지 지원합니다.")
    if _active_batch_id is not None:
        raise HTTPException(status_code=409, detail="다른 배치가 이미 실행 중입니다.")
    if _sim_thread and _sim_thread.is_alive():
        raise HTTPException(
            status_code=409,
            detail="실시간 시뮬레이션이 진행 중입니다(같은 _state를 공유하므로 동시 실행 불가). 먼저 종료하세요.",
        )

    batch_id = str(uuid4())
    _batch_runs[batch_id] = {
        "batch_id": batch_id,
        "label": req.label,
        "status": "running",
        "total": len(req.scenarios),
        "completed": 0,
        "results": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
    }
    _active_batch_id = batch_id
    threading.Thread(target=_run_scenario_batch, args=(batch_id, req.scenarios), daemon=True).start()
    return {"batch_id": batch_id, "status": "running", "total": len(req.scenarios)}


@app.get("/api/scenarios/batch/{batch_id}")
def get_scenario_batch(batch_id: str):
    """배치 진행 상태 + 완료된 시나리오 결과 폴링."""
    run = _batch_runs.get(batch_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"배치 '{batch_id}'를 찾을 수 없습니다.")
    return run


@app.post("/api/scenarios/generate")
def generate_scenarios(req: ScenarioGenerateRequest):
    """
    Phase 3: 자연어 설명으로 시나리오 묶음을 LLM이 생성 + 도로망에 snap.

    LLM은 현재 로드된 지역의 bbox만 알 뿐 실제 도로망은 모르므로, origin/dest로 내놓은
    좌표가 건물 위/도로 밖에 떨어질 수 있다. 그래서 응답을 그대로 쓰지 않고:
      1) bbox 안에 있는지 1차로 거른다(많이 벗어난 좌표는 버리고 warnings에 기록).
      2) 통과한 좌표를 nearest_mock_node()로 실제 도로 그래프 노드에 snap한다
         (origin/dest를 snap된 노드의 실좌표로 덮어쓰고, origin_node_id/dest_node_id에 기록).
    결과는 ScenarioSpec과 동일한 모양이라 POST /api/scenarios/batch에 그대로 넣을 수 있다.
    """
    from app.services.llm.client import generate as llm_generate

    if not _state["network_ready"] or not _state.get("mock_graph"):
        raise HTTPException(
            status_code=400,
            detail="도로 그래프가 아직 로드되지 않았습니다. 먼저 구역을 설정하세요(snap-to-road에 필요).",
        )
    count = max(1, min(req.count, 20))
    bbox = _state.get("current_bbox") or {}
    graph = _state["mock_graph"]

    prompt = f"""당신은 V2X 차량-네트워크 시뮬레이션의 시나리오 설계자입니다.
아래 사용자 설명에 맞는 시뮬레이션 시나리오 {count}개를 생성하세요.

=== 사용자 설명 ===
{req.description}

=== 제약 조건 ===
- 모든 origin/dest 좌표는 반드시 아래 bbox 범위 안에 있어야 합니다:
  남쪽(lat 최소)={bbox.get('s')}, 북쪽(lat 최대)={bbox.get('n')}, 서쪽(lng 최소)={bbox.get('w')}, 동쪽(lng 최대)={bbox.get('e')}
- origin과 dest는 서로 달라야 하고, 직선거리로 최소 200m 이상 떨어져야 합니다.
- vehicle_count는 1~30 사이 정수로, 사용자 설명의 의도(예: "혼잡"은 큰 값, "단일 차량"은 1)를 반영하세요.
- label은 시나리오를 한국어로 간단히 설명하는 10자 내외 문구로 작성하세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 출력하지 마세요.
{{
  "scenarios": [
    {{"label": "...", "origin": {{"lat": 0.0, "lng": 0.0}}, "dest": {{"lat": 0.0, "lng": 0.0}}, "vehicle_count": 1}}
  ]
}}"""

    try:
        text, provider_used = llm_generate(prompt, provider=req.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM API 오류 ({req.provider or 'auto'}): {str(exc)[:400]}")

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        raw_scenarios = json.loads(text[start:end]).get("scenarios", []) if start >= 0 and end > start else []
    except (json.JSONDecodeError, ValueError):
        raw_scenarios = []

    if not raw_scenarios:
        raise HTTPException(status_code=502, detail="LLM 응답에서 시나리오를 파싱하지 못했습니다.")

    pad = 0.02  # bbox 살짝 벗어난 좌표(경계 근처)는 허용, 멀리 벗어난 건 버림
    lat_lo, lat_hi = bbox.get("s", -90) - pad, bbox.get("n", 90) + pad
    lng_lo, lng_hi = bbox.get("w", -180) - pad, bbox.get("e", 180) + pad

    def _in_bbox(pt: dict) -> bool:
        return lat_lo <= pt.get("lat", 0) <= lat_hi and lng_lo <= pt.get("lng", 0) <= lng_hi

    scenarios: list[dict] = []
    warnings: list[str] = []
    seed_base = req.seed_base if req.seed_base is not None else 0

    for i, raw in enumerate(raw_scenarios[:count]):
        label = str(raw.get("label") or f"시나리오 {i + 1}")
        origin = raw.get("origin") or {}
        dest = raw.get("dest") or {}
        if not _in_bbox(origin) or not _in_bbox(dest):
            warnings.append(f"'{label}': origin/dest가 로드된 지역 범위를 벗어나 제외했습니다.")
            continue
        try:
            origin_node_id = nearest_mock_node(graph, float(origin["lat"]), float(origin["lng"]))
            dest_node_id = nearest_mock_node(graph, float(dest["lat"]), float(dest["lng"]))
        except (RuntimeError, KeyError, TypeError):
            warnings.append(f"'{label}': 도로망에 snap하지 못해 제외했습니다.")
            continue

        snapped_origin = graph["nodes"][origin_node_id]
        snapped_dest = graph["nodes"][dest_node_id]
        if origin_node_id == dest_node_id:
            warnings.append(f"'{label}': origin/dest가 같은 도로 노드로 snap되어 제외했습니다.")
            continue

        vehicle_count = max(1, min(int(raw.get("vehicle_count") or 1), 30))
        scenarios.append({
            "id": f"gen-{i + 1}",
            "label": label,
            "mode": "route_metrics",
            "source": "llm_generated",
            "origin": {"lat": snapped_origin["lat"], "lng": snapped_origin["lng"]},
            "dest": {"lat": snapped_dest["lat"], "lng": snapped_dest["lng"]},
            "origin_node_id": origin_node_id,
            "dest_node_id": dest_node_id,
            "vehicle_count": vehicle_count,
            "seed": seed_base + i,
        })

    if not scenarios:
        raise HTTPException(
            status_code=502,
            detail="LLM이 생성한 시나리오 중 도로망에 유효하게 snap된 것이 없습니다. " + " ".join(warnings),
        )

    return {"scenarios": scenarios, "warnings": warnings, "provider": provider_used, "requested_count": count}


@app.post("/api/simulation/stop")
async def stop_simulation():
    """시뮬레이션 일시정지 (스레드·TraCI 연결 유지)."""
    global _pause_event
    _pause_event.set()
    _state["sim_running"] = False
    return {"ok": True}


@app.post("/api/simulation/resume")
async def resume_simulation():
    """일시정지된 시뮬레이션 재개."""
    global _pause_event
    if _sim_thread is not None and _sim_thread.is_alive() and _pause_event.is_set():
        _pause_event.clear()
        _state["sim_running"] = True
        return {"ok": True}
    raise HTTPException(status_code=400, detail="일시정지 중인 시뮬레이션이 없습니다.")


@app.post("/api/simulation/reset")
async def reset_simulation():
    global _stop_event, _sim_thread, _pause_event
    _pause_event.clear()  # 일시정지 해제 후 종료
    _stop_event.set()
    if _sim_thread and _sim_thread.is_alive():
        _sim_thread.join(timeout=5)
    finish_simulation_run(_state.get("simulation_run_id"), {
        "vehicle_pos": _state.get("vehicle_pos"),
        "network_telemetry": _state.get("network_telemetry"),
        "sim_mode": _state.get("sim_mode"),
        # 비교/리포트에 실제로 쓰이는 메트릭까지 같이 저장 — 이전에는 vehicle_pos만 있어서
        # 영구 저장된 run을 나중에 비교할 방법이 없었다.
        "algorithm_metrics": _state.get("algorithm_metrics"),
        "simulation_summary": _state.get("simulation_summary"),
    })
    _sim_thread = None
    _stop_event = threading.Event()
    _pause_event = threading.Event()
    reset_simulation_state()
    return {"ok": True}


@app.get("/api/simulation/runs")
def get_simulation_runs(limit: int = 50, offset: int = 0):
    """
    Persistent, team-shared simulation run history (backed by Postgres
    `simulation_runs`, populated automatically on every start/reset —
    no extra action needed to record a run).

    Falls back gracefully when no DB is connected: returns
    {"available": False} rather than an error, so the frontend can fall
    back to its local-only history (localStorage) instead.

    Response (available):
      { "available": true, "runs": [ {id, origin, destination, mode,
        scenario_id, seed, batch_id, started_at, ended_at, metrics_json}, ... ],
        "limit": int, "offset": int }
    """
    if not postgis_available():
        return {"available": False, "reason": "DB가 연결되어 있지 않습니다 (로컬 기록만 사용 가능)."}
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    runs = list_simulation_runs(limit=limit, offset=offset)
    return {"available": True, "runs": runs, "limit": limit, "offset": offset}


@app.get("/api/simulation/runs/by-sheet/{sheet_id}")
def get_simulation_runs_by_sheet(sheet_id: str, limit: int = 50):
    """Same shape as /api/simulation/runs, filtered to one sheet — lets the
    Dashboard/Report tabs show a sheet's durable history from the DB instead
    of (or alongside) whatever's still sitting in this browser's localStorage."""
    if not postgis_available():
        return {"available": False, "reason": "DB가 연결되어 있지 않습니다 (로컬 기록만 사용 가능)."}
    runs = list_simulation_runs_by_sheet(sheet_id, limit=max(1, min(limit, 200)))
    return {"available": True, "runs": runs}


class RunCaptureRequest(BaseModel):
    sim_logs: list[dict] = []
    sim_history: list[dict] = []
    route_edges: Optional[dict] = None


@app.post("/api/simulation/runs/{run_id}/capture")
def capture_simulation_run(run_id: int, req: RunCaptureRequest):
    """
    프런트의 시트 캡처(도착/정지 시점)가 이 엔드포인트도 같이 호출해, sim_logs/sim_history/
    route_edges처럼 백엔드 _state에는 없는(프런트 전용) 데이터를 DB simulation_runs 행에
    영구 저장한다. finish_simulation_run이 metrics_json을 머지하므로, 이후 다른 경로(예:
    /api/simulation/reset)가 같은 run_id를 다시 finish해도 여기서 저장한 값을 덮어쓰지 않는다.
    """
    finish_simulation_run(run_id, {
        "vehicle_pos": _state.get("vehicle_pos"),
        "network_telemetry": _state.get("network_telemetry"),
        "sim_mode": _state.get("sim_mode"),
        "algorithm_metrics": _state.get("algorithm_metrics"),
        "simulation_summary": _state.get("simulation_summary"),
        "sim_logs": req.sim_logs,
        "sim_history": req.sim_history,
        "route_edges": req.route_edges,
    })
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


# ── Route cost function API ───────────────────────────────────────────────────
@app.get("/api/route/evaluate")
def get_route_evaluate():
    """Return the last computed route cost breakdown (populated after simulation start)."""
    result = _state.get("route_cost_result")
    if not result:
        return {"available": False, "reason": "시뮬레이션을 먼저 시작하세요."}
    return result


@app.get("/api/simulation/config")
def get_simulation_config():
    """Return current simulation config and effective cost weights (Stage-1)."""
    cfg_dict = _state.get("simulation_config") or SimulationConfigModel().model_dump()
    return {
        "simulation_config": cfg_dict,
        "policy_options": _state.get("policy_options"),
        "effective_cost_weights": _weights_to_dict(),
    }


@app.put("/api/simulation/config")
def update_simulation_config(req: SimulationConfigRequest):
    """Persist simulation config and apply immediately (Stage-1)."""
    cfg = validate_simulation_config(req.simulation_config)
    _apply_simulation_config(cfg)
    return {"ok": True, "applied_config": _state.get("simulation_config")}


@app.get("/api/simulation/config/schema")
def get_simulation_config_schema():
    """Return JSON schema for the SimulationConfigModel (for frontend form generation)."""
    return SimulationConfigModel.model_json_schema()


@app.get("/api/route/cost-weights")
def get_cost_weights():
    """Return current cost weights and normalization scales."""
    if not ROUTE_COST_AVAILABLE:
        return {"available": False}
    return {"available": True, **_weights_to_dict(), "norm_scales": _norms_to_dict()}


@app.post("/api/route/cost-weights")
def set_cost_weights(req: CostWeightsRequest):
    """
    Update route cost weights.
    Changes take effect on the next simulation start.
    """
    global _route_cost_weights
    if not ROUTE_COST_AVAILABLE:
        raise HTTPException(status_code=503, detail="경로 비용 모듈을 사용할 수 없습니다.")
    _route_cost_weights = CostWeights(
        w_distance=req.w_distance,
        w_time=req.w_time,
        w_latency=req.w_latency,
        w_load=req.w_load,
        w_handover=req.w_handover,
        w_blockage=req.w_blockage,
        w_coverage_risk=req.w_coverage_risk,
    )
    return {"ok": True, **_weights_to_dict()}


@app.get("/api/route/norm-scales")
def get_norm_scales():
    """Return current normalization denominators."""
    if not ROUTE_COST_AVAILABLE:
        return {"available": False}
    return {"available": True, **_norms_to_dict()}


@app.post("/api/route/norm-scales")
def set_norm_scales(req: NormScalesRequest):
    """
    Update normalization denominators.
    Adjust when your network's typical physical ranges differ from defaults.
    Changes take effect on the next simulation start.
    """
    global _norm_scales
    if not ROUTE_COST_AVAILABLE:
        raise HTTPException(status_code=503, detail="경로 비용 모듈을 사용할 수 없습니다.")
    _norm_scales = NormScales(
        distance_km=req.distance_km,
        time_min=req.time_min,
        latency_ms=req.latency_ms,
        loss_db=req.loss_db,
    )
    return {"ok": True, **_norms_to_dict()}


@app.get("/api/analysis/summary")
def get_simulation_summary():
    """
    Return the SimulationSummary for the last completed simulation.

    This is the primary input for AI report generation.
    Pass summary["recommendation_text_seed"] and summary["to_llm_context"]
    to an LLM to generate natural-language analysis.

    Response shape:
      {
        "available": bool,
        "scenario_id": str,
        "generated_at": str,          # ISO 8601
        "selected_algorithm": str,
        "baseline_algorithm": str,
        "route_summary": { path_edge_ids, total_distance_m, ... },
        "metric_summary": { algorithms: {...}, comparison: {...} },
        "improvement_over_baseline": { cost_delta, cost_improvement_pct, ... },
        "bottleneck_sections":        [ {edge_id, load_ratio, severity, ...} ],
        "overloaded_base_stations":   [ {bs_name, load_ratio, ...} ],
        "frequent_handover_sections": [ {edge_id, from_bs_name, to_bs_name, ...} ],
        "high_latency_sections":      [ {edge_id, latency_ms, excess_ms, ...} ],
        "future_connectivity_risk_sections": [ {edge_id, severity, ...} ],
        "recommendation_text_seed": {
          "primary_finding": str,
          "trade_offs": [str],
          "improvement_highlights": [str],
          "degradation_warnings": [str],
          "risk_factors": [str],
          "network_observations": [str],
          "suggested_focus": str
        }
      }
    """
    summary = _state.get("simulation_summary")
    if not summary:
        return {"available": False, "reason": "시뮬레이션을 먼저 시작하세요."}
    return {"available": True, **summary}


@app.get("/api/route/metrics")
def get_route_metrics():
    """
    Return per-algorithm RouteMetrics and a cross-algorithm comparison.

    Response shape:
      {
        "available": bool,
        "algorithms": {
          "<routing_mode>": { all 13 RouteMetrics fields },
          "k_path_rank_0":  { ... },
          ...
        },
        "comparison": {
          "rankings":        { metric: [best_algo, ..., worst] },
          "scores":          { algo: { metric: 0-1 score } },
          "best_per_metric": { metric: algo_name },
          "summary_rank":    { algo: float }
        }
      }
    """
    metrics = _state.get("algorithm_metrics") or {}
    algorithms = {k: v for k, v in metrics.items() if not k.startswith("_")}
    comparison = metrics.get("_comparison")
    if not algorithms:
        return {"available": False, "reason": "시뮬레이션을 먼저 시작하세요."}
    return {
        "available": True,
        "algorithms": algorithms,
        "comparison": comparison,
    }


@app.post("/api/route/compare-algorithms")
def post_compare_algorithms():
    """
    Re-evaluate the current route under every latency / BS-selection /
    resource-allocation algorithm and store results in
    _state["algorithm_comparison"] for the Comparison tab. Runs in a
    background thread — poll GET /api/route/compare-algorithms for status.
    """
    if not _state.get("route_cost_result"):
        raise HTTPException(status_code=400, detail="시뮬레이션을 먼저 실행하세요.")
    if (_state.get("algorithm_comparison") or {}).get("status") == "running":
        return {"ok": True, "status": "running"}
    threading.Thread(target=_run_algorithm_comparison, daemon=True).start()
    return {"ok": True, "status": "running"}


@app.get("/api/route/compare-algorithms")
def get_compare_algorithms():
    """Poll the status/result of the algorithm-comparison sweep."""
    return _state.get("algorithm_comparison") or {"status": "idle"}


@app.get("/api/latency/algorithms")
def get_latency_algorithms():
    """
    Return all registered latency algorithms and the currently active one.

    Response shape
    --------------
    {
      "available": bool,
      "current_algorithm": str,
      "algorithms": [
        {
          "id": str,
          "description": str,
          "requires_buildings": bool,
          "requires_mec": bool,
          "is_current": bool
        }, ...
      ]
    }
    """
    if not LATENCY_AVAILABLE:
        return {"available": False}
    return {
        "available": True,
        "current_algorithm": LATENCY_REGISTRY.current_algorithm,
        "algorithms": LATENCY_REGISTRY.list_algorithms(),
    }


@app.post("/api/latency/algorithm")
def set_latency_algorithm(req: LatencyAlgorithmRequest):
    """
    Select the active latency calculation algorithm.

    The selected algorithm is used by all subsequent routing calls
    (Dijkstra, K-path, network-aware, RL) without restarting the server.

    Request body
    ------------
    { "algorithm_id": "load_aware_latency" }

    Available IDs
    -------------
    distance_based_latency  — 거리 기반 전파 지연만 반영
    load_aware_latency      — 거리 + 기지국 부하율 (기존 공식과 호환)
    blockage_aware_latency  — 거리 + 건물 차폐 손실
    mec_aware_latency       — 거리 + MEC 처리 + M/M/1 큐잉
    full_composite_latency  — 모든 항목 (기본값)
    """
    if not LATENCY_AVAILABLE:
        raise HTTPException(status_code=503, detail="지연시간 모듈을 사용할 수 없습니다.")
    try:
        LATENCY_REGISTRY.set_algorithm(req.algorithm_id)
        _state["latency_algorithm"] = req.algorithm_id
        return {
            "ok": True,
            "current_algorithm": LATENCY_REGISTRY.current_algorithm,
        }
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/resources/algorithms")
def get_allocation_algorithms():
    """
    List all registered resource allocation algorithms.

    Response shape
    --------------
    {
      "available": bool,
      "current_algorithm": str,
      "algorithms": [{"id": str, "description": str, "is_current": bool}, ...]
    }
    """
    if not RESOURCE_DEMAND_AVAILABLE:
        raise HTTPException(status_code=503, detail="자원할당 모듈을 사용할 수 없습니다.")
    return {
        "available": True,
        "current_algorithm": ALLOCATION_REGISTRY.current_algorithm,
        "algorithms": ALLOCATION_REGISTRY.list_algorithms(),
    }


@app.post("/api/resources/algorithm")
def set_allocation_algorithm(req: AllocationAlgorithmRequest):
    """
    Set the active resource allocation algorithm.

    Supported algorithm IDs
    -----------------------
    equal_allocation               — 균등 배분
    proportional_demand_allocation — 수요 비례 배분
    traffic_aware_allocation       — 교통량 기반 배분 (기본값)
    load_balancing_allocation      — 부하 균형 배분
    latency_minimizing_allocation  — 지연시간 최소화 배분
    priority_based_allocation      — 우선순위 기반 배분
    lookahead_resource_allocation  — 미래 예측 사전 예약 배분
    """
    if not RESOURCE_DEMAND_AVAILABLE:
        raise HTTPException(status_code=503, detail="자원할당 모듈을 사용할 수 없습니다.")
    try:
        ALLOCATION_REGISTRY.set_algorithm(req.algorithm_id)
        _state["allocation_algorithm"] = req.algorithm_id
        return {
            "ok": True,
            "current_algorithm": ALLOCATION_REGISTRY.current_algorithm,
        }
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/resources/allocate")
def run_allocation(algorithm_id: Optional[str] = None):
    """
    Run resource allocation now and update BS loads in network_nodes.

    The allocation result is immediately applied to _state["network_nodes"],
    so subsequent route evaluations and latency calculations use updated loads.

    Query params
    ------------
    algorithm_id : override active algorithm for this call (optional)

    Response shape
    --------------
    {
      "ok": bool,
      "algorithm_id": str,
      "allocation_result": {...},
      "bs_load_after_allocation": {bs_id: load_ratio, ...},
      "resource_deficit_by_bs": {bs_id: deficit_rb, ...},
      "expected_latency_impact": {bs_id: ms_delta, ...}
    }
    """
    if not RESOURCE_DEMAND_AVAILABLE:
        raise HTTPException(status_code=503, detail="자원할당 모듈을 사용할 수 없습니다.")

    bs_nodes = _state.get("network_nodes") or []
    if not bs_nodes:
        return {"ok": True, "algorithm_id": "none", "note": "기지국이 없습니다.",
                "allocation_result": {}, "bs_load_after_allocation": {},
                "resource_deficit_by_bs": {}, "expected_latency_impact": {}}

    graph = _state.get("mock_graph") or {}
    vpos = _state.get("vehicle_pos")
    vehicles = [{
        "lat": vpos["lat"], "lng": vpos["lng"],
        "speed_mps": float(vpos.get("speed", 50)) / 3.6,
    }] if (vpos and vpos.get("lat")) else []

    try:
        demand_map = build_resource_demand_map(
            base_stations=bs_nodes,
            vehicles=vehicles,
            road_graph=graph,
            route_candidates=_state.get("k_path_candidates") or [],
        )
        alloc_inp = AllocationInput(
            base_stations=bs_nodes,
            vehicles=vehicles,
            resource_demand_map={k: v.to_dict() for k, v in demand_map.items()},
            config=AllocationConfig(),
        )
        algo = algorithm_id or _state.get("allocation_algorithm") or "traffic_aware_allocation"
        alloc_out = ALLOCATION_REGISTRY.compute(alloc_inp, algorithm_id=algo)
        apply_allocation_to_network_nodes(alloc_out, _state["network_nodes"])
        _state["allocation_algorithm"] = alloc_out.algorithm_id

        return {
            "ok": True,
            "algorithm_id": alloc_out.algorithm_id,
            "allocation_result": alloc_out.allocation_result,
            "bs_load_after_allocation": alloc_out.bs_load_after_allocation,
            "resource_deficit_by_bs": alloc_out.resource_deficit_by_bs,
            "expected_latency_impact": alloc_out.expected_latency_impact,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/resources/allocation-result")
def get_allocation_result():
    """
    Read the last-computed resource allocation result without recomputing.

    Unlike POST /api/resources/allocate, this does not mutate _state["network_nodes"] —
    safe to poll repeatedly from the UI for display purposes.
    """
    alloc = _state.get("last_allocation_result")
    if not alloc:
        return {"available": False}
    return {
        "available": True,
        "algorithm_id": alloc.get("algorithm_id"),
        "allocation_result": alloc.get("allocation_result", {}),
        "bs_load_after_allocation": alloc.get("bs_load_after_allocation", {}),
        "resource_deficit_by_bs": alloc.get("resource_deficit_by_bs", {}),
        "expected_latency_impact": alloc.get("expected_latency_impact", {}),
    }


@app.get("/api/resources/demand")
def get_resource_demand(node_id: Optional[str] = None, lookahead_hops: int = 3):
    """
    Calculate resource demand for all base stations based on current vehicle
    positions and nearby road traffic.

    Query params
    ------------
    node_id        : Road-graph node to run look-ahead from (optional).
                     When provided, the look-ahead result is fed into
                     ``lookahead_resource_allocation`` demand estimation.
    lookahead_hops : Number of BFS hops for look-ahead (default 3, max 10).

    Response shape
    --------------
    {
      "available": bool,
      "total_base_stations": int,
      "overloaded_count": int,         -- BSes where demand_capacity_ratio > 1
      "demand_map": {
        "<bs_id>": {
          "base_station_id": str,
          "nearby_vehicle_count": int,
          "expected_connected_vehicle_count": float,
          "nearby_traffic_density": float,   -- vehicles/km
          "average_speed": float,            -- m/s
          "estimated_resource_demand": float,
          "capacity": float,
          "demand_capacity_ratio": float
        },
        ...
      }
    }
    """
    if not RESOURCE_DEMAND_AVAILABLE:
        raise HTTPException(status_code=503, detail="자원 수요 모듈을 사용할 수 없습니다.")

    bs_nodes = _state.get("network_nodes") or []
    if not bs_nodes:
        return {"available": True, "total_base_stations": 0,
                "overloaded_count": 0, "demand_map": {}}

    graph = _state.get("mock_graph") or {}
    road_nodes = graph.get("nodes", {})

    # Single vehicle position → list of one vehicle dict
    vpos = _state.get("vehicle_pos")
    vehicles = []
    if vpos and vpos.get("lat") and vpos.get("lng"):
        vehicles = [{
            "lat": vpos["lat"],
            "lng": vpos["lng"],
            "speed_mps": float(vpos.get("speed", 50)) / 3.6,  # speed stored as km/h
        }]

    # Optional look-ahead for this specific node
    la_result = None
    if node_id and LOOK_AHEAD_AVAILABLE and road_nodes and node_id in road_nodes:
        from app.services.routing.look_ahead_scan import look_ahead_bs_scan
        la_result = look_ahead_bs_scan(
            current_node_id=node_id,
            graph=graph,
            road_nodes=road_nodes,
            bs_nodes=bs_nodes,
            lookahead_hops=max(1, min(lookahead_hops, 10)),
        )

    # Route candidates for Source 2 estimate
    k_candidates = _state.get("k_path_candidates") or []

    try:
        demand_map = build_resource_demand_map(
            base_stations=bs_nodes,
            vehicles=vehicles,
            road_graph=graph,
            traffic_data=None,
            lookahead_results=la_result,
            route_candidates=k_candidates,
        )
        return {
            "available": True,
            "total_base_stations": len(demand_map),
            "overloaded_count": sum(
                1 for v in demand_map.values() if v.demand_capacity_ratio > 1.0
            ),
            "demand_map": {k: v.to_dict() for k, v in demand_map.items()},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/route/lookahead")
def get_lookahead(node_id: str, hops: int = 3):
    """
    Look-ahead BS coverage scan from a given road node.

    Scans BS coverage for the next ``hops`` road hops using BFS.
    Near hops are weighted more heavily than far hops.

    Query params
    ------------
    node_id : Road graph node ID to scan from.
    hops    : Number of hops to scan ahead (default 3, max 10).

    Response shape
    --------------
    {
      "available": bool,
      "current_node_id": str,
      "lookahead_hops": int,
      "future_connectivity_score": float,   # 0–1; higher = more coverage guaranteed
      "risk_level": "low" | "medium" | "high",
      "total_edges_scanned": int,
      "total_uncovered": int,
      "per_hop": [
        { "hop": 1, "edge_count": N, "covered_count": K,
          "coverage_fraction": 0.x, "uncovered_edge_ids": [...] },
        ...
      ],
      "scanned_at": "ISO 8601"
    }
    """
    if not LOOK_AHEAD_AVAILABLE:
        raise HTTPException(status_code=503, detail="Look-ahead 모듈을 사용할 수 없습니다.")
    graph = _state.get("mock_graph")
    if not graph:
        raise HTTPException(status_code=400, detail="도로 그래프가 아직 로드되지 않았습니다. 시뮬레이션을 먼저 설정하세요.")
    road_nodes = graph.get("nodes", {})
    if node_id not in road_nodes:
        raise HTTPException(status_code=404, detail=f"노드 '{node_id}'를 도로 그래프에서 찾을 수 없습니다.")
    bs_nodes = _state.get("network_nodes") or []
    hops = max(1, min(hops, 10))
    result = look_ahead_bs_scan(
        current_node_id=node_id,
        graph=graph,
        road_nodes=road_nodes,
        bs_nodes=bs_nodes,
        lookahead_hops=hops,
    )
    return {"available": True, **result.to_dict()}


@app.post("/api/rl/episode")
def run_rl_episode(req: RLEpisodeRequest):
    """
    Run one or more RL episodes using a baseline policy (no training required).

    Supported policies: random | greedy | coverage
      random   : uniformly random valid action
      greedy   : lowest total_cost candidate edge
      coverage : lowest signal-loss candidate edge (best BS connection)

    Request body
    ------------
    origin_id         : start road node ID
    dest_id           : destination road node ID
    policy            : "random" | "greedy" | "coverage"  (default "greedy")
    max_steps         : episode length limit  (default 200)
    n_episodes        : number of episodes to run  (default 1)
    seed              : RNG seed for reproducibility  (optional)
    record_trajectory : include per-step logs in response  (default true)

    Response (n_episodes == 1)
    -------------------------
    Single EpisodeResult dict.

    Response (n_episodes > 1)
    -------------------------
    Aggregate statistics + episodes list.
    """
    if not RL_AVAILABLE:
        raise HTTPException(status_code=503, detail="RL 모듈을 사용할 수 없습니다.")
    graph = _state.get("mock_graph")
    if not graph:
        raise HTTPException(status_code=400, detail="도로 그래프가 아직 로드되지 않았습니다. 시뮬레이션을 먼저 설정하세요.")
    road_nodes = graph.get("nodes", {})
    bs_nodes = _state.get("network_nodes") or []
    if req.origin_id not in road_nodes:
        raise HTTPException(status_code=404, detail=f"출발 노드 '{req.origin_id}'를 찾을 수 없습니다.")
    if req.dest_id not in road_nodes:
        raise HTTPException(status_code=404, detail=f"목적지 노드 '{req.dest_id}'를 찾을 수 없습니다.")
    if req.policy not in SUPPORTED_POLICIES:
        raise HTTPException(status_code=400,
                            detail=f"지원하지 않는 정책입니다. 선택 가능: {SUPPORTED_POLICIES}")
    try:
        env = V2XRoutingEnv(
            graph=graph,
            road_nodes=road_nodes,
            bs_nodes=bs_nodes,
            origin_id=req.origin_id,
            dest_id=req.dest_id,
            max_steps=req.max_steps,
            allocation_output=_state.get("last_allocation_result"),
        )
        if req.n_episodes == 1:
            result = run_episode(
                env,
                policy=req.policy,
                seed=req.seed,
                record_trajectory=req.record_trajectory,
            )
            return result.to_dict()
        else:
            return run_episodes(
                env,
                n_episodes=req.n_episodes,
                policy=req.policy,
                seed=req.seed,
                record_trajectory=req.record_trajectory,
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RL 에피소드 실행 오류: {exc}")


@app.get("/api/route/candidates")
def get_k_candidates():
    """
    Return Yen's K-shortest path candidates with full network cost breakdown.
    Populated automatically after each simulation start.
    The candidate with selected=true has the lowest total network cost.
    """
    result = _state.get("k_path_candidates")
    if not result:
        return {"available": False, "reason": "시뮬레이션을 먼저 시작하세요."}
    return result


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
        "antenna_height_m": row.get("antenna_height_m"),
        "antenna_placement": row.get("antenna_placement"),
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

    # 클릭 위치 근처 건물을 탐색해 설치 위치(좌표 이동 포함)와 안테나 높이 결정
    from app.services.buildings.bs_placement import resolve_placement
    placement = resolve_placement(req.lat, req.lng, BUILDING_REPOSITORY, search_radius_m=100.0)

    name = f"기지국 {max_user_station_number() + 1}"
    node = {
        "id": f"user-bs-{uuid4().hex[:10]}",
        "name": name,
        "node_type": req.node_type,
        "lat": placement.lat,
        "lng": placement.lng,
        "capacity": 100.0,
        "load": 0.0,
        "congestion_score": 0.0,
        "edge_latency_ms": 5.0,
        "coverage_radius_m": 500.0,
        "source": "user_created",
        "antenna_height_m": placement.antenna_height_m,
        "antenna_placement": placement.placement_type,
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


@app.post("/network-nodes/reapply-placement")
async def reapply_placement_to_existing_nodes():
    """기존 user_created 기지국 전체에 건물 탐색 재배치를 적용한다.

    생성 당시 건물 데이터가 없었거나 fix 이전에 만들어진 노드의
    좌표와 안테나 높이를 현재 로직으로 재계산한다.
    """
    from app.services.buildings.bs_placement import resolve_placement
    rows = fetch_network_nodes(source="user_created")
    updated, skipped = 0, 0
    results = []
    for row in rows:
        orig_lat = float(row["lat"])
        orig_lng = float(row["lng"])
        p = resolve_placement(orig_lat, orig_lng, BUILDING_REPOSITORY, search_radius_m=100.0)
        ok = update_network_node_placement(
            node_id=row["id"],
            lat=p.lat,
            lng=p.lng,
            antenna_height_m=p.antenna_height_m,
            antenna_placement=p.placement_type,
        )
        if ok:
            updated += 1
            results.append({
                "id": row["id"],
                "name": row.get("name"),
                "placement_type": p.placement_type,
                "antenna_height_m": p.antenna_height_m,
                "moved": (p.lat != orig_lat or p.lng != orig_lng),
                "lat": p.lat,
                "lng": p.lng,
            })
        else:
            skipped += 1
    _refresh_active_network_nodes()
    return {"ok": True, "updated": updated, "skipped": skipped, "nodes": results}


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


# ── Stage-2: Custom Policy Engine ────────────────────────────────────────────

class CustomPoliciesRequest(BaseModel):
    policies: dict  # {policy_key: policy_dict}


def _build_custom_latency_fn(policy: dict):
    """Return a LatencyInput→LatencyOutput function driven by a custom_bs_selection_policy."""
    _p = policy

    def _fn(inp):
        from app.services.latency.registry import LatencyOutput
        cap = max(inp.base_station.capacity, 1.0)
        cov_r = max(inp.base_station.coverage_radius_m, 1.0)
        features = {
            "distance":         min(inp.dist_m / cov_r, 1.0),
            "latency":          min(inp.base_station.edge_latency_ms / 20.0, 1.0),
            "load":             min(inp.base_station.load / cap, 1.0),
            "resource_deficit": min(
                getattr(inp.resource_state, "resource_deficit", 0.0), 1.0
            ),
            "future_risk": (
                1.0 if inp.dist_m >= cov_r
                else round(inp.dist_m / cov_r, 4)
            ),
        }
        score = run_custom_weighted_policy(_p, features)
        # Physics-based component decomposition (each component from its own feature)
        prop_ms  = round(4.0 + features["distance"] * 10.0, 4)   # free-space propagation: 4–14 ms
        queue_ms = round(features["load"] * 30.0, 4)              # queueing delay: 0–30 ms
        res_ms   = round(features["resource_deficit"] * 15.0, 4)  # resource contention: 0–15 ms
        tx_ms    = round(features["latency"] * 5.0, 4)            # transmission component: 0–5 ms
        mec_ms   = 2.0                                             # fixed MEC processing
        latency_ms = min(round(prop_ms + queue_ms + res_ms + tx_ms + mec_ms, 4), 150.0)
        return LatencyOutput(
            latency=latency_ms,
            propagation_delay=prop_ms,
            transmission_delay=tx_ms,
            queueing_delay=queue_ms,
            mec_processing_delay=mec_ms,
            handover_delay=0.0,
            blockage_delay=0.0,
            algorithm_id="custom_bs_selection",
            debug_info={
                "score": round(score, 4),
                "features": {k: round(v, 4) for k, v in features.items()},
            },
        )

    return _fn


def _build_custom_resource_fn(policy: dict):
    """Return an AllocationInput→AllocationOutput function driven by a custom_resource_policy."""
    _p = policy

    def _fn(inp):
        from app.services.resources.allocation_registry import (
            AllocationOutput, BSAllocation,
        )
        bs_allocs: list = []
        deficit_map: dict = {}
        load_after: dict = {}
        latency_impact: dict = {}

        for bs in inp.base_stations:
            bs_id = str(bs.get("id") or bs.get("name") or "")
            cap = max(float(bs.get("capacity") or inp.config.total_rb_per_bs), 1.0)
            load_ratio = float(bs.get("load", bs.get("current_load", 0.0)))

            demand_rb = load_ratio * cap
            if inp.resource_demand_map and bs_id in inp.resource_demand_map:
                dm = inp.resource_demand_map[bs_id]
                if isinstance(dm, dict):
                    demand_rb = float(dm.get("demand_rb", demand_rb))
                elif hasattr(dm, "demand_rb"):
                    demand_rb = float(dm.demand_rb)

            cov_r = max(float(bs.get("coverage_radius_m", 400.0)), 1.0)
            dist_m = float(bs.get("dist_m", cov_r * 0.5))
            features = {
                "demand":   min(demand_rb / cap, 1.0),
                "load":     min(load_ratio, 1.0),
                # demand urgency: how critically over-loaded this BS is relative to 80% target
                "priority": min(demand_rb / max(cap * 0.8, 1.0), 1.0),
                # normalized distance of vehicles served by this BS
                "distance": min(dist_m / cov_r, 1.0),
            }
            score = run_custom_weighted_policy(_p, features)
            util_target = max(0.0, 1.0 - min(score, 1.0))
            allocated_rb = cap * util_target
            deficit = max(0.0, demand_rb - allocated_rb)
            updated_load = min(allocated_rb / cap, 1.0)
            # 20ms per full RB deficit (realistic queueing overhead per resource unit)
            lat_delta = (deficit / cap) * 20.0

            bs_allocs.append(BSAllocation(
                bs_id=bs_id,
                total_capacity_rb=round(cap, 2),
                allocated_rb=round(allocated_rb, 2),
                utilization_ratio=round(util_target, 4),
                updated_load=round(updated_load, 4),
                demand_rb=round(demand_rb, 2),
                deficit_rb=round(deficit, 2),
            ))
            if deficit > 0:
                deficit_map[bs_id] = round(deficit, 2)
            load_after[bs_id] = round(updated_load, 4)
            latency_impact[bs_id] = round(lat_delta, 2)

        total_cap = sum(a.total_capacity_rb for a in bs_allocs)
        total_alloc = sum(a.allocated_rb for a in bs_allocs)
        avg_util = total_alloc / total_cap if total_cap > 0 else 0.0

        return AllocationOutput(
            algorithm_id="custom_resource_allocation",
            allocation_result={
                "total_capacity_rb": round(total_cap, 2),
                "total_allocated_rb": round(total_alloc, 2),
                "avg_utilization": round(avg_util, 4),
                "overloaded_bs_count": sum(1 for a in bs_allocs if a.deficit_rb > 0),
            },
            base_station_allocations=bs_allocs,
            vehicle_allocations=[],
            resource_deficit_by_bs=deficit_map,
            expected_latency_impact=latency_impact,
            bs_load_after_allocation=load_after,
            debug_info={"policy": "custom_resource_allocation", "bs_count": len(bs_allocs)},
        )

    return _fn


def _register_custom_cost_weights(policy: dict) -> dict:
    """Apply custom_cost_policy weights as scaled CostWeights and return debug info."""
    if not ROUTE_COST_AVAILABLE or not CUSTOM_POLICY_AVAILABLE:
        return {}
    global _route_cost_weights
    w = policy.get("weights", {})
    # User inputs fractions (e.g. 0.15+0.40+…=1.0); scale so their sum matches the
    # default CostWeights magnitude (12.5), preserving relative proportions exactly.
    _DEFAULTS_SUM = 12.5
    raw = {k: max(0.0, float(v)) for k, v in w.items()}
    scale = _DEFAULTS_SUM / max(sum(raw.values()), 1e-9)
    def _sw(key, default):
        return raw[key] * scale if key in raw else default
    _route_cost_weights = CostWeights(
        w_distance=      _sw("distance",    1.0),
        w_time=          _sw("time",        2.0),
        w_latency=       _sw("latency",     3.0),
        w_load=          _sw("load",        1.5),
        w_handover=      _sw("handover",    1.0),
        w_blockage=      _sw("blockage",    1.5),
        w_coverage_risk= _sw("future_risk", 2.5),
        w_resource_deficit=_route_cost_weights.w_resource_deficit,
    )
    return {
        k: round(getattr(_route_cost_weights, k), 4)
        for k in (
            "w_distance", "w_time", "w_latency", "w_load",
            "w_handover", "w_blockage", "w_coverage_risk",
        )
    }


def _apply_custom_policy_set(policies: dict) -> None:
    """Validate, register, and persist a set of custom policies."""
    existing = dict(_state.get("custom_policies") or {})
    debug = dict(_state.get("custom_policy_debug") or {})

    for key, policy in policies.items():
        existing[key] = policy
        if key == "custom_cost_policy":
            applied_weights = _register_custom_cost_weights(policy)
            debug[key] = {"status": "applied", "applied_cost_weights": applied_weights}

        elif key == "custom_bs_selection_policy":
            if not LATENCY_AVAILABLE:
                debug[key] = {"status": "unavailable", "reason": "Latency service not loaded"}
            else:
                try:
                    fn = _build_custom_latency_fn(policy)
                    LATENCY_REGISTRY.register(
                        "custom_bs_selection", fn,
                        description="User-defined weighted-sum BS selection policy (Stage 2)",
                    )
                    LATENCY_REGISTRY.set_algorithm("custom_bs_selection")
                    _state["latency_algorithm"] = "custom_bs_selection"
                    debug[key] = {"status": "applied", "algorithm": "custom_bs_selection"}
                except Exception as exc:
                    debug[key] = {"status": "error", "error": str(exc)}

        elif key == "custom_resource_policy":
            if not RESOURCE_DEMAND_AVAILABLE:
                debug[key] = {"status": "unavailable", "reason": "Resource demand service not loaded"}
            else:
                try:
                    fn = _build_custom_resource_fn(policy)
                    ALLOCATION_REGISTRY.register(
                        "custom_resource_allocation", fn,
                        description="User-defined weighted-sum resource allocation policy (Stage 2)",
                    )
                    ALLOCATION_REGISTRY.set_algorithm("custom_resource_allocation")
                    _state["allocation_algorithm"] = "custom_resource_allocation"
                    debug[key] = {"status": "applied", "algorithm": "custom_resource_allocation"}
                except Exception as exc:
                    debug[key] = {"status": "error", "error": str(exc)}

    _state["custom_policies"] = existing
    _state["custom_policy_debug"] = debug


def _revert_custom_policy(policy_key: str) -> None:
    """Revert a removed policy to the Stage-1 / default algorithm."""
    defaults = SimConfigAlgorithmSelection()
    if policy_key == "custom_bs_selection_policy" and LATENCY_AVAILABLE:
        try:
            LATENCY_REGISTRY.set_algorithm(defaults.latency_algorithm)
            _state["latency_algorithm"] = defaults.latency_algorithm
        except Exception:
            pass
    elif policy_key == "custom_resource_policy" and RESOURCE_DEMAND_AVAILABLE:
        try:
            ALLOCATION_REGISTRY.set_algorithm(defaults.resource_allocation_algorithm)
            _state["allocation_algorithm"] = defaults.resource_allocation_algorithm
        except Exception:
            pass


@app.post("/api/simulation/custom-policy")
def set_custom_policies(req: CustomPoliciesRequest):
    """Validate, register, and persist custom scoring policies (Stage 2)."""
    if not CUSTOM_POLICY_AVAILABLE:
        raise HTTPException(status_code=503, detail="Custom policy engine 사용 불가")

    validation_errors: dict[str, list[str]] = {}
    valid_policies: dict[str, dict] = {}

    for key, raw in req.policies.items():
        if key not in _CUSTOM_POLICY_KEYS:
            validation_errors[key] = [f"알 수 없는 policy key: '{key}'"]
            continue
        try:
            policy = parse_custom_policy(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError) as exc:
            validation_errors[key] = [str(exc)]
            continue
        ok, errs = validate_custom_policy(key, policy)
        if not ok:
            validation_errors[key] = errs
        else:
            valid_policies[key] = policy

    if valid_policies:
        _apply_custom_policy_set(valid_policies)

    return {
        "ok": bool(valid_policies),
        "applied": list(valid_policies.keys()),
        "errors": validation_errors,
        "active_custom_policies": {
            k: {"type": v.get("type"), "weights": v.get("weights")}
            for k, v in (_state.get("custom_policies") or {}).items()
        },
        "debug": _state.get("custom_policy_debug") or {},
    }


@app.get("/api/simulation/custom-policy")
def get_custom_policies():
    """Return active custom policies and available feature sets (Stage 2)."""
    active = _state.get("custom_policies") or {}
    return {
        "active_custom_policies": {
            k: {"type": v.get("type"), "weights": v.get("weights"),
                "constraints": v.get("constraints", {})}
            for k, v in active.items()
        },
        "custom_policy_debug": _state.get("custom_policy_debug") or {},
        "available_features": {
            "custom_cost_policy":         sorted(_CUSTOM_COST_FEATURES),
            "custom_bs_selection_policy": sorted(_CUSTOM_BS_FEATURES),
            "custom_resource_policy":     sorted(_CUSTOM_RESOURCE_FEATURES),
        } if CUSTOM_POLICY_AVAILABLE else {},
    }


@app.delete("/api/simulation/custom-policy/{policy_key}")
def remove_custom_policy(policy_key: str):
    """Remove a single custom policy and revert to its default algorithm (Stage 2)."""
    if CUSTOM_POLICY_AVAILABLE and policy_key not in _CUSTOM_POLICY_KEYS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 policy key: '{policy_key}'")
    policies = dict(_state.get("custom_policies") or {})
    removed = policy_key in policies
    policies.pop(policy_key, None)
    debug = dict(_state.get("custom_policy_debug") or {})
    debug.pop(policy_key, None)
    _state["custom_policies"] = policies
    _state["custom_policy_debug"] = debug
    if removed:
        _revert_custom_policy(policy_key)
    return {"ok": True, "removed": removed, "remaining": list(policies.keys())}


class LLMAnalysisRequest(BaseModel):
    sim_elapsed: float = 0
    vehicle_pos: Optional[dict] = None
    edge_history: list = []
    edge_avg_speeds: dict = {}
    route_edge_names: dict = {}
    sim_logs: list = []
    algorithm: Optional[str] = None
    handover_count: int = 0
    latency_ms: Optional[float] = None
    connected_node: Optional[str] = None
    provider: Optional[str] = None   # "vertex" | "azure" | "bedrock" | None = auto


@app.get("/api/analysis/llm/providers")
def get_llm_providers():
    """List all configured LLM providers and which one is currently active."""
    try:
        from app.services.llm.client import list_providers
        return {"providers": list_providers()}
    except Exception as exc:
        return {"providers": [], "error": str(exc)}


@app.post("/api/analysis/llm")
def run_llm_analysis(req: LLMAnalysisRequest):
    from app.services.llm.client import generate as llm_generate

    elapsed_min = req.sim_elapsed / 60 if req.sim_elapsed > 0 else 0
    arrived = req.vehicle_pos.get("arrived", False) if req.vehicle_pos else False
    progress_pct = (req.vehicle_pos.get("progress", 0) * 100) if req.vehicle_pos else 0
    current_speed = req.vehicle_pos.get("speed", 0) if req.vehicle_pos else 0

    speeds = list(req.edge_avg_speeds.values())
    avg_speed = round(sum(speeds) / len(speeds), 1) if speeds else 0
    max_speed = round(max(speeds), 1) if speeds else 0
    min_speed = round(min(speeds), 1) if speeds else 0

    sorted_edges = sorted(req.edge_avg_speeds.items(), key=lambda x: x[1])
    slowest = [(req.route_edge_names.get(e, e), round(v, 1)) for e, v in sorted_edges[:3]] if sorted_edges else []
    fastest = [(req.route_edge_names.get(e, e), round(v, 1)) for e, v in sorted_edges[-3:]] if sorted_edges else []

    log_kinds: dict = {}
    for lg in req.sim_logs:
        kind = lg.get("kind", "info")
        log_kinds[kind] = log_kinds.get(kind, 0) + 1

    warn_logs = [lg.get("ko", "") for lg in req.sim_logs if lg.get("kind") in ("warn", "risk")]
    handover_freq = round(req.handover_count / elapsed_min, 2) if elapsed_min > 0 else 0
    lat = req.latency_ms
    lat_quality = (
        "우수 (20ms 미만)" if lat is not None and lat < 20
        else "양호 (50ms 미만)" if lat is not None and lat < 50
        else "보통 (100ms 미만)" if lat is not None and lat < 100
        else "불량 (100ms 이상)" if lat is not None
        else "측정값 없음"
    )

    slowest_str = ", ".join(f"{n}: {v}km/h" for n, v in slowest) or "데이터 없음"
    fastest_str = ", ".join(f"{n}: {v}km/h" for n, v in fastest) or "데이터 없음"
    warn_str = "; ".join(warn_logs[:5]) if warn_logs else "없음"

    prompt = f"""당신은 V2X(Vehicle-to-Everything) 자율주행 네트워크 시뮬레이션 전문 분석가입니다.
아래 시뮬레이션 실측 데이터를 바탕으로 8개 항목의 정밀 분석 리포트를 작성하세요.

=== 시뮬레이션 실측 데이터 ===
- 경과 시간: {elapsed_min:.1f}분 ({req.sim_elapsed:.0f}초)
- 완료 여부: {"완료 (도착)" if arrived else f"진행 중 ({progress_pct:.1f}% 완료)"}
- 현재 속도: {current_speed:.1f} km/h
- 라우팅 알고리즘: {req.algorithm or "기본(다익스트라)"}
- 연결 기지국: {req.connected_node or "미연결"}
- 현재 지연 시간: {f"{lat:.1f}ms — {lat_quality}" if lat is not None else "측정값 없음"}
- 핸드오버 횟수: {req.handover_count}회 (분당 {handover_freq}회)
- 통과 완료 엣지 수: {len(req.edge_history)}개
- 이벤트 로그 수: {len(req.sim_logs)}건 (종류별: {json.dumps(log_kinds, ensure_ascii=False)})

속도 실측 통계 (km/h):
- 평균 {avg_speed} / 최고 {max_speed} / 최저 {min_speed}
- 가장 느린 구간: {slowest_str}
- 가장 빠른 구간: {fastest_str}

경고·위험 이벤트: {warn_str}

=== 분석 요구사항 ===
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 출력하지 마세요.
각 section은 한 문단(2~4문장)으로, 실측 수치를 반드시 포함해 구체적으로 작성하세요.

{{
  "sections": [
    "[01 시뮬레이션 개요] 경로 완료 여부, 총 소요 시간, 통과 구간 수, 전체 수행 평가를 요약하세요.",
    "[02 경로·속도 성능] 평균·최고·최저 속도 분석, 실측 기반 이동 효율을 평가하세요.",
    "[03 혼잡 구간 분석] 속도 저하 최하위 구간을 구체적으로 짚고 혼잡 원인을 진단하세요.",
    "[04 핸드오버 품질] 핸드오버 횟수·빈도 평가, V2X 연결 안정성 수준을 판단하세요.",
    "[05 지연 시간 분석] latency 수치 기반 실시간 통신 품질, 자율주행 적합성을 평가하세요.",
    "[06 알고리즘 성능] 사용된 라우팅 알고리즘의 효율·최적화 수준을 평가하세요.",
    "[07 위험 요소·이슈] 경고·위험 이벤트 내용을 구체적으로 진단하고 영향을 서술하세요.",
    "[08 개선 권고사항] 데이터 근거 기반으로 실행 가능한 최적화 방안 3가지를 제시하세요."
  ]
}}"""

    try:
        text, provider_used = llm_generate(prompt, provider=req.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        raise HTTPException(status_code=502, detail=f"LLM API 오류 ({req.provider or 'auto'}): {detail[:400]}")

    # Parse the JSON sections the model was asked to return
    sections: list = []
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            sections = parsed.get("sections", [])
    except (json.JSONDecodeError, ValueError):
        pass

    if not sections:
        sections = [line.strip() for line in text.splitlines() if line.strip()]

    return {"sections": sections, "provider": provider_used}


class BatchAnalysisRequest(BaseModel):
    """
    배치 비교 AI 분석(Phase 6) 요청. 배치 결과는 프런트(localStorage)에 저장돼 있고 백엔드의
    _batch_runs는 서버 재시작 시 사라지므로, batch_id로 재조회하지 않고 프런트가 가진 results
    배열을 그대로 받는다 — GET /api/scenarios/batch/{id}가 반환하는 모양과 동일.
    """
    label: Optional[str] = None
    results: list[dict]
    provider: Optional[str] = None


@app.post("/api/analysis/llm/batch-compare")
def run_batch_comparison_analysis(req: BatchAnalysisRequest):
    """
    여러 시나리오(시트)의 배치 결과를 한 번에 LLM에 보내 비교 분석을 생성한다.
    기존 /api/analysis/llm은 단일 실행을 깊게 분석하는 용도라 그대로 두고,
    이건 "어떤 설정이 어떤 지표에서 우수했는지" 비교 서술이 목적인 별도 엔드포인트다.
    """
    from app.services.llm.client import generate as llm_generate

    if not req.results:
        raise HTTPException(status_code=400, detail="results가 비어 있습니다.")

    lines = []
    for r in req.results:
        label = r.get("label") or r.get("id") or "이름없음"
        if r.get("status") != "done":
            lines.append(f"- {label}: 실패 ({r.get('error', '알 수 없는 오류')})")
            continue
        if r.get("mode") == "rl_episode":
            reward = r.get("total_reward", r.get("mean_reward"))
            steps = r.get("steps", r.get("mean_steps"))
            arrived = r.get("arrived", r.get("arrival_rate"))
            lines.append(
                f"- {label} (RL 정책 {r.get('policy', '')}): reward={reward}, steps={steps}, "
                f"도착={arrived}, seed={r.get('seed')}"
            )
        else:
            rc = r.get("route_cost_result") or {}
            lines.append(
                f"- {label} (경로평가): 총비용={rc.get('total_cost')}, "
                f"평균지연={rc.get('avg_latency_ms')}ms, 핸드오버={rc.get('handover_count')}회, "
                f"커버리지={rc.get('covered_pct')}%, 차량수={r.get('vehicle_count')}, seed={r.get('seed')}"
            )

    prompt = f"""당신은 V2X 네트워크 시뮬레이션 비교 분석가입니다.
아래는 같은 배치("{req.label or '제목 없음'}") 안에서 서로 다른 설정으로 실행한
시나리오(시트)들의 결과입니다. 시나리오 수: {len(req.results)}개

=== 시나리오별 결과 ===
{chr(10).join(lines)}

=== 분석 요구사항 ===
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 출력하지 마세요.
각 section은 한 문단(2~4문장)으로, 위 실측 수치를 반드시 인용해 구체적으로 작성하세요.

{{
  "sections": [
    "[01 종합 비교] 전체적으로 가장 우수한 시나리오와 그 이유를 요약하세요.",
    "[02 지연시간·비용 비교] 시나리오 간 차이와 추정 원인(차량 수, 알고리즘 설정 등)을 분석하세요.",
    "[03 자원·연결 안정성 비교] 핸드오버·커버리지·자원할당 측면의 트레이드오프를 짚으세요.",
    "[04 권장 설정] 어떤 상황(혼잡/한적, 연구 목적 등)에 어떤 시나리오 설정을 추천할지 제시하세요."
  ]
}}"""

    try:
        text, provider_used = llm_generate(prompt, provider=req.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM API 오류 ({req.provider or 'auto'}): {str(exc)[:400]}")

    sections: list = []
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            sections = json.loads(text[start:end]).get("sections", [])
    except (json.JSONDecodeError, ValueError):
        pass

    if not sections:
        sections = [line.strip() for line in text.splitlines() if line.strip()]

    return {"sections": sections, "provider": provider_used}


# 경로/기지국선택 알고리즘은 LATENCY_REGISTRY/ALLOCATION_REGISTRY 같은 description
# 레지스트리가 없어 여기서 직접 한 줄 설명을 작성한다 — route_cost_function.py의
# BS 알고리즘 docstring, main.py의 cost_weights/policy_options 필드 의미를 근거로 함.
_ROUTE_ALGO_DESC = {
    "dijkstra": "거리 기준 최단경로 (기본값)",
    "astar": "목적지 방향 휴리스틱을 더한 최단경로 탐색 — dijkstra와 같은 거리 축을 쓰지만 더 빠르게 같은/비슷한 경로를 찾음",
    "k_shortest_path": "위상적으로 짧은 후보 경로 K개를 뽑은 뒤, 그중 네트워크 비용(지연·부하·핸드오버 등)이 가장 낮은 것을 선택",
    "network_aware": "거리뿐 아니라 지연시간·기지국 부하·핸드오버 비용까지 반영해 그래프 전체를 다시 탐색",
    "lookahead": "출발 지점에서 몇 홉 앞의 기지국 커버리지 공백을 미리 예측해, 커버리지가 끊길 위험이 큰 구간을 피해가도록 가중치를 줌",
    "rl_routing": "강화학습 기반 경로 선택 — 아직 학습된 에이전트가 없어 미구현 상태, 선택해도 baseline dijkstra로 동작함",
}
_BS_SELECTION_DESC = {
    "nearest_bs": "단순 직선거리(Haversine)가 가장 가까운 기지국 선택",
    "lowest_latency_bs": "전파 손실 + 혼잡도 + 구간 지연을 합쳐 예상 지연시간이 가장 낮은 기지국 선택 (기본값)",
    "strongest_signal_bs": "거리의 제곱에 반비례하는 자유공간 경로손실 근사값으로 신호가 가장 강한 기지국 선택",
    "load_balanced_bs": "부하율(load/capacity)이 가장 낮은 기지국을 우선하고, 거리는 동률일 때만 보조 기준으로 사용",
    "look_ahead_bs_selection": "lowest_latency_bs와 동일하지만, 곧 커버리지를 벗어날 기지국에는 강한 페널티를 추가",
    "rl_based_bs_selection": "강화학습 기반 기지국 선택 — 아직 학습된 에이전트가 없어 미구현 상태, 선택해도 lowest_latency_bs로 동작함",
}
_COST_WEIGHT_DESC = {
    "w_distance": "경로 길이(거리)에 대한 가중치 — 클수록 더 짧은 경로를 선호",
    "w_time": "주행 예상 시간에 대한 가중치 — 클수록 더 빠른 경로를 선호",
    "w_latency": "통신 지연시간에 대한 가중치 — 클수록 지연이 낮은 경로/기지국을 선호",
    "w_load": "기지국 부하율에 대한 가중치 — 클수록 혼잡한 기지국 인근 경로를 피함",
    "w_resource": "자원(무선 자원블록) 부족 비용에 대한 가중치",
    "w_handover": "핸드오버(기지국 전환) 횟수에 대한 가중치 — 클수록 전환이 적은 경로를 선호",
    "w_blockage": "건물에 의한 신호 차폐 손실에 대한 가중치",
    "w_future": "미래 커버리지 리스크(앞으로 커버리지가 끊길 가능성)에 대한 가중치",
}
_POLICY_OPTION_DESC = {
    "lookahead_k": "lookahead 경로 알고리즘이 몇 홉 앞까지 미리 내다볼지 (정수 1~10)",
    "lookahead_time": "lookahead가 내다보는 시간 범위(초)",
    "max_handover_allowed": "시뮬레이션 중 허용하는 최대 핸드오버 횟수",
    "prefer_low_latency": "true면 지연시간이 낮은 선택을 더 강하게 선호",
    "prefer_load_balance": "true면 기지국 간 부하 분산을 더 강하게 선호",
    "avoid_disconnection": "true면 커버리지 단절 구간을 적극적으로 회피",
    "traffic_lambda": "배경 차량/트래픽 강도를 나타내는 파라미터 (0~200)",
    "network_mode": "통신 세대 — \"4G\" | \"5G\" | \"6G\"",
}


def _scenario_schema_doc() -> str:
    """시나리오 어시스턴트(파싱/챗봇)가 LLM에 넘기는 설정 스키마 + 알고리즘 설명 문서.

    LATENCY_REGISTRY/ALLOCATION_REGISTRY에 등록된 실제 description을 우선 사용해
    LLM이 실제 백엔드 동작과 어긋나지 않는 설명을 하도록 그라운딩한다.
    """
    lat_lines = []
    if LATENCY_AVAILABLE:
        for meta in LATENCY_REGISTRY.list_algorithms():
            lat_lines.append(f"    {meta['id']}: {meta.get('description', '')}")
    alloc_lines = []
    if RESOURCE_DEMAND_AVAILABLE:
        for meta in ALLOCATION_REGISTRY.list_algorithms():
            alloc_lines.append(f"    {meta['id']}: {meta.get('description', '')}")
    route_lines = [f"    {k}: {v}" for k, v in _ROUTE_ALGO_DESC.items()]
    bs_lines = [f"    {k}: {v}" for k, v in _BS_SELECTION_DESC.items()]
    weight_lines = [f"    {k}: {v}" for k, v in _COST_WEIGHT_DESC.items()]
    policy_lines = [f"    {k}: {v}" for k, v in _POLICY_OPTION_DESC.items()]

    return f"""
스키마 (이 키들만 사용, 다른 키는 절대 만들지 마세요):

cost_weights (모든 값은 0 이상 숫자, 보통 0~20 범위) — 각 필드 의미:
{chr(10).join(weight_lines)}

algorithm_selection (각 키는 아래 후보 중 정확히 하나의 문자열) — 각 옵션 의미:
  route_algorithm 후보:
{chr(10).join(route_lines)}
  latency_algorithm 후보:
{chr(10).join(lat_lines) if lat_lines else "    (레지스트리 미사용 — full_composite_latency 등 기본 후보만 가능)"}
  base_station_selection_algorithm 후보:
{chr(10).join(bs_lines)}
  resource_allocation_algorithm 후보:
{chr(10).join(alloc_lines) if alloc_lines else "    (레지스트리 미사용 — traffic_aware_allocation 등 기본 후보만 가능)"}

policy_options — 각 필드 의미:
{chr(10).join(policy_lines)}
"""


class ScenarioParseRequest(BaseModel):
    input_text: str
    input_type: str = "nl"          # "nl" | "code"
    current_config: dict = {}        # current simConfig for diff context
    provider: Optional[str] = None


@app.post("/api/scenarios/parse")
def parse_scenario(req: ScenarioParseRequest):
    """
    Parse a natural-language or JSON/code scenario description into a
    structured diff against the simulation config schema (cost_weights /
    algorithm_selection / policy_options). Does not apply anything —
    the frontend validates and applies the returned diff itself.
    """
    from app.services.llm.client import generate as llm_generate

    schema_doc = _scenario_schema_doc()

    input_label = "자연어 시나리오 설명" if req.input_type == "nl" else "코드/JSON 형태의 설정 조각"
    prompt = f"""당신은 V2X 네트워크 시뮬레이션의 설정 어시스턴트입니다.
사용자가 아래와 같은 {input_label}을 입력했습니다. 이를 시뮬레이션 설정 변경(diff)으로 변환하세요.

=== 현재 설정 ===
{json.dumps(req.current_config, ensure_ascii=False, indent=2)}

=== 설정 스키마 ===
{schema_doc}

=== 사용자 입력 ===
{req.input_text}

=== 작업 지침 ===
- 사용자 입력에서 실제로 바뀌어야 한다고 판단되는 키만 diff에 포함하세요. 바뀌지 않는 키는 절대 포함하지 마세요.
- 스키마에 없는 키를 만들지 마세요.
- 각 변경 키마다 한 줄짜리 변경 이유(rationale)를 작성하세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 출력하지 마세요.

{{
  "diff": {{
    "cost_weights": {{}},
    "algorithm_selection": {{}},
    "policy_options": {{}}
  }},
  "rationale": {{
    "<바뀐 키>": "한 줄 이유"
  }}
}}"""

    try:
        text, provider_used = llm_generate(prompt, provider=req.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        raise HTTPException(status_code=502, detail=f"LLM API 오류 ({req.provider or 'auto'}): {detail[:400]}")

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end]) if start >= 0 and end > start else {}
    except (json.JSONDecodeError, ValueError):
        parsed = {}

    diff = parsed.get("diff") or {}
    rationale = parsed.get("rationale") or {}
    if not isinstance(diff, dict):
        diff = {}

    return {"ok": bool(diff), "provider": provider_used, "diff": diff, "rationale": rationale}


class ScenarioChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ScenarioChatRequest(BaseModel):
    messages: list[ScenarioChatMessage]
    current_config: dict = {}
    provider: Optional[str] = None


@app.post("/api/scenarios/chat")
def chat_scenario(req: ScenarioChatRequest):
    """
    대화형 시나리오 어시스턴트 — 무상태(stateless) 엔드포인트.
    매 호출마다 프론트가 전체 대화 히스토리를 보낸다(백엔드에 아무것도 저장하지 않음).
    사용자가 옵션에 대해 질문하면 diff 없이 설명만, 변경을 요청하면 diff를 채워 반환한다.
    프론트가 diff를 검증해 적용 여부를 결정한다(파싱 결과를 그대로 믿고 적용하지 않음).
    """
    from app.services.llm.client import generate as llm_generate

    if not req.messages:
        raise HTTPException(status_code=400, detail="messages가 비어 있습니다.")

    schema_doc = _scenario_schema_doc()
    history_lines = []
    for m in req.messages[-10:]:
        speaker = "사용자" if m.role == "user" else "어시스턴트"
        history_lines.append(f"{speaker}: {m.content}")
    last_user_msg = next((m.content for m in reversed(req.messages) if m.role == "user"), "")

    prompt = f"""당신은 V2X 네트워크 시뮬레이션의 설정 어시스턴트 챗봇입니다.
사용자와 대화하며, (1) 시뮬레이션 옵션/필드가 무엇을 하는지 질문하면 설명해주고,
(2) 설정을 바꿔달라고 요청하면 실제로 바뀌어야 하는 값을 diff로 만들어 돌려줍니다.

=== 현재 설정 ===
{json.dumps(req.current_config, ensure_ascii=False, indent=2)}

=== 설정 스키마 및 각 옵션 설명 ===

{schema_doc}

=== 대화 기록 (최근 순) ===
{chr(10).join(history_lines)}

=== 작업 지침 ===
- 마지막 사용자 메시지("{last_user_msg}")가 질문이면: reply에 친절하고 정확한 한국어
  설명만 작성하고, diff는 비워두세요(섹션 모두 빈 객체).
- 마지막 사용자 메시지가 설정 변경 요청이면: reply에 무엇을 어떻게 바꿨는지 한두 문장으로
  확인하고, diff에 실제로 바뀌어야 하는 키만 포함하세요. 스키마에 없는 키는 절대 만들지
  마세요. 바뀌지 않는 키는 diff에 넣지 마세요.
- 변경 요청인지 질문인지 모호하면 설명을 우선하고 diff는 비워둔 채, 어떤 값으로
  바꾸고 싶은지 되물어보세요.
- 각 변경 키마다 한 줄짜리 변경 이유(rationale)를 작성하세요.
- 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 출력하지 마세요.

{{
  "reply": "사용자에게 보여줄 한국어 대화 응답",
  "diff": {{
    "cost_weights": {{}},
    "algorithm_selection": {{}},
    "policy_options": {{}}
  }},
  "rationale": {{
    "<바뀐 키>": "한 줄 이유"
  }}
}}"""

    try:
        text, provider_used = llm_generate(prompt, provider=req.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        raise HTTPException(status_code=502, detail=f"LLM API 오류 ({req.provider or 'auto'}): {detail[:400]}")

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        parsed = json.loads(text[start:end]) if start >= 0 and end > start else {}
    except (json.JSONDecodeError, ValueError):
        parsed = {}

    reply = parsed.get("reply") or "응답을 이해하지 못했어요. 다시 한 번 말씀해 주시겠어요?"
    diff = parsed.get("diff") or {}
    rationale = parsed.get("rationale") or {}
    if not isinstance(diff, dict):
        diff = {}

    return {"ok": True, "provider": provider_used, "reply": reply, "diff": diff, "rationale": rationale}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket — sends vehicle position at ~10 fps."""
    await ws.accept()
    _ws_clients.append(ws)
    try:
        last_pos = None
        last_route = None
        last_telemetry = None
        last_cost_version = 0
        last_connected: bool | None = None  # None=unknown, True=connected, False=disconnected
        bg_tick = 0  # 배경 차량 위치는 매 틱(100ms)이 아니라 더 느리게 보냄 (대충 보여주는 용도)
        while True:
            pos = _state.get("vehicle_pos")
            err = _state.get("error")
            route = _state.get("route_coords")
            telemetry = _state.get("network_telemetry")
            cost_version = _state.get("route_cost_version", 0)

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

            # 다중차량 실험군 — 배경 차량 위치는 매 틱이 아니라 4틱(~400ms)마다 한번씩만 전송
            bg_tick += 1
            bg_veh = _state.get("background_vehicles")
            if bg_veh and bg_tick % 4 == 0:
                await ws.send_json({
                    "type": "background_positions",
                    "vehicles": [
                        {"id": v["id"], "lat": v["lat"], "lng": v["lng"], "speed": v.get("speed", v.get("speed_kmh", 0))}
                        for v in bg_veh
                    ],
                })

            # Disconnection detection: emit once when BS coverage is lost mid-simulation
            sim_running_now = _state.get("sim_running", False)
            if pos and sim_running_now:
                currently_connected = telemetry is not None
                if last_connected is True and not currently_connected:
                    await ws.send_json({"type": "disconnected"})
                last_connected = currently_connected
            elif not sim_running_now:
                last_connected = None

            if cost_version != last_cost_version:
                cost_result = _state.get("route_cost_result")
                if cost_result:
                    per_edge = [
                        {
                            "edge_id":        e["edge_id"],
                            "distance_m":     e.get("distance_m", 0),
                            "latency_ms":     e.get("latency_ms", 0),
                            "load_ratio":     e.get("load_ratio", 0),
                            "within_coverage": e.get("within_coverage", False),
                            "best_node_name": e.get("best_node_name"),
                            "total_cost":     e.get("total_cost", 0),
                        }
                        for e in cost_result.get("per_edge", [])
                    ]
                    await ws.send_json({
                        "type": "route_cost",
                        "per_edge": per_edge,
                        "edge_names": _state.get("route_edge_names", {}),
                        "routing_mode": cost_result.get("routing_mode", ""),
                        "avg_latency_ms": cost_result.get("avg_latency_ms", 0),
                        "total_cost": cost_result.get("total_cost", 0),
                        "total_distance_m": cost_result.get("total_distance_m", 0),
                        "coverage_risk": cost_result.get("coverage_risk", 0),
                        "handover_count": cost_result.get("handover_count", 0),
                    })
                last_cost_version = cost_version

            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
