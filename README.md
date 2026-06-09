# V2X AI Routing Lab

V2X 환경에서 차량 경로를 시뮬레이션하는 웹 기반 실험 도구입니다.  
OpenStreetMap 도로 데이터를 불러와 SUMO로 차량 주행을 시뮬레이션하고, 브라우저에서 실시간으로 확인할 수 있습니다.

## 구조

이 저장소는 모노레포가 아니라 단순한 `backend + frontend` 구조입니다.

```text
V2X_LAB/
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── networks/
├── frontend/
│   ├── index.html
│   ├── app.jsx
│   ├── styles.css
│   └── tab-*.jsx
├── start_backend.bat
├── start_backend.sh
├── start_frontend.bat
└── start_frontend.sh
```

## 기술 구성

- Backend: FastAPI + Uvicorn
- Frontend: 정적 HTML + React 18 UMD + Babel standalone
- Map: Leaflet
- Simulation: SUMO + TraCI
- Network data: OpenStreetMap + Overpass API
- DB: PostgreSQL + PostGIS

SUMO가 정상 동작하지 않는 환경에서는 backend가 자동으로 `OSM fallback mode`로 전환되어,
다운로드한 OSM 도로 그래프 위에서 mock 시뮬레이션을 계속 실행합니다.

## 필수 설치

### 1. Python 3.10+

백엔드 의존성 설치:

```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. SUMO

SUMO는 반드시 별도로 설치해야 합니다.

- 공식 문서: [SUMO Downloads](https://sumo.dlr.de/docs/Downloads.php)

이 프로젝트는 아래 순서로 SUMO를 찾습니다.

1. `SUMO_HOME` 환경변수
2. 시스템 기본 설치 경로
3. `PATH` 안의 `sumo`, `netconvert`

#### macOS 예시

```bash
brew install sumo
```

Homebrew 설치 시 보통 별도 설정 없이 동작하지만, 필요하면:

```bash
export SUMO_HOME=/opt/homebrew/opt/sumo/share/sumo
```

만약 `netconvert`가 `xerces-c` 관련 `dyld` 오류를 내면, 현재 Homebrew의 `sumo`와 `xerces-c`
조합이 깨진 경우일 수 있습니다. 이때도 앱은 자동으로 mock fallback mode로 내려가도록 되어 있습니다.

#### Windows 예시

기본 설치 경로 예:

```text
C:\Program Files (x86)\Eclipse\Sumo
```

다른 경로에 설치했다면 `SUMO_HOME`을 설정하세요.

## 실행 방법

### Demo Mode

PostGIS 컨테이너 실행:

```bash
docker compose up -d
```

데모 DB restore:

```bash
bash scripts/restore_demo_db.sh
```

그 다음 backend / frontend를 실행합니다.

### Full Data Mode

1. 원본 GIS를 `data/raw/standard_link/`, `data/raw/buildings/`에 둡니다.
2. PostGIS를 실행합니다.
3. 전처리 스크립트로 DB를 구축합니다.

```bash
python3 scripts/preprocess_standard_links.py
python3 scripts/preprocess_buildings.py
```

### macOS / Linux

백엔드 실행:

```bash
bash ./start_backend.sh
```

기본값은 안정성을 위해 `--reload` 없이 실행됩니다. 개발 중 자동 리로드가 필요하면:

```bash
V2X_RELOAD=1 bash ./start_backend.sh
```

프론트 열기:

```bash
bash ./start_frontend.sh
```

또는 브라우저에서 직접:

```text
http://localhost:8001/app/index.html
```

### Windows

백엔드 실행:

```text
start_backend.bat
```

자동 리로드가 필요하면 실행 전에 `V2X_RELOAD=1` 환경변수를 설정하세요.

프론트 열기:

```text
start_frontend.bat
```

## 중요 사항

- `frontend/index.html`을 `file://`로 직접 열면 CORS 문제로 동작하지 않습니다.
- 반드시 backend 서버를 통해 `http://localhost:8001/app/index.html`로 접속해야 합니다.
- SUMO가 설치되지 않았거나 `SUMO_HOME` / `PATH`가 맞지 않으면 TraCI 시뮬레이션은 동작하지 않습니다.
- TraCI 또는 `netconvert`가 깨져 있어도, OSM 다운로드가 성공하면 앱은 mock fallback mode로 계속 실행됩니다.

