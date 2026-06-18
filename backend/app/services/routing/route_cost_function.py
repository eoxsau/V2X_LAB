"""
Route cost function — research-grade V2X network-aware routing.

Component definitions
---------------------
Edge-level (6 independent components):
    1. Distance_Cost      d / norm.distance_km
    2. Travel_Time_Cost   t / norm.time_min
    3. Latency_Cost       latency_ms / norm.latency_ms
    4. BS_Load_Cost       load / capacity  (0–1)
    5. Handover_Cost      1.0 if best-BS changes from previous edge, else 0
    6. Blockage_Cost      penetration_loss_db / norm.loss_db

Path-level (1 component, added once per path):
    7. Coverage_Risk      fraction of edges where no BS is within coverage radius
                          Geometry-based; independent of Latency_Cost.

Total_Edge_Cost  = Σ w_i * component_i  for i in 1..6
Total_Path_Cost  = Σ Total_Edge_Cost + w_coverage_risk * Coverage_Risk

Design rationale
----------------
* Resource_Deficit_Cost removed: was correlated with BS_Load_Cost
  (both derived from the same load/capacity variables).
* Coverage_Risk replaces Future_Connectivity_Risk: old FCR was latency-threshold
  based → double-counted with Latency_Cost. New CR is purely geometric
  (dist_to_best_bs > coverage_radius_m), orthogonal to latency.
* NormScales separates "how to normalise a physical quantity" from
  "how much to weight the normalised result". Researchers adjust one
  without touching the other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from app.services.latency.registry import (
        LATENCY_REGISTRY,
        LatencyInput,
        VehiclePosition,
        BaseStation as _BSModel,
        BuildingState,
        HandoverState,
    )
    _LATENCY_REGISTRY_AVAILABLE = True
except ImportError:
    _LATENCY_REGISTRY_AVAILABLE = False


def _build_latency_input(
    mid_lat: float,
    mid_lng: float,
    bs_dist_m: float,
    bs_node: Optional[dict],
    loss_db: float = 0.0,
    latency_penalty_ms: float = 0.0,
    handover: bool = False,
    prev_bs_id: Optional[str] = None,
) -> Any:
    """Build a LatencyInput from raw cost-function parameters."""
    if not _LATENCY_REGISTRY_AVAILABLE:
        return None
    bs = _BSModel.from_dict(bs_node) if bs_node else _BSModel(
        id="", name="", lat=0.0, lng=0.0
    )
    return LatencyInput(
        vehicle_position=VehiclePosition(lat=mid_lat, lng=mid_lng),
        base_station=bs,
        dist_m=bs_dist_m,
        building_state=BuildingState(
            loss_db=loss_db,
            latency_penalty_ms=latency_penalty_ms,
        ),
        handover_state=HandoverState(
            prev_bs_id=prev_bs_id,
            current_bs_id=bs.id if bs_node else None,
            handover_occurred=handover,
        ),
    )


# ── Geometry ──────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


# ── Configuration ─────────────────────────────────────────────────────────────
@dataclass
class NormScales:
    """
    Normalization denominators — chosen so that 1.0 represents
    a typical *maximum* in dense urban V2X deployments.

    Adjust these if your network has different typical ranges;
    do not touch the weights for unit-range changes.
    """
    distance_km: float = 1.0     # 1 km → 1.0
    time_min: float = 1.0        # 1 min → 1.0
    latency_ms: float = 20.0     # 20 ms (hard-latency SLA) → 1.0
    loss_db: float = 30.0        # 30 dB (concrete-wall max) → 1.0


DEFAULT_NORM_SCALES = NormScales()


@dataclass
class CostWeights:
    """
    Multiplicative weights for each cost component.
    All values ≥ 0.  Higher weight = stronger influence on path selection.

    Quick-reference for tuning:
      * Prioritise latency  → raise w_latency
      * Penalise hand-offs  → raise w_handover
      * Prefer covered roads → raise w_coverage_risk
    """
    w_distance: float = 1.0
    w_time: float = 2.0
    w_latency: float = 3.0
    w_load: float = 1.5
    w_handover: float = 1.0
    w_blockage: float = 1.5
    w_coverage_risk: float = 2.5      # path-level weight for Coverage_Risk
    w_resource_deficit: float = 1.0   # path-level weight for resource deficit cost


DEFAULT_WEIGHTS = CostWeights()


# ── BS Selection algorithm ─────────────────────────────────────────────────────
_active_bs_selection: str = "lowest_latency_bs"

def set_bs_selection_algorithm(algo: str) -> None:
    global _active_bs_selection
    _active_bs_selection = algo


def _bs_score(
    dist_m: float,
    cov_r: float,
    cong: float,
    edge_lat: float,
    load: float,
    capacity: float,
    algo: str,
) -> float:
    """
    Per-algorithm BS scoring — lower score = better BS candidate.

    nearest_bs:          raw Haversine distance
    lowest_latency_bs:   propagation penalty + congestion + edge latency (legacy)
    strongest_signal_bs: quadratic distance loss proxy (free-space path loss ∝ d²)
    load_balanced_bs:    load ratio dominates; small distance tiebreaker
    look_ahead_bs:       lowest-latency + hard penalty when out of coverage
    rl_based_bs:         falls back to lowest_latency_bs (RL agent not yet trained)
    """
    dist_pen = dist_m / max(cov_r, 1.0) * 15.0
    if algo == "nearest_bs":
        return dist_m
    if algo == "load_balanced_bs":
        load_ratio = min(load / max(capacity, 1.0), 1.0)
        return load_ratio * 100.0 + dist_pen * 0.05
    if algo == "strongest_signal_bs":
        dist_ratio = dist_m / max(cov_r, 1.0)
        return dist_ratio * dist_ratio * 100.0 + edge_lat
    if algo in ("look_ahead_bs_selection", "look_ahead_bs"):
        within_cov = dist_m <= cov_r
        return dist_pen + cong + edge_lat + (0.0 if within_cov else 10.0)
    # lowest_latency_bs, rl_based_bs_selection, and any unknown → legacy formula
    return dist_pen + cong + edge_lat


# ── Result types ──────────────────────────────────────────────────────────────
@dataclass
class EdgeCostResult:
    edge_id: str
    midpoint_lat: float
    midpoint_lng: float
    distance_m: float
    travel_time_s: float
    best_node_id: Optional[str]
    best_node_name: Optional[str]
    latency_ms: float
    load_ratio: float              # load / capacity (0–1)
    handover_occurred: bool
    loss_db: float
    within_coverage: bool          # dist_to_best_bs ≤ coverage_radius_m
    total_cost: float              # edge-level (components 1–6)
    components: dict = field(default_factory=dict)


@dataclass
class PathCostResult:
    total_cost: float              # edge total + path-level Coverage_Risk + resource_deficit
    distance_time_cost: float      # sum of distance + time components only
    total_distance_m: float
    total_travel_time_s: float
    avg_latency_ms: float
    max_latency_ms: float
    handover_count: int
    coverage_risk: float           # fraction of edges beyond BS coverage (0–1)
    avg_loss_db: float
    covered_pct: float             # fraction of edges within BS coverage (0–1)
    edge_count: int
    edge_results: list[EdgeCostResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    resource_deficit_cost: float = 0.0    # normalized deficit from resource allocation
    allocation_summary: dict = field(default_factory=dict)


@dataclass
class KPathCandidate:
    """Evaluation result for one candidate in Yen's K-shortest paths."""
    rank: int                      # 0 = best (lowest total_cost)
    path: list[str]                # raw path IDs (node IDs or sumolib edge IDs)
    total_cost: float
    distance_time_cost: float
    total_distance_m: float
    total_travel_time_s: float
    avg_latency_ms: float
    max_latency_ms: float
    handover_count: int
    coverage_risk: float
    avg_loss_db: float
    covered_pct: float
    selected_bs_sequence: list[str]   # deduplicated BS transition sequence
    selected: bool = False            # True for the lowest-cost candidate
    path_cost_result: Optional[PathCostResult] = None   # full detail if needed
    resource_deficit_cost: float = 0.0    # normalized BS deficit on this path
    expected_latency_impact: float = 0.0  # sum of allocation latency delta ms


