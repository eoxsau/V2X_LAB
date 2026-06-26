"""
Simulation result summary — structured input data for AI analysis reports.

Aggregates per-algorithm routing results into a single SimulationSummary object
that an LLM can use to generate natural-language analysis.

No LLM calls are made here.  This module only assembles facts.

Typical LLM prompt structure (future use):
    f"다음 데이터를 바탕으로 V2X 경로 선택 결과를 분석하라.\\n{summary.to_llm_context()}"

Example LLM output this data enables:
    "최단거리 경로는 이동거리는 짧지만 특정 구간에서 기지국 부하가 높아
     평균 지연시간이 증가했다."
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional


# ── Thresholds ─────────────────────────────────────────────────────────────────
@dataclass
class SummaryThresholds:
    """
    Configurable detection thresholds for section classification.
    Adjust to match the scale of your V2X deployment.
    """
    bs_load_bottleneck: float = 0.70    # load ratio → bottleneck section
    bs_load_overloaded: float = 0.85    # load ratio → overloaded BS
    latency_high_ms: float = 20.0       # ms → high-latency section
    loss_high_db: float = 10.0          # dB → high-blockage section


DEFAULT_THRESHOLDS = SummaryThresholds()


# ── Sub-structures ─────────────────────────────────────────────────────────────
@dataclass
class RouteSummary:
    """Basic route geometry and timing for the selected algorithm."""
    path_edge_ids: list[str]
    total_distance_m: float
    total_travel_time_s: float
    edge_count: int
    selected_bs_sequence: list[str]     # deduplicated BS transition sequence


@dataclass
class MetricSummary:
    """RouteMetrics for every algorithm that was evaluated."""
    algorithms: dict[str, dict]         # algo_name → RouteMetrics.to_dict()
    comparison: Optional[dict]          # compare_algorithms() output


@dataclass
class ImprovementOverBaseline:
    """
    Delta between selected algorithm and baseline on each metric.
    Negative delta = selected is better (lower is better for all metrics).
    """
    selected_algorithm: str
    baseline_algorithm: str
    distance_delta_m: float             # selected − baseline
    travel_time_delta_s: float
    latency_delta_ms: float             # negative = selected is faster
    handover_delta: int                 # negative = fewer handovers
    load_delta: float                   # negative = less BS load
    cost_delta: float                   # negative = lower composite cost
    cost_improvement_pct: float         # (baseline−selected)/baseline × 100


@dataclass
class BottleneckSection:
    """
    A road edge where the connected BS load exceeds the bottleneck threshold.
    High load → increased latency and potential disconnection risk.
    """
    edge_id: str
    street_name: str
    midpoint_lat: float
    midpoint_lng: float
    connected_bs: str                   # BS name or ID
    load_ratio: float
    latency_ms: float
    severity: str                       # "critical" (>0.9) | "high" (>0.7) | "medium"


@dataclass
class OverloadedBaseStation:
    """
    A BS that is at or near capacity across the route.
    May be the root cause of multiple BottleneckSections.
    """
    bs_id: str
    bs_name: str
    lat: float
    lng: float
    load_ratio: float
    affected_edge_count: int            # edges on the route that connect to this BS
    severity: str


@dataclass
class HandoverSection:
    """
    A road edge where a BS handover occurs.
    Frequent handovers indicate overlapping coverage zones or network instability.
    """
    edge_id: str
    street_name: str
    from_bs_name: str
    to_bs_name: str
    latency_ms: float
    load_ratio: float


@dataclass
class HighLatencySection:
    """
    A road edge where predicted latency exceeds the SLA threshold.
    """
    edge_id: str
    street_name: str
    latency_ms: float
    connected_bs: str
    excess_ms: float                    # latency_ms − threshold
    threshold_ms: float


@dataclass
class CoverageRiskSection:
    """
    A road edge not covered by any BS (within_coverage=False).
    Indicates potential disconnection risk on this segment.
    """
    edge_id: str
    street_name: str
    midpoint_lat: float
    midpoint_lng: float
    nearest_bs_name: Optional[str]
    severity: str                       # "disconnected" (no coverage) | "marginal"


@dataclass
class RecommendationTextSeed:
    """
    Structured factual statements for LLM natural-language generation.

    All strings are in Korean to match the target output language.
    The LLM should weave these facts into coherent analysis paragraphs.

    Field usage guide for LLM prompt construction:
      primary_finding      → opening sentence / headline
      trade_offs           → body paragraph comparing algorithms
      improvement_highlights → positive outcomes section
      degradation_warnings → caution / limitation section
      risk_factors         → risk assessment section
      network_observations → network state / context section
      suggested_focus      → LLM instruction: what to emphasise
    """
    primary_finding: str
    trade_offs: list[str]
    improvement_highlights: list[str]
    degradation_warnings: list[str]
    risk_factors: list[str]
    network_observations: list[str]
    suggested_focus: str


# ── Top-level summary ──────────────────────────────────────────────────────────
@dataclass
class SimulationSummary:
    """
    Complete summary of a simulation run for AI report generation.

    Build with: build_summary(route_cost, k_candidates, algorithm_metrics, ...)
    Serialise with: summary.to_dict()
    LLM context with: summary.to_llm_context()
    """
    scenario_id: str
    generated_at: str                   # ISO 8601 UTC
    selected_algorithm: str
    baseline_algorithm: str
    route_summary: RouteSummary
    metric_summary: MetricSummary
    improvement_over_baseline: Optional[ImprovementOverBaseline]
    bottleneck_sections: list[BottleneckSection]
    overloaded_base_stations: list[OverloadedBaseStation]
    frequent_handover_sections: list[HandoverSection]
    high_latency_sections: list[HighLatencySection]
    future_connectivity_risk_sections: list[CoverageRiskSection]
    recommendation_text_seed: RecommendationTextSeed

    def to_dict(self) -> dict:
        return asdict(self)

    def to_llm_context(self) -> str:
        """
        Compact text representation for use as LLM prompt context.
        Contains the most decision-relevant facts in plain Korean.
        """
        seed = self.recommendation_text_seed
        imp = self.improvement_over_baseline
        lines = [
            f"[시나리오] {self.scenario_id}",
            f"[선택 알고리즘] {self.selected_algorithm}  [기준 알고리즘] {self.baseline_algorithm}",
            f"[핵심 발견] {seed.primary_finding}",
        ]
        if imp:
            lines.append(
                f"[기준 대비] 비용 {'+' if imp.cost_delta >= 0 else ''}{imp.cost_delta:.3f}"
                f" ({imp.cost_improvement_pct:+.1f}%)"
                f"  지연 {imp.latency_delta_ms:+.1f}ms"
                f"  거리 {imp.distance_delta_m:+.0f}m"
                f"  핸드오버 {imp.handover_delta:+d}회"
            )
        if seed.trade_offs:
            lines.append("[트레이드오프] " + " | ".join(seed.trade_offs))
        if seed.risk_factors:
            lines.append("[위험 요인] " + " | ".join(seed.risk_factors))
        if seed.network_observations:
            lines.append("[네트워크 상태] " + " | ".join(seed.network_observations))
        if seed.suggested_focus:
            lines.append(f"[분석 초점] {seed.suggested_focus}")
        return "\n".join(lines)


# ── Public builder ─────────────────────────────────────────────────────────────
def build_summary(
    route_cost: Optional[dict],         # _state["route_cost_result"]
    k_candidates: Optional[dict],       # _state["k_path_candidates"]
    algorithm_metrics: dict,            # _state["algorithm_metrics"]
    bs_nodes: list[dict],
    scenario_id: str = "unknown",
    thresholds: Optional[SummaryThresholds] = None,
    edge_names: Optional[dict] = None,  # _state["route_edge_names"] — edge_id → 도로명
) -> SimulationSummary:
    """
    Build a SimulationSummary from the current simulation state.

    All inputs come directly from _state in main.py.
    Handles missing data gracefully — returns a partial summary if
    only route_cost is available (K-paths optional).
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    now = datetime.now(timezone.utc).isoformat()

    selected_alg, baseline_alg = _pick_algorithms(route_cost, k_candidates, algorithm_metrics)

    selected_edges = _get_edge_snaps(selected_alg, route_cost, k_candidates)
    baseline_edges = _get_edge_snaps(baseline_alg, route_cost, k_candidates)

    # 매칭 안 되는 엣지(OSM에 도로명이 없거나 미수집)는 경로 순서상 직전 엣지의 도로명을
    # 그대로 이어 써서 구간이 빠짐없이 도로명으로 보이게 한다 — tab-network.jsx의 같은
    # 전파 로직을 백엔드 요약 쪽에도 동일하게 적용.
    names = _fill_edge_names(selected_edges, edge_names or {})

    route_sum = _build_route_summary(selected_alg, selected_edges, route_cost, k_candidates)
    metric_sum = MetricSummary(
        algorithms={k: v for k, v in algorithm_metrics.items() if not k.startswith("_")},
        comparison=algorithm_metrics.get("_comparison"),
    )
    improvement = _compute_improvement(selected_alg, baseline_alg, algorithm_metrics)

    bottlenecks = _extract_bottlenecks(selected_edges, thr, names)
    overloaded_bs = _extract_overloaded_bs(selected_edges, bs_nodes, thr)
    handovers = _extract_handover_sections(selected_edges, names)
    high_lat = _extract_high_latency_sections(selected_edges, thr, names)
    cov_risk = _extract_coverage_risk_sections(selected_edges, names)

    rec_seed = _build_recommendation_seed(
        selected_alg, baseline_alg, improvement,
        bottlenecks, overloaded_bs, handovers, high_lat, cov_risk,
        route_sum, algorithm_metrics,
    )

    return SimulationSummary(
        scenario_id=scenario_id,
        generated_at=now,
        selected_algorithm=selected_alg,
        baseline_algorithm=baseline_alg,
        route_summary=route_sum,
        metric_summary=metric_sum,
        improvement_over_baseline=improvement,
        bottleneck_sections=bottlenecks,
        overloaded_base_stations=overloaded_bs,
        frequent_handover_sections=handovers,
        high_latency_sections=high_lat,
        future_connectivity_risk_sections=cov_risk,
        recommendation_text_seed=rec_seed,
    )


