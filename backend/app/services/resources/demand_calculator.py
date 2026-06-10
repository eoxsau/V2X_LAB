"""
BS Resource Demand Calculator — traffic-based per-BS resource demand estimation.

Five public functions:
  get_road_segments_near_base_station   — BFS edge collection within BS coverage
  estimate_traffic_demand_near_base_station — vehicle/traffic aggregation
  estimate_expected_connected_vehicles  — current + route + look-ahead sources
  calculate_base_station_resource_demand — combine into BSResourceDemand
  build_resource_demand_map             — iterate all BSes → demand dict
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Optional

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_SPEED_MPS: float = 13.9      # 50 km/h fallback
_RB_PER_VEHICLE_BASE: float = 5.0    # resource blocks consumed per vehicle
_DWELL_NORM_S: float = 30.0          # normalization for dwell factor
_MAX_DWELL_FACTOR: float = 3.0       # cap: congested vehicles use 3× more RBs
_LOOKAHEAD_WEIGHT: float = 0.3       # α: how much look-ahead contributes
_MAX_BFS_RADIUS_FACTOR: float = 1.5  # BFS expansion limit = cov_r × factor


# ── Geometry ──────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_road_node(
    lat: float,
    lng: float,
    road_nodes: dict,
    max_dist_m: float,
) -> Optional[str]:
    best_id: Optional[str] = None
    best_d = float("inf")
    for nid, n in road_nodes.items():
        d = _haversine_m(lat, lng, float(n.get("lat", 0.0)), float(n.get("lng", 0.0)))
        if d < best_d:
            best_d = d
            best_id = nid
    return best_id if best_d <= max_dist_m else None


# ── Output type ───────────────────────────────────────────────────────────────
@dataclass
class BSResourceDemand:
    base_station_id: str
    nearby_vehicle_count: int
    expected_connected_vehicle_count: float
    nearby_traffic_density: float       # vehicles/km of nearby roads
    average_speed: float                # m/s average of nearby vehicles
    estimated_resource_demand: float    # total resource blocks needed
    capacity: float                     # total resource block capacity
    demand_capacity_ratio: float        # estimated_demand / capacity (can exceed 1)

    def to_dict(self) -> dict:
        return asdict(self)


# ── 1. Road segments near BS ──────────────────────────────────────────────────
def get_road_segments_near_base_station(
    base_station: dict,
    road_graph: dict,
    radius_m: Optional[float] = None,
) -> list[dict]:
    """
    BFS from the nearest road node to BS center.
    Returns all directed edges whose midpoint lies within ``radius_m``
    (defaults to base_station["coverage_radius_m"]).

    Each segment dict:
      edge_id, from_id, to_id, mid_lat, mid_lng,
      length_m, dist_to_bs_m, connection_prob
    """
    bs_lat = float(base_station.get("lat", 0.0))
    bs_lng = float(base_station.get("lng", 0.0))
    cov_r = radius_m if radius_m is not None else float(base_station.get("coverage_radius_m", 400.0))
    expand_limit = cov_r * _MAX_BFS_RADIUS_FACTOR

    road_nodes: dict = road_graph.get("nodes", {}) if isinstance(road_graph, dict) else {}
    adjacency: dict = road_graph.get("adjacency", {}) if isinstance(road_graph, dict) else {}

    if not road_nodes:
        return []

    start_id = _nearest_road_node(bs_lat, bs_lng, road_nodes, expand_limit)
    if start_id is None:
        return []

    visited: set[str] = set()
    frontier: set[str] = {start_id}
    segments: list[dict] = []

    while frontier:
        next_frontier: set[str] = set()
        for node_id in frontier:
            if node_id in visited:
                continue
            visited.add(node_id)

            from_node = road_nodes.get(node_id, {})
            from_lat = float(from_node.get("lat", 0.0))
            from_lng = float(from_node.get("lng", 0.0))

            # Prune: if this node is already beyond the expansion limit, don't expand
            if _haversine_m(bs_lat, bs_lng, from_lat, from_lng) > expand_limit:
                continue

            for entry in adjacency.get(node_id, []):
                if not entry:
                    continue
                if len(entry) >= 4:
                    to_id = str(entry[0])
                    edge_id = str(entry[1])
                    length_m = float(entry[2])
                elif len(entry) >= 2:
                    to_id = str(entry[0])
                    edge_id = f"{node_id}_{to_id}"
                    length_m = float(entry[1])
                else:
                    to_id = str(entry[0])
                    edge_id = f"{node_id}_{to_id}"
                    length_m = 0.0

                to_node = road_nodes.get(to_id, {})
                mid_lat = (from_lat + float(to_node.get("lat", 0.0))) / 2.0
                mid_lng = (from_lng + float(to_node.get("lng", 0.0))) / 2.0
                dist_to_bs = _haversine_m(mid_lat, mid_lng, bs_lat, bs_lng)

                if dist_to_bs <= cov_r:
                    prob = max(0.0, 1.0 - dist_to_bs / cov_r)
                    segments.append({
                        "edge_id": edge_id,
                        "from_id": node_id,
                        "to_id": to_id,
                        "mid_lat": round(mid_lat, 7),
                        "mid_lng": round(mid_lng, 7),
                        "length_m": round(length_m, 2),
                        "dist_to_bs_m": round(dist_to_bs, 2),
                        "connection_prob": round(prob, 4),
                    })
                    if to_id not in visited:
                        next_frontier.add(to_id)
                elif to_id not in visited:
                    # Keep exploring nearby nodes even if this edge's midpoint is outside
                    to_dist = _haversine_m(
                        float(to_node.get("lat", 0.0)),
                        float(to_node.get("lng", 0.0)),
                        bs_lat, bs_lng,
                    )
                    if to_dist <= expand_limit:
                        next_frontier.add(to_id)

        frontier = next_frontier

    return segments


# ── 2. Traffic demand near BS ─────────────────────────────────────────────────
def estimate_traffic_demand_near_base_station(
    base_station: dict,
    vehicles: list[dict],
    road_graph: dict,
    traffic_data: Optional[dict] = None,
) -> dict:
    """
    Aggregate vehicle positions and/or ITS traffic data to estimate demand.

    Returns
    -------
    {
      vehicle_count       : int,    — vehicles within coverage radius
      density_per_km      : float,  — vehicles / km of nearby road
      avg_speed_mps       : float,  — mean speed of nearby vehicles
      demand_rb           : float,  — estimated resource-block consumption
      total_road_length_m : float,  — total length of roads within coverage
      segment_count       : int,
    }
    """
    bs_lat = float(base_station.get("lat", 0.0))
    bs_lng = float(base_station.get("lng", 0.0))
    cov_r = float(base_station.get("coverage_radius_m", 400.0))

    segments = get_road_segments_near_base_station(base_station, road_graph)
    total_road_m = sum(s["length_m"] for s in segments)

    # --- vehicles directly within coverage ---
    nearby = [
        v for v in vehicles
        if _haversine_m(
            bs_lat, bs_lng,
            float(v.get("lat", 0.0)), float(v.get("lng", 0.0))
        ) <= cov_r
    ]
    v_count = len(nearby)
    speed_sum = sum(float(v.get("speed_mps", _DEFAULT_SPEED_MPS)) for v in nearby)
    avg_speed = speed_sum / v_count if v_count > 0 else _DEFAULT_SPEED_MPS
    density = v_count / (total_road_m / 1000.0) if total_road_m > 0 else 0.0

    # --- resource demand ---
    demand_rb = 0.0

    if traffic_data:
        # ITS / external traffic data: edge-level vehicle_count + speed
        for seg in segments:
            info = traffic_data.get(seg["edge_id"], {})
            edge_vehicles = float(info.get("vehicle_count", 0))
            if edge_vehicles == 0:
                # Fall back to speed-derived density estimate
                speed_kph = float(info.get("speed_kph", _DEFAULT_SPEED_MPS * 3.6))
                edge_vehicles = density * max(seg["length_m"] / 1000.0, 0.001)
                speed_ms = speed_kph / 3.6
            else:
                speed_ms = float(info.get("speed_kph", _DEFAULT_SPEED_MPS * 3.6)) / 3.6

            prob = seg["connection_prob"]
            dwell = min(
                seg["length_m"] / max(speed_ms, 0.5) / _DWELL_NORM_S,
                _MAX_DWELL_FACTOR,
            )
            demand_rb += edge_vehicles * prob * dwell * _RB_PER_VEHICLE_BASE
    else:
        # Vehicle-position fallback: estimate per vehicle
        for v in nearby:
            v_lat = float(v.get("lat", 0.0))
            v_lng = float(v.get("lng", 0.0))
            dist = _haversine_m(bs_lat, bs_lng, v_lat, v_lng)
            prob = max(0.0, 1.0 - dist / cov_r)
            speed = float(v.get("speed_mps", _DEFAULT_SPEED_MPS))
            # Use 200 m as typical edge length when no segment data available
            dwell = min(200.0 / max(speed, 0.5) / _DWELL_NORM_S, _MAX_DWELL_FACTOR)
            demand_rb += prob * dwell * _RB_PER_VEHICLE_BASE

    return {
        "vehicle_count": v_count,
        "density_per_km": round(density, 4),
        "avg_speed_mps": round(avg_speed, 4),
        "demand_rb": round(demand_rb, 4),
        "total_road_length_m": round(total_road_m, 2),
        "segment_count": len(segments),
    }


# ── 3. Expected connected vehicles ────────────────────────────────────────────
def estimate_expected_connected_vehicles(
    base_station: dict,
    vehicles: list[dict],
    route_candidates: list,
    lookahead_result: Optional[Any] = None,
    road_nodes: Optional[dict] = None,
    road_graph: Optional[dict] = None,
) -> float:
    """
    Three-source estimate of how many vehicles will connect to this BS.

    Source 1 — currently within coverage (direct count).
    Source 2 — vehicles whose route candidates pass through BS coverage.
    Source 3 — look-ahead BFS: fraction of future road edges covered by this BS,
                weighted by hop proximity and current vehicle density.

    Parameters
    ----------
    route_candidates : list of KPathCandidate (or any object with .path and .rank)
    lookahead_result : LookAheadResult (or None)
    road_nodes / road_graph : needed for source 3 BFS; skipped if not provided
    """
    bs_lat = float(base_station.get("lat", 0.0))
    bs_lng = float(base_station.get("lng", 0.0))
    cov_r = float(base_station.get("coverage_radius_m", 400.0))

    # Source 1: currently in coverage
    current_count = sum(
        1 for v in vehicles
        if _haversine_m(
            bs_lat, bs_lng,
            float(v.get("lat", 0.0)), float(v.get("lng", 0.0))
        ) <= cov_r
    )

    # Source 2: route candidates with at least one node within coverage
    future_from_routes = 0.0
    if route_candidates and road_nodes:
        for candidate in route_candidates:
            path = getattr(candidate, "path", [])
            for node_id in path:
                node = road_nodes.get(str(node_id), {})
                if node and _haversine_m(
                    bs_lat, bs_lng,
                    float(node.get("lat", 0.0)), float(node.get("lng", 0.0))
                ) <= cov_r:
                    # Higher-ranked (lower rank index) candidates contribute more
                    rank = getattr(candidate, "rank", 1) or 1
                    future_from_routes += 1.0 / (rank + 1)
                    break

    # Source 3: look-ahead BFS coverage by this specific BS
    future_from_lookahead = 0.0
    if (
        lookahead_result is not None
        and road_nodes is not None
        and road_graph is not None
        and current_count > 0
    ):
        adjacency = road_graph.get("adjacency", {}) if isinstance(road_graph, dict) else {}
        start_node = str(lookahead_result.current_node_id)
        n_hops = lookahead_result.lookahead_hops

        visited: set[str] = {start_node}
        frontier: set[str] = {start_node}

        for hop_scan in lookahead_result.per_hop:
            hop_num = hop_scan.hop  # 1-indexed
            hop_weight = (n_hops - hop_num + 1) / max(n_hops, 1)

            next_frontier: set[str] = set()
            covered_by_this_bs = 0
            total_edges = 0

            for node_id in frontier:
                from_node = road_nodes.get(str(node_id), {})
                from_lat = float(from_node.get("lat", 0.0))
                from_lng = float(from_node.get("lng", 0.0))

                for entry in adjacency.get(str(node_id), []):
                    if not entry:
                        continue
                    to_id = str(entry[0])
                    to_node = road_nodes.get(to_id, {})
                    mid_lat = (from_lat + float(to_node.get("lat", 0.0))) / 2.0
                    mid_lng = (from_lng + float(to_node.get("lng", 0.0))) / 2.0

                    total_edges += 1
                    if _haversine_m(mid_lat, mid_lng, bs_lat, bs_lng) <= cov_r:
                        covered_by_this_bs += 1

                    if to_id not in visited:
                        next_frontier.add(to_id)
                        visited.add(to_id)

            frontier = next_frontier
            if not frontier:
                break

            if total_edges > 0:
                cov_frac = covered_by_this_bs / total_edges
                # Expected future vehicles ∝ current density × fraction of roads this BS covers
                future_from_lookahead += _LOOKAHEAD_WEIGHT * hop_weight * cov_frac * current_count

    return round(current_count + future_from_routes + future_from_lookahead, 2)


# ── 4. Calculate BS resource demand ──────────────────────────────────────────
def calculate_base_station_resource_demand(
    base_station: dict,
    traffic_context: dict,
    expected_connected: float,
    rb_per_vehicle: float = _RB_PER_VEHICLE_BASE,
) -> BSResourceDemand:
    """
    Combine traffic context and expected vehicle count into a BSResourceDemand.

    estimated_resource_demand = max(traffic-based demand, vehicle-count-based demand)
    This prevents under-estimation when one source is unavailable.
    """
    bs_id = str(base_station.get("id") or base_station.get("name") or "")
    capacity = float(base_station.get("capacity", 100.0))

    traffic_demand = traffic_context.get("demand_rb", 0.0)
    vehicle_demand = expected_connected * rb_per_vehicle
    estimated = max(traffic_demand, vehicle_demand)

    ratio = estimated / max(capacity, 1.0)

    return BSResourceDemand(
        base_station_id=bs_id,
        nearby_vehicle_count=int(traffic_context.get("vehicle_count", 0)),
        expected_connected_vehicle_count=round(expected_connected, 2),
        nearby_traffic_density=float(traffic_context.get("density_per_km", 0.0)),
        average_speed=float(traffic_context.get("avg_speed_mps", _DEFAULT_SPEED_MPS)),
        estimated_resource_demand=round(estimated, 4),
        capacity=capacity,
        demand_capacity_ratio=round(min(ratio, 9.99), 4),
    )


# ── 5. Build full resource demand map ─────────────────────────────────────────
def build_resource_demand_map(
    base_stations: list[dict],
    vehicles: list[dict],
    road_graph: dict,
    traffic_data: Optional[dict] = None,
    lookahead_results: Optional[Any] = None,
    route_candidates: Optional[list] = None,
    rb_per_vehicle: float = _RB_PER_VEHICLE_BASE,
    total_rb_per_bs: float = 100.0,
) -> dict[str, BSResourceDemand]:
    """
    Compute BSResourceDemand for every BS in ``base_stations``.

    Parameters
    ----------
    lookahead_results : A single LookAheadResult (applied to all BSes)
                        OR dict[bs_id → LookAheadResult] (per-BS results).
    route_candidates  : list of KPathCandidate from evaluate_k_candidates().

    Returns
    -------
    dict mapping bs_id → BSResourceDemand
    """
    road_nodes: dict = road_graph.get("nodes", {}) if isinstance(road_graph, dict) else {}
    result: dict[str, BSResourceDemand] = {}

    for bs in base_stations:
        bs_id = str(bs.get("id") or bs.get("name") or "")
        bs_with_cap = {**bs}
        if "capacity" not in bs_with_cap or not bs_with_cap["capacity"]:
            bs_with_cap["capacity"] = total_rb_per_bs

        # Resolve look-ahead result for this BS
        la_result = None
        if lookahead_results is not None:
            if isinstance(lookahead_results, dict):
                la_result = lookahead_results.get(bs_id)
            else:
                la_result = lookahead_results  # single result shared across all BSes

        traffic_ctx = estimate_traffic_demand_near_base_station(
            bs_with_cap, vehicles, road_graph, traffic_data
        )
        expected = estimate_expected_connected_vehicles(
            bs_with_cap,
            vehicles,
            route_candidates=route_candidates or [],
            lookahead_result=la_result,
            road_nodes=road_nodes,
            road_graph=road_graph,
        )
        demand = calculate_base_station_resource_demand(
            bs_with_cap, traffic_ctx, expected, rb_per_vehicle
        )
        result[bs_id] = demand

    return result
