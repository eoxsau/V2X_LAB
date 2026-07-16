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
| α (alpha) | 60.1 | 76.5 | **87.6** | **하드코딩 금지** — 초기화 시 역산 (4절) |
| C_tech (수용량) | 100 | 500 | 2000 | 대/차량 |
| P_tx | 46 dBm | 46 dBm | 46 dBm | 모든 기지국 동일 값 사용 (필수 전제) |
| noise_floor | −95 dBm | −95 dBm | −95 dBm | |
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

검증용 기대값 (구현 후 assert 권장):
- 4G: α ≈ 60.1
- 5G: α ≈ 76.5
- 6G: α ≈ 87.6

계산 결과가 위 값과 ±0.2 이상 어긋나면 PL 구현(특히 d_BP 분기)이 잘못된 것이다.

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
- 건물당 12 dB, 선형 누적. 상한 클램프 없음.

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
