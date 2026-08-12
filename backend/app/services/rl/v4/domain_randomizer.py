"""
Domain Randomizer — Universal Policy Task Sampler for Korean V2X.

Generates training tasks by randomly sampling:
  1. A Korean administrative region (from 2,367 regions in regions.db)
  2. Road network graph for that region (cached from OSM PBF)
  3. Base station (BS) positions at high-degree road intersections
  4. RSU positions at road segments with high traffic density
  5. Origin / destination node pair (guaranteed reachable by BFS)
  6. Traffic density parameter: vehicles/km²

Domain randomization (OpenAI Dactyl, Tobin et al. 2017):
  - Each training episode draws a fresh task from this distribution.
  - The policy must generalize across all tasks → Universal Policy.
  - 500 train regions / 50 holdout regions (disjoint).

References:
  [1] Tobin, J. et al., "Domain Randomization for Transferring Deep Neural
      Networks from Simulation to the Real World", IROS 2017.
  [2] Finn, C. et al., "Model-Agnostic Meta-Learning for Fast Adaptation",
      ICML 2017. (each task here corresponds to a MAML task τ_i)
  [3] Fernandez, H. et al., "Path Loss Characterization for Vehicular
      Communications at 700 MHz and 5.9 GHz", IEEE WCL 3(6), 2014.
"""
from __future__ import annotations

import math
import os
import pickle
import random
import sqlite3
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent.parent.parent.parent  # backend/
_DB_PATH = _BASE / "data" / "regions.db"
_PBF_PATH = _BASE.parent / "data" / "raw" / "south-korea-260711.osm.pbf"
_GRAPH_CACHE_DIR = _BASE / "data" / "v4_graph_cache"

# ── OSM highway types kept for V2X routing ────────────────────────────────────
_HIGHWAY_TYPES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "residential", "living_street",
    "unclassified", "service",
}

# ── Task geometry limits ───────────────────────────────────────────────────────
_MIN_NODES = 30           # reject regions with fewer road nodes
_MAX_NODES = 5000         # Stage 3 graph size; curriculum caps below this
_N_BS_RANGE = (1, 30)     # covers rural macro (1 BS) to dense urban district (30 BSes)
_N_RSU_RANGE = (0, 15)    # smart-city RSU-dense intersections
_DENSITY_RANGE = (0.5, 300.0)  # veh/km²: rural (0.5) to peak Seoul CBD rush hour (300)
_SPEED_RANGE = (10.0, 130.0)   # km/h: near-stationary congestion to expressway
# Coverage radius varies with cell size: macro (large radius) → small cell (small radius)
# Each BS picks a random radius within its mode range at task-sample time.
_BS_COV_RADIUS_RANGE = {"4G": (300, 800), "5G": (150, 500), "6G": (80, 300)}
_RSU_COV_RADIUS_RANGE = {"4G": (80, 150), "5G": (100, 200), "6G": (150, 350)}
_BS_CAPACITY = 200.0      # maximum simultaneous UEs per BS (upgraded for 5G capacity)
# HetNet: probability that a task uses mixed 4G/5G/6G BSes (reflects Korea's real network)
_HETNET_PROB = 0.45        # 45% HetNet, 55% single-mode

# ── Train / holdout split ─────────────────────────────────────────────────────
# Use all 2,367 Korean admin regions: 2000 train / holdout remainder
_N_TRAIN_REGIONS  = 2000
_N_HOLDOUT_REGIONS = None  # None = use remainder after train split