# ── Internal BS selection ─────────────────────────────────────────────────────
def _find_best_bs_light(
    mid_lat: float,
    mid_lng: float,
    nodes: list[dict],
) -> tuple[Optional[dict], float, float, float, bool]:
    """
    Fast BS selection: distance-penalty + congestion + edge_latency.
    No building intersection.
    Returns (node, dist_m, predicted_latency_ms, loss_db=0, within_coverage).
    Called during Dijkstra routing (skip_buildings=True).
    """
    best_node: Optional[dict] = None
    best_score = float("inf")
    best_dist_m = 0.0

    algo = _active_bs_selection
    for node in nodes:
        n_lat = float(node.get("lat") or 0)
        n_lng = float(node.get("lng") or 0)
        if n_lat == 0.0 and n_lng == 0.0:
            continue
        dist_m = _haversine_m(mid_lat, mid_lng, n_lat, n_lng)
        cov_r = float(node.get("coverage_radius_m") or 400.0)
        cong = float(node.get("congestion_penalty") or 0.0)
        edge_lat = float(node.get("edge_latency_ms") or 5.0)
        load = float(node.get("load") or 0.0)
        cap = float(node.get("capacity") or 100.0)
        score = _bs_score(dist_m, cov_r, cong, edge_lat, load, cap, algo)
        if score < best_score:
            best_score = score
            best_node = node
            best_dist_m = dist_m

    if best_node is None:
        return None, 0.0, 50.0, 0.0, False

    cov_r = float(best_node.get("coverage_radius_m") or 400.0)
    dist_pen = best_dist_m / max(cov_r, 1.0) * 15.0
    cong = float(best_node.get("congestion_penalty") or 0.0)
    edge_lat = float(best_node.get("edge_latency_ms") or 5.0)
    pred_lat = round(4.0 + dist_pen + cong + edge_lat, 2)
    within_cov = best_dist_m <= cov_r
    return best_node, best_dist_m, pred_lat, 0.0, within_cov