# ── Algorithm selection ────────────────────────────────────────────────────────
def _pick_algorithms(
    route_cost: Optional[dict],
    k_candidates: Optional[dict],
    algorithm_metrics: dict,
) -> tuple[str, str]:
    """
    Determine selected (best) and baseline algorithms from available data.

    Selection priority:
      selected  → K-path rank 0 > network_aware > route_cost routing_mode > first in metrics
      baseline  → baseline_dijkstra > network_aware > any non-selected > "unknown"
    """
    all_algos = [k for k in algorithm_metrics if not k.startswith("_")]

    # Selected: K-path rank 0 is the explicitly optimised candidate
    selected = "unknown"
    if k_candidates and k_candidates.get("candidates"):
        selected = "k_path_rank_0"
    elif route_cost:
        selected = route_cost.get("routing_mode", "network_aware")
    elif all_algos:
        selected = all_algos[0]

    # Baseline: prefer the pure-distance Dijkstra variant
    baseline = "unknown"
    _baseline_pref = ["baseline_dijkstra", "dijkstra", "network_aware"]
    for name in _baseline_pref:
        if name in algorithm_metrics and name != selected:
            baseline = name
            break
    if baseline == "unknown":
        candidates_b = [a for a in all_algos if a != selected]
        if candidates_b:
            baseline = candidates_b[0]
        elif selected == "unknown" and all_algos:
            baseline = all_algos[0]

    return selected, baseline


