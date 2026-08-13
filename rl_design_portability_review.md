# 강화학습 설계서(v4 범용 배포 정책) 이식 가능성 검토

> **작성일** 2026-07-29 · **검토 대상** `V2X_AI_설계_v4_범용배포정책.pdf`, `V2X_AI_GPU_설계문서.pdf`, `V4_Universal_Policy_구현현황보고서.pdf`
> **검토 방식** 설계서 3종 전문 추출 → `v2x_lab` 코드베이스 읽기 전용 대조
> **코드 변경 없음.** 이 문서는 판정만 담는다.

---

## 0. 한 줄 결론

**설계서의 RL 환경 계약(상태·행동·보상)은 이미 코드에 들어와 있어 이식 부담이 거의 없다.
반대로 설계서가 "구현 완료"라고 적은 학습 스택(PPO/DQN/GNN/MAML/FL)은 이 저장소에 한 줄도
없다.** 즉 이식 작업의 본질은 "설계서를 코드에 맞추는 것"이 아니라 **"다른 저장소에 있는 v4
구현 17개 파일을 여기로 가져오는 것"** 이고, 그때 부딪히는 실제 장벽은 3개다 — ①그래프
관측 어댑터, ②전국 구역 그래프 캐시, ③GPU 인프라.

---

## 1. 먼저 확인해야 할 전제 — 설계서와 이 저장소는 서로 다른 코드베이스를 본다

| 설계서 주장 | 이 저장소 실제 |
|---|---|
| v4 파일 17개 · 4,742 lines 구현 완료 | **존재하지 않음.** `v4/`, `sionna/`, `federated/`, `inference/`, `scripts/train_v4.py` 전부 없음 |
| `ppo_trainer.py` · `dqn_trainer.py` · `gym_wrapper.py` · `agent_registry.py` | **존재하지 않음.** `backend/app/services/rl/`에는 `v2x_routing_env.py`(763L), `rl_trainer.py`(273L), `__init__.py` 3개뿐 |
| MaskablePPO / Double DQN / Curriculum 3단계 "구현 완료" | **학습 코드 0줄.** `rl_trainer.py`는 random·greedy·coverage 휴리스틱 에피소드 러너다 |
| REST API `/api/rl/v4/*` 5개 + `/api/rl/train/status` SSE | **`/api/rl/episode` 1개만 존재** (main.py:6294) |
| `south-korea.osm.pbf` 283MB · `v4_graph_cache/` | **없음.** `data/raw/`에는 buildings·standard_link·traffic_survey만 |
| torch · sb3-contrib · torch_geometric · flwr · learn2learn | **requirements.txt·venv 어디에도 없음.** venv의 수치 패키지는 `numpy` 단독 |

이 불일치는 오류가 아니라 **역할 분담의 결과**로 보인다. `traffic_demand_progress.md` §1에
이렇게 적혀 있다:

> `(RL은 7단계 이후 최종 층으로. 지금은 손대지 않음 — 친구가 재설계 중)`

즉 설계서 3종은 **분리된 브랜치/저장소에서 병렬 개발 중인 v4**를 기술한 것이고, 이 저장소는
그 아래 깔릴 **시뮬레이션·물리 채널·배치 최적화 층**이다. 따라서 이 검토의 질문은
"설계서가 맞느냐"가 아니라 **"v4를 이 층 위에 얹을 수 있느냐"** 가 된다.

**확인 필요 (동혁님께):** v4 17개 파일이 있는 저장소/브랜치 위치. 그게 없으면 아래 B·C 항목은
"이식"이 아니라 "신규 구현"이 된다.

---

## 2. 항목별 이식 판정

### A. 그대로 이식 가능 — 인터페이스가 이미 맞다 (5건)

