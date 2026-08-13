# 경로비용·통신품질 반영 — 인수인계

> **작성일** 2026-08-08
> **대상** 클로드 코드에서 이어서 작업할 사람
> **범위** `backend/main.py`, `backend/app/services/routing/route_cost_function.py`,
> `backend/app/services/buildings/building_obstruction_analyzer.py`

---

## 0. 한 줄 요약

가중치 8개 중 **건물 차폐(`w_blockage`)와 커버리지 이탈(`w_coverage_risk`)이 경로 선택에 전혀 반영되지 않고 있었다.** 다 고른 뒤 채점할 때만 켜졌다. 이번 작업으로 둘 다 탐색 중에 반영되도록 고쳤고, 그러기 위해 필요한 성능 문제를 해결했다. 남은 일은 §3에 있다.

---

## 1. 배경 — 왜 이게 문제였나

### 1-1. 비용 함수의 구조

`compute_edge_network_cost()`가 엣지마다 정규화된 점수를 낸다.

```
total = w_distance·c_dist + w_time·c_time + w_latency·c_latency
      + w_load·c_load + w_handover·c_handover + w_blockage·c_blockage
```

`network_weighted_sumo_path()`(= `route_algorithm="network_aware"`)는 이 값을 간선 가중치로 쓰는 Dijkstra다.

### 1-2. 두 항이 죽어 있었다

**건물 차폐** — 탐색은 `skip_buildings=True`로 호출했고, 그 경로로 가면 `_find_best_bs_light()`가 `loss_db`를 **무조건 0.0**으로 돌려준다. 따라서 `c_blockage = 0/30 = 0`. `w_blockage`를 100으로 올려도 경로가 안 바뀐다.

**커버리지 이탈** — `coverage_risk`가 `evaluate_path()`에서 **경로 레벨 비율**(`커버 밖 엣지 수 / 전체 엣지 수`)로 계산돼 완성된 경로에만 더해졌다.

비율을 쓴 게 실수가 아니라 **구조적 제약**이었다. 비율은 분모가 경로 전체에 의존해서 **더할 수가 없다.** 탐색 중에는 앞으로 몇 구간을 더 갈지 모르니 "지금까지 비율"을 알 수 없다. Dijkstra는 간선 비용을 누적하는 알고리즘이라 비율 형태의 목적함수를 다룰 수 없다.

### 1-3. 왜 그냥 켤 수 없었나

`skip_buildings=False`로 바꾸면 `_find_best_bs_full()`이 불리고, 그게 **기지국 수만큼** `analyze_vehicle_to_node()`를 호출한다. 그 함수가 호출마다 이걸 다 했다.

```python
line_gdf = gpd.GeoDataFrame({"geometry":[line]}, crs="EPSG:4326").to_crs(3857)  # ①
search   = buildings_gdf[buildings_gdf.geometry.intersects(line)]              # ②
search_3857 = search.to_crs(3857)                                              # ③
```

① 선 하나 때문에 GeoDataFrame 생성 + 좌표계 변환 ② **공간 인덱스 없이 전 건물 스캔** ③ 걸린 건물 재변환.

**측정(샌드박스, 건물 2000개·기지국 54개):**

| | 엣지 1개 | 762엣지 | 14,248엣지 |
|---|---:|---:|---:|
| 당시 코드 | 336 ms | 4분 16초 | 1시간 20분 |

`backend/networks/`의 실제 net.xml이 762 ~ 14,248 엣지 범위다. 경로 한 번 찾는 데 이만큼 걸리므로 켤 수가 없었다.

---

## 2. 이번에 한 것 (완료)

### 2-1. 커버리지를 엣지 단위 "커버리지 밖 주행 km"로 전환

**파일** `route_cost_function.py`

```python
# compute_edge_network_cost() 안
c_coverage = 0.0 if within_cov else c_dist      # c_dist = km 단위
total += weights.w_coverage_risk * c_coverage
```

```python
# evaluate_path() — 경로 레벨 가산 제거 (이중계산 방지)
total_cost = sum(r.total_cost for r in edge_results)
```

**왜 km 비례인가.** 엣지당 상수로 붙이면 SUMO가 도로를 교차로마다 자르는 방식에 결과가 끌려간다. 같은 2 km라도 교차로 많은 도심길은 40구간, 간선도로는 5구간이라 벌점이 8배 차이난다. 통신 품질과 무관한 이유로 경로가 갈린다. km 비례로 하면 "음영지역을 몇 km 달리는가"가 되어 분할 방식에 불변이고 물리적 의미도 맞다.

