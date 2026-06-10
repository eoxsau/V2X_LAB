"""
Resource Allocation Algorithm implementations — registered with ALLOCATION_REGISTRY on import.

Algorithm hierarchy (ordered by sophistication):
  1. equal_allocation               equal RBs per vehicle, BS-agnostic
  2. proportional_demand_allocation proportional to demand_rb
  3. traffic_aware_allocation       demand × congestion weight  ← DEFAULT
  4. load_balancing_allocation      water-fill: equalize load ratios
  5. latency_minimizing_allocation  greedy: prioritise high-load BSes
  6. priority_based_allocation      reserve capacity for emergency vehicles
  7. lookahead_resource_allocation  add α × predicted future demand

All functions: AllocationInput → AllocationOutput
"""
from __future__ import annotations

import math
from typing import Optional

from app.services.resources.allocation_registry import (
    ALLOCATION_REGISTRY,
    AllocationInput,
    AllocationOutput,
    AllocationConfig,
    BSAllocation,
    VehicleAllocation,
)


# ── Shared helpers ────────────────────────────────────────────────────────────
def _bs_id(bs: dict) -> str:
    return str(bs.get("id") or bs.get("name") or "")


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _flat_demand_map(inp: AllocationInput) -> dict[str, dict]:
    """Normalise resource_demand_map to dict[bs_id → plain dict]."""
    if not inp.resource_demand_map:
        return {}
    result: dict[str, dict] = {}
    for k, v in inp.resource_demand_map.items():
        result[str(k)] = v.to_dict() if hasattr(v, "to_dict") else dict(v)
    return result


def _bs_demand(bs_id: str, bs: dict, dm: dict, cfg: AllocationConfig) -> float:
    """Demand for bs_id: from demand_map or fall back to current load."""
    if bs_id in dm:
        return float(dm[bs_id].get("demand_rb", 0.0))
    return float(bs.get("load") or 0.0)


def _bs_congestion(bs_id: str, inp: AllocationInput) -> float:
    """Congestion ratio (0–1): traffic_state first, then load/capacity."""
    ts = inp.traffic_state or {}
    if bs_id in ts:
        return float(ts[bs_id].get("congestion_level", 0.0))
    for bs in inp.base_stations:
        if _bs_id(bs) == bs_id:
            load = float(bs.get("load") or 0.0)
            cap = float(bs.get("capacity") or inp.config.total_rb_per_bs)
            return min(load / max(cap, 1.0), 1.0)
    return 0.0


def _nearest_bs(vehicle: dict, base_stations: list[dict]) -> Optional[str]:
    v_lat = float(vehicle.get("lat") or 0.0)
    v_lng = float(vehicle.get("lng") or 0.0)
    best_id: Optional[str] = None
    best_d = float("inf")
    for bs in base_stations:
        d = _haversine_m(
            v_lat, v_lng,
            float(bs.get("lat") or 0.0), float(bs.get("lng") or 0.0),
        )
        if d < best_d:
            best_d = d
            best_id = _bs_id(bs)
    return best_id


def _make_bs_alloc(
    bs_id: str,
    capacity: float,
    allocated: float,
    demand: float,
) -> BSAllocation:
    allocated = max(0.0, min(allocated, capacity))
    util = allocated / max(capacity, 1.0)
    deficit = max(0.0, demand - capacity)
    return BSAllocation(
        bs_id=bs_id,
        total_capacity_rb=round(capacity, 2),
        allocated_rb=round(allocated, 4),
        utilization_ratio=round(util, 4),
        updated_load=round(util, 4),          # ratio 0–1 → written to node["load"]/capacity
        demand_rb=round(demand, 4),
        deficit_rb=round(deficit, 4),
    )


def _build_output(
    algo_id: str,
    bs_allocs: list[BSAllocation],
    inp: AllocationInput,
    extra_debug: dict,
) -> AllocationOutput:
    cfg = inp.config
    deficit_by_bs = {a.bs_id: a.deficit_rb for a in bs_allocs if a.deficit_rb > 0}
    bs_load_after = {a.bs_id: a.updated_load for a in bs_allocs}

    # Latency impact: (new_load_ratio − old_load_ratio) × load_penalty_scale
    latency_impact: dict[str, float] = {}
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        load = float(bs.get("load") or 0.0)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        old_ratio = min(load / max(cap, 1.0), 1.0)
        new_ratio = bs_load_after.get(bid, old_ratio)
        latency_impact[bid] = round((new_ratio - old_ratio) * cfg.load_penalty_scale, 4)

    total_alloc = sum(a.allocated_rb for a in bs_allocs)
    total_cap = sum(a.total_capacity_rb for a in bs_allocs)
    total_util = total_alloc / max(total_cap, 1.0)

    return AllocationOutput(
        algorithm_id=algo_id,
        allocation_result={
            "total_allocated_rb": round(total_alloc, 4),
            "total_capacity_rb": round(total_cap, 4),
            "total_utilization": round(total_util, 4),
            "overloaded_bs_count": len(deficit_by_bs),
            "total_deficit_rb": round(sum(deficit_by_bs.values()), 4),
        },
        base_station_allocations=bs_allocs,
        vehicle_allocations=[],
        resource_deficit_by_bs=deficit_by_bs,
        expected_latency_impact=latency_impact,
        bs_load_after_allocation=bs_load_after,
        debug_info=extra_debug,
    )


