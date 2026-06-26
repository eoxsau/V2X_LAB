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

# ── Technology parameters for the L_total latency model ───────────────────────
# alpha calibrated so RSRP transitions at realistic urban coverage distances:
#   4G: retransmissions begin ~400m, 5G ~500m, 6G ~700m
# L_base는 "무부하(unloaded) 최선의 경우" 바닥값이 아니라 실측 상용망 평균 체감 지연
# 기준으로 잡았다 — 4G LTE 실측 RTT는 약 30-50ms(편도 추정 ~25ms), 5G NSA 혼합망 실측은
# 흔히 5-20ms대(보수적으로 ~15ms), 6G는 미상용이라 ITU-R IMT-2030 목표치 수준(~1ms) 유지.
_TECH_PARAMS: dict[str, dict] = {
    # alpha calibrated so retransmissions begin at realistic distances:
    #   4G ~240m  (urban macro, 300-400m coverage radius)
    #   5G ~450m  (urban NR, 450m coverage radius)
    #   6G ~1000m (future, extended coverage)
    # coverage_radius_m = 위 코멘트의 실제 커버리지 반경 — 하드 컷오프로 사용(아래 참고).
    "4G": dict(L_base=25.0, P_tx=43.0, alpha=45.0, beta=3.5, RSRP_thresh=-85.0,  RSRP_range=25.0, N_max=6, T_retx=8.0,  C_tech=100,  coverage_radius_m=400.0),
    "5G": dict(L_base=15.0, P_tx=46.0, alpha=55.0, beta=3.0, RSRP_thresh=-90.0,  RSRP_range=25.0, N_max=4, T_retx=1.0,  C_tech=500,  coverage_radius_m=450.0),
    "6G": dict(L_base= 1.0, P_tx=48.0, alpha=68.0, beta=2.5, RSRP_thresh=-95.0,  RSRP_range=25.0, N_max=3, T_retx=0.1,  C_tech=2000, coverage_radius_m=1000.0),
}


