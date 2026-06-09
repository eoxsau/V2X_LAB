from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from .standard_link_file_checker import default_raw_dir


def load_raw_links(raw_dir: Path | None = None) -> gpd.GeoDataFrame:
    raw_dir = raw_dir or default_raw_dir()
    return gpd.read_file(raw_dir / "MOCT_LINK.shp")

