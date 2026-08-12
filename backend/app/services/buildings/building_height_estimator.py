from __future__ import annotations

import pandas as pd

# ⚠️ 이 파일의 표들(층고·기본높이·면적구간·용도지역 높이)은 대부분 출처 표기가 없다
# (2026-08-05 감사). 아파트 층고 2.9m 항목만 확인됨 — 국내 신축 아파트 층고는 통상
# 2.8~2.85m로 시공되고(법정 최소는 주택건설기준등에관한규정상 2.2m), 코드값과 근접해
# 실제 시공 관행과 부합함을 확인했다. 나머지 유형별 층고·기본높이·면적/용도지역 구간은
# 값 자체가 상식적인 범위 안에 있어 보이지만 특정 통계·법령·논문과 직접 대조되지 않은
# 추정 설계값으로 취급해야 한다 — 6G 파라미터에 준하는 신뢰도로 보고, 실제 논문에 쓰려면
# 국가통계(건축물대장 통계, 국토교통부) 등 1차 자료 대조가 필요하다.
# ─── Floor height (metres per floor) by building type ──────────────────────
_FLOOR_HEIGHT_BY_TYPE: dict[str, float] = {
    "detached_house":   3.0,    # 단독주택
    "multi_unit_house": 3.0,    # 다가구·다세대·연립
    "apartment":        2.9,    # 공동주택·아파트  (2.8–3.0 mid)
    "commercial":       3.5,    # 근린생활·판매·숙박·위락
    "office":           3.8,    # 업무시설
    "factory":          6.0,    # 공장  (5.0–7.0 mid)
    "warehouse":        6.0,    # 창고  (5.0–7.0 mid)
    "school":           3.75,   # 교육·연구·학교  (3.5–4.0 mid)
    "public":           3.75,   # 노유자·수련·문화·종교  (≈ school)
    "hospital":         4.0,    # 의료시설
    "industrial":       5.0,    # 발전·방송·위험물·교정
    "unknown":          3.2,    # 용도 미상
}

# ─── Default total height (metres) by building type ─────────────────────────
_DEFAULT_HEIGHT_BY_TYPE: dict[str, float] = {
    "detached_house":   7.5,    # 6–9 m
    "multi_unit_house": 12.0,   # 9–15 m
    "apartment":        55.0,   # 30–80 m
    "commercial":       13.5,   # 9–18 m  저층 상가
    "commercial_large": 40.0,   # 20–60 m 대형 상업
    "office":           30.0,   # 20–40 m
    "factory":          14.0,   # 8–20 m
    "warehouse":        10.5,   # 6–15 m
    "school":           15.0,   # 10–20 m
    "public":           15.0,
    "hospital":         35.0,   # 20–50 m
    "industrial":       15.0,
    "unknown":          15.0,   # 10–20 m
}

# ─── Footprint / total area → height thresholds ─────────────────────────────
# Each entry: (upper_bound_m2, height_m)
_AREA_THRESHOLDS: list[tuple[float, float]] = [
    (100.0,          6.0),   # < 100 m²        → 4–8 m   → 6.0 m
    (300.0,         10.5),   # 100–300 m²       → 6–15 m  → 10.5 m
    (1_000.0,       17.5),   # 300–1 000 m²     → 10–25 m → 17.5 m
    (3_000.0,       27.5),   # 1 000–3 000 m²   → 15–40 m → 27.5 m
    (float("inf"),  50.0),   # ≥ 3 000 m²       → 20–80 m → 50.0 m
]

# ─── Zone type → height (midpoint of distribution) ──────────────────────────
# zone_type field is populated by spatial join with 도시계획 레이어 (optional).
# Expected string values (Korean or English key):
#   "저밀도주거" / "low_density_residential"  → 3–12 m  → 7.5 m
#   "일반주거"   / "residential"              → 6–25 m  → 15.5 m
#   "상업"       / "commercial_zone"          → 10–60 m → 35.0 m
#   "업무중심"   / "business_district"        → 20–100 m→ 60.0 m
#   "산업"       / "industrial_zone"          → 6–25 m  → 15.5 m
#   "혼합"       / "mixed"                    → 6–50 m  → 28.0 m
_ZONE_HEIGHT: dict[str, float] = {
    "저밀도주거":            7.5,
    "low_density_residential": 7.5,
    "일반주거":              15.5,
    "residential":           15.5,
    "상업":                  35.0,
    "commercial_zone":       35.0,
    "업무중심":              60.0,
    "business_district":     60.0,
    "산업":                  15.5,
    "industrial_zone":       15.5,
    "혼합":                  28.0,
    "mixed":                 28.0,
}