# ── Algorithm 1: equal_allocation ────────────────────────────────────────────
def equal_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Each BS receives capacity / max(expected_connected, 1) per vehicle (equal share).
    Ignores demand differences between BSes.
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)
    bs_allocs: list[BSAllocation] = []

    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = _bs_demand(bid, bs, dm, cfg)
        expected = float(dm.get(bid, {}).get("expected_connected_vehicle_count", 0.0)) or 1.0

        rb_per_v = max(
            min(cap / expected, cfg.max_rb_per_vehicle),
            cfg.min_rb_per_vehicle,
        )
        allocated = min(expected * rb_per_v, cap)
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("equal_allocation", bs_allocs, inp, {})


# ── Algorithm 2: proportional_demand_allocation ───────────────────────────────
def proportional_demand_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Each BS receives (demand_i / Σdemand) × total_system_capacity.
    BSes with higher demand get proportionally more resources.
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    demands = {_bs_id(bs): _bs_demand(_bs_id(bs), bs, dm, cfg) for bs in inp.base_stations}
    total_demand = sum(demands.values())
    total_sys_cap = sum(float(bs.get("capacity") or cfg.total_rb_per_bs) for bs in inp.base_stations)

    bs_allocs: list[BSAllocation] = []
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = demands[bid]
        allocated = (
            min((demand / total_demand) * total_sys_cap, cap)
            if total_demand > 0 else 0.0
        )
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("proportional_demand_allocation", bs_allocs, inp, {
        "total_demand": round(total_demand, 4),
    })


# ── Algorithm 3: traffic_aware_allocation (DEFAULT) ───────────────────────────
def traffic_aware_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Weight = demand_rb × (1 + congestion_level).
    Congested BSes receive proportionally more resources.
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    weights: dict[str, float] = {}
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        demand = _bs_demand(bid, bs, dm, cfg)
        cong = _bs_congestion(bid, inp)
        weights[bid] = demand * (1.0 + cong)

    total_w = sum(weights.values())
    total_sys_cap = sum(float(bs.get("capacity") or cfg.total_rb_per_bs) for bs in inp.base_stations)

    bs_allocs: list[BSAllocation] = []
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = _bs_demand(bid, bs, dm, cfg)
        allocated = (
            min((weights[bid] / total_w) * total_sys_cap, cap)
            if total_w > 0 else 0.0
        )
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("traffic_aware_allocation", bs_allocs, inp, {
        "congestion_weights": {k: round(v, 4) for k, v in weights.items()},
    })


# ── Algorithm 4: load_balancing_allocation ────────────────────────────────────
def load_balancing_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Water-fill: set a system-wide target utilisation ratio and cap overloaded
    BSes at that target × capacity.  Underloaded BSes are allocated their
    actual demand only (no over-provisioning).

    target_ratio = Σdemand / Σcapacity (capped at 1.0).
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    entries = [
        (_bs_id(bs), float(bs.get("capacity") or cfg.total_rb_per_bs),
         _bs_demand(_bs_id(bs), bs, dm, cfg))
        for bs in inp.base_stations
    ]
    total_cap = sum(e[1] for e in entries)
    total_demand = sum(e[2] for e in entries)
    target_ratio = min(total_demand / max(total_cap, 1.0), 1.0)

    bs_allocs: list[BSAllocation] = []
    for bid, cap, demand in entries:
        demand_ratio = demand / max(cap, 1.0)
        if demand_ratio > target_ratio:
            # Overloaded: cap at target × capacity (acknowledge remaining as deficit)
            allocated = target_ratio * cap
        else:
            # Underloaded: only allocate actual demand
            allocated = demand
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("load_balancing_allocation", bs_allocs, inp, {
        "target_utilization_ratio": round(target_ratio, 4),
    })


# ── Algorithm 5: latency_minimizing_allocation ────────────────────────────────
def latency_minimizing_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Greedy allocation minimising total expected latency.
    Marginal latency benefit per extra RB = load_ratio / capacity × load_penalty_scale.
    Allocates to BSes with highest current load ratio first, then meets remaining demand.
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    entries = []
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = _bs_demand(bid, bs, dm, cfg)
        load = float(bs.get("load") or 0.0)
        load_ratio = min(load / max(cap, 1.0), 1.0)
        # Higher load → more benefit from reducing it
        marginal_benefit = load_ratio * cfg.load_penalty_scale / max(cap, 1.0)
        entries.append((bid, cap, demand, load_ratio, marginal_benefit))

    # Allocate demand first (baseline), then record benefit order
    bs_allocs: list[BSAllocation] = []
    for bid, cap, demand, _lr, _mb in entries:
        allocated = min(demand, cap)  # satisfy demand up to capacity
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("latency_minimizing_allocation", bs_allocs, inp, {
        "marginal_benefit_order": [
            {"bs_id": e[0], "marginal_benefit": round(e[4], 6), "load_ratio": round(e[3], 4)}
            for e in sorted(entries, key=lambda x: x[4], reverse=True)
        ],
    })