**가중치 해석이 바뀌었다.** `w_coverage_risk`는 이제 **km당** 가중치이고 `w_distance`와 같은 단위다. 기본값 2.5 → 커버리지 밖 1 km = 일반 주행 3.5 km와 같은 비용.

**보존한 것.** `PathCostResult.coverage_risk`(비율)는 리포트·엑스포트가 쓰므로 **지표로는 그대로 보고**된다. 총비용에만 안 들어간다.

**⚠ 총비용의 절대 스케일이 바뀐다.** 이전 결과와 숫자를 직접 비교하면 안 된다. 알고리즘 간 비교는 전부 같은 함수를 쓰므로 유효하다.

**검증** — `/tmp/cov_test.py` 상당의 테스트로 확인:

- 커버 안 → `coverage_cost = 0`, 커버 밖 500 m → `0.5`
- 2 km를 1엣지로 두든 500 m 4엣지로 쪼개든 합이 `2.0`으로 동일
- `evaluate_path().total_cost == Σ edge.total_cost` (이중계산 없음)
- `w_coverage_risk` 2.5→10.0 시 커버 밖 엣지만 비싸지고 커버 안 엣지는 불변

### 2-2. 건물 차폐 분석에 STRtree 공간 인덱스 + 좌표 사전변환

**파일** `building_obstruction_analyzer.py`

`_prepare_buildings()`를 추가해 건물 집합당 **1회만** 3857 지오메트리와 STRtree를 만들고 재사용한다. `reset_buildings_index()`로 무효화.

부수적으로 호출당 pandas 왕복 두 곳도 제거했다 — `max_height`를 리스트에서 계산하고(`_height_or_zero()`가 `fillna(0)` 의미를 재현), `highlighted_buildings`는 상위 5행만 꺼낸다.

**측정 (`/tmp/equiv_test.py`):**

| | 엣지 1개 (BS 30회) | 배수 |
|---|---:|---:|
| 이전 | 194 ~ 252 ms | — |
| 이후 | 27 ~ 39 ms | **6.5 ~ 7.1배** |

**결과 동일성: 3,600건 비교, 불일치 0건.** 비교 항목은 `distance_m`, `intersected_building_count`, `max_building_height_m`, `estimated_penetration_loss_db`, `latency_penalty_ms`, `stability_score`, `confidence`, 하이라이트 건물 id·높이·꼭짓점 수 전부. 높이가 `0`·`None`·정상값인 경우를 섞어 경계 조건까지 포함했다.

> **📌 여기서 한 번 틀렸다가 고친 부분 — 같은 실수 반복 주의**
>
> 처음엔 후보 판정을 3857에서 했더니 3,600건 중 2건이 어긋났다. 원인은 건물이 시선에서 **1.6 mm** 떨어져 스치는 경계 사례였다(4326에서는 안 닿고 3857에서는 닿음). Mercator의 y 비선형 항 때문에 두 좌표계의 직선이 완전히 겹치지 않아서다.
>
> 물리적으로는 의미 없는 차이지만 **건물 1채 = 12 dB**라 결과가 흔들린다. 그래서 후보 판정용 STRtree는 **4326으로** 만들고, 3857 지오메트리는 3D LOS 판정(`_is_blocked_3d`)에만 쓴다. 이 구조를 바꾸지 말 것.

### 2-3. 탐색 중 차폐 활성화 배선

**파일** `main.py`

세 함수에 `buildings_gdf=None` 인자를 추가했다. 주면 `skip_buildings=False`로 돌고, 안 주면 예전 동작 그대로다.

- `network_weighted_sumo_path()`
- `lookahead_weighted_sumo_path()`
- `best_of_k_path()`

**닭-달걀 문제 해결.** `_state["route_buildings"]`는 경로가 정해진 **뒤에** 그 경로 bbox로 불러온다. 탐색 중에는 아직 없다. 그래서 알고리즘 디스패치 직전에 출발·도착과 모든 기지국을 덮는 bbox로 미리 불러온다.

```python
SEARCH_BUILDING_PADDING_DEG = 0.005   # ≈550m. load_route_buildings 기본값(0.0015)보다 넓다
```