## API 상태 확인

백엔드 상태:

```text
http://localhost:8001/api/health
```

여기에서 현재 플랫폼, `SUMO_HOME`, `sumo` 탐색 여부, `netconvert` 탐색 여부를 확인할 수 있습니다.
추가로 `sumo_runtime_ok`, `netconvert_runtime_ok` 값으로 실제 바이너리 실행 가능 여부도 볼 수 있습니다.

## 표준노드링크 + ITS 교통정보

표준노드링크 원본 파일은 아래 경로에 둡니다.

```text
data/raw/standard_link/
```

필수 파일:

- `MOCT_LINK.shp/.shx/.dbf/.prj/.cpg`
- `MOCT_NODE.shp/.shx/.dbf/.prj/.cpg`

전처리 실행:

```bash
cd backend
.venv/bin/python -m app.services.standard_link.standard_link_preprocessor
```

전처리 결과는 아래에 저장됩니다.

```text
data/processed/standard_link/
```

환경변수는 `backend/.env`에 둡니다.

```env
ITS_API_KEY=
ITS_API_BASE_URL=https://openapi.its.go.kr:9443
DATABASE_URL=postgresql://v2x_lab:v2x_lab@localhost:5432/v2x_lab
```

주요 엔드포인트:

- `POST /admin/standard-links/preprocess`
- `GET /admin/standard-links/status`
- `GET /debug/standard-link-status`
- `POST /traffic/sync-its`
- `GET /traffic/current`
- `GET /debug/its-link-match`

현재 구현은 `ITS trafficInfo.linkId -> MOCT_LINK.LINK_ID` 직접 매칭을 1순위로 사용합니다.
매칭 결과는 표준링크 geometry와 함께 저장되고, OSM/SUMO 네트워크가 준비된 경우 해당 edge 매핑에도 반영됩니다.

## 건물 GIS 전처리

건물 원본 shp는 서버가 직접 관리합니다. 프론트엔드 업로드는 사용하지 않습니다.

원본 경로:

```text
data/raw/buildings/
```

처리 결과:

```text
data/processed/buildings/
```

환경변수:

```env
BUILDING_RAW_DIR=../data/raw/buildings
BUILDING_PROCESSED_DIR=../data/processed/buildings
POSTGIS_ENABLED=true
```

건물 전처리 실행:

```bash
cd backend
.venv/bin/python -m app.services.buildings.building_preprocessor
```

관리자 / 디버그 API:

- `POST /admin/buildings/preprocess`
- `GET /admin/buildings/status`
- `POST /buildings/query-by-bbox`
- `GET /debug/building-obstruction`

시뮬레이션에서는 현재 route 주변 bbox의 전처리된 건물만 조회하고, ego vehicle과 후보 network node를 잇는 선분과 건물 polygon의 교차를 계산해 추정 차폐 손실과 지연 패널티에 반영합니다.

## DB 기반 서비스 구조

서비스는 다음 테이블을 PostGIS에 저장하도록 설계되어 있습니다.

- `standard_links`
- `standard_nodes`
- `buildings`
- `traffic_snapshots`
- `edge_mappings`
- `network_nodes`
- `simulation_runs`

원본 shp/dbf는 `data/raw/` 아래에만 두고, 실행 중에는 원본 파일을 직접 읽지 않습니다.
전처리 후에는 PostGIS를 우선 조회하고, 로컬 파일 기반 처리 결과는 관리자 전처리와 비상 fallback 용도로만 유지합니다.

DB 덤프 스크립트:

```bash
bash scripts/export_demo_db.sh
bash scripts/restore_demo_db.sh
```

보안 규칙:

- `.env`는 Git에 올리지 않습니다.
- `.env.example`에는 실제 API key를 넣지 않습니다.
- 실제 ITS/VWorld/LLM key는 commit하지 않습니다.
