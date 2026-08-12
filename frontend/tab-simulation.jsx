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
  'tech_latency_v31',        // 설계문서 v3.1 통합 모델 (기본값)
  'distance_based_latency',
  'load_aware_latency',
  'blockage_aware_latency',
  'mec_aware_latency',
  'full_composite_latency',
];
const BS_SELECTION_ALGORITHMS = [
  'rsrp_max',                // RSRP(수신세기) 최대 연결 (v3.1 §9, 기본값)
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
  latency: 'tech_latency_v31',          // v3.1 통합 모델 (백엔드 기본값과 일치)
  base_station_selection: 'rsrp_max',   // RSRP 최대 연결 (v3.1 §9)
  resource_allocation: 'equal_allocation',
};
// rl_routing은 아직 학습된 RL 에이전트가 없어 미구현 — 선택해도 baseline Dijkstra로
// 동작한다(거짓 표시 방지를 위해 선택 버튼에 "미구현" 칩을 붙임).
// rl_routing: GNN-MAML 모델 로드 시 V4RoutingAdapter 사용, 미로드 시 Dijkstra 폴백.
const UNIMPLEMENTED_ROUTE_ALGORITHMS = new Set([]);

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
function SheetTabBar({ sheets, activeIdx, onSwitch, onAdd, onRename, onRemove, onRunBatch, batchRunning, batchError, onToggleGrid, gridView, hasEnv }) {
  const [editingIdx, setEditingIdx] = useState(null);
  const [editValue, setEditValue] = useState('');

  function sheetAlgoLabel(s) {
    const algo = s.config?.selectedAlgorithms?.route || s.config?.selectedAlgorithms?.route_algorithm;
    if (!algo) return null;
    const SHORT = { dijkstra: 'Dijkstra', astar: 'A*', k_shortest_path: 'K-Path', network_aware_routing: 'Net-Aware', look_ahead_routing: 'Lookahead', rl_routing: 'RL' };
    return SHORT[algo] ?? algo;
  }

  return (
    <div style={{
      flex: '0 0 auto', background: 'var(--surface)', borderTop: '1px solid var(--border)',
    }}>
      {/* 공유 환경 표시줄 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '3px 12px',
        borderBottom: '1px solid var(--border)', fontSize: 10.5, color: 'var(--ink-4)',
      }}>
        <span style={{ color: hasEnv ? 'var(--good)' : 'var(--ink-4)' }}>🔒</span>
        <span>공유 환경</span>
        <span style={{ color: 'var(--border)' }}>|</span>
        <span style={{ color: hasEnv ? 'var(--ink-2)' : 'var(--ink-4)' }}>
          {hasEnv ? '구역 · 기지국 · RSU · 출발지 · 도착지 고정 — 알고리즘만 시트별 변경' : '구역·출발지·도착지를 설정하면 잠깁니다'}
        </span>
      </div>
      {/* 시트 탭 스트립 */}
      <div style={{ height: 40, display: 'flex', alignItems: 'center', padding: '0 8px', gap: 3, overflowX: 'auto' }}>
        {sheets.map((s, i) => {
          const algoLabel = sheetAlgoLabel(s);
          return (
            <div
              key={s.id}
              onClick={() => editingIdx !== i && onSwitch(i)}
              onDoubleClick={() => { setEditingIdx(i); setEditValue(s.name); }}
              title="더블클릭으로 이름 수정"
              style={{
                display: 'flex', alignItems: 'center', gap: 5, padding: '0 10px', flex: '0 0 auto', height: 27,
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
              {algoLabel && (
                <span style={{
                  fontSize: 9.5, padding: '1px 5px', borderRadius: 4, whiteSpace: 'nowrap',
                  background: i === activeIdx ? 'var(--brand-tint2)' : 'var(--surface-2)',
                  color: i === activeIdx ? 'var(--brand-2)' : 'var(--ink-4)',
                  fontWeight: 500,
                }}>{algoLabel}</span>
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
          );
        })}
        <button onClick={onAdd} title="새 시트 추가 (환경 유지)" style={{
          display: 'grid', placeItems: 'center', width: 30, flex: '0 0 auto', border: 'none', background: 'none',
          cursor: 'pointer', color: 'var(--ink-3)', fontSize: 16,
        }}>+</button>
        <div style={{ flex: 1 }} />
        {batchError && <span style={{ alignSelf: 'center', fontSize: 11, color: 'var(--bad)', marginRight: 8, whiteSpace: 'nowrap' }}>{batchError}</span>}
        <button
          className="btn sm"
          onClick={onToggleGrid}
          style={{
            alignSelf: 'center', margin: '0 4px', flex: '0 0 auto',
            ...(gridView ? { background: 'var(--brand-2)', color: '#fff', borderColor: 'var(--brand-2)' } : {}),
          }}
          title={gridView ? '단일 뷰로 돌아가기' : '모든 시트를 그리드로 보기 (CCTV 뷰)'}
        >
          {gridView ? '↩ 단일 뷰' : '⊞ 그리드 뷰'}
        </button>
        <button
          className="btn sm"
          disabled={batchRunning || sheets.length < 2 || !hasEnv}
          onClick={onRunBatch}
          style={{ alignSelf: 'center', margin: '0 4px', flex: '0 0 auto' }}
          title={!hasEnv ? '출발지·도착지를 먼저 설정하세요' : sheets.length < 2 ? '비교하려면 시트가 2개 이상 필요합니다' : '같은 환경에서 모든 시트를 일괄 평가해 분석보고서 탭에서 비교'}
        >
          {batchRunning ? <><Icon.reset size={12} className="spin" /> 비교 실행 중…</> : <><Icon.compare size={12} /> 전체 비교 실행 ({sheets.length})</>}
        </button>
      </div>
    </div>
  );
}

// 컨트롤 패널 / 시뮬레이션 챗봇을 지도 우측에 붙이는 세로형 탭 버튼.
// 텍스트를 세로로 표시(writing-mode: vertical-rl)하고 왼쪽에만 모서리를 둥글게 처리해
// 지도 오른쪽 가장자리에 자연스럽게 붙도록 설계했다.
function TabButton({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: 26,
        padding: '12px 0',
        borderRadius: '6px 0 0 6px',
        border: 'none',
        cursor: 'pointer',
        writingMode: 'vertical-rl',
        fontSize: 10.5,
        fontWeight: 700,
        letterSpacing: '0.05em',
        whiteSpace: 'nowrap',
        background: active ? 'var(--brand-2)' : 'var(--brand)',
        color: '#fff',
        boxShadow: '-3px 0 10px rgba(0,0,0,0.18)',
        transition: 'background 0.15s',
        flexShrink: 0,
      }}
    >
      {label}
    </button>
  );
}

// ── CCTV Grid view helpers ─────────────────────────────────────────────────────

// {lat,lng} 또는 [lat,lng] 모두 허용
function normPt(p) { return Array.isArray(p) ? p : [p.lat, p.lng]; }

// 경로 폴리라인 위에서 progress(0~1) 위치의 [lat, lng]를 선형 보간으로 반환
function interpolateOnRoute(coords, progress) {
  if (!coords || coords.length < 2) return null;
  if (progress <= 0) return [coords[0][0], coords[0][1]];
  if (progress >= 1) return [coords[coords.length - 1][0], coords[coords.length - 1][1]];
  let totalLen = 0;
  const segs = [];
  for (let i = 1; i < coords.length; i++) {
    const d = Math.hypot(coords[i][0] - coords[i-1][0], coords[i][1] - coords[i-1][1]);
    segs.push(d);
    totalLen += d;
  }
  if (totalLen === 0) return [coords[0][0], coords[0][1]];
  const target = progress * totalLen;
  let acc = 0;
  for (let i = 0; i < segs.length; i++) {
    if (acc + segs[i] >= target) {
      const t = segs[i] === 0 ? 0 : (target - acc) / segs[i];
      return [
        coords[i][0] + t * (coords[i+1][0] - coords[i][0]),
        coords[i][1] + t * (coords[i+1][1] - coords[i][1]),
      ];
    }
    acc += segs[i];
  }
  return [coords[coords.length - 1][0], coords[coords.length - 1][1]];
}

// ── mini-map 공통 헬퍼 (module scope) ────────────────────────────────────────

// BS 마커 풀 갱신 + 접속선 갱신
function miniApplyNetwork(map, bsPool, connLineRef, nodes, connName, vehPos) {
  const seen = new Set();
  (nodes || []).forEach(node => {
    if (node.lat == null || node.lng == null) return;
    const id = String(node.id || node.name);
    seen.add(id);
    const isConn = (node.name === connName || String(node.id) === connName);
    const fillColor = isConn ? '#1E88E5' : '#78909C';
    const radius    = isConn ? 7 : 5;
    if (!bsPool[id]) {
      bsPool[id] = {
        marker: L.circleMarker([node.lat, node.lng], {
          radius, fillColor, color: '#fff', weight: 1.5, fillOpacity: 0.92, interactive: false,
        }).addTo(map),
        lat: node.lat, lng: node.lng, name: node.name || String(node.id),
      };
    } else {
      bsPool[id].marker.setStyle({ fillColor, radius });
    }
  });
  Object.keys(bsPool).forEach(id => {
    if (!seen.has(id)) { try { bsPool[id].marker.remove(); } catch (_) {} delete bsPool[id]; }
  });
  // 접속선
  if (connLineRef.current) { try { connLineRef.current.remove(); } catch (_) {} connLineRef.current = null; }
  if (vehPos && connName) {
    const entry = Object.values(bsPool).find(b => b.name === connName);
    if (entry) {
      connLineRef.current = L.polyline(
        [vehPos, [entry.lat, entry.lng]],
        { color: '#1E88E5', weight: 1.5, opacity: 0.6, dashArray: '5 5', interactive: false }
      ).addTo(map);
    }
  }
}

// 배경차량 마커 풀 갱신
function miniApplyBgVehicles(map, bgPool, vehicles) {
  const seen = new Set();
  (vehicles || []).forEach(v => {
    if (v.lat == null || v.lng == null) return;
    seen.add(v.id);
    if (bgPool[v.id]) {
      bgPool[v.id].setLatLng([v.lat, v.lng]);
    } else {
      bgPool[v.id] = L.circleMarker([v.lat, v.lng], {
        radius: 3, color: '#9AA5B1', weight: 0,
        fillColor: '#9AA5B1', fillOpacity: 0.6, interactive: false,
      }).addTo(map);
    }
  });
  Object.keys(bgPool).forEach(id => {
    if (!seen.has(id)) { try { bgPool[id].remove(); } catch (_) {} delete bgPool[id]; }
  });
}

// ─────────────────────────────────────────────────────────────────────────────

function SheetMiniMap({ sheet, isActive,
  liveVehiclePos, liveRouteCoords, liveNetworkTelemetry, liveBackgroundVehicles,
  stations, onReplayTick }) {

  const containerRef = useRef(null);
  const mapRef       = useRef(null);
  const polyRef      = useRef(null);
  const vehRef       = useRef(null);
  const bsPoolRef    = useRef({});   // id → { marker, lat, lng, name }
  const connLineRef  = useRef(null);
  const bgPoolRef    = useRef({});   // id → marker

  const origin = sheet.config?.origin;
  const dest   = sheet.config?.dest;

  // ── 맵 초기화 (sheet.id 바뀔 때만) ──────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    if (mapRef.current) { try { mapRef.current.remove(); } catch (_) {} mapRef.current = null; }
    bsPoolRef.current = {}; bgPoolRef.current = {};
    connLineRef.current = null; polyRef.current = null; vehRef.current = null;

    const map = L.map(containerRef.current, {
      zoomControl: false, attributionControl: false,
      dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false,
    });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, subdomains: 'abcd' }).addTo(map);
    mapRef.current = map;

    // 출발지 / 도착지
    if (origin) L.circleMarker([origin.lat, origin.lng], { radius: 6, fillColor: '#1F9D57', color: '#fff', fillOpacity: 1, weight: 2, interactive: false }).addTo(map);
    if (dest)   L.circleMarker([dest.lat,   dest.lng],   { radius: 6, fillColor: '#E0463C', color: '#fff', fillOpacity: 1, weight: 2, interactive: false }).addTo(map);

    // 경로 (비활성 시트만 — 활성은 live effect에서)
    if (!isActive) {
      const route = sheet.result?.routeCoords || [];
      const norm  = route.map(normPt);
      if (norm.length > 0) {
        polyRef.current = L.polyline(norm, { color: '#1E88E5', weight: 3, opacity: 0.85, interactive: false }).addTo(map);
        map.fitBounds(polyRef.current.getBounds(), { padding: [18, 18] });
      } else if (origin) {
        map.setView([origin.lat, origin.lng], 14);
      } else {
        map.setView([36.4, 127.9], 7);
      }

      // 초기 BS 배치 (최종 network_telemetry의 candidate_nodes 또는 stations)
      const tel  = sheet.result?.network_telemetry;
      const cand = tel?.candidate_nodes?.length ? tel.candidate_nodes : (stations || []);
      const conn = tel?.ego_vehicle?.connected_network_node_name
                || tel?.connected_node?.name || tel?.connected_node?.id;
      miniApplyNetwork(map, bsPoolRef.current, connLineRef, cand, conn, null);
    } else {
      map.setView(origin ? [origin.lat, origin.lng] : [36.4, 127.9], origin ? 14 : 7);
    }

    // invalidateSize 재시도 — CSS Grid 확정 전 검은 화면 방지
    const timers = [80, 300, 700, 1500].map(d =>
      setTimeout(() => { try { map.invalidateSize(); } catch (_) {} }, d)
    );
    let ro;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => { try { map.invalidateSize(); } catch (_) {} });
      ro.observe(containerRef.current);
    }

    return () => {
      timers.forEach(clearTimeout);
      if (ro) ro.disconnect();
      if (mapRef.current) { try { mapRef.current.remove(); } catch (_) {} mapRef.current = null; }
    };
  }, [sheet.id]);

  // ── 활성 시트: 실시간 경로 갱신 ────────────────────────────────────
  useEffect(() => {
    if (!isActive || !mapRef.current || !liveRouteCoords?.length) return;
    const norm = liveRouteCoords.map(normPt);
    if (!polyRef.current) {
      polyRef.current = L.polyline(norm, { color: '#1E88E5', weight: 3, opacity: 0.85, interactive: false }).addTo(mapRef.current);
      mapRef.current.fitBounds(polyRef.current.getBounds(), { padding: [18, 18] });
    } else {
      polyRef.current.setLatLngs(norm);
    }
  }, [isActive, liveRouteCoords]);

  // ── 활성 시트: 실시간 자차 + BS + 접속선 ───────────────────────────
  useEffect(() => {
    if (!isActive || !mapRef.current) return;
    const vehPos = liveVehiclePos ? [liveVehiclePos.lat, liveVehiclePos.lng] : null;

    if (vehPos) {
      if (!vehRef.current) {
        vehRef.current = L.circleMarker(vehPos, { radius: 7, fillColor: '#f44336', color: '#fff', fillOpacity: 1, weight: 2, interactive: false }).addTo(mapRef.current);
      } else {
        vehRef.current.setLatLng(vehPos);
      }
    }

    if (liveNetworkTelemetry) {
      const connName = liveNetworkTelemetry.ego_vehicle?.connected_network_node_name
                    || liveNetworkTelemetry.connected_node?.name
                    || liveNetworkTelemetry.connected_node?.id;
      const cand = liveNetworkTelemetry.candidate_nodes || [];
      miniApplyNetwork(mapRef.current, bsPoolRef.current, connLineRef, cand, connName, vehPos);
    }
  }, [isActive, liveVehiclePos, liveNetworkTelemetry]);

  // ── 활성 시트: 배경차량 ───────────────────────────────────────────
  useEffect(() => {
    if (!isActive || !mapRef.current) return;
    miniApplyBgVehicles(mapRef.current, bgPoolRef.current, liveBackgroundVehicles);
  }, [isActive, liveBackgroundVehicles]);

  // ── 비활성 완료 시트: 리플레이 애니메이션 ───────────────────────────
  // simHistory가 있으면 실제 기록 progress로, 없으면 synthetic 0→1 sweep
  useEffect(() => {
    if (isActive) return;
    const route = sheet.result?.routeCoords;
    if (!route?.length) return;
    const normRoute = route.map(normPt);
    const history   = sheet.result?.simHistory;
    const tel       = sheet.result?.network_telemetry;
    const cand      = tel?.candidate_nodes?.length ? tel.candidate_nodes : (stations || []);

    // BS 이름 → {lat,lng} 조회 테이블
    const bsPos = {};
    cand.forEach(n => { if (n.lat != null) bsPos[n.name || String(n.id)] = [n.lat, n.lng]; });

    const hasHistory = history?.length > 0;
    const TICK_MS    = 50;
    const SWEEP      = 160; // 8초 루프
    let frame = 0;

    const interval = setInterval(() => {
      if (!mapRef.current) return;
      let progress, connBs, latency;

      if (hasHistory) {
        const e = history[frame % history.length];
        progress = e?.progress ?? 0; connBs = e?.bs; latency = e?.latency;
        frame++;
      } else {
        progress = (frame % SWEEP) / SWEEP; frame++;
      }

      // 자차 이동
      const pos = interpolateOnRoute(normRoute, progress);
      if (pos) {
        if (!vehRef.current) {
          vehRef.current = L.circleMarker(pos, { radius: 7, fillColor: '#f44336', color: '#fff', fillOpacity: 1, weight: 2, interactive: false }).addTo(mapRef.current);
        } else {
          vehRef.current.setLatLng(pos);
        }

        // 접속 BS 하이라이트 갱신
        if (connBs) {
          Object.entries(bsPoolRef.current).forEach(([, entry]) => {
            const isConn = entry.name === connBs;
            entry.marker.setStyle({ fillColor: isConn ? '#1E88E5' : '#78909C', radius: isConn ? 7 : 5 });
          });
          // 접속선
          if (connLineRef.current) { try { connLineRef.current.remove(); } catch (_) {} connLineRef.current = null; }
          const bsLatLng = bsPos[connBs];
          if (bsLatLng) {
            connLineRef.current = L.polyline(
              [pos, bsLatLng],
              { color: '#1E88E5', weight: 1.5, opacity: 0.6, dashArray: '5 5', interactive: false }
            ).addTo(mapRef.current);
          }
        }
      }

      if (onReplayTick) onReplayTick({ progress, latency, bs: connBs });
    }, TICK_MS);

    return () => clearInterval(interval);
  }, [isActive, sheet.id, !!sheet.result?.routeCoords?.length]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%', background: '#e8eaf0' }} />;
}