넓게 잡는 이유: Dijkstra는 출발–도착 직선 회랑 바깥까지 펼친다. **건물이 없는 구간은 차폐 0으로 계산되므로, bbox를 좁게 잡으면 "건물이 없어서 좋은 길"로 잘못 보여 경로가 그쪽으로 쏠린다.** 조회 실패·건물 0개면 `None`으로 떨어져 예전 동작으로 폴백한다.

**실측 (762엣지 net.xml, 건물 1500개, BS 30개):**

| 탐색이 평가한 엣지 | 차폐 OFF | 차폐 ON | 경로 |
|---:|---:|---:|---|
| 70 | 0.01초 | 7.80초 | 동일 |
| 288 | 0.04초 | 8.29초 | **변경됨** |
| 243 | 0.06초 | 11.15초 | **변경됨** |

**3건 중 2건에서 경로가 실제로 바뀌었다** — 차폐가 경로 선택에 영향을 준다는 뜻이고, 이번 작업의 목표가 달성됐다는 증거다.

---

## 3. 남은 작업 — 우선순위 순

### 🔴 3-1. BS 후보를 상위 K개로 제한 (성능, 가장 급함)

**왜 급한가.** §2-3 측정에서 엣지당 약 46 ms(BS 30개)다. 실제 시스템은 BS 54개이고 큰 네트워크(14,248엣지)에서 탐색이 수천 엣지를 펼치면 **경로 하나에 몇 분**이 걸린다. 762엣지에서 10초는 감당되지만 큰 네트워크는 안 된다.

**할 일.** `_find_best_bs_full()`이 모든 노드를 순회한다(`route_cost_function.py`). haversine 거리로 정렬해 가까운 K개만 `analyze_vehicle_to_node()`에 넣는다. K는 상수 말고 설정값으로 노출할 것.

**측정된 효과** (합성 벤치, 건물 2000개): 54개 → 6개로 줄이면 42 ms → 4.2 ms, **10배**.

**⚠ 이건 근사다.** `rsrp_max`의 점수는 `path_loss(d) + loss_db`인데, **건물 3채에 가린 가까운 BS(+36 dB)가 안 가린 먼 BS에 질 수 있다.** 거리순 상위 K에 진짜 최적 BS가 안 들어올 수 있다는 뜻이다.

→ K=6은 빠듯하다. **K=10 정도로 시작하고, 몇 개 경로에서 전체 순회 결과와 선택된 BS 시퀀스를 대조해 검증할 것.** 검증 없이 K를 줄이지 말 것.

### 🔴 3-2. `_find_best_bs_full`의 거리가 26% 과대평가됨

**파일** `building_obstruction_analyzer.py` — `analyze_vehicle_to_node()`

```python
distance_m = hypot(_nx - _vx, _ny - _vy)   # EPSG:3857 길이
```

**EPSG:3857(Web Mercator) 길이는 실제 거리가 아니다.** 위도 37.5°에서 `1/cos(37.5°) ≈ 1.26`배 부풀려진다. 400 m가 504 m로 계산된다.

이 값이 `path_loss()`와 `_L_total()`에 그대로 들어가므로 **지연이 전부 과대평가된다.**

**더 나쁜 건 불일치다.** `_find_best_bs_light()`는 `_haversine_m()`(정확)을 쓴다. 즉 **같은 (지점, 기지국) 쌍인데 차폐를 켜냐 끄냐에 따라 거리가 26% 달라진다.** 지금은 탐색에서 차폐를 켰으므로 이 불일치가 활성 상태다.

**이번에 안 고친 이유** — 이번 변경을 "속도만 개선, 결과 불변"으로 유지해 검증을 깨끗하게 하려고 일부러 보존했다. 코드에 `⚠` 주석으로 표시해 뒀다.

**할 일.** `_haversine_m()`으로 교체하고, 지연·경로손실 회귀 결과를 다시 뽑을 것. 3D LOS 판정용 3857 지오메트리는 그대로 두면 된다(거기선 상대적 투영만 쓴다).

### 🟡 3-3. 차폐 캐시가 무력화돼 있음

**파일** `building_obstruction_analyzer.py`

```python
def _cache_obstruction(key, result):
    if len(_OBSTRUCTION_CACHE) >= _OBSTRUCTION_CACHE_MAX:   # 20,000
        _OBSTRUCTION_CACHE.clear()      # ← 통째로 삭제
```