# ── Edge data helpers ──────────────────────────────────────────────────────────
def _get_edge_snaps(
    algorithm: str,
    route_cost: Optional[dict],
    k_candidates: Optional[dict],
) -> list[SimpleNamespace]:
    """
    Return a list of edge snapshots for the given algorithm.
    Supports route_cost routing_modes and k_path_rank_N keys.
    """
    raw: list[dict] = []

    if algorithm.startswith("k_path_rank_"):
        rank = _safe_int(algorithm.split("_")[-1])
        if k_candidates:
            for c in k_candidates.get("candidates", []):
                if c.get("rank") == rank:
                    raw = c.get("per_edge", [])
                    break
    elif route_cost and route_cost.get("routing_mode") == algorithm:
        raw = route_cost.get("per_edge", [])
    elif route_cost and algorithm in ("baseline_dijkstra", "network_aware"):
        # Accept any route_cost if algorithm name roughly matches the routing modes
        raw = route_cost.get("per_edge", [])

    return [_snap(d) for d in raw]


def _fill_edge_names(edges: list[SimpleNamespace], raw_names: dict) -> dict[str, str]:
    """경로 순서를 따라 도로명을 전파한다.

    OSM에 이름이 없거나 도로명 매칭에 실패한 엣지는 직전 엣지의 도로명을 그대로
    물려받고(정방향 전파), 경로 맨 앞부터 이름이 없는 경우에는 다음 엣지의 이름을
    당겨온다(역방향 전파) — 두 전파를 다 거쳐도 이름이 없는 엣지만 edge_id로 남는다.
    """
    filled: dict[str, str] = {eid: name for e in edges if (eid := e.edge_id) and (name := raw_names.get(eid))}
    ordered_ids = [e.edge_id for e in edges]
    last = None
    for eid in ordered_ids:
        if eid in filled:
            last = filled[eid]
        elif last:
            filled[eid] = last
    nxt = None
    for eid in reversed(ordered_ids):
        if eid in filled:
            nxt = filled[eid]
        elif nxt:
            filled[eid] = nxt
    return filled