// 개별 시트 셀 — 리플레이 틱을 상태로 관리해야 하므로 별도 컴포넌트로 분리
function SheetGridCell({ sheet, i, activeSheetIdx, onSelectSheet,
  liveVehiclePos, liveRouteCoords, liveNetworkTelemetry, liveBackgroundVehicles,
  stations, sim }) {
  const isActive   = i === activeSheetIdx;
  const isRunning  = isActive && sim.running;
  const isDone     = sheet.status === 'ran';
  const hasRoute   = !!sheet.result?.routeCoords?.length;
  const hasHistory = !!sheet.result?.simHistory?.length;

  // 리플레이 중인 비활성 시트의 현재 프레임 데이터
  const [replayEntry, setReplayEntry] = useState(null);

  const tel = isActive
    ? liveNetworkTelemetry
    : (replayEntry ? { ego_vehicle: { current_latency_ms: replayEntry.latency, connected_network_node_name: replayEntry.bs } }
                   : sheet.result?.network_telemetry);
  const lat = tel?.ego_vehicle?.current_latency_ms ?? tel?.latency_ms;
  const bs  = tel?.ego_vehicle?.connected_network_node_name ?? tel?.connected_node?.name ?? tel?.connected_node?.id;

  // 리플레이 progress bar 값 (0~1)
  const replayProgress = (!isActive && replayEntry?.progress != null) ? replayEntry.progress : null;

  return (
    <div
      key={sheet.id}
      onDoubleClick={() => onSelectSheet(i)}
      style={{
        position: 'relative', borderRadius: 7, overflow: 'hidden',
        border: isActive ? '2px solid #2196f3' : (isDone ? '2px solid #1e3a4a' : '2px solid #2a2a2a'),
        cursor: 'pointer', minHeight: 0, minWidth: 0,
      }}
    >
      <SheetMiniMap
        sheet={sheet} isActive={isActive}
        liveVehiclePos={liveVehiclePos}
        liveRouteCoords={liveRouteCoords}
        liveNetworkTelemetry={isActive ? liveNetworkTelemetry : null}
        liveBackgroundVehicles={isActive ? liveBackgroundVehicles : null}
        stations={stations}
        onReplayTick={setReplayEntry}
      />

      {/* 리플레이 진행 바 (비활성 완료 시트) */}
      {!isActive && isDone && replayProgress != null && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, zIndex: 20, background: 'rgba(255,255,255,0.08)' }}>
          <div style={{ height: '100%', width: `${replayProgress * 100}%`, background: '#2196f3', transition: 'width 0.08s linear' }} />
        </div>
      )}

      {/* top name + status bar */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10,
        background: 'linear-gradient(to bottom, rgba(0,0,0,0.68) 0%, rgba(0,0,0,0) 100%)',
        padding: '8px 10px 22px',
        display: 'flex', alignItems: 'center', gap: 6, pointerEvents: 'none',
      }}>
        <span style={{ color: '#fff', fontSize: 12, fontWeight: 700, flex: 1, textShadow: '0 1px 3px rgba(0,0,0,0.9)' }}>
          {sheet.name}
        </span>
        {isRunning && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#66bb6a', fontWeight: 600 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#66bb6a', display: 'inline-block', boxShadow: '0 0 5px #66bb6a' }} />
            LIVE
          </span>
        )}
        {!isActive && isDone && hasRoute && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#60a5fa', fontWeight: 600 }}>
            <span style={{ fontSize: 9 }}>▶</span> REPLAY
          </span>
        )}
        {isDone && !isRunning && !hasRoute && (
          <span style={{ fontSize: 10, color: '#a5f3fc', padding: '2px 6px', background: 'rgba(34,211,238,0.15)', borderRadius: 4 }}>완료</span>
        )}
        {!isDone && !isRunning && (
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.38)' }}>미실행</span>
        )}
      </div>

      {/* bottom metrics + hint */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 10,
        background: 'linear-gradient(to top, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0) 100%)',
        padding: '22px 10px 8px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
        pointerEvents: 'none',
      }}>
        <span style={{ color: 'rgba(255,255,255,0.38)', fontSize: 9 }}>더블클릭 확장</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {lat != null && (
            <span style={{ color: lat > 20 ? '#ef5350' : '#66bb6a', fontSize: 11, fontWeight: 700 }}>
              {(+lat).toFixed(1)} ms
            </span>
          )}
          {bs && <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 10 }}>{bs}</span>}
        </div>
      </div>
    </div>
  );
}

