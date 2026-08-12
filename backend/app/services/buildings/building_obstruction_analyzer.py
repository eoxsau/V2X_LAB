from __future__ import annotations

from math import exp, hypot, log10

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.strtree import STRtree

from .building_schema import BuildingObstructionResult
from .building_height_estimator import material_category_from_strctcd, _classify

_DEFAULT_ANTENNA_HEIGHT_M = 25.0  # 높이 정보 없는 기지국 기본값
_VEHICLE_HEIGHT_M = 1.5           # 차량 OBU 안테나 높이 (지상 기준)

# ── Material wall-crossing attenuation (dB per effective wall) ─────────────────
_MATERIAL_WALL_LOSS: dict[str, float] = {
    "rc":             15.0,   # 철근콘크리트
    "steel":           8.0,   # 철골구조
    "brick":          12.0,   # 조적조/벽돌
    "wood":            6.0,   # 목구조
    "light_concrete":  9.0,   # ALC/경량콘크리트
    "unknown":        10.0,   # 재질 미상
}

# ── Average effective wall spacing by building use type (m) ───────────────────
_WALL_SPACING_BY_USE: dict[str, float] = {
    "detached_house":   5.0,
    "multi_unit_house": 6.0,
    "apartment":        8.0,
    "commercial":       7.0,
    "office":          10.0,
    "factory":         15.0,
    "warehouse":       20.0,
    "school":           8.0,
    "public":           8.0,
    "hospital":        10.0,
    "industrial":      15.0,
    "unknown":          8.0,
}

# ── Latency model — formula_v31로 전면 이관 (설계문서 latency_formula_v3_1.md) ──
# 기술별 파라미터·MCS 테이블·PL/SINR/α는 이제 app.services.latency.formula_v31이
# 단일 출처다. 이 파일의 _L_total은 하위 호환 래퍼로만 남는다.
from app.services.latency import formula_v31

# main.py 등 기존 임포트 호환용 별칭
_RSU_COVERAGE_RADIUS_M = formula_v31.RSU_COVERAGE_RADIUS_M

# A_seg = 3D LOS를 가로막는 건물 수 × 12 dB (명세 §7)
_A_SEG_PER_BUILDING_DB = 12.0

# 상한 30 dB — route_cost_function.NormScales.loss_db(콘크리트 벽 최대)와 같은 값으로
# 맞춘다. 명세 §7 원문은 "선형 누적·상한 없음"이었으나, 조밀한 시가지 격자에서는 3D
# LOS 판정을 만족하는 건물이 흔히 5~10채(60~120dB)까지 나와 물리적으로 불가능한
# 값이 된다 — 실제로는 2~3개 건물을 넘어서면 신호가 그 건물들을 "관통"하는 게
# 아니라 회절·다중경로로 우회하므로, 선형 누적이 물리를 대표하지 못한다.
#
# ⚠️ "건물 수 × 12dB" 자체는 여전히 출처를 대지 못한 휴리스틱이다(2026-08-05 재검토).
# 이 시나리오(고가 기지국—도로변 차량, 건물에 "들어가지" 않고 옆에서 가려지는 경우)에
# 맞는 정식 모델은 3GPP O2I(옥외→실내 투과손실, 명세상 다른 시나리오 — 벽을 "통과해
# 들어가는" 손실이라 여기 안 맞음)가 아니라 ITU-R P.1411의 다중 스크린 회절
# (multiple screen diffraction, Lbf+Lrts+Lmsd) 계열이다 — 다만 이 모델은 거리·건물
# 배치·도로폭·입사각을 함께 쓰는 다변수 공식이라 "건물당 N dB" 스칼라로 축약되지
# 않는다. 30dB 상한은 그 공식을 대체한 값이 아니라 — "상한 없음"보다는 나은,
# 그러나 검증되지 않은 안전장치(c_blockage·대시보드로 값이 새는 것을 막는 것)로만
# 취급해야 한다. 제대로 하려면 ITU-R P.1411 모델을 별도로 구현해야 한다.
# outage 판정(SINR<-6dB) 자체는 이미 1~2개
# 건물만으로도 대부분 걸리므로, 이 상한은 outage 여부보다 근처(비outage) 구간의
# 수치 스케일을 물리적으로 유의미하게 유지하는 데 의미가 있다.
_A_SEG_MAX_DB = 30.0

