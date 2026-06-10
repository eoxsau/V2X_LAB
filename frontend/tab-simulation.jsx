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
const NETWORK_ROUTING_ALGORITHMS = new Set([
  'network_aware_routing',
  'look_ahead_routing',
  'rl_routing',
]);

function formatAlgorithmName(name) {
  return name.replaceAll('_', ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function SimulationTab({ sim, dispatch, active, vehiclePos, routeCoords, setRouteCoords, setVehiclePos, simNotice, setSimNotice, networkTelemetry, setNetworkTelemetry, api }) {
  const mapRef  = useRef(null);
  const mapObj  = useRef(null);
  const groups  = useRef({});
  const prevVehPos = useRef(null);
  const algorithmMenuRef = useRef(null);

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
  const [showLayers, setShowLayers] = useState({ vehicles: true, routes: true });
  const [simError,   setSimError]   = useState(null);
  const [stations,   setStations]   = useState([]);   // user_created base stations from DB
  const [stationsErr,setStationsErr]= useState(null);
  const [showAlgorithmMenu, setShowAlgorithmMenu] = useState(false);
  const [openAlgorithmGroup, setOpenAlgorithmGroup] = useState(null);
  const [selectedAlgorithms, setSelectedAlgorithms] = useState(DEFAULT_ALGORITHM_SELECTION);

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
    groups.current.blocks = L.layerGroup().addTo(map);
    groups.current.stations = L.layerGroup().addTo(map);
    mapObj.current = map;
    return () => { map.remove(); mapObj.current = null; groups.current = {}; };
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

  useEffect(() => {
    const ng = groups.current.network;
    const bg = groups.current.blocks;
    const map = mapObj.current;
    if (!ng || !bg || !map) return;
    ng.clearLayers();
    bg.clearLayers();
    if (!networkTelemetry) return;

    const selected = networkTelemetry.connected_node;
    (networkTelemetry.candidate_nodes || []).forEach((node, idx) => {
      // user_created: already rendered by the stations layer (avoid overlap)
      // synthetic: auto-generated placeholders — hidden when user has real stations
      if (node.source === 'user_created' || node.source === 'synthetic') return;
      const base = idx === 0 ? '#1E88E5' : '#607D8B';
      const marker = L.circleMarker([node.lat || selected.lat, node.lng || selected.lng], {
        radius: idx === 0 ? 8 : 6,
        color: '#fff',
        weight: 2,
        fillColor: base,
        fillOpacity: 0.9,
      }).addTo(ng);
      marker.bindTooltip(`${node.name || node.id} · ${node.predicted_latency_ms.toFixed(1)}ms`, { direction: 'top' });
    });

    const lines = networkTelemetry.connection_lines?.length
      ? networkTelemetry.connection_lines.map(l => [[l.from.lat, l.from.lng], [l.to.lat, l.to.lng]])
      : (selected && networkTelemetry.connection_line?.length === 2)
        ? [networkTelemetry.connection_line.map(p => [p.lat, p.lng])]
        : [];
    if (lines.length) {
      const loss = networkTelemetry.estimated_penetration_loss_db || 0;
      const color = loss >= 20 ? '#E0463C' : loss >= 10 ? '#B97B11' : '#1F9D57';
      lines.forEach(coords => {
        L.polyline(coords, { color, weight: 3, opacity: 0.85, dashArray: loss >= 10 ? '8 6' : undefined }).addTo(ng);
      });
    }

    (networkTelemetry.highlighted_buildings || []).forEach((b) => {
      if (!b.geometry || b.geometry.length < 3) return;
      L.polygon(
        b.geometry.map(p => [p.lat, p.lng]),
        { color: '#E0463C', weight: 1.5, fillColor: '#E0463C', fillOpacity: 0.18 }
      ).addTo(bg);
    });
  }, [networkTelemetry]);

  /* ── user-created base station markers ──────────────────────────
     Always blue (#1E88E5) with white border — same style as synthetic nodes.
     Delete-hover: gray (#9AA5B1) to indicate the target being removed.
     Label always visible below marker.
  ── */
  useEffect(() => {
    const g = groups.current.stations; if (!g) return;
    g.clearLayers();
    const deleteMode = mode === 'bs_delete';
    // latency lookup so label can include it when sim is running
    const latencyMap = {};
    (networkTelemetry?.candidate_nodes || []).forEach(n => { latencyMap[n.id] = n.predicted_latency_ms; });
    stations.forEach((st) => {
      const marker = L.circleMarker([st.lat, st.lng], {
        radius: 8,
        color: '#fff',
        weight: 2.5,
        fillColor: '#1E88E5',
        fillOpacity: 0.93,
        interactive: true,
      }).addTo(g);
      // permanent label below marker — name only (latency shown in right panel Top 3)
      const lat_ms = latencyMap[st.id];
      const labelText = lat_ms != null ? `${st.name} · ${lat_ms.toFixed(1)}ms` : st.name;
      marker.bindTooltip(labelText, {
        permanent: true,
        direction: 'bottom',
        offset: [0, 6],
        className: 'bs-label',
      });
      if (deleteMode) {
        // hover purely via Leaflet mouse events — no state update to avoid re-render mid-click
        marker.on('mouseover', (e) => {
          e.target.setStyle({ fillColor: '#9AA5B1', color: '#5B6670' });
        });
        marker.on('mouseout', (e) => {
          e.target.setStyle({ fillColor: '#1E88E5', color: '#fff' });
        });
        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          deleteStation(st.id);
        });
      }
    });
  }, [stations, mode, networkTelemetry]);

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
  const tryAlgorithms = () => {
    setShowAlgorithmMenu(v => !v);
    if (showAlgorithmMenu) setOpenAlgorithmGroup(null);
  };

  async function handleStart() {
    if (!ready) return;
    setSimError(null);
    setSimNotice(null);
    setRouteCoords([]);
    setVehiclePos(null);

    try {
      const res = await fetch(`${api}/api/simulation/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origin,
          dest,
          use_network_routing: NETWORK_ROUTING_ALGORITHMS.has(selectedAlgorithms.route),
          algorithm_config: selectedAlgorithms,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || '시뮬레이션 시작 실패');
      }
      if (body.warning) setSimNotice(body.warning);
      dispatch({ type: 'start' });
    } catch (e) {
      setSimError(e.message);
    }
  }

  async function handleStop() {
    await fetch(`${api}/api/simulation/stop`, { method: 'POST' });
    dispatch({ type: 'pause' });
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
    if (groups.current.areaRect) { groups.current.areaRect.remove(); groups.current.areaRect = null; }
    if (groups.current.veh)      { groups.current.veh.remove();      groups.current.veh = null; }
    if (groups.current.route)    { groups.current.route.clearLayers(); }
    if (groups.current.wp)       { groups.current.wp.clearLayers(); }
    if (groups.current.network)  { groups.current.network.clearLayers(); }
    if (groups.current.blocks)   { groups.current.blocks.clearLayers(); }
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
                <span>{formatAlgorithmName(option)}</span>
                {selected === option ? <Icon.check size={12} /> : null}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="fade" style={{ display: 'grid', gridTemplateColumns: '1fr 340px', height: '100%', overflow: 'hidden' }}>
      {/* ── MAP ────────────────────────────────────────── */}
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        <div ref={mapRef} style={{ position: 'absolute', inset: 0 }} />

        {/* step toolbar top-left */}
        <div style={{ position: 'absolute', top: 14, left: 14, zIndex: 600, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', maxWidth: 'calc(100% - 28px)' }}>
          <div className="seg" style={{ background: '#fff', boxShadow: 'var(--sh-2)' }}>
            <button className={mode === 'area' ? 'active' : ''} onClick={tryArea}>
              <span className="row gap8">
                <span style={{ width: 8, height: 8, borderRadius: 2, background: '#1E3A5F', display: 'inline-block' }} />
                구역{area && <Icon.check size={12} style={{ color: 'var(--good)' }} />}
              </span>
            </button>
            {[
              ['origin', '출발지', '#1F9D57', originDone, tryOrigin],
              ['dest',   '도착지', '#E0463C', destDone,   tryDest],
              ['bs_create', '기지국 생성', '#1E88E5', false, tryBsCreate],
              ['bs_delete', '기지국 제거', '#9AA5B1', false, tryBsDelete],
            ].map(([k, lbl, c, done, fn]) => (
              <button key={k} className={mode === k ? 'active' : ''}
                disabled={!area} style={!area ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
                onClick={fn}>
                <span className="row gap8">
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, display: 'inline-block' }} />
                  {lbl}{done && <Icon.check size={12} style={{ color: 'var(--good)' }} />}
                </span>
              </button>
            ))}
            <button className={showAlgorithmMenu ? 'active' : ''} onClick={tryAlgorithms}>
              <span className="row gap8">
                <span style={{ width: 8, height: 8, borderRadius: 2, background: '#8C6CF6', display: 'inline-block' }} />
                알고리즘
              </span>
            </button>
          </div>

          {hint && (
            <div className="chip brand" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px' }}>
              <Icon.pin size={13} /> {hint}
            </div>
          )}
          {!area && !mode && (
            <div className="chip" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px', color: 'var(--ink-3)' }}>
              <Icon.layers size={13} /> '구역'을 눌러 시작하세요 · 남한 전체
            </div>
          )}
        </div>

        {showAlgorithmMenu && (
          <div
            ref={algorithmMenuRef}
            style={{
              position: 'absolute',
              top: 64,
              left: 14,
              zIndex: 610,
              width: 340,
              background: '#fff',
              borderRadius: 12,
              boxShadow: 'var(--sh-2)',
              padding: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
            }}
          >
            <AlgorithmGroup groupKey="route" label="경로 알고리즘" options={ROUTE_ALGORITHMS} />
            <AlgorithmGroup groupKey="latency" label="지연시간 알고리즘" options={LATENCY_ALGORITHMS} />
            <AlgorithmGroup groupKey="base_station_selection" label="기지국 선택 알고리즘" options={BS_SELECTION_ALGORITHMS} />
            <AlgorithmGroup groupKey="resource_allocation" label="자원할당 알고리즘" options={RESOURCE_ALLOCATION_ALGORITHMS} />
          </div>
        )}

        {/* layer panel bottom-left */}
        <div style={{ position: 'absolute', bottom: 14, left: 14, zIndex: 600, background: '#fff', borderRadius: 12, boxShadow: 'var(--sh-2)', padding: '10px 14px', minWidth: 188 }}>
          <div className="row gap8" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4, whiteSpace: 'nowrap' }}>
            <Icon.layers size={13} /> 레이어 <span className="en" style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--ink-4)' }}>LAYERS</span>
          </div>
          <Lp on={showLayers.vehicles} set={v => setShowLayers(s => ({ ...s, vehicles: v }))}>차량</Lp>
          <Lp on={showLayers.routes}   set={v => setShowLayers(s => ({ ...s, routes: v }))}>경로</Lp>
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

      {/* ── CONTROL PANEL ────────────────────────────── */}
      <div style={{ borderLeft: '1px solid var(--border)', background: 'var(--surface)', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)' }}>
          <div className="eyebrow">Control Panel</div>
          <div className="row between" style={{ marginTop: 4 }}>
            <b style={{ fontSize: 15, whiteSpace: 'nowrap' }}>시뮬레이션 제어</b>
            <span className={'status-badge ' + (sim.running ? 'running' : sim.elapsed > 0 ? 'paused' : 'idle')}>
              <span className="dot" />{sim.running ? '실행 중' : sim.elapsed > 0 ? '일시정지' : '대기'}
            </span>
          </div>
        </div>

        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 18, flex: 1 }}>
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

          {/* waypoints */}
          <div className="field">
            <label>경로 지점 <span className="en">WAYPOINTS</span></label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <WayRow color="var(--m-origin)" label="출발지" val={origin ? coordStr(origin) : '미지정'} done={originDone} set={tryOrigin} />
              <WayRow color="var(--m-dest)"   label="도착지" val={dest   ? coordStr(dest)   : '미지정'} done={destDone}   set={tryDest} />
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>

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
            {stations.length > 0 && (
              <button className="btn sm block" onClick={resetUserStations} title="사용자 지정 기지국만 모두 제거합니다 (시뮬레이션 시나리오는 유지)">
                <Icon.reset size={13} /> 사용자 기지국 초기화 ({stations.length})
              </button>
            )}
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
      </div>
    </div>
  );
}
window.SimulationTab = SimulationTab;