| # | 설계서 요구 | 이 저장소의 대응 | 근거 |
|---|---|---|---|
| A1 | 상태 37차원 · `Discrete(5)` · action masking | **완전 일치.** `STATE_DIM = 12 + 5×5 = 37`, `valid_actions()`가 마스크 훅 제공, docstring에 sb3 `V2XGymEnv` 래퍼 예시까지 포함 | `v2x_routing_env.py:87-90, 569-576, 45-75` |
| A2 | 보상 10요소 (진척·거리·지연·부하·핸드오버·차폐·단절·미래위험·도착·타임아웃) | **완전 일치.** `RewardWeights` 10필드, `calculate_reward()`가 10항 합산 | `v2x_routing_env.py:163-192, 497-550` |
| A3 | Domain Randomization: 매 에피소드 구역·BS·RSU·O/D·밀도 랜덤화 | **환경 수정 불필요.** `V2XRoutingEnv.__init__`이 graph·road_nodes·bs_nodes·origin_id·dest_id·max_steps를 전부 생성자 인자로 받는다 → 에피소드마다 새 인스턴스만 만들면 랜덤화 끝 | `v2x_routing_env.py:250-263` |
| A4 | RSU를 특수 타입으로 처리 | **물리 모델 레벨에서 이미 충족.** `network_nodes`에 `type: roadside_unit`이 실재하고, `formula_v31`이 `node_type`별로 안테나 높이(H_RSU=5.0m)·송신전력차(ΔP=20dB)·커버리지 반경을 분기한다 | `main.py:1172-1183`, `formula_v31.py:40-60, 149, 252` |
| A5 | N=30 시드 통계 프로토콜 | **절반 완성.** `run_episodes()`가 `mean/std`를 이미 반환. Wilcoxon + CI95만 추가하면 됨(scipy 필요) | `rl_trainer.py:246-272` |

> **A3이 특히 중요하다.** 설계서 v4의 핵심 주장은 "v3는 고정 시나리오에 묶여 배포 불가"인데,
> 이 저장소의 환경은 **애초에 고정 시나리오에 묶여 있지 않다.** 그래프·BS·O/D가 모두 주입식이라
> Domain Randomization은 환경 바깥의 래퍼 한 겹으로 해결된다. 설계서가 지적한 "v3의 구멍" 중
> 상당 부분이 이 저장소에는 해당되지 않는다.

**또 하나 — 설계서가 금지한 "절대 좌표"는 이미 없다.** state[0]·state[1]이 `lat_norm`/`lng_norm`이라
이름만 보면 절대 좌표 같지만, 실제로는 **출발–목적지 bbox 기준 상대 정규화**다
(`v2x_routing_env.py:457-464`). 설계서 §01의 우려는 이 코드에는 적용되지 않는다.

---

### B. 조건부 이식 — 신규 어댑터가 필요하나 기존 코드는 안 건드려도 된다 (3건)

#### B1. Universal GNN Policy (GATv2) — **가장 큰 작업, 그러나 무수정 이식 가능**

- **문제:** 현재 관측은 **평면 37차원 벡터**다. 설계서는 **PyG `Data` 객체(가변 크기 그래프)** 를 요구한다. `get_state()`는 벡터만 반환하므로 GNN에 그대로 못 넣는다.
- **해결:** 기존 파일을 고칠 필요 없이 **읽기 전용 어댑터**로 해결된다. 필요한 재료가 환경 안에 전부 노출돼 있다.
  - `env._graph["adjacency"]` — 4-hop 서브그래프 추출용 인접 리스트
  - `env._road_nodes` — 노드 좌표
  - `env._cached_edge_costs` — 엣지별 `latency_ms`·`load_ratio`·`loss_db`·`within_coverage` (설계서 엣지 피처 4종과 거의 1:1)
  - `env._bs_grid` (SpatialGrid) — BS/RSU 근접 조회 O(1)
- **⚠️ 진짜 갭 하나:** 설계서 노드 피처는 `node_type: 0=도로, 1=BS, 2=RSU, 3=출발, 4=도착`으로 **BS/RSU를 그래프 노드로 넣으라**고 한다. 그런데 이 저장소에서 BS/RSU는 그래프와 **분리된 별도 리스트**(lat/lng만 있고 도로망에 연결 안 됨)다. → 어댑터에서 `SpatialGrid.within()`으로 커버리지 반경 내 도로 노드에 BS/RSU를 잇는 **가상 엣지 생성 로직**을 새로 써야 한다. 난이도 자체는 낮지만 설계서에 없는 결정 사항(연결 반경 기준, 엣지 방향성)이 생긴다.
- **판정:** 이식 가능. 신규 파일 1개(`gnn_obs_adapter.py` 성격) + 위 결정 1건.

#### B2. 전국 500구역 학습 데이터 — **자산은 있으나 규모가 다르다**

