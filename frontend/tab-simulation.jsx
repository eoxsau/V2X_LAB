/* ============================================================
   Simulation tab — real SUMO backend integration
   Flow:
     1) 구역 설정 → drag bbox → POST /api/setup-network (OSM + netconvert)
     2) 출발지 클릭 → 버튼 재클릭으로 확정
     3) 도착지 클릭 → 버튼 재클릭으로 확정
     4) 시작 → POST /api/simulation/start (SUMO TraCI + Dijkstra)
     5) WebSocket → 차량 마커 실시간 이동
   ============================================================ */
const ROUTE_ALGORITHMS = [
  'dijkstra',
  'astar',
  'k_shortest_path',
  'network_aware_routing',
  'look_ahead_routing',
  'rl_routing',
];
const LATENCY_ALGORITHMS = [
  'distance_based_latency',
  'load_aware_latency',
  'blockage_aware_latency',
  'mec_aware_latency',
  'full_composite_latency',
];
const BS_SELECTION_ALGORITHMS = [
  'nearest_bs',
  'lowest_latency_bs',
  'strongest_signal_bs',
  'load_balanced_bs',
  'look_ahead_bs_selection',
  'rl_based_bs_selection',
];
const RESOURCE_ALLOCATION_ALGORITHMS = [
  'equal_allocation',
  'proportional_allocation',
  'traffic_aware_allocation',
  'load_balancing_allocation',
  'latency_minimizing_allocation',
  'priority_based_allocation',
  'custom_allocation_algorithm',
];
const DEFAULT_ALGORITHM_SELECTION = {
  route: 'dijkstra',
  latency: 'full_composite_latency',
  base_station_selection: 'lowest_latency_bs',
  resource_allocation: 'equal_allocation',
};
// rl_routing은 아직 학습된 RL 에이전트가 없어 미구현 — 선택해도 baseline Dijkstra로
// 동작한다(거짓 표시 방지를 위해 선택 버튼에 "미구현" 칩을 붙임).
const UNIMPLEMENTED_ROUTE_ALGORITHMS = new Set(['rl_routing']);

function formatAlgorithmName(name) {
  return name.replaceAll('_', ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
}

// 시뮬레이션 시트(Phase 5) — 엑셀 시트탭처럼 여러 설정/결과 묶음을 저장·전환·비교.
// "전체 비교 실행"이 만든 결과는 tab-scenario.jsx의 시나리오 배치와 같은 키를 써서
// 분석보고서 탭의 "시나리오 배치 비교" 카드가 그대로 보여준다 — 비교 로직을 한 곳에만 둔다.
const SIM_SHEETS_KEY = 'v2x_sim_sheets';
function loadSimSheets() {
  try {
    const saved = JSON.parse(localStorage.getItem(SIM_SHEETS_KEY) || 'null');
    if (saved && saved.length) return saved;
  } catch {}
  return [{ id: `sheet-${Date.now()}`, name: 'Sheet 1', config: {}, result: null, status: 'draft' }];
}
function saveSimSheets(list) {
  try { localStorage.setItem(SIM_SHEETS_KEY, JSON.stringify(list)); } catch {}
}
const SCB_BATCH_KEY = 'v2x_scenario_batches'; // tab-scenario.jsx / tab-report.jsx와 동일 키(읽기·쓰기 공용)
function scbLoadBatches() {
  try { return JSON.parse(localStorage.getItem(SCB_BATCH_KEY) || '[]'); } catch { return []; }
}
function scbSaveBatches(list) {
  try { localStorage.setItem(SCB_BATCH_KEY, JSON.stringify(list.slice(-10))); } catch {}
}

// 엑셀 시트탭 스트립 — 더블클릭으로 이름 수정, "+"로 새 시트, "전체 비교 실행"으로 일괄 평가.
function SheetTabBar({ sheets, activeIdx, onSwitch, onAdd, onRename, onRemove, onRunBatch, batchRunning, batchError }) {
  const [editingIdx, setEditingIdx] = useState(null);
  const [editValue, setEditValue] = useState('');
  return (
    <div style={{
      flex: '0 0 auto', height: 44, background: 'var(--surface)', borderTop: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', padding: '0 8px', gap: 3, overflowX: 'auto',
    }}>
      {sheets.map((s, i) => (
        <div
          key={s.id}
          onClick={() => editingIdx !== i && onSwitch(i)}
          onDoubleClick={() => { setEditingIdx(i); setEditValue(s.name); }}
          title="더블클릭으로 이름 수정"
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px', flex: '0 0 auto', height: 27,
            borderRadius: 7, cursor: 'pointer', fontSize: 12, fontWeight: i === activeIdx ? 600 : 500,
            background: i === activeIdx ? 'var(--brand-tint)' : 'transparent',
            color: i === activeIdx ? 'var(--brand)' : 'var(--ink-3)',
          }}
        >
          {editingIdx === i ? (
            <input
              autoFocus
              value={editValue}
              onChange={e => setEditValue(e.target.value)}
              onBlur={() => { onRename(i, editValue.trim() || s.name); setEditingIdx(null); }}
              onKeyDown={e => {
                if (e.key === 'Enter') { onRename(i, editValue.trim() || s.name); setEditingIdx(null); }
                if (e.key === 'Escape') setEditingIdx(null);
              }}
              onClick={e => e.stopPropagation()}
              style={{ fontSize: 12.5, padding: '2px 4px', width: 90, border: '1px solid var(--border)', borderRadius: 4 }}
            />
          ) : (
            <span style={{ whiteSpace: 'nowrap' }}>{s.name}</span>
          )}
          {s.status === 'ran' && <span title="실행 완료" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--good)', flex: '0 0 auto' }} />}
          {sheets.length > 1 && (
            <button
              onClick={e => { e.stopPropagation(); onRemove(i); }}
              title="시트 삭제"
              style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--ink-4)', fontSize: 11, padding: 0, lineHeight: 1 }}
            >✕</button>
          )}
        </div>
      ))}
      <button onClick={onAdd} title="새 시트" style={{
        display: 'grid', placeItems: 'center', width: 30, flex: '0 0 auto', border: 'none', background: 'none',
        cursor: 'pointer', color: 'var(--ink-3)', fontSize: 16,
      }}>+</button>
      <div style={{ flex: 1 }} />
      {batchError && <span style={{ alignSelf: 'center', fontSize: 11, color: 'var(--bad)', marginRight: 8, whiteSpace: 'nowrap' }}>{batchError}</span>}
      <button
        className="btn sm"
        disabled={batchRunning || sheets.length < 2}
        onClick={onRunBatch}
        style={{ alignSelf: 'center', margin: '0 4px', flex: '0 0 auto' }}
        title={sheets.length < 2 ? '비교하려면 시트가 2개 이상 필요합니다' : '모든 시트를 헤드리스로 일괄 평가해 분석보고서 탭에서 비교'}
      >
        {batchRunning ? <><Icon.reset size={12} className="spin" /> 비교 실행 중…</> : <><Icon.compare size={12} /> 전체 비교 실행 ({sheets.length})</>}
      </button>
    </div>
  );
}

// 컨트롤 패널 / 시뮬레이션 챗봇을 지도 위에 띄우는 원형 토글 버튼 — 둘 다 같은 디자인.
function FabButton({ icon, active, onClick, title }) {
  const IconComp = Icon[icon];
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 38, height: 38, borderRadius: '50%', border: 'none', cursor: 'pointer',
        display: 'grid', placeItems: 'center', flex: '0 0 auto',
        background: active ? 'var(--brand-2)' : 'var(--brand)',
        color: '#fff', boxShadow: 'var(--sh-2)',
      }}
    >
      <IconComp size={16} />
    </button>
  );
}

