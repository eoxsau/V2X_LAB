"""
통합 latency 수식 — 설계 문서 latency_formula_v3_1.md 구현.

단일 기지국–차량 다운링크 지연. 무선 구간(RAN)만 계산하며 백홀 지연은 포함하지
않는다 (명세 §1). 모든 지연 단위는 ms.

    L_total = L_base + L_transmission + L_queue
    L_base         = TTI × 0.5                       (§1)
    L_transmission = PACKET_SIZE_BITS / (SE × BW)    (§6, SINR→MCS→SE)
    L_queue        = TTI × ρ / (1 − ρ)               (§8, M/M/1)
    SINR < −6 dB → outage → L_OUTAGE = 1000 ms       (§1, 합산보다 먼저)

명세 대비 유지하기로 결정한 확장(2026-07-16 사용자 결정):
  - n_vehicles에 차량 외 부하(폰/IoT, ITS 환산)를 합산해서 넘길 수 있음 — 호출부 책임
  - deficit_ratio > 0이면 L_queue에 TTI × deficit_ratio 선형 가산 (RB 할당 연동)

이 모듈이 파라미터의 단일 출처다 (§10 체크리스트). d_BP와 α는 하드코딩하지 않고
초기화 시 계산하며, α는 명세 §4의 기대값과 ±0.2 이내인지 검증한다.
"""
from __future__ import annotations

from math import log10

# ── 기술별 파라미터 (명세 §2) ───────────────────────────────────────────────────
H_BS = 24.0        # m — UMa 기지국 안테나 높이
H_UT = 0.5         # m — 차량 UE 안테나 높이
C_LIGHT = 3e8      # m/s
P_TX_DBM = 46.0            # 모든 기지국 동일 값 (명세 필수 전제)
                           # 3GPP UMa 평가 설정의 통상적 매크로 BS 송신전력 가정값
                           # (hBS=25m·ISD=500m과 함께 쓰이는 값 — 2026-08-05 웹 검색으로 확인,
                           # 정확한 TR/표 번호까지 원문 대조는 못 함 — ETSI 원문 PDF 접근 차단됨)
# 잡음 바닥 — 대역폭에 비례해 커지는 열잡음 공식으로 기술별 계산한다 (2026-08-06 수정).
# N0 = -174 dBm/Hz(상온 열잡음 밀도, 기본 물리 상수 kT — 임의 튜닝 아님) + 10log10(BW) + NF
# 예전엔 4G/5G/6G(20/100/400MHz) 전부에 -95dBm 고정값 하나를 썼다 — 6G는 실제보다 잡음이
# 약 16dB 과소평가돼 있었다. TECH_CONFIG/RSU_CONFIG 초기화 시 "noise_floor_dbm"으로
# 기술별 계산해 넣는다 — 아래 참조.
UE_NOISE_FIGURE_DB = 9.0   # 차량 OBU(다운링크 수신) 잡음지수 가정값. 3GPP 링크버짓
                           # 평가에서 UE NF에 흔히 쓰이는 값(약 9dB) — 원문 표/문단 번호까지는
                           # 대조 못 함(ETSI 원문 PDF 접근 차단). 대신 3GPP TS 36.521
                           # Table 7.3.3-1의 실측 규격값(LTE Band 20, 20MHz 대역 REFSENS
                           # = -90dBm)과 교차검증했다: NF=9dB로 20MHz 잡음바닥을 구하면
                           # -174+10log10(20e6)+9 ≈ -92dBm — REFSENS(-90dBm, 기준 SINR
                           # 여유 포함)와 2dB 이내로 맞아떨어져 같은 자릿수의 값임을 확인.