def _find_best_bs_full(
    mid_lat: float,
    mid_lng: float,
    nodes: list[dict],
    buildings_gdf: Any,
) -> tuple[Optional[dict], float, float, float, bool]:
    """
    Full BS selection using analyze_vehicle_to_node (building intersection).
    Called during post-route evaluation (skip_buildings=False).
    Returns (node, dist_m, predicted_latency_ms, loss_db, within_coverage).
    Falls back to _find_best_bs_light if geopandas is unavailable.
    """
    try:
        from app.services.buildings.building_obstruction_analyzer import analyze_vehicle_to_node
        import geopandas as gpd
        gdf = buildings_gdf if buildings_gdf is not None else gpd.GeoDataFrame()
    except ImportError:
        return _find_best_bs_light(mid_lat, mid_lng, nodes)

    best_node: Optional[dict] = None
    best_score = float("inf")
    best_obs = None

    for node in nodes:
        n_lat = float(node.get("lat") or 0)
        n_lng = float(node.get("lng") or 0)
        if n_lat == 0.0 and n_lng == 0.0:
            continue
        try:
            obs = analyze_vehicle_to_node(
                vehicle_id="_cost_eval",
                vehicle_lat=mid_lat,
                vehicle_lng=mid_lng,
                network_node=node,
                buildings_gdf=gdf,
            )
        except Exception:
            continue
        cov_r = float(node.get("coverage_radius_m") or 400.0)
        cong = float(node.get("congestion_penalty") or 0.0)
        edge_lat = float(node.get("edge_latency_ms") or 5.0)
        load = float(node.get("load") or 0.0)
        cap = float(node.get("capacity") or 100.0)
        algo = _active_bs_selection
        # For blockage-aware algorithms, add penetration loss to the score
        base_score = _bs_score(obs.distance_m, cov_r, cong, edge_lat, load, cap, algo)
        score = base_score + obs.estimated_penetration_loss_db * 0.8
        if score < best_score:
            best_score = score
            best_node = node
            best_obs = obs

    if best_node is None or best_obs is None:
        return _find_best_bs_light(mid_lat, mid_lng, nodes)

    cov_r = float(best_node.get("coverage_radius_m") or 400.0)
    dist_pen = best_obs.distance_m / max(cov_r, 1.0) * 15.0
    cong = float(best_node.get("congestion_penalty") or 0.0)
    edge_lat = float(best_node.get("edge_latency_ms") or 5.0)
    pred_lat = round(4.0 + dist_pen + cong + best_obs.latency_penalty_ms + edge_lat, 2)
    within_cov = best_obs.distance_m <= cov_r
    return best_node, best_obs.distance_m, pred_lat, best_obs.estimated_penetration_loss_db, within_cov