# ── Path loss model parameters per network mode (Fernandez 2014, Table I–II) ──
# n     = path-loss exponent (LOS urban)
# sigma = log-normal shadowing std-dev [dB]
# freq_ghz = carrier frequency
# d0    = reference distance [m]
# p_tx_dbm = UE transmit power (C-V2X, 3GPP TS 36.101)
PATH_LOSS_PARAMS: dict[str, dict] = {
    "4G": {
        "freq_ghz": 0.7,
        "n": 2.10,
        "sigma": 5.0,
        "d0": 10.0,
        "p_tx_dbm": 23.0,
    },
    "5G": {
        "freq_ghz": 3.5,
        "n": 2.75,
        "sigma": 5.5,
        "d0": 10.0,
        "p_tx_dbm": 23.0,
    },
    "6G": {
        "freq_ghz": 28.0,
        "n": 2.00,
        "sigma": 4.0,
        "d0": 10.0,
        "p_tx_dbm": 23.0,
    },
}
_P_NOISE_DBM = -95.0   # receiver noise floor at 10 MHz bandwidth


@dataclass
class V2XTask:
    """One fully specified training episode."""
    region_id: int
    region_name: str
    graph: dict                  # {"nodes": {id: {lat,lng}}, "adjacency": {id: [(id, dist)]}}
    bs_nodes: list[dict]         # [{id, lat, lng, coverage_radius_m, type, load, ...}]
    rsu_nodes: list[dict]
    origin_id: str               # road graph node id
    dest_id: str
    traffic_density: float       # vehicles/km²
    network_mode: str            # "4G" | "5G" | "6G"
    bbox: dict                   # {s, n, w, e}
    vehicle_speed_kmh: float = 60.0        # sampled from speed_range
    od_dist_m: float = 0.0                 # haversine distance origin→dest
    max_od_dist_m: float = float("inf")    # curriculum constraint used when sampling


