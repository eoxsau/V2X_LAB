from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import geopandas as gpd

from app.db import fetch_all_dicts, postgis_available, replace_edge_mappings, replace_traffic_snapshots
from app.services.link_mapping.edge_mapping_repository import EDGE_MAPPING_REPOSITORY
from app.services.link_mapping.osm_to_sumo_matcher import build_sumo_edges_gdf, match_osm_to_sumo_edges
from app.services.link_mapping.standard_to_osm_matcher import build_osm_edges_from_xml, match_standard_links_to_osm_edges
from app.services.standard_link.standard_link_preprocessor import preprocess_standard_links
from app.services.standard_link.standard_link_repository import StandardLinkRepository

from .its_cache import ITS_CACHE
from .its_client import fetch_its_traffic_info
from .its_models import KST, classify_time_period
from .its_parser import parse_its_traffic_xml
from .its_standard_link_matcher import match_its_to_standard_links, matched_to_dict


class TrafficFusionEngine:
    def __init__(self):
        self.standard_repo = StandardLinkRepository()

    def ensure_standard_links(self) -> dict:
        if not self.standard_repo.processed_ready():
            preprocess_standard_links()
        return self.standard_repo.status()

    def prepare_current_network_mappings(self, *, osm_file: Path | None, net_file: Path | None, bbox: dict | None) -> dict:
        EDGE_MAPPING_REPOSITORY.reset()
        if not osm_file or not Path(osm_file).exists():
            return {"warnings": ["OSM file is unavailable for mapping."], "matched_osm_edges": 0}

        self.ensure_standard_links()
        minx = bbox["w"]
        maxx = bbox["e"]
        miny = bbox["s"]
        maxy = bbox["n"]
        standard_links = self.standard_repo.links_in_bbox(minx, miny, maxx, maxy)
        osm_edges = build_osm_edges_from_xml(Path(osm_file))
        std_to_osm = match_standard_links_to_osm_edges(standard_links, osm_edges)

        EDGE_MAPPING_REPOSITORY.current_osm_edges = osm_edges.to_dict(orient="records")
        EDGE_MAPPING_REPOSITORY.standard_to_osm = std_to_osm

        sumo_matches = []
        if net_file and Path(net_file).exists():
            import sumolib

            net = sumolib.net.readNet(net_file, withInternal=False)
            sumo_edges = build_sumo_edges_gdf(net)
            osm_edges_with_key = osm_edges.copy()
            sumo_matches = match_osm_to_sumo_edges(osm_edges_with_key, sumo_edges)
            EDGE_MAPPING_REPOSITORY.current_sumo_edges = sumo_edges.to_dict(orient="records")
            EDGE_MAPPING_REPOSITORY.osm_to_sumo = sumo_matches

        if postgis_available():
            sumo_by_osm = {item["osm_edge_key"]: item["sumo_edge_id"] for item in sumo_matches}
            rows = [
                {
                    "standard_link_id": item["standard_link_id"],
                    "osm_edge_id": item["osm_edge_key"],
                    "sumo_edge_id": sumo_by_osm.get(item["osm_edge_key"]),
                    "confidence": item.get("confidence", 0.0),
                    "match_distance_m": item.get("distance_m"),
                }
                for item in std_to_osm
            ]
            replace_edge_mappings(rows)

        return {
            "matched_standard_links": len(std_to_osm),
            "matched_osm_edges": len(sumo_matches),
            "warnings": [],
        }

    def sync_its(self, *, bbox: dict, time_period: str | None = None) -> dict:
        # 명시적 override가 없으면 동기화 시점의 실제 시계(KST) 기준으로 첨두/비첨두 자동 분류.
        resolved_period = time_period if time_period in ("peak", "off_peak") else classify_time_period(datetime.now(KST))
        self.ensure_standard_links()
        xml_text = fetch_its_traffic_info(
            min_x=bbox["minX"],
            max_x=bbox["maxX"],
            min_y=bbox["minY"],
            max_y=bbox["maxY"],
        )
        records = parse_its_traffic_xml(xml_text)
        links = self.standard_repo.links_in_bbox(bbox["minX"], bbox["minY"], bbox["maxX"], bbox["maxY"])
        links_by_id = {str(row.link_id): row for row in links.itertuples()}
        matched, stats = match_its_to_standard_links(records, links)

        std_to_osm = {m["standard_link_id"]: m for m in EDGE_MAPPING_REPOSITORY.standard_to_osm}
        osm_to_sumo = {m["osm_edge_key"]: m for m in EDGE_MAPPING_REPOSITORY.osm_to_sumo}
        enriched_links: list[dict] = []
        matched_sumo = 0
        for item in matched:
            item_dict = matched_to_dict(item)
            map1 = std_to_osm.get(item.standard_link_id)
            osm_edge_key = map1["osm_edge_key"] if map1 else None
            sumo_match = osm_to_sumo.get(osm_edge_key) if osm_edge_key else None
            if sumo_match:
                matched_sumo += 1
            standard_row = links_by_id.get(str(item.standard_link_id))
            geometry = []
            if standard_row is not None and standard_row.geometry is not None:
                geometry = [{"lat": lat, "lng": lng} for lng, lat in list(standard_row.geometry.coords)]
            item_dict.update({
                "geometry": geometry,
                "osm_edge_key": osm_edge_key,
                "sumo_edge_id": sumo_match["sumo_edge_id"] if sumo_match else None,
            })
            enriched_links.append(item_dict)

        warnings = []
        if not EDGE_MAPPING_REPOSITORY.standard_to_osm:
            warnings.append("OSM edge mapping is not prepared yet. ITS traffic is matched to standard links only.")
        elif matched_sumo == 0:
            warnings.append("SUMO edge mapping is not available for the current network. ITS traffic is applied to route cost data only.")
        stats.update({
            "matched_standard_links": len(matched),
            "matched_osm_edges": matched_sumo,
            "unmatched_records": stats["unmatched_count"],
        })
        ITS_CACHE.update(
            records=records,
            matches=matched,
            enriched_links=enriched_links,
            bbox=bbox,
            warnings=warnings,
            stats=stats,
        )
        EDGE_MAPPING_REPOSITORY.traffic_applied = enriched_links
        if postgis_available():
            replace_traffic_snapshots(
                [
                    {
                        "link_id": item.get("standard_link_id") or item.get("its_link_id"),
                        "speed_kph": item.get("speed_kph"),
                        "travel_time_s": item.get("travel_time_s"),
                        "congestion_score": item.get("congestion_score"),
                        "collected_at": ITS_CACHE.last_sync_time,
                        "raw_payload": item,
                    }
                    for item in enriched_links
                ],
                time_period=resolved_period,
            )
        return {
            "records_count": len(records),
            "matched_standard_links": len(matched),
            "matched_osm_edges": matched_sumo,
            "unmatched_records": stats["unmatched_count"],
            "last_sync_time": ITS_CACHE.last_sync_time,
            "sample_matches": enriched_links[:5],
            "warnings": warnings,
            "time_period": resolved_period,
        }

    def current_traffic(self, *, time_period: str = "peak") -> dict:
        if postgis_available():
            links = fetch_all_dicts(
                """
                SELECT DISTINCT ON (link_id)
                       link_id, speed_kph, travel_time_s, congestion_score, collected_at, raw_payload
                FROM traffic_snapshots
                WHERE time_period = :time_period
                ORDER BY link_id, collected_at DESC
                """,
                {"time_period": time_period},
            )
            return {
                "links": [
                    {
                        "link_id": item["link_id"],
                        "road_name": (item.get("raw_payload") or {}).get("road_name"),
                        "speed_kph": item.get("speed_kph"),
                        "travel_time_s": item.get("travel_time_s"),
                        "congestion_score": item.get("congestion_score"),
                        "geometry": (item.get("raw_payload") or {}).get("geometry", []),
                    }
                    for item in links
                ],
                "last_sync_time": ITS_CACHE.last_sync_time,
                "stats": ITS_CACHE.stats,
                "warnings": ITS_CACHE.warnings,
            }
        return {
            "links": [
                {
                    "link_id": item.get("standard_link_id") or item.get("its_link_id"),
                    "road_name": item.get("road_name"),
                    "speed_kph": item.get("speed_kph"),
                    "travel_time_s": item.get("travel_time_s"),
                    "congestion_score": item.get("congestion_score"),
                    "geometry": item.get("geometry", []),
                }
                for item in ITS_CACHE.enriched_links
            ],
            "last_sync_time": ITS_CACHE.last_sync_time,
            "stats": ITS_CACHE.stats,
            "warnings": ITS_CACHE.warnings,
        }


TRAFFIC_FUSION_ENGINE = TrafficFusionEngine()