# ── Core computation ──────────────────────────────────────────────────────────
def compute_edge_network_cost(
    *,
    edge_id: str,
    midpoint_lat: float,
    midpoint_lng: float,
    distance_m: float,
    travel_time_s: float,
    nodes: list[dict],
    buildings_gdf: Any = None,
    prev_best_node_id: Optional[str] = None,
    weights: CostWeights = DEFAULT_WEIGHTS,
    norm_scales: NormScales = DEFAULT_NORM_SCALES,
    skip_buildings: bool = False,
) -> EdgeCostResult:
    """
    Compute 6 independent per-edge cost components.

    Coverage_Risk (component 7) is a path-level aggregate computed in
    evaluate_path(); it is NOT included in total_cost here.

    Args:
        skip_buildings: Use fast light BS selection without building intersection.
                        Set True during Dijkstra routing; False during evaluation.
    """
    # Stage 1: BS selection — existing wrapper functions unchanged
    _latency_penalty_ms = 0.0
    if nodes:
        if skip_buildings or buildings_gdf is None:
            best_node, _bs_dist_m, latency_ms, loss_db, within_cov = _find_best_bs_light(
                midpoint_lat, midpoint_lng, nodes
            )
        else:
            best_node, _bs_dist_m, latency_ms, loss_db, within_cov = _find_best_bs_full(
                midpoint_lat, midpoint_lng, nodes, buildings_gdf
            )
            # latency_penalty_ms is embedded in latency_ms by _find_best_bs_full;
            # recover an approximation for the registry building_state
            _latency_penalty_ms = max(0.0, latency_ms - 4.0)
    else:
        best_node, _bs_dist_m, latency_ms, loss_db, within_cov = None, 0.0, 50.0, 0.0, False

    best_node_id = str(best_node["id"]) if best_node else None
    best_node_name = (best_node.get("name") or best_node_id) if best_node else None

    if best_node:
        load = float(best_node.get("load") or 0.0)
        cap = float(best_node.get("capacity") or 100.0)
        load_ratio = min(load / max(cap, 1.0), 1.0)
    else:
        load_ratio = 0.0

    handover = bool(prev_best_node_id and best_node_id and prev_best_node_id != best_node_id)

    # Stage 2: Latency via registry (overrides legacy value when available)
    _latency_components: dict = {}
    if _LATENCY_REGISTRY_AVAILABLE and best_node is not None:
        try:
            _lat_inp = _build_latency_input(
                mid_lat=midpoint_lat,
                mid_lng=midpoint_lng,
                bs_dist_m=_bs_dist_m,
                bs_node=best_node,
                loss_db=loss_db,
                latency_penalty_ms=_latency_penalty_ms,
                handover=handover,
                prev_bs_id=prev_best_node_id,
            )
            _lat_out = LATENCY_REGISTRY.compute(_lat_inp)
            latency_ms = _lat_out.latency
            _latency_components = _lat_out.to_dict()
        except Exception:
            pass  # keep legacy latency_ms on any registry error

    # Normalised components
    c_dist     = distance_m / 1000.0 / norm_scales.distance_km
    c_time     = travel_time_s / 60.0 / norm_scales.time_min
    c_latency  = latency_ms / norm_scales.latency_ms
    c_load     = load_ratio
    c_handover = 1.0 if handover else 0.0
    c_blockage = loss_db / norm_scales.loss_db

    total = (
        weights.w_distance  * c_dist
        + weights.w_time    * c_time
        + weights.w_latency * c_latency
        + weights.w_load    * c_load
        + weights.w_handover * c_handover
        + weights.w_blockage * c_blockage
    )

    return EdgeCostResult(
        edge_id=edge_id,
        midpoint_lat=midpoint_lat,
        midpoint_lng=midpoint_lng,
        distance_m=distance_m,
        travel_time_s=travel_time_s,
        best_node_id=best_node_id,
        best_node_name=best_node_name,
        latency_ms=latency_ms,
        load_ratio=round(load_ratio, 4),
        handover_occurred=handover,
        loss_db=round(loss_db, 2),
        within_coverage=within_cov,
        total_cost=round(total, 4),
        components={
            "distance_cost":  round(c_dist,     4),
            "time_cost":      round(c_time,     4),
            "latency_cost":   round(c_latency,  4),
            "load_cost":      round(c_load,     4),
            "handover_cost":  round(c_handover, 4),
            "blockage_cost":  round(c_blockage, 4),
            **({"latency_components": _latency_components} if _latency_components else {}),
        },
    )


