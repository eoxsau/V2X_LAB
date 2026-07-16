# Import algorithms first — triggers all LATENCY_REGISTRY.register() calls
from app.services.latency import algorithms as _algorithms  # noqa: F401
# v3.1 통합 모델 등록 (기본값 tech_latency_v31) — algorithms 다음에 임포트
from app.services.latency import formula_v31 as _formula_v31  # noqa: F401

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