SINR_MIN_DB = -6.0         # MCS 0 문턱 — 미만이면 outage
L_OUTAGE_MS = 1000.0
PACKET_SIZE_BITS = 100_000     # 100 Kbit — V2X 소형 데이터 단위(압축 센서·CAM 집합체 ~12.5KB)
                               # 변경 이유: 1Mbit 시 4G 커버리지 경계에서 L_trans=213ms(실제 V2X
                               # 100ms 예산 초과), 5G 80% 지점에서 43ms 발생 — 비현실적.
                               # 100Kbit → 4G edge 21ms, 5G 80% 4.5ms: V2X 표준 범위 내 유지.
                               # 명세 §6-2 HD Map 시나리오와 별개로, RAN 지연 평가 목적에 적합.

# ⚠️ C_tech(동시 수용 차량 수)는 출처 미상 — RB 예산이나 트래픽 모델에서 역산한 값이
# 아니라 그냥 정한 추정값이다(2026-08-05 감사 발견). 재검토 필요 시 대역폭(BW)/PRB 수
# 대비 V2X 연결당 소요 자원으로 역산하는 걸 권장 — 지금은 4G:5G:6G가 대역폭 비율
# (20:100:400=1:5:20)과 다른 1:5:20(100:500:2000, 마침 대역폭과 같은 비율)로 맞춰져
# 있어 "대역폭에 비례" 가정 자체는 일관돼 있지만, 비례상수 자체의 근거는 없다.
TECH_CONFIG: dict[str, dict] = {
    "4G": dict(f_c=2.0e9, numerology=0, TTI=1.0,   BW=20e6,  d_edge=2000.0, C_tech=100),
    "5G": dict(f_c=3.5e9, numerology=1, TTI=0.5,   BW=100e6, d_edge=1000.0, C_tech=500),
    # 6G: estimated design value — f_c/numerology/TTI는 표준 근거 없는 추정 설계값 (명세 §2)
    "6G": dict(f_c=7.0e9, numerology=3, TTI=0.125, BW=400e6, d_edge=500.0,  C_tech=2000),
}

# ── RSU(노변장치) — 이종 유닛 파라미터 (배치설계 v2 §3) ────────────────────────
#
# 설계 원칙(§3-3): RSU의 작은 커버리지를 **안테나 높이 하나**로 표현한다.
#   h_RSU를 낮추면 → d_BP가 줄고 → 4.0 기울기가 일찍 시작해 → 빨리 감쇠한다.
#   d_edge를 따로 상수로 박으면 같은 사실을 두 채널로 말하는 role-overlap이 된다.
#
# 설계 원칙(§3-2): 타입 간 결합 파라미터는 **딱 하나**만 둔다 → ΔP(송신전력 격차).
#   α는 타입별로 각자 역산하므로 링크 단위 SINR은 P_tx 절대값과 무관하지만,
#   BS↔RSU를 비교하려면 두 타입의 상대 세기를 정하는 값이 하나 필요하다.
#
# ⚠️ 2026-07-27 변경 — 이전 상수 {4G:100, 5G:150, 6G:250}는 폐기했다:
#   * 코드 주석에 "명세 범위 밖, 기존 값 유지"라고 적혀 있었듯 물리 유도값이 아니었다.
#   * 세대가 올라갈수록 **커지는** 규약이라, BS(2000>1000>500)와 정반대였다.
#     주파수가 높을수록 감쇠가 크다는 같은 물리를 BS만 따르고 RSU는 안 따르는 모순.
#   * h_RSU=5m + ΔP=20dB로 유도하면 4G 312 / 5G 156 / 6G 62 m — BS와 규약이 일치하고,
#     5G 156m는 폐기한 상수 150m와 사실상 같아 기존 5G 결과의 연속성도 유지된다.
_RSU_TYPES = ("rsu", "rsu_node", "roadside_unit")
H_RSU = 5.0                # m — 노변 폴 높이 (배치설계 v2 §10-A 확정)
DELTA_P_RSU_DB = 20.0      # dB — BS 대비 RSU 송신전력 격차 = 유일한 타입 간 결합 파라미터.
                           # RSU P_tx = 46 − 20 = 26 dBm. 실측 대조(2026-08-05): 대규모
                           # 도심 ITS-G5 DSRC 테스트베드 실측값이 노변장치 송신전력
                           # 25/29dBm(안테나 이득 7dBi 별도)을 보고 — 저전력 유닛(25dBm)과
                           # 거의 정확히 일치한다. PC5/ITS-G5 일반 EIRP 23~33dBm 범위 주장도
                           # 방향은 맞지만 정확한 표준 문서 대조는 못 함.