def evaluate_path(
    edge_data_list: list[dict],
    nodes: list[dict],
    buildings_gdf: Any = None,
    weights: CostWeights = DEFAULT_WEIGHTS,
    norm_scales: NormScales = DEFAULT_NORM_SCALES,
) -> PathCostResult:
    """
    Evaluate the full network cost of a route.

    Each item in edge_data_list must supply:
        edge_id, midpoint_lat, midpoint_lng, distance_m, travel_time_s
    """
    if not edge_data_list:
        return PathCostResult(
            total_cost=0.0, distance_time_cost=0.0,
            total_distance_m=0.0, total_travel_time_s=0.0,
            avg_latency_ms=0.0, max_latency_ms=0.0,
            handover_count=0, coverage_risk=0.0,
            avg_loss_db=0.0, covered_pct=0.0, edge_count=0,
        )

    edge_results: list[EdgeCostResult] = []
    prev_node_id: Optional[str] = None

    for ed in edge_data_list:
        r = compute_edge_network_cost(
            edge_id=ed["edge_id"],
            midpoint_lat=ed["midpoint_lat"],
            midpoint_lng=ed["midpoint_lng"],
            distance_m=ed["distance_m"],
            travel_time_s=ed["travel_time_s"],
            nodes=nodes,
            buildings_gdf=buildings_gdf,
            prev_best_node_id=prev_node_id,
            weights=weights,
            norm_scales=norm_scales,
            skip_buildings=False,
        )
        edge_results.append(r)
        prev_node_id = r.best_node_id

    n = len(edge_results)
    total_dist = sum(r.distance_m for r in edge_results)
    total_time = sum(r.travel_time_s for r in edge_results)
    lats = [r.latency_ms for r in edge_results]
    avg_lat = sum(lats) / n
    max_lat = max(lats)
    n_handover = sum(1 for r in edge_results if r.handover_occurred)
    n_uncovered = sum(1 for r in edge_results if not r.within_coverage)
    coverage_risk = n_uncovered / n
    avg_loss = sum(r.loss_db for r in edge_results) / n
    covered_pct = (n - n_uncovered) / n

    # distance + time sub-total for comparison with baseline Dijkstra
    dt_cost = sum(
        weights.w_distance * r.distance_m / 1000.0 / norm_scales.distance_km
        + weights.w_time   * r.travel_time_s / 60.0 / norm_scales.time_min
        for r in edge_results
    )

    # Coverage_Risk added once at path level (geometry-based, not latency-based)
    # resource_deficit_cost is injected by evaluate_route_with_resource_allocation
    total_cost = sum(r.total_cost for r in edge_results) + weights.w_coverage_risk * coverage_risk

    return PathCostResult(
        total_cost=round(total_cost, 4),
        distance_time_cost=round(dt_cost, 4),
        total_distance_m=round(total_dist, 2),
        total_travel_time_s=round(total_time, 2),
        avg_latency_ms=round(avg_lat, 2),
        max_latency_ms=round(max_lat, 2),
        handover_count=n_handover,
        coverage_risk=round(coverage_risk, 4),
        avg_loss_db=round(avg_loss, 2),
        covered_pct=round(covered_pct, 4),
        edge_count=n,
        edge_results=edge_results,
        summary={
            "total_edges":       n,
            "covered_edges":     n - n_uncovered,
            "uncovered_edges":   n_uncovered,
            "handovers":         n_handover,
            "avg_latency_ms":    round(avg_lat, 2),
            "max_latency_ms":    round(max_lat, 2),
            "total_distance_km": round(total_dist / 1000, 3),
            "total_travel_min":  round(total_time / 60, 2),
        },
    )