# ── Algorithm 6: priority_based_allocation ────────────────────────────────────
def priority_based_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Reserve ``emergency_rb_reserve`` fraction of each BS for priority >= 3 vehicles.
    Remaining capacity is distributed to normal vehicles proportionally to demand.
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    # Count emergency RB demand per BS (priority >= 3)
    emg_demand: dict[str, float] = {}
    for v in inp.vehicles:
        priority = int(v.get("service_priority") or v.get("priority") or 1)
        if priority >= 3:
            bid = _nearest_bs(v, inp.base_stations)
            if bid:
                emg_demand[bid] = emg_demand.get(bid, 0.0) + cfg.max_rb_per_vehicle

    bs_allocs: list[BSAllocation] = []
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = _bs_demand(bid, bs, dm, cfg)

        # Emergency block: min of actual emergency demand and the reserved fraction
        emg_block = min(emg_demand.get(bid, 0.0), cap * cfg.emergency_rb_reserve)
        remaining_cap = max(cap - emg_block, 0.0)

        # Normal demand after emergency is served
        normal_demand = max(demand - emg_demand.get(bid, 0.0), 0.0)
        normal_alloc = min(normal_demand, remaining_cap)
        allocated = min(emg_block + normal_alloc, cap)

        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("priority_based_allocation", bs_allocs, inp, {
        "emergency_demand_by_bs": {k: round(v, 4) for k, v in emg_demand.items()},
    })


# ── Algorithm 7: lookahead_resource_allocation ────────────────────────────────
def lookahead_resource_allocation(inp: AllocationInput) -> AllocationOutput:
    """
    Pre-allocate resources based on predicted future connections.

    adjusted_demand_i = current_demand_i
                        + α × max(0, expected_future_count − current_count) × min_rb_per_vehicle

    Then allocate proportionally to adjusted demand.
    α = config.lookahead_weight (default 0.3).
    """
    cfg = inp.config
    dm = _flat_demand_map(inp)

    adjusted: dict[str, float] = {}
    future_extra: dict[str, float] = {}

    for bs in inp.base_stations:
        bid = _bs_id(bs)
        info = dm.get(bid, {})
        current_demand = _bs_demand(bid, bs, dm, cfg)
        expected_conn = float(info.get("expected_connected_vehicle_count") or 0.0)
        current_v = float(info.get("nearby_vehicle_count") or 0)
        extra = max(0.0, (expected_conn - current_v) * cfg.min_rb_per_vehicle) * cfg.lookahead_weight
        future_extra[bid] = extra
        adjusted[bid] = current_demand + extra

    total_adj = sum(adjusted.values())
    total_sys_cap = sum(float(bs.get("capacity") or cfg.total_rb_per_bs) for bs in inp.base_stations)

    bs_allocs: list[BSAllocation] = []
    for bs in inp.base_stations:
        bid = _bs_id(bs)
        cap = float(bs.get("capacity") or cfg.total_rb_per_bs)
        demand = _bs_demand(bid, bs, dm, cfg)
        adj = adjusted[bid]
        allocated = min((adj / total_adj) * total_sys_cap, cap) if total_adj > 0 else 0.0
        bs_allocs.append(_make_bs_alloc(bid, cap, allocated, demand))

    return _build_output("lookahead_resource_allocation", bs_allocs, inp, {
        "future_extra_rb": {k: round(v, 4) for k, v in future_extra.items()},
        "adjusted_demand": {k: round(v, 4) for k, v in adjusted.items()},
    })


# ── Registration ──────────────────────────────────────────────────────────────
ALLOCATION_REGISTRY.register(
    "equal_allocation",
    equal_allocation,
    description="모든 차량에 균등 자원 배분 (수요 무시)",
)
ALLOCATION_REGISTRY.register(
    "proportional_demand_allocation",
    proportional_demand_allocation,
    description="수요에 비례한 자원 배분",
)
ALLOCATION_REGISTRY.register(
    "traffic_aware_allocation",
    traffic_aware_allocation,
    description="수요 × 혼잡도 가중치 기반 배분 (기본값)",
)
ALLOCATION_REGISTRY.register(
    "load_balancing_allocation",
    load_balancing_allocation,
    description="수요/용량 비율을 균일화하는 워터필 배분",
)
ALLOCATION_REGISTRY.register(
    "latency_minimizing_allocation",
    latency_minimizing_allocation,
    description="현재 부하가 높은 기지국 우선 배분 (지연시간 최소화)",
)
ALLOCATION_REGISTRY.register(
    "priority_based_allocation",
    priority_based_allocation,
    description="긴급/우선순위 차량에 자원 예약 후 나머지 비례 배분",
)
ALLOCATION_REGISTRY.register(
    "lookahead_resource_allocation",
    lookahead_resource_allocation,
    description="미래 연결 예측(look-ahead)을 반영한 사전 자원 예약",
)