| 설계서 전제 | 실제 |
|---|---|
| `regions.db` 1,762 유효 / train 1,601 / holdout 161 | `regions.db` **2,367행** 실재. admin_level 분포 = sido 19 / sigungu 243 / eupmyeon 39 / dong 2,066. **7+8 = 2,105** → 보고서의 1,762는 면적 필터 후 값으로 보이나 **필터 코드가 이 저장소에 없어 재현 불가** |
| 500구역 PyG 그래프 캐시 (PBF에서 추출, ~2h) | **PBF 없음.** 대신 `backend/networks/`에 **SUMO net.xml 79개 + osm 79개 (755MB)** 가 이미 빌드돼 있음. `regions.db`의 `network_file`이 채워진 구역은 **2개뿐** |
| Sionna 채널맵 500구역 × 250GB | **미생성.** GPU 필요 |
- **판정:** 500구역은 신규 파이프라인 필요(단 `osmnx>=2.1.0`이 requirements에 이미 있어 PBF 없이도 구역별 다운로드 경로는 열려 있다). **다만 기존 79구역 자산만으로 축소판 실험(train 60 / holdout 19)은 지금 당장 가능하고, "zero-shot 일반화" 주장의 1차 검증으로는 충분하다.**

#### B3. 학습 처리량 — **이미 해법이 저장소 안에 있다**

- **문제:** 설계서는 30M 에피소드 × 20스텝 = **6억 스텝**을 요구한다. 그런데 건물 차폐를 켜면(`buildings_gdf` 전달) `_find_best_bs_full()`이 **BS 개수 × 후보 엣지 수**만큼 `analyze_vehicle_to_node`를 호출하는데, 이 함수는 실측 **호출당 10.43 ms**다. 6억 스텝에 그대로 쓰면 계산이 불가능하다.
- **해법:** `placement/a_seg_cache.py`가 **정확히 이 문제를 위해 이미 만들어져 있다.** (수요점 × 후보) 쌍의 A_seg를 사전계산·캐시하고, GeoDataFrame 생성 우회(91배)·STRtree·거리 컷오프로 "면적의 제곱 → 면적에 선형"을 달성했다고 문서화돼 있다.
- **판정:** Sionna 채널맵이 없어도 **A_seg 캐시 재사용으로 학습 처리량을 확보할 수 있다.** 설계서가 Sionna에 250GB·50 GPU-hour를 배정한 것에 대한 저비용 대안이 이미 손 안에 있다.

---

### C. 이식 전 재검토가 필요한 항목 (4건)

| # | 항목 | 문제 |
|---|---|---|
| C1 | **A100 720시간 일정 전체** | 로컬에 GPU 없음. venv에 torch조차 없음. 설계서 4주 일정(MAML 60h + Sionna 6h + FL 8h)은 **인프라 확보가 선행 조건**이며, 미확보 시 일정 전체가 무효 |
| C2 | **설계서 3종 간 내부 불일치** | v4 설계서는 "전국 2,367구역 중 500개 학습 / 50개 홀드아웃", 구현현황보고서는 "1,762 유효 / 1,601 train / 161 holdout". **구역 수·train·holdout이 세 문서에서 전부 다르다.** 논문 수치로 쓰기 전에 하나로 확정 필요 |
| C3 | **"수정완료"라고 적힌 버그 4건이 이 저장소에는 미반영** | 보고서가 "수정완료"로 표시한 `route_cost_function.py`·`v2x_routing_env.py`의 물리 채널 모델 교체가 **여기엔 없다.** 실제로 `v2x_routing_env._estimate_latency()`는 여전히 `4.0 + dist_pen + cong + edge_lat` (line 736-744). ⚠️ **다만 이건 심각하지 않다** — 이 단순식은 상태 피처 `predicted_latency_ms` 계산에만 쓰이고, **실제 엣지 비용의 latency는 `compute_edge_network_cost`가 latency registry(=formula_v31)로 덮어쓴다**(`route_cost_function.py:441-458`). 즉 보상에 들어가는 지연은 이미 물리 모델이다 |
| C4 | **Meta-RL(MAML) / Federated RL** | 기존 코드 의존관계 없음 = 이식 리스크도 없지만, **Universal GNN이 선행되어야 의미가 있다.** `learn2learn`·`flwr` 미설치. 우선순위 최하위 |

