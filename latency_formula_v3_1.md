# Latency 수식 구현 명세 (v3.1)

> 이 문서는 시뮬레이션 코드의 latency 계산 로직을 아래 명세로 **전면 교체**하기 위한 구현 지시서다.
> 기존 코드의 latency 관련 수식·파라미터는 모두 이 문서 기준으로 덮어쓴다.
> 목적은 절대 지연 예측이 아니라 **기지국 배치안·경로 간 상대 비교**이므로, 아래 수식을 정확히 그대로 구현하는 것이 중요하다.

---

## 1. 최상위 로직

단일 기지국–차량 다운링크 지연. 무선 구간(RAN)만 계산하며 백홀 지연은 포함하지 않는다.

```
function compute_latency(tech, d, n_vehicles, A_seg) -> dict:
    # 1) SINR 계산
    SINR = compute_sinr(tech, d, A_seg)

    # 2) Outage 판정 (SINR < -6 dB이면 즉시 반환)
    if SINR < SINR_MIN:            # SINR_MIN = -6.0 dB
        return { L_total: L_OUTAGE, outage: True }   # L_OUTAGE = 1000.0 ms

    # 3) 세 구성요소 합산
    L_base         = TTI[tech] * 0.5
    L_transmission = compute_transmission(tech, SINR)
    L_queue        = compute_queue(tech, n_vehicles)

    return { L_total: L_base + L_transmission + L_queue, outage: False }
```

- 입력 `d`: 차량–기지국 3D 거리 (m). **최소 1.0 m로 클램프**할 것 (log₁₀(0) 방지).
- 입력 `A_seg`: 해당 (도로 세그먼트, 기지국) 쌍의 건물 차폐 손실 (dB). 사전 계산 테이블에서 조회 (7절).
- 모든 지연 단위는 **ms**로 통일한다.

---

## 2. 기술별 파라미터 테이블

| 파라미터 | 4G LTE | 5G NR | 6G (연구모드) | 비고 |
|---|---|---|---|---|
| f_c (반송파 주파수) | 2.0 GHz | 3.5 GHz | **7.0 GHz** | 6G는 FR3 upper mid-band 후보 대역 기반 추정치 |
| numerology (SCS) | 0 (15 kHz) | 1 (30 kHz) | **3 (120 kHz)** | 6G는 추정치 |
| TTI | 1.0 ms | 0.5 ms | **0.125 ms** | |
| L_base = TTI × 0.5 | 0.5 ms | 0.25 ms | **0.0625 ms** | |
| 대역폭 | 20 MHz | 100 MHz | 400 MHz | |
| d_edge (엣지 반경) | 2000 m | 1000 m | 500 m | |
| d_BP (breakpoint) | 320 m | 560 m | **1120 m** | 수식으로 산출 (3절) — 하드코딩하지 말고 f_c에서 계산 |
| α (alpha) | 57.04 | 66.47 | **71.60** | **하드코딩 금지** — 초기화 시 역산 (4절). noise_floor가 기술별로 바뀌면서 갱신됨(2026-08-06, 아래 참고) |
| C_tech (수용량) | 100 | 500 | 2000 | 대/차량 — 출처 미상, 용량 산정식 없이 그냥 정한 값(2026-08-05 감사에서 발견, 별도 재검토 필요) |
| P_tx | 46 dBm | 46 dBm | 46 dBm | 모든 기지국 동일 값 사용 (필수 전제). 3GPP UMa 평가의 통상적 매크로 BS 가정값(hBS=25m·ISD=500m과 함께 쓰임)으로 확인 — 정확한 TR 표 번호까지는 원문 대조 못 함 |
| noise_floor | **−91.99 dBm** | **−85.00 dBm** | **−78.98 dBm** | 2026-08-06 수정 — 예전엔 세 기술 다 −95dBm 고정이었다(6G는 실제보다 약 16dB 과소평가). N0 = −174dBm/Hz + 10log₁₀(BW) + NF, NF=9dB(차량 OBU 잡음지수 가정 — 3GPP TS 36.521 Table 7.3.3-1의 LTE Band 20/20MHz REFSENS −90dBm과 2dB 이내로 교차검증). RSU도 같은 기술=같은 대역폭이라 BS와 같은 값 사용 |
| SINR_MIN | −6.0 dB | −6.0 dB | −6.0 dB | outage 문턱 |
| L_OUTAGE | 1000 ms | 1000 ms | 1000 ms | |
| 패킷 크기 | 1 Mbit | 1 Mbit | 1 Mbit | 고정값 |

> **6G 값 표기**: 굵게 표시된 6G 값(f_c, numerology, TTI, L_base, d_BP, α)은 표준 근거가 아직 없는 **추정 설계값**이다. 코드 주석에 `# 6G: estimated design value` 를 남길 것. 값이 확정되면 이 테이블만 수정하면 되도록, 파라미터는 반드시 기술별 config dict 한 곳에 모아 관리한다.

---

## 3. 경로손실 PL(d) — 2-Slope Breakpoint 모델

### 3-1. Breakpoint 거리 (초기화 시 1회 계산)