function SheetGridView({ sheets, activeSheetIdx, onSelectSheet,
  liveVehiclePos, liveRouteCoords, liveNetworkTelemetry, liveBackgroundVehicles,
  stations, sim }) {
  const n    = sheets.length;
  const cols = n <= 1 ? 1 : 2;
  const rows = Math.ceil(n / cols);
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${cols}, 1fr)`,
      gridTemplateRows:    `repeat(${rows}, 1fr)`,
      gap: 3, height: '100%', background: '#111', padding: 3, boxSizing: 'border-box',
    }}>
      {sheets.map((sheet, i) => (
        <SheetGridCell
          key={sheet.id}
          sheet={sheet} i={i}
          activeSheetIdx={activeSheetIdx}
          onSelectSheet={onSelectSheet}
          liveVehiclePos={liveVehiclePos}
          liveRouteCoords={liveRouteCoords}
          liveNetworkTelemetry={liveNetworkTelemetry}
          liveBackgroundVehicles={liveBackgroundVehicles}
          stations={stations}
          sim={sim}
        />
      ))}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────

function SimulationTab({ sim, dispatch, active, vehiclePos, routeCoords, setRouteCoords, setVehiclePos, simNotice, setSimNotice, trafficPrep, autoStartRunId, placementProgress, networkTelemetry, setNetworkTelemetry, simConfig, setSimConfig, backgroundVehicles, setBackgroundVehicles, simLogs, setSimLogs, simHistory, setSimHistory, routeEdges, setRouteEdges, sheets, setSheets, activeSheetIdx, setActiveSheetIdx, api, mode: appMode }) {
  const mapRef  = useRef(null);
  const mapObj  = useRef(null);
  const groups  = useRef({});
  const prevVehPos = useRef(null);
  const bgVehMarkers = useRef({}); // 다중차량 실험군 — 배경 차량 마커 풀 (id → circleMarker), setLatLng로 재사용
  const stationMarkers = useRef({});   // 기지국 마커 풀 (id → circleMarker) — networkTelemetry가 초당 10회 들어와도 매번 재생성하지 않음
  const candidateMarkers = useRef({}); // 후보 노드 마커 풀 (id → circleMarker), 위와 동일한 이유
  const buildingsSigRef = useRef(null); // 직전에 그린 차폐 건물 집합의 서명 — 안 바뀌었으면 폴리곤 다시 안 그림
  // 기지국 배치 더블클릭 가드 — Leaflet은 더블클릭 시 click 이벤트를 2번 발생시켜(doubleClickZoom:false여도
  // 중복 click은 안 막힘) 같은 좌표에 기지국이 2개(1·2번) 꽂히던 문제를 막는다. 마지막으로 처리한 클릭의
  // {시각, 좌표}를 기억했다가 "같은 지점(≈3m) + 400ms 이내" 재클릭이면 두 번째를 무시한다.
  const lastBsClickRef = useRef(null);

  const KR_CENTER = [36.4, 127.9], KR_ZOOM = 7;

  // 로컬 PBF 가용 여부 + 면적 상한 (서버에서 받아옴)
  const [networkInfo, setNetworkInfo] = useState({ local_pbf_available: false, max_area_km2: 25 });
  const MAX_SETUP_AREA_KM2 = networkInfo.max_area_km2;

  const [mode,       setMode]       = useState(null);
  const [area,       setArea]       = useState(null);
  const [origin,     setOrigin]     = useState(null);
  const [originDone, setOriginDone] = useState(false);
  const [dest,       setDest]       = useState(null);
  const [destDone,   setDestDone]   = useState(false);
  const [osmStage,   setOsmStage]   = useState(0); // 0 idle · 1 download · 2 convert · 3 ready
  const [osmSource,  setOsmSource]  = useState(null); // 'local_pbf' | 'overpass' — API 응답에서 설정
  const [osmError,   setOsmError]   = useState(null);
  const [osmWarning, setOsmWarning] = useState(null);
  const [showLayers, setShowLayers] = useState({ vehicles: true, routes: true, stations: true });
  const [simError,   setSimError]   = useState(null);
  const [stations,   setStations]   = useState([]);   // user_created base stations from DB
  const [stationsErr,setStationsErr]= useState(null);
  const [openAlgorithmGroup, setOpenAlgorithmGroup] = useState(null);
  const [selectedAlgorithms, setSelectedAlgorithms] = useState(DEFAULT_ALGORITHM_SELECTION);
  const [networkGen, setNetworkGen] = useState('5g'); // 4g · 5g · 6g — UI only, not wired to backend
  // 교통량 배율(%) — 기준 교통량 N*의 몇 %를 흘릴지. 10~300.
  // 예전의 "다중 차량 대수"를 대체한다. 배경 차량 대수는 이제 입력이 아니라 **결과**다
  // (N* × 배율과 24h 시간곡선이 정하고, 동시 주행 대수는 Little's Law로 따라옴).
  const [demandScalePct, setDemandScalePct] = useState(
    () => simConfig?.policy_options?.demand_scale_pct ?? 100);
  // 백엔드가 준 N*·예상 차량 수. 구역 설정 직후엔 준비 중일 수 있어 폴링한다.
  const [demandStatus, setDemandStatus] = useState(null);
  // 시트 전환 시 simConfig가 통째로 바뀌므로 슬라이더 표시를 거기 맞춘다.
  useEffect(() => {
    const v = simConfig?.policy_options?.demand_scale_pct;
    if (v != null) setDemandScalePct(v);
  }, [simConfig?.policy_options?.demand_scale_pct]);
  const [openPanel, setOpenPanel] = useState('control'); // null · 'control' · 'scenario' — 우측 FAB로 띄우는 플로팅 패널, 처음 열 때는 컨트롤 패널이 기본으로 열려있음

  // ── 행정구역 선택 (전국 OSM PBF 기반) ──────────────────────────
  const [areaMode, setAreaMode] = useState('bbox'); // 'bbox' | 'region'
  const [regionDbAvailable, setRegionDbAvailable] = useState(false);
  const [sidoList, setSidoList] = useState([]);
  const [sigunguList, setSigunguList] = useState([]);
  const [dongList, setDongList] = useState([]);
  const [selSido, setSelSido] = useState(null);
  const [selSigungu, setSelSigungu] = useState(null);
  const [selDong, setSelDong] = useState(null);
  const [regionLoading, setRegionLoading] = useState(false);
  const [regionError, setRegionError] = useState(null);
  const [selectedRegion, setSelectedRegion] = useState(null); // 최종 선택된 region 객체

  // ── 시뮬레이션 시트 (Phase 5) ──────────────────────────────────
  // sheets/activeSheetIdx는 App(app.jsx)으로 끌어올려져 props로 내려온다 — 대시보드 탭도
  // "지금 실행 중인 시트가 뭔지" 같은 출처를 봐야 시트별로 분리해서 보여줄 수 있기 때문.
  const [batchRunning, setBatchRunning] = useState(false);
  const [gridView, setGridView] = useState(false); // CCTV grid view — all sheets side by side
  const [batchError, setBatchError] = useState(null);
  const prevArrived = useRef(false);
  const currentRunIdRef = useRef(null); // /api/simulation/start가 돌려준 DB simulation_runs.id — 도착 시 시트 데이터를 같은 행에 영구 저장하는 데 씀

  // 교통 준비 후 **자동 시작**된 런은 시작 응답이 "preparing"이라 run_id가 없었다.
  // WS traffic_prep이 뒤늦게 알려주므로 여기서 위 ref에 채워 넣는다.
  useEffect(() => {
    if (autoStartRunId != null) currentRunIdRef.current = autoStartRunId;
  }, [autoStartRunId]);

  // 캡처된 결과가 있으면(이미 실행 완료된 시트) 그 로그를, 없으면(현재 진행 중인 시트) 실시간
  // simLogs를 보여준다 — 둘 다 같은 시트 안에서만 머무르고 분석보고서 탭으로는 넘어가지 않는다.
  const displayedSheetLogs = sheets[activeSheetIdx]?.result?.simLogs?.length
    ? sheets[activeSheetIdx].result.simLogs
    : (simLogs || []);

  function currentConfigSnapshot() {
    return { origin, dest, demandScalePct, selectedAlgorithms, networkGen, simConfig };
  }

  function loadSheetConfig(sheet) {
    const c = sheet.config || {};
    // 구역·출발지·도착지·차량 수는 모든 시트 공유 — 시트 전환 시 복원하지 않음.
    // 알고리즘 설정과 결과만 시트별로 로드한다.
    setSelectedAlgorithms(c.selectedAlgorithms || DEFAULT_ALGORITHM_SELECTION);
    setNetworkGen(c.networkGen || '5g');
    if (c.simConfig) setSimConfig(c.simConfig);
    setNetworkTelemetry(sheet.result?.network_telemetry || null);
    setRouteCoords(sheet.result?.routeCoords || []);
    setVehiclePos(sheet.result?.vehiclePos || null);
    if (setRouteEdges)  setRouteEdges(sheet.result?.routeEdges ?? null);
    if (setSimLogs)     setSimLogs(sheet.result?.simLogs ?? []);
    if (setSimHistory)  setSimHistory(sheet.result?.simHistory ?? []);
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
    // 새 시트 = 같은 환경(구역·기지국·RSU·출발지·도착지)에서 알고리즘/설정만 바꿔 비교.
    // 구역·기지국·출발지·도착지는 모든 시트에서 공유하므로 초기화하지 않는다.
    // 이전 시트의 실행 결과(경로·차량·로그)만 지우고 알고리즘 선택을 기본값으로 되돌린다.
    //
    // 주의: 이전 시트가 아직 실행 중이면 /api/simulation/stop으로 멈춘다. 안 그러면
    // 웹소켓 위치 업데이트가 새로 만든 시트로 잘못 캡처된다.
    if (sim.running) { try { await fetch(`${api}/api/simulation/stop`, { method: 'POST' }); } catch (_) {} }

    // 새 시트 config: 공유 환경(origin/dest/demandScalePct)은 현재 전역값으로 초기화,
    // 알고리즘은 기본값으로 리셋 — mini map 표시 및 배치 실행에 origin/dest 참조 가능하도록 저장.
    const blankConfig = { origin, dest, demandScalePct, selectedAlgorithms: DEFAULT_ALGORITHM_SELECTION, networkGen, simConfig };
    const newSheet = { id: `sheet-${Date.now()}`, name: `Sheet ${sheets.length + 1}`, config: blankConfig, result: null, status: 'draft' };
    const next = sheets.map((s, i) => i === activeSheetIdx ? { ...s, config: currentConfigSnapshot() } : s).concat(newSheet);
    setSheets(next); saveSimSheets(next);
    setActiveSheetIdx(next.length - 1);

    // 알고리즘 선택만 초기화 (구역·기지국·출발지·도착지·OSM 네트워크는 그대로 유지)
    setSelectedAlgorithms(DEFAULT_ALGORITHM_SELECTION);
    setNetworkTelemetry(null);
    setRouteCoords([]); setVehiclePos(null);
    if (setBackgroundVehicles) setBackgroundVehicles([]);
    setSimError(null); setSimNotice(null);
    prevArrived.current = false;
    dispatch({ type: 'reset' });
    // 경로·차량·배경차량 레이어만 초기화 (구역·네트워크·BS·출발지·도착지 마커는 유지)
    if (groups.current.veh)    { groups.current.veh.remove(); groups.current.veh = null; }
    if (groups.current.route)  groups.current.route.clearLayers();
    if (groups.current.bgVeh)  groups.current.bgVeh.clearLayers();
    bgVehMarkers.current = {};
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
    // 공유 환경(origin/dest/demandScalePct)이 설정된 경우에만 배치 실행 가능
    if (!origin || !dest) { setBatchError('출발지·도착지를 먼저 설정하세요.'); return; }

    const allSheets = sheets.map((s, i) => i === activeSheetIdx ? { ...s, config: currentConfigSnapshot() } : s);
    setSheets(allSheets); saveSimSheets(allSheets);

    // 모든 시트에 공유 출발지·도착지·차량 수 적용 — 알고리즘만 시트별로 달라짐
    const specs = allSheets.map(s => ({
        id: s.id,
        label: s.name,
        mode: 'route_metrics',
        origin: origin,
        dest: dest,
        vehicle_count: 1,   // 타겟 차량만. 배경 차량은 demand_scale_pct가 정한다(백엔드 생성 교통)
        algorithm_config: s.config.selectedAlgorithms || {},
        simulation_config: {
          ...(s.config.simConfig || {}),
          policy_options: { ...(s.config.simConfig?.policy_options || {}), network_mode: (s.config.networkGen || '5g').toUpperCase() },
        },
      }));
    if (specs.length === 0) { setBatchError('시트가 없습니다.'); return; }

    // 이전 런의 경로·기록이 새 배치와 겹치지 않도록 먼저 지운다
    setRouteCoords([]); setVehiclePos(null);
    if (setSimHistory) setSimHistory([]);
    if (setRouteEdges) setRouteEdges(null);

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

  function pollSheetBatch(batchId, onDone) {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${api}/api/scenarios/batch/${batchId}`);
        const data = await res.json();
        if (data.status === 'completed') {
          clearInterval(timer);
          setBatchRunning(false);

          // 분석보고서용 저장 (storage event로 report 탭이 자동 갱신됨)
          const saved = scbLoadBatches();
          saved.push({ batch_id: batchId, label: data.label, started_at: data.started_at, ended_at: data.ended_at, results: data.results });
          scbSaveBatches(saved);

          // 시트별 결과 역매핑
          const resultById = {};
          (data.results || []).forEach(r => { if (r.id) resultById[r.id] = r; });

          const routeEdgesFrom = (r) => r?.route_cost_result ? {
            per_edge:         r.route_cost_result.per_edge || [],
            coverage_risk:    r.route_cost_result.coverage_risk ?? null,
            avg_latency_ms:   r.route_cost_result.avg_latency_ms ?? null,
            handover_count:   r.route_cost_result.handover_count ?? null,
            total_cost:       r.route_cost_result.total_cost ?? null,
            total_distance_m: r.route_cost_result.total_distance_m ?? null,
            routing_mode:     r.route_cost_result.routing_mode ?? null,
            edge_names:       r.network_telemetry?.route_edge_names ?? {},
          } : null;

          setSheets(prev => {
            const next = prev.map(s => {
              const r = resultById[s.id];
              if (!r || r.status !== 'done') return s;
              return {
                ...s, status: 'done',
                result: {
                  ...(s.result || {}),
                  routeCoords:        r.route_coords || [],
                  routeEdges:         routeEdgesFrom(r),
                  network_telemetry:  r.network_telemetry || null,
                  route_cost_result:  r.route_cost_result || null,
                  algorithm_metrics:  r.algorithm_metrics || null,
                  simulation_summary: r.simulation_summary || null,
                  allocation_result:  r.allocation_result || null,
                  simLogs:            s.result?.simLogs || [],
                  simHistory:         s.result?.simHistory || [],
                },
              };
            });
            saveSimSheets(next);

            // 현재 활성 시트 즉시 반영
            const activeR = resultById[next[activeSheetIdx]?.id];
            if (activeR?.status === 'done') {
              setRouteCoords(activeR.route_coords || []);
              setNetworkTelemetry(activeR.network_telemetry || null);
              if (setRouteEdges) setRouteEdges(routeEdgesFrom(activeR));
            }
            return next;
          });

          if (onDone) onDone(data);
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

  // ── GNN-MAML vs 기준선 비교 — Pro 전용.
  // 시트 3개를 자동 생성(GNN-MAML / Nearest BS / RSRP Max)하고 route_metrics 배치로 실행한다.
  // 결과는 각 시트의 result에 저장되어 지도에 경로가 표시되고, 분석보고서에서도 확인 가능.
  const [rlRunning, setRlRunning] = useState(false);
  const [rlError, setRlError] = useState(null);
  const [rlDone, setRlDone] = useState(false);
  const [gnnReady, setGnnReady] = useState(false);
  useEffect(() => {
    fetch(`${api}/api/rl/v4/status`).then(r => r.json()).then(d => {
      setGnnReady(!!d.v4_bs_selector_ready);
    }).catch(() => {});
  }, []);

  async function runRLComparison() {
    if (!ready) { setRlError('구역·출발지·도착지를 먼저 설정하세요.'); return; }

    // 3개 비교 시트 자동 생성 — 기존 시트를 교체한다
    const now = Date.now();
    const rlSheets = [
      { id: `rl-gnn-${now}`,     name: 'GNN-MAML',   config: { selectedAlgorithms: { base_station_selection: 'v4_gnn' },    networkGen: networkGen || '5g' }, result: null, status: 'draft' },
      { id: `rl-nearest-${now}`, name: 'Nearest BS', config: { selectedAlgorithms: { base_station_selection: 'nearest_bs' }, networkGen: networkGen || '5g' }, result: null, status: 'draft' },
      { id: `rl-rsrp-${now}`,    name: 'RSRP Max',   config: { selectedAlgorithms: { base_station_selection: 'rsrp_max' },   networkGen: networkGen || '5g' }, result: null, status: 'draft' },
    ];
    setSheets(rlSheets); setActiveSheetIdx(0); saveSimSheets(rlSheets);

    // 이전 경로 초기화
    setRouteCoords([]); setVehiclePos(null); setNetworkTelemetry(null);
    if (setSimHistory) setSimHistory([]);
    if (setRouteEdges) setRouteEdges(null);

    const specs = rlSheets.map(s => ({
      id: s.id, label: s.name,
      mode: 'route_metrics', origin, dest, vehicle_count: 1,
      algorithm_config: s.config.selectedAlgorithms || {},
      simulation_config: { policy_options: { network_mode: (networkGen || '5g').toUpperCase() } },
    }));

    setRlRunning(true); setBatchRunning(true); setRlError(null); setRlDone(false); setBatchError(null);
    try {
      const res = await fetch(`${api}/api/scenarios/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: 'GNN-MAML 비교 (시트)', scenarios: specs }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      pollSheetBatch(data.batch_id, () => { setRlRunning(false); setRlDone(true); setTimeout(() => setRlDone(false), 3500); });
    } catch (e) {
      setRlRunning(false); setBatchRunning(false);
      setRlError(e.message || 'RL 비교 실행 중 오류가 발생했습니다.');
    }
  }

  const coordStr = (ll) => `${ll.lat.toFixed(4)}, ${ll.lng.toFixed(4)}`;
  const ready    = area && originDone && destDone;
  // 실행 설정(알고리즘·네트워크 세대) 잠금 — 시작 후에는 초기화 전까지 변경 불가.
  // 일시정지 상태도 잠긴다(이미 시작된 런이므로).
  const isConfigLocked = configLocked(sim);

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

    // 새로고침 복원: 기지국은 DB에서 다시 불러오지만 구역(bbox)은 백엔드 메모리에만
    // 있어 사각형이 사라졌었다 — 백엔드가 network_ready면 구역을 다시 그린다.
    fetch(`${api}/api/setup-network/status`)
      .then(r => r.json())
      .then(d => {
        if (!d?.network_ready || !d.bbox || !mapObj.current) return;
        if (groups.current.areaRect) return; // 이미 그려져 있으면(드래그 등) 건드리지 않음
        const b = [[d.bbox.s, d.bbox.w], [d.bbox.n, d.bbox.e]];
        groups.current.areaRect = L.rectangle(b, {
          color: '#1E3A5F', weight: 1.6, dashArray: '6 4',
          fillColor: '#2E75B6', fillOpacity: 0.10,
        }).addTo(mapObj.current);
        mapObj.current.fitBounds(b, { padding: [50, 50] });
        setArea({ s: d.bbox.s, w: d.bbox.w, n: d.bbox.n, e: d.bbox.e });
      })
      .catch(() => {}); // 구버전 백엔드(엔드포인트 없음)면 조용히 무시

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

  /* ── 네트워크 소스 정보 (로컬 PBF 여부 + 면적 상한) ─────────── */
  useEffect(() => {
    fetch(`${api}/api/setup-network/info`)
      .then(r => r.json())
      .then(d => setNetworkInfo(d))
      .catch(() => {});
  }, []);

  /* ── 행정구역 DB 상태 확인 ───────────────────────────────────── */
  useEffect(() => {
    fetch(`${api}/api/regions/status`)
      .then(r => r.json())
      .then(d => {
        setRegionDbAvailable(d.available);
        if (d.available) {
          fetch(`${api}/api/regions/sido`)
            .then(r => r.json())
            .then(d => setSidoList(d.regions || []));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!selSido) { setSigunguList([]); setSelSigungu(null); setDongList([]); setSelDong(null); return; }
    fetch(`${api}/api/regions/sigungu?parent_osm_id=${selSido.osm_id}`)
      .then(r => r.json())
      .then(d => { setSigunguList(d.regions || []); setSelSigungu(null); setDongList([]); setSelDong(null); });
  }, [selSido]);

  useEffect(() => {
    if (!selSigungu) { setDongList([]); setSelDong(null); return; }
    fetch(`${api}/api/regions/dong?parent_osm_id=${selSigungu.osm_id}`)
      .then(r => r.json())
      .then(d => { setDongList(d.regions || []); setSelDong(null); });
  }, [selSigungu]);

  /* ── 행정구역으로 구역 확정 ──────────────────────────────────── */
  async function finalizeAreaFromRegion() {
    const region = selDong || selSigungu || selSido;
    if (!region) return;
    setSelectedRegion(region);
    setOsmError(null);
    setOsmWarning(null);
    setSimNotice(null);
    setOsmSource('local_pbf'); // 행정구역 모드는 항상 로컬 PBF
    setOsmStage(1);
    setRegionError(null);

    try {
      const res = await fetch(`${api}/api/setup-network-region`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ osm_id: region.osm_id }),
      });
      setOsmStage(2);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Network setup failed');

      setArea({ s: region.min_lat, w: region.min_lon, n: region.max_lat, e: region.max_lon });
      setOsmStage(3);
      if (mapObj.current) {
        mapObj.current.fitBounds([[region.min_lat, region.min_lon], [region.max_lat, region.max_lon]], { padding: [50, 50] });
        if (groups.current.areaRect) groups.current.areaRect.remove();
        groups.current.areaRect = L.rectangle(
          [[region.min_lat, region.min_lon], [region.max_lat, region.max_lon]],
          { color: '#1F9D57', weight: 2, fillOpacity: 0.04 }
        ).addTo(mapObj.current);
      }
      setTimeout(() => setOsmStage(0), 1200);
    } catch (e) {
      setRegionError(e.message);
      setOsmStage(0);
    }
  }

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
      const isRsu = st.node_type === 'rsu';
      // BS = 파란 원(#1E88E5), RSU = 주황 다이아몬드 효과(#FF8F00, 더 작고 테두리 두꺼움)
      const fillColor = isRsu ? '#FF8F00' : '#1E88E5';
      const radius    = isRsu ? 6 : 8;
      const weight    = isRsu ? 3 : 2.5;
      let marker = stationMarkers.current[st.id];
      if (!marker) {
        marker = L.circleMarker([st.lat, st.lng], {
          radius, color: '#fff', weight, fillColor, fillOpacity: 0.93, interactive: true,
        }).addTo(g);
        const typeLabel = isRsu ? '[RSU]' : '[BS]';
        marker.bindTooltip(`${typeLabel} ${st.name}`, { permanent: true, direction: 'bottom', offset: [0, 6], className: 'bs-label' });
        stationMarkers.current[st.id] = marker;
      } else {
        marker.setLatLng([st.lat, st.lng]);
      }
      marker.off('mouseover'); marker.off('mouseout'); marker.off('click');
      marker.setStyle({ fillColor, color: '#fff' });
      if (deleteMode) {
        marker.on('mouseover', (e) => { e.target.setStyle({ fillColor: '#9AA5B1', color: '#5B6670' }); });
        marker.on('mouseout',  (e) => { e.target.setStyle({ fillColor, color: '#fff' }); });
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

    // 빨간 점. 예전엔 기지국과 같은 파란 계열(#2E75B6)이라 지도에서 구분이 안 됐다.
    // 기지국(파랑)·RSU(주황)과 겹치지 않는 색이어야 타겟 차량이 한눈에 보인다.
    const color = '#E0463C';
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
  // 실행이 시작되면 지도 상호작용 모드도 강제 해제한다 — 패널만 잠그면, 잠기기 직전에
  // 켜둔 모드(구역 그리기·기지국 배치/삭제 등)가 지도에서 그대로 살아있어 우회로가 된다.
  useEffect(() => {
    if (isConfigLocked && mode) setMode(null);
  }, [isConfigLocked, mode]);

  useEffect(() => {
    const map = mapObj.current; if (!map) return;
    if (isConfigLocked) return;   // 잠금 중에는 어떤 지도 핸들러도 붙이지 않는다
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
      const onClick = (e) => {
        const { lat, lng } = e.latlng;
        const now = Date.now();
        const last = lastBsClickRef.current;
        // 더블클릭이 뿜는 두 번째 click(같은 지점 ≈3m, 400ms 이내)은 무시 — 한 제스처당 기지국 1개.
        // 다른 지점을 빠르게 연속 배치하는 정상 동작은 좌표가 달라 차단되지 않는다.
        if (last && now - last.t < 400 &&
            Math.abs(lat - last.lat) < 3e-5 && Math.abs(lng - last.lng) < 3e-5) return;
        lastBsClickRef.current = { t: now, lat, lng };
        createStation(lat, lng);
      };
      map.on('click', onClick);
      return () => { map.off('click', onClick); map.getContainer().style.cursor = ''; };
    }
    if (mode === 'bs_delete') {
      map.getContainer().style.cursor = 'crosshair';
      return () => { map.getContainer().style.cursor = ''; };
    }
  }, [mode, isConfigLocked]);

  /* ── user-created base station create / delete ───────────────── */
  async function createStation(lat, lng, nodeTypeOverride) {
    setStationsErr(null);
    const nt = nodeTypeOverride !== undefined ? nodeTypeOverride : (stationType === 'rsu' ? 'rsu' : 'base_station');
    try {
      const res = await fetch(`${api}/network-nodes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lng, node_type: nt }),
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

  // method: 'random'(블루노이즈 균등) | 'sa'(최적화). counts={bs,rsu}. setBusy로 버튼별 로딩 표시.
  async function placeNodes(placeMethod, counts, replace, setBusy) {
    if (!area || (counts.bs + counts.rsu) === 0) return;
    setStationsErr(null);
    setBusy(true);
    try {
      const res = await fetch(`${api}/network-nodes/auto-place`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          n_bs: counts.bs,
          n_rsu: counts.rsu,
          method: placeMethod,
          network_mode: networkGen.toUpperCase(),
          spread: 10,
          replace_existing: replace,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || '배치 실패');
      await loadStations();
      setPlaceResult(body.optimization || null);
      if (body.warnings && body.warnings.length) setStationsErr(body.warnings.join(' '));
    } catch (e) {
      setStationsErr(e.message);
    } finally {
      setBusy(false);
    }
  }
  /* ── finalizeArea — real OSM + netconvert via backend ────────── */
  async function finalizeArea(bounds) {
    setArea({ s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() });
    setMode(null);
    setOsmError(null);
    setOsmWarning(null);
    setSimNotice(null);
    setOsmSource(null);
    setOsmStage(1); // downloading

    try {
      const res = await fetch(`${api}/api/setup-network`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bbox: { s: bounds.getSouth(), w: bounds.getWest(), n: bounds.getNorth(), e: bounds.getEast() }
        }),
      });

      setOsmStage(2); // converting

      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || 'Network setup failed');
      }

      setOsmSource(body.source || 'overpass'); // 'local_pbf' or 'overpass' — from backend
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

  /* ── 교통 준비 상태 폴링 ──────────────────────────────────────
     구역을 정하면 백엔드가 백그라운드로 교통을 만든다(N* 보정 포함, 처음엔 수 분).
     준비되면 N*와 예상 차량 수를 받아 배율 슬라이더 옆에 띄운다. */
  useEffect(() => {
    if (!area) { setDemandStatus(null); return; }
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch(`${api}/api/demand/status`);
        if (!res.ok) return;
        const body = await res.json();
        if (alive) setDemandStatus(body);
      } catch { /* 준비 중 네트워크 오류는 무시 — 다음 틱에 다시 본다 */ }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [area, api]);

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
  const [stationType, setStationType] = useState('bs'); // 'bs' | 'rsu' — 생성 버튼 클릭 시 어떤 노드를 만들지
  const tryBsCreate = () => { if (area) setMode(mode === 'bs_create' ? null : 'bs_create'); };
  const tryBsDelete = () => { if (area) setMode(mode === 'bs_delete' ? null : 'bs_delete'); };

  // 배치 방식 — 'manual'(직접 클릭) | 'auto'(랜덤 균등). 최적화(SA)는 별도 섹션.
  const [placeMode, setPlaceMode] = useState('manual');
  // 최적화 배치 결과 진단 — 백엔드 auto-place 응답의 optimization 블록
  const [placeResult, setPlaceResult] = useState(null);
  // 자동 배치(랜덤·블루노이즈) 개수/상태. 항상 '기존 지우고 새로'(replace) — 누적 추가는 미지원.
  const [autoN, setAutoN] = useState({ bs: 0, rsu: 0 });
  const [autoPlacing, setAutoPlacing] = useState(false);
  // 최적화 배치(SA) 개수/상태 — 랜덤과 독립적으로 개수 지정 가능
  const [saN, setSaN] = useState({ bs: 0, rsu: 0 });
  const [saPlacing, setSaPlacing] = useState(false);
  // 통신모드별 목표 간격(m). 5G≈450m→약 5개/km²(2km²당 10개). BS·RSU 동일 밀도(5/km²).
  const BS_SPACING_M = { '4g': 550, '5g': 450, '6g': 350 };
  // 구역/모드가 바뀌면 권장 개수 재산출 (사용자가 입력란을 고치면 다음 변경 전까지 그 값 유지)
  useEffect(() => {
    if (!area) return;
    const km2 = areaKm2(area);
    const s = BS_SPACING_M[networkGen] || 450;
    const n = Math.max(1, Math.round((km2 * 1e6) / (s * s)));
    setAutoN({ bs: n, rsu: n });
    setSaN({ bs: n, rsu: n });
  }, [area, networkGen]);

  // 재생 배속 — 백엔드 시뮬 스레드가 매 틱 읽으므로 실행 중에 바꿔도 즉시 반영된다.
  // 상한은 SUMO 스텝 비용이라 8×를 눌러도 체감은 4~6× 근처에서 포화한다.
  const [simSpeed, setSimSpeed] = useState(1);
  async function changeSpeed(v) {
    setSimSpeed(v);
    try {
      await fetch(`${api}/api/simulation/speed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: v }),
      });
    } catch (_) { /* 배속은 부가 기능 — 실패해도 시뮬 진행을 막지 않는다 */ }
  }
  // Lite 전용 예시 시나리오 프리셋 — 학부생이 출발지/도착지를 직접 찍지 않아도 현재 구역
  // 안에서 바로 시작할 수 있게, 구역(area) 내부 좌표를 비율로 계산해 채운다(특정 도시
  // 좌표를 하드코딩하지 않으므로 어떤 구역을 그려도 항상 동작한다).
  function applyPreset(kind) {
    if (!area) return;
    const { s, w, n, e } = area;
    const latSpan = n - s, lngSpan = e - w;
    const pt = (fLat, fLng) => ({ lat: s + latSpan * fLat, lng: w + lngSpan * fLng });
    // ⚠️ 예전엔 여기서 setVehicleCount()도 불렀는데, 차량 수 UI가 제거되면서
    // 그 setter가 사라졌다. 남아 있던 호출이 ReferenceError를 던져 이 함수가
    // 중간에 죽고 있었다(2026-07-29). 배경 차량 수는 이제 교통량 배율이 정한다.
    if (kind === 'short') {
      setOrigin(pt(0.35, 0.35)); setOriginDone(true);
      setDest(pt(0.55, 0.55)); setDestDone(true);
    } else if (kind === 'congested') {
      setOrigin(pt(0.15, 0.15)); setOriginDone(true);
      setDest(pt(0.85, 0.85)); setDestDone(true);
    }
  }

  async function handleStart() {
    if (!ready) return;
    setSimError(null);
    setSimNotice(null);

    // 일시정지 상태이면 재개 (vehiclePos/routeCoords 유지 — 도착 완료 후 재시작은 제외)
    if (!sim.running && !sim.finished && sim.elapsed > 0 && !vehiclePos?.arrived) {
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

    // 새 시뮬레이션 시작 (최초 시작 또는 완료 후 재시작)
    // 이전 런의 로그·히스토리·경로 엣지를 초기화해 결과 중복 방지
    setRouteCoords([]);
    setVehiclePos(null);
    if (setSimLogs) setSimLogs([]);
    if (setSimHistory) setSimHistory([]);
    if (setRouteEdges) setRouteEdges(null);

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
          vehicle_count: 1,   // 타겟 차량만. 배경 차량은 demand_scale_pct가 정한다(백엔드 생성 교통)
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
      // 교통(N* 보정 등)이 아직 없으면 백엔드가 기다리지 않고 "preparing"으로 돌려준다.
      // 준비가 끝나면 백엔드가 알아서 시작하고, WS traffic_prep(preparing=false)을 받은
      // app.jsx가 그때 실행 상태로 전환한다 — 여기서 미리 start를 걸면 아직 달리지도
      // 않은 런이 "실행 중"으로 보인다.
      if (body.status === 'preparing') return;
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

  /* 재생 버튼 옆 초기화 — **실행 결과만** 지운다.
     구역·출발지·도착지·기지국·생성 교통은 그대로 둬서 곧바로 다시 재생할 수 있어야 한다.
     ⚠️ 예전에는 scope 없이 /api/simulation/reset을 불러 기본값 full로 갔고, 백엔드의
     구역·도로망·생성 교통까지 날아갔다. 그러면 ready(= area && originDone && destDone)가
     false가 되어 **재생 버튼이 영구히 비활성**이 됐다(2026-07-29 사용자 보고 1·2번).
     구역까지 비우려면 탭 맨 아래 "전체 초기화"(clearAll)를 쓴다. */
  async function clearRun() {
    try {
      await fetch(`${api}/api/simulation/reset?scope=runtime`, { method: 'POST' });
    } catch (_) {}
    setSimError(null);
    setSimNotice(null);
    setNetworkTelemetry(null);
    setRouteCoords([]); setVehiclePos(null);
    if (setSimLogs) setSimLogs([]);
    if (setSimHistory) setSimHistory([]);
    if (setRouteEdges) setRouteEdges(null);
    if (setBackgroundVehicles) setBackgroundVehicles([]);
    prevArrived.current = false;
    currentRunIdRef.current = null;
    // 시트는 실행 결과만 비운다 — config(출발지·도착지·알고리즘 선택)는 유지해야
    // 같은 조건으로 바로 다시 돌릴 수 있다.
    setSheets(prev => {
      const next = prev.map((s, i) => i === activeSheetIdx ? { ...s, result: null, status: 'draft' } : s);
      saveSimSheets(next);
      return next;
    });
    // 실행 산출물 레이어만 정리 — 구역 사각형·기지국·출발/도착 마커는 건드리지 않는다.
    if (groups.current.veh)       { groups.current.veh.remove(); groups.current.veh = null; }
    if (groups.current.route)     { groups.current.route.clearLayers(); }
    if (groups.current.connLines) { groups.current.connLines.clearLayers(); }
    if (groups.current.bgVeh)     { groups.current.bgVeh.clearLayers(); }
    bgVehMarkers.current = {};
    dispatch({ type: 'reset' });
  }

  /* 전체 초기화 — 구역·출발지·도착지까지 전부 비운다. 탭 맨 아래에만 둔다. */
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
    // ⚠️ 여기 있던 setVehicleCount(1)이 **정의되지 않은 setter**라 ReferenceError를 던졌고,
    // 이 함수가 그 줄에서 죽어 아래의 시트 초기화·지도 레이어 정리가 통째로 실행되지
    // 않았다. 그래서 "초기화를 해도 구역 사각형과 출발/도착 핀이 지도에 남는" 증상이
    // 났다(2026-07-29 실측: 앞쪽 상태만 지워지고 areaRect.remove()는 호출 0회).
    // 차량 수 UI가 제거될 때 같이 지워졌어야 할 줄이다.
    prevArrived.current = false;
    if (setBackgroundVehicles) setBackgroundVehicles([]);
    // 현재 시트도 빈 draft 상태로 되돌린다 — 안 그러면 화면은 지워져도 시트에 저장된 이전
    // config/result가 그대로 남아있어, 새로고침하거나 이 시트로 다시 돌아오면 방금 지운
    // 결과(연결선·latency 등)가 되살아난다. 기지국 배치는 별도 자원이라 건드리지 않음.
    setSheets(prev => {
      const next = prev.map((s, i) => i === activeSheetIdx ? {
        ...s,
        config: { origin: null, dest: null, demandScalePct: 100, selectedAlgorithms: DEFAULT_ALGORITHM_SELECTION, networkGen, simConfig },
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
    if (mode === 'bs_create') return stationType === 'rsu'
      ? '지도에서 RSU를 설치할 위치를 클릭하세요 — 가장 가까운 교차로로 자동 스냅됩니다.'
      : '지도에서 기지국을 배치할 위치를 클릭하세요 — 가장 가까운 건물 옥상으로 자동 스냅됩니다.';
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
            <LegendRow shape="circle" color="#1E88E5" label="기지국 (BS)">
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{stations.filter(s=>s.node_type!=='rsu').length}개</span>
            </LegendRow>
            <LegendRow shape="circle" color="#FF8F00" label="RSU">
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{stations.filter(s=>s.node_type==='rsu').length}개</span>
            </LegendRow>
            {(backgroundVehicles?.length || 0) > 0 && (
              <LegendRow shape="circle" color="#9AA5B1" label="배경 차량">
                <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{backgroundVehicles.length}대</span>
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
              {(() => {
                const isLocal = osmSource === 'local_pbf' || (osmSource === null && (areaMode === 'region' || networkInfo.local_pbf_available));
                return [
                  [isLocal ? 'OSM 추출 (로컬 PBF)' : 'OSM 다운로드 (Overpass API)', 'Downloading OSM'],
                  ['SUMO 네트워크 변환 (netconvert)', 'Converting network'],
                  ['시뮬레이션 준비 완료', 'Ready'],
                ];
              })().map((s, i) => {
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

      {/* ── 세로형 탭 버튼 — 컨트롤 패널 / 시뮬레이션 챗봇, 지도 우측 가장자리에 붙임 ─ */}
      <div style={{ position: 'absolute', right: 0, top: 20, zIndex: 650, display: 'flex', flexDirection: 'column', gap: 5 }}>
        <TabButton
          label="Control"
          active={openPanel === 'control'}
          onClick={() => setOpenPanel(p => p === 'control' ? null : 'control')}
        />
        <TabButton
          label="Sim Chat"
          active={openPanel === 'scenario'}
          onClick={() => setOpenPanel(p => p === 'scenario' ? null : 'scenario')}
        />
      </div>

      {/* ── 플로팅 패널 — 탭 버튼 클릭 시 왼쪽으로 펼쳐짐 ──────────── */}
      {openPanel && (
        <div style={{
          position: 'absolute', top: 14, bottom: 14, right: 28,
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
                    <span className={'status-badge ' + (sim.running ? 'running' : sim.finished ? 'done' : sim.elapsed > 0 ? 'paused' : 'idle')}>
                      <span className="dot" />{sim.running ? '실행 중' : sim.finished ? '완료' : sim.elapsed > 0 ? '일시정지' : '대기'}
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

          {/* ── 실행 설정 전체 잠금 영역 ────────────────────────────────────
              구역·ITS·경로 지점·차량 수·기지국/RSU·네트워크 세대·알고리즘·실험 트리거까지,
              한 번 시작하면 전부 잠긴다(시작 시점 설정으로 런이 고정되므로). 아래 진행률·
              텔레메트리 등 "읽기 전용 표시"는 이 영역 밖이라 계속 선명하게 보인다. */}
          {isConfigLocked && (
            <div className="row gap8" style={{ padding: '9px 12px', background: 'var(--warn-tint)', borderRadius: 9,
              fontSize: 10.5, color: 'var(--warn-ink, var(--warn))', lineHeight: 1.5 }}>
              <Icon.warn size={13} style={{ flex: '0 0 auto', marginTop: 1 }} />
              <span>실행이 시작되어 <b>설정이 잠겼습니다</b>. 주행 경로·기지국 배치는 시작 시점 값으로
                고정됩니다. 바꾸려면 <b>시나리오 초기화</b> 후 다시 설정하세요.</span>
            </div>
          )}
          <fieldset
            className={'cfg-lock' + (isConfigLocked ? ' locked' : '')}
            disabled={isConfigLocked}
            style={{ display: 'flex', flexDirection: 'column', gap: 18 }}
          >

          {/* area */}
          <div className="col gap8">
            <div className="field">
              <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                구역 선택 <span className="en">AREA</span>
                <span style={{ fontSize: 9.5, padding: '1px 6px', borderRadius: 4, background: 'var(--brand-tint)', color: 'var(--brand-2)', fontWeight: 500, marginLeft: 2 }}>모든 시트 공유</span>
              </label>
            </div>

            {/* 구역 선택 모드 토글 */}
            <Seg value={areaMode} onChange={v => { setAreaMode(v); setOsmError(null); setRegionError(null); }}
              options={[
                { v: 'bbox', label: '지도에서 그리기' },
                { v: 'region', label: '행정구역 선택', disabled: !regionDbAvailable },
              ]}
            />
            {!regionDbAvailable && (
              <div className="muted" style={{ fontSize: 10, marginTop: -4 }}>행정구역 DB 미설치 (build_region_index.py 실행 필요)</div>
            )}

            {/* 지도 그리기 모드 */}
            {areaMode === 'bbox' && (area ? (
              <>
                <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>{areaKm2(area).toFixed(2)} km²</div>
                  <Chip tone="good" dot>선택됨</Chip>
                </div>
                {areaKm2(area) > MAX_SETUP_AREA_KM2 ? (
                  <div className="row gap8" style={{ padding: '8px 11px', background: 'var(--bad-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--bad)' }}>
                    <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
                    {`구역이 너무 큽니다 (${areaKm2(area).toFixed(0)}km²). ${MAX_SETUP_AREA_KM2}km² 이하로 줄여주세요.`}
                  </div>
                ) : areaKm2(area) > 50 && (
                  <div className="row gap8" style={{ padding: '8px 11px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--warn)' }}>
                    <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
                    구/시 단위 — netconvert 변환에 시간이 걸릴 수 있습니다
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
            ))}

            {/* 행정구역 선택 모드 */}
            {areaMode === 'region' && regionDbAvailable && (
              <div className="col gap8">
                {/* 도/광역시 선택 */}
                <div className="col gap4">
                  <div style={{ fontSize: 10.5, color: 'var(--ink-3)', fontWeight: 600 }}>도 / 광역시</div>
                  <select
                    style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--ink)', fontSize: 12 }}
                    value={selSido?.osm_id || ''}
                    onChange={e => {
                      const r = sidoList.find(x => x.osm_id === Number(e.target.value));
                      setSelSido(r || null);
                    }}
                  >
                    <option value="">— 선택 —</option>
                    {sidoList.map(r => <option key={r.osm_id} value={r.osm_id}>{r.name_ko}</option>)}
                  </select>
                </div>

                {/* 시/군/구 선택 */}
                {selSido && (
                  <div className="col gap4">
                    <div style={{ fontSize: 10.5, color: 'var(--ink-3)', fontWeight: 600 }}>시 / 군 / 구</div>
                    <select
                      style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--ink)', fontSize: 12 }}
                      value={selSigungu?.osm_id || ''}
                      onChange={e => {
                        const r = sigunguList.find(x => x.osm_id === Number(e.target.value));
                        setSelSigungu(r || null);
                      }}
                    >
                      <option value="">— 선택 (선택 시 해당 시/군/구 전체) —</option>
                      {sigunguList.map(r => <option key={r.osm_id} value={r.osm_id}>{r.name_ko}</option>)}
                    </select>
                  </div>
                )}

                {/* 읍/면/동 선택 */}
                {selSigungu && dongList.length > 0 && (
                  <div className="col gap4">
                    <div style={{ fontSize: 10.5, color: 'var(--ink-3)', fontWeight: 600 }}>읍 / 면 / 동</div>
                    <select
                      style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--ink)', fontSize: 12 }}
                      value={selDong?.osm_id || ''}
                      onChange={e => {
                        const r = dongList.find(x => x.osm_id === Number(e.target.value));
                        setSelDong(r || null);
                      }}
                    >
                      <option value="">— 선택 (선택 시 해당 동 전체) —</option>
                      {dongList.map(r => <option key={r.osm_id} value={r.osm_id}>{r.name_ko}</option>)}
                    </select>
                  </div>
                )}

                {/* 선택된 구역 미리보기 + 확정 버튼 */}
                {(selDong || selSigungu || selSido) && (() => {
                  const reg = selDong || selSigungu || selSido;
                  const areaSqKm = ((reg.max_lat - reg.min_lat) * 111) * ((reg.max_lon - reg.min_lon) * 111 * Math.cos(((reg.min_lat + reg.max_lat) / 2) * Math.PI / 180));
                  const tooLarge = areaSqKm > MAX_SETUP_AREA_KM2;
                  return (
                    <div className="col gap8">
                      <div className="row between" style={{ padding: '9px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                        <span style={{ fontSize: 12, fontWeight: 600 }}>{reg.name_ko}</span>
                        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{areaSqKm.toFixed(1)} km²</span>
                      </div>
                      {tooLarge && (
                        <div className="row gap8" style={{ padding: '7px 10px', background: 'var(--bad-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--bad)' }}>
                          <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
                          {`구역이 너무 큽니다 (${areaSqKm.toFixed(0)}km²). 시/군/구 이하 단위를 선택해주세요 (상한 ${MAX_SETUP_AREA_KM2}km²).`}
                        </div>
                      )}
                      {!tooLarge && areaSqKm > 100 && (
                        <div className="row gap8" style={{ padding: '7px 10px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 10.5, color: 'var(--warn)' }}>
                          <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
                          {`넓은 구역 (${areaSqKm.toFixed(0)}km²) — netconvert 변환에 1분 이상 소요될 수 있습니다.`}
                        </div>
                      )}
                      {regionError && (
                        <div style={{ fontSize: 10.5, padding: '7px 10px', background: 'var(--bad-tint)', borderRadius: 8, color: 'var(--bad)' }}>
                          {regionError}
                        </div>
                      )}
                      <button
                        className="btn sm primary"
                        disabled={tooLarge || regionLoading || osmStage > 0}
                        onClick={finalizeAreaFromRegion}
                      >
                        <Icon.layers size={13} />
                        {osmStage > 0 ? '네트워크 준비 중…' : `"${reg.name_ko}" 구역으로 시뮬레이션 준비`}
                      </button>
                      {area && selectedRegion?.osm_id === reg.osm_id && (
                        <Chip tone="good" dot>준비 완료</Chip>
                      )}
                    </div>
                  );
                })()}

                {!selSido && (
                  <div className="muted" style={{ fontSize: 10.5 }}>도/광역시를 먼저 선택하세요</div>
                )}
              </div>
            )}
          </div>

          {/* ITS 동기화 + 첨두/비첨두 셀렉터는 제거됨 (2026-07-27).
              교통은 이제 건물 질량 → radiation OD → SUMO로 생성하며, 시간대별 분포는
              24시간 곡선이 담당한다. ITS 속도 덮어쓰기는 생성 교통과 같은 혼잡을 두 번
              세는 것이라 백엔드에서도 건너뛴다. 대신 아래 "교통량" 배율 노브가 대체한다. */}

          {/* waypoints */}
          <div className="field">
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              경로 지점 <span className="en">WAYPOINTS</span>
              <span style={{ fontSize: 9.5, padding: '1px 6px', borderRadius: 4, background: 'var(--brand-tint)', color: 'var(--brand-2)', fontWeight: 500, marginLeft: 2 }}>모든 시트 공유</span>
            </label>
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

          {/* 교통량 — 기준 교통량(N*) 대비 배율. 예전 "다중 차량 대수"를 대체한다.
              대수는 이제 입력이 아니라 결과다(N* × 배율 × 24h 곡선 → Little's Law). */}
          <div className="field">
            <label>교통량 <span className="en">DEMAND SCALE</span></label>
            <div className="row" style={{ alignItems: 'center', gap: 10 }}>
              <input
                type="range" min="10" max="300" step="5"
                value={demandScalePct}
                disabled={!area}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  setDemandScalePct(v);
                  setSimConfig(cfg => ({ ...(cfg || {}), policy_options: {
                    ...(cfg?.policy_options || {}), demand_scale_pct: v } }));
                }}
                style={{ flex: 1 }}
              />
              <span className="num" style={{ minWidth: 52, textAlign: 'right', fontWeight: 600 }}>
                {demandScalePct}%
              </span>
            </div>

            {/* 총 차량 수 — N*가 준비돼야 계산할 수 있다 */}
            {!area ? (
              <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>먼저 구역을 설정하세요</div>
            ) : !demandStatus?.ready ? (
              <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>
                {demandStatus?.preparing
                  ? '기준 교통량(N*)을 산정하는 중입니다… 처음 한 번은 몇 분 걸립니다.'
                  : '교통 준비 대기 중…'}
              </div>
            ) : (
              <div style={{ fontSize: 10.5, marginTop: 6 }}>
                <div>
                  기준 교통량 <span className="num">{demandStatus.n_star?.toLocaleString()}</span>
                  {' × '}{demandScalePct}% ={' '}
                  <span className="num" style={{ fontWeight: 600 }}>
                    약 {Math.round((demandStatus.n_star || 0) * demandScalePct / 100).toLocaleString()}대
                  </span>
                  <span className="muted"> / 창(07:00~09:00)</span>
                </div>
                {!demandStatus.calibrated && (
                  <div className="muted" style={{ marginTop: 2 }}>
                    기준값은 아직 <b>추정치</b>입니다 (오차 ±20%). 시뮬레이션을 처음 시작할 때 보정됩니다.
                  </div>
                )}
                {demandStatus.calibrated && demandScalePct === demandStatus.demand_scale_pct && (
                  <div className="muted" style={{ marginTop: 2 }}>
                    실제 생성 {demandStatus.n_vehicles?.toLocaleString()}대
                    {demandStatus.vehicle_count_error_pct != null &&
                      Math.abs(demandStatus.vehicle_count_error_pct) >= 10 && (
                      <span style={{ color: 'var(--warn)' }}>
                        {' '}({demandStatus.vehicle_count_error_pct > 0 ? '+' : ''}
                        {demandStatus.vehicle_count_error_pct}% — 배율이 낮으면 오차가 커집니다)
                      </span>
                    )}
                    {' · 동시 주행 피크 '}
                    <span className="num">{demandStatus.peak_running?.toLocaleString()}</span>대
                  </div>
                )}
                {demandStatus.calibrated && demandScalePct !== demandStatus.demand_scale_pct && (
                  <div className="muted" style={{ marginTop: 2 }}>
                    시뮬레이션을 시작하면 이 배율로 교통을 다시 만듭니다.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* network generation — policy_options.network_mode로 백엔드에 전달됨 */}
          <div className="field">
            <label>
              네트워크 세대 <span className="en">NETWORK GEN</span>
              {isConfigLocked && <span className="cfg-lock-note" style={{ marginLeft: 6 }}>실행 중 잠김</span>}
            </label>
            <div className="seg" style={{ display: 'flex', width: '100%' }}>
              {[['4g', '4G'], ['5g', '5G'], ['6g', '6G-like']].map(([v, lbl]) => (
                <button key={v} className={networkGen === v ? 'active' : ''} style={{ flex: 1 }} onClick={() => setNetworkGen(v)}>
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {/* base stations + RSU — Pro 전용 (Lite는 자동 배치된 기지국을 그대로 사용) */}
          {appMode === 'pro' && (
          <div className="field">
            <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              기지국 / RSU <span className="en">BS &amp; RSU</span>
              <span style={{ fontSize: 9.5, padding: '1px 6px', borderRadius: 4, background: 'var(--brand-tint)', color: 'var(--brand-2)', fontWeight: 500, marginLeft: 2 }}>모든 시트 공유</span>
            </label>
            <div className="col gap8" style={{ opacity: area ? 1 : 0.5 }}>
              <div className="row between" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                <span className="row gap8" style={{ minWidth: 0 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#1E88E5', flex: '0 0 auto' }} />
                  <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
                    BS {stations.filter(s => s.node_type !== 'rsu').length}개
                  </span>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#FF8F00', flex: '0 0 auto', marginLeft: 4 }} />
                  <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
                    RSU {stations.filter(s => s.node_type === 'rsu').length}개
                  </span>
                </span>
              </div>
              {/* 배치 방식 — 수동 / 랜덤 / 최적화 세 가지를 같은 위상으로 (2026-07-27 통일).
                  예전엔 [수동|자동] 셀렉터 아래에 최적화 배치가 따로 떠 있어 위상이 어긋났다. */}
              <Seg value={placeMode} onChange={setPlaceMode}
                options={[{ v: 'manual', label: '수동' }, { v: 'random', label: '랜덤' }, { v: 'sa', label: '최적화' }]} />

              {/* 수동 배치 — 기존 방식(BS/RSU 선택 후 지도 클릭) */}
              {placeMode === 'manual' && (
                <div className="col gap8">
                  <Seg value={stationType} onChange={setStationType}
                    options={[{ v: 'bs', label: 'BS (기지국)' }, { v: 'rsu', label: 'RSU' }]} />
                  {stationType === 'rsu' && (
                    <div className="muted" style={{ fontSize: 10.5 }}>
                      RSU는 PC5 사이드링크(~1–3ms, 범위 150m) — 교차로에 자동 스냅됩니다
                    </div>
                  )}
                  <div className="row gap8">
                    <button className={'btn sm ' + (mode === 'bs_create' ? 'accent' : '')} style={{ flex: 1 }} disabled={!area} onClick={tryBsCreate}>
                      <Icon.antenna size={13} /> {mode === 'bs_create' ? '지도 클릭…' : (stationType === 'rsu' ? 'RSU 배치' : 'BS 배치')}
                    </button>
                    <button className={'btn sm ' + (mode === 'bs_delete' ? 'accent' : '')} style={{ flex: 1 }} disabled={!area || stations.length === 0} onClick={tryBsDelete}>
                      <Icon.antenna size={13} /> {mode === 'bs_delete' ? '제거할 곳 클릭…' : '제거'}
                    </button>
                  </div>
                </div>
              )}

              {/* 랜덤 배치 — 면적 기반 권장 개수로 도로망에 고르게(블루노이즈) 뿌린다 */}
              {placeMode === 'random' && (
                <div style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                  <div className="muted" style={{ fontSize: 10, marginBottom: 8, lineHeight: 1.45 }}>
                    {area
                      ? `면적 ${areaKm2(area).toFixed(1)}km² · ${networkGen.toUpperCase()} 기준 권장값 (수정 가능) — 도로망에 고르게(랜덤) 배치`
                      : '구역을 먼저 설정하세요'}
                  </div>
                  <div className="row gap8" style={{ marginBottom: 8 }}>
                    <label style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)' }}>
                      BS
                      <input type="number" min="0" value={autoN.bs} disabled={!area}
                        onChange={e => setAutoN(v => ({ ...v, bs: Math.max(0, parseInt(e.target.value) || 0) }))}
                        style={{ width: '100%', marginTop: 3 }} />
                    </label>
                    <label style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)' }}>
                      RSU
                      <input type="number" min="0" value={autoN.rsu} disabled={!area}
                        onChange={e => setAutoN(v => ({ ...v, rsu: Math.max(0, parseInt(e.target.value) || 0) }))}
                        style={{ width: '100%', marginTop: 3 }} />
                    </label>
                  </div>
                  <div className="muted" style={{ fontSize: 10, marginBottom: 8 }}>
                    누르면 기존 배치를 지우고 새로 배치합니다 (번호 1부터).
                  </div>
                  <button className="btn sm block accent" disabled={!area || autoPlacing || (autoN.bs + autoN.rsu) === 0}
                    onClick={() => placeNodes('random', autoN, true, setAutoPlacing)}>
                    <Icon.antenna size={13} /> {autoPlacing ? '배치 중…' : `배치 (BS ${autoN.bs} · RSU ${autoN.rsu})`}
                  </button>
                </div>
              )}

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

              {/* 최적화 배치(SA) — 생성 교통의 피크 스냅샷을 수요로 써서 지연 최소화 */}
              {placeMode === 'sa' && (
              <div style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)' }}>
                <div className="muted" style={{ fontSize: 10, marginBottom: 8, lineHeight: 1.45 }}>
                  생성 교통의 가장 붐비는 시점을 수요로 삼아, BS와 RSU를 <b>함께</b> 최적화합니다.
                  BS는 건물 옥상, RSU는 교차로가 후보입니다.
                </div>
                <div className="row gap8" style={{ marginBottom: 8 }}>
                  <label style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)' }}>
                    BS
                    <input type="number" min="0" value={saN.bs} disabled={!area}
                      onChange={e => setSaN(v => ({ ...v, bs: Math.max(0, parseInt(e.target.value) || 0) }))}
                      style={{ width: '100%', marginTop: 3 }} />
                  </label>
                  <label style={{ flex: 1, fontSize: 11, color: 'var(--ink-2)' }}>
                    RSU
                    <input type="number" min="0" value={saN.rsu} disabled={!area}
                      onChange={e => setSaN(v => ({ ...v, rsu: Math.max(0, parseInt(e.target.value) || 0) }))}
                      style={{ width: '100%', marginTop: 3 }} />
                  </label>
                </div>
                <div className="muted" style={{ fontSize: 10, marginBottom: 8 }}>
                  누르면 기존 배치를 지우고 새로 배치합니다 (번호 1부터).
                </div>
                {/* 교통이 만들어지기 전에는 누르지 못하게 막는다.
                    `calibrated`는 곧 "교통 시나리오가 실제로 존재한다"는 뜻이다
                    (백엔드 /api/demand/status — current_traffic_scenario(build=False)가 값을 냈을 때만 true).
                    최적화가 쓰는 optimize_placement_v2()도 같은 build=False로 교통을 집으므로,
                    이 값이 false인 동안 누르면 (a) 교통 생성이 끝날 때까지 요청이 수 분~수십 분
                    매달리거나 (b) 생성이 실패하면 실측 교통 대신 균일 수요로 폴백해
                    "골고루 뿌리기"에 가까운 배치가 나온다(v2 §8-1). 둘 다 사용자가 알기 어렵다. */}
                {!demandStatus?.calibrated && area && (
                  <div className="muted" style={{ fontSize: 10, marginBottom: 8, color: 'var(--warn)' }}>
                    {demandStatus?.preparing
                      ? `교통량 계산이 끝나야 누를 수 있습니다${demandStatus?.stage ? ` (${demandStatus.stage} 단계)` : ''} — 실측 교통을 수요로 써야 배치가 의미가 있습니다.`
                      : '교통이 아직 준비되지 않았습니다. 시뮬레이션을 한 번 시작하거나 교통 준비가 끝날 때까지 기다려 주세요.'}
                  </div>
                )}
                <button className="btn sm block accent"
                  disabled={!area || saPlacing || (saN.bs + saN.rsu) === 0 || !demandStatus?.calibrated}
                  onClick={() => placeNodes('sa', saN, true, setSaPlacing)}>
                  <Icon.antenna size={13} /> {saPlacing
                    ? (placementProgress ? `최적화 중… ${placementProgress.pct}%` : '최적화 중…')
                    : !demandStatus?.calibrated && area
                    ? '교통량 계산 대기 중…'
                    : `최적화 배치 (BS ${saN.bs} · RSU ${saN.rsu})`}
                </button>
                {/* 진행률 막대 — 예전엔 콘솔에만 찍혀서 사용자는 수 분 동안 멈춘 건지
                    도는 건지 알 수 없었다. 백엔드가 WS로 pct·phase를 보낸다. */}
                {saPlacing && placementProgress && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ height: 6, background: 'var(--surface-3)', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${placementProgress.pct}%`,
                        background: 'var(--brand-2)', transition: 'width .3s ease' }} />
                    </div>
                    <div className="muted" style={{ fontSize: 10, marginTop: 4, textAlign: 'center' }}>
                      {placementProgress.phase} — {placementProgress.pct}%
                    </div>
                  </div>
                )}
                {placeResult && (
                  <div style={{ fontSize: 10, marginTop: 8, lineHeight: 1.5 }}>
                    <div>무작위 배치 <span className="num">{placeResult.random_baseline_ms?.toFixed(1)}</span> ms
                      {' → 최적화 '}<span className="num" style={{ fontWeight: 600 }}>{placeResult.cost_final_ms?.toFixed(1)}</span> ms
                      {placeResult.gain_vs_random_pct != null && (
                        <span style={{ color: 'var(--good)', fontWeight: 600 }}> ({placeResult.gain_vs_random_pct.toFixed(1)}% 개선)</span>
                      )}
                    </div>
                    <div className="muted">
                      음영(outage) {placeResult.outage_pct?.toFixed(1)}% · 미커버 {placeResult.uncovered_pct?.toFixed(1)}%
                      {' · 후보 BS '}{placeResult.n_candidates_bs}{' / RSU '}{placeResult.n_candidates_rsu}
                    </div>
                  </div>
                )}
              </div>
              )}
            </div>
            {!area && <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>먼저 구역을 설정하세요</div>}
          </div>
          )}

          {/* algorithms — Pro 전용 (Lite는 simConfig의 기본 알고리즘을 그대로 사용) */}
          {appMode === 'pro' && (
          <div className="field">
            <label>
              알고리즘 <span className="en">ALGORITHMS</span>
              {isConfigLocked && <span className="cfg-lock-note" style={{ marginLeft: 6 }}>실행 중 잠김</span>}
            </label>
            <div className="col gap8">
              <AlgorithmGroup groupKey="route" label="경로 알고리즘" options={ROUTE_ALGORITHMS} />
              <AlgorithmGroup groupKey="latency" label="지연시간 알고리즘" options={LATENCY_ALGORITHMS} />
              <AlgorithmGroup groupKey="base_station_selection" label="기지국 선택 알고리즘" options={BS_SELECTION_ALGORITHMS} />
              <AlgorithmGroup groupKey="resource_allocation" label="자원할당 알고리즘" options={RESOURCE_ALLOCATION_ALGORITHMS} />
            </div>
          </div>
          )}

          {/* GNN-MAML vs 기준선 비교 — Pro 전용. 시트 3개 자동 생성 후 배치 실행 */}
          {appMode === 'pro' && (
          <div className="field">
            <label>
              GNN-MAML 비교 <span className="en">BS SELECTION COMPARE</span>
              {' '}
              <span className={'chip sm ' + (gnnReady ? 'good' : 'warn')} style={{ marginLeft: 6 }}>
                {gnnReady ? 'GNN-MAML 로드됨' : 'GNN 미로드'}
              </span>
            </label>
            <div className="col gap8" style={{ opacity: ready ? 1 : 0.5 }}>
              <div className="muted" style={{ fontSize: 10.5 }}>
                GNN-MAML / Nearest BS / RSRP Max 시트 3개를 자동 생성하고 같은 경로로 비교합니다. 결과는 각 시트 지도 + 분석보고서에서 확인하세요.
                {!gnnReady && ' (GNN 미로드 시 Lowest Latency로 대체)'}
              </div>
              <button className={'btn sm ' + (rlDone ? 'good' : '')} disabled={!ready || rlRunning || batchRunning} onClick={runRLComparison}>
                {rlRunning ? <><Icon.reset size={13} className="spin" /> 비교 실행 중…</> : rlDone ? <><Icon.check size={13} /> 완료 — 시트·보고서에서 확인</> : <><Icon.spark size={13} /> GNN-MAML 비교 실행</>}
              </button>
              {!ready && <div className="muted" style={{ fontSize: 10.5 }}>구역·출발지·도착지를 먼저 설정하세요</div>}
              {rlError && <div style={{ fontSize: 10.5, color: 'var(--bad)' }}>{rlError}</div>}
            </div>
          </div>
          )}

          </fieldset>
          {/* ── 잠금 영역 끝 — 아래는 읽기 전용 표시 ───────────────────────── */}

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
                ? <button className="btn good block" disabled={!ready || osmStage > 0 || !!trafficPrep} onClick={handleStart}>
                    <Icon.play size={15} /> {trafficPrep ? '준비 중…'
                      : sim.finished ? '다시 시작'
                      : sim.elapsed > 0 ? '재개' : '시작'}
                  </button>
                : <button className="btn block" style={{ borderColor: 'var(--warn-line)', color: 'var(--warn)' }} onClick={handleStop}>
                    <Icon.pause size={15} /> 정지
                  </button>}
              <button className="btn icon" onClick={clearRun} title="시뮬레이션 초기화 — 실행 결과만 지웁니다 (구역·출발지·도착지는 유지)"><Icon.reset size={15} /></button>
            </div>
            {/* 교통 준비 안내 — 새 구역은 N* 보정에 수 분이 걸린다. 예전엔 시작을 눌러도
                아무 표시 없이 응답이 멈춰 있어서, 사용자가 멈춘 줄 알고 시작을 여러 번
                눌렀다(그러면 서로의 TraCI 연결을 끊어 런이 전멸했다). 이제 진행 상황을
                보여주고 버튼을 잠그며, 준비가 끝나면 자동으로 시작된다. */}
            {trafficPrep && (
              <div style={{ padding: '9px 12px', background: 'var(--surface-2)', border: '1px solid var(--border)',
                borderRadius: 9, fontSize: 10.5, lineHeight: 1.5 }}>
                <div style={{ fontWeight: 600, marginBottom: 3 }}>교통량 계산 중…</div>
                <div className="muted">{trafficPrep.message || '새 구역의 기준 교통량(N*)을 구하는 중입니다. 몇 분 걸릴 수 있고, 끝나면 시뮬레이션이 자동으로 시작됩니다.'}</div>
              </div>
            )}
            {/* 재생 배속 — 실행 중에도 바꿀 수 있어야 하므로 설정 잠금(isConfigLocked) 밖에 둔다 */}
            <div className="row between" style={{ alignItems: 'center' }}>
              <span className="muted" style={{ fontSize: 10.5 }}>배속</span>
              <Seg value={simSpeed} onChange={changeSpeed}
                options={[{ v: 1, label: '1×' }, { v: 2, label: '2×' },
                          { v: 4, label: '4×' }, { v: 8, label: '8×' }]} />
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
                {osmWarning || simNotice}
              </div>
            )}

            {/* 전체 초기화 — 구역까지 비우는 유일한 경로. 재생 옆 초기화(clearRun)와 달리
                도로망·생성 교통이 사라져서 다음 실행 때 N* 보정을 처음부터 다시 해야 하므로,
                실수로 누르지 않도록 맨 아래에 따로 둔다. */}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 4 }}>
              {/* 준비 중에도 누를 수 있어야 한다 — 비활성으로 두면 눌러도 아무 반응이 없어
                  "초기화가 안 먹는다"로 보인다. 백엔드의 /api/simulation/reset이 대기 중인
                  시작(pending_start)을 먼저 취소하므로, 준비가 끝나도 시뮬이 혼자 시작되지 않는다. */}
              <button className="btn sm block" onClick={clearAll}
                title="구역·출발지·도착지까지 모두 비웁니다. 교통량 계산 중이면 그 대기도 취소합니다."
                style={{ borderColor: 'var(--warn-line)', color: 'var(--warn)' }}>
                <Icon.reset size={13} /> 전체 초기화
              </button>
              <div className="muted" style={{ fontSize: 10, lineHeight: 1.5, marginTop: 6, textAlign: 'center' }}>
                구역·출발지·도착지까지 모두 지웁니다. 교통량을 다시 계산해야 합니다.
              </div>
            </div>
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

      {/* ── CCTV 그리드 뷰 — 모든 시트를 나란히 ─────────────────────── */}
      {gridView && (
        <div style={{ position: 'absolute', inset: 0, zIndex: 900 }}>
          <SheetGridView
            sheets={sheets}
            activeSheetIdx={activeSheetIdx}
            onSelectSheet={idx => { switchToSheet(idx); setGridView(false); }}
            liveVehiclePos={vehiclePos}
            liveRouteCoords={routeCoords}
            liveNetworkTelemetry={networkTelemetry}
            liveBackgroundVehicles={backgroundVehicles}
            stations={stations}
            sim={sim}
          />
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
      onToggleGrid={() => setGridView(v => !v)}
      gridView={gridView}
      hasEnv={!!(originDone && destDone)}
    />
    </div>
  );
}
window.SimulationTab = SimulationTab;
