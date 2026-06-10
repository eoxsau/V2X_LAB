"""
Latency algorithm implementations — registered with LATENCY_REGISTRY on import.

Algorithm hierarchy (ordered by complexity):
  1. distance_based_latency    propagation only
  2. load_aware_latency        + BS congestion  ← matches legacy inline formula
  3. blockage_aware_latency    + building penetration loss
  4. mec_aware_latency         + MEC server + M/M/1 queuing
  5. full_composite_latency    all components combined  (DEFAULT)

All functions: LatencyInput → LatencyOutput
"""
from __future__ import annotations

from app.services.latency.registry import (
    LATENCY_REGISTRY,
    LatencyInput,
    LatencyOutput,
)


# ── Shared component helpers ────────────────────────────────────────────────────
def _propagation(inp: LatencyInput) -> float:
    """
    Distance-based propagation delay.
    Formula: base_ms + (dist_m / coverage_radius_m) * dist_factor
    Mirrors the legacy dist_pen computation in _find_best_bs_light().
    """
    cfg = inp.config
    cov_r = max(inp.base_station.coverage_radius_m, 1.0)
    return cfg.base_latency_ms + (inp.dist_m / cov_r) * cfg.dist_penalty_factor


def _load_penalty(inp: LatencyInput) -> float:
    """
    Congestion penalty from BS load ratio.
    load_ratio = load / capacity; capped at 1.0.
    """
    bs = inp.base_station
    load_ratio = min(bs.load / max(bs.capacity, 1.0), 1.0)
    return load_ratio * inp.config.load_penalty_scale


def _blockage_penalty(inp: LatencyInput) -> float:
    """
    Building penetration delay.
    Combines geometric loss_db and the analyzer's direct latency_penalty_ms.
    """
    b = inp.building_state
    return b.loss_db * inp.config.blockage_factor + b.latency_penalty_ms


def _queueing_delay(inp: LatencyInput) -> float:
    """
    M/M/1 approximate queuing delay (ms).
    E[W] = queue_length / queue_service_rate * 1000
    Returns 0 when queue is empty; capped at 50 ms when saturated.
    """
    bs = inp.base_station
    if bs.queue_length <= 0:
        return 0.0
    if bs.queue_service_rate > bs.queue_length:
        return min((bs.queue_length / bs.queue_service_rate) * 1000.0, 50.0)
    return 50.0     # saturated queue


def _mec_delay(inp: LatencyInput) -> float:
    return inp.base_station.mec_processing_ms


def _handover_delay(inp: LatencyInput) -> float:
    return inp.handover_state.handover_base_ms if inp.handover_state.handover_occurred else 0.0


def _transmission(inp: LatencyInput) -> float:
    return inp.config.transmission_ms


# ── Algorithm 1: distance_based_latency ───────────────────────────────────────
def distance_based_latency(inp: LatencyInput) -> LatencyOutput:
    """
    Propagation-only model.
    latency = base_ms + (dist_m / cov_r) * dist_factor + TX
    """
    prop = _propagation(inp)
    tx = _transmission(inp)
    total = round(prop + tx, 4)
    return LatencyOutput(
        latency=total,
        propagation_delay=round(prop, 4),
        transmission_delay=round(tx, 4),
        queueing_delay=0.0,
        mec_processing_delay=0.0,
        handover_delay=0.0,
        blockage_delay=0.0,
        debug_info={
            "dist_m": round(inp.dist_m, 2),
            "coverage_radius_m": inp.base_station.coverage_radius_m,
        },
    )


# ── Algorithm 2: load_aware_latency ───────────────────────────────────────────
def load_aware_latency(inp: LatencyInput) -> LatencyOutput:
    """
    Propagation + BS load congestion.
    Matches the legacy inline formula in _find_best_bs_light() when
    transmission_ms=0 (default): 4 + dist_pen + cong + edge_lat.
    """
    prop = _propagation(inp)
    tx = _transmission(inp)
    load_ms = _load_penalty(inp)
    edge_ms = inp.base_station.edge_latency_ms
    cong_ms = inp.base_station.congestion_penalty
    total = round(prop + tx + load_ms + edge_ms + cong_ms, 4)
    return LatencyOutput(
        latency=total,
        propagation_delay=round(prop, 4),
        transmission_delay=round(tx, 4),
        queueing_delay=0.0,
        mec_processing_delay=0.0,
        handover_delay=0.0,
        blockage_delay=0.0,
        debug_info={
            "load_ratio": round(inp.base_station.load / max(inp.base_station.capacity, 1.0), 4),
            "load_penalty_ms": round(load_ms, 4),
            "edge_latency_ms": edge_ms,
            "congestion_penalty": cong_ms,
        },
    )