```
d_BP = 4 * h_BS * h_UT * f_c / c
     # h_BS = 24.0 m,  h_UT = 0.5 m,  c = 3e8 m/s,  f_c는 Hz 단위
```

- 4G(2.0 GHz) → 320 m / 5G(3.5 GHz) → 560 m / 6G(7.0 GHz) → 1120 m
- 6G는 d_BP(1120 m) > d_edge(500 m)이므로 **셀 전체가 완만 구간(기울기 2.2)에서만 동작**한다. 정상 동작이며 버그가 아님.

### 3-2. 거리별 경로손실

```
if d <= d_BP:
    PL(d) = 10 * 2.2 * log10(d)
else:
    PL(d) = 10 * 2.2 * log10(d_BP) + 10 * 4.0 * log10(d / d_BP)
```

- 기울기 2.2 / 4.0은 세 기술 공통.
- 건물 손실은 여기 넣지 않는다. 건물은 **오직 A_seg만** 담당한다 (이중계산 금지).

---

## 4. α 역산 (초기화 시 1회 계산, 하드코딩 금지)

α는 "d_edge에서 SINR이 딱 SINR_MIN이 되도록" 역산하는 보정값이다.

```
alpha[tech] = P_tx - noise_floor - SINR_MIN - PL(d_edge[tech])
            # PL은 3절의 2-Slope 식을 그대로 사용
```

검증용 기대값 (구현 후 assert 권장, 2026-08-06 noise_floor 기술별 계산 반영 후 갱신):
- 4G: α ≈ 57.04
- 5G: α ≈ 66.47
- 6G: α ≈ 71.60

계산 결과가 위 값과 ±0.2 이상 어긋나면 PL 구현(특히 d_BP 분기)이 잘못된 것이다.
(예전 60.1/76.5/87.6은 noise_floor를 세 기술 다 −95dBm 고정으로 썼을 때의 값 — §2의
noise_floor 수정과 함께 갱신됨. RSU d_edge/§4 예전 검증값(312/156/62m)은 noise_floor와
무관하게 유도되므로 그대로 유효하다.)

---

## 5. SINR 계산

```
P_rx  = P_tx - alpha[tech] - PL(d) - A_seg
SINR  = P_rx - noise_floor        # dB 단위
```

- 간섭 I = 0 (SNR 근사). 간섭 항을 추가하지 말 것.

---

## 6. L_transmission — MCS 기반 전송 시간

### 6-1. MCS 테이블 (세 기술 공통)

SINR으로 사용 가능한 **가장 높은** MCS를 고른다. (`SINR >= 최소 SINR`을 만족하는 행 중 최대 MCS)

| MCS | 변조 | 스펙트럼 효율 (bit/s/Hz) | 최소 SINR (dB) |
|---|---|---|---|
| 0 | QPSK | 0.2344 | −6.0 |
| 4 | QPSK | 0.6016 | −2.0 |
| 9 | QPSK | 1.3262 | 3.0 |
| 12 | 16QAM | 1.6953 | 6.0 |
| 16 | 16QAM | 2.5703 | 9.0 |
| 20 | 64QAM | 3.3223 | 13.0 |
| 24 | 64QAM | 4.5234 | 17.0 |
| 28 | 64QAM | 5.9004 | 21.0 |

### 6-2. 전송 시간

```
rate_bps       = spectral_efficiency * bandwidth_hz[tech]
L_transmission = (PACKET_SIZE_BITS / rate_bps) * 1000    # ms 변환
               # PACKET_SIZE_BITS = 1_000_000 (1 Mbit)
```

검증용 예시 (5G, 100 MHz):
- MCS 28: 590.04 Mbps → L_transmission ≈ **1.695 ms**
- MCS 0: 23.44 Mbps → L_transmission ≈ **42.66 ms**

---

## 7. A_seg — 건물 차폐 (사전 계산)

```
A_seg = (차량–기지국 3D 직선을 가로막는 건물 수) * 12.0   # dB
```

- 판정은 **3D ray-casting**: 기지국 높이(h_BS=24 m)와 차량 높이(h_UT=0.5 m)를 잇는 3차원 직선이 건물 폴리곤(높이 포함)과 교차하는지 검사. 기지국보다 낮은 건물은 직선을 못 막을 수 있으므로 2D 판정 금지.
- 매 스텝 계산하면 느리므로 **초기화 시 (segment_id, bs_id) 키로 사전 계산**해 테이블 조회로 사용한다. 세그먼트 대표점(중점 또는 몇 개 샘플 지점 평균)을 기준으로 계산.
- 건물당 12 dB, 선형 누적. **상한 30 dB** (2026-08-04 수정 — `route_cost_function.NormScales.loss_db`의 "콘크리트 벽 최대" 값과 통일). 원래 "상한 없음"이었으나 조밀한 시가지 격자에서는 3D LOS 판정을 만족하는 건물이 5~10채(60~120dB)까지 나와 물리적으로 불가능한 값이 되고, c_blockage 비용과 대시보드 latency_penalty_ms에 그대로 전파돼 경로 선택·지표를 왜곡했다.
- ⚠️ **"건물당 12dB" 자체는 출처 미상 — 검증 안 된 휴리스틱이다** (2026-08-05 재검토). 이 시나리오(고가 기지국—도로변 차량이 건물 옆에서 가려지는 경우)에 맞는 정식 모델은 3GPP O2I가 아니다 — O2I는 "벽을 뚫고 실내로 들어가는" 손실이라 시나리오 자체가 다르다. 맞는 계열은 ITU-R P.1411의 다중 스크린 회절(Lbf+Lrts+Lmsd)이지만, 이건 거리·건물 배치·도로폭·입사각을 함께 쓰는 다변수 공식이라 "건물당 N dB" 스칼라로 축약되지 않는다. 30dB 상한은 그 공식의 근사치가 아니라 "상한 없음보다 나은, 그러나 검증되지 않은 안전장치"일 뿐이다. 제대로 하려면 ITU-R P.1411 모델을 별도로 구현해야 한다 — 별도 논의 필요.

