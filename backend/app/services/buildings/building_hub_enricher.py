"""건축물대장 API를 사용해 GeoDataFrame의 건물 높이를 보강(enrichment)한다.

사용법:
    gdf = enrich_gdf_with_bldrgst_api(gdf, api_key="YOUR_KEY")

  - pnu 컬럼이 없거나 api_key가 비어있으면 원본 GDF를 그대로 반환한다.
  - API 응답에서 heit, grndFlrCnt, mainPurpsCdNm, totArea, arhArea 필드를 추가한다.
  - 이후 building_height_estimator.estimate_building_height()가 이 필드를 우선 활용한다.
"""
from __future__ import annotations

import asyncio
import logging

import geopandas as gpd
import pandas as pd

from .bldrgst_api_client import BldRgstApiClient

log = logging.getLogger(__name__)

_API_FIELDS = ("heit", "grndFlrCnt", "mainPurpsCdNm", "totArea", "arhArea", "zone_type")


async def _enrich_async(
    gdf: gpd.GeoDataFrame,
    client: BldRgstApiClient,
    max_queries: int,
) -> gpd.GeoDataFrame:
    if "pnu" not in gdf.columns:
        log.warning("GDF has no 'pnu' column — skipping API enrichment")
        return gdf

    for col in _API_FIELDS:
        if col not in gdf.columns:
            gdf[col] = None

    queried = enriched = 0
    for idx, row in gdf.iterrows():
        pnu = str(row.get("pnu") or "").strip()
        if not pnu or pnu in ("nan", "None") or len(pnu) != 19:
            continue
        if queried >= max_queries:
            log.info("API enrichment stopped at max_queries=%d", max_queries)
            break

        result = await client.query_pnu_async(pnu)
        queried += 1
        if result is None:
            continue

        enriched += 1
        for field in _API_FIELDS:
            val = result.get(field)
            if val is None:
                continue
            # zone_type: V-World가 이미 채웠으면 유지
            if field == "zone_type":
                existing = gdf.at[idx, "zone_type"]
                if existing is not None and pd.notna(existing):
                    continue
            gdf.at[idx, field] = val

    log.info(
        "Building Hub enrichment done: %d queried / %d enriched / %d total",
        queried, enriched, len(gdf),
    )
    return gdf


def enrich_gdf_with_bldrgst_api(
    gdf: gpd.GeoDataFrame,
    api_key: str,
    *,
    max_queries: int = 5_000,
) -> gpd.GeoDataFrame:
    """Enrich GDF building heights via 건축물대장 API.

    Runs in a new event loop so it is safe to call from both sync and FastAPI
    contexts.
    """
    if not api_key:
        return gdf

    client = BldRgstApiClient(api_key)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _enrich_async(gdf.copy(), client, max_queries=max_queries)
        )
    except Exception as exc:
        log.warning("Building Hub enrichment failed: %s", exc)
        return gdf
    finally:
        loop.close()