---

## 3. 설계서가 몰랐던 이 저장소의 강점 3가지

설계서는 이 층을 "v3 = 고정 시나리오, 단순 공식"으로 상정하는데, 실제 코드는 그보다 앞서 있다.

1. **물리 채널 모델이 이미 논문급이다.** `formula_v31.py`는 3GPP UMa 기반 PL → SINR → MCS → SE → `L_total = L_base + L_transmission + L_queue`(M/M/1)를 구현하고, SINR < −6dB outage(1000ms)까지 처리한다. **Sionna가 없어도 폴백이 충분히 강해서, C5(Sionna 기여)를 빼도 논문이 성립한다.**
2. **RSU 이종 확장이 물리적으로 유도돼 있다.** `placement_optimization_design_v2.md` §3에 따라 RSU 커버리지를 상수가 아니라 **안테나 높이 h=5m + ΔP=20dB 하나로 유도**한다(4G 312 / 5G 156 / 6G 62m). 설계서의 "RSU=특수 노드 타입" 요구보다 정교하다.
3. **베이스라인과 UI가 이미 배선돼 있다.** random/greedy/coverage 3종 + 프론트 비교 UI(`tab-simulation.jsx:969-971, 2686`)가 동작 중이라, 설계서 C1의 "Random/Greedy vs GNN" 비교 대상이 **오늘 바로 확보된다.**

---

## 4. 권장 이식 순서

전제: **기존 파일 무수정.** 아래는 전부 신규 파일 또는 래퍼로 가능하다.

```
0) v4 저장소 위치 확인 ─ 없으면 아래는 "이식"이 아니라 "신규 구현"
   └ 동시에 C2(구역 수 3종 불일치) 확정

1) [즉시, GPU 불필요] 학습 스택 설치 + 랜덤화 래퍼
   requirements에 torch·sb3-contrib 추가 → V2XRoutingEnv를 gymnasium으로 감싸고
   (docstring 예시가 이미 있음) reset()마다 구역·BS·O/D 재샘플링
   → MLP-PPO 베이스라인 확보. 설계서 "MLP-PPO vs GNN-PPO" 비교의 좌변

2) [즉시, GPU 불필요] 79구역 축소판 홀드아웃 세트 구성
   backend/networks/ 기존 net.xml 79개 → train 60 / holdout 19
   → zero-shot 일반화 1차 검증. 500구역 없이도 가설 검정 가능

3) [1~2주] GNN 관측 어댑터 (B1)
   서브그래프 추출 + BS/RSU 가상 엣지 연결 결정
   ⚠️ 여기서 설계서에 없는 설계 결정 1건 발생 (연결 반경 기준)

4) [처리량 확보] A_seg 캐시를 RL 경로에 재사용 (B3)
   Sionna 250GB 계획의 저비용 대체

5) [GPU 확보 후] MAML → Federated
```

**1)과 2)는 GPU·A100·Sionna 없이 오늘 시작 가능하고, 설계서 핵심 가설(Domain Randomization이
고정 시나리오보다 일반화가 좋다 / zero-shot이 성립한다)을 검증하기에 충분하다.** 720시간 A100
계획은 그 결과가 나온 뒤에 확정하는 것이 안전하다.

---

## 5. 최종 판정

| 구분 | 건수 | 비고 |
|---|---|---|
| 그대로 이식 가능 | 5 | 상태·행동·보상 계약, DR 주입 구조, RSU 물리 모델, 베이스라인 |
| 조건부 (신규 어댑터, 기존 코드 무수정) | 3 | GNN 관측 어댑터 / 구역 그래프 캐시 / 처리량 |
| 재검토 필요 | 4 | GPU 인프라 / 문서 간 수치 불일치 / 미반영 수정 / MAML·FL 우선순위 |

**설계서는 이 프로젝트에 적용 가능하다.** 환경 계약이 이미 맞아떨어져 있어서 "설계서를 위해
코드를 뜯어고쳐야 하는" 항목은 **하나도 없다.** 실제 장벽은 설계 정합성이 아니라 **①v4 구현체의
소재 확인, ②GPU 인프라, ③문서 간 수치 확정** 세 가지 행정·인프라 문제다.
