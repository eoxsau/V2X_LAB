from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from app.algorithms.types import (
    BaseStationSelectionResult,
    CustomAlgorithmResult,
    LatencyAlgorithmResult,
    ResourceAllocationResult,
    RouteAlgorithmResult,
)

AlgorithmResultT = TypeVar("AlgorithmResultT")
AlgorithmInput = dict[str, Any]
AlgorithmHandler = Callable[[AlgorithmInput], AlgorithmResultT]


@dataclass(frozen=True)
class RegisteredAlgorithm(Generic[AlgorithmResultT]):
    name: str
    category: str
    handler: AlgorithmHandler[AlgorithmResultT]


class AlgorithmRegistry(Generic[AlgorithmResultT]):
    def __init__(self, category: str) -> None:
        self._category = category
        self._algorithms: dict[str, RegisteredAlgorithm[AlgorithmResultT]] = {}

    def register(
        self,
        name: str,
        algorithm: AlgorithmHandler[AlgorithmResultT] | RegisteredAlgorithm[AlgorithmResultT],
    ) -> RegisteredAlgorithm[AlgorithmResultT]:
        """Register a named algorithm so it can be looked up uniformly later."""
        if isinstance(algorithm, RegisteredAlgorithm):
            registered_algorithm = algorithm
        else:
            registered_algorithm = RegisteredAlgorithm(
                name=name,
                category=self._category,
                handler=algorithm,
            )

        self._algorithms[name] = registered_algorithm
        return registered_algorithm

    def get(self, name: str) -> RegisteredAlgorithm[AlgorithmResultT]:
        """Return a registered algorithm definition or raise when it is missing."""
        if name not in self._algorithms:
            raise KeyError(f"{self._category} algorithm '{name}' is not registered")
        return self._algorithms[name]

    def list(self) -> list[str]:
        """List the currently registered algorithm names in insertion order."""
        return list(self._algorithms.keys())

    def has(self, name: str) -> bool:
        """Return whether the given algorithm name is registered."""
        return name in self._algorithms

    def run(self, name: str, input: AlgorithmInput) -> AlgorithmResultT:
        """Resolve and execute a registered algorithm with a shared input envelope."""
        return self.get(name).handler(input)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _route_result_template(algorithm_name: str, input: AlgorithmInput) -> RouteAlgorithmResult:
    """Normalize route algorithm outputs into a shared response shape."""
    path = list(input.get("path") or input.get("candidate_path") or input.get("route_coords") or [])
    selected_base_station_sequence = list(input.get("selected_base_station_sequence") or [])
    cost_breakdown = {
        key: _safe_float(value)
        for key, value in dict(input.get("cost_breakdown") or {}).items()
    }
    total_cost = _safe_float(input.get("total_cost"))
    if total_cost == 0.0 and cost_breakdown:
        total_cost = round(sum(cost_breakdown.values()), 4)

    return {
        "path": path,
        "selected_base_station_sequence": selected_base_station_sequence,
        "cost_breakdown": cost_breakdown,
        "total_cost": total_cost,
        "debug_info": {
            "algorithm": algorithm_name,
            "path_length": len(path),
            "input_keys": sorted(input.keys()),
        },
    }


def _latency_result_template(algorithm_name: str, input: AlgorithmInput) -> LatencyAlgorithmResult:
    """Normalize latency outputs into a fixed delay-component structure."""
    propagation_delay = _safe_float(input.get("propagation_delay"))
    transmission_delay = _safe_float(input.get("transmission_delay"))
    queueing_delay = _safe_float(input.get("queueing_delay"))
    mec_processing_delay = _safe_float(input.get("mec_processing_delay"))
    handover_delay = _safe_float(input.get("handover_delay"))
    blockage_delay = _safe_float(input.get("blockage_delay"))
    latency = _safe_float(input.get("latency"))

    if latency == 0.0:
        latency = round(
            propagation_delay
            + transmission_delay
            + queueing_delay
            + mec_processing_delay
            + handover_delay
            + blockage_delay,
            4,
        )

    return {
        "latency": latency,
        "propagation_delay": propagation_delay,
        "transmission_delay": transmission_delay,
        "queueing_delay": queueing_delay,
        "mec_processing_delay": mec_processing_delay,
        "handover_delay": handover_delay,
        "blockage_delay": blockage_delay,
        "debug_info": {
            "algorithm": algorithm_name,
            "input_keys": sorted(input.keys()),
        },
    }