# ── Allocation helpers ────────────────────────────────────────────────────────
def _bs_capacity_from_nodes(bs_id: str, nodes: list[dict]) -> float:
    for node in nodes:
        nid = str(node.get("id") or node.get("name") or "")
        if nid == bs_id:
            return float(node.get("capacity") or 100.0)
    return 100.0


def _allocation_costs_for_path(
    bs_ids_on_path: list[str],
    allocation_output: dict,
    nodes: list[dict],
) -> tuple[float, float]:
    """
    Return (resource_deficit_cost, expected_latency_impact_ms) for a path
    given the BSes it visits and an AllocationOutput dict.

    resource_deficit_cost  : sum of (deficit_rb / capacity) for each visited BS.
                             Dimensionless; 1.0 = one full BS over capacity.
    expected_latency_impact: sum of latency delta ms for each visited BS.
                             Negative means the allocation improved latency.
    """
    deficit_map  = allocation_output.get("resource_deficit_by_bs", {})
    latency_map  = allocation_output.get("expected_latency_impact", {})
    deficit_cost = 0.0
    latency_imp  = 0.0
    for bid in set(bs_ids_on_path):
        if bid in deficit_map:
            cap = _bs_capacity_from_nodes(bid, nodes)
            deficit_cost += deficit_map[bid] / max(cap, 1.0)
        if bid in latency_map:
            latency_imp += latency_map[bid]
    return round(deficit_cost, 4), round(latency_imp, 4)


# ── K-path candidate evaluation ───────────────────────────────────────────────
def evaluate_k_candidates(
    candidates: list[tuple[list[str], list[dict]]],
    nodes: list[dict],
    buildings_gdf: Any = None,
    weights: CostWeights = DEFAULT_WEIGHTS,
    norm_scales: NormScales = DEFAULT_NORM_SCALES,
    allocation_output: Optional[dict] = None,
) -> list[KPathCandidate]:
    """
    Evaluate K candidate paths and return them sorted by total_cost (ascending).
    The lowest-cost candidate is flagged with selected=True.

    When allocation_output is provided:
      - resource_deficit_cost is computed per path (BSes visited × deficit fraction)
      - resource_deficit_cost × weights.w_resource_deficit is added to total_cost
      - Sorting uses the allocation-adjusted total_cost
      - KPathCandidate.resource_deficit_cost and .expected_latency_impact are populated

    Args:
        candidates: list of (path_ids, edge_data_list) tuples.
        allocation_output: AllocationOutput.to_dict() from a prior allocation run.
    """
    results: list[KPathCandidate] = []

    for idx, (path_ids, edge_data) in enumerate(candidates):
        pc = evaluate_path(edge_data, nodes, buildings_gdf, weights, norm_scales)

        # BS sequence (deduplicated transitions)
        bs_seq: list[str] = []
        for r in pc.edge_results:
            name = r.best_node_name or r.best_node_id or "—"
            if not bs_seq or bs_seq[-1] != name:
                bs_seq.append(name)

        # Allocation-aware cost additions
        res_deficit = 0.0
        lat_impact  = 0.0
        if allocation_output:
            bs_ids = [r.best_node_id for r in pc.edge_results if r.best_node_id]
            res_deficit, lat_impact = _allocation_costs_for_path(bs_ids, allocation_output, nodes)

        adjusted_cost = round(
            pc.total_cost + weights.w_resource_deficit * res_deficit, 4
        )

        results.append(KPathCandidate(
            rank=idx,
            path=list(path_ids),
            total_cost=adjusted_cost,
            distance_time_cost=pc.distance_time_cost,
            total_distance_m=pc.total_distance_m,
            total_travel_time_s=pc.total_travel_time_s,
            avg_latency_ms=pc.avg_latency_ms,
            max_latency_ms=pc.max_latency_ms,
            handover_count=pc.handover_count,
            coverage_risk=pc.coverage_risk,
            avg_loss_db=pc.avg_loss_db,
            covered_pct=pc.covered_pct,
            selected_bs_sequence=bs_seq,
            selected=False,
            path_cost_result=pc,
            resource_deficit_cost=res_deficit,
            expected_latency_impact=lat_impact,
        ))

    results.sort(key=lambda r: r.total_cost)
    for i, r in enumerate(results):
        r.rank = i
    if results:
        results[0].selected = True

    return results