# ── Algorithm 3: blockage_aware_latency ───────────────────────────────────────
def blockage_aware_latency(inp: LatencyInput) -> LatencyOutput:
    """
    Propagation + building blockage penalty.
    Requires loss_db from building obstruction analysis (building_state).
    Falls back gracefully when loss_db=0 (no building data loaded).
    """
    prop = _propagation(inp)
    tx = _transmission(inp)
    blockage_ms = _blockage_penalty(inp)
    edge_ms = inp.base_station.edge_latency_ms
    total = round(prop + tx + blockage_ms + edge_ms, 4)
    return LatencyOutput(
        latency=total,
        propagation_delay=round(prop, 4),
        transmission_delay=round(tx, 4),
        queueing_delay=0.0,
        mec_processing_delay=0.0,
        handover_delay=0.0,
        blockage_delay=round(blockage_ms, 4),
        debug_info={
            "loss_db": inp.building_state.loss_db,
            "latency_penalty_ms": inp.building_state.latency_penalty_ms,
            "blockage_total_ms": round(blockage_ms, 4),
        },
    )


# ── Algorithm 4: mec_aware_latency ────────────────────────────────────────────
def mec_aware_latency(inp: LatencyInput) -> LatencyOutput:
    """
    Propagation + BS load + MEC processing + M/M/1 queuing.
    Uses mec_processing_ms and queue_length/queue_service_rate from BaseStation.
    """
    prop = _propagation(inp)
    tx = _transmission(inp)
    load_ms = _load_penalty(inp)
    queue_ms = _queueing_delay(inp)
    mec_ms = _mec_delay(inp)
    edge_ms = inp.base_station.edge_latency_ms
    total = round(prop + tx + load_ms + queue_ms + mec_ms + edge_ms, 4)
    return LatencyOutput(
        latency=total,
        propagation_delay=round(prop, 4),
        transmission_delay=round(tx, 4),
        queueing_delay=round(queue_ms, 4),
        mec_processing_delay=round(mec_ms, 4),
        handover_delay=0.0,
        blockage_delay=0.0,
        debug_info={
            "queue_length": inp.base_station.queue_length,
            "queue_service_rate": inp.base_station.queue_service_rate,
            "mec_processing_ms": inp.base_station.mec_processing_ms,
            "load_ratio": round(inp.base_station.load / max(inp.base_station.capacity, 1.0), 4),
        },
    )


# ── Algorithm 5: full_composite_latency (default) ─────────────────────────────
def full_composite_latency(inp: LatencyInput) -> LatencyOutput:
    """
    All components: propagation + load + blockage + MEC + queuing + handover.
    Default algorithm — most comprehensive, all terms degrade gracefully to 0.
    """
    prop = _propagation(inp)
    tx = _transmission(inp)
    load_ms = _load_penalty(inp)
    blockage_ms = _blockage_penalty(inp)
    queue_ms = _queueing_delay(inp)
    mec_ms = _mec_delay(inp)
    ho_ms = _handover_delay(inp)
    edge_ms = inp.base_station.edge_latency_ms
    cong_ms = inp.base_station.congestion_penalty
    total = round(
        prop + tx + load_ms + blockage_ms + queue_ms + mec_ms + ho_ms + edge_ms + cong_ms, 4
    )
    return LatencyOutput(
        latency=total,
        propagation_delay=round(prop, 4),
        transmission_delay=round(tx, 4),
        queueing_delay=round(queue_ms, 4),
        mec_processing_delay=round(mec_ms, 4),
        handover_delay=round(ho_ms, 4),
        blockage_delay=round(blockage_ms, 4),
        debug_info={
            "load_ratio": round(inp.base_station.load / max(inp.base_station.capacity, 1.0), 4),
            "loss_db": inp.building_state.loss_db,
            "handover_occurred": inp.handover_state.handover_occurred,
            "queue_length": inp.base_station.queue_length,
            "mec_processing_ms": inp.base_station.mec_processing_ms,
        },
    )


# ── Registration ───────────────────────────────────────────────────────────────
LATENCY_REGISTRY.register(
    "distance_based_latency",
    distance_based_latency,
    description="거리 기반 전파 지연만 반영",
    requires_buildings=False,
    requires_mec=False,
)
LATENCY_REGISTRY.register(
    "load_aware_latency",
    load_aware_latency,
    description="거리 + 기지국 부하율 반영 (기존 공식과 호환)",
    requires_buildings=False,
    requires_mec=False,
)
LATENCY_REGISTRY.register(
    "blockage_aware_latency",
    blockage_aware_latency,
    description="거리 + 건물 차폐 손실 반영 (buildings_gdf 필요)",
    requires_buildings=True,
    requires_mec=False,
)
LATENCY_REGISTRY.register(
    "mec_aware_latency",
    mec_aware_latency,
    description="거리 + MEC 처리 지연 + M/M/1 큐잉 지연 반영",
    requires_buildings=False,
    requires_mec=True,
)
LATENCY_REGISTRY.register(
    "full_composite_latency",
    full_composite_latency,
    description="거리 + 부하율 + 건물 차폐 + MEC + 큐잉 + 핸드오버 모두 반영 (기본값)",
    requires_buildings=True,
    requires_mec=True,
)
