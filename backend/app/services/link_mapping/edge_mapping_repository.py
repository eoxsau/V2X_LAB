from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EdgeMappingRepository:
    current_osm_edges: list[dict] = field(default_factory=list)
    current_sumo_edges: list[dict] = field(default_factory=list)
    standard_to_osm: list[dict] = field(default_factory=list)
    osm_to_sumo: list[dict] = field(default_factory=list)
    traffic_applied: list[dict] = field(default_factory=list)

    def reset(self) -> None:
        self.current_osm_edges.clear()
        self.current_sumo_edges.clear()
        self.standard_to_osm.clear()
        self.osm_to_sumo.clear()
        self.traffic_applied.clear()


EDGE_MAPPING_REPOSITORY = EdgeMappingRepository()

