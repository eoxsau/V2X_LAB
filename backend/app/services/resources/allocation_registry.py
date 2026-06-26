"""
Resource Allocation Algorithm Registry — pluggable BS resource allocation.

ALLOCATION_REGISTRY is a module-level singleton populated by allocation_algorithms.py.

Callers:
    from app.services.resources.allocation_registry import ALLOCATION_REGISTRY, AllocationInput
    ALLOCATION_REGISTRY.set_algorithm("traffic_aware_allocation")
    out = ALLOCATION_REGISTRY.compute(inp)       # → AllocationOutput
    _state["network_nodes"] updated via out.bs_load_after_allocation

Input  : AllocationInput
Output : AllocationOutput  (includes bs_load_after_allocation → fed back to _state)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


# ── Input types ───────────────────────────────────────────────────────────────
@dataclass
class AllocationConfig:
    """Tunable parameters for all allocation algorithms."""
    total_rb_per_bs: float = 100.0        # default capacity when not in BS dict
    min_rb_per_vehicle: float = 2.0       # guaranteed minimum RBs per vehicle
    max_rb_per_vehicle: float = 20.0      # cap per vehicle
    emergency_rb_reserve: float = 0.15    # fraction of capacity for priority >= 3
    lookahead_weight: float = 0.3         # α: future demand contribution
    load_penalty_scale: float = 10.0      # matches LatencyConfig default (ms per unit load)
    overload_threshold: float = 1.0       # demand/capacity ratio = overloaded


@dataclass
class AllocationInput:
    """Unified input for all resource allocation algorithms."""
    base_stations: list[dict]                        # raw BS dicts from _state
    vehicles: list[dict]                             # vehicle positions + optional priority
    resource_demand_map: Optional[dict] = None       # dict[bs_id → BSResourceDemand | dict]
    network_state: Optional[dict] = None             # global network metrics
    traffic_state: Optional[dict] = None             # dict[bs_id → {"congestion_level": ...}]
    lookahead_results: Optional[Any] = None          # LookAheadResult or dict[bs_id → ...]
    config: AllocationConfig = field(default_factory=AllocationConfig)


# ── Output types ──────────────────────────────────────────────────────────────
@dataclass
class BSAllocation:
    bs_id: str
    total_capacity_rb: float
    allocated_rb: float
    utilization_ratio: float             # allocated / capacity  (0–1)
    updated_load: float                  # ratio 0–1, written back to network_nodes
    demand_rb: float
    deficit_rb: float                    # max(0, demand − capacity)


@dataclass
class VehicleAllocation:
    vehicle_id: str
    bs_id: str
    allocated_rb: float
    priority: int


@dataclass
class AllocationOutput:
    """
    Output of any allocation algorithm.

    bs_load_after_allocation : dict[bs_id → load_ratio 0–1].
        Apply to _state["network_nodes"] via apply_allocation_to_network_nodes()
        to propagate load changes to latency calculations and route cost.
    """
    algorithm_id: str
    allocation_result: dict                     # summary: utilization, deficit, etc.
    base_station_allocations: list[BSAllocation]
    vehicle_allocations: list[VehicleAllocation]
    resource_deficit_by_bs: dict                # bs_id → deficit_rb (only if > 0)
    expected_latency_impact: dict               # bs_id → ms delta (negative = improvement)
    bs_load_after_allocation: dict              # bs_id → load_ratio 0–1  ← key output
    debug_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "algorithm_id": self.algorithm_id,
            "allocation_result": self.allocation_result,
            "base_station_allocations": [asdict(a) for a in self.base_station_allocations],
            "vehicle_allocations": [asdict(v) for v in self.vehicle_allocations],
            "resource_deficit_by_bs": self.resource_deficit_by_bs,
            "expected_latency_impact": self.expected_latency_impact,
            "bs_load_after_allocation": self.bs_load_after_allocation,
            "debug_info": self.debug_info,
        }


# ── Registry ──────────────────────────────────────────────────────────────────
_DEFAULT_ALGORITHM = "traffic_aware_allocation"


class AllocationAlgorithmRegistry:
    """
    Pluggable registry for BS resource allocation algorithms.

    All registered algorithms share the signature:
        fn(AllocationInput) -> AllocationOutput

    The registry is a module-level singleton (ALLOCATION_REGISTRY).
    allocation_algorithms.py populates it on import; main.py selects active one.
    """

    def __init__(self) -> None:
        self._algorithms: dict[str, Callable[[AllocationInput], AllocationOutput]] = {}
        self._current: str = _DEFAULT_ALGORITHM
        self._metadata: dict[str, dict] = {}

    def register(
        self,
        name: str,
        fn: Callable[[AllocationInput], AllocationOutput],
        *,
        description: str = "",
    ) -> None:
        self._algorithms[name] = fn
        self._metadata[name] = {"id": name, "description": description}

    def set_algorithm(self, name: str) -> None:
        if name not in self._algorithms:
            raise KeyError(
                f"Unknown allocation algorithm '{name}'. "
                f"Registered: {list(self._algorithms)}"
            )
        self._current = name

    def compute(
        self,
        inp: AllocationInput,
        algorithm_id: Optional[str] = None,
    ) -> AllocationOutput:
        algo = algorithm_id or self._current
        if algo not in self._algorithms:
            algo = next(iter(self._algorithms), None)
            if algo is None:
                return _fallback_output(inp)
        result = self._algorithms[algo](inp)
        result.algorithm_id = algo
        return result

    def list_algorithms(self) -> list[dict]:
        return [
            {**v, "is_current": (k == self._current)}
            for k, v in self._metadata.items()
        ]

    @property
    def current_algorithm(self) -> str:
        return self._current


def _fallback_output(inp: AllocationInput) -> AllocationOutput:
    """Emergency fallback — no-op allocation (load unchanged)."""
    bs_allocs = []
    for bs in inp.base_stations:
        bs_id = str(bs.get("id") or bs.get("name") or "")
        cap = float(bs.get("capacity") or inp.config.total_rb_per_bs)
        load = float(bs.get("load") or 0.0)
        bs_allocs.append(BSAllocation(
            bs_id=bs_id,
            total_capacity_rb=cap,
            allocated_rb=load,
            utilization_ratio=round(min(load / max(cap, 1.0), 1.0), 4),
            updated_load=round(min(load / max(cap, 1.0), 1.0), 4),
            demand_rb=load,
            deficit_rb=0.0,
        ))
    return AllocationOutput(
        algorithm_id="fallback_noop",
        allocation_result={"note": "fallback — no algorithms registered"},
        base_station_allocations=bs_allocs,
        vehicle_allocations=[],
        resource_deficit_by_bs={},
        expected_latency_impact={},
        bs_load_after_allocation={a.bs_id: a.updated_load for a in bs_allocs},
    )


# ── Utility: apply results back to network_nodes ──────────────────────────────
def apply_allocation_to_network_nodes(
    alloc_out: AllocationOutput,
    network_nodes: list[dict],
) -> None:
    """
    Write bs_load_after_allocation back to network_nodes in-place.
    After this call, all downstream callers (latency registry, route cost function,
    RL environment, building_obstruction_analyzer's queuing model) automatically use
    the updated load/deficit values.

    Sets node["load"] = load_ratio × capacity (absolute value).
    Sets node["deficit_rb"] = unmet demand beyond capacity (0.0 when not overloaded) —
    utilization_ratio alone is capped at 1.0 by _make_bs_alloc(), so a BS that's 5x
    oversubscribed looks identical to one merely at capacity unless deficit_rb is
    carried along separately.
    """
    load_after = alloc_out.bs_load_after_allocation
    deficit_by_bs = alloc_out.resource_deficit_by_bs
    for node in network_nodes:
        nid = str(node.get("id") or node.get("name") or "")
        if nid in load_after:
            cap = float(node.get("capacity") or 100.0)
            node["load"] = round(load_after[nid] * cap, 4)
        node["deficit_rb"] = round(deficit_by_bs.get(nid, 0.0), 4)


# Singleton — populated when allocation_algorithms.py is imported via __init__.py
ALLOCATION_REGISTRY = AllocationAlgorithmRegistry()
