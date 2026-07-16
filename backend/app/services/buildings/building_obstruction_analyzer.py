from __future__ import annotations

from math import exp, log10

import geopandas as gpd
from shapely.geometry import LineString

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

# A_seg = 3D LOS를 가로막는 건물 수 × 12 dB (명세 §7, 선형 누적·상한 없음)
_A_SEG_PER_BUILDING_DB = 12.0

# ── A_seg 사전계산 캐시 (명세 §7 — 매 스텝 재계산 방지) ─────────────────────────
# 키: (차량 위치 1e-5도≈1m 격자, 노드 id/위치/안테나, 밀도 페널티, 건물셋 버전).
# 경로 엣지 중점처럼 같은 지점을 반복 평가하는 호출(K-path 2초 주기 재점수)에 적중.
_OBSTRUCTION_CACHE: dict[tuple, "BuildingObstructionResult"] = {}
_OBSTRUCTION_CACHE_MAX = 20_000


def reset_obstruction_cache() -> None:
    _OBSTRUCTION_CACHE.clear()


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
    line_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:4326").to_crs(3857)
    line_3857 = line_gdf.geometry.iloc[0]
    distance_m = float(line_gdf.length.iloc[0])

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

    # 2D 경로와 교차하는 건물 후보
    search = buildings_gdf[buildings_gdf.geometry.intersects(line)].copy().reset_index(drop=True)
    search_3857 = search.to_crs(3857).copy().reset_index(drop=True) if not search.empty else search

    # ── 3D LOS 필터 (명세 §7 — 2D 판정 금지) ──────────────────────────────────
    is_blocking: list[bool] = []
    unknown_height_blockers = 0
    for i in range(len(search_3857)):
        bldg_h = float(search["height_m"].iloc[i] or 0.0) if "height_m" in search.columns else 0.0
        if bldg_h <= 0:
            # 높이 미상 — 보수적으로 차단 처리하되 신뢰도를 낮춘다
            is_blocking.append(True)
            unknown_height_blockers += 1
        else:
            blocked = _is_blocked_3d(
                line_3857, search_3857.geometry.iloc[i], bldg_h,
                _VEHICLE_HEIGHT_M, h_antenna,
            )
            is_blocking.append(blocked)

    mask = [bool(v) for v in is_blocking]
    search_bl = search[mask].copy()

    count = int(len(search_bl))
    max_height = float(search_bl["height_m"].fillna(0).max()) if count and "height_m" in search_bl.columns else 0.0

    # ── A_seg = 차단 건물 수 × 12 dB (명세 §7 — 재질 기반 모델 대체, 상한 없음) ──
    A_seg_db = round(count * _A_SEG_PER_BUILDING_DB, 2)
    confidence = "high" if unknown_height_blockers == 0 else "medium"

    # latency_penalty_ms: building-only contribution shown on dashboard (ms)
    latency_penalty_ms = round(A_seg_db * 0.45, 2)
    stability_score = round(max(0.0, exp(-(A_seg_db / 20.0)) - vehicle_density_penalty / 100.0), 3)

    highlighted_buildings = []
    top = search_bl.head(5)
    for pos in range(len(top)):
        row = top.iloc[pos]
        geom = top.geometry.iloc[pos]
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
        sinr_db = round(rsrp_dbm - formula_v31.NOISE_FLOOR_DBM, 2)
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
