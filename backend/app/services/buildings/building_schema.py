from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class BuildingDatasetInfo:
    folder: Path
    stem: str
    region_code: str
    shp: Path
    shx: Path
    dbf: Path
    prj: Path | None
    cpg: Path | None
    encoding: str | None


@dataclass(slots=True)
class BuildingObstructionResult:
    vehicle_id: str
    network_node_id: str
    distance_m: float
    intersected_building_count: int
    max_building_height_m: float
    estimated_penetration_loss_db: float
    latency_penalty_ms: float
    stability_score: float
    confidence: str
    highlighted_buildings: list[dict]
