from __future__ import annotations

import geopandas as gpd


TARGET_CRS = "EPSG:4326"


def to_epsg4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise ValueError("Source GeoDataFrame has no CRS.")
    if str(gdf.crs).upper() == TARGET_CRS:
        return gdf
    return gdf.to_crs(TARGET_CRS)

