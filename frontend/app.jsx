/* ============================================================ App shell + state */
const API = 'http://localhost:8001';
const WS_URL = 'ws://localhost:8001/ws';

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

  // Vehicle state from WebSocket
  const [vehiclePos, setVehiclePos]     = useState(null);
  const [routeCoords, setRouteCoords]   = useState([]);
  const [wsConnected, setWsConnected]   = useState(false);
  const wsRef = useRef(null);

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

  // WebSocket — connect once, auto-reconnect
  useEffect(() => {
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
        } else if (msg.type === 'error') {
          console.error('[WS]', msg.message);
        }
      };
    }

    connect();
    return () => { dead = true; ws && ws.close(); };
  }, []);

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
        {tab === 'dashboard'  && <Dashboard sim={sim} go={go} />}
        <div style={{ display: tab === 'simulation' ? 'block' : 'none', height: '100%' }}>
          <SimulationTab
            sim={sim}
            dispatch={dispatch}
            active={tab === 'simulation'}
            vehiclePos={vehiclePos}
            routeCoords={routeCoords}
            setRouteCoords={setRouteCoords}
            setVehiclePos={setVehiclePos}
            api={API}
          />
        </div>
        {tab === 'vehicles'   && <VehiclesTab />}
        {tab === 'network'    && <NetworkTab />}
        {tab === 'routes'     && <RoutesTab />}
        {tab === 'analysis'   && <AnalysisTab />}
        {tab === 'settings'   && <SettingsTab sim={sim} dispatch={dispatch} />}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