def _classify(usability_code=None, purps_name: str | None = None) -> str:
    """Map 용도코드 (int/str) or mainPurpsCdNm (str) → height category."""
    code_str = ""
    name_str = ""

    if usability_code is not None:
        s = str(usability_code).strip()
        if s and s.lower() not in ("nan", "none", ""):
            code_str = s.zfill(5)
    if purps_name is not None:
        name_str = str(purps_name).strip()

    # ── 용도코드 5자리 기반 분류 ─────────────────────────────────────────
    if code_str:
        prefix = code_str[:2]
        if prefix == "01":
            return "multi_unit_house" if code_str == "01030" else "detached_house"
        if prefix == "02":
            return "apartment" if code_str in ("02000", "02001") else "multi_unit_house"
        if prefix in ("03", "04", "07", "15", "16", "27"):
            return "commercial"
        if prefix == "14":
            return "office"
        if prefix == "17":
            return "factory"
        if prefix == "18":
            return "warehouse"
        if prefix in ("10", "11", "12"):
            return "school"
        if prefix == "09":
            return "hospital"
        if prefix in ("05", "06", "08", "13", "20", "21", "23", "24", "26", "28"):
            return "public"
        if prefix in ("19", "22", "25"):
            return "industrial"

    # ── API mainPurpsCdNm 한글명 기반 분류 ──────────────────────────────
    if name_str:
        if "단독주택" in name_str:
            return "detached_house"
        if any(k in name_str for k in ("다가구", "다세대", "연립")):
            return "multi_unit_house"
        if "아파트" in name_str:
            return "apartment"
        if "공동주택" in name_str:
            return "apartment"
        if any(k in name_str for k in ("근린생활", "판매", "숙박", "위락", "관광")):
            return "commercial"
        if "업무" in name_str:
            return "office"
        if "공장" in name_str or "제조" in name_str:
            return "factory"
        if "창고" in name_str or "물류" in name_str:
            return "warehouse"
        if any(k in name_str for k in ("교육", "연구", "학교", "학원")):
            return "school"
        if any(k in name_str for k in ("노유자", "수련", "복지")):
            return "public"
        if any(k in name_str for k in ("의료", "병원")):
            return "hospital"
        if any(k in name_str for k in ("문화", "집회", "종교", "운동", "운수")):
            return "public"
        if any(k in name_str for k in ("발전", "방송", "교정", "군사", "위험")):
            return "industrial"

    return "unknown"


def _area_height(area_m2: float) -> float:
    for threshold, h in _AREA_THRESHOLDS:
        if area_m2 < threshold:
            return h
    return 50.0


def estimate_building_height(row: pd.Series) -> tuple[float, str, str]:
    """Return (height_m, source_label, confidence_label).

    6-tier cascade:
      1. Direct height field  — shapefile HEIGHT or API heit
      2. Floor count × type floor height  — grndFlrCnt or GRND_FLR
      3. Building type default height  — from usability code or API name
      4. Footprint / total area height  — footprint_area_m2 / totArea / arhArea
      5. Zone type midpoint  — zone_type field (도시계획 레이어 조인 시 활용)
      6. Hard-coded 10 m fallback
    """
    # ── Tier 1: direct height measurement ──────────────────────────────────
    for field in ("heit", "HEIGHT"):
        raw = row.get(field)
        if raw is not None and pd.notna(raw):
            try:
                h = float(raw)
                if h > 0:
                    return round(h, 2), "height_field", "high"
            except (TypeError, ValueError):
                pass

    # classify for tiers 2 & 3
    btype = _classify(
        usability_code=row.get("USABILITY") or row.get("usability_code"),
        purps_name=row.get("mainPurpsCdNm"),
    )
    floor_h = _FLOOR_HEIGHT_BY_TYPE.get(btype, 3.2)

    # ── Tier 2: floor count × floor height ─────────────────────────────────
    for field in ("grndFlrCnt", "GRND_FLR", "ground_floor"):
        raw = row.get(field)
        if raw is not None and pd.notna(raw):
            try:
                floors = float(raw)
                if floors > 0:
                    return round(floors * floor_h, 2), "floor_estimated", "medium"
            except (TypeError, ValueError):
                pass

    # ── Tier 3: building type default ──────────────────────────────────────
    if btype != "unknown":
        if btype == "commercial":
            for area_field in ("totArea", "arhArea", "footprint_area_m2"):
                raw = row.get(area_field)
                if raw is not None and pd.notna(raw):
                    try:
                        if float(raw) >= 3_000:
                            return 40.0, "type_estimated", "medium_low"
                    except (TypeError, ValueError):
                        pass
        h = _DEFAULT_HEIGHT_BY_TYPE.get(btype, 15.0)
        return round(h, 2), "type_estimated", "medium_low"

    # ── Tier 4: area-based height ───────────────────────────────────────────
    for area_field in ("totArea", "arhArea", "footprint_area_m2"):
        raw = row.get(area_field)
        if raw is not None and pd.notna(raw):
            try:
                a = float(raw)
                if a > 0:
                    return round(_area_height(a), 2), "area_estimated", "low"
            except (TypeError, ValueError):
                pass

    # ── Tier 5: zone type midpoint ──────────────────────────────────────────
    # Populated by spatial join with 도시계획 레이어; None/NaN if unavailable.
    zone_raw = row.get("zone_type")
    if zone_raw is not None and pd.notna(zone_raw):
        zone_key = str(zone_raw).strip()
        # normalise: strip trailing numbers / whitespace, try both Korean and English
        zone_h = _ZONE_HEIGHT.get(zone_key)
        if zone_h is None:
            # partial match — e.g. "제1종일반주거지역" → 일반주거
            for key, h in _ZONE_HEIGHT.items():
                if key in zone_key or zone_key in key:
                    zone_h = h
                    break
        if zone_h is not None:
            return round(zone_h, 2), "zone_estimated", "very_low"

    # ── Tier 6: final fallback ───────────────────────────────────────────────
    return 10.0, "default_estimated", "very_low"


def material_category_from_strctcd(strct_cd_nm: str | None) -> str:
    """Map 건축물대장 strctCdNm to a material category for wall-penetration loss."""
    if not strct_cd_nm:
        return "unknown"
    s = str(strct_cd_nm).strip()
    # Order matters: "철골철근콘크리트" must be matched before bare "철골"
    if "철골철근" in s or "철근콘크리트" in s:
        return "rc"
    if any(k in s for k in ("철골", "경량철골", "강구조", "강재")):
        return "steel"
    if any(k in s for k in ("목구조", "목조", "일반목", "경목")):
        return "wood"
    if any(k in s for k in ("조적", "벽돌", "블록", "석조")):
        return "brick"
    if any(k in s for k in ("ALC", "경량콘크리트", "PC조", "프리캐스트")):
        return "light_concrete"
    return "unknown"
