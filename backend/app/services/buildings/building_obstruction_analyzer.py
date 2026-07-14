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

# ── Technology parameters — latency model v3.1 ────────────────────────────────
# L_total = L_base + L_transmission + L_queue
#   L_base         = TTI × 0.5            (scheduling wait, uniform dist mean)
#   L_transmission = packet_bits / (SE × BW)  (MCS → throughput → time)
#   L_queue        = TTI × ρ/(1−ρ)        (M/M/1, service time = 1 TTI slot)
#
# Path loss: 2-Slope model (3GPP TR 38.901 UMa LOS, Table 7.4.1-1)
#   d ≤ d_BP : PL(d) = 10 × 2.2 × log10(d)
#   d > d_BP : PL(d) = 10 × 2.2 × log10(d_BP) + 10 × 4.0 × log10(d/d_BP)
#   d_BP = 4 × h_BS × h_UT × f_c / c   (c = 3×10⁸ m/s)
#
# SINR via α back-calculation — P_tx and noise_floor cancel (sec. 5 of doc):
#   SINR(d) = SINR_min + PL(d_edge) − PL(d) − A_seg
#
# 출처: 설계 문서 v3.1 (Autonomous V2X AI Routing Lab)
#   TTI/numerology: 3GPP TS 38.211; MCS SE: 3GPP TS 38.214 Table 5.1.3.1-1
#   2-Slope β: 3GPP TR 38.901 UMa LOS Table 7.4.1-1
#   BW 근거: 4G [TS 36.101 단일반송파 최대], 5G [국내 3사 3.5GHz 100MHz 실배치]
_TECH_PARAMS: dict[str, dict] = {
    "4G": dict(
        TTI=1.0,              # ms; numerology 0, SCS 15 kHz (3GPP TS 38.211)
        f_c=2.0e9,            # Hz; LTE 2.0 GHz 대표 반송파
        h_BS=24.0,            # m;  UMa 기지국 높이
        h_UT=0.5,             # m;  차량 UE 안테나 높이
        BW=20e6,              # Hz; LTE 단일반송파 최대 (3GPP TS 36.101)
        d_edge=2000.0,        # m;  설계 앵커 — 도심 매크로 LTE 커버리지 반경
        C_tech=100,           # vehicles; M/M/1 수용량 [설계 파라미터]
        coverage_radius_m=400.0,
        # ── 비무선 구간 지연 (3GPP TR 36.912, Aijaz et al. IEEE Commun. Surv. 2015) ──
        backhaul_ms=8.0,      # S1-U 인터페이스(eNB→SGW): 광섬유 도심 편도 평균
        core_ms=5.0,          # EPC 처리(SGW+PGW+MME): 각 노드 1-2ms × 복수 홉
    ),
    "5G": dict(
        TTI=0.5,              # ms; numerology 1, SCS 30 kHz (국내 3.5GHz 실배치)
        f_c=3.5e9,            # Hz; 5G NR 3.5 GHz
        h_BS=24.0,
        h_UT=0.5,
        BW=100e6,             # Hz; 국내 3사 3.5GHz 각 100MHz 실배치 근거
        d_edge=1000.0,        # m;  설계 앵커
        C_tech=500,
        coverage_radius_m=450.0,
        # ── 비무선 구간 지연 (3GPP TR 38.913 §8.1.1, Patel et al. IEEE Access 2022) ──
        backhaul_ms=3.0,      # NG3 인터페이스(gNB→UPF): CU/DU 분리 포함 편도
        core_ms=2.0,          # 5GC 처리(UPF+SMF+AMF): MEC 배치 기준
    ),
    "6G": dict(
        TTI=0.125,            # ms; [설계 가정] numerology 3, SCS 120 kHz 플레이스홀더
        f_c=7.0e9,            # Hz; [설계 가정] upper mid-band 연구 주파수
        h_BS=24.0,
        h_UT=0.5,
        BW=400e6,             # Hz; NR FR2 반송파 최대 (TS 38.101-2) 차용
        d_edge=500.0,         # m;  설계 앵커
        C_tech=2000,
        coverage_radius_m=1000.0,
        # ── 비무선 구간 지연 (IMT-2030 요구사항 기반 설계 가정) ──
        backhaul_ms=0.5,      # 분산 RAN + 초저지연 광섬유 인터페이스
        core_ms=0.5,          # 완전 분산 코어(제로트러스트 UPF 로컬 배치)
    ),
}