class DomainRandomizer:
    """
    Task factory for Universal Policy training.

    Parameters
    ----------
    train : bool
        True → draw from the 500 train regions.
        False → draw from the 50 holdout regions (zero-shot test).
    seed : int | None
        Controls reproducibility of the region split (not per-episode).
    """

    def __init__(
        self,
        train: bool = True,
        seed: Optional[int] = 42,
        max_nodes: Optional[int] = None,
    ) -> None:
        self._train = train
        self._rng = random.Random(seed)
        self._max_nodes = max_nodes  # None = use global _MAX_NODES
        _GRAPH_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        all_regions = self._load_region_list()
        self._rng.shuffle(all_regions)
        # Use _N_TRAIN_REGIONS from the full 2,367-region pool.
        # Remaining regions become holdout — no hard ratio cap.
        split = min(_N_TRAIN_REGIONS, max(1, len(all_regions) - 50))
        self._train_regions = all_regions[:split]
        self._holdout_regions = all_regions[split:]

    # ── Public API ─────────────────────────────────────────────────────────────
    def sample_task(
        self,
        max_nodes: Optional[int] = None,
        max_od_dist_m: float = float("inf"),
        speed_range: tuple[float, float] = _SPEED_RANGE,
    ) -> Optional["V2XTask"]:
        """
        Sample one task (episode specification).

        Parameters
        ----------
        max_nodes : int | None
            Override graph node cap for curriculum learning.
            None → use the DomainRandomizer's own _max_nodes or _MAX_NODES.
        max_od_dist_m : float
            Maximum straight-line O/D distance. Used by curriculum stage 0
            to keep episodes short (e.g. 500 m). Default = no limit.
        speed_range : (float, float)
            Vehicle speed uniform sample range in km/h. Curriculum may
            narrow this (e.g. stage 0: (30, 60)).

        Returns None only if no valid graph can be constructed after retries.
        """
        pool = self._train_regions if self._train else self._holdout_regions
        if not pool:
            return None

        cap = max_nodes or self._max_nodes or _MAX_NODES
        for _ in range(20):   # retry limit
            region = self._rng.choice(pool)
            graph = self._load_or_build_graph(region)
            if graph is None or len(graph["nodes"]) < _MIN_NODES:
                continue
            # Sample a candidate O/D pair first so BFS trims around the actual route area
            node_ids = list(graph["nodes"].keys())
            _anchor = self._rng.choice(node_ids) if node_ids else None
            graph = self._trim_graph(graph, cap, anchor_node=_anchor)
            if len(graph["nodes"]) < _MIN_NODES:
                continue
            task = self._make_task(
                region, graph,
                max_od_dist_m=max_od_dist_m,
                speed_range=speed_range,
            )
            if task is not None:
                return task
        return None

    def _trim_graph(
        self,
        graph: dict,
        max_nodes: int,
        anchor_node: Optional[str] = None,
    ) -> dict:
        """
        BFS-trim graph to at most max_nodes nodes.

        Seeds BFS from `anchor_node` when provided (e.g. O/D midpoint node),
        ensuring the trimmed subgraph contains the actual route area.
        Falls back to a random node when anchor_node is absent or disconnected.
        """
        nodes = graph["nodes"]
        if len(nodes) <= max_nodes:
            return graph
        adj = graph["adjacency"]

        # Pick the best available BFS seed
        node_ids = list(nodes.keys())
        if anchor_node and anchor_node in nodes:
            seed = anchor_node
        else:
            seed = self._rng.choice(node_ids)

        visited, queue = {seed}, deque([seed])
        while queue and len(visited) < max_nodes:
            u = queue.popleft()
            for v, _ in adj.get(u, []):
                if v not in visited:
                    visited.add(v)
                    queue.append(v)
                    if len(visited) >= max_nodes:
                        break

        trimmed_nodes = {k: v for k, v in nodes.items() if k in visited}
        trimmed_adj = {
            k: [(nb, d) for nb, d in vs if nb in visited]
            for k, vs in adj.items() if k in visited
        }
        return {
            "nodes": trimmed_nodes,
            "adjacency": trimmed_adj,
            "way_tags": {
                k: v for k, v in graph.get("way_tags", {}).items()
                if k[0] in visited and k[1] in visited
            },
        }

    def pre_cache_all(self, verbose: bool = True) -> None:
        """
        One-time pre-computation: extract OSM graphs for all regions and
        save to disk.  Should be run once before training starts on A100.
        """
        regions = self._train_regions + self._holdout_regions
        for i, region in enumerate(regions):
            cache_path = _GRAPH_CACHE_DIR / f"{region['osm_id']}.pkl"
            if cache_path.exists():
                if verbose:
                    print(f"  [cache] {i+1}/{len(regions)} {region['name_ko']} — exists")
                continue
            g = self._build_graph_from_pbf(region)
            if g and len(g["nodes"]) >= _MIN_NODES:
                try:
                    with open(cache_path, "wb") as f:
                        pickle.dump(g, f, protocol=4)
                    if verbose:
                        print(f"  [cache] {i+1}/{len(regions)} {region['name_ko']} — {len(g['nodes'])} nodes saved")
                except Exception:
                    pass
            else:
                if verbose:
                    print(f"  [cache] {i+1}/{len(regions)} {region['name_ko']} — skipped (too small)")

    # ── Internal: region list ──────────────────────────────────────────────────
    def _load_region_list(self) -> list[dict]:
        if not _DB_PATH.exists():
            return []
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT osm_id, name_ko, admin_level,
                   min_lat, max_lat, min_lon, max_lon
            FROM regions
            WHERE admin_level IN (7, 8)
              AND min_lat IS NOT NULL
              AND (max_lat - min_lat) * 111 * (max_lon - min_lon) * 111 BETWEEN 0.1 AND 150
            ORDER BY osm_id
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Internal: graph building ───────────────────────────────────────────────
    def _load_or_build_graph(self, region: dict) -> Optional[dict]:
        cache_path = _GRAPH_CACHE_DIR / f"{region['osm_id']}.pkl"
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    return pickle.load(f)
            except Exception:
                cache_path.unlink(missing_ok=True)

        g = self._build_graph_from_pbf(region)
        if g and len(g["nodes"]) >= _MIN_NODES:
            try:
                with open(cache_path, "wb") as f:
                    pickle.dump(g, f, protocol=4)
            except Exception:
                pass
        return g

    def _build_graph_from_pbf(self, region: dict) -> Optional[dict]:
        """Extract road graph for region using osmium + temp .osm file."""
        bbox = {
            "s": region["min_lat"] - 0.001,
            "n": region["max_lat"] + 0.001,
            "w": region["min_lon"] - 0.001,
            "e": region["max_lon"] + 0.001,
        }
        try:
            osm_xml = self._extract_osm_xml(bbox)
            if osm_xml is None:
                return None
            return self._parse_osm_xml(osm_xml, max_nodes=_MAX_NODES)
        except Exception:
            return None

    def _extract_osm_xml(self, bbox: dict) -> Optional[str]:
        """Use osmium or osmconvert to extract a bbox from the PBF."""
        if not _PBF_PATH.exists():
            return None
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix=".osm", delete=False) as tf:
            tmp = tf.name
        try:
            box_str = f"{bbox['w']},{bbox['s']},{bbox['e']},{bbox['n']}"
            r = subprocess.run(
                ["osmium", "extract", "-b", box_str, str(_PBF_PATH), "-o", tmp, "--overwrite"],
                capture_output=True, timeout=60
            )
            if r.returncode != 0:
                return None
            return Path(tmp).read_text(encoding="utf-8")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _parse_osm_xml(self, xml_str: str, max_nodes: int = _MAX_NODES) -> Optional[dict]:
        root = ET.fromstring(xml_str)

        raw_nodes: dict[str, tuple[float, float]] = {}
        for node in root.findall("node"):
            nid = node.get("id")
            lat = node.get("lat")
            lon = node.get("lon")
            if nid and lat and lon:
                raw_nodes[nid] = (float(lat), float(lon))

        graph_nodes: dict[str, dict] = {}
        adjacency: dict[str, list[tuple[str, float]]] = {}
        way_tags: dict[tuple[str, str], dict] = {}

        for way in root.findall("way"):
            tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
            if tags.get("highway") not in _HIGHWAY_TYPES:
                continue
            refs = [nd.get("ref") for nd in way.findall("nd")]
            refs = [r for r in refs if r and r in raw_nodes]
            if len(refs) < 2:
                continue
            oneway = tags.get("oneway", "no") in ("yes", "1", "true")
            hw_rank = _hw_rank(tags.get("highway", ""))
            for ref in refs:
                lat, lng = raw_nodes[ref]
                graph_nodes[ref] = {"lat": lat, "lng": lng}
                adjacency.setdefault(ref, [])
            for a, b in zip(refs, refs[1:]):
                alat, alng = raw_nodes[a]
                blat, blng = raw_nodes[b]
                d = _haversine_m(alat, alng, blat, blng)
                adjacency[a].append((b, d))
                if not oneway:
                    adjacency[b].append((a, d))
                way_tags[(a, b)] = {"hw_rank": hw_rank, "dist_m": d}
                if not oneway:
                    way_tags[(b, a)] = {"hw_rank": hw_rank, "dist_m": d}

        if not graph_nodes:
            return None

        # Cap at max_nodes (keep highest-degree nodes)
        if len(graph_nodes) > max_nodes:
            by_degree = sorted(graph_nodes.keys(), key=lambda n: -len(adjacency.get(n, [])))
            keep = set(by_degree[:max_nodes])
            graph_nodes = {k: v for k, v in graph_nodes.items() if k in keep}
            adjacency = {k: [(b, d) for b, d in v if b in keep]
                         for k, v in adjacency.items() if k in keep}

        return {"nodes": graph_nodes, "adjacency": adjacency, "way_tags": way_tags}

    # ── Internal: task construction ────────────────────────────────────────────
    def _make_task(
        self,
        region: dict,
        graph: dict,
        max_od_dist_m: float = float("inf"),
        speed_range: tuple[float, float] = _SPEED_RANGE,
    ) -> Optional[V2XTask]:
        nodes = list(graph["nodes"].keys())
        adj = graph["adjacency"]

        degree = {n: len(adj.get(n, [])) for n in nodes}

        # BS nodes: prefer high-degree intersections (≥ 3 connections)
        intersections = [n for n in nodes if degree[n] >= 3]
        if not intersections:
            intersections = nodes
        n_bs = self._rng.randint(*_N_BS_RANGE)
        bs_picks = self._rng.sample(intersections, min(n_bs, len(intersections)))

        # HetNet: with _HETNET_PROB, each BS independently draws its own network_mode.
        # Otherwise all BSes share one mode (single-technology deployment).
        hetnet = self._rng.random() < _HETNET_PROB
        if hetnet:
            global_mode = "hetnet"
            # Build per-BS mode list with plausible distribution: 5G dominant, some 4G, rare 6G
            bs_mode_pool = ["4G"] * 2 + ["5G"] * 5 + ["6G"] * 1
            bs_modes = [self._rng.choice(bs_mode_pool) for _ in bs_picks]
        else:
            global_mode = self._rng.choice(["4G", "5G", "6G"])
            bs_modes = [global_mode] * len(bs_picks)

        # RSU always uses the most common BS mode (or 5G if hetnet)
        rsu_mode = "5G" if hetnet else global_mode

        density = round(self._rng.uniform(*_DENSITY_RANGE), 1)

        bs_nodes: list[dict] = []
        for i, (nid, mode) in enumerate(zip(bs_picks, bs_modes)):
            nd = graph["nodes"][nid]
            # Randomize coverage radius within per-mode range (macro vs small cell)
            cov_range = _BS_COV_RADIUS_RANGE[mode]
            bs_cov = float(self._rng.randint(*cov_range))
            area_km2 = math.pi * (bs_cov / 1000.0) ** 2
            veh_in_cov = density * area_km2
            bs_load = min(veh_in_cov / _BS_CAPACITY, 1.0)
            # Edge latency: 4G ≈ 5ms, 5G ≈ 3ms, 6G ≈ 1ms (3GPP typical values)
            edge_lat = {"4G": 5.0, "5G": 3.0, "6G": 1.0}.get(mode, 3.0)
            bs_nodes.append({
                "id": f"BS-{i+1:02d}",
                "type": "base_station",
                "network_mode": mode,             # per-BS mode (enables HetNet in _node_rf_features)
                "lat": nd["lat"],
                "lng": nd["lng"],
                "coverage_radius_m": bs_cov,
                "edge_latency_ms": edge_lat,
                "capacity": _BS_CAPACITY,
                "load": round(bs_load, 4),
                "n_background_vehicles": int(veh_in_cov),
                "n_other_devices": 0,
                "n_its_load": 0,
                "source": "domain_randomized",
            })

        # RSU nodes: random road segments (mid-points), randomized radius
        n_rsu = self._rng.randint(*_N_RSU_RANGE)
        rsu_nodes: list[dict] = []
        edge_list = [(a, b) for a, neighbors in adj.items() for b, _ in neighbors]
        if edge_list:
            rsu_edges = self._rng.sample(edge_list, min(n_rsu, len(edge_list)))
            for i, (a, b) in enumerate(rsu_edges):
                na, nb = graph["nodes"][a], graph["nodes"][b]
                rsu_cov_range = _RSU_COV_RADIUS_RANGE[rsu_mode]
                rsu_cov = float(self._rng.randint(*rsu_cov_range))
                rsu_area_km2 = math.pi * (rsu_cov / 1000.0) ** 2
                rsu_veh = density * rsu_area_km2
                rsu_load = min(rsu_veh / 50.0, 1.0)
                rsu_nodes.append({
                    "id": f"RSU-{i+1:02d}",
                    "type": "roadside_unit",
                    "network_mode": rsu_mode,
                    "lat": (na["lat"] + nb["lat"]) / 2,
                    "lng": (na["lng"] + nb["lng"]) / 2,
                    "coverage_radius_m": rsu_cov,
                    "edge_latency_ms": 0.5,
                    "capacity": 50.0,
                    "load": round(rsu_load, 4),
                    "n_background_vehicles": int(rsu_veh),
                    "n_other_devices": 0,
                    "n_its_load": 0,
                    "source": "domain_randomized",
                })

        # O/D pair: guaranteed reachable, with optional max distance
        od = self._sample_od_pair(
            nodes, adj, graph["nodes"],
            max_dist_m=max_od_dist_m,
        )
        if od is None:
            return None
        origin_id, dest_id, od_dist_m = od

        vehicle_speed_kmh = round(self._rng.uniform(*speed_range), 1)

        bbox = {
            "s": region["min_lat"], "n": region["max_lat"],
            "w": region["min_lon"], "e": region["max_lon"],
        }

        return V2XTask(
            region_id=region["osm_id"],
            region_name=region["name_ko"],
            graph=graph,
            bs_nodes=bs_nodes,
            rsu_nodes=rsu_nodes,
            origin_id=origin_id,
            dest_id=dest_id,
            traffic_density=density,
            network_mode=global_mode,   # "4G"/"5G"/"6G" or "hetnet" for mixed
            bbox=bbox,
            vehicle_speed_kmh=vehicle_speed_kmh,
            od_dist_m=od_dist_m,
            max_od_dist_m=max_od_dist_m,
        )

    def _sample_od_pair(
        self,
        nodes: list[str],
        adj: dict[str, list[tuple[str, float]]],
        graph_nodes: dict,
        min_hops: int = 4,
        max_dist_m: float = float("inf"),
        max_tries: int = 50,
    ) -> Optional[tuple[str, str, float]]:
        """
        Sample an O/D pair with at least `min_hops` between them (BFS).

        Returns (origin_id, dest_id, od_dist_m) or None.
        max_dist_m constrains the straight-line distance for curriculum control.
        """
        for _ in range(max_tries):
            origin = self._rng.choice(nodes)
            dest = self._rng.choice(nodes)
            if origin == dest:
                continue
            dist_m = _haversine_m(
                graph_nodes[origin]["lat"], graph_nodes[origin]["lng"],
                graph_nodes[dest]["lat"], graph_nodes[dest]["lng"],
            )
            if dist_m > max_dist_m:
                continue
            if _bfs_reachable(adj, origin, dest, min_hops):
                return origin, dest, dist_m
        return None


# ── Utilities ─────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))   # clip to avoid FP domain error


def _bfs_reachable(
    adj: dict[str, list[tuple[str, float]]],
    start: str,
    goal: str,
    min_hops: int,
) -> bool:
    visited = {start: 0}
    q = deque([start])
    while q:
        node = q.popleft()
        hops = visited[node]
        if hops >= min_hops and node == goal:
            return True
        if hops >= 20:
            break
        for nb, _ in adj.get(node, []):
            if nb not in visited:
                visited[nb] = hops + 1
                q.append(nb)
    return goal in visited and visited[goal] >= min_hops


def _hw_rank(highway: str) -> float:
    """0.0 (service) → 1.0 (motorway). Used as an edge feature."""
    _MAP = {
        "motorway": 1.0, "motorway_link": 0.9,
        "trunk": 0.85, "trunk_link": 0.8,
        "primary": 0.75, "primary_link": 0.7,
        "secondary": 0.6, "secondary_link": 0.55,
        "tertiary": 0.45, "tertiary_link": 0.4,
        "residential": 0.3, "living_street": 0.2,
        "unclassified": 0.2, "service": 0.1,
    }
    return _MAP.get(highway, 0.2)