---

## 8. L_queue — M/M/1 혼잡 대기

```
rho     = min(n_vehicles / C_tech[tech], 0.99)    # 0.99 상한 필수 (발산 방지)
L_queue = TTI[tech] * rho / (1.0 - rho)           # ms
```

- `n_vehicles`: 해당 기지국에 현재 접속 중인 차량 수. 상수가 아니라 **호출 시점의 실시간 값**을 넣는다 (RL 보상 계산 시 TraCI에서 그 순간 부하를 읽음).
- 검증용 예시: ρ=0.9일 때 4G → 9.0 ms, 5G → 4.5 ms, 6G → 1.125 ms.

---

## 9. 차량–기지국 연결 규칙

- 차량은 **RSRP(수신 세기) 최대** 기지국에 연결한다. 즉 `P_rx = P_tx - alpha - PL(d) - A_seg` 가 가장 큰 기지국을 고른다.
- 부하(n_vehicles) 기준으로 연결 기지국을 고르지 말 것 (부하 순환 회피).

---

## 9-A. 커버리지 반경 — 쉐도잉 마진 (2026-08-04 추가)

d_edge(§4)는 alpha 역산 앵커일 뿐 — "무차폐 시 SINR이 정확히 문턱"인 마진 0 경계다.
라우팅 후보 필터링/coverage_risk 비용/UI 표시에 쓰는 **커버리지 반경**은 d_edge가 아니라
여기서 유도하는 `d_reliable`을 쓴다. outage 판정(§1) 자체는 A_seg를 직접 받아 d_reliable과
무관하게 독립 계산되므로 이중계산이 아니다.

```
d_reliable[tech] = PL(d) = PL(d_edge[tech]) - k·σ_SF 를 만족하는 d   # 이분 탐색
```

- σ_SF(로그노멀 쉐도잉 표준편차)는 3GPP TR 38.901 Table 7.4.1-1 값을 쓴다. ETSI 원문 PDF는
  접근이 막혀 있어 그 표를 그대로 구현한 ns-3 레퍼런스 구현(three-gpp-propagation-loss-model.cc,
  2026-08-05 소스 직접 대조)을 1차 자료 대용으로 확인했다 — **f_c 기준으로 분기**한다:
  - f_c < 6GHz(4G 2.0GHz, 5G 3.5GHz): 시나리오 무관 σ_SF = **7.00 dB**
  - f_c ≥ 6GHz(6G 7.0GHz): BS(UMa, H_BS=24m) → σ_SF=**6.00dB**, RSU(UMi-SC에 근접, H_RSU=5m) → σ_SF=**7.82dB**
  (초판은 6G용 6.00/7.82를 4G/5G에도 그대로 썼던 오류가 있었다 — 6G만 우연히 일치했다.)
- k=1(현재 값): 쉐도잉이 평균 0·표준편차 σ_SF인 정규분포라 할 때, 그 반경에서 실제 SINR≥문턱일
  확률은 Φ(k) — k=0(마진 없음)은 50.0%, k=1은 84.1%. 목표 신뢰도를 바꾸려면 k만 바꾸면 된다.
- alpha·d_edge 자체와 §4의 검증값(60.1/76.5/87.6)은 이 절과 무관하게 그대로 유지된다.

---

## 10. 구현 체크리스트

- [ ] 파라미터는 기술별 config dict 한 곳에 집중 (`TECH_CONFIG = {'4G': {...}, '5G': {...}, '6G': {...}}`)
- [ ] d_BP와 α는 config의 f_c, d_edge에서 **초기화 시 계산** (하드코딩 금지)
- [ ] α 검증 assert: 60.1 / 76.5 / 87.6 (±0.2)
- [ ] outage 분기가 세 구성요소 합산보다 **먼저** 실행되는지 확인
- [ ] 거리 d 최소 1.0 m 클램프
- [ ] ρ 최대 0.99 클램프
- [ ] 모든 반환값 단위 ms 통일
- [ ] A_seg 사전 계산 테이블 (segment_id, bs_id) 키 구조 유지
- [ ] 6G 추정값에 `# 6G: estimated design value` 주석
