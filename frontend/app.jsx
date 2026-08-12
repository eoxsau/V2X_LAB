/* ============================================================ App shell + state */
const API = window.location.origin;
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

/* started: 한 번이라도 실행되면 true, 초기화(reset)해야만 false로 돌아간다.
   실행 설정(알고리즘·가중치·네트워크 세대)은 "초기화된 상태"에서만 바꿀 수 있어야 하므로,
   running만으로는 부족하다 — 일시정지(pause)도 running=false지만 이미 시작된 런이라
   설정을 바꾸면 주행 경로(옛 설정)와 텔레메트리(새 설정)가 섞인다. configLocked() 참조. */
function simReducer(s, a) {
  switch (a.type) {
    case 'start':   return { ...s, running: true, started: true, finished: false };
    case 'pause':   return { ...s, running: false };
    /* 도착으로 **끝난** 런. 일시정지와 반드시 구분해야 한다 — 도착한 런은 재개할 것이
       없는데도 상태가 같으면 버튼이 '재개'로 뜬다(2026-07-29 사용자 보고). */
    case 'finish':  return { ...s, running: false, finished: true };
    case 'reset':   return { ...s, running: false, started: false, finished: false, elapsed: 0, tick: 0 };
    case 'tick':    return { ...s, elapsed: s.elapsed + 1, tick: s.tick + 1 };
    case 'mode':    return { ...s, mode: a.v };
    default: return s;
  }
}

/* 실행 설정 잠금 여부 — 실행 중이거나 이미 시작된 런이 있으면 true.
   해제하려면 "전체 초기화"로 리셋해야 한다. */
function configLocked(sim) {
  return !!(sim && (sim.running || sim.started));
}

