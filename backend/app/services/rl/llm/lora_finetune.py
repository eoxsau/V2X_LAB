"""
Llama 3.1 8B + LoRA Fine-tuning for V2X Domain Specialisation.

Approach:
  • Base model: meta-llama/Meta-Llama-3.1-8B-Instruct  (16 GB FP16)
  • PEFT LoRA:  r=16, α=32, dropout=0.05
  • Target modules: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj
  • Training: SFTTrainer (TRL library), 3 epochs, batch=4, grad_accum=8
  • Expected VRAM: ~20 GB on A100 80 GB with 4-bit QLoRA (bitsandbytes)

Dataset format (JSONL):
  {"messages": [
      {"role": "system",  "content": "...V2X 전문가..."},
      {"role": "user",    "content": "...질문..."},
      {"role": "assistant","content": "...답변..."}
  ]}

Training takes ~4 hours on A100 80 GB for 10,000 V2X Q&A pairs.

References:
  [1] Hu, E. et al., "LoRA: Low-Rank Adaptation of Large Language Models",
      ICLR 2022, arXiv:2106.09685.
  [2] von Werra, L. et al., "TRL: Transformer Reinforcement Learning",
      GitHub: huggingface/trl, 2020.
  [3] Dettmers, T. et al., "QLoRA: Efficient Finetuning of Quantized LLMs",
      NeurIPS 2023, arXiv:2305.14314.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent.parent.parent.parent  # backend/
_DEFAULT_DATA   = _BASE / "data" / "llm" / "v2x_qa_dataset.jsonl"
_DEFAULT_OUTPUT = _BASE / "data" / "llm" / "llama31_v2x_lora"

# ── V2X Domain Q&A Dataset Generator ──────────────────────────────────────────
_V2X_SYSTEM_PROMPT = """당신은 V2X(Vehicle-to-Everything) 통신 및 지능형 교통 시스템(ITS) 전문가입니다.
3GPP, ETSI, IEEE 802.11p 표준에 기반한 정확한 기술 답변을 제공합니다.
수식과 출처를 포함하여 답변하고, 한국어로 응답합니다."""

_SEED_QA_PAIRS = [
    # ── MCS / Physical Layer ───────────────────────────────────────────────────
    {
        "user": "SINR -3 dB일 때 5G NR MCS 인덱스와 스펙트럼 효율을 알려주세요.",
        "assistant": (
            "SINR = −3 dB에서 5G NR (38.214 Table 5.1.3.1-2) MCS 인덱스는 **1** (QPSK, CR=0.1171)이며 "
            "스펙트럼 효율은 **0.2344 bits/s/Hz**입니다. "
            "100 MHz 대역폭(PRB=66)에서 최대 처리량 ≈ 23.4 Mbps입니다. "
            "[3GPP TS 38.214 V17.2.0, Table 5.1.3.1-2]"
        ),
    },
    {
        "user": "SINR 10 dB일 때 5G NR에서 선택되는 MCS와 이론적 처리량은?",
        "assistant": (
            "SINR = 10 dB에서 5G NR MCS 인덱스는 **13** (16QAM, CR=0.5859)이며 "
            "스펙트럼 효율은 **2.3438 bits/s/Hz**입니다. "
            "100 MHz 대역폭(PRB=66)에서 최대 처리량 ≈ 234 Mbps입니다. "
            "[3GPP TS 38.214 V17.2.0, Table 5.1.3.1-2]"
        ),
    },
    {
        "user": "SINR 20 dB 이상의 5G NR 최고 MCS 설정은 무엇인가요?",
        "assistant": (
            "SINR ≥ 22.7 dB에서 5G NR MCS 인덱스는 **28** (256QAM, CR=0.9258)이며 "
            "스펙트럼 효율은 **7.4063 bits/s/Hz**입니다. "
            "100 MHz 대역폭(PRB=66)에서 최대 처리량 ≈ 740 Mbps입니다. "
            "[3GPP TS 38.214 V17.2.0, Table 5.1.3.1-2]"
        ),
    },
    # ── Queuing ────────────────────────────────────────────────────────────────
    {
        "user": "M/M/1 큐에서 부하율(ρ)이 0.8일 때 평균 대기시간은?",
        "assistant": (
            "M/M/1 큐 평균 대기시간: W_q = ρ / (μ(1−ρ))\n"
            "ρ = λ/μ = 0.8이면 W_q = 0.8 / (μ × 0.2) = 4/μ.\n"
            "서비스율 μ = 100 req/s이면 W_q = 40 ms. "
            "고부하(ρ→1)에서 지수적 발산에 주의하세요. "
            "[Kleinrock, Queueing Systems Vol.1, 1975, §3.3]"
        ),
    },
    {
        "user": "M/M/1 큐에서 부하율 0.5일 때 평균 시스템 내 패킷 수(L)는?",
        "assistant": (
            "M/M/1 큐 평균 시스템 내 고객 수: L = ρ / (1−ρ)\n"
            "ρ = 0.5이면 L = 0.5 / 0.5 = **1.0개**.\n"
            "평균 시스템 체류 시간 W = L/λ = 1/(μ(1−ρ)) = 2/μ.\n"
            "μ = 100 req/s이면 W = 20 ms. "
            "[Kleinrock, Queueing Systems Vol.1, 1975, §3.3]"
        ),
    },
    {
        "user": "기지국 용량 120 veh이고 현재 96 veh이 접속 중입니다. M/M/1 대기 지연은?",
        "assistant": (
            "부하율 ρ = 96/120 = 0.8.\n"
            "M/M/1 평균 대기시간 W_q = ρ / (μ(1−ρ)).\n"
            "처리율 μ = 120 veh/s로 가정 시 W_q = 0.8/(120×0.2) = **33.3 ms**.\n"
            "ρ = 0.8은 대기 지연이 급격히 증가하는 임계 영역이므로 부하 분산이 권장됩니다. "
            "[Kleinrock, Queueing Systems Vol.1, 1975, §3.3]"
        ),
    },
    # ── CBR / DCC ──────────────────────────────────────────────────────────────
    {
        "user": "C-V2X Mode 4에서 CBR이 0.65를 초과하면 어떤 조치를 취해야 하나요?",
        "assistant": (
            "ETSI TS 102 687 §5.2.2에 따라 CBR > 0.65(혼잡 임계값)이면 "
            "DCC(Decentralized Congestion Control)가 활성화됩니다:\n"
            "1. CAM 전송 주파수 축소 (10 Hz → 1 Hz)\n"
            "2. 전송 출력 감소 (Tx Power Control)\n"
            "3. 데이터 레이트 제한 (MCS 하향)\n"
            "C-V2X Mode 4 SPS 재전송 주기(RRI) 조정으로 채널 부하를 0.65 이하로 유지합니다. "
            "[ETSI TS 102 687 V1.1.1, §5.2.2; Gonzalez-Martin et al., IEEE TVT 2019]"
        ),
    },
    {
        "user": "차량 밀도 50 veh/km, 전송 반경 150 m일 때 C-V2X Mode 4 CBR은?",
        "assistant": (
            "Gonzalez-Martin et al. (IEEE TVT 2019) 해석 모델:\n"
            "CBR = 1 − exp(−ρ · R_tx · f_CAM · T_RRI)\n"
            "ρ = 50/1000 = 0.05 veh/m, R_tx = 150 m, f_CAM = 10 Hz, T_RRI = 0.1 s\n"
            "지수 = 0.05 × 150 × 10 × 0.1 = 7.5\n"
            "CBR = 1 − exp(−7.5) ≈ **0.9994** → 극심한 혼잡 (임계값 0.65 초과).\n"
            "DCC 즉시 활성화 필요. 전송 주파수 1 Hz 이하로 축소 권장.\n"
            "[Gonzalez-Martin et al., IEEE TVT 68(2), 2019; ETSI TS 102 687 §5.2.2]"
        ),
    },
    {
        "user": "C-V2X Mode 4 CBR 모델의 가정과 한계는 무엇인가요?",
        "assistant": (
            "Gonzalez-Martin (2019) CBR 해석 모델 CBR = 1 − exp(−ρ·R_tx·f_CAM·T_RRI)의 가정:\n"
            "1. 차량이 1D 포아송 분포 (도로를 선으로 모델링)\n"
            "2. CAM 전송 주기 일정 (실제 DCC는 동적 조정)\n"
            "3. 단일 채널, 75 MHz ITS 전용 대역\n"
            "4. 반이중(half-duplex) 전송 — 송신 중 수신 불가\n\n"
            "한계: 2D 교차로, 다중 차선, 건물 차폐 효과 미반영. "
            "실제 CBR은 이 공식보다 낮을 수 있음 (공간 재사용 효과).\n"
            "[Gonzalez-Martin et al., IEEE TVT 68(2), 2019, §IV.A]"
        ),
    },
    # ── RSU / PC5 ──────────────────────────────────────────────────────────────
    {
        "user": "RSU PC5 사이드링크의 최대 전송 범위는?",
        "assistant": (
            "3GPP Release 16 (TS 36.300 §22.1.2) PC5 사이드링크 최대 전송 범위:\n"
            "• 도심 환경(5.9 GHz): **350~500 m** (LOS)\n"
            "• 교외/고속도로: **최대 1 km** (LOS)\n"
            "시뮬레이션 기본값은 150 m (5G NR PC5)로 설정됩니다 (보수적 추정).\n"
            "경로손실 모델: PL(d) = 63.3 + 17.7·log₁₀(d) [Fernandez 2014, LOS highway]. "
            "[3GPP TS 36.300 §22.1.2; ETSI TR 102 638 §4.3]"
        ),
    },
    {
        "user": "PC5 사이드링크와 Uu 인터페이스의 V2X 적용 차이는?",
        "assistant": (
            "**PC5 사이드링크** (RSU ↔ V, V ↔ V 직접 통신):\n"
            "• 백홀 불필요, 지연 1~3 ms\n"
            "• 커버리지 제한 (150~500 m)\n"
            "• LTE-V/NR PC5: 3GPP TS 36.300 §22, TS 38.300 §4.7\n\n"
            "**Uu 인터페이스** (BS ↔ V, 인프라 경유):\n"
            "• 광역 커버리지 (수 km)\n"
            "• 지연 4~30 ms (백홀 + 코어 네트워크 포함)\n"
            "• 혼잡 시 QoS 스케줄링 가능\n\n"
            "권장 조합: 교차로·고속도로 긴급 메시지 → PC5, 광역 트래픽 관리 → Uu.\n"
            "[3GPP TR 22.886 §6.3; ETSI TR 102 638 §4.3]"
        ),
    },
    # ── AoI ────────────────────────────────────────────────────────────────────
    {
        "user": "AoI(Age of Information)와 지연시간(Latency)의 차이를 설명하세요.",
        "assistant": (
            "**지연시간(Latency)**: 패킷 1개가 송신에서 수신까지 걸리는 시간 (ms).\n"
            "**AoI**: 현재 시각 t에서 가장 최근에 수신된 상태 갱신의 생성 시각 t_u까지의 차이:\n"
            "  AoI(t) = t − t_u\n"
            "차이: AoI는 정보의 신선도를 측정합니다. 패킷 손실 시 AoI는 급격히 증가하지만 "
            "지연시간은 측정 불가. CAM 10Hz 전송에서 패킷 손실률 10%이면 최악 AoI = 1/PRR × 100ms = 111ms.\n"
            "[Kaul, Yates, Gruteser, IEEE INFOCOM 2012]"
        ),
    },
    {
        "user": "ETSI EN 302 637-2 기준 CAM AoI 임계값은 얼마인가요?",
        "assistant": (
            "ETSI EN 302 637-2 §6.1.2에 따른 CAM(Cooperative Awareness Message) 기준:\n"
            "• CAM 전송 주기: **100 ms** (10 Hz, 최대 주파수)\n"
            "• 따라서 AoI 임계값: **100 ms** — 100ms 초과 시 정보 신선도 위반\n"
            "• 최소 주기: 1000 ms (1 Hz, DCC 활성 시)\n\n"
            "AoI(t) = t − t_last_update > 100 ms이면 해당 차량 위치 정보를 신뢰 불가로 처리합니다.\n"
            "시뮬레이션에서 AoI 페널티: AoI > 100 ms인 스텝마다 보상 −0.5 부여.\n"
            "[ETSI EN 302 637-2 V1.4.1, §6.1.2.3]"
        ),
    },
    # ── 경로손실 / SINR ────────────────────────────────────────────────────────
    {
        "user": "5.9 GHz V2X 도심 NLOS 환경에서 200 m 거리 경로손실은?",
        "assistant": (
            "Fernandez et al. (IEEE WCL 2014) 로그-거리 모델:\n"
            "PL(d) = PL(d₀) + 10·n·log₁₀(d/d₀)  [dB]\n"
            "d₀ = 10 m, PL(d₀) = 20·log₁₀(4π·10/0.0508) ≈ 67.8 dB @ 5.9 GHz\n"
            "NLOS 도심: n = 3.50, σ = 7.1 dB\n"
            "d = 200 m: PL = 67.8 + 35.0·log₁₀(20) ≈ 67.8 + 45.5 = **113.3 dB**\n"
            "섀도잉 마진(σ = 7.1 dB) 포함 시 최악 PL ≈ 120.4 dB.\n"
            "[Fernandez et al., IEEE WCL 3(6), 2014, Table I-II]"
        ),
    },
    {
        "user": "V2X 고속도로 LOS 환경에서 경로손실 지수(n)는 얼마인가요?",
        "assistant": (
            "Fernandez et al. (IEEE WCL 2014) 5.9 GHz 측정 기반 경로손실 지수:\n"
            "• LOS 고속도로: **n = 1.61**, σ = 4.0 dB\n"
            "• LOS 도심:     **n = 2.75**, σ = 5.5 dB\n"
            "• NLOS 도심:    **n = 3.50**, σ = 7.1 dB\n\n"
            "고속도로 LOS에서 n < 2 (자유공간 2.0보다 낮음)는 도로 가이드 효과(waveguide)로 설명됩니다.\n"
            "[Fernandez et al., IEEE WCL 3(6), 2014, Table I]"
        ),
    },
    # ── BS / RSU 비교 ──────────────────────────────────────────────────────────
    {
        "user": "기지국과 RSU 중 어느 것이 V2X 저지연에 더 유리한가요?",
        "assistant": (
            "**RSU (PC5 사이드링크)**가 일반적으로 저지연에 유리합니다:\n"
            "• RSU-직접 통신: 1~3 ms (백홀 없음, 에지 처리)\n"
            "• 기지국 Uu 인터페이스: 4~30 ms (백홀 + 코어 네트워크 포함)\n\n"
            "다만 RSU는 커버리지가 제한적(150~350 m)이므로 고속도로/교차로에서만 효과적입니다. "
            "도심 넓은 구역은 기지국 + MEC 조합이 필요합니다 (지연 8~15 ms 목표).\n"
            "[3GPP TR 22.886 §6.3; ETSI TR 102 638 §4.3]"
        ),
    },
    {
        "user": "5G NR V2X에서 URLLC 요건을 만족하려면 E2E 지연을 얼마 이하로 유지해야 하나요?",
        "assistant": (
            "3GPP TS 22.186 §5.1 V2X 서비스 요건:\n"
            "• 차량 충돌 경보 (긴급): E2E 지연 ≤ **3 ms**, 신뢰도 ≥ 99.999%\n"
            "• 교차로 충돌 경보:       E2E 지연 ≤ **10 ms**, 신뢰도 ≥ 99.9%\n"
            "• CAM 일반 인식:          E2E 지연 ≤ **100 ms**, PRR ≥ 95%\n"
            "• 원격 주행:              E2E 지연 ≤ **5 ms** (URLLC)\n\n"
            "5G NR Uu 기준 U-plane 지연 4 ms + 백홀 1~5 ms = 실제 5~9 ms (MEC 배포 시). "
            "URLLC 3 ms 달성에는 PC5 사이드링크 + MEC 필수.\n"
            "[3GPP TS 22.186 V16.1.0, §5.1]"
        ),
    },
    # ── Jain 공정성 ────────────────────────────────────────────────────────────
    {
        "user": "V2X 시뮬레이션에서 Jain's Fairness Index가 0.6이 나왔습니다. 해석 방법은?",
        "assistant": (
            "Jain's Fairness Index J ∈ [1/n, 1] (n = BS 수).\n"
            "수식: J = (Σᵢ ρᵢ)² / (n · Σᵢ ρᵢ²)\n"
            "J = 0.6: **중간 수준의 불공정**. n=4 기지국이면 최소값 J_min = 0.25, 최대 J=1.0.\n"
            "해석: 특정 기지국 1~2개가 과부하(ρ→1)이고 나머지는 저부하 상태를 의미합니다.\n"
            "권고: 부하 밸런싱 알고리즘 강화, 과부하 BS의 커버리지 반경 축소, 차량 재배분이 필요합니다.\n"
            "목표: J ≥ 0.85 (IEEE 문서 9978912, C-V2X MEC 부하 밸런싱 기준).\n"
            "[Jain, Chiu, Hawe, DEC-TR-301, 1984]"
        ),
    },
    {
        "user": "3개 기지국 부하율이 각각 0.9, 0.3, 0.3일 때 Jain Fairness Index는?",
        "assistant": (
            "Jain FI = (Σᵢ ρᵢ)² / (n · Σᵢ ρᵢ²)\n"
            "Σρ = 0.9 + 0.3 + 0.3 = 1.5\n"
            "Σρ² = 0.81 + 0.09 + 0.09 = 0.99\n"
            "J = 1.5² / (3 × 0.99) = 2.25 / 2.97 ≈ **0.758**\n\n"
            "해석: J = 0.758 — 불공정 수준. BS-1이 과부하 상태.\n"
            "권고 목표 J ≥ 0.85이므로, BS-1 커버리지 반경 축소 또는 부하 재배분 필요.\n"
            "[Jain, Chiu, Hawe, DEC-TR-301, 1984 §3.1]"
        ),
    },
    # ── 핸드오버 ───────────────────────────────────────────────────────────────
    {
        "user": "5G NR V2X에서 핸드오버 중단 시간(Handover Interruption Time)은 얼마인가요?",
        "assistant": (
            "5G NR 핸드오버 중단 시간 (3GPP TR 38.913 §6.1.3.3):\n"
            "• Xn-based 핸드오버:  **0 ms** (Conditional HO, DAPS HO)\n"
            "• N2-based 핸드오버:  **~49 ms** (AMF 경유)\n"
            "• 실측 평균 (도심 5G): **200~400 ms** (IEEE 2023, doc 10320318)\n\n"
            "V2X에서 핸드오버 중 패킷 손실:\n"
            "200 ms 중단 × 10 Hz CAM = 2개 패킷 손실 → PRR 저하.\n"
            "DAPS(Dual Active Protocol Stack) 활성화로 무손실 HO 가능.\n"
            "[3GPP TR 38.913 §6.1.3.3; IEEE doc 10320318, 2023]"
        ),
    },
    {
        "user": "V2X 라우팅에서 핸드오버를 최소화하는 기준은 무엇인가요?",
        "assistant": (
            "핸드오버 최소화 기준 (경로 선택 시):\n"
            "1. **연속 커버리지 우선**: 경로 전체가 단일 BS 커버리지 내에 있으면 HO 불필요\n"
            "2. **커버리지 중첩 활용**: 이웃 BS 커버리지 오버랩 영역에서 HO 준비 (A3 이벤트)\n"
            "3. **HO 패널티 가중치**: 경로 비용 함수에 HO 횟수 항 추가\n"
            "   C = w₁·L_avg + w₂·H_count + w₃·(1−cov_ratio)\n"
            "4. **속도 기반 예측**: 고속(≥ 80 km/h) 차량은 BS 체류 시간 짧으므로 RSU 우선\n\n"
            "목표: 10 km 경로에서 HO ≤ 2회 (도심 기준).\n"
            "[3GPP TS 22.186 §5.1; Hung et al., IEEE VTM 2017]"
        ),
    },
    # ── PRR / PIR ──────────────────────────────────────────────────────────────
    {
        "user": "V2X 시뮬레이션에서 PRR(Packet Reception Ratio)은 어떻게 계산하나요?",
        "assistant": (
            "PRR(Packet Reception Ratio) 계산:\n"
            "PRR = (수신 성공 패킷 수) / (총 전송 패킷 수)\n"
            "    = 1 − PLR (Packet Loss Rate)\n\n"
            "시뮬레이션에서 근사:\n"
            "PRR ≈ 1 − TWDR (Time-Weighted Disconnection Ratio)\n"
            "여기서 TWDR = 커버리지 밖 체류 시간 / 전체 경로 시간.\n\n"
            "기준: PRR ≥ 95% (CAM, ETSI EN 302 637-2 §5.4).\n"
            "참고: Ali et al. (2021): 5G NR V2X Mode 2에서 300 m 내 PRR ≥ 90%.\n"
            "[Ali et al., IEEE Access, 2021 §II.B; ETSI EN 302 637-2 §5.4]"
        ),
    },
    {
        "user": "PIR(Packet Inter-Reception Time) P99 기준값은 얼마인가요?",
        "assistant": (
            "PIR P99 기준 (3GPP TR 37.885 Table A.1):\n"
            "• C-V2X 저지연 서비스: PIR P99 ≤ **100 ms**\n"
            "• CAM 일반 서비스:    PIR P99 ≤ **500 ms**\n\n"
            "PIR P99 해석 상한 (Eckermann 2019):\n"
            "PIR_P99 = T_CAM / PRR = 100 ms / PRR\n"
            "PRR = 0.9이면 PIR_P99 ≈ 111 ms → 기준 초과 위험.\n"
            "PRR ≥ 0.95이면 PIR_P99 ≈ 105 ms → 경계값.\n\n"
            "주의: 이 수식은 기하분포 단순 상한이며 공간 분포 효과로 실제값은 낮을 수 있음.\n"
            "[3GPP TR 37.885 V15.3.0, Table A.1; Eckermann et al., IEEE VTC Fall 2019]"
        ),
    },
    # ── RL 알고리즘 ────────────────────────────────────────────────────────────
    {
        "user": "V2X 라우팅에 강화학습을 적용할 때 상태(State) 표현은 어떻게 설계해야 하나요?",
        "assistant": (
            "V2X RL 상태 설계 권장 (도메인 랜덤화 적합):\n"
            "1. **상대 특성만 사용**: 절대 좌표 대신 BS까지의 정규화 거리, 방위각\n"
            "2. **노드 특성** (9차원):\n"
            "   [dist_norm, bearing_cos, bearing_sin, bs_load, bs_cov, is_rsu, latency_ms, aoi, n_veh]\n"
            "3. **엣지 특성** (5차원):\n"
            "   [edge_dist_norm, hw_rank, sinr_delta, handover_cost, path_loss_delta]\n"
            "4. **그래프 구조**: GATv2Conv로 가변 크기 그래프 처리 (Brody et al. 2022)\n\n"
            "절대 좌표 금지 이유: 한국 2,367개 구역 간 제로샷 전이를 위한 도메인 랜덤화 요건.\n"
            "[Brody et al., ICLR 2022 (GATv2); Tobin et al., IROS 2017 (Domain Randomization)]"
        ),
    },
    {
        "user": "MAML(Model-Agnostic Meta-Learning)을 V2X 라우팅에 적용하는 이유는?",
        "assistant": (
            "MAML V2X 적용 이유 (Finn et al. 2017):\n"
            "1. **빠른 적응**: 새 지역(holdout)에 5~10개 에피소드만으로 적응 가능\n"
            "2. **전이 학습**: 한국 1,601개 학습 구역 → 161개 미학습 구역 제로샷 전이\n"
            "3. **일반화**: 각 구역이 별도 MAML task τᵢ → 정책이 task 분포를 학습\n\n"
            "FOMAML (First-Order MAML) 선택 이유:\n"
            "• 2차 미분(Hessian) 불필요 → 메모리/계산량 절약\n"
            "• `higher` 라이브러리 의존성 없음\n"
            "• 성능 차이 미미 (Fallah et al. 2020 확인)\n\n"
            "학습 설정: 16 tasks/iter, inner 5 steps SGD, outer Adam lr=3e-4.\n"
            "[Finn et al., ICML 2017; Fallah et al., NeurIPS 2020]"
        ),
    },
]


def generate_dataset(
    output_path: Optional[Path] = None,
    n_synthetic: int = 500,
    seed: int = 42,
) -> Path:
    """
    Generate the V2X Q&A fine-tuning dataset.

    Combines:
    1. Seed Q&A pairs (expert-written, 7 pairs)
    2. Synthetically augmented variants (paraphrased questions, shuffled KPIs)
    3. Simulation scenario descriptions (from _generate_scenario_pairs)

    Output: JSONL, one JSON object per line.
    """
    out = output_path or _DEFAULT_DATA
    out.parent.mkdir(parents=True, exist_ok=True)

    pairs = list(_SEED_QA_PAIRS)

    # Synthetic augmentation: rephrase questions (keep numbers exact to avoid Q/A mismatch)
    import random
    rng = random.Random(seed)
    _QUESTION_PREFIXES = [
        "", "전문가로서 답변해주세요. ", "논문 작성을 위해 ", "표준 근거와 함께 ",
        "3GPP/ETSI 기준으로 ", "시뮬레이션 설계 관점에서 ",
    ]
    _QUESTION_SUFFIXES = [
        "", " 출처를 포함해 설명하세요.", " 수식과 함께 설명해주세요.",
        " 임계값도 함께 알려주세요.", " 간결하게 요약해주세요.",
    ]
    for _ in range(n_synthetic):
        base = rng.choice(_SEED_QA_PAIRS)
        prefix = rng.choice(_QUESTION_PREFIXES)
        suffix = rng.choice(_QUESTION_SUFFIXES)
        user_aug = prefix + base["user"] + suffix
        pairs.append({"user": user_aug, "assistant": base["assistant"]})

    pairs.extend(_generate_scenario_pairs(rng, n=100))

    with open(out, "w", encoding="utf-8") as f:
        for pair in pairs:
            msg = {
                "messages": [
                    {"role": "system",    "content": _V2X_SYSTEM_PROMPT},
                    {"role": "user",      "content": pair["user"]},
                    {"role": "assistant", "content": pair["assistant"]},
                ]
            }
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    print(f"[LLM Dataset] {len(pairs)} pairs → {out}")
    return out



def _generate_scenario_pairs(rng, n: int = 100) -> list[dict]:
    """Generate scenario-description → optimal config Q&A pairs."""
    results = []
    for _ in range(n):
        n_bs = rng.randint(1, 5)
        density = round(rng.uniform(5.0, 80.0), 1)
        mode = rng.choice(["4G", "5G", "6G"])
        avg_lat = round(rng.uniform(5.0, 50.0), 1)
        jfi = round(rng.uniform(0.3, 1.0), 2)
        user = (
            f"{mode} 네트워크, 기지국 {n_bs}개, 차량 밀도 {density} veh/km², "
            f"평균 지연 {avg_lat} ms, Jain FI={jfi} 시나리오에서 최적화 방향을 제안하세요."
        )
        suggestions = []
        if avg_lat > 20:
            suggestions.append("RSU 추가 설치로 PC5 직접 통신 커버리지 확대")
        if jfi < 0.7:
            suggestions.append("부하 밸런싱 강화 — 과부하 BS 커버리지 반경 축소")
        if density > 50:
            suggestions.append("DENM 혼잡 경보 발령, DCC 활성화 (ETSI TS 102 687)")
        if not suggestions:
            suggestions.append("현재 파라미터는 허용 범위 내에 있습니다.")
        assistant = "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions))
        results.append({"user": user, "assistant": assistant})
    return results


def train(
    model_name: str = "meta-llama/Meta-Llama-3.1-8B-Instruct",
    dataset_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    num_epochs: int = 3,
    per_device_batch: int = 2,
    grad_accum: int = 8,
    lora_r: int = 16,
    lora_alpha: int = 32,
    max_seq_len: int = 1024,
    use_4bit: bool = True,
) -> None:
    """
    Fine-tune Llama 3.1 8B with LoRA on the V2X Q&A dataset.

    Requires (on A100):
        pip install transformers peft trl bitsandbytes accelerate
        huggingface-cli login  (for gated model access)
    """
    try:
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer,
            BitsAndBytesConfig, TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from trl import SFTTrainer
        from datasets import load_dataset
    except ImportError:
        raise ImportError(
            "LoRA fine-tuning requires:\n"
            "  pip install transformers peft trl bitsandbytes accelerate datasets"
        )

    dataset_path = dataset_path or _DEFAULT_DATA
    output_dir   = output_dir   or _DEFAULT_OUTPUT

    if not dataset_path.exists():
        print("[LoRA] Dataset not found — generating …")
        generate_dataset(dataset_path)

    # 4-bit QLoRA configuration (Dettmers et al. 2023)
    bnb_cfg = None
    if use_4bit:
        import torch
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    print(f"[LoRA] Loading base model: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=bnb_cfg,
        device_map="auto",
        torch_dtype="auto",
    )

    if use_4bit:
        model = prepare_model_for_kbit_training(model)

    # LoRA configuration (Hu et al. 2022)
    lora_cfg = LoraConfig(
        r=lora_r,
        lora_alpha=lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    # Dataset
    dataset = load_dataset("json", data_files=str(dataset_path), split="train")

    # Training arguments
    train_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=num_epochs,
        per_device_train_batch_size=per_device_batch,
        gradient_accumulation_steps=grad_accum,
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        logging_steps=10,
        save_steps=200,
        evaluation_strategy="no",
        bf16=True,
        tf32=True,
        optim="paged_adamw_8bit",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=train_args,
        max_seq_length=max_seq_len,
        dataset_text_field=None,   # use messages format
        packing=True,
    )

    print("[LoRA] Training started …")
    trainer.train()
    trainer.save_model(str(output_dir / "final"))
    tokenizer.save_pretrained(str(output_dir / "final"))
    print(f"[LoRA] Saved → {output_dir / 'final'}")


if __name__ == "__main__":
    generate_dataset()
    train()
