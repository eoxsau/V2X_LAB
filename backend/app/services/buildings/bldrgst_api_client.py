"""국토교통부 건축HUB 건축물대장정보 서비스 API 클라이언트.

Endpoint : https://apis.data.go.kr/1613000/BldRgstHubService
Auth     : serviceKey (일반 인증키, URL-decoded form)
Quota    : 1 req/s (free tier)

PNU 분해 규칙 (19자리 → API 파라미터):
  [0:5]   sigunguCd  — 시도(2)+시군구(3) 합산 5자리  (예: 11680 = 서울 강남구)
  [5:10]  bjdongCd   — 법정동코드 5자리              (예: 10300 = 개포동)
  [10]    platGbCd   — 대지구분코드 1자리 (0=토지, 1=산, 2=도로)
  [11:15] bun        — 본번 4자리 (leading zeros 유지)
  [15:19] ji         — 부번 4자리 (leading zeros 유지)

공식 예시: getBrTitleInfo?sigunguCd=11680&bjdongCd=10300&bun=0012&ji=0000
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService"


def _pnu_to_params(pnu: str) -> dict[str, str] | None:
    """PNU 19자리를 API 쿼리 파라미터로 분해."""
    pnu = pnu.strip()
    if len(pnu) != 19:
        return None
    return {
        "sigunguCd": pnu[0:5],    # 시도(2)+시군구(3) 합산 5자리
        "bjdongCd":  pnu[5:10],   # 법정동코드 5자리
        "platGbCd":  pnu[10],     # 대지구분 1자리
        "bun":       pnu[11:15],  # 본번 4자리 (zero-padded 유지)
        "ji":        pnu[15:19],  # 부번 4자리 (zero-padded 유지)
    }


class BldRgstApiClient:
    """건축물대장 표제부 조회 클라이언트."""

    _RATE_INTERVAL = 1.05   # seconds (slightly above 1 req/s)

    def __init__(self, api_key: str) -> None:
        self._key = api_key.strip()
        self._last_call: float = 0.0
        self._cache: dict[str, dict[str, Any] | None] = {}

    # ── public helpers ───────────────────────────────────────────────────────
    def query_pnu_sync(self, pnu: str) -> dict[str, Any] | None:
        """Synchronous entry point (runs a new event loop)."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.query_pnu_async(pnu))
        finally:
            loop.close()

    async def query_pnu_async(self, pnu: str) -> dict[str, Any] | None:
        if pnu in self._cache:
            return self._cache[pnu]
        addr = _pnu_to_params(pnu)
        if addr is None:
            log.debug("Invalid PNU length: %r", pnu)
            self._cache[pnu] = None
            return None

        title   = await self._get("getBrTitleInfo",  addr)
        jijigu  = await self._get("getBrJijiguInfo", addr)

        if title is None and jijigu is None:
            self._cache[pnu] = None
            return None

        result: dict[str, Any] = {}
        if title:
            result.update(title)
        if jijigu:
            result["zone_type"] = jijigu.get("zone_type")

        self._cache[pnu] = result
        return result

    # ── internals ────────────────────────────────────────────────────────────
    async def _get(self, operation: str, addr: dict[str, str]) -> dict[str, Any] | None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._RATE_INTERVAL:
            await asyncio.sleep(self._RATE_INTERVAL - elapsed)
        self._last_call = time.monotonic()

        params = {
            "serviceKey": self._key,
            "numOfRows":  "1",
            "pageNo":     "1",
            "_type":      "json",
            **addr,
        }
        url = f"{_BASE_URL}/{operation}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
        except Exception as exc:
            log.warning("BldRgst HTTP error (%s): %s", operation, exc)
            return None

        if resp.status_code != 200:
            log.warning("BldRgst API %d (%s) — addr=%s", resp.status_code, operation, addr)
            return None
        return _parse_response(operation, resp.text)


_JIJIGU_ZONE_MAP: dict[str, str] = {
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
    "일반주거지역":      "일반주거",
    "준주거지역":        "일반주거",
    "근린상업지역":      "상업",
    "일반상업지역":      "상업",
    "유통상업지역":      "상업",
    "중심상업지역":      "업무중심",
    "전용공업지역":      "산업",
    "일반공업지역":      "산업",
    "준공업지역":        "산업",
}


def _map_jijigu(name: str) -> str:
    z = (name or "").strip()
    if z in _JIJIGU_ZONE_MAP:
        return _JIJIGU_ZONE_MAP[z]
    for keyword, cat in (("전용주거", "저밀도주거"), ("일반주거", "일반주거"),
                          ("준주거", "일반주거"), ("중심상업", "업무중심"),
                          ("상업", "상업"), ("공업", "산업"),
                          ("녹지", "저밀도주거"), ("농림", "저밀도주거")):
        if keyword in z:
            return cat
    return "혼합"


def _parse_response(operation: str, text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        header = data["response"]["header"]
        if header.get("resultCode", "") != "00":
            log.debug("BldRgst (%s) non-OK: %s", operation, header.get("resultMsg"))
            return None
        body  = data["response"]["body"]
        total = int(body.get("totalCount") or 0)
        if total == 0:
            return None
        items = body.get("items") or {}
        item  = items.get("item")
        if not item:
            return None
        if isinstance(item, list):
            item = item[0]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.debug("BldRgst (%s) parse error: %s", operation, exc)
        return None

    if operation == "getBrTitleInfo":
        return {
            "heit":          item.get("heit"),
            "grndFlrCnt":    item.get("grndFlrCnt"),
            "ugrndFlrCnt":   item.get("ugrndFlrCnt"),
            "mainPurpsCdNm": item.get("mainPurpsCdNm") or item.get("etcPurps"),
            "totArea":       item.get("totArea"),
            "arhArea":       item.get("arhArea"),
            "bldNm":         item.get("bldNm"),
            "strctCdNm":     item.get("strctCdNm"),
            "useAprDay":     item.get("useAprDay"),
        }

    if operation == "getBrJijiguInfo":
        # 용도지역(jijiguGbCd=1) 항목 중 대표(reprYn=1)를 우선 사용
        if isinstance(items.get("item"), list):
            all_items = items["item"]
        else:
            all_items = [item]
        zone_name = None
        for it in all_items:
            if str(it.get("jijiguGbCd", "")) == "1":
                candidate = str(it.get("jijiguCdNm") or "").strip()
                if candidate:
                    zone_name = candidate
                    if str(it.get("reprYn", "")) == "1":
                        break
        if zone_name:
            return {"zone_type": _map_jijigu(zone_name)}

    return None