# ── A_seg 사전계산 캐시 (명세 §7 — 매 스텝 재계산 방지) ─────────────────────────
# 키: (차량 위치 1e-5도≈1m 격자, 노드 id/위치/안테나, 밀도 페널티, 건물셋 버전).
# 경로 엣지 중점처럼 같은 지점을 반복 평가하는 호출(K-path 2초 주기 재점수)에 적중.
_OBSTRUCTION_CACHE: dict[tuple, "BuildingObstructionResult"] = {}
_OBSTRUCTION_CACHE_MAX = 20_000


def reset_obstruction_cache() -> None:
    _OBSTRUCTION_CACHE.clear()


# ── 건물 공간 인덱스 사전계산 (2026-08-08) ────────────────────────────────────
# 이전 구현은 analyze_vehicle_to_node 호출마다 다음 셋을 전부 반복했다:
#   (1) 선 하나짜리 GeoDataFrame 생성 + to_crs(3857)
#   (2) buildings_gdf.geometry.intersects(line) — 공간 인덱스 없이 전 건물 스캔
#   (3) 걸린 건물들을 다시 to_crs(3857)
# 이 함수는 기지국 수만큼(예: 54회) × 엣지마다 불리므로, 경로탐색 중에 건물 차폐를
# 켜면 감당이 안 됐다. 실측(건물 2000개·기지국 54개): 엣지 1개당 336ms →
# 14,248엣지 네트워크에서 경로 한 번에 1시간 20분.
#
# 건물 집합은 경로탐색 한 번 동안 바뀌지 않으므로 3857 변환과 STRtree를 한 번만
# 만들어 재사용한다. 측정 결과 엣지 1개당 336ms → 42ms (약 8배).
#
# ⚠ 결과는 이전 구현과 **비트 단위로** 같다. 공간 인덱스는 근사가 아니라 "교차할 리 없는
# 건물을 검사하지 않을 뿐"이고, 좌표 변환도 매번 같은 값을 다시 만들던 것을 재사용할 뿐이다.
#
# 교차 후보 판정은 반드시 원본 좌표계(4326)에서 해야 한다. 3857에서 판정하도록 짰다가
# 3,600건 중 2건이 어긋났는데, 원인은 건물이 시선에서 **1.6 mm** 떨어져 스치는 경계
# 사례였다(4326에서는 안 닿고 3857에서는 닿음). Mercator의 y 비선형 항 때문에 두
# 좌표계의 직선이 완전히 겹치지는 않아서 생긴 차이다. 물리적으로는 의미 없는 차이지만
# 건물 1채가 곧 12 dB라 결과가 흔들리므로, 후보 판정용 STRtree는 4326으로 만들고
# 3857 지오메트리는 3D LOS 판정에만 쓴다.
_WGS84_TO_3857 = Transformer.from_crs(4326, 3857, always_xy=True)

_PREP_KEY: tuple | None = None
_PREP: dict = {}


def _prepare_buildings(buildings_gdf: "gpd.GeoDataFrame") -> dict:
    """건물 집합당 1회만 3857 지오메트리·높이·STRtree를 만들어 캐시한다.

    캐시 키에 id()를 쓰지만 _PREP가 gdf 자체를 붙들고 있으므로, 캐시가 살아있는
    동안 그 객체가 GC되어 주소가 재사용되는 일은 생기지 않는다.
    """
    global _PREP_KEY, _PREP
    key = (id(buildings_gdf), len(buildings_gdf))
    if _PREP_KEY == key and _PREP:
        return _PREP

    geoms_4326 = list(buildings_gdf.geometry) if len(buildings_gdf) else []
    geoms_3857 = list(buildings_gdf.to_crs(3857).geometry) if geoms_4326 else []
    _PREP = {
        "gdf": buildings_gdf,                       # 원본(4326) — 높이/속성/하이라이트용
        "geoms_3857": geoms_3857,                   # 3D LOS 판정 전용
        # 높이는 원본 컬럼을 그대로 보관한다. `float(v or 0.0)` 판정을 호출부에서
        # 예전과 똑같이 재현해야 하므로 여기서 fillna 하지 않는다.
        "heights_raw": list(buildings_gdf["height_m"]) if "height_m" in buildings_gdf.columns else None,
        # 후보 판정은 원본 좌표계에서 — 위 주석의 1.6 mm 경계 사례 참조.
        "tree": STRtree(geoms_4326) if geoms_4326 else None,
    }
    _PREP_KEY = key
    return _PREP