# ── MCS 테이블 (명세 §6-1, 세 기술 공통) ────────────────────────────────────────
# (최소 SINR dB, 스펙트럼 효율 bit/s/Hz)
# 스펙트럼 효율 값은 3GPP MCS/CQI 표의 실제 값과 일치한다(2026-08-05 검증) — 예:
# MCS 0(QPSK, code rate 120/1024): 2 × 120/1024 = 0.2344, 표의 값과 정확히 일치.
# ETSI 원문 PDF는 접근이 막혀 정확한 TS 38.214 표 번호까지 원문 대조는 못 했지만,
# 값 자체가 정확한 계산과 일치해 임의로 만든 숫자가 아님은 확인했다.
MCS_TABLE: list[tuple[float, float]] = [
    (-6.0, 0.2344),   # MCS  0, QPSK
    (-2.0, 0.6016),   # MCS  4, QPSK
    ( 3.0, 1.3262),   # MCS  9, QPSK
    ( 6.0, 1.6953),   # MCS 12, 16QAM
    ( 9.0, 2.5703),   # MCS 16, 16QAM
    (13.0, 3.3223),   # MCS 20, 64QAM
    (17.0, 4.5234),   # MCS 24, 64QAM
    (21.0, 5.9004),   # MCS 28, 64QAM
]


# ── 경로손실 PL(d) — 2-Slope Breakpoint 모델 (명세 §3) ─────────────────────────
def _pl(d: float, d_bp: float) -> float:
    d = max(d, 1.0)                    # log10(0) 방지 클램프 (명세 §1)
    if d <= d_bp:
        return 10.0 * 2.2 * log10(d)
    return 10.0 * 2.2 * log10(d_bp) + 10.0 * 4.0 * log10(d / d_bp)


# ── 초기화: d_BP·α 계산 + 검증 (명세 §3-1, §4 — 하드코딩 금지) ───────────────────
#
# BS: d_edge가 앵커 → α를 역산.
# RSU: h_RSU가 물리적 원인 → d_BP가 정해지고, ΔP만큼 낮은 전력으로 BS와 **같은 셀엣지
#      판정기준(PL 예산)** 에 도달하는 거리를 풀어 d_edge를 **유도**한다. 그 d_edge로 α 역산.
#      → 높이 하나가 커버리지·차폐·엣지를 일관되게 지배한다(§3-3).
for _cfg in TECH_CONFIG.values():
    _cfg["d_BP"] = 4.0 * H_BS * H_UT * _cfg["f_c"] / C_LIGHT
    _cfg["noise_floor_dbm"] = round(-174.0 + 10.0 * log10(_cfg["BW"]) + UE_NOISE_FIGURE_DB, 3)
    _cfg["alpha"] = P_TX_DBM - _cfg["noise_floor_dbm"] - SINR_MIN_DB - _pl(_cfg["d_edge"], _cfg["d_BP"])


def _solve_d_edge(pl_budget_db: float, d_bp: float) -> float:
    """PL(d) = pl_budget 을 만족하는 d. PL이 d에 단조증가라 이분 탐색이 안전하다."""
    lo, hi = 1.0, 1.0e5
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _pl(mid, d_bp) < pl_budget_db:
            lo = mid
        else:
            hi = mid
    return lo