def _snap(d: dict) -> SimpleNamespace:
    """Convert a stored per_edge dict to a SimpleNamespace for uniform access."""
    return SimpleNamespace(
        edge_id         = d.get("edge_id", ""),
        midpoint_lat    = float(d.get("midpoint_lat", 0.0)),
        midpoint_lng    = float(d.get("midpoint_lng", 0.0)),
        distance_m      = float(d.get("distance_m", 0.0)),
        latency_ms      = float(d.get("latency_ms", 0.0)),
        best_node_id    = d.get("best_node_id"),
        best_node_name  = d.get("best_node_name") or d.get("best_node_id") or "—",
        load_ratio      = float(d.get("load_ratio", 0.0)),
        handover        = bool(d.get("handover", False)),
        within_coverage = bool(d.get("within_coverage", True)),
        loss_db         = float(d.get("loss_db", 0.0)),
        total_cost      = float(d.get("total_cost", 0.0)),
    )


# ── Route summary ──────────────────────────────────────────────────────────────
def _build_route_summary(
    algorithm: str,
    edges: list[SimpleNamespace],
    route_cost: Optional[dict],
    k_candidates: Optional[dict],
) -> RouteSummary:
    path = [e.edge_id for e in edges]
    dist = sum(e.distance_m for e in edges)
    time_s = sum(
        e.distance_m / 13.9 for e in edges   # fallback 50 km/h if no travel_time
    )

    # Prefer precise values from the stored summary dict
    if algorithm.startswith("k_path_rank_"):
        rank = _safe_int(algorithm.split("_")[-1])
        for c in (k_candidates or {}).get("candidates", []):
            if c.get("rank") == rank:
                dist = c.get("total_distance_m", dist)
                time_s = c.get("total_travel_time_s", time_s)
                path = c.get("path", path) or path
                break
    elif route_cost:
        dist = route_cost.get("total_distance_m", dist)
        time_s = route_cost.get("total_travel_time_s", time_s)

    # Deduplicated BS sequence
    bs_seq: list[str] = []
    for e in edges:
        name = e.best_node_name
        if name and (not bs_seq or bs_seq[-1] != name):
            bs_seq.append(name)

    return RouteSummary(
        path_edge_ids=path,
        total_distance_m=round(dist, 2),
        total_travel_time_s=round(time_s, 2),
        edge_count=len(edges),
        selected_bs_sequence=bs_seq,
    )