def _base_station_selection_result_template(
    algorithm_name: str,
    input: AlgorithmInput,
) -> BaseStationSelectionResult:
    """Normalize BS selection outputs into a shared ranking structure."""
    candidate_scores = {
        str(key): _safe_float(value)
        for key, value in dict(input.get("candidate_scores") or {}).items()
    }
    selected_base_station = input.get("selected_base_station")
    if selected_base_station is None and candidate_scores:
        selected_base_station = min(candidate_scores, key=candidate_scores.get)

    return {
        "selected_base_station": str(selected_base_station) if selected_base_station is not None else None,
        "candidate_scores": candidate_scores,
        "reason": str(input.get("reason") or f"{algorithm_name} selected the best scored base station"),
        "debug_info": {
            "algorithm": algorithm_name,
            "candidate_count": len(candidate_scores),
            "input_keys": sorted(input.keys()),
        },
    }


def _resource_allocation_result_template(
    algorithm_name: str,
    input: AlgorithmInput,
) -> ResourceAllocationResult:
    """Normalize resource allocation outputs into a single allocator response shape."""
    allocation_result = dict(input.get("allocation_result") or {})
    bs_load_after_allocation = {
        str(key): _safe_float(value)
        for key, value in dict(input.get("bs_load_after_allocation") or {}).items()
    }
    vehicle_allocations = dict(input.get("vehicle_allocations") or {})

    return {
        "allocation_result": allocation_result,
        "resource_deficit": _safe_float(input.get("resource_deficit")),
        "bs_load_after_allocation": bs_load_after_allocation,
        "vehicle_allocations": vehicle_allocations,
        "debug_info": {
            "algorithm": algorithm_name,
            "allocation_keys": sorted(allocation_result.keys()),
            "input_keys": sorted(input.keys()),
        },
    }


def _custom_result_template(algorithm_name: str, input: AlgorithmInput) -> CustomAlgorithmResult:
    """Normalize custom algorithm outputs without constraining the user payload shape."""
    return {
        "output": input.get("output"),
        "debug_info": {
            "algorithm": algorithm_name,
            "input_keys": sorted(input.keys()),
        },
    }


routeAlgorithmRegistry = AlgorithmRegistry[RouteAlgorithmResult](category="route")
latencyAlgorithmRegistry = AlgorithmRegistry[LatencyAlgorithmResult](category="latency")
baseStationSelectionRegistry = AlgorithmRegistry[BaseStationSelectionResult](category="base_station_selection")
resourceAllocationRegistry = AlgorithmRegistry[ResourceAllocationResult](category="resource_allocation")
customAlgorithmRegistry = AlgorithmRegistry[CustomAlgorithmResult](category="custom")


for _name in (
    "dijkstra",
    "astar",
    "k_shortest_path",
    "network_aware_routing",
    "look_ahead_routing",
    "rl_routing",
):
    routeAlgorithmRegistry.register(_name, lambda input, algorithm_name=_name: _route_result_template(algorithm_name, input))


for _name in (
    "distance_based_latency",
    "load_aware_latency",
    "blockage_aware_latency",
    "mec_aware_latency",
    "full_composite_latency",
):
    latencyAlgorithmRegistry.register(
        _name,
        lambda input, algorithm_name=_name: _latency_result_template(algorithm_name, input),
    )


for _name in (
    "nearest_bs",
    "lowest_latency_bs",
    "strongest_signal_bs",
    "load_balanced_bs",
    "look_ahead_bs_selection",
    "rl_based_bs_selection",
):
    baseStationSelectionRegistry.register(
        _name,
        lambda input, algorithm_name=_name: _base_station_selection_result_template(algorithm_name, input),
    )


for _name in (
    "equal_allocation",
    "proportional_allocation",
    "traffic_aware_allocation",
    "load_balancing_allocation",
    "latency_minimizing_allocation",
    "priority_based_allocation",
    "custom_allocation_algorithm",
):
    resourceAllocationRegistry.register(
        _name,
        lambda input, algorithm_name=_name: _resource_allocation_result_template(algorithm_name, input),
    )


for _name in (
    "custom_cost_function",
    "custom_route_evaluation",
    "custom_base_station_selection",
    "custom_resource_allocation",
):
    customAlgorithmRegistry.register(
        _name,
        lambda input, algorithm_name=_name: _custom_result_template(algorithm_name, input),
    )
