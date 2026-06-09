from __future__ import annotations

import geopandas as gpd

from .building_schema import BuildingDatasetInfo


def load_building_dataset(dataset: BuildingDatasetInfo) -> gpd.GeoDataFrame:
    encoding = dataset.encoding or "euc-kr"
    return gpd.read_file(dataset.shp, encoding=encoding)
