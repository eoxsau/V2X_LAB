"""
Latency Algorithm Registry — pluggable latency model for V2X routing.

LATENCY_REGISTRY is a module-level singleton populated by algorithms.py.
Callers never import algorithm functions directly — they call:

    from app.services.latency import LATENCY_REGISTRY
    LATENCY_REGISTRY.set_algorithm("load_aware_latency")
    out = LATENCY_REGISTRY.compute(inp)        # → LatencyOutput
    print(out.latency)                         # total ms

Input/output types mirror the user-visible request schema:
  Input  : LatencyInput  (vehicle_position, base_station, network_state,
                          building_state, resource_state, handover_state, config)
  Output : LatencyOutput (latency, propagation_delay, transmission_delay,
                          queueing_delay, mec_processing_delay,
                          handover_delay, blockage_delay, debug_info)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# ── Input data classes ─────────────────────────────────────────────────────────
@dataclass
class VehiclePosition:
    lat: float
    lng: float


@dataclass
class BaseStation:
    """
    Minimal view of a BS node for latency computation.
    Build from a raw dict with BaseStation.from_dict().
    """
    id: str
    name: str
    lat: float
    lng: float
    coverage_radius_m: float = 400.0
    capacity: float = 100.0
    load: float = 0.0
    edge_latency_ms: float = 5.0        # fixed backhaul delay
    congestion_penalty: float = 0.0     # operator-assigned congestion signal
    mec_processing_ms: float = 2.0      # MEC server processing time
    queue_length: float = 0.0           # current items in service queue (λ)
    queue_service_rate: float = 100.0   # items/s  (μ for M/M/1)

    @staticmethod
    def from_dict(d: dict) -> "BaseStation":
        return BaseStation(
            id=str(d.get("id", "")),
            name=d.get("name") or str(d.get("id", "")),
            lat=float(d.get("lat", 0.0)),
            lng=float(d.get("lng", 0.0)),
            coverage_radius_m=float(d.get("coverage_radius_m", 400.0)),
            capacity=float(d.get("capacity", 100.0)),
            load=float(d.get("load", d.get("current_load", 0.0))),
            edge_latency_ms=float(d.get("edge_latency_ms", 5.0)),
            congestion_penalty=float(d.get("congestion_penalty", 0.0)),
            mec_processing_ms=float(d.get("mec_processing_ms", 2.0)),
            queue_length=float(d.get("queue_length", 0.0)),
            queue_service_rate=float(d.get("queue_service_rate", 100.0)),
        )


@dataclass
class NetworkState:
    avg_load: float = 0.0
    num_connected_vehicles: int = 0


@dataclass
class BuildingState:
    loss_db: float = 0.0
    obstruction_factor: float = 0.0     # 0–1 fraction
    latency_penalty_ms: float = 0.0     # from analyze_vehicle_to_node()


@dataclass
class ResourceState:
    resource_utilization: float = 0.0
    resource_deficit: float = 0.0


@dataclass
class HandoverState:
    prev_bs_id: Optional[str] = None
    current_bs_id: Optional[str] = None
    handover_occurred: bool = False
    handover_base_ms: float = 5.0       # flat handover transition cost (ms)


@dataclass
class LatencyConfig:
    """
    Tunable parameters for latency models.
    Defaults reproduce the legacy inline formula when transmission_ms=0.
    """
    base_latency_ms: float = 4.0          # propagation floor (ms)
    dist_penalty_factor: float = 15.0     # dist_m / cov_r * factor  (ms)
    load_penalty_scale: float = 10.0      # load_ratio * scale  (ms)
    blockage_factor: float = 0.5          # loss_db * factor  (ms)
    transmission_ms: float = 0.0          # TX processing — 0 for legacy compat
    resource_penalty_scale: float = 5.0   # resource_deficit * scale  (ms)


@dataclass
class LatencyInput:
    """Unified input for all latency algorithms."""
    vehicle_position: VehiclePosition
    base_station: BaseStation
    dist_m: float                           # precomputed vehicle → BS distance
    network_state: NetworkState = field(default_factory=NetworkState)
    building_state: BuildingState = field(default_factory=BuildingState)
    resource_state: ResourceState = field(default_factory=ResourceState)
    handover_state: HandoverState = field(default_factory=HandoverState)
    config: LatencyConfig = field(default_factory=LatencyConfig)


# ── Output ─────────────────────────────────────────────────────────────────────
@dataclass
class LatencyOutput:
    """
    Per-component latency breakdown.
    ``latency`` is the total (sum of all components) used in routing.
    """
    latency: float                      # total ms — primary value used by cost fn
    propagation_delay: float
    transmission_delay: float
    queueing_delay: float
    mec_processing_delay: float
    handover_delay: float
    blockage_delay: float
    algorithm_id: str = ""
    debug_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "latency": round(self.latency, 4),
            "propagation_delay": round(self.propagation_delay, 4),
            "transmission_delay": round(self.transmission_delay, 4),
            "queueing_delay": round(self.queueing_delay, 4),
            "mec_processing_delay": round(self.mec_processing_delay, 4),
            "handover_delay": round(self.handover_delay, 4),
            "blockage_delay": round(self.blockage_delay, 4),
            "algorithm_id": self.algorithm_id,
            "debug_info": self.debug_info,
        }


# ── Registry ───────────────────────────────────────────────────────────────────
_DEFAULT_ALGORITHM = "full_composite_latency"


class LatencyAlgorithmRegistry:
    """
    Pluggable registry for V2X latency algorithms.

    All registered algorithms share the signature:
        fn(LatencyInput) -> LatencyOutput

    The registry is a module-level singleton (LATENCY_REGISTRY).
    algorithms.py populates it on import; main.py selects the active one.
    """

    def __init__(self) -> None:
        self._algorithms: dict[str, Callable[[LatencyInput], LatencyOutput]] = {}
        self._current: str = _DEFAULT_ALGORITHM
        self._metadata: dict[str, dict] = {}

    def register(
        self,
        name: str,
        fn: Callable[[LatencyInput], LatencyOutput],
        *,
        description: str = "",
        requires_buildings: bool = False,
        requires_mec: bool = False,
    ) -> None:
        self._algorithms[name] = fn
        self._metadata[name] = {
            "id": name,
            "description": description,
            "requires_buildings": requires_buildings,
            "requires_mec": requires_mec,
        }

    def set_algorithm(self, name: str) -> None:
        if name not in self._algorithms:
            raise KeyError(
                f"Unknown latency algorithm '{name}'. "
                f"Registered: {list(self._algorithms)}"
            )
        self._current = name

    def compute(
        self,
        inp: LatencyInput,
        algorithm_id: Optional[str] = None,
    ) -> LatencyOutput:
        """
        Run the specified (or current) algorithm.
        Falls back to inline formula when no algorithm is registered.
        """
        algo = algorithm_id or self._current
        if algo not in self._algorithms:
            algo = next(iter(self._algorithms), None)
            if algo is None:
                # Emergency inline fallback — never reached when algorithms.py loaded
                bs = inp.base_station
                cfg = inp.config
                cov_r = max(bs.coverage_radius_m, 1.0)
                prop = cfg.base_latency_ms + (inp.dist_m / cov_r) * cfg.dist_penalty_factor
                total = round(prop + bs.edge_latency_ms, 4)
                return LatencyOutput(
                    latency=total,
                    propagation_delay=round(prop, 4),
                    transmission_delay=0.0,
                    queueing_delay=0.0,
                    mec_processing_delay=0.0,
                    handover_delay=0.0,
                    blockage_delay=0.0,
                    algorithm_id="fallback_inline",
                )
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


# Singleton — populated when algorithms.py is imported via __init__.py
LATENCY_REGISTRY = LatencyAlgorithmRegistry()