def reset_buildings_index() -> None:
    """건물 집합을 갈아끼웠을 때 사전계산 인덱스를 버린다."""
    global _PREP_KEY, _PREP
    _PREP_KEY, _PREP = None, {}


def _height_or_zero(value) -> float:
    """pandas `fillna(0)` + float() 과 같은 의미 — None·NaN·비수치는 0.0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f      # NaN 체크


def _L_rsu(distance_m: float, coverage_radius_m: float) -> float:
    """PC5 인터페이스(RSU–차량 직접 통신) 단방향 지연 (ms).

    셀룰러 Uu(_L_total)와 달리 HARQ 재전송·큐잉 없음 — 브로드캐스트/유니캐스트 채널에서
    재전송은 상위 레이어(V2X APP)가 독립적으로 처리하고, 단말 레벨 큐잉 지연이 사실상 없어
    3ms 이하의 매우 짧은 지연이 특징이다.

    모델: L_pc5 = L_access + L_proc (ms)
      L_access  ≈ 0.5ms  (PC5 공중 인터페이스 — 슬롯 배정·전송 지연 합산)
      L_proc    ≈ 0.5ms  (RSU 처리 지연)
      거리 패널티: 커버리지 경계에 가까울수록 재전송 확률이 선형 증가 →
                  1.0ms(중심) ~ 3.0ms(경계)의 선형 보간으로 근사한다.

    출처: 3GPP TR 36.885 §A.1(E2E latency budget), ETSI TR 102 638 §4.3.
    """
    ratio = max(0.0, min(1.0, distance_m / max(coverage_radius_m, 1.0)))
    return round(1.0 + ratio * 2.0, 3)


def _material_a_seg_db(
    buildings_bl: "gpd.GeoDataFrame",
    buildings_bl_3857: "gpd.GeoDataFrame",
    line_3857: LineString,
) -> tuple[float, str, float]:
    """Compute A_seg (total building penetration loss dB) from material + geometry.

    [미사용 — 롤백 보존] v3.1 명세(§7)가 A_seg를 '차단 건물 수 × 12 dB'로 단순화하면서
    analyze_vehicle_to_node에서 더 이상 호출하지 않는다. 재질 기반 모델로 되돌리려면
    analyze_vehicle_to_node의 A_seg 계산부에서 이 함수를 다시 호출하면 된다.

    Returns (A_seg_db, confidence, total_crossed_length_m).
    """
    if buildings_bl.empty:
        return 0.0, "high", 0.0

    per_bldg_crossed = buildings_bl_3857.geometry.intersection(line_3857).length
    total_crossed = float(per_bldg_crossed.sum())

    has_known_material = False
    A_seg_db = 0.0
    for i in range(len(buildings_bl)):
        row = buildings_bl.iloc[i]
        material = material_category_from_strctcd(row.get("strctCdNm"))
        if material != "unknown":
            has_known_material = True
        use_type = _classify(
            usability_code=row.get("USABILITY") or row.get("usability_code"),
            purps_name=row.get("mainPurpsCdNm"),
        )
        wall_spacing = _WALL_SPACING_BY_USE.get(use_type, 8.0)
        crossed = float(per_bldg_crossed.iloc[i])
        n_walls = max(1, round(crossed / wall_spacing))
        A_seg_db += n_walls * _MATERIAL_WALL_LOSS.get(material, 10.0)

    confidence = "medium" if has_known_material else "low"
    return round(A_seg_db, 2), confidence, round(total_crossed, 2)


def _L_total(
    distance_m: float,
    A_seg_db: float,
    n_vehicles: int,
    network_mode: str,
    deficit_ratio: float = 0.0,
) -> tuple[float, float, float, float]:
    """v3.1 통합 모델(formula_v31.compute_latency) 위임 래퍼 — 하위 호환용.

    반환 (L_total, L_base, L_transmission, L_queue). L_total은 RAN 구간만이며
    구모델과 달리 백홀+코어(L_transport)를 포함하지 않는다 (명세 §1: RAN only).
    호출부는 l_signal_ms 변수명으로 L_transmission을 받아 저장함 (API 필드명 유지).
    """
    r = formula_v31.compute_latency(
        network_mode, distance_m, n_vehicles, A_seg_db, deficit_ratio,
    )
    return r["l_total_ms"], r["l_base_ms"], r["l_transmission_ms"], r["l_queue_ms"]


def _is_blocked_3d(
    line_3857: LineString,
    bldg_geom_3857,
    bldg_height: float,
    h_vehicle: float,
    h_antenna: float,
) -> bool:
    """3D LOS 판단: 기지국-차량 빔이 건물 상단을 통과하지 못하면 True."""
    centroid = bldg_geom_3857.centroid
    t = line_3857.project(centroid, normalized=True)
    t = max(0.0, min(1.0, t))
    h_beam = h_vehicle + t * (h_antenna - h_vehicle)
    return h_beam < bldg_height


def analyze_vehicle_to_node(
    *,
    vehicle_id: str,
    vehicle_lat: float,
    vehicle_lng: float,
    network_node: dict,
    buildings_gdf: gpd.GeoDataFrame,
    vehicle_density_penalty: float = 0.0,
) -> BuildingObstructionResult:
    # ── §7 사전계산 캐시 — 같은 (지점, 노드) 쌍 반복 평가를 테이블 조회로 대체 ──
    h_antenna = float(network_node.get("antenna_height_m") or _DEFAULT_ANTENNA_HEIGHT_M)
    cache_key = (
        round(vehicle_lat, 5), round(vehicle_lng, 5),          # ~1m 격자
        str(network_node.get("id")),
        round(float(network_node.get("lat") or 0.0), 5),
        round(float(network_node.get("lng") or 0.0), 5),
        round(h_antenna, 1),
        round(vehicle_density_penalty, 2),
        len(buildings_gdf),                                     # 건물셋 버전 근사
    )
    cached = _OBSTRUCTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    line = LineString([(vehicle_lng, vehicle_lat), (network_node["lng"], network_node["lat"])])
    # 끝점만 3857로 옮겨 직선을 만든다. GeoDataFrame을 거치던 예전 코드와 결과가
    # 같다 — to_crs도 LineString의 꼭짓점(=끝점 2개)만 변환했기 때문이다.
    _vx, _vy = _WGS84_TO_3857.transform(vehicle_lng, vehicle_lat)
    _nx, _ny = _WGS84_TO_3857.transform(network_node["lng"], network_node["lat"])
    line_3857 = LineString([(_vx, _vy), (_nx, _ny)])
    # ⚠ 예전 동작 보존: 이 거리는 EPSG:3857(Web Mercator) 길이이므로 위도 37.5°에서
    # 실제보다 약 1/cos(37.5°)=1.26배 크다. _find_best_bs_light는 haversine을 쓰므로
    # 같은 쌍에 대해 두 경로가 서로 다른 거리를 낸다. 여기서 고치면 이번 변경이
    # '속도만 개선'이 아니게 되므로 그대로 두고 인수인계 문서에 남긴다.
    distance_m = hypot(_nx - _vx, _ny - _vy)

    if buildings_gdf.empty:
        result = BuildingObstructionResult(
            vehicle_id=vehicle_id,
            network_node_id=network_node["id"],
            distance_m=distance_m,
            intersected_building_count=0,
            max_building_height_m=0.0,
            estimated_penetration_loss_db=0.0,
            latency_penalty_ms=round(vehicle_density_penalty, 2),
            stability_score=max(0.0, 1.0 - vehicle_density_penalty / 50.0),
            confidence="high",
            highlighted_buildings=[],
        )
        _cache_obstruction(cache_key, result)
        return result

    # 2D 경로와 교차하는 건물 후보 — STRtree로 후보를 좁힌 뒤에만 정밀 판정한다.
    # (예전에는 전 건물을 매번 스캔했다. 위 '건물 공간 인덱스 사전계산' 주석 참조.)
    prep = _prepare_buildings(buildings_gdf)
    tree = prep["tree"]
    geoms_3857 = prep["geoms_3857"]
    heights_raw = prep["heights_raw"]

    cand_idx = (
        [int(i) for i in tree.query(line, predicate="intersects")]
        if tree is not None else []
    )

    # ── 3D LOS 필터 (명세 §7 — 2D 판정 금지) ──────────────────────────────────
    blocking_idx: list[int] = []
    unknown_height_blockers = 0
    for i in cand_idx:
        bldg_h = float(heights_raw[i] or 0.0) if heights_raw is not None else 0.0
        if bldg_h <= 0:
            # 높이 미상 — 보수적으로 차단 처리하되 신뢰도를 낮춘다
            blocking_idx.append(i)
            unknown_height_blockers += 1
        else:
            if _is_blocked_3d(
                line_3857, geoms_3857[i], bldg_h,
                _VEHICLE_HEIGHT_M, h_antenna,
            ):
                blocking_idx.append(i)

    # STRtree.query는 입력 순서를 보장하지 않는다. 예전 구현은 GeoDataFrame 행 순서를
    # 따랐고 highlighted_buildings가 그중 앞 5개를 잘라 쓰므로, 순서를 되돌려 둔다.
    blocking_idx.sort()
    count = int(len(blocking_idx))

    # 예전엔 차단 건물 전체로 DataFrame 슬라이스를 떠서 fillna().max()를 돌렸다. 호출
    # 하나당 pandas 왕복이라 이 함수가 수만 번 불리는 경로탐색에서는 무시할 수 없다.
    # fillna(0) 의미(None·NaN → 0)를 그대로 살려 파이썬 리스트에서 계산한다.
    if count and heights_raw is not None:
        max_height = max(_height_or_zero(heights_raw[i]) for i in blocking_idx)
    else:
        max_height = 0.0

    # ── A_seg = 차단 건물 수 × 12 dB, 상한 30dB (명세 §7 — 재질 기반 모델 대체) ──
    A_seg_db = round(min(count * _A_SEG_PER_BUILDING_DB, _A_SEG_MAX_DB), 2)
    confidence = "high" if unknown_height_blockers == 0 else "medium"

    # latency_penalty_ms: building-only contribution shown on dashboard (ms)
    latency_penalty_ms = round(A_seg_db * 0.45, 2)
    stability_score = round(max(0.0, exp(-(A_seg_db / 20.0)) - vehicle_density_penalty / 100.0), 3)

    # 하이라이트는 앞 5개만 쓰므로 그 5행만 꺼낸다(예전엔 차단 건물 전체를 슬라이스했다).
    highlighted_buildings = []
    for i in blocking_idx[:5]:
        row = buildings_gdf.iloc[i]
        geom = buildings_gdf.geometry.iloc[i]
        coords = []
        if geom.geom_type == "Polygon":
            coords = [{"lat": lat, "lng": lng} for lng, lat in list(geom.exterior.coords)]
        elif geom.geom_type == "MultiPolygon":
            poly = max(list(geom.geoms), key=lambda g: g.area)
            coords = [{"lat": lat, "lng": lng} for lng, lat in list(poly.exterior.coords)]
        highlighted_buildings.append({
            "id": row.get("ufid") or row.get("pnu"),
            "height_m": float(row.get("height_m", 0.0) or 0.0),
            "height_confidence": row.get("height_confidence", "low"),
            "material": material_category_from_strctcd(row.get("strctCdNm")),
            "geometry": coords,
        })

    result = BuildingObstructionResult(
        vehicle_id=vehicle_id,
        network_node_id=network_node["id"],
        distance_m=round(distance_m, 2),
        intersected_building_count=count,
        max_building_height_m=round(max_height, 2),
        estimated_penetration_loss_db=A_seg_db,
        latency_penalty_ms=latency_penalty_ms,
        stability_score=stability_score,
        confidence=confidence,
        highlighted_buildings=highlighted_buildings,
    )
    _cache_obstruction(cache_key, result)
    return result


def _cache_obstruction(key: tuple, result: BuildingObstructionResult) -> None:
    if len(_OBSTRUCTION_CACHE) >= _OBSTRUCTION_CACHE_MAX:
        _OBSTRUCTION_CACHE.clear()
    _OBSTRUCTION_CACHE[key] = result


def _planar_dist_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """평면 근사 지상거리(m). 수 km 범위에서 haversine과 차이가 무시할 만하다."""
    from math import cos, hypot, radians
    dlat = (lat2 - lat1) * 111_320.0
    dlng = (lng2 - lng1) * 111_320.0 * cos(radians((lat1 + lat2) * 0.5))
    return hypot(dlat, dlng)


# 커버리지 사전 필터의 안전 여유. `analyze_vehicle_to_node`가 재는 `distance_m`은
# **EPSG:3857(웹 메르카토르) 길이**라 위도 37도에서 지상거리보다 약 1/cos(lat)=1.26배 크다.
# 즉 정확한 판정은 `지상거리 <= R × cos(lat)`에 해당한다. 사전 필터가 실수로 후보를
# 떨어뜨리면 안 되므로 여기에 여유를 더 준다(값이 커질수록 안전하고 덜 걸러진다).
_COVERAGE_PREFILTER_MARGIN = 1.15


def analyze_candidates(
    *,
    vehicle_id: str,
    vehicle_lat: float,
    vehicle_lng: float,
    candidate_nodes: list[dict],
    buildings_gdf: gpd.GeoDataFrame,
    vehicle_density_penalty: float = 0.0,
    network_mode: str = "5G",
) -> list[dict]:
    results = []
    for node in candidate_nodes:
        node_type = node.get("type") or node.get("node_type")
        is_rsu = str(node_type or "").lower() in ("rsu", "rsu_node", "roadside_unit")
        # 커버리지 반경은 노드 저장값이 아니라 평가 시점의 기술 모드로 실시간 결정
        # (2026-07-16 결정) — 4G/5G/6G 전환이 기존 노드에도 즉시 반영된다.
        # BS = d_edge(α 앵커, 셀 안에서는 무차폐 시 SINR ≥ SINR_MIN 보장), RSU = PC5 반경.
        node_coverage_radius = formula_v31.resolve_coverage_radius(network_mode, node_type)

        # ⚠️ 커버리지 밖 노드는 **차폐 분석 전에** 버린다 (2026-07-28).
        # 아래 `analyze_vehicle_to_node`는 거리 하나를 얻으려고 호출마다 GeoDataFrame을
        # 만들고 `to_crs`로 좌표계를 바꾼다 — 호출당 ~20ms다. 예전엔 그걸 **전부 돌린 뒤**
        # 거리로 걸러내서, 노드 54개면 틱당 1.2~2.0초가 들었고 시뮬 루프의 80~90%를 먹었다.
        # 대부분의 노드는 애초에 커버리지 밖이라 값이 쓰이지도 않는다.
        from math import cos, radians
        _cos_lat = max(cos(radians(vehicle_lat)), 1e-6)
        if (_planar_dist_m(vehicle_lat, vehicle_lng,
                           float(node.get("lat") or 0.0), float(node.get("lng") or 0.0))
                > node_coverage_radius * _cos_lat * _COVERAGE_PREFILTER_MARGIN):
            continue

        obs = analyze_vehicle_to_node(
            vehicle_id=vehicle_id,
            vehicle_lat=vehicle_lat,
            vehicle_lng=vehicle_lng,
            network_node=node,
            buildings_gdf=buildings_gdf,
            vehicle_density_penalty=vehicle_density_penalty,
        )
        # 물리적 커버리지 반경 밖이면 후보에서 제외 — 노드별 반경 사용(RSU/BS 각각 다름).
        if obs.distance_m > node_coverage_radius:
            continue

        edge_latency = float(node.get("edge_latency_ms", 5.0))

        # RSRP — 연결 규칙(명세 §9)의 기준값. RSU는 명세 밖이지만 동일 수식으로
        # 근사해 하나의 순위 축을 유지한다 (P_tx·α 동일 전제, 도로 레벨이라 A_seg 작음).
        rsrp_dbm = round(formula_v31.compute_rsrp_dbm(
            network_mode, obs.distance_m, obs.estimated_penetration_loss_db,
        ), 2)
        sinr_db = round(rsrp_dbm - formula_v31.resolve_unit(network_mode)["noise_floor_dbm"], 2)
        outage = False

        if is_rsu:
            # RSU — PC5 사이드링크. HARQ·큐잉 없음, 건물 차폐 영향 최소(도로 레벨 직접 통신).
            # 명세 범위 밖이라 기존 선형 모델(1~3ms) 유지. RSU 처리+전달 지연
            # (edge_latency_ms)은 합산한다. 출처: 3GPP TR 36.885 §A.1; ETSI TR 102 638 §4.3
            l_air_ms = _L_rsu(obs.distance_m, node_coverage_radius)
            l_base_ms = 1.0
            l_signal_ms = round(l_air_ms - 1.0, 3)
            l_queue_ms = 0.0
            predicted_latency_ms = round(l_air_ms + edge_latency, 3)
        else:
            # BS — 셀룰러 Uu: v3.1 통합 모델(RAN only — 백홀/코어 없음).
            # n_vehicles: ego + 배경 차량 + 차량 외 기기 + ITS 환산 부하 (유지 결정)
            n_vehicles = (
                1
                + int(node.get("n_background_vehicles", 0))
                + int(node.get("n_other_devices", 0))
                + int(node.get("n_its_load", 0))
            )
            cap = float(node.get("capacity") or 100.0)
            deficit_ratio = float(node.get("deficit_rb", 0.0)) / max(cap, 1.0)
            r = formula_v31.compute_latency(
                network_mode, obs.distance_m, n_vehicles,
                obs.estimated_penetration_loss_db, deficit_ratio,
            )
            l_base_ms, l_signal_ms, l_queue_ms = (
                r["l_base_ms"], r["l_transmission_ms"], r["l_queue_ms"],
            )
            outage = r["outage"]
            # E2E = RAN + MEC/앱 처리(edge_latency_ms). outage면 L_OUTAGE 그대로.
            predicted_latency_ms = (
                formula_v31.L_OUTAGE_MS if outage
                else round(r["l_total_ms"] + edge_latency, 3)
            )

        node_score = round(predicted_latency_ms, 2)

        results.append({
            "node": node,
            "distance_m": obs.distance_m,
            "intersected_building_count": obs.intersected_building_count,
            "max_building_height_m": obs.max_building_height_m,
            "estimated_penetration_loss_db": obs.estimated_penetration_loss_db,
            "latency_penalty_ms": obs.latency_penalty_ms,
            "stability_score": obs.stability_score,
            "confidence": obs.confidence,
            "predicted_latency_ms": predicted_latency_ms,
            "l_base_ms": l_base_ms,
            "l_signal_ms": l_signal_ms,
            "l_queue_ms": l_queue_ms,
            "rsrp_dbm": rsrp_dbm,
            "sinr_db": sinr_db,
            "outage": outage,
            "node_score": node_score,
            "highlighted_buildings": obs.highlighted_buildings,
        })
    # 연결 규칙 (명세 §9, 기본 알고리즘 rsrp_max): RSRP 최대 기지국에 연결.
    # 부하 기반 선택 금지 — 동률일 때만 낮은 예측 지연으로 타이브레이크.
    results.sort(key=lambda item: (-item["rsrp_dbm"], item["predicted_latency_ms"]))
    return results