# ── MCS lookup table (3GPP TS 38.214 Table 5.1.3.1-1) ─────────────────────────
# (최소 SINR dB [AWGN BLER 10% 근사], 스펙트럼 효율 bit/s/Hz)
# SINR 문턱은 근사값 — 표준값은 스펙트럼 효율만; 출처: Sadhana 48:77 AWGN LUT
_MCS_TABLE: list[tuple[float, float]] = [
    (-6.0, 0.2344),   # MCS  0, QPSK
    (-2.0, 0.6016),   # MCS  4, QPSK
    ( 3.0, 1.3262),   # MCS  9, QPSK
    ( 6.0, 1.6953),   # MCS 12, 16QAM
    ( 9.0, 2.5703),   # MCS 16, 16QAM
    (13.0, 3.3223),   # MCS 20, 64QAM
    (17.0, 4.5234),   # MCS 24, 64QAM
    (21.0, 5.9004),   # MCS 28, 64QAM
]
_SINR_MIN_DB: float = -6.0    # MCS 0 문턱 — 이하면 outage
_L_OUTAGE_MS: float = 1000.0  # outage 유한 페널티 [설계 가정: 최적화 수렴 유지]
# CAM 메시지 크기: ETSI EN 302 637-2 V1.4.1 §B.2 (일반 CAM 페이로드 300-800 바이트,
# 대표값 800 바이트 = 6400 bits 사용)
_PACKET_BITS: int   = 6_400


