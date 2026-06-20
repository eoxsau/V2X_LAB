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
_TECH_PARAMS: dict[str, dict] = {
    # alpha calibrated so retransmissions begin at realistic distances:
    #   4G ~240m  (urban macro, 300-400m coverage radius)
    #   5G ~450m  (urban NR, 450m coverage radius)
    #   6G ~1000m (future, extended coverage)
    "4G": dict(L_base=10.0, P_tx=43.0, alpha=45.0, beta=3.5, RSRP_thresh=-85.0,  RSRP_range=25.0, N_max=6, T_retx=8.0,  C_tech=100),
    "5G": dict(L_base= 1.0, P_tx=46.0, alpha=55.0, beta=3.0, RSRP_thresh=-90.0,  RSRP_range=25.0, N_max=4, T_retx=1.0,  C_tech=500),
    "6G": dict(L_base= 0.1, P_tx=48.0, alpha=68.0, beta=2.5, RSRP_thresh=-95.0,  RSRP_range=25.0, N_max=3, T_retx=0.1,  C_tech=2000),
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
) -> float:
    """Compute L_total = L_base + L_signal + L_queue (ms).

    L_signal uses the Log-Distance RSRP model + HARQ retransmission mapping.
    L_queue uses the M/M/1 model with rho = n_vehicles / C_tech.
    """
    p = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])
    L_base = p["L_base"]

    # Log-Distance Path Loss RSRP model
    RSRP = p["P_tx"] - p["alpha"] - 10.0 * p["beta"] * log10(max(distance_m, 1.0)) - A_seg_db

    # HARQ retransmission latency
    N_retx = p["N_max"] * max(0.0, min(1.0, (p["RSRP_thresh"] - RSRP) / p["RSRP_range"]))
    L_signal = N_retx * p["T_retx"]

    # M/M/1 queuing latency
    rho = min(n_vehicles / p["C_tech"], 0.99)
    L_queue = L_base * rho / (1.0 - rho)

    return round(L_base + L_signal + L_queue, 3)


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
    latency_penalty_ms = round(A_seg_db * 0.45 + vehicle_density_penalty, 2)
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
        # n_vehicles: ego vehicle + Poisson-sampled background at this BS
        n_vehicles = 1 + int(node.get("n_background_vehicles", 0))
        edge_latency = float(node.get("edge_latency_ms", 5.0))

        predicted_latency_ms = _L_total(
            distance_m=obs.distance_m,
            A_seg_db=obs.estimated_penetration_loss_db,
            n_vehicles=n_vehicles,
            network_mode=network_mode,
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
            "node_score": node_score,
            "highlighted_buildings": obs.highlighted_buildings,
        })
    results.sort(key=lambda item: (item["predicted_latency_ms"], item["node_score"]))
    return results
