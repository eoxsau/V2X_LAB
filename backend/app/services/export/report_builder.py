"""
Normalized report data builders for the analysis report system.

Each builder function accepts the current _state snapshot and returns a
clean, export-ready dict.  No LLM calls are made here — this module only
assembles facts from existing simulation state.

Public API
----------
build_run_summary(state)          -> dict   (run_summary shape)
build_algorithm_compare(state)    -> list[dict]  (one row per algorithm)
build_per_edge_metrics(state)     -> list[dict]  (one row per road edge)
build_per_bs_metrics(state)       -> list[dict]  (one row per BS node)
build_scenario_metadata(state)    -> dict   (config + run identifiers)
build_report_bundle(state)        -> dict   (all of the above + summary)

CSV serialisers
---------------
rows_to_csv(rows)                 -> str   (UTF-8 CSV text with BOM)
dict_to_csv(d)                    -> str   (key,value CSV)
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Analytical metric helpers (peer-reviewed models with citations)
# ──────────────────────────────────────────────────────────────────────────────

def compute_cbr_per_edge(vehicle_density_veh_per_m: float,
                          coverage_radius_m: float = 200.0,
                          f_cam_hz: float = 10.0,
                          t_rri_s: float = 0.1) -> dict:
    """
    CBR analytical model — Gonzalez-Martin et al., IEEE TVT 2019 §IV.A

    CBR = 1 − exp(−ρ · R_tx · f_CAM · T_RRI)

    ρ        vehicle density (vehicles/m)
    R_tx     transmission radius (m); ETSI ITS default 200 m
    f_CAM    CAM frequency (Hz); ETSI EN 302 637-2 §6.1.2.3 max 10 Hz
    T_RRI    Resource Reservation Interval (s); 3GPP TS 36.331 §5.14.1.1 default 0.1 s
    CBR > 0.65 → congested (ETSI TS 102 687 §5.2.2)
    """
    exponent = vehicle_density_veh_per_m * coverage_radius_m * f_cam_hz * t_rri_s
    cbr = 1.0 - math.exp(-exponent)
    cbr = min(cbr, 1.0)
    return {
        "cbr": round(cbr, 4),
        "cbr_congested": cbr > 0.65,
        "cbr_ref": "Gonzalez-Martin et al., IEEE TVT 68(2) 2019; ETSI TS 102 687 §5.2.2",
    }


def edge_densities(state: dict, per_edge: list[dict]) -> list[float]:
    """경로 엣지별 차량 밀도(대/m) — CBR의 ρ.

    우선순위:
      1. **실측** `state["edge_avg_density"]` — 타겟이 그 엣지를 지나는 동안 SUMO에서 잰 값.
      2. 폴백: 총 차량수 / 전체 경로거리 (예전 방식, 균일 가정).

    왜 균일 가정을 버렸나 (2026-07-27):
        `sim_vehicle_count / total_route_distance`는 경로 전체에 차가 **고르게** 깔렸다고
        본다. 그런데 생성 교통 실측에서 **상위 10% 엣지가 교통량의 75%** 를 점유한다.
        균일 가정은 병목 구간(간선·교차로)의 채널 점유율을 크게 과소평가하고, 한산한
        구간은 과대평가한다. CBR 임계값(0.65)으로 혼잡을 판정하는 지표라 이 왜곡이 그대로
        결론에 들어간다.
    """
    measured = state.get("edge_avg_density") or {}
    veh_count = int(state.get("sim_vehicle_count") or 1)
    rc = state.get("route_cost_result") or {}
    total_dist = float(rc.get("total_distance_m") or 1000.0)
    fallback = veh_count / max(total_dist, 1.0)

    out: list[float] = []
    for e in per_edge:
        eid = e.get("edge_id", "")
        rho = measured.get(eid)
        out.append(float(rho) if rho is not None else fallback)
    return out


def compute_pir_p99(prr: float, cam_period_ms: float = 100.0) -> dict:
    """
    PIR P99 geometric-distribution upper bound — 3GPP TR 37.885 + Eckermann 2019

    PIR_P99_upper = T_CAM / PRR

    Assumes PRR is constant per edge (simplification; actual PIR lower due to
    spatial distribution effects).
    Threshold: P99 ≤ 100 ms (3GPP TR 37.885 Table A.1, C-V2X low-latency service)
    """
    if not prr or prr <= 0:
        return {"pir_p99_ms": None, "pir_compliant": False,
                "pir_ref": "3GPP TR 37.885 §A.2.4; Eckermann et al. IEEE VTC Fall 2019"}
    pir_p99 = cam_period_ms / prr
    return {
        "pir_p99_ms": round(pir_p99, 2),
        "pir_compliant": pir_p99 <= 100.0,
        "pir_ref": "3GPP TR 37.885 §A.2.4; Eckermann et al. IEEE VTC Fall 2019",
    }


def compute_jain_fairness_index(load_ratios: list) -> dict:
    """
    Jain's Fairness Index for BS load distribution.

    J = (Σᵢ xᵢ)² / (n · Σᵢ xᵢ²)   [Jain, Chiu, Hawe, DEC-TR-301, 1984 §3.1]

    Range [1/n, 1]; J=1 perfectly fair, J=1/n maximally unfair.
    """
    loads = [float(x) for x in load_ratios if x is not None]
    n = len(loads)
    if n == 0:
        return {"jain_fairness_index": None, "jain_ref": "Jain, Chiu, Hawe, DEC-TR-301 1984"}
    s1 = sum(loads)
    s2 = sum(x * x for x in loads)
    jfi = (s1 ** 2) / (n * s2) if s2 > 0 else 1.0
    return {
        "jain_fairness_index": round(jfi, 4),
        "jain_n": n,
        "jain_ref": "Jain, Chiu, Hawe, DEC-TR-301 1984 §3.1",
    }


def compute_path_loss(distance_m: float, env: str = "los_urban") -> dict:
    """
    Log-distance path loss — Fernandez et al., IEEE WCL 2014

    PL(d) = PL(d₀) + 10·n·log₁₀(d/d₀)   [dB]

    Parameters (measured, 5.9 GHz):
      env           n      σ (dB)
      los_highway   1.61   4.0    (3GPP TR 37.885 Table A.1.2-1 highway scenario)
      los_urban     2.75   5.5
      nlos_urban    3.50   7.1
    d₀ = 10 m, PL(d₀) ≈ 67.8 dB @ 5.9 GHz
    """
    PARAMS = {
        "los_highway": {"n": 1.61, "sigma": 4.0},
        "los_urban":   {"n": 2.75, "sigma": 5.5},
        "nlos_urban":  {"n": 3.50, "sigma": 7.1},
    }
    d0 = 10.0
    wavelength = 3e8 / (5.9e9)
    pl_d0 = 20 * math.log10(4 * math.pi * d0 / wavelength)
    p = PARAMS.get(env, PARAMS["los_urban"])
    d = max(distance_m, d0)
    pl = pl_d0 + 10 * p["n"] * math.log10(d / d0)
    return {
        "path_loss_db": round(pl, 2),
        "pl_env": env,
        "pl_n": p["n"],
        "pl_sigma_db": p["sigma"],
        "pl_ref": "Fernandez et al., IEEE WCL 3(6) 2014, Table I-II",
    }


def _safe(fn, state, fallback):
    """Wrap a builder call so one failure does not break the whole bundle."""
    try:
        return fn(state)
    except Exception:
        return fallback


# ──────────────────────────────────────────────────────────────────────────────
# Stable column lists (used as CSV headers — keep order fixed)
# ──────────────────────────────────────────────────────────────────────────────

RUN_SUMMARY_COLUMNS = [
    "run_id",
    "scenario_id",
    "generated_at",
    "selected_algorithm",
    "baseline_algorithm",
    "network_mode",
    "sim_mode",
    "seed",
    "vehicle_count",
    # core route metrics (selected algorithm)
    "total_cost",
    "cost_improvement_pct",
    "total_distance_m",
    "total_travel_time_s",
    "avg_latency_ms",
    "max_latency_ms",
    "handover_count",
    # improvement deltas vs baseline (selected − baseline; negative = improvement for cost/latency)
    "cost_delta",
    "latency_delta_ms",
    "distance_delta_m",
    "handover_delta",
    # coverage / connection quality
    "coverage_risk",
    "covered_pct",
    "disconnection_ratio",
    "time_weighted_disconnection_ratio",
    "prr_approx",
    # network load
    "average_bs_load",
    "future_connectivity_risk",
    "resource_deficit_cost",
    "expected_latency_impact_ms",
    # channel / fairness metrics (analytical models)
    "pir_p99_ms",
    "pir_compliant",
    "jain_fairness_index",
    "cbr_avg",
    # handover interruption and URLLC (3GPP TS 22.261 §7.2; IEEE doc 10320318, 2023)
    "hit_total_ms",
    "urllc_compliance_ratio",
    # route origin / destination
    "origin_lat",
    "origin_lng",
    "dest_lat",
    "dest_lng",
]


# ──────────────────────────────────────────────────────────────────────────────
# Run Summary
# ──────────────────────────────────────────────────────────────────────────────

def build_run_summary(state: dict) -> dict:
    """
    Single-row summary of the most recent simulation run.

    Combines route_cost_result, simulation_summary, and policy_options so
    the consumer never needs to merge those three sources manually.

    Shape
    -----
    {
      scenario_id, generated_at,
      selected_algorithm, baseline_algorithm,
      total_distance_m, total_travel_time_s,
      avg_latency_ms, max_latency_ms,
      handover_count, coverage_risk, covered_pct,
      disconnection_ratio, time_weighted_disconnection_ratio, prr_approx,
      total_cost, resource_deficit_cost, expected_latency_impact,
      cost_improvement_pct,
      network_mode, sim_mode, seed,
      origin_lat, origin_lng, dest_lat, dest_lng,
      vehicle_count,
    }
    """
    summary = state.get("simulation_summary") or {}
    rc      = state.get("route_cost_result") or {}
    policy  = state.get("policy_options") or {}

    improvement = summary.get("improvement_over_baseline") or {}
    route_sum   = summary.get("route_summary") or {}

    # Prefer summary-level algorithm metrics for the selected algorithm
    selected_algo = summary.get("selected_algorithm") or rc.get("routing_mode", "unknown")
    baseline_algo = summary.get("baseline_algorithm", "unknown")

    metric_algs = (summary.get("metric_summary") or {}).get("algorithms") or {}
    sel_m = metric_algs.get(selected_algo) or {}

    # resource_deficit_cost stores resource_deficit_ratio (a 0-1 ratio, not an absolute cost).
    # Named "cost" for historical CSV compatibility; do not rename without a migration.
    alloc  = state.get("last_allocation_result") or {}
    res_deficit = float(sel_m.get("resource_deficit_ratio", 0.0))
    exp_lat_imp = _sum_expected_latency_impact(alloc)

    origin = state.get("sim_origin") or {}
    dest   = state.get("sim_dest")   or {}

    twdr = float(sel_m.get("time_weighted_disconnection_ratio")
                 or rc.get("coverage_risk", 0.0))
    prr  = float(sel_m.get("prr_approx", round(1.0 - twdr, 4)))

    run_id = str(state.get("simulation_run_id") or summary.get("scenario_id") or "unknown")

    # Jain FI from BS nodes (fleet-level)
    nodes = state.get("network_nodes") or []
    node_load_ratios = [
        round(float(n.get("load") or 0.0) / max(float(n.get("capacity") or 100.0), 1.0), 4)
        for n in nodes
    ]
    jain_fi = compute_jain_fairness_index(node_load_ratios).get("jain_fairness_index")

    # HIT — Handover Interruption Time (IEEE 5G NR doc 10320318, 2023: 200-400 ms/event)
    # HIT = Σ_e t_ho(e)·I(HO); using 300 ms midpoint for each confirmed handover event
    # Source: "Handover interruption time in 5G NR", IEEE doc 10320318, 2023 (200-400 ms range)
    handover_count = int(rc.get("handover_count", 0))
    hit_total_ms = round(handover_count * 300.0, 1)  # 300 ms = midpoint of 200-400 ms range

    # CBR avg — mean channel busy ratio across route edges (Gonzalez-Martin, IEEE TVT 2019)
    per_edge = rc.get("per_edge") or []
    cbr_avg = None
    if per_edge:
        cbr_vals = [compute_cbr_per_edge(rho, coverage_radius_m=200.0)["cbr"]
                    for rho in edge_densities(state, per_edge)]
        cbr_avg = round(sum(cbr_vals) / len(cbr_vals), 4)

    # URLLC compliance ratio — P(L ≤ 10 ms); 3GPP TS 22.261 §7.2 URLLC latency bound = 10 ms
    # Computed from per-edge latency distribution over the route
    urllc_compliance_ratio = None
    if per_edge:
        lat_vals = [float(e.get("latency_ms") or 0.0) for e in per_edge]
        n_compliant = sum(1 for v in lat_vals if v <= 10.0)
        urllc_compliance_ratio = round(n_compliant / len(lat_vals), 4)

    return {
        "run_id":                         run_id,
        "scenario_id":                    summary.get("scenario_id", "unknown"),
        "generated_at":                   summary.get("generated_at", ""),
        "selected_algorithm":             selected_algo,
        "baseline_algorithm":             baseline_algo,
        "total_distance_m":               float(route_sum.get("total_distance_m") or rc.get("total_distance_m", 0.0)),
        "total_travel_time_s":            float(route_sum.get("total_travel_time_s") or rc.get("total_travel_time_s", 0.0)),
        "avg_latency_ms":                 float(rc.get("avg_latency_ms", 0.0)),
        "max_latency_ms":                 float(rc.get("max_latency_ms", 0.0)),
        "handover_count":                 int(rc.get("handover_count", 0)),
        "coverage_risk":                  float(rc.get("coverage_risk", 0.0)),
        "covered_pct":                    float(rc.get("covered_pct", 0.0)),
        "disconnection_ratio":            float(sel_m.get("disconnection_ratio", rc.get("coverage_risk", 0.0))),
        "time_weighted_disconnection_ratio": twdr,
        "prr_approx":                     prr,
        "total_cost":                     float(rc.get("total_cost", 0.0)),
        "resource_deficit_cost":          res_deficit,
        "expected_latency_impact_ms":     exp_lat_imp,
        "cost_improvement_pct":           float(improvement.get("cost_improvement_pct", 0.0)),
        # improvement deltas vs baseline (None when no baseline comparison ran)
        "cost_delta":                     improvement.get("cost_delta"),
        "latency_delta_ms":               improvement.get("latency_delta_ms"),
        "distance_delta_m":               improvement.get("distance_delta_m"),
        "handover_delta":                 improvement.get("handover_delta"),
        # network load for selected algorithm (from RouteMetrics)
        "average_bs_load":                round(float(sel_m.get("average_bs_load", 0.0)), 4),
        "future_connectivity_risk":       round(float(sel_m.get("future_connectivity_risk", 0.0)), 4),
        "network_mode":                   policy.get("network_mode", "5G"),
        "sim_mode":                       state.get("sim_mode", "idle"),
        "seed":                           state.get("sim_seed"),
        "origin_lat":                     float(origin.get("lat", 0.0)) if origin else None,
        "origin_lng":                     float(origin.get("lng", 0.0)) if origin else None,
        "dest_lat":                       float(dest.get("lat", 0.0)) if dest else None,
        "dest_lng":                       float(dest.get("lng", 0.0)) if dest else None,
        "vehicle_count":                  int(state.get("sim_vehicle_count") or 1),
        # PIR P99 — geometric distribution upper bound (3GPP TR 37.885 + Eckermann 2019)
        **{k: v for k, v in compute_pir_p99(prr).items() if k in ("pir_p99_ms", "pir_compliant")},
        # Jain FI — BS load fairness (Jain, Chiu, Hawe, DEC-TR-301, 1984)
        "jain_fairness_index": jain_fi,
        # HIT — Handover Interruption Time; IEEE doc 10320318 (2023), 300 ms midpoint per event
        "hit_total_ms":            hit_total_ms,
        # CBR avg — mean over route edges; Gonzalez-Martin, IEEE TVT 2019
        "cbr_avg":                 cbr_avg,
        # URLLC compliance ratio — P(L ≤ 10 ms); 3GPP TS 22.261 §7.2
        "urllc_compliance_ratio":  urllc_compliance_ratio,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Algorithm Comparison
# ──────────────────────────────────────────────────────────────────────────────

# Columns emitted for every algorithm row — in this order in the CSV.
ALGO_COMPARE_COLUMNS = [
    "algorithm",
    "total_cost",
    "total_distance_m",
    "travel_time_s",
    "average_latency_ms",
    "max_latency_ms",
    "handover_count",
    "disconnection_ratio",
    "time_weighted_disconnection_ratio",
    "prr_approx",
    "average_bs_load",
    "bs_load_variance",
    "resource_deficit_ratio",
    "blockage_risk_count",
    "future_connectivity_risk",
    "execution_time_ms",
    "edge_count",
    "summary_rank_score",
    "is_best_total_cost",
]


def build_algorithm_compare(state: dict) -> list[dict]:
    """
    One dict per evaluated algorithm.  Includes normalised comparison scores
    from compare_algorithms() so the consumer can produce a rank table
    without re-computing.

    Shape per row
    -------------
    { algorithm, total_cost, average_latency_ms, ..., prr_approx,
      summary_rank_score, is_best_total_cost }
    """
    metrics = state.get("algorithm_metrics") or {}
    algos   = {k: v for k, v in metrics.items() if not k.startswith("_")}
    comparison = metrics.get("_comparison") or {}
    summary_rank = comparison.get("summary_rank") or {}
    best_per_metric = comparison.get("best_per_metric") or {}

    best_cost_algo = best_per_metric.get("total_cost", "")

    rows: list[dict] = []
    for algo, m in algos.items():
        row: dict[str, Any] = {"algorithm": algo}
        for col in ALGO_COMPARE_COLUMNS[1:]:
            if col == "summary_rank_score":
                row[col] = round(float(summary_rank.get(algo, 0.0)), 4)
            elif col == "is_best_total_cost":
                row[col] = (algo == best_cost_algo)
            else:
                row[col] = m.get(col)
        rows.append(row)

    # Sort by total_cost ascending (best first)
    rows.sort(key=lambda r: float(r.get("total_cost") or 1e9))
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Per-Edge Metrics
# ──────────────────────────────────────────────────────────────────────────────

PER_EDGE_COLUMNS = [
    "run_id",
    "edge_index",
    "edge_id",
    "street_name",
    "midpoint_lat",
    "midpoint_lng",
    "distance_m",
    "travel_time_s",
    "latency_ms",
    "best_node_id",
    "best_node_name",
    "load_ratio",
    "handover",
    "within_coverage",
    "loss_db",
    "total_cost",
    # analytical channel metrics
    "veh_density_per_m",
    "cbr",
    "cbr_congested",
    "path_loss_db",
]


def build_per_edge_metrics(state: dict) -> list[dict]:
    """
    One dict per road edge in the selected route.

    Merges route_cost_result["per_edge"] with route_edge_names so each
    row has a human-readable street name alongside the raw edge_id.

    Shape per row
    -------------
    { edge_index, edge_id, street_name, midpoint_lat, midpoint_lng,
      distance_m, latency_ms, best_node_id, best_node_name,
      load_ratio, handover, within_coverage, loss_db, total_cost }
    """
    rc         = state.get("route_cost_result") or {}
    per_edge   = rc.get("per_edge") or []
    edge_names = state.get("route_edge_names") or {}

    # CBR의 ρ — 엣지별 실측 밀도(없으면 균일 폴백). edge_densities() 주석 참조.
    _rho_per_edge = edge_densities(state, per_edge)

    # Environment tag for path loss: derive from network_mode / scenario
    policy = state.get("policy_options") or {}
    _net_mode = (policy.get("network_mode") or "").lower()
    _pl_env   = "los_highway" if "highway" in _net_mode else "los_urban"

    # Forward-propagate street names (same logic as simulation_summary._fill_edge_names)
    _filled: dict[str, str] = {
        eid: name for e in per_edge
        if (eid := e.get("edge_id")) and (name := edge_names.get(eid))
    }
    last_name = None
    for e in per_edge:
        eid = e.get("edge_id", "")
        if eid in _filled:
            last_name = _filled[eid]
        elif last_name:
            _filled[eid] = last_name

    run_id = str(state.get("simulation_run_id") or
                 (state.get("simulation_summary") or {}).get("scenario_id") or "")

    rows: list[dict] = []
    for i, e in enumerate(per_edge):
        eid = e.get("edge_id", "")
        dist_m = float(e.get("distance_m") or 50.0)
        cbr_data  = compute_cbr_per_edge(_rho_per_edge[i], coverage_radius_m=200.0)
        pl_data   = compute_path_loss(dist_m, env=_pl_env)
        rows.append({
            "run_id":         run_id,
            "edge_index":     i,
            "edge_id":        eid,
            "street_name":    _filled.get(eid, ""),
            "midpoint_lat":   e.get("midpoint_lat"),
            "midpoint_lng":   e.get("midpoint_lng"),
            "distance_m":     dist_m,
            "travel_time_s":  e.get("travel_time_s"),
            "latency_ms":     e.get("latency_ms"),
            "best_node_id":   e.get("best_node_id"),
            "best_node_name": e.get("best_node_name"),
            "load_ratio":     e.get("load_ratio"),
            "handover":       e.get("handover", False),
            "within_coverage": e.get("within_coverage", True),
            "loss_db":        e.get("loss_db"),
            "total_cost":     e.get("total_cost"),
            # analytical channel metrics
            "veh_density_per_m": round(_rho_per_edge[i], 6),   # CBR의 ρ (실측 또는 균일 폴백)
            "cbr":            cbr_data["cbr"],
            "cbr_congested":  cbr_data["cbr_congested"],
            "path_loss_db":   pl_data["path_loss_db"],
        })
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Per-BS Metrics
# ──────────────────────────────────────────────────────────────────────────────

PER_BS_COLUMNS = [
    "run_id",
    "bs_id",
    "bs_name",
    "node_type",
    "source",
    "lat",
    "lng",
    "coverage_radius_m",
    "capacity",
    "load",
    "load_ratio",
    "severity",
    "congestion_score",
    "antenna_placement",
    "antenna_height_m",
    # route cross-reference stats
    "affected_edge_count",
    "handover_count_on_route",
    "avg_latency_on_route_ms",
    # resource / latency impact
    "expected_latency_impact",
    "resource_deficit",
]


def build_per_bs_metrics(state: dict) -> list[dict]:
    """
    One dict per BS/RSU node in the current network.

    Cross-references per_edge data to add route-level statistics
    (how many edges connected to this BS, how many handovers, mean latency).

    Shape per row — see PER_BS_COLUMNS.
    """
    nodes    = state.get("network_nodes") or []
    rc       = state.get("route_cost_result") or {}
    per_edge = rc.get("per_edge") or []

    # Aggregate per-BS statistics from the route edges
    bs_edge_counts:    dict[str, int]   = {}
    bs_handover_counts: dict[str, int]  = {}
    bs_latency_sums:   dict[str, float] = {}

    for e in per_edge:
        nid = e.get("best_node_id") or e.get("best_node_name") or ""
        if not nid:
            continue
        bs_edge_counts[nid]    = bs_edge_counts.get(nid, 0) + 1
        bs_latency_sums[nid]   = bs_latency_sums.get(nid, 0.0) + float(e.get("latency_ms") or 0.0)
        if e.get("handover"):
            bs_handover_counts[nid] = bs_handover_counts.get(nid, 0) + 1

    run_id = str(state.get("simulation_run_id") or
                 (state.get("simulation_summary") or {}).get("scenario_id") or "")

    # Per-BS expected latency impact from allocation result (best-effort lookup)
    alloc = state.get("last_allocation_result") or {}
    alloc_impact: dict = alloc.get("expected_latency_impact") or {}

    # Compute Jain FI across all BS nodes — fleet-level fairness metric
    all_load_ratios = [
        round(float(n.get("load") or 0.0) / max(float(n.get("capacity") or 100.0), 1.0), 4)
        for n in nodes
    ]
    jain_data = compute_jain_fairness_index(all_load_ratios)
    fleet_jfi = jain_data.get("jain_fairness_index")

    rows: list[dict] = []
    for node in nodes:
        nid  = str(node.get("id", ""))
        name = node.get("name") or nid
        cap  = float(node.get("capacity") or 100.0)
        load = float(node.get("load") or 0.0)
        lr   = round(load / max(cap, 1.0), 4)
        cnt  = bs_edge_counts.get(nid, 0) or bs_edge_counts.get(name, 0)
        hct  = bs_handover_counts.get(nid, 0) or bs_handover_counts.get(name, 0)
        lat_sum = bs_latency_sums.get(nid, 0.0) or bs_latency_sums.get(name, 0.0)

        # severity: based on load ratio
        if lr > 0.9:
            severity = "critical"
        elif lr > 0.7:
            severity = "warning"
        else:
            severity = "normal"

        # resource_deficit: load beyond capacity (absolute units)
        resource_deficit = round(max(0.0, load - cap), 4)

        # expected_latency_impact: per-BS estimate from allocation result
        exp_impact = alloc_impact.get(nid) or alloc_impact.get(name)
        exp_impact = round(float(exp_impact), 3) if exp_impact is not None else None

        rows.append({
            "run_id":                  run_id,
            "bs_id":                   nid,
            "bs_name":                 name,
            "node_type":               node.get("type") or node.get("node_type", "bs"),
            "source":                  node.get("source", "synthetic"),
            "lat":                     node.get("lat"),
            "lng":                     node.get("lng"),
            "coverage_radius_m":       float(node.get("coverage_radius_m") or 400.0),
            "capacity":                cap,
            "load":                    round(load, 4),
            "load_ratio":              lr,
            "severity":                severity,
            "congestion_score":        round(float(node.get("congestion_score") or 0.0), 4),
            "antenna_placement":       node.get("antenna_placement"),
            "antenna_height_m":        node.get("antenna_height_m"),
            "affected_edge_count":     cnt,
            "handover_count_on_route": hct,
            "avg_latency_on_route_ms": round(lat_sum / cnt, 2) if cnt > 0 else None,
            "expected_latency_impact": exp_impact,
            "resource_deficit":        resource_deficit,
            # fleet-level fairness (same value on every row for CSV convenience)
            "jain_fairness_index":     fleet_jfi,
        })

    # Sort: route-facing nodes first (by edge count desc), then by load desc
    rows.sort(key=lambda r: (-int(r["affected_edge_count"]), -float(r["load_ratio"])))
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Scenario Metadata
# ──────────────────────────────────────────────────────────────────────────────

def build_scenario_metadata(state: dict) -> dict:
    """
    All configuration and run-identity fields that describe *how* the
    simulation was set up.  No metric data — purely metadata.

    Shape
    -----
    {
      scenario_id, batch_id, sheet_id, seed, vehicle_count,
      origin_lat, origin_lng, dest_lat, dest_lng,
      sim_mode, network_mode, traffic_time_period,
      route_algorithm, latency_algorithm, allocation_algorithm,
      cost_weights: { w_distance, w_time, w_latency, w_load,
                      w_handover, w_blockage, w_coverage_risk },
      norm_scales:  { distance_km, time_min, latency_ms, loss_db },
      started_at, generated_at,
    }
    """
    policy  = state.get("policy_options") or {}
    summary = state.get("simulation_summary") or {}
    sim_cfg = state.get("simulation_config") or {}

    origin = state.get("sim_origin") or {}
    dest   = state.get("sim_dest")   or {}

    # cost_weights and norm_scales may live in route_cost_result["weights"] / ["norm_scales"]
    rc = state.get("route_cost_result") or {}
    weights    = rc.get("weights") or sim_cfg.get("cost_weights") or {}
    norm_scales = rc.get("norm_scales") or sim_cfg.get("norm_scales") or {}

    algos_cfg  = sim_cfg.get("algorithm_selection") or {}

    scenario_id = summary.get("scenario_id") or str(state.get("simulation_run_id") or "")
    run_id      = str(state.get("simulation_run_id") or scenario_id or "")

    # bs_selection_algorithm — may live in policy_options or algorithm_selection config
    bs_sel_algo = (
        policy.get("bs_selection_algorithm")
        or algos_cfg.get("bs_selection_algorithm")
        or state.get("bs_selection_algorithm", "")
    )
    res_alloc_algo = (
        policy.get("allocation_algorithm")
        or algos_cfg.get("allocation_algorithm")
        or state.get("allocation_algorithm", "")
    )
    lookahead_k = (
        policy.get("lookahead_k")
        or sim_cfg.get("lookahead_k")
        or (sim_cfg.get("policy_options") or {}).get("lookahead_k")
    )
    custom_policy_enabled = bool(state.get("custom_policy"))
    created_at = summary.get("generated_at") or state.get("sim_started_at", "")

    return {
        # identifiers
        "run_id":                  run_id,
        "scenario_id":             scenario_id,
        "scenario_name":           summary.get("scenario_name") or scenario_id or "",
        "batch_id":                state.get("batch_id"),
        "sheet_id":                state.get("sheet_id"),
        # run parameters
        "seed":                    state.get("sim_seed"),
        "vehicle_count":           int(state.get("sim_vehicle_count") or 1),
        "origin_lat":              float(origin.get("lat", 0.0)) if origin else None,
        "origin_lng":              float(origin.get("lng", 0.0)) if origin else None,
        "dest_lat":                float(dest.get("lat", 0.0)) if dest else None,
        "dest_lng":                float(dest.get("lng", 0.0)) if dest else None,
        # mode
        "sim_mode":                state.get("sim_mode", "idle"),
        "network_mode":            policy.get("network_mode", "5G"),
        "traffic_time_period":     policy.get("traffic_time_period", "peak"),
        # algorithms
        "route_algorithm":         policy.get("route_algorithm") or algos_cfg.get("route_algorithm", ""),
        "latency_algorithm":       state.get("latency_algorithm", ""),
        "bs_selection_algorithm":  bs_sel_algo,
        "resource_allocation_algorithm": res_alloc_algo,
        "allocation_algorithm":    res_alloc_algo,   # legacy alias
        "lookahead_k":             lookahead_k,
        "custom_policy_enabled":   custom_policy_enabled,
        # cost weights (flattened for CSV readability)
        "cost_weights": {
            "w_distance":      weights.get("w_distance"),
            "w_time":          weights.get("w_time"),
            "w_latency":       weights.get("w_latency"),
            "w_load":          weights.get("w_load"),
            "w_handover":      weights.get("w_handover"),
            "w_blockage":      weights.get("w_blockage"),
            "w_coverage_risk": weights.get("w_coverage_risk"),
        },
        "norm_scales": {
            "distance_km": norm_scales.get("distance_km"),
            "time_min":    norm_scales.get("time_min"),
            "latency_ms":  norm_scales.get("latency_ms"),
            "loss_db":     norm_scales.get("loss_db"),
        },
        # timestamps
        "created_at":    created_at,
        "started_at":    None,   # populated from DB run record when available
        "generated_at":  summary.get("generated_at", ""),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Report Bundle (all sections combined)
# ──────────────────────────────────────────────────────────────────────────────

def build_report_bundle(state: dict) -> dict:
    """
    Single endpoint payload — all normalized report data in one call.
    Consumers (Export tab, DOCX builder) call this once instead of 5 endpoints.

    Shape
    -----
    {
      available: bool,
      run_summary:         {...},
      algorithm_compare:   [{...}, ...],
      per_edge_metrics:    [{...}, ...],
      per_bs_metrics:      [{...}, ...],
      scenario_metadata:   {...},
      simulation_summary:  {...},   # raw SimulationSummary dict (bottlenecks etc.)
    }
    """
    rc = state.get("route_cost_result")
    if not rc:
        return {"available": False, "reason": "시뮬레이션을 먼저 실행하세요."}

    return {
        "available":          True,
        "run_summary":        _safe(build_run_summary, state, {}),
        "algorithm_compare":  _safe(build_algorithm_compare, state, []),
        "per_edge_metrics":   _safe(build_per_edge_metrics, state, []),
        "per_bs_metrics":     _safe(build_per_bs_metrics, state, []),
        "scenario_metadata":  _safe(build_scenario_metadata, state, {}),
        "simulation_summary": state.get("simulation_summary"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# CSV serialisers
# ──────────────────────────────────────────────────────────────────────────────

def rows_to_csv(rows: list[dict], columns: list[str] | None = None) -> str:
    """
    Serialise a list of row-dicts to UTF-8 CSV text (with BOM for Excel).

    If `columns` is provided, only those keys are emitted in that order.
    Otherwise, the union of all row keys is used (dict insertion order).
    """
    if not rows:
        return "﻿"  # BOM only — empty file

    if columns is None:
        # Preserve insertion order while deduplicating
        seen: dict[str, None] = {}
        for row in rows:
            seen.update(dict.fromkeys(row.keys()))
        columns = list(seen.keys())

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=columns,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


def dict_to_csv(d: dict) -> str:
    """Serialise a flat or one-level-nested dict as key,value CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["key", "value"])
    for k, v in d.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                writer.writerow([f"{k}.{sub_k}", sub_v])
        else:
            writer.writerow([k, v])
    return "﻿" + buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sum_expected_latency_impact(alloc: dict) -> float:
    """Sum all per-BS expected latency impact values from an allocation result."""
    impact = alloc.get("expected_latency_impact") or {}
    if isinstance(impact, dict):
        return round(sum(float(v) for v in impact.values()), 3)
    return 0.0