# ── Improvement computation ────────────────────────────────────────────────────
def _compute_improvement(
    selected: str,
    baseline: str,
    metrics: dict,
) -> Optional[ImprovementOverBaseline]:
    sel_m = metrics.get(selected)
    base_m = metrics.get(baseline)
    if not sel_m or not base_m or selected == baseline:
        return None

    cost_base = float(base_m.get("total_cost", 0.0))
    cost_sel = float(sel_m.get("total_cost", 0.0))
    cost_pct = (cost_base - cost_sel) / max(abs(cost_base), 1e-9) * 100.0

    return ImprovementOverBaseline(
        selected_algorithm=selected,
        baseline_algorithm=baseline,
        distance_delta_m=round(
            float(sel_m.get("total_distance_m", 0)) - float(base_m.get("total_distance_m", 0)), 2
        ),
        travel_time_delta_s=round(
            float(sel_m.get("travel_time_s", 0)) - float(base_m.get("travel_time_s", 0)), 2
        ),
        latency_delta_ms=round(
            float(sel_m.get("average_latency_ms", 0)) - float(base_m.get("average_latency_ms", 0)), 2
        ),
        handover_delta=int(sel_m.get("handover_count", 0)) - int(base_m.get("handover_count", 0)),
        load_delta=round(
            float(sel_m.get("average_bs_load", 0)) - float(base_m.get("average_bs_load", 0)), 4
        ),
        cost_delta=round(cost_sel - cost_base, 4),
        cost_improvement_pct=round(cost_pct, 2),
    )


# ── Section extractors ─────────────────────────────────────────────────────────
def _extract_bottlenecks(
    edges: list[SimpleNamespace],
    thr: SummaryThresholds,
    edge_names: dict,
) -> list[BottleneckSection]:
    out: list[BottleneckSection] = []
    for e in edges:
        if e.load_ratio >= thr.bs_load_bottleneck:
            sev = "critical" if e.load_ratio >= 0.90 else "high"
            out.append(BottleneckSection(
                edge_id=e.edge_id,
                street_name=edge_names.get(e.edge_id) or e.edge_id,
                midpoint_lat=e.midpoint_lat,
                midpoint_lng=e.midpoint_lng,
                connected_bs=e.best_node_name,
                load_ratio=round(e.load_ratio, 4),
                latency_ms=round(e.latency_ms, 2),
                severity=sev,
            ))
    return sorted(out, key=lambda x: x.load_ratio, reverse=True)


def _extract_overloaded_bs(
    edges: list[SimpleNamespace],
    bs_nodes: list[dict],
    thr: SummaryThresholds,
) -> list[OverloadedBaseStation]:
    """
    Identify BS nodes that are overloaded AND appear on the route.
    Cross-references the route's edge BS assignments with the live BS load.
    """
    # Count how many route edges connect to each BS
    bs_edge_count: dict[str, int] = {}
    for e in edges:
        if e.best_node_name and e.best_node_name != "—":
            bs_edge_count[e.best_node_name] = bs_edge_count.get(e.best_node_name, 0) + 1

    out: list[OverloadedBaseStation] = []
    seen: set[str] = set()
    for bs in bs_nodes:
        name = bs.get("name") or str(bs.get("id", ""))
        if name in seen:
            continue
        load = float(bs.get("load", bs.get("current_load", 0.0)))
        cap = float(bs.get("capacity", 100.0))
        ratio = load / max(cap, 1.0)
        if ratio >= thr.bs_load_overloaded:
            seen.add(name)
            sev = "critical" if ratio >= 0.95 else "high"
            out.append(OverloadedBaseStation(
                bs_id=str(bs.get("id", name)),
                bs_name=name,
                lat=float(bs.get("lat", 0.0)),
                lng=float(bs.get("lng", 0.0)),
                load_ratio=round(ratio, 4),
                affected_edge_count=bs_edge_count.get(name, 0),
                severity=sev,
            ))
    return sorted(out, key=lambda x: x.load_ratio, reverse=True)


def _extract_handover_sections(edges: list[SimpleNamespace], edge_names: dict) -> list[HandoverSection]:
    out: list[HandoverSection] = []
    prev_bs = ""
    for e in edges:
        if e.handover and prev_bs:
            out.append(HandoverSection(
                edge_id=e.edge_id,
                street_name=edge_names.get(e.edge_id) or e.edge_id,
                from_bs_name=prev_bs,
                to_bs_name=e.best_node_name,
                latency_ms=round(e.latency_ms, 2),
                load_ratio=round(e.load_ratio, 4),
            ))
        if e.best_node_name and e.best_node_name != "—":
            prev_bs = e.best_node_name
    return out


