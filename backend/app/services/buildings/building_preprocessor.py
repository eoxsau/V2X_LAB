from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from dotenv import load_dotenv

from app.db import ensure_postgis_schema, postgis_available, upsert_buildings

from .building_file_scanner import default_processed_dir, default_raw_dir, scan_building_datasets
from .building_height_estimator import estimate_building_height
from .building_loader import load_building_dataset
from .building_spatial_indexer import add_spatial_metadata


def _postgis_hint() -> tuple[bool, list[str]]:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    warnings: list[str] = []
    if not postgis_available():
        warnings.append("POSTGIS_ENABLED is false. File-based building repository is used.")
        return False, warnings
    ensure_postgis_schema()
    return True, warnings


def preprocess_buildings(raw_dir: Path | None = None, processed_dir: Path | None = None) -> dict:
    raw_dir = raw_dir or default_raw_dir()
    processed_dir = processed_dir or default_processed_dir()
    processed_dir.mkdir(parents=True, exist_ok=True)

    datasets = scan_building_datasets(raw_dir)
    if not datasets:
        raise FileNotFoundError(f"No building shapefile sets found in {raw_dir}")

    existing_index_path = processed_dir / "index.json"
    existing = json.loads(existing_index_path.read_text(encoding="utf-8")) if existing_index_path.exists() else {}
    region_map = {item["region_code"]: item for item in existing.get("regions", [])}

    bldrgst_api_key: str = os.getenv("BLDRGST_API_KEY", "").strip()
    vworld_api_key:  str = os.getenv("VWORLD_API_KEY",  "").strip()

    for dataset in datasets:
        gdf = load_building_dataset(dataset)
        gdf = gdf.to_crs(4326)

        # Tier-4: footprint area in m² (EPSG:3857 기준)
        gdf["footprint_area_m2"] = gdf.to_crs(3857).area

        # Tier-5: V-World Data API 용도지역 공간조인 → zone_type 컬럼 채움
        if vworld_api_key:
            from .zone_enricher import enrich_zone_types
            gdf = enrich_zone_types(gdf, vworld_api_key)
        if "zone_type" not in gdf.columns:
            gdf["zone_type"] = None

        # Tier-1/2: 건축물대장 API → heit, grndFlrCnt, mainPurpsCdNm 등 보강
        if bldrgst_api_key:
            from .building_hub_enricher import enrich_gdf_with_bldrgst_api
            gdf = enrich_gdf_with_bldrgst_api(gdf, bldrgst_api_key)

        heights = gdf.apply(estimate_building_height, axis=1, result_type="expand")
        heights.columns = ["height_m", "height_source", "height_confidence"]
        gdf = gdf.join(heights)
        gdf["ufid"] = gdf.get("UFID")
        gdf["building_name"] = gdf.get("BLD_NM")
        gdf["dong_name"] = gdf.get("DONG_NM")
        gdf["pnu"] = gdf.get("PNU")
        gdf["ground_floor"] = gdf.get("GRND_FLR")
        gdf["underground_floor"] = gdf.get("UGRND_FLR")
        gdf["structure_code"] = gdf.get("STRCT_CD")
        gdf["usability_code"] = gdf.get("USABILITY")
        gdf["region_code"] = dataset.region_code
        gdf["source_file"] = dataset.shp.name
        gdf["created_at"] = datetime.now(timezone.utc).isoformat()
        centroids = gdf.to_crs(3857).centroid.to_crs(4326)
        gdf["centroid_lat"] = centroids.y
        gdf["centroid_lng"] = centroids.x
        gdf = gdf[[
            "ufid",
            "building_name",
            "dong_name",
            "pnu",
            "ground_floor",
            "underground_floor",
            "height_m",
            "height_source",
            "height_confidence",
            "structure_code",
            "usability_code",
            "zone_type",
            "region_code",
            "centroid_lat",
            "centroid_lng",
            "source_file",
            "created_at",
            "geometry",
        ]].copy()

        file_name = f"buildings_{dataset.region_code}.parquet"
        out_path = processed_dir / file_name
        gdf.to_parquet(out_path)
        postgis_ready, warnings = _postgis_hint()
        if postgis_ready:
            upsert_buildings(gdf)

        region_bounds = add_spatial_metadata(gdf)
        count = int(len(gdf))
        available = int((gdf["height_source"] == "height_field").sum())
        estimated = count - available
        region_map[dataset.region_code] = {
            "region_code": dataset.region_code,
            "file": file_name,
            "count": count,
            "height_available_count": available,
            "height_estimated_count": estimated,
            "encoding": dataset.encoding or "euc-kr",
            "bounds": region_bounds,
        }

    regions = sorted(region_map.values(), key=lambda item: item["region_code"])
    total_count = sum(int(item.get("count", 0)) for item in regions)
    height_available_count = sum(int(item.get("height_available_count", 0)) for item in regions)
    height_estimated_count = sum(int(item.get("height_estimated_count", 0)) for item in regions)

    postgis_ready, warnings = _postgis_hint()
    meta = {
        "raw_dir": str(raw_dir),
        "processed_dir": str(processed_dir),
        "regions": regions,
        "building_count": total_count,
        "height_available_count": height_available_count,
        "height_estimated_count": height_estimated_count,
        "postgis_ready": postgis_ready,
        "last_preprocess_time": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
    }
    (processed_dir / "index.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> None:
    meta = preprocess_buildings()
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