def _material_a_seg_db(
    buildings_bl: "gpd.GeoDataFrame",
    buildings_bl_3857: "gpd.GeoDataFrame",
    line_3857: LineString,
) -> tuple[float, str, float]:
    """Compute A_seg (total building penetration loss dB) from material + geometry.

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
    """Compute L_total = L_base + L_signal + L_queue (ms).

    L_signal uses the Log-Distance RSRP model + HARQ retransmission mapping.
    L_queue uses the M/M/1 model with rho = n_vehicles / C_tech, PLUS deficit_ratio.

    deficit_ratio: connecting BS's RB allocation deficit_rb / capacity (0.0 when the
    BS isn't oversubscribed). The resource-allocation system caps utilization_ratio
    at 1.0 (a BS 5x oversubscribed looks the same as one merely at capacity once you
    only look at load/capacity), so without this term a real RB shortage found by the
    allocation algorithms would never show up as queuing delay here — the two systems
    would keep estimating congestion independently and disagreeing.

    deficit_ratio is added as a SEPARATE linear penalty on top of (not folded into) rho.
    Earlier version added it directly to rho before the 0.99 cap — any BS with a large
    enough deficit got clipped to the same rho≈0.99 ceiling, so distinct BSs with very
    different oversubscription levels all produced the same ~L_base×99 latency (observed
    as multiple candidates showing an identical, suspiciously round 1500.0ms — 2026-06-24
    user report). Linear addition instead keeps L_queue strictly increasing in deficit_ratio
    (no collision between different severities) and is exactly 0 when deficit_ratio=0, so
    behavior is unchanged whenever the allocation system reports no deficit.

    Returns (L_total, L_base, L_signal, L_queue) — the three components are
    returned alongside the total so callers (e.g. the dashboard breakdown)
    can display the model's actual terms instead of an ad-hoc approximation.
    """
    p = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])
    L_base = p["L_base"]

    # Log-Distance Path Loss RSRP model
    RSRP = p["P_tx"] - p["alpha"] - 10.0 * p["beta"] * log10(max(distance_m, 1.0)) - A_seg_db

    # HARQ retransmission latency
    N_retx = p["N_max"] * max(0.0, min(1.0, (p["RSRP_thresh"] - RSRP) / p["RSRP_range"]))
    L_signal = N_retx * p["T_retx"]

    # M/M/1 queuing latency (background load only) + separate RB-deficit penalty
    rho = min(n_vehicles / p["C_tech"], 0.99)
    L_queue = L_base * rho / (1.0 - rho) + L_base * max(0.0, deficit_ratio)

    total = round(L_base + L_signal + L_queue, 3)
    return total, round(L_base, 3), round(L_signal, 3), round(L_queue, 3)


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
    line = LineString([(vehicle_lng, vehicle_lat), (network_node["lng"], network_node["lat"])])
    line_gdf = gpd.GeoDataFrame({"geometry": [line]}, crs="EPSG:4326").to_crs(3857)
    line_3857 = line_gdf.geometry.iloc[0]
    distance_m = float(line_gdf.length.iloc[0])

    if buildings_gdf.empty:
        return BuildingObstructionResult(
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

    # 2D 경로와 교차하는 건물 후보
    search = buildings_gdf[buildings_gdf.geometry.intersects(line)].copy().reset_index(drop=True)
    search_3857 = search.to_crs(3857).copy().reset_index(drop=True) if not search.empty else search

    h_antenna = float(network_node.get("antenna_height_m") or _DEFAULT_ANTENNA_HEIGHT_M)

    # ── 3D LOS 필터 ────────────────────────────────────────────────────────────
    is_blocking: list[bool] = []
    for i in range(len(search_3857)):
        bldg_h = float(search["height_m"].iloc[i] or 0.0) if "height_m" in search.columns else 0.0
        if bldg_h <= 0:
            is_blocking.append(True)
        else:
            blocked = _is_blocked_3d(
                line_3857, search_3857.geometry.iloc[i], bldg_h,
                _VEHICLE_HEIGHT_M, h_antenna,
            )
            is_blocking.append(blocked)

    mask = [bool(v) for v in is_blocking]
    search_bl = search[mask].copy()
    search_bl_3857 = search_3857[mask].copy()

    count = int(len(search_bl))
    max_height = float(search_bl["height_m"].fillna(0).max()) if count and "height_m" in search_bl.columns else 0.0

    # ── Material-based wall penetration loss ──────────────────────────────────
    A_seg_db, confidence, crossed_length_m = _material_a_seg_db(search_bl, search_bl_3857, line_3857)

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

    return BuildingObstructionResult(
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
    coverage_radius_m = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])["coverage_radius_m"]
    results = []
    for node in candidate_nodes:
        obs = analyze_vehicle_to_node(
            vehicle_id=vehicle_id,
            vehicle_lat=vehicle_lat,
            vehicle_lng=vehicle_lng,
            network_node=node,
            buildings_gdf=buildings_gdf,
            vehicle_density_penalty=vehicle_density_penalty,
        )
        # 물리적 커버리지 반경 밖이면 latency가 아무리 낮게 계산돼도 후보에서 제외한다 —
        # 컷오프 없이는 혼잡도가 낮은 먼 기지국이 가까운 혼잡 기지국보다 "낮은 latency"로
        # 이겨서 지도 전체를 가로지르는 비현실적인 연결선이 그려지는 문제가 있었다.
        if obs.distance_m > coverage_radius_m:
            continue
        # n_vehicles: ego + 배경 차량(실시간 또는 Poisson 기본값) + 차량 외 기기(폰/IoT, Poisson)
        # + 실시간 ITS 교통량 환산 부하 — 같은 기지국 capacity를 공유하는 모든 활성 연결을
        # 큐잉 모델 분모에 반영한다.
        n_vehicles = (
            1
            + int(node.get("n_background_vehicles", 0))
            + int(node.get("n_other_devices", 0))
            + int(node.get("n_its_load", 0))
        )
        edge_latency = float(node.get("edge_latency_ms", 5.0))
        # RB 자원할당 시스템이 산출한 deficit_rb(용량 초과분)를 capacity 대비 비율로
        # 환산해 큐잉 지연에 직접 반영한다 — apply_allocation_to_network_nodes()가
        # 매 할당 주기마다 채워주는 필드(할당이 아직 안 됐으면 0.0, 즉 기존 동작과 동일).
        cap = float(node.get("capacity") or 100.0)
        deficit_ratio = float(node.get("deficit_rb", 0.0)) / max(cap, 1.0)

        predicted_latency_ms, l_base_ms, l_signal_ms, l_queue_ms = _L_total(
            distance_m=obs.distance_m,
            A_seg_db=obs.estimated_penetration_loss_db,
            n_vehicles=n_vehicles,
            network_mode=network_mode,
            deficit_ratio=deficit_ratio,
        )
        node_score = round(predicted_latency_ms + edge_latency * 0.5, 2)

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
            "node_score": node_score,
            "highlighted_buildings": obs.highlighted_buildings,
        })
    results.sort(key=lambda item: (item["predicted_latency_ms"], item["node_score"]))
    return results
