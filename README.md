# V2X AI Routing Lab

V2X 환경에서 차량 경로를 시뮬레이션하는 웹 기반 실험 도구입니다.  
OpenStreetMap 도로 데이터를 불러와 SUMO로 차량 주행을 시뮬레이션하고, 브라우저에서 실시간으로 확인할 수 있습니다.

---

## 필수 설치

### 1. SUMO
SUMO(Simulation of Urban MObility)를 반드시 먼저 설치해야 합니다.

- 다운로드: https://sumo.dlr.de/docs/Downloads.php
- **권장 설치 경로: `C:\Program Files (x86)\Eclipse\Sumo`**
  - 다른 경로에 설치할 경우, 환경변수 `SUMO_HOME`을 해당 경로로 설정해야 합니다.
  - 예: `SUMO_HOME=D:\tools\sumo`

> `traci`, `sumolib`은 pip 패키지가 아닙니다. SUMO 설치 시 함께 포함됩니다.

### 2. Python 3.x

패키지 설치:

```bash
cd backend
pip install -r requirements.txt
```

---

## 실행 방법

1. **백엔드 실행** — `start_backend.bat` 더블클릭  
   → FastAPI 서버가 `http://localhost:8001`에서 시작됩니다.

2. **프론트엔드 열기** — `start_frontend.bat` 더블클릭  
   → 브라우저에서 `http://localhost:8001/app/index.html`이 열립니다.

> `frontend/index.html`을 파일 탐색기에서 직접 열면 CORS 오류가 발생합니다.  
> 반드시 백엔드 서버를 통해 접속하세요.

---

## 사용 방법

1. 브라우저에서 지도 위에 시뮬레이션할 구역을 드래그하여 선택
2. **구역 설정** 버튼 클릭 → OSM 도로 데이터 다운로드 및 SUMO 네트워크 변환 (수십 초 소요)
3. 출발지/목적지를 지도에서 클릭
4. **시뮬레이션 시작** → 차량이 다익스트라 경로로 주행

---

## 프로젝트 구조

```
v2x_lab/
├── backend/
│   ├── main.py            # FastAPI 서버, SUMO 연동 로직
│   ├── requirements.txt   # Python 패키지 목록
│   └── networks/          # 실행 시 자동 생성 (OSM, SUMO 네트워크 파일)
├── frontend/
│   ├── index.html
│   ├── app.jsx
│   └── ...
├── start_backend.bat      # 백엔드 실행 스크립트
└── start_frontend.bat     # 브라우저 오픈 스크립트
```

---

## SUMO_HOME 환경변수 설정 (기본 경로가 아닌 경우)

**Windows (PowerShell):**
```powershell
$env:SUMO_HOME = "C:\your\path\to\sumo"
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

또는 시스템 환경변수에 영구 등록:  
제어판 → 시스템 → 고급 시스템 설정 → 환경 변수 → `SUMO_HOME` 추가