14,248엣지 × BS 10개 = 142,480 항목이 필요한데 한도가 20,000이다. **7번 꽉 차고 7번 전멸한다.** LRU가 아니라 전체 삭제라 방금 넣은 것도 같이 날아간다.

**할 일.** `collections.OrderedDict` + `move_to_end()`로 LRU 전환, 또는 키를 해시 가능하게 정리해 `functools.lru_cache` 사용. 한도도 재검토(§3-1로 항목 수가 줄면 20,000이 맞을 수도 있다).

### 🟡 3-4. A\* 휴리스틱 단위 (통신비용 A\*를 만들 때 반드시)

**파일** `main.py` — `astar_sumo_path()`

**현재 상태를 정확히 알 것:**

| | 실제 비용 |
|---|---|
| `route_algorithm="dijkstra"` = `traci.simulation.findRoute()` | **통행시간** (SUMO 기본 routing mode) |
| `route_algorithm="astar"` = `astar_sumo_path()` | **거리** (`getLength()`, 미터) |

`astar_sumo_path()`의 docstring이 *"Cost is distance-only — the same metric as baseline Dijkstra"*라고 적혀 있는데 **틀렸다.** 둘은 서로 다른 비용을 쓰므로 결과가 다르다. 주석을 고칠 것.

**통신비용을 A\*에 넣을 때의 함정.** `compute_edge_network_cost()`는 미터가 아니라 **정규화 점수**를 돌려준다(엣지 200 m당 대략 1~2점). 그런데 현재 `h()`는 haversine **미터**를 돌려준다(목적지 2 km면 2000). 힌트가 실제 남은 비용보다 200배 커져 **admissible이 깨지고 A\*가 greedy 탐색으로 퇴화한다** — 통신 비용을 넣어놓고 무시하게 된다.

**권장 수정 (거리 + 시간 하한):**

```python
v_max = max(e.getSpeed() for e in net.getEdges())   # 1회만 계산

def h(edge_id):
    d_m = haversine_m(lat, lon, end_lat, end_lon)
    h_dist = weights.w_distance * (d_m / 1000.0 / norm_scales.distance_km)
    h_time = weights.w_time * ((d_m / v_max) / 60.0 / norm_scales.time_min)
    return h_dist + h_time
```

실제 통행시간은 `직선거리 / 최고속도`보다 짧을 수 없으므로 admissible이 유지된다. 나머지 항(지연·부하·차폐·커버리지)은 전부 0 이상이라 h가 하한이라는 성질이 깨지지 않는다.

**절대 하지 말 것:** 힌트에 통신비용의 **평균**이나 추정치를 넣기. 평균은 하한이 아니라서 지름길을 놓친다. 최소값만 쓸 것.

**같이 고칠 것:** `OUT_OF_AREA_PENALTY_M = 1e5`(미터)도 점수 단위로 환산해야 한다. 지금 그대로면 10만 점이 붙는데, 정상 경로가 10점대라 동작은 하지만 숫자가 의미를 잃는다.

**참고:** A\*의 속도 이점은 줄어든다. 기본 가중치에서 통신 항이 비용의 절반 이상인데 힌트는 그걸 모르므로 탐색량이 Dijkstra에 가까워진다. 정상이다. 더 조이려면 "네트워크 전체에서 통신이 가장 좋은 구간의 km당 비용 × 남은 km"를 더할 수 있다(사전 계산 1회 필요).

### 🟡 3-5. 차폐 비용도 km 비례화

커버리지는 §2-1에서 km 비례로 바꿨지만 **`c_blockage`, `c_load`, `c_latency`는 여전히 엣지당 상수**다. §2-1에서 설명한 "엣지 길이 편차" 문제가 이 항들에는 그대로 남아 있다.

```python
km = distance_m / 1000.0
total = w_distance*c_dist + w_time*c_time \
      + km * (w_latency*c_latency + w_load*c_load + w_blockage*c_blockage) \
      + w_coverage_risk*c_coverage        # 이미 km 비례
# 핸드오버는 사건이므로 길이와 무관한 게 맞다 — 그대로 둘 것
```

**주의:** 총비용 스케일이 또 바뀐다. §3-4의 A\* 휴리스틱 작업과 **같이** 하는 게 좋다. 안 그러면 스케일을 두 번 재조정해야 한다.

### 🟢 3-6. 가중치 UI 노출

사용자가 `CostWeights`를 조절할 수 있게 하는 건 보류 상태다. 참고할 것:

- **`NormScales`와 `CostWeights`를 분리해서 노출할 것.** 전자는 "50 ms를 1.0으로 볼 것인가", 후자는 "그 1.0을 몇 배로 칠 것인가". 설계 의도가 그렇다.
- **기본값이 통신 쪽으로 많이 기울어 있다.** 1 km 주행 = 1.0점, 지연 50 ms = 3.0점. 즉 지연 50 ms짜리 구간 하나를 피하려고 3 km를 우회한다. UI에 이런 환산 예시를 같이 보여주면 사용자가 감을 잡기 쉽다.
- **음수 가중치를 절대 허용하지 말 것.** Dijkstra는 음수 간선에서 깨진다("가까운 곳부터 확정" 전제가 무너진다). "통신 좋으면 보너스"는 나쁜 쪽 벌점으로만 표현할 것. 지금 `sanitize_algorithm_selection` 근처에서 `max(0.0, ...)`로 막고 있으니 그 방어를 유지할 것.

### 🟢 3-7. mock 모드 미적용

`network_weighted_mock_path()`, `lookahead_weighted_mock_path()`는 여전히 `buildings_gdf=None`·`skip_buildings=True`다. SUMO 모드만 배선했다. mock은 데모 폴백 경로라 우선순위를 낮췄다. 일관성을 원하면 SUMO 쪽과 같은 방식으로 인자를 추가하면 된다.

### 🟢 3-8. RL 라우팅이 SUMO 모드에서 실행되지 않음 (별건)

`main.py` 라우팅 디스패치:

```python
_sumo_routing_mode = "baseline_dijkstra"           # 초기화
... astar / k_shortest_path / network_aware / lookahead 분기 ...
if _sumo_routing_mode == "rl_routing" and RL_AVAILABLE:   # ← 항상 False
```

`_sumo_routing_mode`는 위 분기에서 `"rl_routing"`이 되는 경로가 없다. 조건이 `route_algorithm == "rl_routing"`이어야 한다. 지금은 RL을 선택해도 조용히 baseline Dijkstra가 나간다.

RL이 아직 미완성이라 보류하기로 한 항목이다. RL 작업을 재개할 때 **가장 먼저** 고칠 것 — 안 그러면 에이전트를 학습시켜도 시뮬에 반영이 안 된다.

---

## 4. 회귀 검증 방법

### 4-1. 차폐 분석 결과 동일성

`git show <이번 커밋 이전>:backend/app/services/buildings/building_obstruction_analyzer.py`로 이전 버전을 꺼내 별도 모듈로 로드한 뒤, 같은 입력에 대해 두 구현의 `BuildingObstructionResult` 전 필드를 비교한다. 높이에 `0`·`None`·정상값을 섞어야 경계 조건이 잡힌다. **불일치 0건이 기준이다.**

### 4-2. 커버리지 비용

- 커버 밖 500 m 엣지 → `components["coverage_cost"] == 0.5`
- 2 km를 1엣지 / 500 m 4엣지로 나눠도 커버리지 항 합이 같을 것
- `evaluate_path().total_cost == Σ edge.total_cost` (이중계산 없음)
- `coverage_risk` 비율이 지표로는 계속 나올 것

### 4-3. 샌드박스 환경 메모

`backend/.venv`는 윈도우용이라 리눅스에서 못 쓴다. 검증 스크립트를 돌리려면:

```bash
pip install geopandas sqlalchemy sumolib --break-system-packages
# psycopg2는 스텁으로 대체 (app.db가 임포트 체인에 걸린다)
PYTHONPATH=/tmp/stub:. python3 <script>
```

---

## 5. 실측치 요약 (샌드박스 기준, 절대값은 환경에 따라 다름)

| 항목 | 이전 | 이후 |
|---|---:|---:|
| 차폐 분석 1엣지 (BS 30회, 건물 1200개) | 252 ms | 39 ms |
| 차폐 분석 1엣지 (BS 54회, 건물 2000개) | 336 ms | 42 ms |
| ↑ + BS 상위 6개 적용 시 (**§3-1 미적용**) | — | 4.2 ms |
| 경로탐색 1회 (762엣지, BS 30, 건물 1500) — 차폐 OFF | — | 0.01~0.06초 |
| 경로탐색 1회 (같은 조건) — 차폐 ON | 불가능 | 8~11초 |

§3-1을 적용하면 마지막 줄이 2초대로 내려갈 것으로 예상된다(BS 30→6, 5배).