# ── RSU (Road Side Unit) coverage radii by network mode ───────────────────────
# RSU는 교차로 폴에 4~8m 높이로 설치되는 PC5/사이드링크 전용 노드. 셀룰러 Uu 기지국보다
# 커버리지 반경이 훨씬 작지만 도로 레벨 직접 통신이라 지연이 극히 낮다.
# 출처: ETSI EN 302 663(ITS-G5), 3GPP TR 36.885(LTE-V2X), Rel-16 NR-V2X PC5 링크 예산
_RSU_COVERAGE_RADIUS_M: dict[str, float] = {
    "4G": 100.0,   # DSRC/LTE-V2X Mode 4 urban RSU, 실측 범위 50-150m
    "5G": 150.0,   # NR-V2X PC5 urban RSU, 3GPP Rel-16 링크 예산 기준 ~150m
    "6G": 250.0,   # IMT-2030 ultra-reliable V2X 확장 커버리지 목표
}


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
    """Compute L_total = L_base + L_transmission + L_queue (ms).  [설계 문서 v3.1]

    ① L_base = TTI × 0.5
       패킷이 다음 슬롯 경계까지 기다리는 평균 시간 (균등분포 평균).
       출처: Coll-Perales et al., IEEE TVT 2023; 3GPP TS 38.211 (TTI/numerology)

    ② L_transmission = _PACKET_BITS / (SE × BW)
       SINR → MCS → 스펙트럼 효율 → 처리량 → 전송 시간.
       • 2-Slope 경로손실 (3GPP TR 38.901 UMa LOS Table 7.4.1-1):
           d ≤ d_BP : PL = 10·2.2·log10(d)
           d > d_BP : PL = 10·2.2·log10(d_BP) + 10·4.0·log10(d/d_BP)
           d_BP = 4·h_BS·h_UT·f_c / c
       • SINR α-역산 소거 (설계 문서 §5):
           SINR(d) = SINR_min + PL(d_edge) − PL(d) − A_seg_db
           (P_tx = 46 dBm, noise_floor = −95 dBm이 완전 소거됨)
       • SINR < −6 dB → outage → L_OUTAGE = 1000 ms 반환
       • MCS 표: 3GPP TS 38.214 Table 5.1.3.1-1 (SE); SINR 문턱은 AWGN 근사

    ③ L_queue = TTI × ρ / (1 − ρ)
       M/M/1 대기행렬, 서비스 시간 = 1 TTI 슬롯 [설계 가정].
       ρ = n_vehicles / C_tech (최대 0.99 클램프).
       deficit_ratio > 0이면 RB 할당 부족 선형 페널티 TTI × deficit_ratio 추가.
       출처: Coll-Perales et al., IEEE TVT 2023

    ④ L_transport = backhaul_ms + core_ms  (기술별 비무선 구간)
       4G: backhaul 8ms(S1-U) + core 5ms(SGW+PGW+MME) = 13ms
           출처: Aijaz et al., IEEE Commun. Surv. Tutorials 2015; 3GPP TR 36.912
       5G: backhaul 3ms(NG3) + core 2ms(UPF+SMF) = 5ms
           출처: 3GPP TR 38.913 §8.1.1; Patel et al., IEEE Access 2022
       6G: backhaul 0.5ms + core 0.5ms = 1ms  [IMT-2030 목표 기반 설계 가정]

    Returns (L_total, L_base, L_transmission, L_queue).
    L_total은 L_transport를 포함한 셀룰러 Uu 구간 전체 지연.
    호출부는 l_signal_ms 변수명으로 L_transmission을 받아 저장함 (API 필드명 유지).
    """
    p = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])
    C_LIGHT = 3e8  # m/s

    # ── ① L_base: 스케줄링 대기 ─────────────────────────────────────────────
    TTI = p["TTI"]
    L_base = TTI * 0.5

    # ── ② 2-Slope 경로손실 ──────────────────────────────────────────────────
    d = max(distance_m, 1.0)
    d_BP = 4.0 * p["h_BS"] * p["h_UT"] * p["f_c"] / C_LIGHT

    def _pl(dist: float) -> float:
        if dist <= d_BP:
            return 10.0 * 2.2 * log10(max(dist, 1.0))
        return 10.0 * 2.2 * log10(d_BP) + 10.0 * 4.0 * log10(dist / d_BP)

    # SINR — α·P_tx·noise_floor 소거 후 단순화 (설계 문서 §5)
    SINR = _SINR_MIN_DB + _pl(p["d_edge"]) - _pl(d) - A_seg_db

    # Outage: MCS 0 문턱 미만
    if SINR < _SINR_MIN_DB:
        return _L_OUTAGE_MS, 0.0, _L_OUTAGE_MS, 0.0

    # MCS 선택: SINR을 만족하는 최고 MCS 스펙트럼 효율
    se = _MCS_TABLE[0][1]
    for sinr_thr, se_val in _MCS_TABLE:
        if SINR >= sinr_thr:
            se = se_val

    throughput_bps = se * p["BW"]
    L_transmission = round((_PACKET_BITS / throughput_bps) * 1000.0, 3)  # → ms

    # ── ③ L_queue: M/M/1 (서비스 시간 = TTI) ───────────────────────────────
    rho = min(n_vehicles / p["C_tech"], 0.99)
    L_queue = round(TTI * rho / (1.0 - rho), 3)
    if deficit_ratio > 0.0:
        L_queue = round(L_queue + TTI * max(0.0, deficit_ratio), 3)

    # ── ④ L_transport: 백홀 + 코어 네트워크 지연 (기술별 비무선 구간) ─────────
    L_transport = round(p.get("backhaul_ms", 3.0) + p.get("core_ms", 2.0), 3)

    total = round(L_base + L_transmission + L_queue + L_transport, 3)
    return total, round(L_base, 3), round(L_transmission, 3), round(L_queue, 3)


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
    bs_global_radius = _TECH_PARAMS.get(network_mode, _TECH_PARAMS["5G"])["coverage_radius_m"]
    rsu_global_radius = _RSU_COVERAGE_RADIUS_M.get(network_mode, 150.0)
    results = []
    for node in candidate_nodes:
        is_rsu = str(node.get("type") or "").lower() in ("rsu", "rsu_node", "roadside_unit")
        # 노드 자신의 coverage_radius_m을 우선 사용하고, 없으면 기술 모드별 전역값으로 폴백.
        # RSU는 150m, BS는 400~500m — 노드별 반경을 직접 쓰는 것이 더 정확하다.
        node_coverage_radius = float(node.get("coverage_radius_m") or (
            rsu_global_radius if is_rsu else bs_global_radius
        ))

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

        if is_rsu:
            # RSU — PC5 사이드링크. HARQ·큐잉 없음, 건물 차폐 영향 최소(도로 레벨 직접 통신).
            # L_rsu: 거리 기반 선형 모델(1~3ms). PC5 브로드캐스트에도 RSU 처리+전달 지연
            # (edge_latency_ms)은 존재하므로 합산한다.
            # 출처: 3GPP TR 36.885 §A.1; ETSI TR 102 638 §4.3
            l_air_ms = _L_rsu(obs.distance_m, node_coverage_radius)
            l_base_ms = 1.0
            l_signal_ms = round(l_air_ms - 1.0, 3)
            l_queue_ms = 0.0
        else:
            # BS — 셀룰러 Uu 인터페이스: 공중 구간(스케줄링+전송+큐잉)만 계산.
            # edge_latency_ms(백홀+5GC/EPC 처리)는 아래서 합산.
            # 출처: 3GPP TS 38.211(TTI/MCS), 3GPP TR 38.901(경로손실 2-Slope), 3GPP TS 38.214
            n_vehicles = (
                1
                + int(node.get("n_background_vehicles", 0))
                + int(node.get("n_other_devices", 0))
                + int(node.get("n_its_load", 0))
            )
            cap = float(node.get("capacity") or 100.0)
            deficit_ratio = float(node.get("deficit_rb", 0.0)) / max(cap, 1.0)
            l_air_ms, l_base_ms, l_signal_ms, l_queue_ms = _L_total(
                distance_m=obs.distance_m,
                A_seg_db=obs.estimated_penetration_loss_db,
                n_vehicles=n_vehicles,
                network_mode=network_mode,
                deficit_ratio=deficit_ratio,
            )

        # E2E 지연 = 공중 구간 + 백홀/코어/MEC 지연 (edge_latency_ms).
        # 5G BS: 공중 2-4ms + 백홀+5GC 8-12ms → E2E 10-20ms (3GPP TR 38.913 §8.1.1)
        # PC5 RSU: 공중 1-3ms + RSU 처리 ~1ms → E2E 2-4ms (ETSI GS MEC 003)
        predicted_latency_ms = round(l_air_ms + edge_latency, 3)
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
            "node_score": node_score,
            "highlighted_buildings": obs.highlighted_buildings,
        })
    results.sort(key=lambda item: (item["predicted_latency_ms"], item["node_score"]))
    return results