function SimulationTab({ sim, dispatch, active, vehiclePos, routeCoords, setRouteCoords, setVehiclePos, simNotice, setSimNotice, networkTelemetry, setNetworkTelemetry, simConfig, setSimConfig, backgroundVehicles, setBackgroundVehicles, simLogs, simHistory, routeEdges, sheets, setSheets, activeSheetIdx, setActiveSheetIdx, api, mode: appMode }) {
  const mapRef  = useRef(null);
  const mapObj  = useRef(null);
  const groups  = useRef({});
  const prevVehPos = useRef(null);
  const bgVehMarkers = useRef({}); // 다중차량 실험군 — 배경 차량 마커 풀 (id → circleMarker), setLatLng로 재사용
  const stationMarkers = useRef({});   // 기지국 마커 풀 (id → circleMarker) — networkTelemetry가 초당 10회 들어와도 매번 재생성하지 않음
  const candidateMarkers = useRef({}); // 후보 노드 마커 풀 (id → circleMarker), 위와 동일한 이유
  const buildingsSigRef = useRef(null); // 직전에 그린 차폐 건물 집합의 서명 — 안 바뀌었으면 폴리곤 다시 안 그림

  const KR_CENTER = [36.4, 127.9], KR_ZOOM = 7;
  const MAX_SETUP_AREA_KM2 = 25;

  const [mode,       setMode]       = useState(null);
  const [area,       setArea]       = useState(null);
  const [origin,     setOrigin]     = useState(null);
  const [originDone, setOriginDone] = useState(false);
  const [dest,       setDest]       = useState(null);
  const [destDone,   setDestDone]   = useState(false);
  const [osmStage,   setOsmStage]   = useState(0); // 0 idle · 1 download · 2 convert · 3 ready
  const [osmError,   setOsmError]   = useState(null);
  const [osmWarning, setOsmWarning] = useState(null);
  const [showLayers, setShowLayers] = useState({ vehicles: true, routes: true, stations: true });
  const [simError,   setSimError]   = useState(null);
  const [stations,   setStations]   = useState([]);   // user_created base stations from DB
  const [stationsErr,setStationsErr]= useState(null);
  const [openAlgorithmGroup, setOpenAlgorithmGroup] = useState(null);
  const [selectedAlgorithms, setSelectedAlgorithms] = useState(DEFAULT_ALGORITHM_SELECTION);
  const [networkGen, setNetworkGen] = useState('5g'); // 4g · 5g · 6g — UI only, not wired to backend
  const [vehicleCount, setVehicleCount] = useState(1); // 다중차량 실험군 — 타겟 1대 + 배경 차량 (vehicleCount - 1)대

  // ITS 첨두/비첨두 교통 — Pro 전용. 어느 버킷을 이번 시뮬레이션에 쓸지(트래픽 편향/배경차량 샘플링).
  const [trafficPeriod, setTrafficPeriod] = useState(() => simConfig?.policy_options?.traffic_time_period || 'peak');
  const [trafficSyncing, setTrafficSyncing] = useState(false);
  const [trafficSyncInfo, setTrafficSyncInfo] = useState(null);
  const [trafficSyncError, setTrafficSyncError] = useState(null);
  // 시트 전환 시 loadSheetConfig가 simConfig를 통째로 교체하므로, 거기 실려온
  // traffic_time_period로 셀렉터 표시를 맞춘다(없으면 기존 값 유지).
  useEffect(() => {
    const p = simConfig?.policy_options?.traffic_time_period;
    if (p) setTrafficPeriod(p);
  }, [simConfig?.policy_options?.traffic_time_period]);
  const [openPanel, setOpenPanel] = useState('control'); // null · 'control' · 'scenario' — 우측 FAB로 띄우는 플로팅 패널, 처음 열 때는 컨트롤 패널이 기본으로 열려있음

  // ── 시뮬레이션 시트 (Phase 5) ──────────────────────────────────
  // sheets/activeSheetIdx는 App(app.jsx)으로 끌어올려져 props로 내려온다 — 대시보드 탭도
  // "지금 실행 중인 시트가 뭔지" 같은 출처를 봐야 시트별로 분리해서 보여줄 수 있기 때문.
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchError, setBatchError] = useState(null);
  const prevArrived = useRef(false);
  const currentRunIdRef = useRef(null); // /api/simulation/start가 돌려준 DB simulation_runs.id — 도착 시 시트 데이터를 같은 행에 영구 저장하는 데 씀

  // 캡처된 결과가 있으면(이미 실행 완료된 시트) 그 로그를, 없으면(현재 진행 중인 시트) 실시간
  // simLogs를 보여준다 — 둘 다 같은 시트 안에서만 머무르고 분석보고서 탭으로는 넘어가지 않는다.
  const displayedSheetLogs = sheets[activeSheetIdx]?.result?.simLogs?.length
    ? sheets[activeSheetIdx].result.simLogs
    : (simLogs || []);

  function currentConfigSnapshot() {
    return { origin, dest, vehicleCount, selectedAlgorithms, networkGen, simConfig };
  }

  function loadSheetConfig(sheet) {
    const c = sheet.config || {};
    setOrigin(c.origin || null); setOriginDone(!!c.origin);
    setDest(c.dest || null); setDestDone(!!c.dest);
    setVehicleCount(c.vehicleCount ?? 1);
    setSelectedAlgorithms(c.selectedAlgorithms || DEFAULT_ALGORITHM_SELECTION);
    setNetworkGen(c.networkGen || '5g');
    if (c.simConfig) setSimConfig(c.simConfig);
    setNetworkTelemetry(sheet.result?.network_telemetry || null);
    setRouteCoords(sheet.result?.routeCoords || []);
    setVehiclePos(sheet.result?.vehiclePos || null);
    prevArrived.current = !!sheet.result?.vehiclePos?.arrived;
  }

  async function switchToSheet(idx) {
    if (idx === activeSheetIdx) return;
    // 이전 시트의 시뮬레이션 스레드가 백그라운드에서 계속 돌고 있으면, 그 위치 업데이트가
    // 웹소켓으로 계속 들어와 방금 비운 vehiclePos를 되돌려놓고, 나중에 도착하면 "지금"
    // 활성화된(전혀 다른) 시트로 잘못 캡처되는 문제가 있었다 — 전환 전에 반드시 멈춘다.
    if (sim.running) { try { await fetch(`${api}/api/simulation/stop`, { method: 'POST' }); } catch (_) {} }
    const next = sheets.map((s, i) => i === activeSheetIdx ? { ...s, config: currentConfigSnapshot() } : s);
    setSheets(next); saveSimSheets(next);
    loadSheetConfig(next[idx]);
    setActiveSheetIdx(idx);
    dispatch({ type: 'pause' });
  }

  async function addSheet() {
    // 새 시트 = 완전히 새로운 시뮬레이션. 이전엔 현재 설정을 복제만 해서 지도/출발지·도착지가
    // 그대로 남아있어 "바뀐 게 없어 보인다"는 문제가 있었다 — origin/dest/결과를 비워서
    // 사용자가 새로 출발지·도착지를 찍게 한다.
    //
    // 구역(area)도 비운다 — 시트끼리 같은 구역/지도를 그대로 이어받으면 "시트가 분리가
    // 안 되고 하나의 지도에서 시뮬레이션되는 것처럼" 보인다는 피드백이 있었다. 새 시트는
    // "지도에서 구역 그리기"부터 다시 시작해야 한다.
    //
    // 주의1: /api/simulation/reset은 호출하지 않는다 — 그 엔드포인트는 reset_simulation_state()를
    // 통해 network_ready/mock_graph/current_bbox까지 전부 지워버리지만, 이미 area를 null로
    // 비웠으므로 의미가 없다. 어차피 사용자가 새 구역을 그리면 /api/setup-network가 백엔드의
    // current_bbox/mock_graph를 통째로 덮어써서 이전 시트의 네트워크를 자연스럽게 대체한다.
    //
    // 주의2: 이전 시트가 아직 실행 중이면 반드시 /api/simulation/stop으로 멈춰야 한다. 안 그러면
    // 백그라운드 스레드가 계속 vehiclePos를 웹소켓으로 밀어넣어 방금 비운 상태를 되돌리고,
    // 나중에 도착하면 그 시점에 활성화된(새로 만든) 시트로 잘못 캡처된다 — 실행도 안 한 새
    // 시트에 초록 점이 찍히는 버그의 원인이었다.
    if (sim.running) { try { await fetch(`${api}/api/simulation/stop`, { method: 'POST' }); } catch (_) {} }

    const blankConfig = { origin: null, dest: null, vehicleCount: 1, selectedAlgorithms: DEFAULT_ALGORITHM_SELECTION, networkGen, simConfig };
    const newSheet = { id: `sheet-${Date.now()}`, name: `Sheet ${sheets.length + 1}`, config: blankConfig, result: null, status: 'draft' };
    const next = sheets.map((s, i) => i === activeSheetIdx ? { ...s, config: currentConfigSnapshot() } : s).concat(newSheet);
    setSheets(next); saveSimSheets(next);
    setActiveSheetIdx(next.length - 1);

    setMode(null);
    setArea(null);
    setOsmStage(0); setOsmError(null); setOsmWarning(null);
    setOrigin(null); setOriginDone(false);
    setDest(null); setDestDone(false);
    setVehicleCount(1);
    setSelectedAlgorithms(DEFAULT_ALGORITHM_SELECTION);
    setNetworkTelemetry(null);
    setRouteCoords([]); setVehiclePos(null);
    if (setBackgroundVehicles) setBackgroundVehicles([]);
    setSimError(null); setSimNotice(null);
    prevArrived.current = false;
    dispatch({ type: 'reset' });
    if (groups.current.areaRect) { groups.current.areaRect.remove(); groups.current.areaRect = null; }
    if (groups.current.veh)     { groups.current.veh.remove(); groups.current.veh = null; }
    if (groups.current.route)   groups.current.route.clearLayers();
    if (groups.current.wp)      groups.current.wp.clearLayers();
    if (groups.current.network)   groups.current.network.clearLayers();
    if (groups.current.connLines) groups.current.connLines.clearLayers();
    if (groups.current.blocks)    groups.current.blocks.clearLayers();
    if (groups.current.bgVeh)     groups.current.bgVeh.clearLayers();
    bgVehMarkers.current = {};
    candidateMarkers.current = {};
    buildingsSigRef.current = null;
  }

  function renameSheet(idx, name) {
    const next = sheets.map((s, i) => i === idx ? { ...s, name } : s);
    setSheets(next); saveSimSheets(next);
  }

  function removeSheet(idx) {
    if (sheets.length <= 1) return;
    const next = sheets.filter((_, i) => i !== idx);
    setSheets(next); saveSimSheets(next);
    if (idx === activeSheetIdx) loadSheetConfig(next[Math.min(idx, next.length - 1)]);
    setActiveSheetIdx(prev => idx < prev ? prev - 1 : Math.min(prev, next.length - 1));
  }

  // 도착(또는 정지)을 그 시점의 시트 결과로 캡처 — 시트를 바꿔도 잃지 않게 프런트에 저장.
  // simLogs는 시트 안에 읽기전용으로만 저장한다(설계 B) — 분석보고서 탭의 로그 뷰는 항상
  // "현재/마지막 실행"만 보여주고, 시트별 로그 비교는 이 시뮬레이션 탭에서 따로 보여준다.
  // simHistory/routeEdges도 같이 담아둔다 — 대시보드 탭이 시트별로 분리해서 보여주려면
  // (지금 실행 중이 아닌) 다른 시트의 latency 추이/엣지 비용도 스냅샷으로 남아 있어야 한다.
  function captureResultIntoActiveSheet() {
    setSheets(prev => {
      const next = prev.map((s, i) => i === activeSheetIdx ? {
        ...s,
        config: currentConfigSnapshot(),
        result: {
          network_telemetry: networkTelemetry, routeCoords, vehiclePos, simLogs: simLogs || [],
          simHistory: simHistory || [], routeEdges: routeEdges || null,
          capturedAt: new Date().toISOString(),
        },
        status: 'ran',
      } : s);
      saveSimSheets(next);
      return next;
    });
    // localStorage 스냅샷과 같은 데이터를 DB simulation_runs 행(sheet_id/sheet_name 태깅됨)에도
    // 영구 저장 — 브라우저를 바꾸거나 localStorage가 지워져도 시트별 로그/수치가 남아있게 한다.
    if (currentRunIdRef.current) {
      fetch(`${api}/api/simulation/runs/${currentRunIdRef.current}/capture`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sim_logs: simLogs || [], sim_history: simHistory || [], route_edges: routeEdges || null }),
      }).catch(() => {});
    }
  }

  useEffect(() => {
    if (vehiclePos?.arrived && !prevArrived.current) captureResultIntoActiveSheet();
    prevArrived.current = !!vehiclePos?.arrived;
  }, [vehiclePos?.arrived]);

  async function runAllSheetsAsBatch() {
    const allSheets = sheets.map((s, i) => i === activeSheetIdx ? { ...s, config: currentConfigSnapshot() } : s);
    setSheets(allSheets); saveSimSheets(allSheets);

    const specs = allSheets
      .filter(s => s.config?.origin && s.config?.dest)
      .map(s => ({
        id: s.id,
        label: s.name,
        mode: 'route_metrics',
        origin: s.config.origin,
        dest: s.config.dest,
        vehicle_count: s.config.vehicleCount || 1,
        algorithm_config: s.config.selectedAlgorithms || {},
        simulation_config: {
          ...(s.config.simConfig || {}),
          policy_options: { ...(s.config.simConfig?.policy_options || {}), network_mode: (s.config.networkGen || '5g').toUpperCase() },
        },
      }));
    if (specs.length === 0) { setBatchError('출발지/도착지가 설정된 시트가 없습니다.'); return; }

    setBatchRunning(true); setBatchError(null);
    try {
      const res = await fetch(`${api}/api/scenarios/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: '시뮬레이션 시트 비교', scenarios: specs }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      pollSheetBatch(data.batch_id);
    } catch (e) {
      setBatchRunning(false);
      setBatchError(e.message || '배치 실행 중 오류가 발생했습니다.');
    }
  }

  function pollSheetBatch(batchId) {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${api}/api/scenarios/batch/${batchId}`);
        const data = await res.json();
        if (data.status === 'completed') {
          clearInterval(timer);
          setBatchRunning(false);
          const saved = scbLoadBatches();
          saved.push({ batch_id: batchId, label: data.label, started_at: data.started_at, ended_at: data.ended_at, results: data.results });
          scbSaveBatches(saved);
        }
      } catch {}
    }, 1200);
  }

  // 시트 비교(runAllSheetsAsBatch)와 같은 배치 인프라(POST /api/scenarios/batch)를 재사용하는
  // 범용 폴러 — 완료되면 분석보고서 탭의 "시나리오 배치 비교"가 읽는 SCB_BATCH_KEY에 동일하게
  // 저장한다(별도 표시 UI를 새로 만들 필요 없음).
  function pollScenarioBatch(batchId, onDone) {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${api}/api/scenarios/batch/${batchId}`);
        const data = await res.json();
        if (data.status === 'completed') {
          clearInterval(timer);
          const saved = scbLoadBatches();
          saved.push({ batch_id: batchId, label: data.label, started_at: data.started_at, ended_at: data.ended_at, results: data.results });
          scbSaveBatches(saved);
          onDone(data);
        }
      } catch {}
    }, 1200);
  }

  // ── 파라미터 스윕(민감도 분석) — Pro 전용. 현재 출발/도착/차량수/알고리즘은 고정하고
  // 비용가중치·정책옵션 중 하나만 N단계로 바꿔가며 배치 평가 → 분석보고서에서 비교.
  const SWEEP_PARAMS = [
    { key: 'w_latency',  label: 'Latency 가중치',   section: 'cost_weights',   path: 'w_latency',   defaultFrom: 1,   defaultTo: 5, step: 0.1 },
    { key: 'w_load',     label: '부하 가중치',       section: 'cost_weights',   path: 'w_load',      defaultFrom: 0.5, defaultTo: 3, step: 0.1 },
    { key: 'w_handover', label: '핸드오버 가중치',   section: 'cost_weights',   path: 'w_handover',  defaultFrom: 0.5, defaultTo: 3, step: 0.1 },
    { key: 'lookahead_k', label: 'Lookahead K',     section: 'policy_options', path: 'lookahead_k', defaultFrom: 1,   defaultTo: 8, step: 1, integer: true },
  ];
  const [sweepParam, setSweepParam] = useState(SWEEP_PARAMS[0].key);
  const [sweepFrom, setSweepFrom] = useState(SWEEP_PARAMS[0].defaultFrom);
  const [sweepTo, setSweepTo] = useState(SWEEP_PARAMS[0].defaultTo);
  const [sweepSteps, setSweepSteps] = useState(5);
  const [sweepRunning, setSweepRunning] = useState(false);
  const [sweepError, setSweepError] = useState(null);
  const [sweepDone, setSweepDone] = useState(false);

  function selectSweepParam(key) {
    const def = SWEEP_PARAMS.find(p => p.key === key);
    setSweepParam(key);
    setSweepFrom(def.defaultFrom);
    setSweepTo(def.defaultTo);
  }

  async function runParamSweep() {
    if (!ready) { setSweepError('구역·출발지·도착지를 먼저 설정하세요.'); return; }
    const def = SWEEP_PARAMS.find(p => p.key === sweepParam);
    const steps = Math.max(2, Math.min(10, sweepSteps || 2));
    const values = Array.from({ length: steps }, (_, i) => {
      const raw = sweepFrom + (sweepTo - sweepFrom) * (steps === 1 ? 0 : i / (steps - 1));
      return def.integer ? Math.round(raw) : Math.round(raw * 100) / 100;
    });
    const specs = values.map((v, i) => {
      const cfgOverride = {
        ...(simConfig || {}),
        [def.section]: { ...(simConfig?.[def.section] || {}), [def.path]: v },
      };
      cfgOverride.policy_options = { ...(cfgOverride.policy_options || {}), network_mode: (networkGen || '5g').toUpperCase() };
      return {
        id: `sweep-${def.key}-${i}-${Date.now()}`,
        label: `${def.label}=${v}`,
        mode: 'route_metrics',
        source: 'param_batch',
        origin, dest,
        vehicle_count: vehicleCount,
        algorithm_config: selectedAlgorithms,
        simulation_config: cfgOverride,
      };
    });
    setSweepRunning(true); setSweepError(null); setSweepDone(false);
    try {
      const res = await fetch(`${api}/api/scenarios/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: `파라미터 스윕 — ${def.label}`, scenarios: specs }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      pollScenarioBatch(data.batch_id, () => { setSweepRunning(false); setSweepDone(true); setTimeout(() => setSweepDone(false), 3500); });
    } catch (e) {
      setSweepRunning(false);
      setSweepError(e.message || '스윕 실행 중 오류가 발생했습니다.');
    }
  }

  // ── RL 정책 비교(실험적) — Pro 전용. 동일 출발/도착에 대해 random/greedy/coverage 베이스라인
  // 정책을 각각 평가해 배치로 비교(/api/rl/episode와 동일 로직, 배치 인프라로 일괄 실행).
  const RL_POLICIES = ['random', 'greedy', 'coverage'];
  const [rlRunning, setRlRunning] = useState(false);
  const [rlError, setRlError] = useState(null);
  const [rlDone, setRlDone] = useState(false);

  async function runRLComparison() {
    if (!ready) { setRlError('구역·출발지·도착지를 먼저 설정하세요.'); return; }
    const specs = RL_POLICIES.map((policy, i) => ({
      id: `rl-${policy}-${Date.now()}`,
      label: `RL ${policy}`,
      mode: 'rl_episode',
      source: 'param_batch',
      origin, dest,
      policy,
      n_episodes: 20, // 평균만으로는 정책 간 우열을 통계적으로 말할 수 없어, 표준편차를 같이 보고할 수 있을 만큼 충분히 반복
      max_steps: 200,
    }));
    setRlRunning(true); setRlError(null); setRlDone(false);
    try {
      const res = await fetch(`${api}/api/scenarios/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: 'RL 정책 비교', scenarios: specs }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      pollScenarioBatch(data.batch_id, () => { setRlRunning(false); setRlDone(true); setTimeout(() => setRlDone(false), 3500); });
    } catch (e) {
      setRlRunning(false);
      setRlError(e.message || 'RL 비교 실행 중 오류가 발생했습니다.');
    }
  }

  const coordStr = (ll) => `${ll.lat.toFixed(4)}, ${ll.lng.toFixed(4)}`;
  const ready    = area && originDone && destDone;

  /* ── marker builders ────────────────────────────────────────── */
  function pinIcon(color, faded) {
    return L.divIcon({ className: '', iconSize: [26, 34], iconAnchor: [13, 32], html:
      `<svg width="26" height="34" viewBox="0 0 26 34" style="opacity:${faded ? 0.6 : 1};filter:drop-shadow(0 2px 3px rgba(0,0,0,.3))">
        <path d="M13 33C13 33 24 20 24 12A11 11 0 1 0 2 12C2 20 13 33 13 33Z" fill="${color}" stroke="#fff" stroke-width="2"/>
        <circle cx="13" cy="12" r="4" fill="#fff"/>
      </svg>` });
  }

  function carIcon(color) {
    return L.divIcon({ className: '', iconSize: [20, 20], iconAnchor: [10, 10], html:
      `<div style="width:20px;height:20px;border-radius:50%;background:${color};border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35)"></div>` });
  }

  /* ── init map (once, persists across tab switches) ──────────── */
  useEffect(() => {
    if (mapObj.current || !window.L) return;
    const map = L.map(mapRef.current, { zoomControl: false, attributionControl: false }).setView(KR_CENTER, KR_ZOOM);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, subdomains: 'abcd' }).addTo(map);
    groups.current.wp    = L.layerGroup().addTo(map);
    groups.current.route = L.layerGroup().addTo(map);
    groups.current.network = L.layerGroup().addTo(map);
    groups.current.connLines = L.layerGroup().addTo(map);
    groups.current.blocks = L.layerGroup().addTo(map);
    groups.current.stations = L.layerGroup().addTo(map);
    // 다중차량 실험군 — 배경 차량용 캔버스 렌더러 (N=1000에서도 가벼움)
    groups.current.bgVeh = L.layerGroup().addTo(map);
    groups.current.bgVehRenderer = L.canvas({ padding: 0.5 });
    mapObj.current = map;
    return () => {
      map.remove(); mapObj.current = null; groups.current = {};
      stationMarkers.current = {}; candidateMarkers.current = {}; buildingsSigRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (active && mapObj.current) setTimeout(() => mapObj.current.invalidateSize(), 60);
  }, [active]);

  /* ── load user-created base stations from DB (persists across reloads) ──── */
  async function loadStations() {
    try {
      const res = await fetch(`${api}/network-nodes`);
      const body = await res.json();
      setStations((body.nodes || []).filter(n => n.source === 'user_created'));
      setStationsErr(null);
    } catch (e) {
      setStationsErr(e.message);
    }
  }
  useEffect(() => { loadStations(); }, []);

  /* ── draw origin/dest markers ───────────────────────────────── */
  useEffect(() => {
    const wp = groups.current.wp; if (!wp) return;
    wp.clearLayers();
    if (origin) L.marker([origin.lat, origin.lng], { icon: pinIcon('#1F9D57', !originDone) }).addTo(wp)
        .bindTooltip(originDone ? '출발지 ✓' : '출발지 (미확정)', { direction: 'top' });
    if (dest)   L.marker([dest.lat,   dest.lng],   { icon: pinIcon('#E0463C', !destDone) }).addTo(wp)
        .bindTooltip(destDone   ? '도착지 ✓' : '도착지 (미확정)', { direction: 'top' });
  }, [origin, originDone, dest, destDone]);

  /* ── draw SUMO route polyline received from backend ────────── */
  useEffect(() => {
    const g = groups.current.route; if (!g) return;
    g.clearLayers();
    if (routeCoords && routeCoords.length > 1 && showLayers.routes) {
      L.polyline(routeCoords, { color: '#2E75B6', weight: 4, opacity: 0.7 }).addTo(g);
    }
  }, [routeCoords, showLayers.routes]);

  /* networkTelemetry는 시뮬레이션 중 초당 10회 들어온다 — 예전엔 이 effect가 매번
     ng.clearLayers()로 후보 노드 마커/툴팁을 통째로 지우고 새로 만들어서, 마커 수가 늘수록
     지도가 버벅였다. 이제 마커는 id로 풀링해 재사용(setLatLng/setTooltipContent만 갱신)하고,
     접속선은 개수가 적어(0~2개) 그대로 다시 그리되 별도 레이어(connLines)에 둬서 후보 마커
     풀을 건드리지 않는다. 차폐 건물 폴리곤은 같은 건물 집합이면 다시 그리지 않는다. */
  useEffect(() => {
    const ng = groups.current.network;
    const cl = groups.current.connLines;
    const bg = groups.current.blocks;
    const map = mapObj.current;
    if (!ng || !cl || !bg || !map) return;
    if (!networkTelemetry) {
      ng.clearLayers(); cl.clearLayers(); bg.clearLayers();
      candidateMarkers.current = {};
      buildingsSigRef.current = null;
      return;
    }

    const selected = networkTelemetry.connected_node;

    // ── 후보 노드 마커 — id로 풀링 ──────────────────────────────
    const seenIds = new Set();
    (networkTelemetry.candidate_nodes || []).forEach((node, idx) => {
      // user_created: already rendered by the stations layer (avoid overlap)
      // synthetic: auto-generated placeholders — hidden when user has real stations
      if (node.source === 'user_created' || node.source === 'synthetic') return;
      const id = node.id || node.name || `cand-${idx}`;
      seenIds.add(id);
      const lat = node.lat || selected.lat, lng = node.lng || selected.lng;
      const base = idx === 0 ? '#1E88E5' : '#607D8B';
      const radius = idx === 0 ? 8 : 6;
      const tooltipText = `${node.name || node.id} · ${node.predicted_latency_ms.toFixed(1)}ms`;
      let marker = candidateMarkers.current[id];
      if (!marker) {
        marker = L.circleMarker([lat, lng], { radius, color: '#fff', weight: 2, fillColor: base, fillOpacity: 0.9 }).addTo(ng);
        marker.bindTooltip(tooltipText, { direction: 'top' });
        candidateMarkers.current[id] = marker;
      } else {
        marker.setLatLng([lat, lng]);
        marker.setStyle({ radius, fillColor: base });
        marker.setTooltipContent(tooltipText);
      }
    });
    Object.keys(candidateMarkers.current).forEach((id) => {
      if (!seenIds.has(id)) { candidateMarkers.current[id].remove(); delete candidateMarkers.current[id]; }
    });

    // ── 접속선 — 개수가 적어 그대로 다시 그림 ───────────────────
    cl.clearLayers();
    const lines = networkTelemetry.connection_lines?.length
      ? networkTelemetry.connection_lines.map(l => [[l.from.lat, l.from.lng], [l.to.lat, l.to.lng]])
      : (selected && networkTelemetry.connection_line?.length === 2)
        ? [networkTelemetry.connection_line.map(p => [p.lat, p.lng])]
        : [];
    if (lines.length) {
      const loss = networkTelemetry.estimated_penetration_loss_db || 0;
      const color = loss >= 20 ? '#E0463C' : loss >= 10 ? '#B97B11' : '#1F9D57';
      lines.forEach(coords => {
        L.polyline(coords, { color, weight: 3, opacity: 0.85, dashArray: loss >= 10 ? '8 6' : undefined }).addTo(cl);
      });
    }

    // ── 차폐 건물 폴리곤 — 같은 건물 집합이면 다시 그리지 않음 ─────
    const buildings = networkTelemetry.highlighted_buildings || [];
    const sig = buildings.map(b => b.id ?? b.ufid ?? `${b.geometry?.[0]?.lat},${b.geometry?.[0]?.lng}`).join('|');
    if (sig !== buildingsSigRef.current) {
      buildingsSigRef.current = sig;
      bg.clearLayers();
      buildings.forEach((b) => {
        if (!b.geometry || b.geometry.length < 3) return;
        L.polygon(
          b.geometry.map(p => [p.lat, p.lng]),
          { color: '#E0463C', weight: 1.5, fillColor: '#E0463C', fillOpacity: 0.18 }
        ).addTo(bg);
      });
    }
  }, [networkTelemetry]);

  /* ── user-created base station markers ──────────────────────────
     Always blue (#1E88E5) with white border — same style as synthetic nodes.
     Delete-hover: gray (#9AA5B1) to indicate the target being removed.
     Label always visible below marker.
  ── */
  /* 기지국 마커 본체 — stations/삭제모드/레이어 표시 여부가 바뀔 때만 다시 만든다(id로 풀링).
     networkTelemetry(초당 10회)는 더 이상 이 effect의 의존성이 아니다 — 아래 별도 effect가
     기존 마커의 툴팁 텍스트만 갱신해서, 시뮬레이션 중에도 마커를 통째로 지웠다 새로 만들지
     않는다(이게 예전 지도 렌더링이 느려지던 주된 원인). */
  useEffect(() => {
    const g = groups.current.stations; if (!g) return;
    if (!showLayers.stations) {
      g.clearLayers();
      stationMarkers.current = {};
      return;
    }
    const deleteMode = mode === 'bs_delete';
    const seenIds = new Set();
    stations.forEach((st) => {
      seenIds.add(st.id);
      let marker = stationMarkers.current[st.id];
      if (!marker) {
        marker = L.circleMarker([st.lat, st.lng], {
          radius: 8, color: '#fff', weight: 2.5, fillColor: '#1E88E5', fillOpacity: 0.93, interactive: true,
        }).addTo(g);
        marker.bindTooltip(st.name, { permanent: true, direction: 'bottom', offset: [0, 6], className: 'bs-label' });
        stationMarkers.current[st.id] = marker;
      } else {
        marker.setLatLng([st.lat, st.lng]);
      }
      // 삭제모드 핸들러는 모드가 바뀔 때마다 깨끗하게 다시 건다(중복 바인딩 방지)
      marker.off('mouseover'); marker.off('mouseout'); marker.off('click');
      marker.setStyle({ fillColor: '#1E88E5', color: '#fff' });
      if (deleteMode) {
        marker.on('mouseover', (e) => { e.target.setStyle({ fillColor: '#9AA5B1', color: '#5B6670' }); });
        marker.on('mouseout',  (e) => { e.target.setStyle({ fillColor: '#1E88E5', color: '#fff' }); });
        marker.on('click', (e) => { L.DomEvent.stopPropagation(e); deleteStation(st.id); });
      }
    });
    Object.keys(stationMarkers.current).forEach((id) => {
      if (!seenIds.has(id)) { stationMarkers.current[id].remove(); delete stationMarkers.current[id]; }
    });
  }, [stations, mode, showLayers.stations]);

  /* 기지국 라벨의 latency 텍스트만 갱신 — 마커/툴팁 DOM을 새로 만들지 않고 텍스트만 바꿔서
     초당 10회가 들어와도 가볍다. */
  useEffect(() => {
    if (!showLayers.stations) return;
    const latencyMap = {};
    (networkTelemetry?.candidate_nodes || []).forEach(n => { latencyMap[n.id] = n.predicted_latency_ms; });
    stations.forEach((st) => {
      const marker = stationMarkers.current[st.id];
      if (!marker) return;
      const lat_ms = latencyMap[st.id];
      const labelText = lat_ms != null ? `${st.name} · ${lat_ms.toFixed(1)}ms` : st.name;
      marker.setTooltipContent(labelText);
    });
  }, [networkTelemetry, stations, showLayers.stations]);

  /* ── vehicle marker from WebSocket position ─────────────────── */
  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    if (!vehiclePos || !showLayers.vehicles) {
      if (groups.current.veh) { groups.current.veh.remove(); groups.current.veh = null; }
      return;
    }

    const color = '#2E75B6'; // blue dot for now (no signal quality without BS)
    const icon  = carIcon(color);
    const pos   = [vehiclePos.lat, vehiclePos.lng];

    if (!groups.current.veh) {
      groups.current.veh = L.marker(pos, { icon, zIndexOffset: 1000 }).addTo(map);
    } else {
      groups.current.veh.setLatLng(pos);
      groups.current.veh.setIcon(icon);
    }

    prevVehPos.current = vehiclePos;
  }, [vehiclePos, showLayers.vehicles]);

  /* ── 다중차량 실험군 — 배경 차량 회색 점 (작게, 마커 풀 재사용) ─────── */
  useEffect(() => {
    const g = groups.current.bgVeh; if (!g) return;
    const list = backgroundVehicles || [];
    if (!showLayers.vehicles || list.length === 0) {
      g.clearLayers();
      bgVehMarkers.current = {};
      return;
    }
    const seen = new Set();
    list.forEach((v) => {
      seen.add(v.id);
      const existing = bgVehMarkers.current[v.id];
      if (existing) {
        existing.setLatLng([v.lat, v.lng]);
      } else {
        bgVehMarkers.current[v.id] = L.circleMarker([v.lat, v.lng], {
          renderer: groups.current.bgVehRenderer,
          radius: 3,
          color: '#9AA5B1',
          weight: 0,
          fillColor: '#9AA5B1',
          fillOpacity: 0.65,
          interactive: false,
        }).addTo(g);
      }
    });
    // 더 이상 존재하지 않는 배경 차량 마커는 제거
    Object.keys(bgVehMarkers.current).forEach((id) => {
      if (!seen.has(id)) {
        bgVehMarkers.current[id].remove();
        delete bgVehMarkers.current[id];
      }
    });
  }, [backgroundVehicles, showLayers.vehicles]);

  /* ── mode interaction handlers ───────────────────────────────── */
  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    if (mode === 'area') {
      map.dragging.disable();
      map.getContainer().style.cursor = 'crosshair';
      let start = null;
      const onDown = (e) => {
        start = e.latlng;
        if (groups.current.areaRect) groups.current.areaRect.remove();
        groups.current.areaRect = L.rectangle([start, start], {
          color: '#1E3A5F', weight: 1.6, dashArray: '6 4',
          fillColor: '#2E75B6', fillOpacity: 0.10
        }).addTo(map);
      };
      const onMove = (e) => {
        if (start && groups.current.areaRect)
          groups.current.areaRect.setBounds(L.latLngBounds(start, e.latlng));
      };
      const onUp = (e) => {
        if (!start) return;
        const b = L.latLngBounds(start, e.latlng);
        start = null;
        finalizeArea(b);
      };
      map.on('mousedown', onDown);
      map.on('mousemove', onMove);
      map.on('mouseup',   onUp);
      return () => {
        map.off('mousedown', onDown);
        map.off('mousemove', onMove);
        map.off('mouseup',   onUp);
        map.dragging.enable();
        map.getContainer().style.cursor = '';
      };
    }
    if (mode === 'origin' || mode === 'dest') {
      map.getContainer().style.cursor = 'crosshair';
      const onClick = (e) => {
        const ll = { lat: e.latlng.lat, lng: e.latlng.lng };
        if (mode === 'origin') { setOrigin(ll); setOriginDone(false); }
        else                   { setDest(ll);   setDestDone(false);   }
      };
      map.on('click', onClick);
      return () => { map.off('click', onClick); map.getContainer().style.cursor = ''; };
    }
    if (mode === 'bs_create') {
      map.getContainer().style.cursor = 'crosshair';
      const onClick = (e) => { createStation(e.latlng.lat, e.latlng.lng); };
      map.on('click', onClick);
      return () => { map.off('click', onClick); map.getContainer().style.cursor = ''; };
    }
    if (mode === 'bs_delete') {
      map.getContainer().style.cursor = 'crosshair';
      return () => { map.getContainer().style.cursor = ''; };
    }
  }, [mode]);

  /* ── user-created base station create / delete ───────────────── */
  async function createStation(lat, lng) {
    setStationsErr(null);
    try {
      const res = await fetch(`${api}/network-nodes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lng, node_type: 'base_station' }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || '기지국 생성 실패');
      setStations(s => [...s, body]);
    } catch (e) {
      setStationsErr(e.message);
    }
  }

  async function deleteStation(id) {
    setStationsErr(null);
    try {
      const res = await fetch(`${api}/network-nodes/${id}`, { method: 'DELETE' });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || '기지국 삭제 실패');
      setStations(s => s.filter(st => st.id !== id));
    } catch (e) {
      setStationsErr(e.message);
    }
  }

  async function resetUserStations() {
    setStationsErr(null);
    try {
      const res = await fetch(`${api}/network-nodes/reset-user-created`, { method: 'POST' });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || '기지국 초기화 실패');
      setStations([]);
    } catch (e) {
      setStationsErr(e.message);
    }
  }

  async function reapplyPlacement() {
    setStationsErr(null);
    try {
      const res = await fetch(`${api}/network-nodes/reapply-placement`, { method: 'POST' });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || '재배치 실패');
      // 이동된 좌표로 stations 상태 갱신
      const updated = body.nodes || [];
      setStations(prev => prev.map(s => {
        const u = updated.find(n => n.id === s.id);
        return u ? { ...s, lat: u.lat, lng: u.lng, antenna_height_m: u.antenna_height_m, antenna_placement: u.placement_type } : s;
      }));
    } catch (e) {
      setStationsErr(e.message);
    }
  }

  /* ── finalizeArea — real OSM + netconvert via backend ────────── */
  async function finalizeArea(bounds) {
    setArea({ s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() });
    setMode(null);
    setOsmError(null);
    setOsmWarning(null);
    setSimNotice(null);
    setOsmStage(1); // downloading

    try {
      const res = await fetch(`${api}/api/setup-network`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bbox: { s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() }
        }),
      });

      setOsmStage(2); // converting (response received means OSM done, converting is fast)

      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || 'Network setup failed');
      }

      if (body.warning) setOsmWarning(body.warning);
      setOsmStage(3); // ready
      if (mapObj.current) mapObj.current.fitBounds(bounds, { padding: [50, 50] });
      setTimeout(() => setOsmStage(0), 1200);

    } catch (e) {
      setOsmError(e.message);
      setOsmStage(0);
      setArea(null);
    }
  }

  /* ── ITS 첨두/비첨두 교통 동기화 (Pro 전용) ───────────────────── */
  async function syncTraffic() {
    if (!area) return;
    setTrafficSyncing(true);
    setTrafficSyncError(null);
    try {
      const res = await fetch(`${api}/traffic/sync-its`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bbox: { minX: area.w, maxX: area.e, minY: area.s, maxY: area.n },
          time_period: trafficPeriod,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'ITS 동기화 실패');
      setTrafficSyncInfo(body);
    } catch (e) {
      setTrafficSyncError(e.message);
    } finally {
      setTrafficSyncing(false);
    }
  }

  /* ── button actions ──────────────────────────────────────────── */
  const tryArea   = () => setMode(mode === 'area' ? null : 'area');
  const tryOrigin = () => {
    if (!area) return;
    if (mode === 'origin') { if (origin) { setOriginDone(true); setMode(null); } }
    else setMode('origin');
  };
  const tryDest = () => {
    if (!area) return;
    if (mode === 'dest') { if (dest) { setDestDone(true); setMode(null); } }
    else setMode('dest');
  };
  const tryBsCreate = () => { if (area) setMode(mode === 'bs_create' ? null : 'bs_create'); };
  const tryBsDelete = () => { if (area) setMode(mode === 'bs_delete' ? null : 'bs_delete'); };

  // Lite 전용 예시 시나리오 프리셋 — 학부생이 출발지/도착지를 직접 찍지 않아도 현재 구역
  // 안에서 바로 시작할 수 있게, 구역(area) 내부 좌표를 비율로 계산해 채운다(특정 도시
  // 좌표를 하드코딩하지 않으므로 어떤 구역을 그려도 항상 동작한다).
  function applyPreset(kind) {
    if (!area) return;
    const { s, w, n, e } = area;
    const latSpan = n - s, lngSpan = e - w;
    const pt = (fLat, fLng) => ({ lat: s + latSpan * fLat, lng: w + lngSpan * fLng });
    if (kind === 'short') {
      setOrigin(pt(0.35, 0.35)); setOriginDone(true);
      setDest(pt(0.55, 0.55)); setDestDone(true);
      setVehicleCount(1);
    } else if (kind === 'congested') {
      setOrigin(pt(0.15, 0.15)); setOriginDone(true);
      setDest(pt(0.85, 0.85)); setDestDone(true);
      setVehicleCount(40);
    }
  }

  async function handleStart() {
    if (!ready) return;
    setSimError(null);
    setSimNotice(null);

    // 일시정지 상태이면 재개 (vehiclePos/routeCoords 유지)
    if (!sim.running && sim.elapsed > 0) {
      try {
        const res = await fetch(`${api}/api/simulation/resume`, { method: 'POST' });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || '재개 실패');
        dispatch({ type: 'start' });
      } catch (e) {
        setSimError(e.message);
      }
      return;
    }

    // 새 시뮬레이션 시작
    setRouteCoords([]);
    setVehiclePos(null);

    try {
      const res = await fetch(`${api}/api/simulation/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin,
          dest,
          algorithm_config: selectedAlgorithms,
          simulation_config: {
            ...(simConfig || {}),
            policy_options: { ...(simConfig?.policy_options || {}), network_mode: networkGen.toUpperCase() },
          },
          vehicle_count: vehicleCount,
          // DB simulation_runs에 이 런을 시트별로 태깅 — 시트 이름으로 분리해서 조회/비교할 수 있게.
          sheet_id: sheets[activeSheetIdx]?.id || null,
          sheet_name: sheets[activeSheetIdx]?.name || null,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || '시뮬레이션 시작 실패');
      }
      currentRunIdRef.current = body.run_id ?? null;
      if (body.warning) setSimNotice(body.warning);
      dispatch({ type: 'start' });
    } catch (e) {
      setSimError(e.message);
    }
  }

  async function handleStop() {
    await fetch(`${api}/api/simulation/stop`, { method: 'POST' });
    dispatch({ type: 'pause' });
    if (vehiclePos) captureResultIntoActiveSheet(); // 도착 전 정지해도 그 시점 결과를 시트에 남김
  }

  async function clearAll() {
    try {
      await fetch(`${api}/api/simulation/reset`, { method: 'POST' });
    } catch (_) {}
    setMode(null); setArea(null);
    setOrigin(null); setOriginDone(false);
    setDest(null);   setDestDone(false);
    setOsmStage(0);  setOsmError(null);
    setOsmWarning(null);
    setSimError(null);
    setSimNotice(null);
    setNetworkTelemetry(null);
    setRouteCoords([]); setVehiclePos(null);
    setVehicleCount(1);
    prevArrived.current = false;
    if (setBackgroundVehicles) setBackgroundVehicles([]);
    // 현재 시트도 빈 draft 상태로 되돌린다 — 안 그러면 화면은 지워져도 시트에 저장된 이전
    // config/result가 그대로 남아있어, 새로고침하거나 이 시트로 다시 돌아오면 방금 지운
    // 결과(연결선·latency 등)가 되살아난다. 기지국 배치는 별도 자원이라 건드리지 않음.
    setSheets(prev => {
      const next = prev.map((s, i) => i === activeSheetIdx ? {
        ...s,
        config: { origin: null, dest: null, vehicleCount: 1, selectedAlgorithms: DEFAULT_ALGORITHM_SELECTION, networkGen, simConfig },
        result: null,
        status: 'draft',
      } : s);
      saveSimSheets(next);
      return next;
    });
    if (groups.current.areaRect) { groups.current.areaRect.remove(); groups.current.areaRect = null; }
    if (groups.current.veh)      { groups.current.veh.remove();      groups.current.veh = null; }
    if (groups.current.route)    { groups.current.route.clearLayers(); }
    if (groups.current.wp)       { groups.current.wp.clearLayers(); }
    if (groups.current.network)   { groups.current.network.clearLayers(); }
    if (groups.current.connLines){ groups.current.connLines.clearLayers(); }
    if (groups.current.blocks)   { groups.current.blocks.clearLayers(); }
    if (groups.current.bgVeh)    { groups.current.bgVeh.clearLayers(); }
    bgVehMarkers.current = {};
    candidateMarkers.current = {};
    buildingsSigRef.current = null;
    if (mapObj.current) mapObj.current.setView(KR_CENTER, KR_ZOOM);
    dispatch({ type: 'reset' });
  }

  function areaKm2(a) {
    if (!a || !mapObj.current) return 0;
    const w = mapObj.current.distance([a.s, a.w], [a.s, a.e]);
    const h = mapObj.current.distance([a.s, a.w], [a.n, a.w]);
    return w * h / 1e6;
  }

  const hint = (() => {
    if (mode === 'area')   return '지도에서 드래그하여 시뮬레이션 구역을 선택하세요';
    if (mode === 'origin') return origin ? "'출발지'를 눌러 확정 (다시 클릭하면 위치 변경)" : '지도를 클릭해 출발지를 선택하세요';
    if (mode === 'dest')   return dest   ? "'도착지'를 눌러 확정 (다시 클릭하면 위치 변경)"  : '지도를 클릭해 도착지를 선택하세요';
    if (mode === 'bs_create') return '지도에서 기지국을 배치할 위치를 클릭하세요.';
    if (mode === 'bs_delete') return '삭제할 기지국을 클릭하세요.';
    return '';
  })();

  const Lp = ({ on, set, children }) => (
    <label className="row between" style={{ padding: '7px 0', cursor: 'pointer' }}>
      <span style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>{children}</span>
      <Toggle on={on} onChange={set} />
    </label>
  );

  const WayRow = ({ color, label, val, done, set }) => (
    <div className="row between" style={{
      padding: '8px 11px', background: 'var(--surface-2)', borderRadius: 9,
      border: '1px solid var(--border)', cursor: area ? 'pointer' : 'default'
    }} onClick={area ? set : undefined}>
      <span className="row gap8" style={{ minWidth: 0 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flex: '0 0 auto' }} />
        <span className="num" style={{ fontSize: 11.5, color: val === '미지정' ? 'var(--ink-4)' : 'var(--ink)' }}>{val}</span>
      </span>
      {done
        ? <Icon.check size={14} style={{ color: 'var(--good)', flex: '0 0 auto' }} />
        : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-4)', flex: '0 0 auto' }}>{label}</span>}
    </div>
  );

  // display-only legend row (map markers + live status; no interaction)
  const LegendRow = ({ shape, color, label, children }) => (
    <div className="row between" style={{ padding: '4px 0' }}>
      <span className="row gap8" style={{ minWidth: 0 }}>
        <span style={{ width: 10, height: 10, borderRadius: shape === 'square' ? 2 : '50%', background: color, flex: '0 0 auto' }} />
        <span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{label}</span>
      </span>
      {children}
    </div>
  );

  const AlgorithmGroup = ({ groupKey, label, options }) => {
    const isOpen = openAlgorithmGroup === groupKey;
    const selected = selectedAlgorithms[groupKey];
    return (
      <div className="col gap8">
        <button
          className="row between"
          style={{
            padding: '8px 11px',
            background: 'var(--surface-2)',
            borderRadius: 9,
            border: '1px solid var(--border)',
            cursor: 'pointer',
            width: '100%',
          }}
          onClick={() => setOpenAlgorithmGroup(isOpen ? null : groupKey)}
        >
          <span className="row gap8" style={{ minWidth: 0 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: '#8C6CF6', display: 'inline-block' }} />
            <span style={{ fontSize: 11.5, fontWeight: 600 }}>{label}</span>
          </span>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
            {formatAlgorithmName(selected)}
          </span>
        </button>
        {isOpen && (
          <div className="col gap6" style={{ paddingLeft: 4 }}>
            {options.map((option) => (
              <button
                key={option}
                className="btn sm"
                style={{
                  justifyContent: 'space-between',
                  background: selected === option ? undefined : 'var(--surface-2)',
                  color: selected === option ? undefined : 'var(--ink-2)',
                  borderColor: 'var(--border)',
                }}
                onClick={() => {
                  setSelectedAlgorithms((prev) => ({ ...prev, [groupKey]: option }));
                }}
              >
                <span className="row gap6">
                  {formatAlgorithmName(option)}
                  {UNIMPLEMENTED_ROUTE_ALGORITHMS.has(option) && (
                    <span className="chip" style={{ fontSize: 9, padding: '1px 5px' }}>미구현</span>
                  )}
                </span>
                {selected === option ? <Icon.check size={12} /> : null}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="fade" style={{ position: 'relative', height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
    <div style={{ position: 'relative', flex: 1, overflow: 'hidden' }}>
      {/* ── MAP — full width, panels float on top ───────── */}
      <div style={{ position: 'absolute', inset: 0 }}>
        <div ref={mapRef} style={{ position: 'absolute', inset: 0 }} />

        {/* legend top-left — display only (controls live in the right panel).
            Wrapper itself has pointer-events:none so the dead space between/around
            the boxes (its bounding rect is as wide as the widest child, e.g. the
            "오른쪽 패널에서…" chip, even where the legend box above doesn't reach)
            doesn't swallow clicks meant for the map underneath — each visible box
            opts back in with pointer-events:auto. */}
        <div style={{ position: 'absolute', top: 14, left: 14, zIndex: 600, display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start', maxWidth: 'calc(100% - 28px)', pointerEvents: 'none' }}>
          <div style={{ background: '#fff', borderRadius: 12, boxShadow: 'var(--sh-2)', padding: '10px 14px', minWidth: 176, pointerEvents: 'auto' }}>
            <div className="row gap8" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 6, whiteSpace: 'nowrap' }}>
              <Icon.map size={13} /> 범례 <span className="en" style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--ink-4)' }}>LEGEND</span>
            </div>
            <LegendRow shape="square" color="#1E3A5F" label="구역">
              {area ? <Icon.check size={13} style={{ color: 'var(--good)' }} /> : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-4)' }}>대기</span>}
            </LegendRow>
            <LegendRow shape="circle" color="#1F9D57" label="출발지">
              {originDone ? <Icon.check size={13} style={{ color: 'var(--good)' }} /> : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-4)' }}>—</span>}
            </LegendRow>
            <LegendRow shape="circle" color="#E0463C" label="도착지">
              {destDone ? <Icon.check size={13} style={{ color: 'var(--good)' }} /> : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-4)' }}>—</span>}
            </LegendRow>
            <LegendRow shape="circle" color="#1E88E5" label="기지국">
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{stations.length}개</span>
            </LegendRow>
            {vehicleCount > 1 && (
              <LegendRow shape="circle" color="#9AA5B1" label="배경 차량">
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{backgroundVehicles?.length || 0}대</span>
              </LegendRow>
            )}
          </div>

          {hint && (
            <div className="chip brand" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px', pointerEvents: 'auto' }}>
              <Icon.pin size={13} /> {hint}
            </div>
          )}
          {!area && !mode && (
            <div className="chip" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px', color: 'var(--ink-3)', pointerEvents: 'auto' }}>
              <Icon.layers size={13} /> 오른쪽 패널에서 구역을 설정해 시작하세요
            </div>
          )}
        </div>

        {/* layer panel bottom-left */}
        <div style={{ position: 'absolute', bottom: 14, left: 14, zIndex: 600, background: '#fff', borderRadius: 12, boxShadow: 'var(--sh-2)', padding: '10px 14px', minWidth: 188 }}>
          <div className="row gap8" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4, whiteSpace: 'nowrap' }}>
            <Icon.layers size={13} /> 레이어 <span className="en" style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--ink-4)' }}>LAYERS</span>
          </div>
          <Lp on={showLayers.vehicles} set={v => setShowLayers(s => ({ ...s, vehicles: v }))}>차량</Lp>
          <Lp on={showLayers.routes}   set={v => setShowLayers(s => ({ ...s, routes: v }))}>경로</Lp>
          <Lp on={showLayers.stations} set={v => setShowLayers(s => ({ ...s, stations: v }))}>기지국</Lp>
        </div>

        {/* vehicle speed badge */}
        {vehiclePos && !vehiclePos.arrived && (
          <div style={{ position: 'absolute', bottom: 14, right: 14, zIndex: 600, background: '#1E3A5F', borderRadius: 10, padding: '8px 14px', color: '#fff' }}>
            <div className="mono" style={{ fontSize: 10, opacity: 0.6, marginBottom: 2 }}>SPEED</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 700, lineHeight: 1 }}>{vehiclePos.speed || 0}</div>
            <div className="mono" style={{ fontSize: 9, opacity: 0.6 }}>km/h</div>
          </div>
        )}
        {vehiclePos?.arrived && (
          <div style={{ position: 'absolute', bottom: 14, right: 14, zIndex: 600, background: 'var(--good)', borderRadius: 10, padding: '8px 14px', color: '#fff' }}>
            <Icon.check size={18} /> 도착!
          </div>
        )}

        {/* OSM loading overlay */}
        {osmStage > 0 && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 700, background: 'rgba(245,248,250,0.88)', backdropFilter: 'blur(2px)', display: 'grid', placeItems: 'center' }}>
            <div className="card" style={{ width: 400, padding: 24, boxShadow: 'var(--sh-3)' }}>
              <div className="row gap12" style={{ marginBottom: 16 }}>
                {osmStage < 3
                  ? <div className="spin" style={{ width: 22, height: 22, border: '2.5px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%' }} />
                  : <div style={{ width: 22, height: 22, borderRadius: '50%', background: 'var(--good)', display: 'grid', placeItems: 'center', color: '#fff' }}><Icon.check size={14} /></div>}
                <b style={{ fontSize: 14 }}>{osmStage < 3 ? '구역 데이터 준비 중…' : '시뮬레이션 준비 완료'}</b>
              </div>
              {[
                ['OSM 데이터 다운로드 (Overpass API)', 'Downloading OSM'],
                ['SUMO 네트워크 변환 (netconvert)',     'Converting network'],
                ['시뮬레이션 준비 완료',                'Ready'],
              ].map((s, i) => {
                const st = osmStage > i + 1 ? 'done' : osmStage === i + 1 ? 'active' : 'idle';
                return (
                  <div key={i} className="row gap12" style={{ padding: '8px 0', opacity: st === 'idle' ? 0.4 : 1 }}>
                    <div style={{ width: 20, height: 20, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: '0 0 auto', background: st === 'done' ? 'var(--good)' : st === 'active' ? 'var(--brand-2)' : 'var(--surface-3)', color: '#fff' }}>
                      {st === 'done' ? <Icon.check size={12} /> : <span className="num" style={{ fontSize: 10 }}>{i + 1}</span>}
                    </div>
                    <span style={{ fontSize: 12.5, fontWeight: 500 }}>{s[0]}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* error toast */}
        {osmError && (
          <div style={{ position: 'absolute', top: 60, left: '50%', transform: 'translateX(-50%)', zIndex: 800, background: '#E0463C', color: '#fff', borderRadius: 8, padding: '10px 18px', fontSize: 13, boxShadow: 'var(--sh-2)', maxWidth: 400, textAlign: 'center' }}>
            <Icon.warn size={14} /> {osmError}
          </div>
        )}
        {osmWarning && (
          <div style={{ position: 'absolute', top: 60, left: '50%', transform: 'translateX(-50%)', zIndex: 790, background: '#B97B11', color: '#fff', borderRadius: 8, padding: '10px 18px', fontSize: 13, boxShadow: 'var(--sh-2)', maxWidth: 520, textAlign: 'center' }}>
            <Icon.warn size={14} /> {osmWarning}
          </div>
        )}
        {simError && (
          <div style={{ position: 'absolute', top: 60, left: '50%', transform: 'translateX(-50%)', zIndex: 800, background: '#E0463C', color: '#fff', borderRadius: 8, padding: '10px 18px', fontSize: 13, boxShadow: 'var(--sh-2)', maxWidth: 400, textAlign: 'center' }}>
            <Icon.warn size={14} /> {simError}
          </div>
        )}
        {simNotice && (
          <div style={{ position: 'absolute', top: 110, left: '50%', transform: 'translateX(-50%)', zIndex: 790, background: '#1E3A5F', color: '#fff', borderRadius: 8, padding: '10px 18px', fontSize: 13, boxShadow: 'var(--sh-2)', maxWidth: 520, textAlign: 'center' }}>
            <Icon.warn size={14} /> {simNotice}
          </div>
        )}
      </div>

      {/* ── FAB 스택 — 컨트롤 패널 / 시뮬레이션 챗봇을 지도 우상단에 아이콘으로 ─ */}
      <div style={{ position: 'absolute', right: 14, top: 14, zIndex: 650, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <FabButton
          icon="sliders"
          active={openPanel === 'control'}
          onClick={() => setOpenPanel(p => p === 'control' ? null : 'control')}
          title="컨트롤 패널"
        />
        <FabButton
          icon="spark"
          active={openPanel === 'scenario'}
          onClick={() => setOpenPanel(p => p === 'scenario' ? null : 'scenario')}
          title="시뮬레이션 챗봇"
        />
      </div>

      {/* ── 플로팅 패널 — FAB 클릭 시 아이콘 옆에서 펼쳐짐 ──────────── */}
      {openPanel && (
        <div style={{
          position: 'absolute', top: 14, bottom: 14, right: 64,
          width: 360, zIndex: 660,
          background: 'var(--surface)', borderRadius: 14, boxShadow: 'var(--sh-3)',
          border: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {openPanel === 'control' ? (
            <>
              <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)', flex: '0 0 auto' }}>
                <div className="eyebrow">Control Panel</div>
                <div className="row between" style={{ marginTop: 4 }}>
                  <b style={{ fontSize: 15, whiteSpace: 'nowrap' }}>시뮬레이션 제어</b>
                  <div className="row gap8">
                    <span className={'status-badge ' + (sim.running ? 'running' : sim.elapsed > 0 ? 'paused' : 'idle')}>
                      <span className="dot" />{sim.running ? '실행 중' : sim.elapsed > 0 ? '일시정지' : '대기'}
                    </span>
                    <button className="btn icon sm" onClick={() => setOpenPanel(null)} title="닫기">✕</button>
                  </div>
                </div>
              </div>
              <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 18, flex: 1, overflowY: 'auto' }}>
          {/* step chips */}
          <div className="row gap8" style={{ flexWrap: 'wrap' }}>
            {[['구역', !!area], ['출발지', originDone], ['도착지', destDone]].map(([l, ok]) => (
              <span key={l} className={'chip ' + (ok ? 'good' : '')} style={{ fontSize: 10.5 }}>
                {ok && <Icon.check size={11} />} {l}
              </span>
            ))}
          </div>

          {/* area */}
          <div className="col gap8">
            <div className="field"><label>구역 선택 <span className="en">AREA / BBOX</span></label></div>
            {area ? (
              <>
                <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>{areaKm2(area).toFixed(2)} km²</div>
                  <Chip tone="good" dot>선택됨</Chip>
                </div>
                {areaKm2(area) > 5 && (
                  <div className="row gap8" style={{ padding: '8px 11px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--warn)' }}>
                    <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
                    {areaKm2(area) > MAX_SETUP_AREA_KM2 ? `선택 구역이 너무 큽니다. ${MAX_SETUP_AREA_KM2}km² 이하로 줄여주세요` : '동 단위 이하로 선택을 권장합니다'}
                  </div>
                )}
                <button className="btn sm" onClick={() => setMode('area')}><Icon.layers size={13} /> 구역 다시 그리기</button>
              </>
            ) : (
              <>
                <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                  <div className="mono muted" style={{ fontSize: 11 }}>구역 미설정</div>
                  <Chip>대기</Chip>
                </div>
                <button className={'btn sm ' + (mode === 'area' ? 'accent' : 'primary')} onClick={tryArea}>
                  <Icon.pin size={13} /> {mode === 'area' ? '드래그하여 선택…' : '지도에서 구역 그리기'}
                </button>
              </>
            )}
          </div>

          {/* ITS 첨두/비첨두 교통 동기화 — Pro 전용 (학부생 대상 Lite는 기본값 '첨두시'를 조용히 사용) */}
          {appMode === 'pro' && (
            <div className="field">
              <label>실시간 교통 데이터 <span className="en">ITS TRAFFIC</span></label>
              <Seg value={trafficPeriod} onChange={(v) => {
                setTrafficPeriod(v);
                setSimConfig(cfg => ({ ...(cfg || {}), policy_options: { ...(cfg?.policy_options || {}), traffic_time_period: v } }));
              }} options={[{ v: 'peak', label: '첨두시' }, { v: 'off_peak', label: '비첨두시' }]} />
              <div className="row gap8" style={{ marginTop: 8 }}>
                <button className="btn sm" disabled={!area || trafficSyncing} onClick={syncTraffic}>
                  {trafficSyncing ? '동기화 중…' : <><Icon.reset size={13} /> ITS 동기화</>}
                </button>
                {trafficSyncInfo?.last_sync_time && (
                  <Chip tone="good" dot>마지막 동기화 {new Date(trafficSyncInfo.last_sync_time).toLocaleTimeString('ko-KR')}</Chip>
                )}
              </div>
              {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>먼저 구역을 설정하세요</div>}
              {trafficSyncError && <div style={{ fontSize: 10.5, marginTop: 4, color: 'var(--bad)' }}>{trafficSyncError}</div>}
            </div>
          )}

          {/* waypoints */}
          <div className="field">
            <label>경로 지점 <span className="en">WAYPOINTS</span></label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <WayRow color="var(--m-origin)" label="출발지" val={origin ? coordStr(origin) : '미지정'} done={originDone} set={tryOrigin} />
              <WayRow color="var(--m-dest)"   label="도착지" val={dest   ? coordStr(dest)   : '미지정'} done={destDone}   set={tryDest} />
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>

          {/* 예시 시나리오 프리셋 — Lite 전용(Pro는 출발/도착지를 직접 지정하는 워크플로를 유지) */}
          {appMode === 'lite' && (
          <div className="field">
            <label>예시 시나리오 <span className="en">EXAMPLE SCENARIOS</span></label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <button className="btn sm" disabled={!area} onClick={() => applyPreset('short')}>
                <Icon.route size={13} /> 가벼운 예시 (차량 1대)
              </button>
              <button className="btn sm" disabled={!area} onClick={() => applyPreset('congested')}>
                <Icon.route size={13} /> 혼잡한 예시 (차량 40대)
              </button>
              <div className="muted" style={{ fontSize: 10.5 }}>현재 구역 안에서 출발지·도착지를 자동으로 채웁니다. 직접 지도를 클릭해 바꿀 수도 있습니다.</div>
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>
          )}

          {/* multi-vehicle experimental group — background vehicle count */}
          <div className="field">
            <label>다중 차량 대수 <span className="en">VEHICLE COUNT</span></label>
            <input
              className="input"
              type="number"
              min="1"
              max="20000"
              step="1"
              value={vehicleCount}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                setVehicleCount(Number.isFinite(v) ? Math.max(1, v) : 1);
              }}
              onBlur={(e) => {
                const v = parseInt(e.target.value, 10);
                setVehicleCount(Number.isFinite(v) && v >= 1 ? v : 1);
              }}
              style={{ width: '100%' }}
            />
            {vehicleCount > 1 && (
              <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>
                타겟 차량 1대 + 배경 차량 {vehicleCount - 1}대가 구역 안을 무작위로 이동하며 기지국 자원할당에 함께 반영됩니다.
              </div>
            )}
            {vehicleCount > 2000 && (
              <div className="muted" style={{ fontSize: 10.5, marginTop: 4, color: 'var(--warn)' }}>
                대수가 많을수록 도로 규모에 비해 비현실적인 혼잡이 발생할 수 있습니다 (시작 시 서버가 안내 메시지를 표시합니다).
              </div>
            )}
          </div>

          {/* network generation — UI only (not wired to backend) */}
          <div className="field">
            <label>네트워크 세대 <span className="en">NETWORK GEN</span></label>
            <div className="seg" style={{ display: 'flex', width: '100%' }}>
              {[['4g', '4G'], ['5g', '5G'], ['6g', '6G-like']].map(([v, lbl]) => (
                <button key={v} className={networkGen === v ? 'active' : ''} style={{ flex: 1 }} onClick={() => setNetworkGen(v)}>
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {/* base stations — Pro 전용 (Lite는 자동 배치된 기지국을 그대로 사용) */}
          {appMode === 'pro' && (
          <div className="field">
            <label>기지국 <span className="en">BASE STATIONS</span></label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                <span className="row gap8" style={{ minWidth: 0 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#1E88E5', flex: '0 0 auto' }} />
                  <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>배치됨 {stations.length}개</span>
                </span>
              </div>
              <div className="row gap8">
                <button className={'btn sm ' + (mode === 'bs_create' ? 'accent' : '')} style={{ flex: 1 }} disabled={!area} onClick={tryBsCreate}>
                  <Icon.antenna size={13} /> {mode === 'bs_create' ? '지도 클릭…' : '생성'}
                </button>
                <button className={'btn sm ' + (mode === 'bs_delete' ? 'accent' : '')} style={{ flex: 1 }} disabled={!area || stations.length === 0} onClick={tryBsDelete}>
                  <Icon.antenna size={13} /> {mode === 'bs_delete' ? '제거할 곳 클릭…' : '제거'}
                </button>
              </div>
              {stations.length > 0 && (
                <div className="row gap8">
                  <button className="btn sm block" onClick={reapplyPlacement} title="기존 기지국을 가장 가까운 건물 옥상으로 재배치합니다" style={{ flex: 1 }}>
                    <Icon.antenna size={13} /> 옥상 재배치
                  </button>
                  <button className="btn sm block" onClick={resetUserStations} title="사용자 지정 기지국만 모두 제거합니다 (시뮬레이션 시나리오는 유지)" style={{ flex: 1 }}>
                    <Icon.reset size={13} /> 초기화 ({stations.length})
                  </button>
                </div>
              )}
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>
          )}

          {/* algorithms — Pro 전용 (Lite는 simConfig의 기본 알고리즘을 그대로 사용) */}
          {appMode === 'pro' && (
          <div className="field">
            <label>알고리즘 <span className="en">ALGORITHMS</span></label>
            <div className="col gap8">
              <AlgorithmGroup groupKey="route" label="경로 알고리즘" options={ROUTE_ALGORITHMS} />
              <AlgorithmGroup groupKey="latency" label="지연시간 알고리즘" options={LATENCY_ALGORITHMS} />
              <AlgorithmGroup groupKey="base_station_selection" label="기지국 선택 알고리즘" options={BS_SELECTION_ALGORITHMS} />
              <AlgorithmGroup groupKey="resource_allocation" label="자원할당 알고리즘" options={RESOURCE_ALLOCATION_ALGORITHMS} />
            </div>
          </div>
          )}

          {/* 파라미터 스윕(민감도 분석) — Pro 전용. 결과는 분석보고서 탭 "시나리오 배치 비교"에서 확인 */}
          {appMode === 'pro' && (
          <div className="field">
            <label>파라미터 스윕 <span className="en">SENSITIVITY SWEEP</span></label>
            <div className="col gap8" style={{ opacity: ready ? 1 : 0.5 }}>
              <select className="input" value={sweepParam} onChange={e => selectSweepParam(e.target.value)} style={{ width: '100%' }}>
                {SWEEP_PARAMS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
              </select>
              <div className="row gap8">
                <input className="input" type="number" step={SWEEP_PARAMS.find(p => p.key === sweepParam)?.step ?? 0.1}
                  value={sweepFrom} onChange={e => setSweepFrom(parseFloat(e.target.value) || 0)} style={{ flex: 1 }} placeholder="시작값" />
                <input className="input" type="number" step={SWEEP_PARAMS.find(p => p.key === sweepParam)?.step ?? 0.1}
                  value={sweepTo} onChange={e => setSweepTo(parseFloat(e.target.value) || 0)} style={{ flex: 1 }} placeholder="끝값" />
                <input className="input" type="number" min="2" max="10" value={sweepSteps}
                  onChange={e => setSweepSteps(parseInt(e.target.value, 10) || 2)} style={{ width: 56 }} title="구간 수" />
              </div>
              <button className={'btn sm ' + (sweepDone ? 'good' : '')} disabled={!ready || sweepRunning} onClick={runParamSweep}>
                {sweepRunning ? <><Icon.reset size={13} className="spin" /> 실행 중…</> : sweepDone ? <><Icon.check size={13} /> 완료 — 분석보고서에서 확인</> : <><Icon.compare size={13} /> 스윕 실행 ({Math.max(2, Math.min(10, sweepSteps || 2))}개)</>}
              </button>
              {!ready && <div className="muted" style={{ fontSize: 10.5 }}>구역·출발지·도착지를 먼저 설정하세요</div>}
              {sweepError && <div style={{ fontSize: 10.5, color: 'var(--bad)' }}>{sweepError}</div>}
            </div>
          </div>
          )}

          {/* RL 정책 비교(실험적) — Pro 전용. 결과는 분석보고서 탭 "시나리오 배치 비교"에서 확인 */}
          {appMode === 'pro' && (
          <div className="field">
            <label>RL 정책 비교 <span className="en">RL POLICY COMPARE (EXPERIMENTAL)</span></label>
            <div className="col gap8" style={{ opacity: ready ? 1 : 0.5 }}>
              <div className="muted" style={{ fontSize: 10.5 }}>random / greedy / coverage 베이스라인 정책을 같은 출발·도착지로 각 20회 평가해 평균±표준편차로 비교합니다.</div>
              <button className={'btn sm ' + (rlDone ? 'good' : '')} disabled={!ready || rlRunning} onClick={runRLComparison}>
                {rlRunning ? <><Icon.reset size={13} className="spin" /> 실행 중…</> : rlDone ? <><Icon.check size={13} /> 완료 — 분석보고서에서 확인</> : <><Icon.spark size={13} /> RL 정책 비교 실행</>}
              </button>
              {!ready && <div className="muted" style={{ fontSize: 10.5 }}>구역·출발지·도착지를 먼저 설정하세요</div>}
              {rlError && <div style={{ fontSize: 10.5, color: 'var(--bad)' }}>{rlError}</div>}
            </div>
          </div>
          )}

          {/* progress bar */}
          {vehiclePos && (
            <div className="col gap6">
              <div className="row between" style={{ fontSize: 11.5 }}>
                <span>경로 진행률</span>
                <span className="num">{Math.round((vehiclePos.progress || 0) * 100)}%</span>
              </div>
              <div style={{ height: 6, borderRadius: 3, background: 'var(--surface-3)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(vehiclePos.progress || 0) * 100}%`, background: 'var(--brand-2)', borderRadius: 3, transition: 'width 0.3s ease' }} />
              </div>
            </div>
          )}

          {networkTelemetry && (
            <div className="card" style={{ padding: 14, background: 'var(--surface-2)' }}>
              {/* header */}
              <div className="row between" style={{ marginBottom: 10 }}>
                <b style={{ fontSize: 13 }}>기지국 연결 상태</b>
                <span className="chip" style={{ fontSize: 10 }}>
                  {networkTelemetry.ego_vehicle?.connected_network_node_name || networkTelemetry.connected_node?.name || networkTelemetry.connected_node?.id}
                </span>
              </div>

              {/* 연결 정보 */}
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>연결 기지국</span>
                <span className="num">
                  {networkTelemetry.ego_vehicle?.connected_network_node_name || networkTelemetry.connected_node?.name || networkTelemetry.connected_node?.id}
                </span>
              </div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>기지국까지 거리</span>
                <span className="num">
                  {(networkTelemetry.distance_m?.toFixed?.(1) ?? networkTelemetry.distance_m)} m
                </span>
              </div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>혼잡도</span>
                <span className="num">{networkTelemetry.connected_node?.congestion_score ?? '—'}</span>
              </div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 10 }}>
                <span>예상 지연시간</span>
                <span className="num">
                  {networkTelemetry.ego_vehicle?.current_latency_ms ?? networkTelemetry.latency_ms} ms
                </span>
              </div>

              {/* 신호 품질 구분선 */}
              <div style={{ borderTop: '1px solid var(--border)', marginBottom: 8 }} />
              <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 6 }}>신호 품질</div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>교차 건물</span>
                <span className="num">{networkTelemetry.intersected_building_count ?? '—'} 개</span>
              </div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>최대 건물 높이</span>
                <span className="num">{networkTelemetry.max_building_height_m ?? '—'} m</span>
              </div>
              <div className="row between" style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span>신호 손실</span>
                <span className="num">{networkTelemetry.estimated_penetration_loss_db ?? '—'} dB</span>
              </div>
              <div className="row between" style={{ fontSize: 11.5 }}>
                <span>안정성 점수</span>
                <span className="num">{networkTelemetry.stability_score ?? '—'}</span>
              </div>

              {/* 후보 기지국 Top 3 */}
              {!!(networkTelemetry.candidate_nodes || []).length && (
                <>
                  <div style={{ borderTop: '1px solid var(--border)', margin: '10px 0 8px' }} />
                  <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginBottom: 6 }}>후보 기지국 Top 3</div>
                  <div className="col gap6">
                    {networkTelemetry.candidate_nodes.slice(0, 3).map((c, i) => (
                      <div key={c.id} className="row between" style={{
                        fontSize: 11, padding: '6px 9px', borderRadius: 7,
                        background: i === 0 ? 'var(--surface)' : 'transparent',
                        border: '1px solid var(--border)',
                      }}>
                        <span className="row gap8">
                          <span className="mono" style={{ color: 'var(--ink-4)' }}>{i + 1}</span>
                          <span>{c.name}</span>
                        </span>
                        <span className="num" style={{ color: 'var(--ink-3)' }}>
                          {c.distance_m?.toFixed?.(0) ?? c.distance_m} m · {c.predicted_latency_ms} ms
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {/* 이 시트의 로그 — 설계 B: 분석보고서 탭과는 별개로, 시트별 로그는 여기서만 읽기전용으로 보여준다 */}
          {displayedSheetLogs.length > 0 && (
            <div className="card" style={{ padding: 14, background: 'var(--surface-2)' }}>
              <div className="row between" style={{ marginBottom: 8 }}>
                <b style={{ fontSize: 13 }}>{sheets[activeSheetIdx]?.name || '시트'}의 로그</b>
                <Chip>{displayedSheetLogs.length}건</Chip>
              </div>
              <div className="col gap6" style={{ maxHeight: 180, overflowY: 'auto' }}>
                {[...displayedSheetLogs].reverse().slice(0, 30).map((l, i) => (
                  <div key={i} className="row gap8" style={{ fontSize: 11, padding: '5px 8px', background: 'var(--surface)', borderRadius: 6 }}>
                    <span className="num muted" style={{ fontSize: 10, flex: '0 0 auto' }}>{l.t}</span>
                    <span style={{ flex: 1, lineHeight: 1.4 }}>{l.ko}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div style={{ flex: 1 }} />

          {/* controls */}
          <div className="col gap8" style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <div className="row gap8">
              {!sim.running
                ? <button className="btn good block" disabled={!ready || osmStage > 0} onClick={handleStart}>
                    <Icon.play size={15} /> {sim.elapsed > 0 ? '재개' : '시작'}
                  </button>
                : <button className="btn block" style={{ borderColor: 'var(--warn-line)', color: 'var(--warn)' }} onClick={handleStop}>
                    <Icon.pause size={15} /> 정지
                  </button>}
              <button className="btn icon" onClick={clearAll} title="시나리오 초기화"><Icon.reset size={15} /></button>
            </div>
            {stationsErr && (
              <div style={{ padding: '8px 11px', background: 'var(--warn-tint)', border: '1px solid var(--warn-line)', borderRadius: 9, color: 'var(--warn)', fontSize: 11 }}>
                {stationsErr}
              </div>
            )}
            {!ready && !sim.running && (
              <div className="muted" style={{ fontSize: 10.5, textAlign: 'center' }}>
                구역 · 출발지 · 도착지를 확정하면 시작할 수 있습니다
              </div>
            )}
            <div className="row between" style={{ padding: '11px 13px', background: 'var(--brand)', borderRadius: 10, color: '#fff' }}>
              <span style={{ fontSize: 11, opacity: 0.7 }}>경과 시간 <span className="mono">ELAPSED</span></span>
              <span className="num" style={{ fontSize: 19, fontWeight: 600 }}>{fmtClock(sim.elapsed)}</span>
            </div>
            {(osmWarning || simNotice) && (
              <div style={{ padding: '10px 12px', background: 'var(--warn-tint)', border: '1px solid var(--warn-line)', borderRadius: 10, color: 'var(--warn)', fontSize: 11.5 }}>
                현재 시스템에서는 SUMO 대신 OSM fallback mode가 사용될 수 있습니다.
              </div>
            )}
          </div>
              </div>
            </>
          ) : (
            <>
              <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)', flex: '0 0 auto' }}>
                <div className="eyebrow">Simulation Chatbot</div>
                <div className="row between" style={{ marginTop: 4 }}>
                  <b style={{ fontSize: 15, whiteSpace: 'nowrap' }}>시뮬레이션 챗봇</b>
                  <button className="btn icon sm" onClick={() => setOpenPanel(null)} title="닫기">✕</button>
                </div>
              </div>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <SimulationChatPanel simConfig={simConfig} setSimConfig={setSimConfig} />
              </div>
            </>
          )}
        </div>
      )}
    </div>

    <SheetTabBar
      sheets={sheets}
      activeIdx={activeSheetIdx}
      onSwitch={switchToSheet}
      onAdd={addSheet}
      onRename={renameSheet}
      onRemove={removeSheet}
      onRunBatch={runAllSheetsAsBatch}
      batchRunning={batchRunning}
      batchError={batchError}
    />
    </div>
  );
}
window.SimulationTab = SimulationTab;
