# 교통 수요 생성 파이프라인 — 진행 현황 & 다음 단계 (인수인계)

> **이 문서의 목적:** 새 채팅으로 이관 시 매끄러운 연속을 위한 상태 스냅샷.
> 이론·수식은 `traffic_demand_design_v2.md`(v2 설계문서, 유지됨)를 참조하고,
> 이 문서는 **무엇을 결정했고, 무엇을 만들었고, 다음에 무엇을 하는지**를 담는다.
> 통신 지연은 `latency_formula_v3_1.md` 참조. (v1 `radiation_model_traffic_demand.md`는 v2로 대체되어 삭제됨.)

---

## 0. 한 줄 목표

ITS 실시간 데이터가 거의 없으므로, **건물(질량) → radiation OD → SUMO 배정**으로 각 도로의
교통량을 **생성**한다. 이 생성 교통 위에서 **① 기지국(BS)/RSU 배치**와 **② 경로 라우팅
(다익스트라·k-shortest·나중에 RL)**을 평가한다.

핵심 원칙: **총량과 분포를 분리.** 분포(어디↔어디)는 radiation이, 총량(몇 대)은 `N* × 사용자
배율 n`이 담당. 우리는 절대 예측기가 아니라 **상대 배치 비교기**다(v2 §5-2).

---

## 1. 로드맵 & 현재 위치

```
1. 데이터 확보 ...................... ✅ 완료
2. 데이터 분석 (등급표 + 24h 곡선) ... ✅ 완료
3. 격자 + 건물 질량 ................. ✅ 완료 (모듈+검증)
4. radiation OD 생성 ............... ✅ 완료 (모듈+검증)
5. 배정 (OD→도로별 교통량) .......... ✅ 완료. 수요 손실 3건 전부 해결(§5-A)
6. SUMO 운영설정 + 시간프로파일 주입 . ⬜ 다음
7. 파이프라인 통합 ................. ⬜ 다음
   (RL은 7단계 이후 최종 층으로. 지금은 손대지 않음 — 친구가 재설계 중)
```

**지금 위치: 5단계 완료, 최종 생존율 96.2%(영등포)·100.6%(안양·의왕).** 45.4%에서 새던
수요 손실 3건을 전부 잡았다(커밋 `5458136`, §5-A). **다음은 6단계 — 시간대 OD 슬라이스와
동적 SUMO.** N\* 튜닝의 선결조건이던 "생존율 안정"이 확보됐으므로 이제 튜닝도 가능하다.

> ⚠️ **프론트엔드(`frontend/*.jsx`)는 임의로 수정하지 말 것.** 사용자와 의논 후 함께 진행하기로
> 합의됨(2026-07-27). 7단계 UI 정리 항목에 도달하면 멈추고 상의할 것.

---

## 2. 이번 대화에서 확정한 핵심 결정 (중요)

1. **건축물대장 API 안 씀.** F_FAC_BUILDING에 층수(`GRND_FLR`)가 이미 100% 들어있음.
   건물 데이터는 **전국 이미 보유**(아래 §4). 질량 계산에 API 호출 0회.
2. **질량 정의:** `m = 폴리곤 바닥면적(m²) × 층수(GRND_FLR)` = 연면적. 층수 누락 시 1로.
   (v2 §4의 "footprint × 층수" 그대로. 건물 부피 프록시.)
3. **교통 기준값 = 2010 TVOS 전국 상시조사.** 등급별 기준 교통량표 + 24h 프로파일 +
   **실측 K계수 0.068** (문헌 KHCM 0.09를 실측으로 대체). → KTDB AADT 다운로드는 *선택*으로 강등.
4. **λ = 0.9999 채택 (RMS 앵커).** 서울 도심 검증: λ=0 기본형은 통행거리가 셀크기(~300m)로
   붕괴(고밀도 도심 실패, v2 §6-5) → λ=0.9999에서 통행거리 중앙 ~1.5km로 현실화.
5. **격자 300m.** BS 커버(1000m)엔 OK, RSU 커버(150m)와 충돌. → **최종 배치는 격자 질량이
   아니라 "엣지별 교통량"을 사용**(격자는 OD 생성 전용). 이 분리로 RSU 충돌 무력화.
6. **두 워크플로우 (둘 다 지금 챙긴다):**
   - **배치:** 가장 붐비는 시점(피크) **스냅샷 → 전 차량 위치 → BS/RSU 배치**.
   - **타겟 주행:** **예열(warm-up, 배속으로 빠르게) → 타겟 출발 → 정체 뚫고 주행 → 통신 평가.**