RSU_CONFIG: dict[str, dict] = {}
for _tech, _cfg in TECH_CONFIG.items():
    _dbp = 4.0 * H_RSU * H_UT * _cfg["f_c"] / C_LIGHT
    # BS가 자기 셀엣지에서 쓰는 PL 예산에서 ΔP를 뺀 만큼이 RSU의 예산
    _budget = _pl(_cfg["d_edge"], _cfg["d_BP"]) - DELTA_P_RSU_DB
    _d_edge = _solve_d_edge(_budget, _dbp)
    RSU_CONFIG[_tech] = dict(
        f_c=_cfg["f_c"], numerology=_cfg["numerology"], TTI=_cfg["TTI"], BW=_cfg["BW"],
        d_BP=_dbp, d_edge=_d_edge,
        # 용량 비율 — 결합 파라미터를 늘리지 않도록 ΔP와 같은 근거(작은 유닛)에 묶어
        # BS의 1/10으로 고정한다. 절대값보다 비율이 방어 가능하다(§10-A).
        C_tech=max(_cfg["C_tech"] // 10, 1),
        # 잡음 바닥은 채널 대역폭(=수신기 쪽 속성)의 함수라 BS와 같은 값을 쓴다 —
        # RSU는 송신전력·커버리지만 다를 뿐 같은 기술의 같은 대역폭을 쓰는 차량 수신기다.
        noise_floor_dbm=_cfg["noise_floor_dbm"],
        # α는 자기 d_edge·자기 d_BP·자기 P_tx로 역산 — 전제 ②(하드코딩 금지) 준수
        alpha=(P_TX_DBM - DELTA_P_RSU_DB) - _cfg["noise_floor_dbm"] - SINR_MIN_DB - _pl(_d_edge, _dbp),
    )

# 2026-08-06 갱신: 잡음바닥을 기술별 계산으로 바꾸면서(위 UE_NOISE_FIGURE_DB) 값이
# 바뀌었다 — 이전 60.1/76.5/87.6은 잡음바닥 -95dBm 고정값 기준이었다. 이 기대값 자체가
# "정답"이 아니라 "PL/d_BP 구현이 의도대로 도는지"를 잡는 회귀 방지용이므로, 물리
# 파라미터를 의도적으로 바꿨으면 이 값도 같이 새로 계산해 갱신하는 게 맞다.
_EXPECTED_ALPHA = {"4G": 57.04, "5G": 66.47, "6G": 71.60}
for _tech, _exp in _EXPECTED_ALPHA.items():
    _got = TECH_CONFIG[_tech]["alpha"]
    if abs(_got - _exp) > 0.2:
        raise RuntimeError(
            f"formula_v31: alpha 검증 실패 — {_tech} 기대 {_exp}, 계산 {_got:.2f}. "
            f"PL 구현(특히 d_BP 분기)을 확인하세요 (명세 §4)."
        )

# RSU도 같은 방식으로 검증한다 — 유도 경로(h_RSU → d_BP → d_edge → α)가 바뀌면 즉시 걸린다.
_EXPECTED_RSU_D_EDGE = {"4G": 312.0, "5G": 156.0, "6G": 62.0}
for _tech, _exp in _EXPECTED_RSU_D_EDGE.items():
    _got = RSU_CONFIG[_tech]["d_edge"]
    if abs(_got - _exp) > 2.0:
        raise RuntimeError(
            f"formula_v31: RSU d_edge 검증 실패 — {_tech} 기대 {_exp}m, 계산 {_got:.1f}m. "
            f"H_RSU/DELTA_P_RSU_DB를 의도적으로 바꿨다면 이 기대값도 함께 갱신하세요."
        )

# ── 쉐도잉 마진 — coverage_radius(=d_edge)에 통계적 안전여유 반영 (2026-08-04) ──
# α/d_edge는 "차폐가 전혀 없을 때 SINR이 정확히 문턱에 닿는" 이론적 경계다(§3-1) —
# 여유(마진)가 0이다. 실제 셀 설계는 이 경계를 그대로 "서비스 반경"으로 쓰지 않고,
# 로그노멀 쉐도잉 표준편차만큼 PL 예산을 미리 깎아 실제 커버리지 반경을 구한다.
#
# 이 마진은 A_seg(analyze_vehicle_to_node가 실측 건물 폴리곤으로 계산하는 결정론적
# 차폐값)와 이중계산되지 않는다 — 아래에서 유도하는 d_reliable은 resolve_coverage_radius()
# 를 통해 라우팅 후보 필터링/coverage_risk 비용/UI 원 표시에만 쓰이고, 실제 outage 판정
# (compute_latency의 SINR 계산)은 이 값을 전혀 참조하지 않는다 — 거리와 A_seg만으로
# 독립적으로 계산된다. 즉 "이미 아는 건물"은 A_seg가, "모르는 잔여 변동"은 이 마진이
# 각자 다른 자리에서 담당한다.
#
# σ_SF 값은 시나리오를 골라 맞춘 게 아니라, 이 파일이 이미 쓰고 있는 안테나 높이·f_c를
# 3GPP TR 38.901 Table 7.4.1-1(σ_SF)에 그대로 대입한 것이다. ETSI가 공개한 원문 PDF는
# 접근이 막혀 있어, 그 표를 그대로 구현한 ns-3(오픈소스 3GPP 채널모델 레퍼런스 구현,
# src/propagation/model/three-gpp-propagation-loss-model.cc)의 실제 소스 코드를
# 1차 자료 대용으로 직접 대조해 확인했다 (2026-08-05):
#
#   ThreeGppUmaPropagationLossModel::GetShadowingStd
#     f_c <  6GHz → 7.00 dB (LOS/NLOS 구분 없음)
#     f_c >= 6GHz → LOS 4.00 dB, NLOS 6.00 dB
#   ThreeGppUmiStreetCanyonPropagationLossModel::GetShadowingStd
#     f_c <  6GHz → 7.00 dB (LOS/NLOS 구분 없음)
#     f_c >= 6GHz → LOS 4.00 dB, NLOS 7.82 dB
#
# → 4G(2.0GHz)·5G(3.5GHz)는 6GHz 미만이라 시나리오 무관 7.00dB, 6G(7.0GHz)만
#   6GHz 이상 분기에 해당돼 안테나 높이로 시나리오를 나눈 값(BS=UMa 6.00 / RSU=UMi-SC
#   7.82)을 쓴다. 이전 버전은 6G용 상수를 4G/5G에도 그대로 썼던 오류가 있었다 —
#   6G만 우연히 일치했다.
#
# 안테나 높이-시나리오 매칭 근거: H_BS=24m는 위 상수 정의에 "UMa 기지국 안테나 높이"로
# 이미 명시돼 있다(3GPP UMa 기준 hBS=25m와 근접, ns-3 문서도 UMa hBS=25m로 확인).
# H_RSU=5m는 UMa(25m)보다 UMi-Street Canyon(hBS 고정 10m)에 훨씬 가깝다.
#
# 마진을 1σ로 잡은 것도 임의 선택이 아니라 명시적 목표치다: 쉐도잉이 평균 0·표준편차
# σ_SF인 로그노멀 분포를 따른다고 가정하면(표준 셀 설계 가정), PL 예산에서 k·σ_SF를
# 미리 까둔 반경에서 "실제로 SINR≥문턱일 확률"은 표준정규 CDF Φ(k)다:
#   k=0(기존, 마진 없음) → Φ(0)=50.0%,  k=1(현재) → Φ(1)=84.1%,  k=2 → Φ(2)=97.7%
# 즉 지금 값은 "셀 경계 신뢰도 84.1%"라는 명시적 목표를 산다 — 2026-08-04 사용자 결정.
# 목표 신뢰도를 바꾸려면 아래 각 σ_SF 값에 곱하는 k(현재 1.0)를 바꾸면 된다.
_FC_HIGH_BAND_HZ = 6.0e9  # ns-3/38.901 분기 기준

def _shadow_fade_margin_db(f_c_hz: float, kind: str) -> float:
    if f_c_hz < _FC_HIGH_BAND_HZ:
        return 7.0
    return {"bs": 6.0, "rsu": 7.82}[kind]

for _cfg in TECH_CONFIG.values():
    _margin = _shadow_fade_margin_db(_cfg["f_c"], "bs")
    _budget_reliable = _pl(_cfg["d_edge"], _cfg["d_BP"]) - _margin
    _cfg["d_reliable"] = round(_solve_d_edge(_budget_reliable, _cfg["d_BP"]), 1)

for _tech, _cfg in RSU_CONFIG.items():
    _margin = _shadow_fade_margin_db(TECH_CONFIG[_tech]["f_c"], "rsu")
    _budget_reliable = _pl(_cfg["d_edge"], _cfg["d_BP"]) - _margin
    _cfg["d_reliable"] = round(_solve_d_edge(_budget_reliable, _cfg["d_BP"]), 1)

# 폐기된 상수의 이름을 유지하되 유도값을 담는다 — 기존 import 경로 호환용.
# (신뢰 반경 기준 — 예전엔 d_edge를 담아 마진 0 반경을 그대로 노출했다.)
RSU_COVERAGE_RADIUS_M: dict[str, float] = {
    _t: _c["d_reliable"] for _t, _c in RSU_CONFIG.items()
}


def resolve_unit(tech: str, node_type: str | None = None) -> dict:
    """(기술, 유닛 타입) → 파라미터 묶음. BS면 TECH_CONFIG, RSU면 RSU_CONFIG.

    RSU는 d_BP·d_edge·α·C_tech가 전부 다르다(h_RSU=5m, ΔP=20dB에서 유도).
    타입을 안 넘기면 BS로 본다 — 기존 호출부 호환.
    """
    if str(node_type or "").lower() in _RSU_TYPES:
        return RSU_CONFIG.get(tech.upper(), RSU_CONFIG["5G"])
    return _resolve_tech(tech)


def _resolve_tech(tech: str) -> dict:
    return TECH_CONFIG.get(tech, TECH_CONFIG["5G"])


# ── 활성 기술 모드 (main이 policy_options.network_mode 적용 시 갱신) ─────────────
_active_mode: str = "5G"


def set_active_mode(mode: str) -> None:
    global _active_mode
    if mode in TECH_CONFIG:
        _active_mode = mode


def get_active_mode() -> str:
    return _active_mode


# ── 공개 계산 함수 ──────────────────────────────────────────────────────────────
def path_loss(tech: str, d: float, node_type: str | None = None) -> float:
    p = resolve_unit(tech, node_type)
    return _pl(d, p["d_BP"])


def compute_rsrp_dbm(tech: str, d: float, a_seg_db: float = 0.0,
                     node_type: str | None = None) -> float:
    """P_rx = P_tx − α − PL(d) − A_seg (명세 §5). §9의 연결 규칙 기준값.

    RSU는 P_tx가 ΔP만큼 낮고 α도 그에 맞춰 역산돼 있으므로, 타입만 넘기면
    두 유닛의 수신세기를 같은 척도에서 비교할 수 있다(배치설계 v2 §3-2).
    """
    p = resolve_unit(tech, node_type)
    p_tx = P_TX_DBM - (DELTA_P_RSU_DB if str(node_type or "").lower() in _RSU_TYPES else 0.0)
    return p_tx - p["alpha"] - _pl(d, p["d_BP"]) - a_seg_db


def compute_sinr_db(tech: str, d: float, a_seg_db: float = 0.0,
                    node_type: str | None = None) -> float:
    """SINR = P_rx − noise_floor. 간섭 I=0 (SNR 근사, 명세 §5).

    noise_floor는 기술별 대역폭에서 계산된 값(resolve_unit()["noise_floor_dbm"])을
    쓴다 — RSU/BS 모두 같은 기술이면 같은 대역폭이라 같은 값이다.
    """
    p = resolve_unit(tech, node_type)
    return compute_rsrp_dbm(tech, d, a_seg_db, node_type) - p["noise_floor_dbm"]


def compute_latency(
    tech: str,
    d: float,
    n_vehicles: float,
    a_seg_db: float = 0.0,
    deficit_ratio: float = 0.0,
    node_type: str | None = None,
) -> dict:
    """
    명세 §1의 compute_latency. 반환 dict:
      l_total_ms, l_base_ms, l_transmission_ms, l_queue_ms,
      outage(bool), sinr_db, rsrp_dbm, spectral_efficiency
    l_total_ms는 RAN 구간만 — edge_latency_ms(MEC 등) 합산은 호출부 책임.
    """
    p = resolve_unit(tech, node_type)
    sinr = compute_sinr_db(tech, d, a_seg_db, node_type)
    rsrp = sinr + p["noise_floor_dbm"]

    # Outage 판정이 세 구성요소 합산보다 먼저 (명세 §10 체크리스트)
    if sinr < SINR_MIN_DB:
        return {
            "l_total_ms": L_OUTAGE_MS, "l_base_ms": 0.0,
            "l_transmission_ms": L_OUTAGE_MS, "l_queue_ms": 0.0,
            "outage": True, "sinr_db": round(sinr, 2), "rsrp_dbm": round(rsrp, 2),
            "spectral_efficiency": 0.0,
        }

    l_base = p["TTI"] * 0.5

    se = MCS_TABLE[0][1]
    for thr, se_val in MCS_TABLE:
        if sinr >= thr:
            se = se_val
    l_trans = (PACKET_SIZE_BITS / (se * p["BW"])) * 1000.0

    rho = min(max(n_vehicles, 0.0) / p["C_tech"], 0.99)   # 0.99 상한 (명세 §8)
    l_queue = p["TTI"] * rho / (1.0 - rho)
    if deficit_ratio > 0.0:
        l_queue += p["TTI"] * max(0.0, deficit_ratio)     # RB 부족 선형 가산 (유지 결정)

    return {
        "l_total_ms": round(l_base + l_trans + l_queue, 3),
        "l_base_ms": round(l_base, 3),
        "l_transmission_ms": round(l_trans, 3),
        "l_queue_ms": round(l_queue, 3),
        "outage": False, "sinr_db": round(sinr, 2), "rsrp_dbm": round(rsrp, 2),
        "spectral_efficiency": se,
    }


def resolve_coverage_radius(tech: str, node_type: str | None) -> float:
    """
    기술 모드 기반 커버리지 반경 (2026-07-16 결정: 노드에 저장된 고정값 대신
    평가 시점의 network_mode로 실시간 결정 — 모드 전환이 즉시 반영된다).

    d_edge가 아니라 d_reliable(쉐도잉 마진 반영, 위 _SHADOW_FADE_MARGIN_DB 참고)을
    반환한다 — d_edge는 무차폐 시 SINR이 문턱에 "딱" 닿는 마진 0 경계라 라우팅
    후보 필터링·coverage_risk 비용·UI 표시용 "커버리지"로 쓰기엔 낙관적이었다
    (2026-08-04 결정). alpha·d_edge 자체와 그 검증값은 그대로다 — 이 함수가
    반환하는 반경만 더 보수적으로 바뀐다.
    """
    return resolve_unit(tech, node_type)["d_reliable"]


# ── LATENCY_REGISTRY 등록 — 경로비용(evaluate_path)이 이 기본값을 통해 사용 ──────
def _registry_adapter(inp) -> "LatencyOutput":  # noqa: F821 — 지연 임포트
    """
    LatencyInput → 명세 v3.1 계산. 기술 모드는 set_active_mode()로 주입된
    활성 모드를 쓴다(레지스트리 인터페이스에 모드 필드가 없음).
    latency = RAN L_total + edge_latency_ms(MEC) — outage면 L_OUTAGE 그대로.

    RSU(PC5 사이드링크)는 명세 §1~8(셀룰러 Uu)의 대상이 아니다 — analyze_candidates()/
    _find_best_bs_light()의 RSU 분기와 동일하게 별도 선형 모델(HARQ 재전송·큐잉 없음)로
    처리한다. 이전에는 node_type을 못 받아 모든 노드가 BS 파라미터(d_BP/α/C_tech)로
    계산돼, RSU가 자기 커버리지·전력과 무관한 값으로 평가되는 불일치가 있었다.
    """
    from app.services.latency.registry import LatencyOutput

    bs = inp.base_station
    node_type = getattr(bs, "node_type", "") or None
    is_rsu = str(node_type or "").lower() in _RSU_TYPES

    if is_rsu:
        cov_r = resolve_coverage_radius(_active_mode, node_type)
        ratio = max(0.0, min(1.0, inp.dist_m / max(cov_r, 1.0)))
        l_air = round(1.0 + ratio * 2.0, 3)   # PC5 접속 지연 — _L_rsu와 동일 모델
        total = round(l_air + bs.edge_latency_ms, 4)
        return LatencyOutput(
            latency=total,
            propagation_delay=l_air,
            transmission_delay=0.0,
            queueing_delay=0.0,
            mec_processing_delay=bs.edge_latency_ms,
            handover_delay=0.0,
            blockage_delay=0.0,
            debug_info={
                "tech": _active_mode, "node_type": node_type, "rsu": True,
                "dist_m": round(inp.dist_m, 1), "coverage_radius_m": cov_r,
            },
        )

    n_vehicles = (
        1
        + int(getattr(bs, "n_background_vehicles", 0))
        + int(getattr(bs, "n_other_devices", 0))
        + int(getattr(bs, "n_its_load", 0))
    )
    deficit_ratio = float(getattr(bs, "deficit_rb", 0.0)) / max(bs.capacity, 1.0)
    r = compute_latency(
        _active_mode, inp.dist_m, n_vehicles,
        a_seg_db=inp.building_state.loss_db,
        deficit_ratio=deficit_ratio,
        node_type=node_type,
    )
    total = L_OUTAGE_MS if r["outage"] else round(r["l_total_ms"] + bs.edge_latency_ms, 4)
    return LatencyOutput(
        latency=total,
        propagation_delay=r["l_base_ms"],        # 슬롯 대기 (L_base)
        transmission_delay=r["l_transmission_ms"],
        queueing_delay=r["l_queue_ms"],
        mec_processing_delay=0.0 if r["outage"] else bs.edge_latency_ms,
        handover_delay=0.0,   # 핸드오버는 경로비용의 독립 컴포넌트 — 여기 넣으면 이중계산
        blockage_delay=0.0,   # A_seg는 SINR 경유로 반영됨 — 가산항 아님
        debug_info={
            "tech": _active_mode,
            "sinr_db": r["sinr_db"],
            "rsrp_dbm": r["rsrp_dbm"],
            "outage": r["outage"],
            "a_seg_db": inp.building_state.loss_db,
            "n_vehicles": n_vehicles,
            "spectral_efficiency": r["spectral_efficiency"],
        },
    )


def _register() -> None:
    from app.services.latency.registry import LATENCY_REGISTRY

    LATENCY_REGISTRY.register(
        "tech_latency_v31",
        _registry_adapter,
        description="3GPP 2-Slope PL + MCS + M/M/1 통합 모델 — 설계문서 v3.1 (기본값)",
        requires_buildings=True,
        requires_mec=False,
    )


_register()
