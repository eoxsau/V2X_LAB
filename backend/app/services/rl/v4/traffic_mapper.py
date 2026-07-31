"""
TrafficMapper — bridges ITS/SUMO traffic data to mock_graph edges.

Matching priority (Path 1 → 2 → 3):
  Path 1 (primary): enriched_links with osm_edge_key → parse "u:v:key" → mock_graph node keys
  Path 2:           SUMO peak_edge_loads + reverse osm_to_sumo mapping → osm_edge_key → (u,v)
  Path 3 (fallback): midpoint coordinate proximity — KD-tree 500m search

Outputs:
  annotate_mock_graph(mock_graph)   — inject n_its_vehicles into node dicts
  as_traffic_data()                 — demand_calculator-compatible dict
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_FALLBACK_RADIUS_M = 500.0


@dataclass
class _LinkData:
    u: str
    v: str
    volume_veh_per_h: float = 0.0
    speed_kph: float = 50.0
    path: int = 0  # 1, 2, or 3


class TrafficMapper:
    """
    Maps ITS/SUMO traffic state to mock_graph nodes and edges.

    Parameters
    ----------
    enriched_links : list[dict]
        Output of TrafficFusionEngine.sync_its() — each dict contains at minimum
        "osm_edge_key" (format "u:v:key"), optionally "volume_veh_per_h" and "speed_kph".
    peak_edge_loads : dict, optional
        SUMO scenario peak loads: {sumo_edge_id: load_value}. Used for Path 2.
    osm_to_sumo_mapping : list[dict], optional
        EDGE_MAPPING_REPOSITORY.osm_to_sumo — each dict has "osm_edge_key" and "sumo_edge_id".
    """

    def __init__(
        self,
        enriched_links: Optional[List[dict]] = None,
        peak_edge_loads: Optional[Dict[str, float]] = None,
        osm_to_sumo_mapping: Optional[List[dict]] = None,
    ):
        self._matched: Dict[Tuple[str, str], _LinkData] = {}
        enriched = enriched_links or []
        peak = peak_edge_loads or {}
        osm_sumo = osm_to_sumo_mapping or []

        self._run_path1(enriched)
        self._run_path2(peak, osm_sumo, enriched)
        log.debug(
            "TrafficMapper: %d links matched (path1=%d, path2=%d)",
            len(self._matched),
            sum(1 for v in self._matched.values() if v.path == 1),
            sum(1 for v in self._matched.values() if v.path == 2),
        )

    # ── Path 1 ────────────────────────────────────────────────────────────────

    def _run_path1(self, enriched_links: List[dict]) -> None:
        for item in enriched_links:
            osm_key = item.get("osm_edge_key")
            if not osm_key:
                continue
            parts = str(osm_key).split(":")
            if len(parts) < 2:
                continue
            u, v = parts[0], parts[1]
            vol = float(item.get("volume_veh_per_h") or 0.0)
            spd = float(item.get("speed_kph") or 50.0)
            if (u, v) not in self._matched:
                self._matched[(u, v)] = _LinkData(u=u, v=v,
                                                   volume_veh_per_h=vol,
                                                   speed_kph=spd, path=1)

    # ── Path 2 ────────────────────────────────────────────────────────────────

    def _run_path2(
        self,
        peak_edge_loads: Dict[str, float],
        osm_to_sumo_mapping: List[dict],
        enriched_links: List[dict],
    ) -> None:
        if not peak_edge_loads or not osm_to_sumo_mapping:
            return

        sumo_to_osm: Dict[str, str] = {
            m["sumo_edge_id"]: m["osm_edge_key"]
            for m in osm_to_sumo_mapping
            if m.get("sumo_edge_id") and m.get("osm_edge_key")
        }

        enriched_by_key: Dict[str, dict] = {
            item["osm_edge_key"]: item
            for item in enriched_links
            if item.get("osm_edge_key")
        }

        for sumo_id, load in peak_edge_loads.items():
            osm_key = sumo_to_osm.get(str(sumo_id))
            if not osm_key:
                continue
            parts = str(osm_key).split(":")
            if len(parts) < 2:
                continue
            u, v = parts[0], parts[1]
            if (u, v) in self._matched:
                continue  # already matched by Path 1

            src = enriched_by_key.get(osm_key, {})
            vol = float(src.get("volume_veh_per_h") or load)
            spd = float(src.get("speed_kph") or 50.0)
            self._matched[(u, v)] = _LinkData(u=u, v=v,
                                               volume_veh_per_h=vol,
                                               speed_kph=spd, path=2)

    # ── Path 3 — KD-tree coordinate fallback ──────────────────────────────────

    def match_by_coordinates(
        self,
        mock_graph: dict,
        enriched_links: List[dict],
        radius_m: float = _FALLBACK_RADIUS_M,
    ) -> int:
        """
        Fallback: match unresolved enriched_links to mock_graph edges by midpoint proximity.
        Returns count of newly matched links.

        Call explicitly if Path 1+2 coverage is insufficient; it is not run automatically
        because coordinate proximity is less reliable than osm_edge_key direct mapping.
        """
        nodes = mock_graph.get("nodes", {})
        adjacency = mock_graph.get("adjacency", {})

        # Build (midpoint_lat, midpoint_lng, u, v) index
        edge_midpoints: List[Tuple[float, float, str, str]] = []
        for u, neighbors in adjacency.items():
            nd_u = nodes.get(u, {})
            for entry in neighbors:
                v = str(entry[0]) if isinstance(entry, (list, tuple)) else str(entry)
                nd_v = nodes.get(v, {})
                if "lat" not in nd_u or "lat" not in nd_v:
                    continue
                mid_lat = (float(nd_u["lat"]) + float(nd_v["lat"])) / 2
                mid_lng = (float(nd_u["lng"]) + float(nd_v["lng"])) / 2
                edge_midpoints.append((mid_lat, mid_lng, u, v))

        added = 0
        for item in enriched_links:
            geom = item.get("geometry", [])
            if not geom:
                continue
            lats = [p["lat"] for p in geom if "lat" in p]
            lngs = [p["lng"] for p in geom if "lng" in p]
            if not lats:
                continue
            q_lat = sum(lats) / len(lats)
            q_lng = sum(lngs) / len(lngs)

            best_d, best_u, best_v = float("inf"), "", ""
            for (m_lat, m_lng, u, v) in edge_midpoints:
                d = _haversine_m(q_lat, q_lng, m_lat, m_lng)
                if d < best_d:
                    best_d, best_u, best_v = d, u, v

            if best_d > radius_m or not best_u:
                continue
            if (best_u, best_v) in self._matched:
                continue

            vol = float(item.get("volume_veh_per_h") or 0.0)
            spd = float(item.get("speed_kph") or 50.0)
            self._matched[(best_u, best_v)] = _LinkData(
                u=best_u, v=best_v, volume_veh_per_h=vol, speed_kph=spd, path=3
            )
            added += 1

        log.debug("Path 3 coordinate fallback: %d additional links matched", added)
        return added

    # ── Outputs ───────────────────────────────────────────────────────────────

    def annotate_mock_graph(self, mock_graph: dict) -> None:
        """
        Inject n_its_vehicles into mock_graph node dicts for matched edges.

        Uses Little's Law: N ≈ λ × (edge_length_km / speed_kph) × 3600
        approximated as proportional to volume_veh_per_h / 10 (cap 200).

        Called after Path 1+2 matching. build_graph_obs() automatically reads
        nodes[nid]["n_its_vehicles"] for edge feature [4] (bs_load proxy).
        """
        nodes = mock_graph.get("nodes", {})
        adjacency = mock_graph.get("adjacency", {})

        # Build edge length lookup: (u, v) → dist_m
        edge_lengths: Dict[Tuple[str, str], float] = {}
        for u, neighbors in adjacency.items():
            for entry in neighbors:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    v, dist_m = str(entry[0]), float(entry[1])
                    edge_lengths[(u, v)] = dist_m

        for (u, v), link in self._matched.items():
            dist_m = edge_lengths.get((u, v), 200.0)
            dist_km = max(dist_m / 1000.0, 0.01)
            spd = max(link.speed_kph, 1.0)
            # Little's Law: average occupancy = flow × traversal_time
            traversal_h = dist_km / spd
            n_veh = min(round(link.volume_veh_per_h * traversal_h), 200)

            for nid in (u, v):
                if nid in nodes:
                    nodes[nid]["n_its_vehicles"] = max(
                        int(nodes[nid].get("n_its_vehicles", 0)), n_veh
                    )

    def as_traffic_data(self) -> Dict[str, dict]:
        """
        Return demand_calculator-compatible traffic data.

        Format: {"{u}_{v}": {"vehicle_count": int, "speed_kph": float}}
        Compatible with build_resource_demand_map(traffic_data=...).
        """
        return {
            f"{u}_{v}": {
                "vehicle_count": round(link.volume_veh_per_h),
                "speed_kph": link.speed_kph,
            }
            for (u, v), link in self._matched.items()
        }

    @property
    def matched_count(self) -> int:
        return len(self._matched)


# ── Helper ────────────────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))
