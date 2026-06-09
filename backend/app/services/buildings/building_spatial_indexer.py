from __future__ import annotations

import geopandas as gpd


def add_spatial_metadata(gdf: gpd.GeoDataFrame) -> dict:
    if gdf.empty:
        return {"min_lng": None, "min_lat": None, "max_lng": None, "max_lat": None}
    bounds = gdf.total_bounds
    return {
        "min_lng": float(bounds[0]),
        "min_lat": float(bounds[1]),
        "max_lng": float(bounds[2]),
        "max_lat": float(bounds[3]),
    }
