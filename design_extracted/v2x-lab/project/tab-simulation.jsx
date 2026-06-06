/* ============================================================ Simulation tab — Leaflet scenario builder
   Flow: 1) 구역 설정 → drag a rectangle → OSM loads for that bbox → zoom in
         2) 출발지 → click to pick → press button again to confirm
         3) 도착지 / 기지국 → same confirm-on-second-press pattern
   Initial (nothing chosen): map shows all of South Korea.
   ============================================================ */
function SimulationTab({ sim, dispatch, active }) {
  const mapRef = useRef(null);
  const mapObj = useRef(null);
  const groups = useRef({});          // wp, bs, route layerGroups + areaRect + veh marker
  const routePts = useRef(null);

  const KR_CENTER = [36.4, 127.9], KR_ZOOM = 7;

  const [mode, setMode] = useState(null);      // 'area' | 'origin' | 'dest' | 'bs' | null
  const [area, setArea] = useState(null);      // {s,w,n,e}
  const [origin, setOrigin] = useState(null);
  const [originDone, setOriginDone] = useState(false);
  const [dest, setDest] = useState(null);
  const [destDone, setDestDone] = useState(false);
  const [bsList, setBsList] = useState([]);
  const [bsDraft, setBsDraft] = useState(null);
  const [bsHeight, setBsHeight] = useState(30);
  const [osmStage, setOsmStage] = useState(0); // 0 idle · 1 download · 2 convert · 3 ready
  const [showLayers, setShowLayers] = useState({ vehicles: true, bs: true, routes: true });

  const coordStr = (ll) => `${ll.lat.toFixed(4)}, ${ll.lng.toFixed(4)}`;
  const ready = area && originDone && destDone;

  /* ---- marker builders ---- */
  function pinIcon(color, faded) {
    return L.divIcon({ className: '', iconSize: [26, 34], iconAnchor: [13, 32], html:
      `<svg width="26" height="34" viewBox="0 0 26 34" style="opacity:${faded ? 0.6 : 1};filter:drop-shadow(0 2px 3px rgba(0,0,0,.3))"><path d="M13 33C13 33 24 20 24 12A11 11 0 1 0 2 12C2 20 13 33 13 33Z" fill="${color}" stroke="#fff" stroke-width="2"/><circle cx="13" cy="12" r="4" fill="#fff"/></svg>` });
  }
  function bsIcon(faded) {
    const tone = '#2E75B6';
    return L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15], html:
      `<div style="position:relative;width:30px;height:30px;opacity:${faded ? 0.6 : 1}">
        <div style="position:absolute;left:5px;top:5px;width:20px;height:20px;border-radius:6px;background:${tone};display:grid;place-items:center;box-shadow:0 1px 4px rgba(0,0,0,.35)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M12 12v8M9 20h6"/><circle cx="12" cy="9" r="1.5"/><path d="M7.5 13.5a6 6 0 0 1 0-9M16.5 4.5a6 6 0 0 1 0 9"/></svg>
        </div></div>` });
  }

  /* ---- init map (once, persists across tab switches) ---- */
  useEffect(() => {
    if (mapObj.current || !window.L) return;
    const map = L.map(mapRef.current, { zoomControl: false, attributionControl: false }).setView(KR_CENTER, KR_ZOOM);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', { maxZoom: 19, subdomains: 'abcd' }).addTo(map);
    groups.current.wp = L.layerGroup().addTo(map);
    groups.current.bs = L.layerGroup().addTo(map);
    groups.current.route = L.layerGroup().addTo(map);
    mapObj.current = map;
    return () => { map.remove(); mapObj.current = null; groups.current = {}; };
  }, []);

  // recalc size when tab becomes visible
  useEffect(() => {
    if (active && mapObj.current) setTimeout(() => mapObj.current.invalidateSize(), 60);
  }, [active]);

  /* ---- finalize a dragged area: OSM load sequence ---- */
  function finalizeArea(bounds) {
    setArea({ s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() });
    setMode(null);
    setOsmStage(1);
    setTimeout(() => setOsmStage(2), 1100);
    setTimeout(() => { setOsmStage(3); if (mapObj.current) mapObj.current.fitBounds(bounds, { padding: [50, 50] }); }, 2300);
    setTimeout(() => setOsmStage(0), 3300);
  }

  /* ---- mode interaction handlers ---- */
  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    if (mode === 'area') {
      map.dragging.disable();
      map.getContainer().style.cursor = 'crosshair';
      let start = null;
      const onDown = (e) => {
        start = e.latlng;
        if (groups.current.areaRect) { groups.current.areaRect.remove(); }
        groups.current.areaRect = L.rectangle([start, start], { color: '#1E3A5F', weight: 1.6, dashArray: '6 4', fillColor: '#2E75B6', fillOpacity: 0.10 }).addTo(map);
      };
      const onMove = (e) => { if (start && groups.current.areaRect) groups.current.areaRect.setBounds(L.latLngBounds(start, e.latlng)); };
      const onUp = (e) => { if (!start) return; const b = L.latLngBounds(start, e.latlng); start = null; finalizeArea(b); };
      map.on('mousedown', onDown); map.on('mousemove', onMove); map.on('mouseup', onUp);
      return () => { map.off('mousedown', onDown); map.off('mousemove', onMove); map.off('mouseup', onUp); map.dragging.enable(); map.getContainer().style.cursor = ''; };
    }
    if (mode === 'origin' || mode === 'dest' || mode === 'bs') {
      map.getContainer().style.cursor = 'crosshair';
      const onClick = (e) => {
        const ll = { lat: e.latlng.lat, lng: e.latlng.lng };
        if (mode === 'origin') { setOrigin(ll); setOriginDone(false); }
        else if (mode === 'dest') { setDest(ll); setDestDone(false); }
        else setBsDraft(ll);
      };
      map.on('click', onClick);
      return () => { map.off('click', onClick); map.getContainer().style.cursor = ''; };
    }
  }, [mode]);

  /* ---- render origin / dest markers ---- */
  useEffect(() => {
    const wp = groups.current.wp; if (!wp) return;
    wp.clearLayers();
    if (origin) L.marker([origin.lat, origin.lng], { icon: pinIcon('#1F9D57', !originDone) }).addTo(wp);
    if (dest) L.marker([dest.lat, dest.lng], { icon: pinIcon('#E0463C', !destDone) }).addTo(wp);
  }, [origin, originDone, dest, destDone]);

  /* ---- render base stations + coverage ---- */
  useEffect(() => {
    const g = groups.current.bs; if (!g) return;
    g.clearLayers();
    bsList.forEach(b => {
      const radius = 180 + b.h * 6;
      L.circle([b.lat, b.lng], { radius, color: '#2E75B6', weight: 1, fillColor: '#2E75B6', fillOpacity: 0.06, dashArray: '4 4' }).addTo(g);
      L.marker([b.lat, b.lng], { icon: bsIcon(false) }).addTo(g).bindTooltip(`기지국 · ${b.h}m`, { direction: 'top' });
    });
    if (bsDraft) L.marker([bsDraft.lat, bsDraft.lng], { icon: bsIcon(true) }).addTo(g);
  }, [bsList, bsDraft]);

  /* ---- route preview ---- */
  function buildRoute(o, d) {
    const dx = d.lat - o.lat, dy = d.lng - o.lng;
    const mid = [(o.lat + d.lat) / 2 - dy * 0.12, (o.lng + d.lng) / 2 + dx * 0.12];
    return [[o.lat, o.lng], mid, [d.lat, d.lng]];
  }
  useEffect(() => {
    const g = groups.current.route; if (!g) return;
    g.clearLayers(); routePts.current = null;
    if (origin && dest && originDone && destDone) {
      const pts = buildRoute(origin, dest);
      routePts.current = pts;
      L.polyline(pts, { color: '#2E75B6', weight: 4, opacity: 0.55, dashArray: sim.running ? null : '2 8' }).addTo(g);
    }
  }, [origin, dest, originDone, destDone, sim.running]);

  /* ---- vehicle animation ---- */
  function interpAlong(pts, t) {
    const segs = []; let total = 0;
    for (let i = 0; i < pts.length - 1; i++) { const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]); segs.push(d); total += d; }
    let dist = t * total;
    for (let i = 0; i < segs.length; i++) { if (dist <= segs[i] || i === segs.length - 1) { const f = segs[i] ? dist / segs[i] : 0; return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f]; } dist -= segs[i]; }
    return pts[pts.length - 1];
  }
  function signalColor(pos) {
    if (!bsList.length) return '#E8A23B';
    let min = Infinity;
    bsList.forEach(b => { const d = mapObj.current.distance(pos, [b.lat, b.lng]); min = Math.min(min, d / (180 + b.h * 6)); });
    return min < 0.6 ? '#1F9D57' : min < 1.0 ? '#E8A23B' : '#E0463C';
  }
  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    const pts = routePts.current;
    const show = sim.running && pts && showLayers.vehicles;
    if (!show) { if (groups.current.veh) { groups.current.veh.remove(); groups.current.veh = null; } return; }
    const t = (sim.tick * 0.02) % 1;
    const pos = interpAlong(pts, t);
    const icon = L.divIcon({ className: '', iconSize: [16, 16], iconAnchor: [8, 8], html: `<div class="dot-marker" style="width:16px;height:16px;background:${signalColor(pos)}"></div>` });
    if (!groups.current.veh) groups.current.veh = L.marker(pos, { icon, zIndexOffset: 1000 }).addTo(map);
    else { groups.current.veh.setLatLng(pos); groups.current.veh.setIcon(icon); }
  }, [sim.tick, sim.running, showLayers.vehicles, bsList]);

  /* ---- layer toggles ---- */
  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    const { bs, route } = groups.current;
    if (bs) showLayers.bs ? bs.addTo(map) : bs.remove();
    if (route) showLayers.routes ? route.addTo(map) : route.remove();
  }, [showLayers]);

  /* ---- button actions ---- */
  const tryArea = () => setMode(mode === 'area' ? null : 'area');
  const tryOrigin = () => { if (!area) return; if (mode === 'origin') { if (origin) { setOriginDone(true); setMode(null); } } else setMode('origin'); };
  const tryDest = () => { if (!area) return; if (mode === 'dest') { if (dest) { setDestDone(true); setMode(null); } } else setMode('dest'); };
  const tryBs = () => { if (!area) return; if (mode === 'bs') { if (bsDraft) { setBsList(l => [...l, { ...bsDraft, h: Number(bsHeight) || 30 }]); setBsDraft(null); setMode(null); } } else setMode('bs'); };

  function clearAll() {
    setMode(null); setArea(null); setOrigin(null); setOriginDone(false); setDest(null); setDestDone(false);
    setBsList([]); setBsDraft(null); setOsmStage(0);
    if (groups.current.areaRect) { groups.current.areaRect.remove(); groups.current.areaRect = null; }
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
    if (mode === 'area') return '지도에서 드래그하여 시뮬레이션 구역을 선택하세요';
    if (mode === 'origin') return origin ? "위치를 옮기려면 다시 클릭 · '출발지'를 눌러 확정" : '지도를 클릭해 출발지를 선택하세요';
    if (mode === 'dest') return dest ? "다시 클릭해 위치 조정 · '도착지'를 눌러 확정" : '지도를 클릭해 도착지를 선택하세요';
    if (mode === 'bs') return bsDraft ? "다시 클릭해 위치 조정 · '기지국'을 눌러 확정" : '지도를 클릭해 기지국 위치를 선택하세요';
    return '';
  })();

  const Lp = ({ on, set, children }) => (
    <label className="row between" style={{ padding: '7px 0', cursor: 'pointer' }}>
      <span style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>{children}</span>
      <Toggle on={on} onChange={set} />
    </label>
  );

  const WayRow = ({ color, label, val, done, set }) => (
    <div className="row between" style={{ padding: '8px 11px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)', cursor: area ? 'pointer' : 'default' }} onClick={area ? set : undefined}>
      <span className="row gap8" style={{ minWidth: 0 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flex: '0 0 auto' }} />
        <span className="num" style={{ fontSize: 11.5, color: val === '미지정' ? 'var(--ink-4)' : 'var(--ink)' }}>{val}</span>
      </span>
      {done ? <Icon.check size={14} style={{ color: 'var(--good)', flex: '0 0 auto' }} /> : <span className="mono" style={{ fontSize: 9, color: 'var(--ink-4)', flex: '0 0 auto' }}>{label}</span>}
    </div>
  );

  return (
    <div className="fade" style={{ display: 'grid', gridTemplateColumns: '1fr 340px', height: '100%', overflow: 'hidden' }}>
      {/* MAP */}
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        <div ref={mapRef} style={{ position: 'absolute', inset: 0 }} />

        {/* step toolbar top-left */}
        <div style={{ position: 'absolute', top: 14, left: 14, zIndex: 600, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', maxWidth: 'calc(100% - 28px)' }}>
          <div className="seg" style={{ background: '#fff', boxShadow: 'var(--sh-2)' }}>
            <button className={mode === 'area' ? 'active' : ''} onClick={tryArea}>
              <span className="row gap8"><span style={{ width: 8, height: 8, borderRadius: 2, background: '#1E3A5F', display: 'inline-block' }} /> 구역{area && <Icon.check size={12} style={{ color: 'var(--good)' }} />}</span>
            </button>
            {[['origin', '출발지', '#1F9D57', originDone, tryOrigin], ['dest', '도착지', '#E0463C', destDone, tryDest], ['bs', '기지국', '#2E75B6', bsList.length > 0, tryBs]].map(([k, lbl, c, done, fn]) => (
              <button key={k} className={mode === k ? 'active' : ''} disabled={!area} style={!area ? { opacity: 0.4, cursor: 'not-allowed' } : undefined} onClick={fn}>
                <span className="row gap8"><span style={{ width: 8, height: 8, borderRadius: '50%', background: c, display: 'inline-block' }} /> {lbl}{done && <Icon.check size={12} style={{ color: 'var(--good)' }} />}</span>
              </button>
            ))}
          </div>
          {hint && <div className="chip brand" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px' }}><Icon.pin size={13} /> {hint}</div>}
          {!area && !mode && <div className="chip" style={{ background: '#fff', boxShadow: 'var(--sh-2)', height: 34, padding: '0 12px', color: 'var(--ink-3)' }}><Icon.layers size={13} /> '구역'을 눌러 시작하세요 · 남한 전체</div>}
        </div>

        {/* layer panel bottom-left */}
        <div style={{ position: 'absolute', bottom: 14, left: 14, zIndex: 600, background: '#fff', borderRadius: 12, boxShadow: 'var(--sh-2)', padding: '10px 14px', minWidth: 188 }}>
          <div className="row gap8" style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', marginBottom: 4, whiteSpace: 'nowrap' }}><Icon.layers size={13} /> 레이어 <span className="en" style={{ fontFamily: 'var(--mono)', fontSize: 8.5, color: 'var(--ink-4)' }}>LAYERS</span></div>
          <Lp on={showLayers.vehicles} set={v => setShowLayers(s => ({ ...s, vehicles: v }))}>차량</Lp>
          <Lp on={showLayers.bs} set={v => setShowLayers(s => ({ ...s, bs: v }))}>기지국 + 커버리지</Lp>
          <Lp on={showLayers.routes} set={v => setShowLayers(s => ({ ...s, routes: v }))}>경로</Lp>
        </div>

        {/* legend top-right */}
        <div style={{ position: 'absolute', top: 14, right: 14, zIndex: 600, background: '#fff', borderRadius: 10, boxShadow: 'var(--sh-2)', padding: '9px 12px' }}>
          <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6, whiteSpace: 'nowrap' }}>신호 품질 / Signal</div>
          <div className="col gap8">
            {[['#1F9D57', '양호 < 12ms'], ['#E8A23B', '보통 12–20ms'], ['#E0463C', '위험 > 20ms']].map(([c, l]) => (
              <span key={l} className="row gap8" style={{ fontSize: 11 }}><span style={{ width: 10, height: 10, borderRadius: '50%', background: c }} /> {l}</span>
            ))}
          </div>
        </div>

        {/* OSM loading overlay */}
        {osmStage > 0 && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 700, background: 'rgba(245,248,250,0.82)', backdropFilter: 'blur(2px)', display: 'grid', placeItems: 'center' }}>
            <div className="card" style={{ width: 380, padding: 24, boxShadow: 'var(--sh-3)' }}>
              <div className="row gap12" style={{ marginBottom: 16 }}>
                {osmStage < 3
                  ? <div className="spin" style={{ width: 22, height: 22, border: '2.5px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%' }} />
                  : <div style={{ width: 22, height: 22, borderRadius: '50%', background: 'var(--good)', display: 'grid', placeItems: 'center', color: '#fff' }}><Icon.check size={14} /></div>}
                <b style={{ fontSize: 14 }}>{osmStage < 3 ? '구역 데이터 준비 중…' : '시뮬레이션 준비 완료'}</b>
              </div>
              {[['OSM 데이터 다운로드', 'Downloading OSM'], ['SUMO 네트워크 변환 (netconvert)', 'Converting network'], ['시뮬레이션 준비 완료', 'Ready']].map((s, i) => {
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
      </div>

      {/* CONTROL PANEL */}
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
          {/* step progress */}
          <div className="row gap8" style={{ flexWrap: 'wrap' }}>
            {[['구역', !!area], ['출발지', originDone], ['도착지', destDone], ['기지국', bsList.length > 0]].map(([l, ok]) => (
              <span key={l} className={'chip ' + (ok ? 'good' : '')} style={{ fontSize: 10.5 }}>{ok && <Icon.check size={11} />} {l}</span>
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
                {areaKm2(area) > 5 && <div className="row gap8" style={{ padding: '8px 11px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--warn)' }}><Icon.warn size={13} style={{ flex: '0 0 auto' }} /> 동 단위 이하로 선택을 권장합니다</div>}
                <button className="btn sm" onClick={() => setMode('area')}><Icon.layers size={13} /> 구역 다시 그리기</button>
              </>
            ) : (
              <>
                <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                  <div className="mono muted" style={{ fontSize: 11 }}>구역 미설정</div>
                  <Chip>대기</Chip>
                </div>
                <button className={'btn sm ' + (mode === 'area' ? 'accent' : 'primary')} onClick={tryArea}><Icon.pin size={13} /> {mode === 'area' ? '드래그하여 선택…' : '지도에서 구역 그리기'}</button>
              </>
            )}
          </div>

          {/* network mode */}
          <div className="field">
            <label>네트워크 모드 <span className="en">NETWORK</span></label>
            <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
              <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: sim.mode === '6G' ? 'var(--brand-2)' : 'var(--ink)' }}>{sim.mode === '6G' ? '6G-like' : '5G NR'}</span>
              <Toggle on={sim.mode === '6G'} onChange={v => dispatch({ type: 'mode', v: v ? '6G' : '5G' })} labels={['5G', '6G']} />
            </div>
          </div>

          {/* waypoints */}
          <div className="field">
            <label>경로 지점 <span className="en">WAYPOINTS</span></label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <WayRow color="var(--m-origin)" label="출발지" val={origin ? coordStr(origin) : '미지정'} done={originDone} set={tryOrigin} />
              <WayRow color="var(--m-dest)" label="도착지" val={dest ? coordStr(dest) : '미지정'} done={destDone} set={tryDest} />
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>

          {/* base stations */}
          <div className="field">
            <label>기지국 높이 <span className="en">BS HEIGHT</span></label>
            <div className="input-suffix">
              <input className="input" type="number" value={bsHeight} onChange={e => setBsHeight(e.target.value)} disabled={!area} />
              <span className="sfx">m</span>
            </div>
            <div className="muted" style={{ fontSize: 10.5 }}>{bsList.length}개 배치됨{bsDraft ? ' · 1개 선택 중 (확정 대기)' : ''}</div>
          </div>

          <div style={{ flex: 1 }} />

          {/* transport */}
          <div className="col gap8" style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <div className="row gap8">
              {!sim.running
                ? <button className="btn good block" disabled={!ready} onClick={() => dispatch({ type: 'start' })}><Icon.play size={15} /> {sim.elapsed > 0 ? '재개' : '시작'}</button>
                : <button className="btn block" style={{ borderColor: 'var(--warn-line)', color: 'var(--warn)' }} onClick={() => dispatch({ type: 'pause' })}><Icon.pause size={15} /> 일시정지</button>}
              <button className="btn icon" onClick={clearAll} title="시나리오 초기화"><Icon.reset size={15} /></button>
            </div>
            {!ready && !sim.running && <div className="muted" style={{ fontSize: 10.5, textAlign: 'center' }}>구역 · 출발지 · 도착지를 확정하면 시작할 수 있습니다</div>}
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
