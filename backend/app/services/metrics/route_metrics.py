"""
Route performance metrics — unified calculator for algorithm comparison.

Accepts results from any routing algorithm that produces EdgeCostResult objects
(Dijkstra, A*, network-aware, K-shortest, RL) and returns a unified RouteMetrics.

Primary API
-----------
compute_metrics(algorithm, edge_results, bs_nodes, ...)  → RouteMetrics
from_path_cost(algorithm, path_cost, bs_nodes, ...)      → RouteMetrics
from_k_candidates(k_candidates, bs_nodes, ...)           → list[RouteMetrics]
compare_algorithms(metrics_dict)                         → ranking/score dict
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any


# ── Result type ────────────────────────────────────────────────────────────────
@dataclass
class RouteMetrics:
    """
    Unified performance metrics for one routing algorithm result.

    Metric definitions
    ------------------
    total_distance_m        Sum of edge lengths along the route.
    travel_time_s           Sum of edge travel times (length / speed).
    average_latency_ms      Mean predicted BS latency across all edges.
    max_latency_ms          Peak latency on any single edge.
    handover_count          Number of BS transitions along the route.
    disconnection_ratio     Fraction of edges outside all BS coverage radii  (0–1).
    average_bs_load         Mean BS load ratio across traversed edges  (0–1).
    bs_load_variance        Variance of BS load ratios (load consistency metric).
    resource_deficit_ratio  Fraction of BS nodes currently above capacity  (0–1).
    blockage_risk_count     Edges where penetration loss > blockage_threshold_db.
    future_connectivity_risk  Coverage gap in the FINAL 25% of the route.
                            Captures connectivity degradation as the vehicle approaches
                            the destination — distinct from disconnection_ratio which
                            averages across the full path.
    total_cost              Weighted composite cost from route_cost_function.
    execution_time_ms       Algorithm wall-clock time (cost evaluation portion).
    edge_count              Number of road edges in the route.
    blockage_threshold_db   Loss threshold used for blockage_risk_count.
    """
    algorithm: str
    total_distance_m: float
    travel_time_s: float
    average_latency_ms: float
    max_latency_ms: float
    handover_count: int
    disconnection_ratio: float
    average_bs_load: float
    bs_load_variance: float
    resource_deficit_ratio: float
    blockage_risk_count: int
    future_connectivity_risk: float
    total_cost: float
    execution_time_ms: float
    edge_count: int
    blockage_threshold_db: float = 10.0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Constructors ───────────────────────────────────────────────────────────────
def compute_metrics(
    algorithm: str,
    edge_results: list[Any],          # list[EdgeCostResult] from route_cost_function
    bs_nodes: list[dict],
    execution_time_ms: float = 0.0,
    total_cost: float = 0.0,
    blockage_threshold_db: float = 10.0,
) -> RouteMetrics:
    """
    Compute RouteMetrics directly from EdgeCostResult objects.

    Compatible with PathCostResult.edge_results from evaluate_path().
    """
    deficit = _bs_deficit_ratio(bs_nodes)

    if not edge_results:
        return RouteMetrics(
            algorithm=algorithm,
            total_distance_m=0.0,
            travel_time_s=0.0,
            average_latency_ms=0.0,
            max_latency_ms=0.0,
            handover_count=0,
            disconnection_ratio=0.0,
            average_bs_load=0.0,
            bs_load_variance=0.0,
            resource_deficit_ratio=deficit,
            blockage_risk_count=0,
            future_connectivity_risk=0.0,
            total_cost=total_cost,
            execution_time_ms=execution_time_ms,
            edge_count=0,
            blockage_threshold_db=blockage_threshold_db,
        )

    n = len(edge_results)
    latencies = [float(r.latency_ms) for r in edge_results]
    loads = [float(r.load_ratio) for r in edge_results]
    avg_load = sum(loads) / n
    load_var = sum((l - avg_load) ** 2 for l in loads) / n

    n_disconnected = sum(1 for r in edge_results if not r.within_coverage)
    n_blockage = sum(
        1 for r in edge_results if float(r.loss_db) > blockage_threshold_db
    )
    auto_cost = total_cost or sum(float(r.total_cost) for r in edge_results)

    # future_connectivity_risk: coverage gap in the LAST 25% of the route.
    # Distinct from disconnection_ratio (whole-path average).
    quarter_start = max(0, n - max(1, n // 4))
    last_quarter = edge_results[quarter_start:]
    n_future_disc = sum(1 for r in last_quarter if not r.within_coverage)
    future_conn_risk = n_future_disc / len(last_quarter) if last_quarter else 0.0

    return RouteMetrics(
        algorithm=algorithm,
        total_distance_m=round(sum(float(r.distance_m) for r in edge_results), 2),
        travel_time_s=round(sum(float(r.travel_time_s) for r in edge_results), 2),
        average_latency_ms=round(sum(latencies) / n, 2),
        max_latency_ms=round(max(latencies), 2),
        handover_count=sum(1 for r in edge_results if r.handover_occurred),
        disconnection_ratio=round(n_disconnected / n, 4),
        average_bs_load=round(avg_load, 4),
        bs_load_variance=round(load_var, 6),
        resource_deficit_ratio=round(deficit, 4),
        blockage_risk_count=n_blockage,
        future_connectivity_risk=round(future_conn_risk, 4),
        total_cost=round(auto_cost, 4),
        execution_time_ms=round(execution_time_ms, 2),
        edge_count=n,
        blockage_threshold_db=blockage_threshold_db,
    )


def from_path_cost(
    algorithm: str,
    path_cost: Any,                   # PathCostResult from route_cost_function
    bs_nodes: list[dict],
    execution_time_ms: float = 0.0,
    blockage_threshold_db: float = 10.0,
) -> RouteMetrics:
    """
    Build RouteMetrics from an existing PathCostResult.
    Avoids re-computing any network costs.
    """
    return compute_metrics(
        algorithm=algorithm,
        edge_results=path_cost.edge_results,
        bs_nodes=bs_nodes,
        execution_time_ms=execution_time_ms,
        total_cost=path_cost.total_cost,
        blockage_threshold_db=blockage_threshold_db,
    )


def from_k_candidates(
    k_candidates: list[Any],          # list[KPathCandidate] from route_cost_function
    bs_nodes: list[dict],
    execution_time_ms: float = 0.0,
    blockage_threshold_db: float = 10.0,
) -> list[RouteMetrics]:
    """
    Compute metrics for each K-path candidate.
    Returns list in the same order as k_candidates (rank 0 first).
    """
    results: list[RouteMetrics] = []
    for c in k_candidates:
        algo_name = f"k_path_rank_{c.rank}"
        if c.path_cost_result is not None:
            m = from_path_cost(
                algo_name,
                c.path_cost_result,
                bs_nodes,
                execution_time_ms,
                blockage_threshold_db,
            )
        else:
            # Fallback: build from KPathCandidate summary fields
            deficit = _bs_deficit_ratio(bs_nodes)
            m = RouteMetrics(
                algorithm=algo_name,
                total_distance_m=c.total_distance_m,
                travel_time_s=c.total_travel_time_s,
                average_latency_ms=c.avg_latency_ms,
                max_latency_ms=c.max_latency_ms,
                handover_count=c.handover_count,
                disconnection_ratio=c.coverage_risk,
                average_bs_load=0.0,
                bs_load_variance=0.0,
                resource_deficit_ratio=deficit,
                blockage_risk_count=0,
                future_connectivity_risk=c.coverage_risk,
                total_cost=c.total_cost,
                execution_time_ms=execution_time_ms,
                edge_count=len(c.path),
                blockage_threshold_db=blockage_threshold_db,
            )
        results.append(m)
    return results


# ── Comparison ─────────────────────────────────────────────────────────────────
# All 13 user-facing metrics, lower = better for all of them
_LOWER_IS_BETTER = frozenset({
    "total_distance_m",
    "travel_time_s",
    "average_latency_ms",
    "max_latency_ms",
    "handover_count",
    "disconnection_ratio",
    "average_bs_load",
    "bs_load_variance",
    "resource_deficit_ratio",
    "blockage_risk_count",
    "future_connectivity_risk",
    "total_cost",
    "execution_time_ms",
})

_COMPARE_METRICS = list(_LOWER_IS_BETTER)   # deterministic order via frozenset iteration avoided
_COMPARE_METRICS = [
    "total_distance_m", "travel_time_s", "average_latency_ms", "max_latency_ms",
    "handover_count", "disconnection_ratio", "average_bs_load", "bs_load_variance",
    "resource_deficit_ratio", "blockage_risk_count", "future_connectivity_risk",
    "total_cost", "execution_time_ms",
]


def compare_algorithms(metrics: dict[str, "RouteMetrics"]) -> dict:
    """
    Rank algorithms across all 13 metrics.

    Returns
    -------
    dict with keys:
      rankings         {metric: [best_algo, ..., worst_algo]}
      scores           {algo: {metric: normalised_score_0_to_1}}
                       1.0 = best on that metric, 0.0 = worst
      best_per_metric  {metric: algo_name}
      summary_rank     {algo: float}  mean normalised score; higher = better overall
    """
    if not metrics:
        return {}

    algos = list(metrics.keys())
    rankings: dict[str, list[str]] = {}
    best_per_metric: dict[str, str] = {}
    scores: dict[str, dict[str, float]] = {a: {} for a in algos}

    for metric in _COMPARE_METRICS:
        vals = {a: float(getattr(metrics[a], metric, 0.0)) for a in algos}
        # lower raw value → better rank for all metrics in this set
        ranked = sorted(algos, key=lambda a: vals[a])
        rankings[metric] = ranked
        best_per_metric[metric] = ranked[0]

        mn, mx = min(vals.values()), max(vals.values())
        span = mx - mn
        for a in algos:
            if span < 1e-12:
                norm = 1.0
            else:
                # lower raw → higher normalised score
                norm = 1.0 - (vals[a] - mn) / span
            scores[a][metric] = round(norm, 4)

    summary_rank = {
        a: round(sum(scores[a].values()) / len(_COMPARE_METRICS), 4)
        for a in algos
    }

    return {
        "rankings": rankings,
        "scores": scores,
        "best_per_metric": best_per_metric,
        "summary_rank": summary_rank,
    }


# ── Internal helpers ───────────────────────────────────────────────────────────
def _bs_deficit_ratio(bs_nodes: list[dict]) -> float:
    """Fraction of BS nodes currently at or above capacity."""
    if not bs_nodes:
        return 0.0
    n_over = sum(
        1 for bs in bs_nodes
        if float(bs.get("load", bs.get("current_load", 0.0)))
           >= float(bs.get("capacity", 100.0))
    )
    return n_over / len(bs_nodes)