def _extract_high_latency_sections(
    edges: list[SimpleNamespace],
    thr: SummaryThresholds,
    edge_names: dict,
) -> list[HighLatencySection]:
    out: list[HighLatencySection] = []
    for e in edges:
        if e.latency_ms > thr.latency_high_ms:
            out.append(HighLatencySection(
                edge_id=e.edge_id,
                street_name=edge_names.get(e.edge_id) or e.edge_id,
                latency_ms=round(e.latency_ms, 2),
                connected_bs=e.best_node_name,
                excess_ms=round(e.latency_ms - thr.latency_high_ms, 2),
                threshold_ms=thr.latency_high_ms,
            ))
    return sorted(out, key=lambda x: x.latency_ms, reverse=True)


def _extract_coverage_risk_sections(
    edges: list[SimpleNamespace],
    edge_names: dict,
) -> list[CoverageRiskSection]:
    out: list[CoverageRiskSection] = []
    for e in edges:
        if not e.within_coverage:
            out.append(CoverageRiskSection(
                edge_id=e.edge_id,
                street_name=edge_names.get(e.edge_id) or e.edge_id,
                midpoint_lat=e.midpoint_lat,
                midpoint_lng=e.midpoint_lng,
                nearest_bs_name=e.best_node_name if e.best_node_name != "—" else None,
                severity="disconnected",
            ))
    return out