# ── Allocation-integrated route evaluation ────────────────────────────────────
def evaluate_route_with_resource_allocation(
    path: list[str],
    edge_data_list: list[dict],
    nodes: list[dict],
    allocation_output: Optional[dict] = None,
    buildings_gdf: Any = None,
    weights: CostWeights = DEFAULT_WEIGHTS,
    norm_scales: NormScales = DEFAULT_NORM_SCALES,
) -> dict:
    """
    Evaluate a single route and incorporate resource allocation results.

    Returns the dict shape specified by the resource allocation integration spec:
    {
      path, selected_base_station_sequence,
      resource_allocation_result, resource_deficit_cost,
      expected_latency_impact, total_cost,
      cost_breakdown, debug_info
    }

    The allocation must already be applied to ``nodes`` (via
    apply_allocation_to_network_nodes) before calling this function so that
    evaluate_path() reads allocation-updated loads automatically.
    """
    pc = evaluate_path(edge_data_list, nodes, buildings_gdf, weights, norm_scales)

    # BS sequence
    bs_seq: list[str] = []
    bs_ids: list[str] = []
    for r in pc.edge_results:
        name = r.best_node_name or r.best_node_id or "—"
        if not bs_seq or bs_seq[-1] != name:
            bs_seq.append(name)
        if r.best_node_id and (not bs_ids or bs_ids[-1] != r.best_node_id):
            bs_ids.append(r.best_node_id)

    res_deficit = 0.0
    lat_impact  = 0.0
    alloc_result: dict = {}
    debug: dict = {}

    if allocation_output:
        res_deficit, lat_impact = _allocation_costs_for_path(bs_ids, allocation_output, nodes)
        alloc_result = allocation_output.get("allocation_result", {})
        debug = {
            "algorithm_id": allocation_output.get("algorithm_id", ""),
            "bs_visited_ids": bs_ids,
            "deficit_by_visited_bs": {
                bid: allocation_output.get("resource_deficit_by_bs", {}).get(bid, 0.0)
                for bid in bs_ids
            },
            "latency_impact_by_visited_bs": {
                bid: allocation_output.get("expected_latency_impact", {}).get(bid, 0.0)
                for bid in bs_ids
            },
        }

    adjusted_cost = round(pc.total_cost + weights.w_resource_deficit * res_deficit, 4)

    return {
        "path":                        list(path),
        "selected_base_station_sequence": bs_seq,
        "resource_allocation_result":  alloc_result,
        "resource_deficit_cost":       res_deficit,
        "expected_latency_impact":     lat_impact,
        "total_cost":                  adjusted_cost,
        "cost_breakdown": {
            "base_total_cost":      round(pc.total_cost, 4),
            "resource_deficit_cost": res_deficit,
            "total_distance_m":     pc.total_distance_m,
            "total_travel_time_s":  pc.total_travel_time_s,
            "avg_latency_ms":       pc.avg_latency_ms,
            "max_latency_ms":       pc.max_latency_ms,
            "handover_count":       pc.handover_count,
            "coverage_risk":        pc.coverage_risk,
            "distance_time_cost":   pc.distance_time_cost,
        },
        "debug_info": debug,
    }