function App() {
  const [tab, setTab] = useState(() => location.hash.replace('#', '') || 'simulation');
  const [sim, dispatch] = React.useReducer(simReducer, { running: false, started: false, finished: false, elapsed: 0, tick: 0, mode: '5G' });
  const [bootReady, setBootReady]      = useState(false);

  // Lite/Professional 모드 게이트 — 선택 전엔 LandingPage만 보여준다(아래 return 직전 분기).
  // 페이지를 새로 열거나 새로고침할 때마다 다시 선택하게 한다(저장하지 않음) — 같은 로드 안에서
  // 탭을 오가는 동안에는 메모리에 유지되므로 다시 묻지 않는다. 설정 탭에서도 언제든 바꿀 수 있음.
  const [appMode, setAppMode] = useState(null);
  function chooseMode(m) {
    setAppMode(m);
  }

  // Vehicle state from WebSocket
  const [vehiclePos, setVehiclePos]     = useState(null);
  const [routeCoords, setRouteCoords]   = useState([]);
  const [wsConnected, setWsConnected]   = useState(false);
  const [simNotice, setSimNotice]       = useState(null);
  const [networkTelemetry, setNetworkTelemetry] = useState(null);
  const [routeEdges, setRouteEdges] = useState(null);
  const [backgroundVehicles, setBackgroundVehicles] = useState([]); // 다중차량 실험군 — 배경 차량 [{id,lat,lng}]
  /* 교통 준비(N* 보정 등) 진행 상태 — null이면 준비 중 아님.
     시작을 눌렀을 때 교통이 아직 없으면 백엔드가 기다리지 않고 "preparing"으로 돌려주고,
     준비가 끝나면 스스로 시뮬을 시작한다. 그 사이 화면에 진행 상황을 보여주는 용도. */
  const [trafficPrep, setTrafficPrep] = useState(null);
  /* 준비 후 자동 시작된 런의 DB id — 시작 응답이 "preparing"이라 그때는 받을 수 없었다. */
  const [autoStartRunId, setAutoStartRunId] = useState(null);
  /* 배치 최적화 진행률 {pct, phase}. null이면 돌고 있지 않음. */
  const [placementProgress, setPlacementProgress] = useState(null);
  /* 사용자가 누른 시작이 준비를 기다리는 중인지. 구역 설정 직후의 백그라운드 준비도
     preparing=true지만 그건 아무도 기다리지 않으므로, 끝났다고 실행 상태로 바꾸면 안 된다. */
  const awaitingAutoStart = useRef(false);
  const wsRef = useRef(null);

  // 시뮬레이션 시트 — App으로 끌어올림(tab-simulation.jsx에 있던 걸 옮김). 시뮬레이션 탭뿐 아니라
  // 대시보드 탭도 "지금 실행 중인 시트가 몇 번인지"를 알아야 시트별로 분리해서 보여줄 수 있다.
  const [sheets, setSheets] = useState(() => loadSimSheets());
  const [activeSheetIdx, setActiveSheetIdx] = useState(0);

  // Stage-1 simulation config (persisted to localStorage)
  const DEFAULT_SIM_CONFIG = {
    cost_weights: {
      w_distance: 1.0, w_time: 2.0, w_latency: 3.0, w_load: 1.5,
      w_resource: 1.0, w_handover: 1.0, w_blockage: 1.5, w_future: 2.5,
    },
    algorithm_selection: {
      route_algorithm: 'dijkstra',
      latency_algorithm: 'full_composite_latency',
      base_station_selection_algorithm: 'lowest_latency_bs',
      resource_allocation_algorithm: 'traffic_aware_allocation',
    },
    policy_options: {
      lookahead_k: 3, lookahead_time: 10.0, max_handover_allowed: 10,
      prefer_low_latency: true, prefer_load_balance: false, avoid_disconnection: true,
      // other_device_lambda: 백엔드 SimConfigPolicyOptions 기본값(30)과 맞춘 값.
      // 300은 "총 기기 밀도"이고 실제로 써야 할 건 그 활성 비율 10%인 30이다.
      // 300이면 반경 1km 기지국 하나에 기기 942개가 깔려 5G 수용량 500을 혼자 넘겨,
      // 차가 한 대도 없어도 모든 기지국이 부하 100%가 된다(2026-08-11 실측).
      traffic_lambda: 5.0, other_device_lambda: 30.0, network_mode: '5G',
      // bg_reroute_prob: 기본 끔. 켜면 배경 차량이 도착 전에 목적지를 다시 받아
      // 통행을 끝내지 못하고, 도로 정체가 끝까지 안 풀린다(같은 날 실측).
      demand_scale_pct: 100, bg_reroute_prob: 0, bg_reroute_mode: 'random',
    },
  };
  // 설정은 localStorage에 통째로 저장된다 — 기본값만 고치면 **이미 쓰던 사람에게는 반영되지
  // 않는다.** 옛 기본값을 그대로 들고 있는 저장본만 한 번 올려준다. 사용자가 직접 바꾼 값은
  // 건드리지 않으려고, 정확히 옛 기본값과 같을 때만 교체한다.
  const SIM_CONFIG_VERSION = 2;   // 2026-08-11: other_device_lambda 300→30, bg_reroute_prob 0.02→0
  const [simConfig, setSimConfig] = useState(() => {
    try {
      const saved = localStorage.getItem('v2x_sim_config');
      if (!saved) return DEFAULT_SIM_CONFIG;
      const cfg = JSON.parse(saved);
      if ((cfg._version ?? 1) < SIM_CONFIG_VERSION) {
        const pol = cfg.policy_options || (cfg.policy_options = {});
        if (pol.other_device_lambda === 300) pol.other_device_lambda = 30.0;
        if (pol.bg_reroute_prob === 0.02) pol.bg_reroute_prob = 0;
        cfg._version = SIM_CONFIG_VERSION;
        try { localStorage.setItem('v2x_sim_config', JSON.stringify(cfg)); } catch {}
      }
      return cfg;
    } catch { return DEFAULT_SIM_CONFIG; }
  });
  function saveSimConfig(cfg) {
    setSimConfig(cfg);
    try { localStorage.setItem('v2x_sim_config', JSON.stringify(cfg)); } catch {}
  }

  // Cross-tab live data
  const [simHistory, setSimHistory] = useState([]);   // {t, speed, progress, latency, bs} per tick
  const [simLogs,    setSimLogs]    = useState([]);   // {t, target, kind, ko} event log
  const prevBsRef = useRef(null);
  const lastLatencyWarnRef = useRef(null); // 마지막으로 '위험' 로그를 남긴 시각(elapsed) — 매 틱 중복 적재 방지
  const simElapsedRef = useRef(0);

  // hash routing
  useEffect(() => {
    const onHash = () => setTab(location.hash.replace('#', '') || 'simulation');
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (id) => { location.hash = id; setTab(id); };

  /* sim clock — **차량이 실제로 출발한 뒤부터** 센다.
     시작을 누르면 교통 준비와 예열(SUMO를 화면 갱신 없이 수십 초 굴린다)이 먼저인데,
     그동안 시계가 돌면 "출발도 안 했는데 시간이 흐르는" 그림이 된다.
     ⚠️ 의존성은 vehiclePos 객체가 아니라 **불리언**이어야 한다. 객체를 그대로 넣으면
        위치가 갱신될 때마다(약 0.1초) 인터벌이 초기화돼 1초 틱이 영영 안 온다. */
  const hasDeparted = !!vehiclePos;
  useEffect(() => {
    if (!sim.running || !hasDeparted) return;
    const t = setInterval(() => dispatch({ type: 'tick' }), 1000);
    return () => clearInterval(t);
  }, [sim.running, hasDeparted]);

  useEffect(() => {
    let closed = false;

    async function bootReset() {
      try {
        // scope=runtime: 차량/경로 찌꺼기만 정리 — 구역(bbox)·도로망은 유지해서
        // 새로고침 후에도 설정해둔 구역이 (기지국처럼) 그대로 남는다.
        await fetch(`${API}/api/simulation/reset?scope=runtime`, { method: 'POST' });
      } catch (_) {}
      if (!closed) {
        setVehiclePos(null);
        setRouteCoords([]);
        setSimNotice(null);
        setNetworkTelemetry(null);
        setRouteEdges(null);
        setBackgroundVehicles([]);
        setTrafficPrep(null);
        awaitingAutoStart.current = false;
        setSimHistory([]);
        setSimLogs([]);
        prevBsRef.current = null;
        lastLatencyWarnRef.current = null;
        dispatch({ type: 'reset' });
        setBootReady(true);
      }
    }

    bootReset();

    const resetOnUnload = () => {
      try {
        navigator.sendBeacon(`${API}/api/simulation/reset?scope=runtime`);
      } catch (_) {}
    };
    window.addEventListener('beforeunload', resetOnUnload);

    return () => {
      closed = true;
      window.removeEventListener('beforeunload', resetOnUnload);
    };
  }, []);

  // WebSocket — connect once, auto-reconnect
  useEffect(() => {
    if (!bootReady) return;
    let ws;
    let dead = false;

    function connect() {
      if (dead) return;
      ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen  = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        if (!dead) setTimeout(connect, 2000);
      };
      ws.onerror = () => {};

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'position') {
          setVehiclePos({ lat: msg.lat, lng: msg.lng, speed: msg.speed, progress: msg.progress, current_edge_id: msg.current_edge_id ?? null });
        } else if (msg.type === 'route') {
          setRouteCoords(msg.coords);
        } else if (msg.type === 'arrived') {
          dispatch({ type: 'finish' });
          setVehiclePos(v => v ? { ...v, arrived: true } : v);
        } else if (msg.type === 'warning') {
          setSimNotice(msg.message);
        } else if (msg.type === 'error') {
          console.error('[WS]', msg.message);
        } else if (msg.type === 'telemetry') {
          setNetworkTelemetry(msg);
        } else if (msg.type === 'route_cost') {
          const perEdge = msg.per_edge || [];
          const totalDist = msg.total_distance_m > 0
            ? msg.total_distance_m
            : perEdge.reduce((s, e) => s + (e.distance_m || 0), 0);
          setRouteEdges({ per_edge: perEdge, edge_names: msg.edge_names || {}, routing_mode: msg.routing_mode, avg_latency_ms: msg.avg_latency_ms, total_cost: msg.total_cost, total_distance_m: totalDist, coverage_risk: msg.coverage_risk ?? 0, handover_count: msg.handover_count ?? 0 });
        } else if (msg.type === 'disconnected') {
          setSimLogs(prev => [...prev, {
            t: fmtClock(simElapsedRef.current), target: 'EGO', kind: 'disconnect',
            ko: 'BS 커버리지 단절',
          }]);
        } else if (msg.type === 'background_positions') {
          setBackgroundVehicles(msg.vehicles || []);
        } else if (msg.type === 'placement_progress') {
          setPlacementProgress(msg.progress || null);
        } else if (msg.type === 'traffic_prep') {
          // 교통 준비 진행 상황. preparing이 내려가는 순간, 우리가 누른 시작이 대기 중이었다면
          // 백엔드가 방금 시뮬을 자동으로 띄운 것이므로 여기서 실행 상태로 전환한다.
          if (msg.preparing) {
            setTrafficPrep({ stage: msg.stage, message: msg.message });
            // 래치가 아니라 **매번 갱신**이다. 준비 중에 초기화로 취소하면 백엔드가
            // pending_start를 풀고 그 사실이 여기로 오는데, 래치로 두면 뒤이어 배경 준비가
            // 끝나는 순간 누르지도 않은 런이 "실행 중"으로 바뀐다.
            awaitingAutoStart.current = !!msg.pending_start;
          } else {
            setTrafficPrep(null);
            if (awaitingAutoStart.current) {
              awaitingAutoStart.current = false;
              // 자동 시작된 런의 DB id — 시작 응답이 "preparing"이라 run_id가 없었으므로
              // 여기서 받아 시뮬레이션 탭의 currentRunIdRef에 넣어준다(도착 결과 저장용).
              setAutoStartRunId(msg.run_id ?? null);
              dispatch({ type: 'start' });
            }
          }
        }
      };
    }

    connect();
    return () => { dead = true; ws && ws.close(); };
  }, [bootReady]);

  // Keep elapsed ref current for WS handlers that need current time
  useEffect(() => { simElapsedRef.current = sim.elapsed; }, [sim.elapsed]);

  // Accumulate simHistory + detect events each tick
  useEffect(() => {
    if (!sim.running) return;
    const bs = networkTelemetry?.ego_vehicle?.connected_network_node_name
      ?? networkTelemetry?.connected_node?.name
      ?? null;
    const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
      ?? networkTelemetry?.latency_ms
      ?? null;
    const entry = {
      t: sim.elapsed,
      speed: vehiclePos?.speed ?? 0,
      progress: vehiclePos?.progress ?? 0,
      latency,
      bs,
    };
    setSimHistory(prev => {
      const next = [...prev, entry];
      return next.length > 60 ? next.slice(1) : next;
    });
    if (latency !== null && latency > 20) {
      // 위험 상태로 처음 진입했을 때만 로그를 남기고, 계속 지속되면 10초마다 한 번만
      // 다시 남긴다 — 매 틱(1초)마다 똑같은 경고가 쌓여 이벤트 피드가 도배되는 것을 방지.
      const last = lastLatencyWarnRef.current;
      if (last === null || sim.elapsed - last >= 10) {
        setSimLogs(prev => [...prev, {
          t: fmtClock(sim.elapsed), target: 'EGO', kind: 'warn',
          ko: `Latency 위험: ${latency.toFixed(1)}ms (임계치 초과)`
        }]);
        lastLatencyWarnRef.current = sim.elapsed;
      }
    } else {
      lastLatencyWarnRef.current = null;
    }
    if (bs && prevBsRef.current && prevBsRef.current !== bs) {
      setSimLogs(prev => [...prev, {
        t: fmtClock(sim.elapsed), target: 'EGO', kind: 'handover',
        ko: `${prevBsRef.current} → ${bs} 핸드오버`
      }]);
    }
    if (bs) prevBsRef.current = bs;
  }, [sim.tick]);

  // Log arrival event
  useEffect(() => {
    if (!vehiclePos?.arrived) return;
    setSimLogs(prev => [...prev, {
      t: fmtClock(sim.elapsed), target: 'EGO', kind: 'done',
      ko: '목적지 도착 완료'
    }]);
  }, [vehiclePos?.arrived]);

  // Log simulation start
  useEffect(() => {
    if (!sim.running) return;
    setSimLogs(prev => {
      if (prev.length > 0 && prev[prev.length - 1].kind === 'info' && prev[prev.length - 1].target === 'SYS') return prev;
      return [...prev, { t: fmtClock(sim.elapsed), target: 'SYS', kind: 'info', ko: '시뮬레이션 시작됨' }];
    });
  }, [sim.running]);

  // Reset history/logs on full reset
  useEffect(() => {
    if (!sim.running && sim.elapsed === 0) {
      setSimHistory([]);
      setSimLogs([]);
      prevBsRef.current = null;
      lastLatencyWarnRef.current = null;
    }
  }, [sim.running, sim.elapsed]);

  const current = NAV.find(n => n.id === tab) || NAV[0];

  if (!appMode) return <LandingPage onSelect={chooseMode} />;

  return (
    <div className="app">
      <header className="nav">
        <div className="nav-brand" onClick={() => go('simulation')} style={{ cursor: 'pointer' }} title="시뮬레이션으로 이동">
          <div className="nav-logo"><Icon.route size={17} /></div>
          <div className="nav-title">
            <b>V2X AI Routing Lab</b>
            <span>자율주행 통신 기반 경로 시뮬레이터</span>
          </div>
        </div>
        <nav className="nav-tabs">
          {NAV.map(n => {
            const I = Icon[n.icon];
            return (
              <button key={n.id} className={'nav-tab' + (tab === n.id ? ' active' : '')} onClick={() => go(n.id)}>
                <span className="tab-ko">{n.ko}</span>
                <span className="tab-en">{n.en}</span>
              </button>
            );
          })}
        </nav>
        <div className="nav-right" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: wsConnected ? 'var(--good)' : '#ccc',
            display: 'inline-block'
          }} title={wsConnected ? 'Backend connected' : 'Backend disconnected'} />
          <span className={'status-badge ' + (sim.running ? 'running' : sim.elapsed > 0 ? 'paused' : 'idle')}>
            <span className="dot" />{sim.running ? '실행 중' : sim.elapsed > 0 ? '일시정지' : '미시작'}
            <span className="num">{fmtClock(sim.elapsed)}</span>
          </span>
        </div>
      </header>

      <main className="page" style={tab === 'simulation' ? { overflow: 'hidden' } : {}}>
        {tab === 'dashboard'  && <Dashboard sim={sim} go={go} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} simHistory={simHistory} simLogs={simLogs} simConfig={simConfig} routeEdges={routeEdges} sheets={sheets} activeSheetIdx={activeSheetIdx} mode={appMode} />}
        <div style={{ display: tab === 'simulation' ? 'block' : 'none', height: '100%' }}>
          <SimulationTab
            sim={sim}
            dispatch={dispatch}
            active={tab === 'simulation'}
            vehiclePos={vehiclePos}
            routeCoords={routeCoords}
            setRouteCoords={setRouteCoords}
            setVehiclePos={setVehiclePos}
            simNotice={simNotice}
            setSimNotice={setSimNotice}
            trafficPrep={trafficPrep}
            autoStartRunId={autoStartRunId}
            placementProgress={placementProgress}
            networkTelemetry={networkTelemetry}
            setNetworkTelemetry={setNetworkTelemetry}
            simConfig={simConfig}
            setSimConfig={saveSimConfig}
            backgroundVehicles={backgroundVehicles}
            setBackgroundVehicles={setBackgroundVehicles}
            simLogs={simLogs}
            setSimLogs={setSimLogs}
            simHistory={simHistory}
            setSimHistory={setSimHistory}
            routeEdges={routeEdges}
            setRouteEdges={setRouteEdges}
            sheets={sheets}
            setSheets={setSheets}
            activeSheetIdx={activeSheetIdx}
            setActiveSheetIdx={setActiveSheetIdx}
            api={API}
            mode={appMode}
          />
        </div>
        {/* 시뮬레이션 탭과 같은 이유로 **언마운트하지 않고 숨기기만** 한다. 조건부 렌더링이면
            탭을 옮길 때 입력·생성한 시나리오 목록이 통째로 날아가고, 무엇보다 배치 진행을
            묻는 타이머까지 같이 죽어서 **끝난 배치 결과가 저장되지 않는다**(서버는 끝까지
            계산하는데 받는 쪽이 없어 분석보고서 탭에도 안 뜬다 — 2026-08-12 실측). */}
        <div style={{ display: tab === 'scenario' ? 'block' : 'none' }}>
          {/* 시나리오를 "적용"하면 시트를 만들어 시뮬레이션 탭에 띄운다 — 그래서 시트
              상태와 탭 이동(go)을 여기까지 내려준다. 실행은 사용자가 시뮬레이션 탭에서. */}
          <ScenarioTab simConfig={simConfig} setSimConfig={saveSimConfig} mode={appMode}
            sheets={sheets} setSheets={setSheets} setActiveSheetIdx={setActiveSheetIdx}
            api={API} go={go} />
        </div>
        {tab === 'report'     && <ReportTab sim={sim} simLogs={simLogs} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} routeCoords={routeCoords} routeEdges={routeEdges} simHistory={simHistory} simConfig={simConfig} mode={appMode} />}
        {tab === 'settings'   && <SettingsTab sim={sim} dispatch={dispatch} api={API} simConfig={simConfig} setSimConfig={saveSimConfig} mode={appMode} setAppMode={chooseMode} />}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