7. **시간 프로파일 방식 채택 (미루지 않음).** 정상 peak = 시간프로파일의 "평평한 특수케이스"라,
   일반(시간가변) 버전을 지금 만들면 정상peak는 공짜로 얻어지고 RL이 나중에 그대로 얹힘.
   od2trips가 시간대별 OD 슬라이스를 지원하므로 추가 비용 작음.
8. **ITS·첨두/비첨두 UI는 폐기 대상.** radiation 생성 + "차량 대수(수요 배율 n)" 노브가 대체.
   (첨두/비첨두 구분은 대수 노브로 흡수됨.)
9. **차량 대수 = 시뮬레이션 창 전체의 총 통행 수(통행/창).** 곡선이 시간대별로 분배.
   동시 주행 대수는 *결과*(Little's Law).
10. **N\* 레벨 튜닝이 중요:** 시간프로파일에서 정체가 "생겼다 풀리려면" **피크가 도로 용량을
    넘어야** 함(v2 §5-1). 너무 낮으면 피크에도 안 막히고, 너무 높으면 계속 막힘.
11. **RL은 라우팅 결과 하나** (다익스트라·k-shortest와 동급). 교통 환경은 모든 라우팅에 공통.
    **지금 RL 코드는 건드리지 않는다** (친구가 처음부터 재설계 중).

---

## 3. 완료된 코드 & 검증

### 신규 모듈 (`backend/app/services/`)

| 모듈 | 역할 (v2 절) | 핵심 API |
|---|---|---|
| `geo/spatial_grid.py` | 순수파이썬 공간 해시 그리드 (scipy 없이 O(1) 최근접/반경) | `SpatialGrid(items, coords_fn, cell_size_m).nearest()/within()/add()` |
| `demand/grid_mass.py` | §3·§4 격자 존 + 건물 질량 | `build_zones(buildings=[(lat,lng,mass)], cell_size_m, ref_lat, origin_shift_m) → [Zone]`; `cell_of(lat,lng,cell,ref_lat,shift)`; `zone_stats()` |
| `demand/radiation.py` | §6 radiation OD | `radiation_od_matrix(masses, coords, total_trips, lam=0.9999) → [ODFlow(i,j,trips)]`; `od_summary()` |
| `demand/assignment.py` | §7·§8 배정 준비 | `read_net(path)`(한 번 읽어 재사용); **`net_bbox(net, margin_m=300)`** (실제 도로 범위 — 건물 조회 bbox는 반드시 이걸로); **`build_taz(net, cell, ref_lat, largest_component_only=True)`** (최대 승용차 SCC만 담음); **`component_summary(net)`** (고립 섬 진단); `write_taz_xml()`; **`write_od_o_format(..., keep_intra_zone=True)`** (o==d 살림); **`map_zones_to_taz(zones, taz, cell_size_m, max_reassign_m=900)`** (도로 없는 존 → 최근접 도로존 재배정); **`taz_mapping_summary()`** (자기셀/재배정/버림 진단) |

> `Zone`: `ix, iy, center_lat, center_lng, mass, n_buildings`. 존↔엣지 정렬은 **동일 `ref_lat`·
> `origin_shift`로 `cell_of`** 를 쓰는 게 필수(TAZ 매핑 정합).

### 실행 스크립트 (`scripts/`)

| 스크립트 | 역할 |
|---|---|
| `smoke_demand_pipeline.py` | **파이프라인 end-to-end 스모크 테스트.** 건물→존→OD→TAZ→od2trips→duarouter를 한 번에. PostGIS 함정·ASCII 스테이징 자동 처리. 회귀 확인용으로 계속 쓸 것 (§5-A) |
| `preprocess_traffic_survey.py` | 2010 TVOS → 등급별 기준교통량 + 24h 프로파일 |

### 검증 결과 (실데이터)

- **격자+질량 (춘천 도심 5×5km, F_FAC_BUILDING 6.7만동):** 셀 커버리지 98%, 질량 max/중앙
  **16.5배**(도심 핵 구조), 존당 건물 중앙 40동(§4 `s_ij` 통계 유효성 충족).
- **radiation OD (서울 도심 종로·중구, 건물 5.2만동 → 존 294):** 도착 상위5% 존이 **20%** 흡수
  (고질량 쏠림=구조성), 방향 비대칭 중앙 **0.50**(통근 방향성 자동생성), λ=0.9999→통행 ~1.5km.
- **배정 (서울 도심 실제 net, 신호 234개):** radiation OD → od2trips → duarouter → 도로별 교통량.
  상위10% 엣지가 통과량 **63%** 점유(간선 집중), **최다통과 상위10 중 7개가 OD 종점 아닌
  통과경로** → v2 §8-1 "병목=통과 간선·교차로" 실측 확인. (RSU를 교차로에 놓는 서사와 직결.)

### 브랜치 & 커밋 (`dhkchoi`, origin에 푸시됨)

**2026-07-27 세션 (최신 → 과거):**
```
5458136 수요 손실 3건 해결: 최종 생존율 45.4% → 96.2%
5571478 gitignore: 스모크 테스트 작업 디렉터리 제외
0fcbf5c 인수인계 갱신: 스모크 테스트 스크립트 리포 보존 + 진행문서 전면 갱신
e46471e 수요 손실 정책: 도로 없는 존을 최근접 도로존으로 재배정
253e5a4 진행문서 정정: 스모크 테스트 실측 결과 반영, 틀린 서술 3건 수정
50040e7 netconvert에 --tls.guess 추가 — 교차로 신호 추정 생성
```
(각 커밋 메시지에 실측 수치가 들어 있으니 `git show`로 근거 확인 가능.)

**이전 세션:**
```
ca98501 교통수요 파이프라인 진행현황 인수인계 문서 추가, 구식 v1 삭제
0046eec 배정 준비 모듈 추가: radiation OD → SUMO od2trips (§7·§8)
b4f15e1 Radiation model OD 생성 모듈 추가 (radiation OD, §6)
f7bd3f1 격자 존 타일링 + 건물 질량 모듈 추가 (radiation 재료)
1787ec1 전국 교통량 상시조사(2010 TVOS) 전처리 스크립트 추가
ac58377 기지국/RSU 배치 기능 확장: 자동(블루노이즈)·최적화(SA) 배치 추가
```
(그 외 이 세션 초반: 더블클릭 배치 버그, RSU 높이 상수화, 자동/최적화 배치 UI, 배치 후보
bbox 필터, RL환경·SA 공간인덱스 등도 포함.)

---

## 4. 데이터 인벤토리 (전부 `data/`, **gitignore**됨 — 리포엔 스크립트만)

| 데이터 | 위치 | 내용 |
|---|---|---|
| **건물 (처리됨)** | `data/processed/buildings/buildings_{코드}.parquet` + `index.json` | **전국 17개 시도, 14.4M동.** 컬럼: `geometry`(WGS84), `ground_floor`(층수 100%), `height_m`(100%), `centroid_lat/lng`, `usability_code` 등. 서울=`buildings_11`(69.7만동). CRS 4326, 면적계산은 `to_crs(5186)`. |
| **건물 (원본)** | `data/raw/buildings/F_FAC_BUILDING_{시도}_{시군구}/*.shp` | 272 시군구. 필드 `GRND_FLR, ARCHAREA, TOTALAREA, HEIGHT, USABILITY, geom`. CRS EPSG:5186. (파일명 NFD 정규화 주의: `unicodedata.normalize('NFC', name)`) |
| **교통 산출물** | `data/processed/traffic_survey/등급별_기준교통량.csv`, `시간대_프로파일.csv`, `README.md` | 등급별 기준 교통량 + 24h 곡선(전체+등급별). 재현: `scripts/preprocess_traffic_survey.py` |
| **교통 원본** | `data/raw/traffic_survey/{코드}_{지역}/교통량*.xlsx` | 2010 TVOS 16지역. 열: `jj_code, jj_kind, josa_time, car1~10`. |

`BUILDING_REPOSITORY.query_by_bbox(minlng,minlat,maxlng,maxlat)`로 건물 조회(`ground_floor` 반환).

> ⚠️ **"파일 폴백"은 실제로 없다 (2026-07-27 확인).** `postgis_available()`은
> [`db.py`](backend/app/db.py)에서 **환경변수만 검사**한다(`POSTGIS_ENABLED` + `DATABASE_URL` 존재 여부).
> 서버가 꺼져 있어도 True → `query_by_bbox`가 PostGIS 분기로 들어가고 → 조회 실패 →
> **빈 결과 반환**. parquet 폴백 코드([`building_repository.py:65`](backend/app/services/buildings/building_repository.py))에
> **도달하지 못한다.** 실제로 Postgres가 꺼진 상태에서 건물 0동이 나왔다.
> **회피법:** `POSTGIS_ENABLED=false` 환경변수로 실행하면 parquet 경로를 탄다.
> (수요 파이프라인은 parquet만 쓰면 되므로 PostGIS 상태와 무관하게 동작해야 함.)

### 단계 A 핵심 숫자 (참고)
- 등급별 기준 일교통량(대/일): 고속국도 50,919 / 시도·간선 35,123 / 기타도로 15,987 /
  일반국도 15,014 / 국가지원지방도 8,437 / 지방도 6,273.
- 24h 곡선: 아침 첨두 08~09시 6.8%, 저녁 18~19시 6.6%, 새벽 저점 03~04시 0.7%. **K=0.068**.

---

## 5. 다음 단계 상세 (6~7단계)

### 6단계 — SUMO 운영설정 + 시간 프로파일 주입

- **시간대별 OD 슬라이스:** 24h 곡선(`시간대_프로파일.csv`)의 시간대별 비율로 `total_trips`를
  나눠 od2trips O-format에 **여러 시간 구간**(`begin_h~end_h` + factor)으로. 창은 예: **07:00
  예열 → 09:00** (상승~피크). `assignment.write_od_o_format`에 슬라이스 반복.
- **동적 SUMO 실행:** duarouter(정적 자유류) 대신 **실제 마이크로시뮬레이션**으로 정체 반영.
  경로는 duarouter로 만들고 `sumo`로 굴리거나(rerouting device), 필요시 `duaIterate.py`(UE 근사).
- **신호등:** `netconvert`에 **`--tls.guess`** 추가 — ✅ **반영 완료**(`main.py:netconvert`, 커밋 `50040e7`).
  ⚠️ **단, 효과가 구역마다 극단적으로 다름 (2026-07-27 실측):**

  | 구역 | 현행 | +`--tls.guess` |
  |---|---|---|
  | 영등포 (`area-0baecbba`) | 2개 | **11개** ✅ |
  | 안양·의왕 (`area-1b5adb59`) | 8개 | **8개 (변화 없음)** ⚠️ |

  원인은 **`--tls.guess.threshold`(교차로 진입차로 속도 합, 기본 250)**. 이 값을 넘는 교차로에만
  신호가 붙는다. 안양·의왕은 임계값을 **50**까지 낮춰야 33개가 됐다(250·150·100 전부 8개).
  → **구역마다 신호 수를 먼저 확인**할 것. 정체가 안 생기면 이 임계값을 제일 먼저 의심.
  적정값은 정체 관측 수단(동적 SUMO)이 생긴 뒤 튜닝하기로 보류.
  ※ 기존에 적혀 있던 "52→**234개**"는 서울 도심 종로·중구 **별도 net 기준**이며, 위 두 구역에서는
  재현되지 않았다. 일반적으로 기대할 수 있는 수치가 아니므로 근거로 삼지 말 것.
- **teleport off:** `--time-to-teleport -1` (프로젝트 sumo 실행에 이미 있음). 정체 연구 필수.
- **예열(warm-up):** t=0 빈 도로 → 비현실적. 15~30분 예열 후 측정. **예열은 화면갱신 없이
  배속(스텝만 빠르게)**, 타겟 출발부터 실시간 재생.
- **피크 스냅샷:** 가장 붐비는 스텝에서 전 차량 위치(`libsumo getPosition`) → 엣지별/구역별
  차량 밀도 → **배치 수요**로.

### 7단계 — 파이프라인 통합

- **수요 생성 1회 + 캐시:** 구역 설정 후 `건물 로드(query_by_bbox 전체 bbox) → build_zones →
  radiation_od_matrix → build_taz/OD → od2trips → 배정 → SUMO` 를 **구역·시나리오당 1회** 실행,
  궤적 캐시("교통 1회, 평가 여러 번"). 배치 swap·타겟 트립은 캐시 재사용.
- **균일랜덤 배경차량 교체:** 현재 `main.py:_inject_bg_vehicle`(균일 무작위 OD) → 생성 교통으로.
- **배치 수요 교체:** `sa_placement.build_demand_from_graph`(균일 5.0) → 피크 스냅샷의 엣지별
  차량 밀도로. (`DemandPoint(lat,lng,vehicle_count)`는 지리 기반이라 엣지 중점+밀도로 바로 연결.)
- **UI 정리:** ITS 동기화·첨두/비첨두 셀렉터 제거. **"수요 배율 n(차량 대수=통행/창)"** 노브 추가.
- **타겟 예열 배속:** 위 6단계 예열을 프론트/시뮬 루프에 반영.

---

## 5-A. 스모크 테스트 (2026-07-27) — **여기부터 읽을 것**

파이프라인을 처음으로 **end-to-end 관통**시켰다. 재현 스크립트가 **리포에 있다**:

```bash
backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py
```

(기본 구역 `area-0baecbba` = 영등포 3.3km². 인자로 다른 `backend/networks/*.osm` 지정 가능.
PostGIS 함정·ASCII 스테이징을 스크립트가 알아서 처리한다. 콘솔 깨지면 `PYTHONIOENCODING=utf-8`.)

### 현재 상태 — **생존율 96.2% / 100.6%** (커밋 `5458136`으로 손실 3건 해결)

```
                        영등포(0baecbba)      안양·의왕(1b5adb59)
total_trips                  5000                  5000
  → OD 파일 적재             4787 (95.7%)          5000 (100.0%)
  → od2trips trip            4812                  5030
  → duarouter 차량           4812 (100.0%)         5030 (100.0%)
  ★ 최종 생존율              96.2%                 100.6%
```

> 100%를 넘는 건 오류가 아니다 — od2trips가 OD 라인마다 소수 통행을 정수로 반올림하기
> 때문(라인 2,352개). 총량 기준 오차 ~1% 수준이라 무시해도 된다.

| 단계 | 영등포 결과 |
|---|---|
| netconvert (한글 경로) | ✅ 신호 11개 |
| **도로 bbox** | ✅ `net_bbox()`로 엣지 형상에서 측정 (origBoundary 금지 — 아래) |
| 건물 로드 | ✅ 5,194동 / 연면적 6.20km² (`POSTGIS_ENABLED=false` 필요, §4) |
| build_zones | ✅ 존 61개, 질량 max/중앙 3.7배 |
| radiation OD | ✅ 통행거리 중앙 ~950m |
| 승용차 연결성 | 엣지 498 / 성분 31개 / 최대 448(90%) / 고립 50 |
| TAZ + OD | ✅ 적재 4,787 (95.7%) — 자기셀 17 / 재배정 42 / 버림 2존(질량 2.2%) |
| od2trips | ✅ trip 4,812개 (**ASCII 스테이징 필수** — §7) |
| duarouter | ✅ **4,812대 (100.0%)** |

남은 손실은 **버려진 존 2개(질량 2.2%)** 뿐이고, 이건 버그가 아니라 정책이다 —
`max_reassign_m=900`을 넘도록 도로에서 먼 건물은 의도적으로 버린다(§5-A 재배정 항목).

### ✅ 해결 1: 도로 없는 존의 수요 (커밋 `e46471e`)

`map_zones_to_taz`가 도로 없는 셀을 `None`으로 버려 **질량 48.7%가 배정 전에 증발**했었다.
→ **최근접 도로존 재배정**으로 확정·구현. 건물이 있다 = 사람이 산다는 뜻이고, 그들은 차를
안 타는 게 아니라 골목을 걸어나가 옆 셀 큰길에서 탄다.

| | 구 정책(버림) | 신 정책(재배정) |
|---|---|---|
| OD 적재 통행량 | 1,326 (26.5%) | **4,681 (93.6%)** |
| 매핑된 존 | 23/47 | **47/47** |
| 버려진 존 | 24개 (질량 48.7%) | **0개** |

재배정 거리 **중앙 300m / 최대 849m** — 상한 900m 안에서 전부 해결. `taz_mapping_summary()`로
매 실행마다 자기셀/재배정/버림 비율을 확인할 것.

> 참고: 존↔TAZ **정렬은 원래부터 정상**이었다(셀 인덱스 범위 ix 37326~37331 양쪽 일치).
> `ref_lat` 정합 문제가 아니라 진짜로 도로가 없는 셀들이었다. 헛다리 짚지 말 것.
>
> ⚠️ 위 표의 존 개수(47)는 **origBoundary bbox 시절 값**이다. 아래 "해결 4"의 `net_bbox`
> 적용 후에는 영등포 존이 61개(자기셀 17 / 재배정 42 / 버림 2)로 바뀐다. 재배정 정책 자체의
> 효과를 보여주는 표로만 읽을 것.

### ✅ 해결 2: duarouter 라우팅률 48.7% → 100% — 고립 성분 필터

**원인: 승용차 전용 그래프가 쪼개져 있었다.**

| | 영등포 | 안양·의왕 |
|---|---|---|
| 승용차 통행가능 엣지 | 498개 | 577개 |
| 최대 강연결성분(SCC) | **448개 (90%)** | 544개 (94.3%) |
| 고립 성분에 흩어진 엣지 | **50개** (31개 성분) | 33개 (10개 성분) |

`build_taz`가 **이 고립 엣지들도 TAZ에 담았기 때문에**, od2trips가 그걸 출발지·도착지로 뽑고
duarouter가 경로를 못 찾았다. 실패 상위 목적지 엣지가 정확히 그 섬들이었다 —
`517043458#*`(14짜리 섬, 815건) · `278157943#0`(6짜리 섬) · `517254646#3`(단독).
**엣지의 10%가 실패의 51%를 만들었다** (TAZ가 길이 가중이라 긴 섬 도로가 자주 뽑힘).

⚠️ **`netconvert --keep-edges.components 1`은 해법이 아니다 (실측 확인).** 엣지 16개만 제거되고
라우팅률은 2272로 그대로였다. 그 옵션은 **vClass를 구분하지 않아** 보행자 길로 이어진 것도
"연결됨"으로 치기 때문. 전체 그래프 기준 최대 SCC는 723개(88%)라 문제가 안 보인다 —
**반드시 `allows("passenger")` 기준으로 SCC를 계산해야** 고립이 드러난다.

**→ 구현:** `build_taz(..., largest_component_only=True)` (기본값). `getAllowedOutgoing(vclass)`로
엣지 그래프를 세워 Tarjan SCC를 구하고 최대 성분 밖 엣지를 제외한다. 이 API는 **duarouter가
쓰는 것과 같은 허용 규칙**(from-lane·to-lane·connection 전부 vclass 허용)이라 판정이 일치한다.
재배정과 맞물려 동작한다 — 섬만 있던 존은 TAZ가 사라지고 재배정이 가장 가까운 **연결된**
존으로 보낸다. 진단은 `component_summary(net)`.

### ✅ 해결 3: self-loop(o==d) 살리기 — +515통행 (10.3%p)

재배정으로 A존이 B셀에 합쳐지면 A→B 통행이 `o==d`가 되고, `write_od_o_format`이 이를
건너뛰었다. 그런데 **radiation은 애초에 i==j를 만들지 않으므로 여기 생기는 `o==d`는 전부
재배정이 서로 다른 두 존을 합친 결과 = 실재 수요다.** 버리면 앞단에서 재배정으로 살린 수요를
뒷단에서 도로 버리는 셈이다.

**→ 구현:** `write_od_o_format(..., keep_intra_zone=True)` (기본값) + od2trips에
**`--different-source-sink`**. od2trips는 같은 TAZ 안에서도 source/sink 엣지를 따로 뽑으므로
주행이 성립하고, 이 플래그가 출발=도착 엣지로 뽑히는 것을 막는다.

### ✅ 해결 4 (교차검증 중 새로 발견): `origBoundary`는 실제 도로 범위가 아니다

**이게 넷 중 제일 위험했다.** 다른 구역으로 교차 검증하다 드러났다.

Overpass가 bbox에 걸친 way의 **전 노드**를 반환하므로, 멀리 뻗은 도로·노선 관계의 노드까지
`.osm`에 들어온다. netconvert는 그 노드들까지 포함해 `origBoundary`/`convBoundary`를 계산하지만
**엣지로는 만들지 않는다.** 그 결과 두 값이 크게 어긋난다:

| 구역 | origBoundary | 실제 승용차 도로 범위 | 그대로 썼을 때 |
|---|---|---|---|
| 안양·의왕 (`1b5adb59`) | 6.1×13.4km (82km²) | **1.5×1.0km** | 존 897개 중 도로 있는 셀 17개 → **질량 95.8% 증발, 생존율 2.0%** |
| 영등포 (`0baecbba`) | 도로범위의 0.8×1.0배 | — | 우연히 비슷해 문제가 안 보였다 |

**→ 구현:** `net_bbox(net, margin_m=300)` — 엣지 형상 좌표에서 직접 재고 셀 한 칸 여유를 준다.
안양·의왕 생존율 2.0% → **100.6%**.

> `.osm`의 `<bounds>` 태그는 맞는 값이지만 **파일에 없을 수도 있어 신뢰 불가**
> (`area-0baecbba.osm`엔 아예 없다). 엣지에서 직접 재는 게 유일하게 안전하다.
> **교훈: 한 구역에서만 검증하지 말 것.** 영등포만 봤으면 이 버그를 못 잡았다.

---

## 6. 미결정 사항 (다음 채팅에서 정할 것)

- **시뮬레이션 창 길이** — 예열 몇 분 + 상승~피크 어디까지(예: 07:00~09:00).
- **N\* 레벨 튜닝** — 총 통행/창을 피크가 용량 넘도록. 반복 보정(v2 §5-3, γ 배율 수렴).
- **λ 최종값** — 0.9999 앵커, 배정 결과를 실측 지점 교통량과 비교해 미세조정(가능하면).
- **"가장 붐비는 시점" 정의** — 시간곡선 정점 스냅샷 1장 (이미 시간프로파일 채택했으므로 명확).
- **RSU 배치 해상도** — 엣지별 교통량 사용(격자 질량 아님) 확정. 구현 시 반영.
- ~~**도로 없는 존의 수요 처리**~~ → ✅ **결정·구현 완료**: 최근접 도로존 재배정 (커밋 `e46471e`).
- ~~**`build_taz` 고립 성분 필터**~~ → ✅ **구현 완료**: 최대 승용차 SCC만 담음 (커밋 `5458136`).
- ~~**self-loop 통행 살릴지**~~ → ✅ **결정·구현 완료**: 살린다 (`keep_intra_zone=True` +
  od2trips `--different-source-sink`, 커밋 `5458136`).
- **`max_reassign_m=900` 상한 유지 여부** — 영등포에서 존 2개(질량 2.2%)가 이 상한 밖이라
  버려진다. 유일하게 남은 손실. 상한을 늘리면 회수되지만 도로에서 먼 건물이 간선에 붙어
  질량장이 왜곡된다. **버그가 아니라 정책** — 지금은 그대로 두는 쪽.
- **`build_taz` 중점 규칙 개선** — 엣지 전체 shape 기준으로 바꿀지(질량 +6.9%p). 값싼 개선이나,
  재배정이 이미 대부분 흡수하므로 **우선순위 낮아짐**.
- **`net_bbox` 여유(margin) 값** — 현재 셀 한 칸(300m). 넓히면 도로 밖 건물을 더 담고
  재배정 부담이 커진다. 6단계 결과 보고 조정.
- **`postgis_available()` 수정 여부** — 실제 연결을 확인하도록 고칠지(앱 전체 영향) / 수요
  파이프라인만 parquet 직접 읽게 할지(국소적, 스모크 스크립트는 이미 후자). §4 참조.
- **netconvert discard 플래그 재검토** — `--tls.discard-loaded`/`--tls.discard-simple`가 OSM 신호를
  버리는 문제(§7). 바꾸면 기존 모든 시나리오 net이 달라져 영향 범위가 넓다.
- **`--tls.guess.threshold` 튜닝** — 구역별 신호 수 편차(§5). 정체 관측 수단이 생긴 뒤.

---

## 7. 기술 노트 / 함정 (재현 필수)

- **SUMO 위치:** `C:\Program Files (x86)\Eclipse\Sumo`. 도구 od2trips/duarouter/duaIterate.py/
  sumo/netconvert 전부 있음(SUMO 1.27).
- **od2trips 옵션 함정:** `-n` = `--taz-files` (net 아님!). od2trips는 **net 불필요**, TAZ+OD만.
  명령: `od2trips --taz-files T.xml --od-matrix-files OD.txt -o trips.xml --spread.uniform`.
- **⚠️ 현행 netconvert 플래그가 OSM 신호를 오히려 버림 (2026-07-27 발견):**
  `--tls.discard-loaded`·`--tls.discard-simple` 탓에 **아무 옵션 없이 변환할 때보다 신호가 적다.**
  (실측: 옵션 없음 → 영등포 **4개**·안양 **9개** / 현행 설정 → **2개**·**8개**.) 정체 생성이 목표라면
  재검토 대상이지만, 바꾸면 **기존 모든 시나리오의 net이 달라져** 영향 범위가 넓다. → 미결정(§6).
- **⚠️ 한글 경로 문제 — 도구마다 다름 (2026-07-27 실측으로 정정):**
  같은 파일을 경로만 바꿔 실행한 결과, **일괄 규칙이 아니라 도구별로 갈린다.**

  | 도구 | 한글 경로 | 비고 |
  |---|---|---|
  | `netconvert` | ✅ **정상** | `C:\Users\최동혁\...`에서 rc=0. 기존 문서의 "netconvert도 동일"은 **틀림** |
  | `duarouter` | ✅ **정상** | 한글·ASCII 양쪽 다 rc=0 |
  | `od2trips` | ❌ **실패** | `Error: Could not open '...\smoke.od.txt'` — 파일 내용은 동일한데 ASCII 경로로 복사하면 즉시 성공 |

  → **od2trips 단계만 ASCII 임시 디렉터리에 스테이징**하면 된다. 전체를 옮길 필요 없음.
  `scripts/smoke_demand_pipeline.py`가 이 방식으로 처리하고 있으니 그대로 따를 것.
  (`sumo` 본체는 아직 미검증 — 6단계에서 확인할 것.)
- **⚠️ 승용차 그래프는 vClass 기준으로 봐야 한다:** 전체 엣지 기준 SCC를 보면 88%가 한 덩어리라
  멀쩡해 보이지만, `allows("passenger")`로 거르면 최대 SCC가 90%로 떨어지고 50개 엣지가
  31개 고립 섬에 흩어져 있다. **연결성 진단은 반드시 vClass 필터 후에** 할 것 (§5-A).
- **⚠️ `origBoundary`/`convBoundary`를 구역 범위로 쓰지 말 것 (2026-07-27 발견):**
  Overpass가 경계에 걸친 way의 전 노드를 반환 → netconvert가 그 노드까지 포함해 boundary를
  계산하지만 엣지로는 안 만든다. `area-1b5adb59`는 origBoundary 82km² vs 실제 도로 1.5km².
  **건물 조회 bbox는 `assignment.net_bbox(net, margin_m=300)`** 으로 엣지 형상에서 직접 잴 것.
  `.osm`의 `<bounds>` 태그도 신뢰 불가(`area-0baecbba.osm`엔 없음). §5-A "해결 4".
- **⚠️ 구역 하나로 검증하지 말 것:** 위 origBoundary 버그는 영등포에서 우연히 배율이 0.8~1.0배라
  보이지 않았고, 두 번째 구역을 돌리자마자 생존율 2.0%로 드러났다. 스모크는 최소 2개 구역에서.
  ```bash
  backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py area-1b5adb59
  ```
- **od2trips 반올림:** OD 라인마다 소수 통행을 정수로 반올림하므로 총량이 몇 % 넘칠 수 있다
  (안양·의왕 라인 2,352개 → 생존율 100.6%). 100%를 넘어도 오류가 아니다.
- **O-format:** 첫 줄 `$OR;D2`, 다음 `begin_h end_h`(시 단위), `factor`, 이후 `origin dest count`.
- **TAZ 파일:** `<tazs><taz id="ix_iy"><tazSource id=edge weight=len/><tazSink .../></taz></tazs>`.
- **CRS:** 건물 parquet 4326. 면적은 `to_crs(5186).area`. 원본 shp는 5186.
- **건물 파일명 NFD:** `os.listdir`가 자모분리형 반환 → `unicodedata.normalize('NFC', s)` 후 매칭.
- **존↔엣지 정렬:** `build_zones`와 `build_taz`에 **같은 `ref_lat`**(보통 bbox 중심 위도) 전달.
- **mock_graph 클리핑 없음:** OSM 다운로드가 bbox를 확장(`expand_bbox` ~278m)하고 Overpass가
  경계 걸친 way의 전 노드를 반환 → mock_graph는 구역 밖 노드 포함. 배치 후보는 이미
  `current_bbox`로 필터링함(이 세션에서 수정). 수요 파이프라인도 bbox 필터 유의.
- **검증 스크립트:** `scripts/smoke_demand_pipeline.py`로 **리포에 보존됨**(2026-07-27).
  회귀 확인은 이걸 돌릴 것. 그 전 세션의 일회성 스크립트들은 scratchpad와 함께 사라졌으나
  필요 로직은 모듈·이 문서에 반영돼 있다.
- **콘솔 인코딩:** 기본 cp949라 한글·em-dash 출력이 깨지거나 `UnicodeEncodeError`가 난다.
  파이썬 실행 시 `PYTHONIOENCODING=utf-8`를 붙일 것.
- **파이썬 환경:** `backend/.venv/Scripts/python.exe` (3.11.3). pandas·geopandas·shapely·
  pyarrow·sumolib 모두 설치돼 있음. 시스템 파이썬 말고 반드시 이걸 쓸 것.

---

## 8. 바로 다음 액션 (새 채팅 시작점)

**5단계는 끝났다.** 수요 손실 4건을 전부 잡아 생존율이 45.4% → **96.2%(영등포) /
100.6%(안양·의왕)** 가 됐다(§5-A). 다음은 **6단계(시간 프로파일 + 동적 SUMO)** 다.

**0. 현재 상태 재현부터** — 두 구역 모두 돌려서 기준선 확인:
```bash
backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py
backend/.venv/Scripts/python.exe scripts/smoke_demand_pipeline.py area-1b5adb59
```
기대값 — 영등포: 존 61 / OD적재 95.7% / trip 4812 / 차량 4812(100%) / 최종 **96.2%**.
안양·의왕: 존 49 / OD적재 100% / trip 5030 / 차량 5030(100%) / 최종 **100.6%**.

1. **오케스트레이터 모듈화** — `smoke_demand_pipeline.py`의 흐름을 `backend/app/services/demand/`
   아래 정식 모듈로. 모듈 3개는 **아직 앱 어디에서도 import되지 않는다**(고아 상태 —
   스모크 스크립트만 씀). od2trips만 ASCII 스테이징(§7), 건물은 parquet 직접(§4),
   bbox는 `net_bbox`(§5-A 해결 4).
2. **시간대 OD 슬라이스** — `write_od_o_format`이 이미 `begin_h/end_h/factor`를 받으므로
   24h 곡선(`시간대_프로파일.csv`)으로 **반복 호출**만 하면 됨.
3. **동적 SUMO** — 예열 → 피크 스냅샷 → 엣지별 교통량 → 배치 수요.
   ⚠️ `sumo` 본체의 한글 경로 내성은 **아직 미검증**(§7) — 여기서 확인할 것.
4. **N\* 레벨 튜닝** — 이제 가능하다. 생존율이 안정됐으므로 `TOTAL_TRIPS`를 올려가며
   피크가 도로 용량을 넘는 지점을 찾는다(v2 §5-1·§5-3).
5. **UI 정리** — ITS·첨두/비첨두 제거, 수요 배율 n 노브. **프론트는 반드시 상의 후 함께.**

> 참고 명령/데이터는 §4·§5·§7에 다 있음. 모듈은 `backend/app/services/demand/`에 준비됨.
