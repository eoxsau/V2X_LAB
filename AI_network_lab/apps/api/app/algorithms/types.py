from __future__ import annotations

from typing import Any, TypedDict


AlgorithmDebugInfo = dict[str, Any]


class RouteAlgorithmResult(TypedDict):
    path: list[Any]
    selected_base_station_sequence: list[str]
    cost_breakdown: dict[str, float]
    total_cost: float
    debug_info: AlgorithmDebugInfo


class LatencyAlgorithmResult(TypedDict):
    latency: float
    propagation_delay: float
    transmission_delay: float
    queueing_delay: float
    mec_processing_delay: float
    handover_delay: float
    blockage_delay: float
    debug_info: AlgorithmDebugInfo


class BaseStationSelectionResult(TypedDict):
    selected_base_station: str | None
    candidate_scores: dict[str, float]
    reason: str
    debug_info: AlgorithmDebugInfo


class ResourceAllocationResult(TypedDict):
    allocation_result: dict[str, Any]
    resource_deficit: float
    bs_load_after_allocation: dict[str, float]
    vehicle_allocations: dict[str, Any]
    debug_info: AlgorithmDebugInfo


class CustomAlgorithmResult(TypedDict):
    output: Any
    debug_info: AlgorithmDebugInfo
