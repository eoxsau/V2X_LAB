# Import algorithms first — triggers all ALLOCATION_REGISTRY.register() calls
from app.services.resources import allocation_algorithms as _alloc_algorithms  # noqa: F401

from app.services.resources.demand_calculator import (
    BSResourceDemand,
    get_road_segments_near_base_station,
    estimate_traffic_demand_near_base_station,
    estimate_expected_connected_vehicles,
    calculate_base_station_resource_demand,
    build_resource_demand_map,
)
from app.services.resources.allocation_registry import (
    ALLOCATION_REGISTRY,
    AllocationInput,
    AllocationOutput,
    AllocationConfig,
    BSAllocation,
    VehicleAllocation,
    apply_allocation_to_network_nodes,
)

__all__ = [
    # demand
    "BSResourceDemand",
    "get_road_segments_near_base_station",
    "estimate_traffic_demand_near_base_station",
    "estimate_expected_connected_vehicles",
    "calculate_base_station_resource_demand",
    "build_resource_demand_map",
    # allocation registry
    "ALLOCATION_REGISTRY",
    "AllocationInput",
    "AllocationOutput",
    "AllocationConfig",
    "BSAllocation",
    "VehicleAllocation",
    "apply_allocation_to_network_nodes",
]
