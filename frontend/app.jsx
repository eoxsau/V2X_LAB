/* ============================================================ App shell + state */
const API = window.location.origin;
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

function simReducer(s, a) {
  switch (a.type) {
    case 'start':   return { ...s, running: true };
    case 'pause':   return { ...s, running: false };
    case 'reset':   return { ...s, running: false, elapsed: 0, tick: 0 };
    case 'tick':    return { ...s, elapsed: s.elapsed + 1, tick: s.tick + 1 };
    case 'mode':    return { ...s, mode: a.v };
    default: return s;
  }
}

function App() {
  const [tab, setTab] = useState(() => location.hash.replace('#', '') || 'dashboard');
  const [sim, dispatch] = React.useReducer(simReducer, { running: false, elapsed: 0, tick: 0, mode: '5G' });
  const [bootReady, setBootReady]      = useState(false);

  // Vehicle state from WebSocket
  const [vehiclePos, setVehiclePos]     = useState(null);
  const [routeCoords, setRouteCoords]   = useState([]);
  const [wsConnected, setWsConnected]   = useState(false);
  const [simNotice, setSimNotice]       = useState(null);
  const [networkTelemetry, setNetworkTelemetry] = useState(null);
  const [routeEdges, setRouteEdges] = useState(null);
  const wsRef = useRef(null);

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
    },
  };
  const [simConfig, setSimConfig] = useState(() => {
    try {
      const saved = localStorage.getItem('v2x_sim_config');
      return saved ? JSON.parse(saved) : DEFAULT_SIM_CONFIG;
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

  // hash routing
  useEffect(() => {
    const onHash = () => setTab(location.hash.replace('#', '') || 'dashboard');
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (id) => { location.hash = id; setTab(id); };

  // sim clock — only tick locally while running
  useEffect(() => {
    if (!sim.running) return;
    const t = setInterval(() => dispatch({ type: 'tick' }), 1000);
    return () => clearInterval(t);
  }, [sim.running]);

  useEffect(() => {
    let closed = false;

    async function bootReset() {
      try {
        await fetch(`${API}/api/simulation/reset`, { method: 'POST' });
      } catch (_) {}
      if (!closed) {
        setVehiclePos(null);
        setRouteCoords([]);
        setSimNotice(null);
        setNetworkTelemetry(null);
        setRouteEdges(null);
        setSimHistory([]);
        setSimLogs([]);
        prevBsRef.current = null;
        dispatch({ type: 'reset' });
        setBootReady(true);
      }
    }

    bootReset();

    const resetOnUnload = () => {
      try {
        navigator.sendBeacon(`${API}/api/simulation/reset`);
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
          setVehiclePos({ lat: msg.lat, lng: msg.lng, speed: msg.speed, progress: msg.progress });
        } else if (msg.type === 'route') {
          setRouteCoords(msg.coords);
        } else if (msg.type === 'arrived') {
          dispatch({ type: 'pause' });
          setVehiclePos(v => v ? { ...v, arrived: true } : v);
        } else if (msg.type === 'warning') {
          setSimNotice(msg.message);
        } else if (msg.type === 'error') {
          console.error('[WS]', msg.message);
        } else if (msg.type === 'telemetry') {
          setNetworkTelemetry(msg);
        } else if (msg.type === 'route_cost') {
          setRouteEdges({ per_edge: msg.per_edge, edge_names: msg.edge_names || {}, routing_mode: msg.routing_mode, avg_latency_ms: msg.avg_latency_ms, total_cost: msg.total_cost });
        }
      };
    }

    connect();
    return () => { dead = true; ws && ws.close(); };
  }, [bootReady]);

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
      setSimLogs(prev => [...prev, {
        t: fmtClock(sim.elapsed), target: 'EGO', kind: 'warn',
        ko: `Latency 위험: ${latency.toFixed(1)}ms (임계치 초과)`
      }]);
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
    }
  }, [sim.running, sim.elapsed]);

  const current = NAV.find(n => n.id === tab) || NAV[0];

  return (
    <div className="app">
      <header className="nav">
        <div className="nav-brand" onClick={() => go('dashboard')} style={{ cursor: 'pointer' }} title="대시보드로 이동">
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
        {tab === 'dashboard'  && <Dashboard sim={sim} go={go} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} simHistory={simHistory} />}
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
            networkTelemetry={networkTelemetry}
            setNetworkTelemetry={setNetworkTelemetry}
            simConfig={simConfig}
            api={API}
          />
        </div>
        {tab === 'vehicles'   && <VehiclesTab sim={sim} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} simHistory={simHistory} />}
        {tab === 'network'    && <NetworkTab networkTelemetry={networkTelemetry} routeEdges={routeEdges} />}
        {tab === 'routes'     && <RoutesTab sim={sim} vehiclePos={vehiclePos} routeCoords={routeCoords} networkTelemetry={networkTelemetry} simHistory={simHistory} />}
        {tab === 'analysis'   && <AnalysisTab sim={sim} simLogs={simLogs} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} />}
        {tab === 'settings'   && <SettingsTab sim={sim} dispatch={dispatch} api={API} simConfig={simConfig} setSimConfig={saveSimConfig} />}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