# ── Recommendation seed ────────────────────────────────────────────────────────
def _build_recommendation_seed(
    selected: str,
    baseline: str,
    imp: Optional[ImprovementOverBaseline],
    bottlenecks: list[BottleneckSection],
    overloaded_bs: list[OverloadedBaseStation],
    handovers: list[HandoverSection],
    high_lat: list[HighLatencySection],
    cov_risk: list[CoverageRiskSection],
    route: RouteSummary,
    metrics: dict,
) -> RecommendationTextSeed:
    """
    Generate factual Korean text seeds for LLM report generation.
    All statements are derived mechanically from measured data — no inference.
    """
    sel_m = metrics.get(selected, {})

    # ── primary_finding ──────────────────────────────────────────────────────
    if imp and abs(imp.cost_improvement_pct) >= 0.5:
        direction = "개선" if imp.cost_improvement_pct > 0 else "증가"
        primary = (
            f"{_algo_ko(selected)} 알고리즘이 {_algo_ko(baseline)} 대비 "
            f"총 비용 {abs(imp.cost_improvement_pct):.1f}% {direction}"
        )
    elif sel_m:
        primary = (
            f"{_algo_ko(selected)} 경로: "
            f"총 {route.total_distance_m:.0f}m, "
            f"평균 지연 {float(sel_m.get('average_latency_ms', 0)):.1f}ms, "
            f"핸드오버 {sel_m.get('handover_count', 0)}회"
        )
    else:
        primary = f"{_algo_ko(selected)} 경로 평가 완료"

    # ── trade_offs ───────────────────────────────────────────────────────────
    trade_offs: list[str] = []
    if imp:
        if abs(imp.distance_delta_m) >= 10:
            sign = "+" if imp.distance_delta_m > 0 else ""
            trade_offs.append(f"이동거리 {sign}{imp.distance_delta_m:.0f}m")
        if abs(imp.latency_delta_ms) >= 0.5:
            sign = "+" if imp.latency_delta_ms > 0 else ""
            trade_offs.append(f"평균 지연시간 {sign}{imp.latency_delta_ms:.1f}ms")
        if imp.handover_delta != 0:
            sign = "+" if imp.handover_delta > 0 else ""
            trade_offs.append(f"핸드오버 {sign}{imp.handover_delta}회")
        if abs(imp.load_delta) >= 0.01:
            sign = "+" if imp.load_delta > 0 else ""
            trade_offs.append(f"평균 BS 부하율 {sign}{imp.load_delta * 100:.1f}%p")

    # ── improvement_highlights ────────────────────────────────────────────────
    highlights: list[str] = []
    if imp:
        if imp.latency_delta_ms < -0.5:
            highlights.append(
                f"지연시간 {abs(imp.latency_delta_ms):.1f}ms 개선 "
                f"({_pct(imp.latency_delta_ms, float(metrics.get(baseline, {}).get('average_latency_ms', 1)))})"
            )
        if imp.handover_delta < 0:
            highlights.append(f"핸드오버 {abs(imp.handover_delta)}회 감소")
        if imp.cost_improvement_pct > 0.5:
            highlights.append(f"총 비용 {imp.cost_improvement_pct:.1f}% 절감")
        if imp.load_delta < -0.01:
            highlights.append(f"BS 부하율 {abs(imp.load_delta) * 100:.1f}%p 감소")

    # ── degradation_warnings ──────────────────────────────────────────────────
    warnings_: list[str] = []
    if imp:
        if imp.distance_delta_m > 50:
            base_dist = float(metrics.get(baseline, {}).get("total_distance_m", 1))
            warnings_.append(
                f"이동거리 {imp.distance_delta_m:.0f}m 증가 "
                f"({_pct(imp.distance_delta_m, base_dist)})"
            )
        if imp.latency_delta_ms > 0.5:
            warnings_.append(f"지연시간 {imp.latency_delta_ms:.1f}ms 증가")
        if imp.handover_delta > 0:
            warnings_.append(f"핸드오버 {imp.handover_delta}회 증가")
        if imp.cost_improvement_pct < -0.5:
            warnings_.append(f"총 비용 {abs(imp.cost_improvement_pct):.1f}% 증가")

    # ── risk_factors ──────────────────────────────────────────────────────────
    risks: list[str] = []
    if cov_risk:
        risks.append(f"{len(cov_risk)}개 구간 기지국 커버리지 외부 (연결 단절 위험)")
    if overloaded_bs:
        for bs in overloaded_bs[:2]:
            risks.append(f"{bs.bs_name} 과부하 ({bs.load_ratio * 100:.0f}%)")
    if bottlenecks:
        risks.append(
            f"고부하 병목 구간 {len(bottlenecks)}개 "
            f"(최대 {max(b.load_ratio for b in bottlenecks) * 100:.0f}%)"
        )
    if high_lat:
        risks.append(
            f"고지연 구간 {len(high_lat)}개 "
            f"(최대 {max(s.latency_ms for s in high_lat):.1f}ms)"
        )

    # ── network_observations ──────────────────────────────────────────────────
    observations: list[str] = []
    if route.selected_bs_sequence:
        observations.append(
            f"경유 기지국 순서: {' → '.join(route.selected_bs_sequence)}"
        )
    if handovers:
        observations.append(f"핸드오버 발생 {len(handovers)}회")
    total_edges = route.edge_count
    if total_edges > 0:
        covered = total_edges - len(cov_risk)
        observations.append(
            f"커버리지 유지 구간 {covered}/{total_edges}개 ({covered / total_edges * 100:.0f}%)"
        )
    avg_load = float(sel_m.get("average_bs_load", 0))
    if avg_load > 0:
        observations.append(f"평균 기지국 부하율 {avg_load * 100:.1f}%")

    # ── suggested_focus ───────────────────────────────────────────────────────
    focus_parts: list[str] = []
    if highlights:
        focus_parts.append(f"{_algo_ko(selected)}의 {highlights[0]} 효과")
    if bottlenecks:
        focus_parts.append(f"고부하 구간 {len(bottlenecks)}개의 영향 분석")
    if overloaded_bs:
        focus_parts.append(f"{overloaded_bs[0].bs_name} 과부하 문제")
    if not focus_parts:
        focus_parts.append(f"{_algo_ko(selected)}와 {_algo_ko(baseline)} 비교 분석")

    suggested_focus = " 및 ".join(focus_parts) + "를 중점으로 분석"

    return RecommendationTextSeed(
        primary_finding=primary,
        trade_offs=trade_offs,
        improvement_highlights=highlights,
        degradation_warnings=warnings_,
        risk_factors=risks,
        network_observations=observations,
        suggested_focus=suggested_focus,
    )


# ── Utility ───────────────────────────────────────────────────────────────────
def _algo_ko(name: str) -> str:
    """Return a short Korean display name for an algorithm key."""
    _map = {
        "baseline_dijkstra": "최단거리(Dijkstra)",
        "dijkstra": "Dijkstra",
        "network_aware": "네트워크 인식",
        "k_path_rank_0": "K-경로 최적",
        "k_path_rank_1": "K-경로 2위",
        "k_path_rank_2": "K-경로 3위",
    }
    return _map.get(name, name)


def _pct(delta: float, base: float) -> str:
    if abs(base) < 1e-9:
        return "—"
    return f"{abs(delta / base) * 100:.1f}% {'증가' if delta > 0 else '감소'}"


def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default
