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
5. 배정 (OD→도로별 교통량) .......... ✅ 핵심 완료 (정적 배정). 시간프로파일·동적SUMO는 6단계와 함께
6. SUMO 운영설정 + 시간프로파일 주입 . ⬜ 다음
7. 파이프라인 통합 ................. ⬜ 다음
   (RL은 7단계 이후 최종 층으로. 지금은 손대지 않음 — 친구가 재설계 중)
```

**지금 우리는 5단계 핵심을 끝내고 6단계 문 앞.**

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
| `demand/assignment.py` | §7·§8 배정 준비 | `build_taz(net_file, cell, ref_lat)`; `write_taz_xml()`; `write_od_o_format(flows, zone_taz, path, begin_h, end_h)`; `map_zones_to_taz(zones, taz)` |

> `Zone`: `ix, iy, center_lat, center_lng, mass, n_buildings`. 존↔엣지 정렬은 **동일 `ref_lat`·
> `origin_shift`로 `cell_of`** 를 쓰는 게 필수(TAZ 매핑 정합).

### 검증 결과 (실데이터)

- **격자+질량 (춘천 도심 5×5km, F_FAC_BUILDING 6.7만동):** 셀 커버리지 98%, 질량 max/중앙
  **16.5배**(도심 핵 구조), 존당 건물 중앙 40동(§4 `s_ij` 통계 유효성 충족).
- **radiation OD (서울 도심 종로·중구, 건물 5.2만동 → 존 294):** 도착 상위5% 존이 **20%** 흡수
  (고질량 쏠림=구조성), 방향 비대칭 중앙 **0.50**(통근 방향성 자동생성), λ=0.9999→통행 ~1.5km.
- **배정 (서울 도심 실제 net, 신호 234개):** radiation OD → od2trips → duarouter → 도로별 교통량.
  상위10% 엣지가 통과량 **63%** 점유(간선 집중), **최다통과 상위10 중 7개가 OD 종점 아닌
  통과경로** → v2 §8-1 "병목=통과 간선·교차로" 실측 확인. (RSU를 교차로에 놓는 서사와 직결.)

### 브랜치 & 커밋 (`dhkchoi`, origin에 푸시됨)

```
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

`BUILDING_REPOSITORY.query_by_bbox(minlng,minlat,maxlng,maxlat)`가 파일 폴백으로 건물 조회
(`ground_floor` 반환). ⚠️ 현 PostGIS `buildings` 테이블은 비어 있어 **파일(parquet) 경로가 실사용**.

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
- **신호등:** `netconvert`에 **`--tls.guess`** 추가 필수(안 넣으면 신호 0.7%뿐 → 정체 안 생김).
  검증 net에선 넣어서 52→**234개**로 증가 확인. → **프로젝트의 `main.py:netconvert`에도 반영 필요**
  (현재 `--tls.guess-signals`만 있고 `--tls.guess` 없음).
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

## 6. 미결정 사항 (다음 채팅에서 정할 것)

- **시뮬레이션 창 길이** — 예열 몇 분 + 상승~피크 어디까지(예: 07:00~09:00).
- **N\* 레벨 튜닝** — 총 통행/창을 피크가 용량 넘도록. 반복 보정(v2 §5-3, γ 배율 수렴).
- **λ 최종값** — 0.9999 앵커, 배정 결과를 실측 지점 교통량과 비교해 미세조정(가능하면).
- **"가장 붐비는 시점" 정의** — 시간곡선 정점 스냅샷 1장 (이미 시간프로파일 채택했으므로 명확).
- **RSU 배치 해상도** — 엣지별 교통량 사용(격자 질량 아님) 확정. 구현 시 반영.

---

## 7. 기술 노트 / 함정 (재현 필수)

- **SUMO 위치:** `C:\Program Files (x86)\Eclipse\Sumo`. 도구 od2trips/duarouter/duaIterate.py/
  sumo/netconvert 전부 있음(SUMO 1.27).
- **od2trips 옵션 함정:** `-n` = `--taz-files` (net 아님!). od2trips는 **net 불필요**, TAZ+OD만.
  명령: `od2trips --taz-files T.xml --od-matrix-files OD.txt -o trips.xml --spread.uniform`.
- **⚠️ 한글 경로 문제:** SUMO CLI는 경로에 한글(`최동혁`)이 있으면 **파일을 못 연다**. 작업파일은
  **ASCII 경로**(예: `C:/v2x_stage5`)에서 실행. (netconvert도 동일. net.xml 등 복사해서 실행.)
- **O-format:** 첫 줄 `$OR;D2`, 다음 `begin_h end_h`(시 단위), `factor`, 이후 `origin dest count`.
- **TAZ 파일:** `<tazs><taz id="ix_iy"><tazSource id=edge weight=len/><tazSink .../></taz></tazs>`.
- **CRS:** 건물 parquet 4326. 면적은 `to_crs(5186).area`. 원본 shp는 5186.
- **건물 파일명 NFD:** `os.listdir`가 자모분리형 반환 → `unicodedata.normalize('NFC', s)` 후 매칭.
- **존↔엣지 정렬:** `build_zones`와 `build_taz`에 **같은 `ref_lat`**(보통 bbox 중심 위도) 전달.
- **mock_graph 클리핑 없음:** OSM 다운로드가 bbox를 확장(`expand_bbox` ~278m)하고 Overpass가
  경계 걸친 way의 전 노드를 반환 → mock_graph는 구역 밖 노드 포함. 배치 후보는 이미
  `current_bbox`로 필터링함(이 세션에서 수정). 수요 파이프라인도 bbox 필터 유의.
- **검증 스크립트:** 일회성이라 scratchpad에 있음(리포 미포함). 필요 로직은 모듈에 이미 반영.

---

## 8. 바로 다음 액션 (새 채팅 시작점)

**6단계 시작:** 24h 곡선을 시간대별 OD 슬라이스로 만들어 od2trips에 주입 → `sumo`로 동적
실행(신호 `--tls.guess`, teleport off, 예열) → 정체가 생겼다 풀리는 **시간별 도로 교통량** 확인 →
**피크 스냅샷**을 배치 수요로 연결. 그 다음 7단계 통합.

> 참고 명령/데이터는 §4·§5·§7에 다 있음. 모듈은 `backend/app/services/demand/` 에 준비됨.
