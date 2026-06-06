/* ============================================================
   Simulation tab — real SUMO backend integration
   Flow:
     1) 구역 설정 → drag bbox → POST /api/setup-network (OSM + netconvert)
     2) 출발지 클릭 → 버튼 재클릭으로 확정
     3) 도착지 클릭 → 버튼 재클릭으로 확정
     4) 시작 → POST /api/simulation/start (SUMO TraCI + Dijkstra)
     5) WebSocket → 차량 마커 실시간 이동
   ============================================================ */
function SimulationTab({ sim, dispatch, active, vehiclePos, routeCoords, setRouteCoords, setVehiclePos, api }) {
  const mapRef  = useRef(null);
  const mapObj  = useRef(null);
  const groups  = useRef({});
  const prevVehPos = useRef(null);

  const KR_CENTER = [36.4, 127.9], KR_ZOOM = 7;

  const [mode,       setMode]       = useState(null);
  const [area,       setArea]       = useState(null);
  const [origin,     setOrigin]     = useState(null);
  const [originDone, setOriginDone] = useState(false);
  const [dest,       setDest]       = useState(null);
  const [destDone,   setDestDone]   = useState(false);
  const [osmStage,   setOsmStage]   = useState(0); // 0 idle · 1 download · 2 convert · 3 ready
  const [osmError,   setOsmError]   = useState(null);
  const [showLayers, setShowLayers] = useState({ vehicles: true, routes: true });
  const [simError,   setSimError]   = useState(null);

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
    mapObj.current = map;
    return () => { map.remove(); mapObj.current = null; groups.current = {}; };
  }, []);

  useEffect(() => {
    if (active && mapObj.current) setTimeout(() => mapObj.current.invalidateSize(), 60);
  }, [active]);

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
  }, [mode]);

  /* ── finalizeArea — real OSM + netconvert via backend ────────── */
  async function finalizeArea(bounds) {
    setArea({ s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() });
    setMode(null);
    setOsmError(null);
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

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Network setup failed');
      }

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

  async function handleStart() {
    if (!ready) return;
    setSimError(null);
    setRouteCoords([]);
    setVehiclePos(null);

    try {
      const res = await fetch(`${api}/api/simulation/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin, dest }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '시뮬레이션 시작 실패');
      }
      dispatch({ type: 'start' });
    } catch (e) {
      setSimError(e.message);
    }
  }

  async function handleStop() {
    await fetch(`${api}/api/simulation/stop`, { method: 'POST' });
    dispatch({ type: 'pause' });
  }

  function clearAll() {
    handleStop().catch(() => {});
    setMode(null); setArea(null);
    setOrigin(null); setOriginDone(false);
    setDest(null);   setDestDone(false);
    setOsmStage(0);  setOsmError(null);
    setSimError(null);
    setRouteCoords([]); setVehiclePos(null);
    if (groups.current.areaRect) { groups.current.areaRect.remove(); groups.current.areaRect = null; }
    if (groups.current.veh)      { groups.current.veh.remove();      groups.current.veh = null; }
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
        {simError && (
          <div style={{ position: 'absolute', top: 60, left: '50%', transform: 'translateX(-50%)', zIndex: 800, background: '#E0463C', color: '#fff', borderRadius: 8, padding: '10px 18px', fontSize: 13, boxShadow: 'var(--sh-2)', maxWidth: 400, textAlign: 'center' }}>
            <Icon.warn size={14} /> {simError}
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
                    <Icon.warn size={13} style={{ flex: '0 0 auto' }} /> 동 단위 이하로 선택을 권장합니다
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
            {!ready && !sim.running && (
              <div className="muted" style={{ fontSize: 10.5, textAlign: 'center' }}>
                구역 · 출발지 · 도착지를 확정하면 시작할 수 있습니다
              </div>
            )}
            <div className="row between" style={{ padding: '11px 13px', background: 'var(--brand)', borderRadius: 10, color: '#fff' }}>
              <span style={{ fontSize: 11, opacity: 0.7 }}>경과 시간 <span className="mono">ELAPSED</span></span>
              <span className="num" style={{ fontSize: 19, fontWeight: 600 }}>{fmtClock(sim.elapsed)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
window.SimulationTab = SimulationTab;
