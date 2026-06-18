"""V-World Data API 클라이언트 — LT_C_UQ111 (용도지역) 레이어 조회.

API  : https://api.vworld.kr/req/data
Layer: LT_C_UQ111  (용도지역지구구역)
Key  : VWORLD_API_KEY (환경변수)

응답 속성:
  uname      — 용도지역명 (예: "제1종일반주거지역", "일반상업지역")
  dyear      — 지정연도
  sido_name  — 시도명
  sigg_name  — 시군구명

용도지역명 → zone_type 매핑:
  저밀도주거  (전용주거·녹지·관리·농림·자연환경보전)
  일반주거    (일반주거·준주거)
  상업        (근린·일반·유통상업)
  업무중심    (중심상업)
  산업        (전용·일반·준공업)
  혼합        (기타·분류 불명)
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

import geopandas as gpd
from shapely.geometry import shape

log = logging.getLogger(__name__)

_DATA_URL = "https://api.vworld.kr/req/data"
_LAYER    = "LT_C_UQ111"
_PAGE_SIZE = 1_000
_MAX_PAGES = 20    # 최대 20 000개 폴리곤 (도시 전체 커버에 충분)

# ─── 용도지역명 → 높이 추정 카테고리 (실제 API 응답값 기준) ─────────────────
_EXACT: dict[str, str] = {
    "제1종전용주거지역": "저밀도주거",
    "제2종전용주거지역": "저밀도주거",
    "보전녹지지역":      "저밀도주거",
    "생산녹지지역":      "저밀도주거",
    "자연녹지지역":      "저밀도주거",
    "관리지역":          "저밀도주거",
    "농림지역":          "저밀도주거",
    "자연환경보전지역":  "저밀도주거",
    "제1종일반주거지역": "일반주거",
    "제2종일반주거지역": "일반주거",
    "제3종일반주거지역": "일반주거",
    "준주거지역":        "일반주거",
    "근린상업지역":      "상업",
    "일반상업지역":      "상업",
    "유통상업지역":      "상업",
    "중심상업지역":      "업무중심",
    "전용공업지역":      "산업",
    "일반공업지역":      "산업",
    "준공업지역":        "산업",
}

_PARTIAL: list[tuple[str, str]] = [
    ("전용주거",   "저밀도주거"),
    ("녹지",       "저밀도주거"),
    ("농림",       "저밀도주거"),
    ("자연환경",   "저밀도주거"),
    ("관리",       "저밀도주거"),
    ("일반주거",   "일반주거"),
    ("준주거",     "일반주거"),
    ("중심상업",   "업무중심"),
    ("상업",       "상업"),
    ("공업",       "산업"),
]


def map_zone_name(name: str) -> str:
    """V-World uname → height estimation category."""
    z = (name or "").strip()
    if not z:
        return "혼합"
    if z in _EXACT:
        return _EXACT[z]
    for keyword, cat in _PARTIAL:
        if keyword in z:
            return cat
    return "혼합"


class VWorldZoneClient:
    """V-World Data API 용도지역 폴리곤 조회 (LT_C_UQ111, EPSG:4326)."""

    def __init__(self, api_key: str, domain: str = "localhost") -> None:
        self._key    = api_key.strip()
        self._domain = domain

    def fetch_zones(
        self,
        minx: float,
        miny: float,
        maxx: float,
        maxy: float,
    ) -> gpd.GeoDataFrame | None:
        """bbox 내 용도지역 폴리곤을 GeoDataFrame으로 반환. 실패 시 None."""
        bbox_str = f"BOX({minx},{miny},{maxx},{maxy})"
        rows: list[dict] = []

        for page in range(1, _MAX_PAGES + 1):
            params: dict[str, str] = {
                "service":    "data",
                "request":    "GetFeature",
                "data":       _LAYER,
                "geometry":   "true",
                "attribute":  "true",
                "page":       str(page),
                "size":       str(_PAGE_SIZE),
                "key":        self._key,
                "domain":     self._domain,
                "crs":        "EPSG:4326",
                "geomFilter": bbox_str,
            }
            url = f"{_DATA_URL}?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
            except Exception as exc:
                log.warning("V-World Data API error (page %d): %s", page, exc)
                break

            parsed = self._parse_page(body)
            if parsed is None:
                break
            features, total_pages = parsed
            rows.extend(features)
            log.debug("V-World LT_C_UQ111: page %d/%d (%d rows)", page, total_pages, len(rows))
            if page >= total_pages:
                break

        if not rows:
            log.info("V-World LT_C_UQ111: 0 features for bbox %s", bbox_str)
            return None

        log.info("V-World LT_C_UQ111: %d zone polygons fetched", len(rows))
        return gpd.GeoDataFrame(rows, crs="EPSG:4326")

    @staticmethod
    def _parse_page(text: str) -> tuple[list[dict], int] | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("V-World JSON parse error: %s", exc)
            return None

        status = data.get("response", {}).get("status", "")
        if status != "OK":
            log.warning("V-World Data API status: %s", status)
            return None

        try:
            total_pages = int(data["response"]["page"]["total"])
            features_raw = data["response"]["result"]["featureCollection"]["features"]
        except (KeyError, ValueError):
            return None

        rows: list[dict] = []
        for feat in features_raw:
            geom_raw = feat.get("geometry")
            if not geom_raw:
                continue
            props    = feat.get("properties") or {}
            zone_nm  = str(props.get("uname") or "").strip()
            rows.append({
                "geometry":  shape(geom_raw),
                "zone_name": zone_nm,
                "zone_type": map_zone_name(zone_nm),
            })

        return rows, total_pages
