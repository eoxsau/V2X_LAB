"""
Look-ahead Base Station Scan — forward connectivity prediction.

Distinct from:
  - coverage_risk in route_cost_function  : retrospective (already-traversed path)
  - _future_connectivity in v2x_routing_env: single-step, candidate edges only
  - disconnection_ratio in route_metrics   : whole-path coverage fraction

look_ahead_bs_scan() does a BFS-based multi-hop scan AHEAD of the
current position, weighting near hops more heavily.  The result is the
project's primary "Look-ahead" differentiator — it allows the router to
detect coverage gaps before the vehicle reaches them.

API surface
-----------
look_ahead_bs_scan(node_id, graph, road_nodes, bs_nodes, hops) → LookAheadResult
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# ── Geometry ──────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _best_bs_coverage(
    mid_lat: float,
    mid_lng: float,
    bs_nodes: list[dict],
) -> tuple[Optional[str], float, bool]:
    """
    Return (bs_name_or_id, dist_m_to_best_bs, within_coverage).
    Lightweight: no building intersection, uses coverage_radius_m only.
    """
    best_bs: Optional[dict] = None
    best_dist = float("inf")
    for bs in bs_nodes:
        d = _haversine_m(mid_lat, mid_lng,
                         float(bs.get("lat", 0)), float(bs.get("lng", 0)))
        if d < best_dist:
            best_dist = d
            best_bs = bs
    if best_bs is None:
        return None, float("inf"), False
    cov_r = float(best_bs.get("coverage_radius_m", 400.0))
    name = best_bs.get("name") or str(best_bs.get("id", ""))
    return name, best_dist, best_dist <= cov_r


# ── Result types ───────────────────────────────────────────────────────────────
@dataclass
class HopScan:
    """Coverage snapshot for all edges at exactly ``hop`` hops from origin."""
    hop: int
    edge_count: int
    covered_count: int
    coverage_fraction: float        # covered_count / edge_count (0–1)
    uncovered_edge_ids: list[str]   # edges with no BS within coverage radius


@dataclass
class LookAheadResult:
    """
    Multi-hop look-ahead BS coverage scan result.

    future_connectivity_score: weighted mean coverage across all scanned hops.
        Weighting: nearer hops receive higher weight (hop 1 = weight N, hop N = weight 1).
        Range 0–1; higher = more future coverage guaranteed.

    risk_level: "low" (≥0.85) | "medium" (≥0.60) | "high" (<0.60)
    """
    current_node_id: str
    lookahead_hops: int
    future_connectivity_score: float
    per_hop: list[HopScan]
    total_edges_scanned: int
    total_uncovered: int
    risk_level: str
    scanned_at: str                 # ISO 8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)


# ── Core function ─────────────────────────────────────────────────────────────
def look_ahead_bs_scan(
    current_node_id: str,
    graph: dict,
    road_nodes: dict,
    bs_nodes: list[dict],
    lookahead_hops: int = 3,
    buildings_gdf: Any = None,      # reserved — full obstruction scan (P2)
) -> LookAheadResult:
    """
    Scan BS coverage for the next ``lookahead_hops`` road hops.

    Algorithm
    ---------
    BFS from ``current_node_id``, expanding one layer per hop.
    For every directed edge encountered, compute the midpoint and check
    whether any BS is within its coverage_radius_m.

    Coverage score per hop = covered_edges / total_edges_at_this_hop.
    Overall future_connectivity_score = distance-weighted mean
        (hop 1 has the highest weight; farther hops matter less).

    Args
    ----
    current_node_id : Starting road-graph node (key in road_nodes).
    graph           : {"nodes": {...}, "adjacency": {id: [(to_id, dist_m), ...]}}
                      or 4-tuple entries (to_id, edge_id, dist_m, speed_mps).
    road_nodes      : Pass graph["nodes"] directly.
    bs_nodes        : List of BS dicts with {lat, lng, coverage_radius_m}.
    lookahead_hops  : Number of hops to scan (1–10 recommended; default 3).
    buildings_gdf   : Reserved for full building-obstruction scan (not yet used).

    Returns
    -------
    LookAheadResult with per_hop breakdown, overall score, and risk level.
    """
    adjacency = (
        graph.get("adjacency", graph) if isinstance(graph, dict) else {}
    )

    visited_nodes: set[str] = {current_node_id}
    frontier: set[str] = {current_node_id}
    hop_scans: list[HopScan] = []
    total_edges = 0
    total_uncovered = 0

    for hop in range(1, lookahead_hops + 1):
        next_frontier: set[str] = set()
        hop_edges: list[tuple[str, float, float]] = []  # (edge_id, mid_lat, mid_lng)

        for node_id in frontier:
            from_node = road_nodes.get(node_id, {})
            from_lat = float(from_node.get("lat", 0.0))
            from_lng = float(from_node.get("lng", 0.0))

            for entry in adjacency.get(node_id, []):
                if len(entry) >= 4:
                    to_id, edge_id = str(entry[0]), str(entry[1])
                elif len(entry) >= 2:
                    to_id = str(entry[0])
                    edge_id = f"{node_id}_{to_id}"
                else:
                    continue

                to_node = road_nodes.get(to_id, {})
                mid_lat = (from_lat + float(to_node.get("lat", 0.0))) / 2.0
                mid_lng = (from_lng + float(to_node.get("lng", 0.0))) / 2.0
                hop_edges.append((edge_id, mid_lat, mid_lng))

                if to_id not in visited_nodes:
                    next_frontier.add(to_id)
                    visited_nodes.add(to_id)

        covered_ids: list[str] = []
        uncovered_ids: list[str] = []
        for edge_id, mid_lat, mid_lng in hop_edges:
            _, _, within = _best_bs_coverage(mid_lat, mid_lng, bs_nodes)
            if within:
                covered_ids.append(edge_id)
            else:
                uncovered_ids.append(edge_id)

        n_edges = len(hop_edges)
        n_covered = len(covered_ids)
        cov_frac = (n_covered / n_edges) if n_edges > 0 else 1.0

        hop_scans.append(HopScan(
            hop=hop,
            edge_count=n_edges,
            covered_count=n_covered,
            coverage_fraction=round(cov_frac, 4),
            uncovered_edge_ids=uncovered_ids,
        ))
        total_edges += n_edges
        total_uncovered += len(uncovered_ids)

        frontier = next_frontier
        if not frontier:
            break

    # Weighted mean: weight[i] = (n_actual - i), so hop 1 has the highest weight
    n_actual = len(hop_scans)
    if n_actual == 0:
        score = 1.0
    else:
        weights = [n_actual - i for i in range(n_actual)]
        total_w = sum(weights)
        score = sum(w * s.coverage_fraction for w, s in zip(weights, hop_scans)) / total_w

    if score >= 0.85:
        risk = "low"
    elif score >= 0.60:
        risk = "medium"
    else:
        risk = "high"

    return LookAheadResult(
        current_node_id=current_node_id,
        lookahead_hops=lookahead_hops,
        future_connectivity_score=round(score, 4),
        per_hop=hop_scans,
        total_edges_scanned=total_edges,
        total_uncovered=total_uncovered,
        risk_level=risk,
        scanned_at=datetime.now(timezone.utc).isoformat(),
    )
