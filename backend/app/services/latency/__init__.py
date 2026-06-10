# Import algorithms first — triggers all LATENCY_REGISTRY.register() calls
from app.services.latency import algorithms as _algorithms  # noqa: F401

from app.services.latency.registry import (
    LATENCY_REGISTRY,
    LatencyInput,
    LatencyOutput,
    VehiclePosition,
    BaseStation,
    NetworkState,
    BuildingState,
    ResourceState,
    HandoverState,
    LatencyConfig,
)

__all__ = [
    "LATENCY_REGISTRY",
    "LatencyInput",
    "LatencyOutput",
    "VehiclePosition",
    "BaseStation",
    "NetworkState",
    "BuildingState",
    "ResourceState",
    "HandoverState",
    "LatencyConfig",
]
