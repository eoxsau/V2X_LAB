/* ============================================================ Dashboard tab */

const ALGO_LABELS = {
  dijkstra:                  'Dijkstra',
  astar:                     'A*',
  baseline_dijkstra:         '기본 Dijkstra',
  network_aware:             '네트워크 가중치',
  network_weighted:          '네트워크 가중치',
  lowest_latency_bs:         'Lowest Latency',
  highest_confidence_bs:     'Highest Confidence',
  load_balanced_bs:          'Load Balanced',
  nearest_bs:                'Nearest BS',
  full_composite_latency:    'Full Composite',
  simple_distance_latency:   'Simple Distance',
  distance_based_latency:    'Distance Based',
  load_aware_latency:        'Load Aware',
  blockage_aware_latency:    'Blockage Aware',
  mec_aware_latency:         'MEC Aware',
  traffic_aware_allocation:  'Traffic Aware',
  equal_allocation:          'Equal Alloc',
  proportional_allocation:   'Proportional',
  proportional_demand_allocation: 'Proportional Demand',
  load_balancing_allocation: 'Load Balancing',
  latency_minimizing_allocation: 'Latency Minimizing',
  priority_based_allocation: 'Priority Based',
  lookahead_resource_allocation: 'Look-ahead',
};
function algoLabel(key) {
  if (!key) return null;
  return ALGO_LABELS[key] ?? key;
}

const LOG_TONE_DASH = {
  info: '', handover: 'brand', warn: 'warn', risk: 'bad',
  done: 'good', reroute: 'brand', sys: '', disconnect: 'bad',
};
const LOG_KO_DASH = {
  info: '정보', handover: '핸드오버', warn: '경고', risk: '위험',
  done: '완료', reroute: '재경로', sys: '시스템', disconnect: '단절',
};

/* ---- ported from tab-network.jsx (merged into Dashboard) --- */
function edgeCongTone(loadRatio) {
  if (loadRatio >= 0.65) return 'bad';
  if (loadRatio >= 0.35) return 'warn';
  return 'good';
}
function edgeCongLabel(loadRatio) {
  if (loadRatio >= 0.65) return '혼잡';
  if (loadRatio >= 0.35) return '보통';
  return '원활';
}
function formatSignedDelta(value, unit) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}${unit || ''}`;
}

/* ---- ported from tab-forecast.jsx (merged into Dashboard) -- */
const RISK_TONE = { low: 'good', medium: 'warn', high: 'bad' };
const RISK_KO = { low: '낮음', medium: '보통', high: '높음' };
function buildFallbackLookahead(routeEdges, vehiclePos, hops) {
  const perEdge = routeEdges?.per_edge || [];
  if (!perEdge.length) return null;
  const progress = vehiclePos?.progress ?? 0;
  const curIdx = Math.min(Math.max(Math.floor(progress * perEdge.length), 0), perEdge.length - 1);
  const futureEdges = perEdge.slice(curIdx, curIdx + Math.max(hops, 1));
  if (!futureEdges.length) return null;

  const perHop = futureEdges.map((edge, idx) => {
    const hasCoverage = edge.within_coverage !== false;
    const latency = edge.latency_ms ?? 0;
    const load = edge.load_ratio ?? 0;
    let coverageFraction = hasCoverage ? 1 : 0;
    if (hasCoverage && latency > 20) coverageFraction = 0.55;
    else if (hasCoverage && load > 0.7) coverageFraction = 0.72;
    else if (hasCoverage && load > 0.45) coverageFraction = 0.86;
    return {
      hop: idx + 1,
      edge_count: 1,
      covered_count: coverageFraction >= 0.8 ? 1 : 0,
      coverage_fraction: coverageFraction,
      uncovered_edge_ids: coverageFraction >= 0.8 ? [] : [edge.edge_id],
      edge_id: edge.edge_id,
      latency_ms: latency,
      load_ratio: load,
    };
  });

  const weighted = perHop.reduce((sum, hop) => sum + hop.coverage_fraction * (1 / hop.hop), 0);
  const weightTotal = perHop.reduce((sum, hop) => sum + (1 / hop.hop), 0) || 1;
  const score = weighted / weightTotal;
  const totalUncovered = perHop.reduce((sum, hop) => sum + (hop.coverage_fraction >= 0.8 ? 0 : 1), 0);
  const riskLevel = score < 0.45 ? 'high' : score < 0.75 ? 'medium' : 'low';

  return {
    available: true,
    source: 'route_fallback',
    current_node_id: futureEdges[0]?.edge_id || null,
    lookahead_hops: perHop.length,
    future_connectivity_score: Number(score.toFixed(4)),
    risk_level: riskLevel,
    total_edges_scanned: perHop.length,
    total_uncovered: totalUncovered,
    per_hop: perHop,
    scanned_at: new Date().toISOString(),
  };
}

/* ---- Latency breakdown bar --------------------------------- */
function LatencyBreakdown({ total, lBase, lSignal, lQueue }) {
  if (total === null || lBase === null || lSignal === null || lQueue === null) return null;
  const items = [
    { label: '기본 지연(L_base)',     value: lBase,   color: 'var(--brand-2)' },
    { label: '신호 지연(HARQ)',       value: lSignal, color: 'var(--warn)'    },
    { label: '혼잡 대기(큐잉)',       value: lQueue,  color: 'var(--bad)'     },
  ];
  return (
    <Card
      title="Latency 분해"
      en="Breakdown · ms"
      right={<Chip tone={total >= 20 ? 'bad' : total >= 12 ? 'warn' : 'good'}>{total.toFixed(1)}ms 합계</Chip>}
      style={{ marginBottom: 14 }}
    >
      <div className="row gap16" style={{ flexWrap: 'wrap' }}>
        {items.map((item, i) => (
          <div key={i} style={{ flex: 1, minWidth: 110 }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 5 }}>{item.label}</div>
            <div className="row gap8" style={{ alignItems: 'center' }}>
              <div style={{ flex: 1, height: 10, background: 'var(--surface-3)', borderRadius: 5, overflow: 'hidden' }}>
                <div style={{
                  width: total > 0 ? `${Math.min(100, (item.value / total) * 100)}%` : '0%',
                  height: '100%', background: item.color, borderRadius: 5, transition: 'width .5s',
                }} />
              </div>
              <span className="num" style={{ fontSize: 12, fontWeight: 600, width: 50, textAlign: 'right' }}>
                {item.value.toFixed(1)}ms
              </span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ---- Info card row (resource / route quality / signal env) - */
function InfoRow({ networkTelemetry, routeEdges }) {
  const allocRb     = networkTelemetry?.ego_allocated_rb ?? null;
  const congestion  = networkTelemetry?.connected_node?.congestion_score ?? null;
  const coverageRisk   = routeEdges?.coverage_risk   ?? null;
  const expectedHo     = routeEdges?.handover_count  ?? null;
  const totalDistM     = routeEdges?.total_distance_m > 0 ? routeEdges.total_distance_m : null;
  const routeAvgLat    = routeEdges?.avg_latency_ms  ?? null;
  const buildingCount  = networkTelemetry?.intersected_building_count ?? null;
  const buildingPenMs  = networkTelemetry?.latency_penalty_ms ?? null;
  const densityPenMs   = networkTelemetry?.vehicle_density_penalty_ms ?? null;
  const distM          = networkTelemetry?.distance_m ?? null;

  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: 14 }}>
      {/* Resource allocation */}
      <Card title="자원 할당" en="Resource alloc">
        <div className="row gap16" style={{ marginBottom: congestion !== null ? 10 : 0 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 4 }}>EGO 할당 RB</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              {allocRb !== null ? allocRb.toFixed(1) : '—'}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 4 }}>BS 혼잡도</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>
              {congestion !== null ? `${(congestion * 100).toFixed(0)}%` : '—'}
            </div>
          </div>
        </div>
        {congestion !== null && (
          <div className="pbar">
            <div style={{
              width: `${Math.min(100, congestion * 100)}%`, height: '100%', borderRadius: 6,
              background: congestion > 0.7 ? 'var(--bad)' : congestion > 0.4 ? 'var(--warn)' : 'var(--good)',
              transition: 'width .5s',
            }} />
          </div>
        )}
      </Card>

      {/* Route quality */}
      <Card title="경로 품질" en="Route quality">
        <div className="col" style={{ gap: 10 }}>
          {[
            { label: '커버리지 위험',
              node: coverageRisk !== null
                ? <Chip tone={coverageRisk > 0.15 ? 'bad' : coverageRisk > 0.07 ? 'warn' : 'good'}>
                    {(coverageRisk * 100).toFixed(1)}%
                  </Chip>
                : <span className="muted">—</span> },
            { label: '예상 핸드오버',
              node: expectedHo !== null
                ? <span className="num" style={{ fontWeight: 600 }}>{expectedHo}회</span>
                : <span className="muted">—</span> },
            { label: '총 경로 거리',
              node: totalDistM
                ? <span className="num" style={{ fontWeight: 600 }}>{(totalDistM / 1000).toFixed(2)}km</span>
                : <span className="muted">—</span> },
            { label: '경로 평균 Latency',
              node: routeAvgLat != null
                ? <span className="num" style={{ fontWeight: 600 }}>{routeAvgLat.toFixed(1)}ms</span>
                : <span className="muted">—</span> },
          ].map((row, i) => (
            <div key={i} className="row between" style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--ink-2)' }}>{row.label}</span>
              {row.node}
            </div>
          ))}
        </div>
      </Card>

      {/* Signal environment */}
      <Card title="신호 환경" en="Signal env">
        <div className="col" style={{ gap: 10 }}>
          {[
            { label: '간섭 건물 수',
              node: buildingCount !== null
                ? <span className="num" style={{ fontWeight: 600 }}>{buildingCount}개</span>
                : <span className="muted">—</span> },
            { label: '빌딩 패널티',
              node: buildingPenMs !== null
                ? <span className="num" style={{ fontWeight: 600 }}>{buildingPenMs.toFixed(1)}ms</span>
                : <span className="muted">—</span> },
            { label: '밀도 페널티',
              node: densityPenMs !== null
                ? <span className="num" style={{ fontWeight: 600 }}>{densityPenMs.toFixed(1)}ms</span>
                : <span className="muted">—</span> },
            { label: '연결 BS 거리',
              node: distM != null
                ? <span className="num" style={{ fontWeight: 600 }}>{distM.toFixed(0)}m</span>
                : <span className="muted">—</span> },
          ].map((row, i) => (
            <div key={i} className="row between" style={{ fontSize: 12 }}>
              <span style={{ color: 'var(--ink-2)' }}>{row.label}</span>
              {row.node}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ---- Main Dashboard ---------------------------------------- */
function Dashboard({ sim, go, vehiclePos: liveVehiclePos, networkTelemetry: liveNetworkTelemetry, simHistory: liveSimHistory, simLogs: liveSimLogs, simConfig: liveSimConfig, routeEdges: liveRouteEdges, sheets, activeSheetIdx }) {
  const [subTab, setSubTab] = useState('summary'); // 'summary' | 'network' | 'forecast' — 한 화면에 다 펼치지 않고 묶음별로 분리

  // 시트별 대시보드 분리 — 시뮬레이션 탭에서 "지금 실행 중인" 시트(activeSheetIdx)만 실시간
  // props를 쓰고, 다른 시트를 고르면 그 시트가 캡처해둔 result(읽기전용 스냅샷)를 보여준다.
  // 마운트 시점에는 항상 "현재 실행 시트"를 기본으로 보여준다(대시보드는 탭 전환마다 새로 마운트됨).
  const [viewSheetIdx, setViewSheetIdx] = useState(() => activeSheetIdx ?? 0);
  const sheetList = sheets || [];
  const isLiveSheet = viewSheetIdx === activeSheetIdx;
  const viewedSheet = sheetList[viewSheetIdx];
  const vehiclePos       = isLiveSheet ? liveVehiclePos       : (viewedSheet?.result?.vehiclePos || null);
  const networkTelemetry = isLiveSheet ? liveNetworkTelemetry : (viewedSheet?.result?.network_telemetry || null);
  const simHistory       = isLiveSheet ? liveSimHistory       : (viewedSheet?.result?.simHistory || []);
  const simLogs          = isLiveSheet ? liveSimLogs          : (viewedSheet?.result?.simLogs || []);
  const simConfig        = isLiveSheet ? liveSimConfig        : (viewedSheet?.config?.simConfig || liveSimConfig);
  const routeEdges       = isLiveSheet ? liveRouteEdges       : (viewedSheet?.result?.routeEdges || null);

  const hasLive  = !!vehiclePos;
  const arrived  = vehiclePos?.arrived ?? false;
  const progress = vehiclePos?.progress ?? 0;
  const speed    = vehiclePos?.speed ?? null;

  /* telemetry shortcuts */
  const connNode       = networkTelemetry?.ego_vehicle?.connected_network_node_name
                         ?? networkTelemetry?.connected_node?.name ?? null;
  const latency        = networkTelemetry?.ego_vehicle?.current_latency_ms
                         ?? networkTelemetry?.latency_ms ?? null;
  const lossDb         = networkTelemetry?.estimated_penetration_loss_db ?? null;
  const stability      = networkTelemetry?.stability_score ?? null;
  const lBase          = networkTelemetry?.l_base_ms ?? null;
  const lSignal        = networkTelemetry?.l_signal_ms ?? null;
  const lQueue         = networkTelemetry?.l_queue_ms ?? null;

  /* current road name — fall back to last known name on unmapped edges */
  const currentEdgeId  = vehiclePos?.current_edge_id ?? null;
  const edgeNames      = networkTelemetry?.route_edge_names ?? routeEdges?.edge_names ?? {};
  const currentRoad    = currentEdgeId ? (edgeNames[currentEdgeId] || null) : null;
  const lastRoadRef    = useRef(null);
  if (currentRoad) lastRoadRef.current = currentRoad;
  if (!hasLive) lastRoadRef.current = null;
  const displayRoad    = currentEdgeId ? (currentRoad || lastRoadRef.current) : null;

  /* algorithm settings */
  const routeAlgo    = simConfig?.algorithm_selection?.route_algorithm ?? null;
  const bsAlgo       = simConfig?.algorithm_selection?.base_station_selection_algorithm ?? null;
  const latencyAlgo  = simConfig?.algorithm_selection?.latency_algorithm ?? null;
  const resourceAlgo = simConfig?.algorithm_selection?.resource_allocation_algorithm ?? null;

  /* simLogs derived */
  const logs_safe      = simLogs ?? [];
  const handoverCount  = logs_safe.filter(l => l.kind === 'handover').length;
  const disconnCount   = logs_safe.filter(l => l.kind === 'disconnect').length;
  const recentLogs     = [...logs_safe].slice(-5).reverse();

  /* latency history & stats */
  const latencyHistory = simHistory.length >= 2
    ? simHistory.map(h => h.latency ?? 0)
    : [];
  const latencyLabels  = simHistory.length >= 2
    ? simHistory.map((h, i) =>
        i % Math.max(1, Math.floor(simHistory.length / 6)) === 0 ? fmtClock(h.t) : '')
    : [];
  const validLats  = simHistory.filter(h => h.latency !== null).map(h => h.latency);
  const avgLatency = validLats.length > 0
    ? validLats.reduce((s, v) => s + v, 0) / validLats.length
    : null;
  const maxLatency = validLats.length > 0 ? Math.max(...validLats) : null;

  // resource_deficit_by_bs / bs_load_after_allocation는 백엔드 자원할당 로직이 내부 식별자(bs_id,
  // 예: "user-bs-53c0bb7328")를 키로 쓴다 — network_telemetry.network_nodes에는 같은 노드의
  // 실제 이름이 있으니, 화면에 원본 id가 그대로 노출되지 않도록 항상 이 맵을 거쳐서 표시한다.
  const nodeNameById = useMemo(() => {
    const map = {};
    (networkTelemetry?.network_nodes || []).forEach(n => { map[n.id] = n.name || n.id; });
    return map;
  }, [networkTelemetry]);
  const bsLabel = (id) => nodeNameById[id] || id;

  /* BS candidates */
  const candidates = networkTelemetry?.candidate_nodes ?? [];
  const bsItems    = candidates.slice(0, 5).map(c => {
    const ms  = c.predicted_latency_ms ?? 0;
    const pct = Math.min(100, Math.round(ms / 0.3));
    return {
      label:   (c.name ?? c.id ?? '').replace(/기지국\s*/, 'BS-'),
      value:   pct,
      display: ms.toFixed(1) + 'ms',
      color:   pct > 50 ? 'var(--bad)' : pct > 25 ? 'var(--warn)' : 'var(--good)',
    };
  });

  /* latency chip */
  const latTone = latency !== null ? latencyTone(latency) : null;
  const latTxt  = latency !== null ? latency.toFixed(1) : '—';

  /* route distance for progress sub */
  const totalDistM = routeEdges?.total_distance_m > 0 ? routeEdges.total_distance_m : null;

  /* ====== ported from tab-network.jsx — 네트워크 상세 ====== */
  const hasLiveNet = !!networkTelemetry;
  const connNodeObj = networkTelemetry?.connected_node ?? null;

  const [alloc, setAlloc] = useState(null);
  useEffect(() => {
    // 백엔드는 시트별로 자원배분 결과를 따로 보관하지 않는다 — "현재 실행 시트"가 아닐 때
    // 폴링하면 다른(엄밀히는 지금 살아있는) 시트의 데이터가 잘못 표시될 수 있으므로 멈춘다.
    if (!isLiveSheet) { setAlloc(null); return; }
    let dead = false;
    function poll() {
      fetch('http://127.0.0.1:8001/api/resources/allocation-result')
        .then(r => r.json())
        .then(data => { if (!dead) setAlloc(data); })
        .catch(() => {});
    }
    poll();
    const t = setInterval(poll, 3000);
    return () => { dead = true; clearInterval(t); };
  }, [isLiveSheet]);

  const edgeStatsNet = networkTelemetry?.edge_stats || [];
  const perEdgeNet   = routeEdges?.per_edge || [];
  const perEdgeMapNet = {};
  perEdgeNet.forEach(e => { perEdgeMapNet[e.edge_id] = e; });
  const hasLiveEdges = edgeStatsNet.length > 0;

  const _allEdgesRaw = hasLiveEdges
    ? edgeStatsNet.map(e => {
        const meta = perEdgeMapNet[e.edge_id] || {};
        return {
          edge_id:         e.edge_id,
          street_name:     edgeNames[e.edge_id] || '',
          speed_kmh:       e.speed_kmh,
          occupancy:       e.occupancy,
          vehicle_count:   e.vehicle_count,
          load_ratio:      e.occupancy ?? meta.load_ratio ?? 0,
          distance_m:      meta.distance_m  ?? null,
          latency_ms:      meta.latency_ms  ?? null,
          within_coverage: meta.within_coverage ?? true,
        };
      })
    : perEdgeNet.map(e => ({
        edge_id:         e.edge_id,
        street_name:     edgeNames[e.edge_id] || '',
        speed_kmh:       null,
        occupancy:       e.load_ratio,
        vehicle_count:   null,
        load_ratio:      e.load_ratio,
        distance_m:      e.distance_m  ?? null,
        latency_ms:      e.latency_ms  ?? null,
        within_coverage: e.within_coverage ?? true,
      }));
  // 도로명이 없는 엣지에 인접 엣지 이름 전파 (앞→뒤, 뒤→앞)
  const allEdges = (() => {
    const a = _allEdgesRaw.map(e => ({ ...e }));
    for (let i = 1; i < a.length; i++)
      if (!a[i].street_name && a[i - 1].street_name) a[i].street_name = a[i - 1].street_name;
    for (let i = a.length - 2; i >= 0; i--)
      if (!a[i].street_name && a[i + 1].street_name) a[i].street_name = a[i + 1].street_name;
    return a;
  })();

  const edgeHistory   = networkTelemetry?.edge_history   || [];
  const edgeAvgSpeeds = networkTelemetry?.edge_avg_speeds || {};
  const nEdges = allEdges.length;
  const curEdgeIdx = nEdges > 0 ? Math.min(Math.floor(progress * nEdges), nEdges - 1) : 0;
  const currentEdgeNet = nEdges > 0 ? { ...allEdges[curEdgeIdx], isCurrent: true } : null;

  const allEdgeMap = {};
  allEdges.forEach(e => { allEdgeMap[e.edge_id] = e; });
  const completedEdges = edgeHistory.slice(-6).reverse().map((eid, i, arr) => {
    const base = allEdgeMap[eid] || { edge_id: eid, street_name: edgeNames[eid] || '', load_ratio: 0, occupancy: 0, vehicle_count: null, distance_m: null, latency_ms: null, within_coverage: true };
    const name = base.street_name
      || (allEdgeMap[arr[i - 1]]?.street_name)
      || (allEdgeMap[arr[i + 1]]?.street_name)
      || '';
    return { ...base, street_name: name, isCurrent: false, speed_kmh: edgeAvgSpeeds[eid] ?? base.speed_kmh ?? null };
  });

  const mergedEdges = [
    ...(currentEdgeNet ? [currentEdgeNet] : []),
    ...completedEdges,
  ];

  const distanceM = networkTelemetry?.distance_m ?? null;
  const buildings = networkTelemetry?.intersected_building_count ?? null;
  const congestion = connNodeObj?.congestion_score ?? null;
  const currentNodeType = connNodeObj?.type ?? candidates[0]?.type ?? '—';
  const currentEdgeName = currentEdgeNet?.street_name || currentEdgeNet?.edge_id || '—';
  const egoAllocatedRb = networkTelemetry?.ego_allocated_rb ?? null;
  const routingMode = networkTelemetry?.routing_mode || routeEdges?.routing_mode || simConfig?.algorithm_selection?.route_algorithm || '—';
  const activeSelectionAlgo = simConfig?.algorithm_selection?.base_station_selection_algorithm || '—';
  const activeLatencyAlgoNet = simConfig?.algorithm_selection?.latency_algorithm || '—';
  const activeAllocAlgo = alloc?.algorithm_id || simConfig?.algorithm_selection?.resource_allocation_algorithm || '—';

  const latencyValues = candidates.map(c => c.predicted_latency_ms).filter(v => typeof v === 'number');
  const distanceValues = candidates.map(c => c.distance_m).filter(v => typeof v === 'number');
  const loadValues = candidates.map(c => c.congestion_score).filter(v => typeof v === 'number');
  const minLatency = latencyValues.length ? Math.min(...latencyValues) : null;
  const minDistance = distanceValues.length ? Math.min(...distanceValues) : null;
  const minCongestion = loadValues.length ? Math.min(...loadValues) : null;
  const bestAlt = candidates.find(c => (c.name ?? c.id) !== connNode) || null;
  const bestAltDelta = bestAlt && latency !== null ? (bestAlt.predicted_latency_ms ?? latency) - latency : null;

  const primaryReason = (() => {
    if (!hasLiveNet) return '라이브 텔레메트리가 연결되면 현재 노드 선택 이유를 표시합니다.';
    if (latency !== null && minLatency !== null && Math.abs(latency - minLatency) < 0.25) return '후보 중 예측 지연시간이 가장 낮아 즉시 응답성이 가장 좋습니다.';
    if (congestion !== null && minCongestion !== null && Math.abs(congestion - minCongestion) < 0.03) return '현재 혼잡도가 가장 낮은 축에 있어 큐 지연 위험이 작습니다.';
    if (distanceM !== null && minDistance !== null && Math.abs(distanceM - minDistance) < 10) return '차량과 가장 가까운 후보라 전파 거리 페널티가 낮습니다.';
    if (lossDb !== null && lossDb <= 8) return '현재 차폐 손실이 낮아 연결 품질이 안정적입니다.';
    return '현재 종합 노드 점수가 가장 낮아 운영상 가장 안정적인 선택입니다.';
  })();

  const secondaryReason = (() => {
    if (!hasLiveNet) return '현재 텔레메트리 대기 중';
    if (stability !== null && stability >= 0.85) return '안정성 점수가 높아 단기 품질 흔들림 가능성이 작습니다.';
    if (lossDb !== null && lossDb <= 12) return '건물 차폐가 제한적이라 신호 손실이 관리 가능한 수준입니다.';
    return '현재 경로 구간과 후보 노드의 균형이 가장 우수한 조합입니다.';
  })();

  const currentNodeName = connNodeObj?.name ?? connNode ?? '없음';
  const currentDeficit = currentNodeName !== '없음'
    ? (alloc?.resource_deficit_by_bs?.[currentNodeName] ?? alloc?.resource_deficit_by_bs?.[connNodeObj?.id] ?? 0)
    : 0;
  const allocationImpacts = alloc?.expected_latency_impact || {};
  const currentLatencyImpact = allocationImpacts[currentNodeName] ?? allocationImpacts[connNodeObj?.id] ?? null;

  const deficitEntries = Object.entries(alloc?.resource_deficit_by_bs || {}).sort((a, b) => b[1] - a[1]);
  const hottestLoadEntry = Object.entries(alloc?.bs_load_after_allocation || {}).sort((a, b) => b[1] - a[1])[0] || null;
  const hottestDeficit = deficitEntries[0] || null;

  const currentRisks = [];
  if (latency !== null && latency > 20) currentRisks.push({ tone: 'bad', text: `현재 지연시간 ${latency.toFixed(1)}ms로 임계치에 근접합니다.` });
  if (congestion !== null && congestion >= 0.65) currentRisks.push({ tone: 'bad', text: `${currentNodeName} 혼잡도가 ${(congestion * 100).toFixed(0)}%로 높습니다.` });
  if (lossDb !== null && lossDb >= 15) currentRisks.push({ tone: 'warn', text: `건물 차폐 손실 ${lossDb.toFixed(1)}dB로 전파 품질 저하가 큽니다.` });
  if (buildings !== null && buildings >= 3) currentRisks.push({ tone: 'warn', text: `현재 연결선이 건물 ${buildings}개를 가로질러 차폐 민감도가 높습니다.` });
  if (currentDeficit > 0) currentRisks.push({ tone: 'warn', text: `${currentNodeName}에 RB 부족분 ${currentDeficit.toFixed(1)}가 남아 있습니다.` });
  if (currentEdgeNet && currentEdgeNet.within_coverage === false) currentRisks.push({ tone: 'bad', text: `현재 구간 ${currentEdgeName}이 커버리지 외곽으로 표시됩니다.` });
  if (!currentRisks.length) currentRisks.push({ tone: 'good', text: '현재 연결 상태는 즉시 대응이 필요한 위험 없이 안정적입니다.' });

  let recommendedAction = '현재 노드 유지';
  let actionUrgency = '낮음';
  let actionReason = '현재 선택이 latency와 안정성 기준에서 가장 균형이 좋습니다.';
  if (currentDeficit > 0 && hottestDeficit && (hottestDeficit[0] === currentNodeName || hottestDeficit[0] === connNodeObj?.id)) {
    recommendedAction = '자원 재배분 검토';
    actionUrgency = '중간';
    actionReason = '현재 연결 노드에 자원 부족이 발생해 queue delay 확산 가능성이 있습니다.';
  } else if (congestion !== null && congestion >= 0.75) {
    recommendedAction = '대체 노드 감시';
    actionUrgency = '중간';
    actionReason = '혼잡도가 급증하면 다음 후보 노드로 전환할 준비가 필요합니다.';
  } else if (lossDb !== null && lossDb >= 18) {
    recommendedAction = '차폐 구간 확인';
    actionUrgency = '중간';
    actionReason = '차폐 손실이 커서 현재 경로 인접 구간의 통신 품질 저하를 점검해야 합니다.';
  } else if (bestAlt && bestAltDelta !== null && bestAltDelta < -2) {
    recommendedAction = '후보 재평가';
    actionUrgency = '높음';
    actionReason = `${bestAlt.name ?? bestAlt.id}가 현재보다 ${Math.abs(bestAltDelta).toFixed(1)}ms 더 낮은 지연을 보입니다.`;
  }

  const coverageSeverity = currentEdgeNet?.within_coverage === false ? 'bad' : (lossDb !== null && lossDb >= 15 ? 'warn' : 'good');

  const edgeTimeline = [...completedEdges.slice().reverse(), ...(currentEdgeNet ? [currentEdgeNet] : [])];
  const stripItems = edgeTimeline.map((e, idx) => {
    const tone = edgeCongTone(e.load_ratio || 0);
    const label = e.street_name
      ? e.street_name.replace(/\s+/g, ' ').slice(0, 10)
      : (e.edge_id || `e${idx}`).slice(-8);
    return {
      label,
      title: e.street_name || e.edge_id || `edge ${idx + 1}`,
      badge: e.isCurrent ? 'NOW' : `-${edgeTimeline.length - idx - 1}`,
      value: hasLiveEdges && e.speed_kmh !== null ? `${e.speed_kmh.toFixed(0)}` : `${((e.load_ratio || 0) * 100).toFixed(0)}%`,
      meta: hasLiveEdges && e.speed_kmh !== null
        ? `속도 ${e.speed_kmh.toFixed(1)}km/h · 혼잡도 ${(e.load_ratio * 100).toFixed(0)}%`
        : `부하 ${(e.load_ratio * 100).toFixed(0)}%`,
      color: e.isCurrent
        ? 'linear-gradient(180deg, rgba(55,132,255,0.95) 0%, rgba(39,96,211,0.95) 100%)'
        : tone === 'good'
          ? 'linear-gradient(180deg, rgba(41,182,124,0.9) 0%, rgba(23,141,93,0.9) 100%)'
          : tone === 'warn'
            ? 'linear-gradient(180deg, rgba(245,171,53,0.92) 0%, rgba(217,126,20,0.92) 100%)'
            : 'linear-gradient(180deg, rgba(229,83,83,0.95) 0%, rgba(187,48,48,0.95) 100%)',
      isCurrent: !!e.isCurrent,
    };
  });
  const profileEdges = edgeTimeline;
  const profileSeries = [profileEdges.map(e => hasLiveEdges ? (e.speed_kmh ?? 0) : ((e.load_ratio || 0) * 100))];
  const profileLabels = profileEdges.map((e, idx) => e.isCurrent ? 'NOW' : `${idx + 1}`);
  const profileTone = hasLiveEdges ? 'var(--brand-2)' : 'var(--warn)';
  const profileThreshold = hasLiveEdges ? 20 : 65;
  const minProfileSpeed = hasLiveEdges
    ? (() => {
        const vals = profileEdges.map(e => e.speed_kmh).filter(v => typeof v === 'number');
        return vals.length ? Math.min(...vals) : null;
      })()
    : null;
  const maxProfileLoad = !hasLiveEdges
    ? (() => {
        const vals = profileEdges.map(e => (e.load_ratio || 0) * 100);
        return vals.length ? Math.max(...vals) : null;
      })()
    : null;

  /* ====== ported from tab-forecast.jsx — 미래 위험 예측 ====== */
  const [hops, setHops] = useState(3);
  const [lookahead, setLookahead] = useState(null);
  const [lookaheadLoading, setLookaheadLoading] = useState(false);
  const [lookaheadError, setLookaheadError] = useState(null);

  const aheadNodeId = currentEdgeId && currentEdgeId.includes('_') ? currentEdgeId.split('_')[1] : null;
  const fallbackLookahead = useMemo(
    () => buildFallbackLookahead(routeEdges, vehiclePos, hops),
    [routeEdges, vehiclePos, hops]
  );
  const activeLookahead = lookahead?.available ? lookahead : fallbackLookahead;
  const usingFallback = !lookahead?.available && !!fallbackLookahead;

  function fetchLookahead() {
    if (!aheadNodeId) {
      setLookahead(null);
      setLookaheadLoading(false);
      return;
    }
    setLookaheadLoading(true); setLookaheadError(null);
    fetch(`http://127.0.0.1:8001/api/route/lookahead?node_id=${encodeURIComponent(aheadNodeId)}&hops=${hops}`)
      .then(r => r.json())
      .then(data => {
        if (!data.available) {
          setLookaheadError(data.detail || '데이터 없음');
          setLookahead(null);
        } else {
          setLookahead(data);
        }
        setLookaheadLoading(false);
      })
      .catch(e => {
        setLookaheadError(e.message || '조회 실패');
        setLookahead(null);
        setLookaheadLoading(false);
      });
  }

  useEffect(() => {
    // 현재 실행 시트가 아니면 라이브 백엔드 조회를 멈춘다 — 안 그러면 지금 살아있는 다른
    // 시트의 lookahead가 잘못 표시된다. 이 시트의 routeEdges/vehiclePos로 만든
    // fallbackLookahead(아래 activeLookahead)가 대신 보여진다.
    if (!isLiveSheet) { setLookahead(null); setLookaheadLoading(false); setLookaheadError(null); return; }
    fetchLookahead();
    const t = setInterval(fetchLookahead, 4000);
    return () => clearInterval(t);
  }, [aheadNodeId, hops, isLiveSheet]);

  const lookaheadScore = activeLookahead?.future_connectivity_score ?? null;
  const lookaheadRisk = activeLookahead?.risk_level ?? null;
  const perHop = activeLookahead?.per_hop ?? [];
  const avgCoverage = perHop.length
    ? perHop.reduce((sum, hop) => sum + (hop.coverage_fraction ?? 0), 0) / perHop.length
    : null;
  const worstHop = perHop.length
    ? perHop.reduce((worst, hop) => ((hop.coverage_fraction ?? 1) < (worst?.coverage_fraction ?? 2) ? hop : worst), null)
    : null;
  const weakHopCount = perHop.filter(h => (h.coverage_fraction ?? 0) < 0.8).length;
  const dangerHopCount = perHop.filter(h => (h.coverage_fraction ?? 0) < 0.5).length;
  const disconnectRisk = (() => {
    if (!activeLookahead) return null;
    if ((activeLookahead.total_uncovered ?? 0) >= 3 || dangerHopCount >= 2) return 'high';
    if ((activeLookahead.total_uncovered ?? 0) >= 1 || weakHopCount >= 2) return 'medium';
    return 'low';
  })();
  const futureRiskPenalty = lookaheadScore !== null ? Math.max(0, 1 - lookaheadScore) : null;
  const policyLookahead = simConfig?.policy_options?.lookahead_k ?? hops;
  const forecastRouteAlgo = simConfig?.algorithm_selection?.route_algorithm || routeEdges?.routing_mode || '—';
  const futureWatch = (() => {
    if (!activeLookahead) return '예측 데이터를 기다리는 중입니다.';
    if (disconnectRisk === 'high') return '다음 구간에서 미커버 edge가 이어져 단절 위험이 큽니다.';
    if (disconnectRisk === 'medium') return '약한 커버리지 구간이 연속으로 나타나 handover 감시가 필요합니다.';
    return '현재 look-ahead 범위에서는 커버리지 유지 가능성이 높습니다.';
  })();
  const rerouteWatch = (() => {
    if (!activeLookahead) return '—';
    if (disconnectRisk === 'high') return 'reroute 또는 node switch 검토';
    if (dangerHopCount >= 1) return 'node switch 감시';
    if (weakHopCount >= 1) return 'coverage monitor';
    return '현재 경로 유지';
  })();

  return (
    <div className="page-pad fade">

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="page-head" style={{ alignItems: 'flex-start' }}>
        <div>
          <div className="eyebrow">Overview</div>
          <h1>대시보드 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Dashboard</span></h1>
          <div className="sub">V2X 통신 알고리즘 성능 실시간 모니터링</div>
          {/* Algorithm badges */}
          <div className="row gap8" style={{ marginTop: 10, flexWrap: 'wrap' }}>
            {routeAlgo && (
              <Chip tone="brand">
                <span className="mono" style={{ fontSize: 9.5, opacity: 0.7 }}>경로</span>{' '}
                {algoLabel(routeAlgo)}
              </Chip>
            )}
            {bsAlgo && (
              <Chip>
                <span className="mono" style={{ fontSize: 9.5, opacity: 0.7 }}>BS</span>{' '}
                {algoLabel(bsAlgo)}
              </Chip>
            )}
            {latencyAlgo && (
              <Chip>
                <span className="mono" style={{ fontSize: 9.5, opacity: 0.7 }}>지연</span>{' '}
                {algoLabel(latencyAlgo)}
              </Chip>
            )}
            {resourceAlgo && (
              <Chip>
                <span className="mono" style={{ fontSize: 9.5, opacity: 0.7 }}>자원</span>{' '}
                {algoLabel(resourceAlgo)}
              </Chip>
            )}
          </div>
        </div>
        <div className="row gap12">
          {hasLive && !arrived && <Chip tone="good" dot>LIVE</Chip>}
          {arrived   && <Chip tone="good" dot>도착 완료</Chip>}
          <button className="btn" onClick={() => go('analysis')}><Icon.chart size={15} /> 분석</button>
          <button className="btn primary" onClick={() => go('simulation')}><Icon.map size={15} /> 시뮬레이션</button>
        </div>
      </div>

      {sheetList.length > 1 && (
        <div className="row gap8" style={{ marginBottom: 12 }}>
          <Seg value={viewSheetIdx} onChange={setViewSheetIdx} options={sheetList.map((s, i) => ({
            v: i,
            label: (
              <span className="row gap6" style={{ alignItems: 'center' }}>
                {s.name}
                {i === activeSheetIdx && (
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--good)', display: 'inline-block', flex: '0 0 auto' }} title="현재 시뮬레이션 실행 시트" />
                )}
              </span>
            ),
          }))} />
        </div>
      )}

      <div className="row gap8" style={{ marginBottom: 18 }}>
        <Seg value={subTab} onChange={setSubTab} options={[
          { v: 'summary', label: '현황 요약' },
          { v: 'network', label: '네트워크 상세' },
          { v: 'forecast', label: '미래 위험' },
        ]} />
      </div>

      {subTab === 'summary' && <>
      {/* ── Arrival summary ────────────────────────────────── */}
      {arrived && (
        <div style={{
          marginBottom: 14, padding: '14px 20px',
          background: 'var(--good-tint)', border: '1px solid var(--good-line)',
          borderRadius: 'var(--r-lg)',
        }}>
          <div className="row gap8" style={{ marginBottom: 12 }}>
            <Icon.check size={15} style={{ color: 'var(--good)' }} />
            <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--good)' }}>시뮬레이션 완료</span>
          </div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
            {[
              { label: '총 소요 시간',    value: fmtClock(sim.elapsed) },
              { label: '평균 Latency',    value: avgLatency !== null ? avgLatency.toFixed(1) + 'ms' : '—' },
              { label: '최고 Latency',    value: maxLatency !== null ? maxLatency.toFixed(1) + 'ms' : '—' },
              { label: '핸드오버',        value: handoverCount + '회' },
              { label: '단절 횟수',       value: disconnCount + '회' },
            ].map((item, i) => (
              <div key={i} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 4 }}>{item.label}</div>
                <div className="num" style={{ fontSize: 17, fontWeight: 600 }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Stat row 1 ─────────────────────────────────────── */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: 14 }}>
        {/* 시뮬레이션 상태 */}
        <Stat
          label="시뮬레이션 상태"
          icon="clock"
          value={sim.running ? '실행 중' : sim.elapsed > 0 ? '일시정지' : '미시작'}
          sub={fmtClock(sim.elapsed) + ' 경과'}
          accent={sim.running}
        />
        {/* 차량 수 */}
        <Stat
          label="차량 수"
          icon="car"
          value={hasLive ? '1' : '0'}
          unit="대"
          sub={hasLive ? (arrived ? '도착 완료' : `진행 ${(progress * 100).toFixed(0)}%`) : '—'}
        />
        {/* 현재 속도 */}
        <Stat
          label="현재 속도"
          icon="speed"
          value={speed !== null ? speed.toFixed(1) : '—'}
          unit={speed !== null ? 'km/h' : ''}
          sub={hasLive ? 'SUMO 실측' : '—'}
        />
        {/* 진행률 */}
        <div className="stat">
          <div className="label">진행률<span style={{ marginLeft: 4 }}><Icon.route size={13} style={{ opacity: 0.4 }} /></span></div>
          <div className="value num">{(progress * 100).toFixed(0)}<span className="unit">%</span></div>
          <div style={{ marginTop: 8 }}>
            <div className="pbar">
              <div style={{
                width: `${progress * 100}%`, height: '100%', borderRadius: 6,
                background: progress >= 1 ? 'var(--good)' : 'var(--brand-2)',
                transition: 'width .5s',
              }} />
            </div>
          </div>
          <div className="sub" style={{ display: 'flex', gap: 3, alignItems: 'baseline' }}>
            {totalDistM ? (
              <>
                <span className="num" style={{ fontWeight: 600, fontSize: 11 }}>
                  {Math.round(progress * totalDistM)}m
                </span>
                <span style={{ opacity: 0.5 }}>/ {Math.round(totalDistM)}m</span>
              </>
            ) : '—'}
          </div>
        </div>
        {/* 현재 도로 */}
        <div className="stat">
          <div className="label">현재 도로<span style={{ marginLeft: 4 }}><Icon.pin size={13} style={{ opacity: 0.4 }} /></span></div>
          <div style={{ fontSize: displayRoad ? 14 : 27, fontWeight: 600, marginTop: 6, lineHeight: 1.35, wordBreak: 'keep-all', fontFamily: displayRoad ? 'inherit' : 'var(--mono)' }}>
            {displayRoad ?? '—'}
          </div>
          {displayRoad && !currentRoad && (
            <div className="sub" style={{ marginTop: 4, fontSize: 10, opacity: 0.5 }}>이전 구간</div>
          )}
        </div>
      </div>

      {/* ── Stat row 2 ─────────────────────────────────────── */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: 14 }}>
        {/* 현재 Latency */}
        <div className="stat">
          <div className="label">현재 Latency<span style={{ marginLeft: 4 }}><Icon.latency size={13} style={{ opacity: 0.4 }} /></span></div>
          <div className="value num">{latTxt}{latency !== null && <span className="unit">ms</span>}</div>
          <div className="row gap8" style={{ marginTop: 8, flexWrap: 'wrap' }}>
            {latTone && (
              <Chip tone={latTone}>
                {latTone === 'good' ? '정상' : latTone === 'warn' ? '보통' : '위험'}
              </Chip>
            )}
            {avgLatency !== null && (
              <span className="sub" style={{ margin: 0 }}>평균 {avgLatency.toFixed(1)}ms</span>
            )}
          </div>
        </div>
        {/* 핸드오버 횟수 */}
        <Stat
          label="핸드오버 횟수"
          icon="antenna"
          value={handoverCount}
          unit="회"
          sub={connNode ? `현재: ${connNode}` : (hasLive ? 'BS 없음' : '—')}
        />
        {/* 단절 횟수 */}
        <Stat
          label="단절 횟수"
          icon="warn"
          value={disconnCount}
          unit="회"
          sub={disconnCount > 0 ? '커버리지 단절 감지' : (hasLive ? '단절 없음' : '—')}
        />
        {/* 신호 손실 */}
        <div className="stat">
          <div className="label">신호 손실<span style={{ marginLeft: 4 }}><Icon.layers size={13} style={{ opacity: 0.4 }} /></span></div>
          <div className="value num">
            {lossDb !== null ? lossDb.toFixed(1) : '—'}
            {lossDb !== null && <span className="unit">dB</span>}
          </div>
          {stability !== null && (
            <div style={{ marginTop: 8 }}>
              <Chip tone={stability > 0.7 ? 'good' : stability > 0.4 ? 'warn' : 'bad'}>
                안정성 {(stability * 100).toFixed(0)}%
              </Chip>
            </div>
          )}
        </div>
      </div>

      {/* ── Latency breakdown ──────────────────────────────── */}
      <LatencyBreakdown total={latency} lBase={lBase} lSignal={lSignal} lQueue={lQueue} />

      {/* ── Info row ───────────────────────────────────────── */}
      <InfoRow networkTelemetry={networkTelemetry} routeEdges={routeEdges} />

      {/* ── Charts row ─────────────────────────────────────── */}
      <div className="grid" style={{ gridTemplateColumns: '1.4fr 1fr', gap: 14 }}>
        {/* Latency 추이 */}
        <Card
          title="Latency 추이"
          en={simHistory.length >= 2 ? '실시간 · ms' : '대기 중'}
          right={latency !== null
            ? <Chip tone={latTone} dot>{latTxt}ms</Chip>
            : <Chip>대기중</Chip>}
        >
          <LineChart
            series={[latencyHistory.length >= 2 ? latencyHistory : [0, 0]]}
            height={190} yUnit="ms" yMax={32} threshold={20}
            labels={latencyLabels}
          />
          <div className="row gap16" style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-3)' }}>
            <span className="row gap6">
              <span style={{ width: 18, height: 3, background: 'var(--brand-2)', borderRadius: 2, flexShrink: 0 }} />
              실시간 Latency
            </span>
            <span className="row gap6">
              <span style={{ width: 18, height: 0, borderTop: '2px dashed var(--bad)', flexShrink: 0 }} />
              위험 임계 20ms
            </span>
          </div>
        </Card>

        {/* Right column */}
        <div className="col" style={{ gap: 14 }}>
          {/* 최근 이벤트 피드 */}
          <Card
            title="최근 이벤트"
            en="Event feed"
            right={<Chip>{logs_safe.length}건</Chip>}
          >
            {recentLogs.length > 0 ? (
              <div className="col" style={{ gap: 0 }}>
                {recentLogs.map((l, i) => (
                  <div key={i} className="row gap10" style={{
                    padding: '7px 0',
                    borderBottom: i < recentLogs.length - 1 ? '1px solid var(--border)' : 'none',
                    alignItems: 'flex-start',
                  }}>
                    <span className="mono muted" style={{ fontSize: 10, flex: '0 0 auto', marginTop: 2 }}>{l.t}</span>
                    <Chip tone={LOG_TONE_DASH[l.kind] ?? ''} style={{ flex: '0 0 auto' }}>
                      {LOG_KO_DASH[l.kind] ?? l.kind}
                    </Chip>
                    <span style={{ fontSize: 11.5, lineHeight: 1.4, color: 'var(--ink-2)' }}>{l.ko}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '18px 0', color: 'var(--ink-4)', fontSize: 12 }}>
                이벤트 없음
              </div>
            )}
          </Card>

          {/* BS 후보 비교 */}
          <Card
            title="BS 후보 비교"
            en={candidates.length > 0 ? `후보 ${candidates.length}개 · latency` : '후보 없음'}
            right={<span className="mono muted" style={{ fontSize: 10 }}>ms</span>}
          >
            {bsItems.length > 0 ? (
              <BarChart items={bsItems} max={100} />
            ) : (
              <div style={{ textAlign: 'center', padding: '16px 0', color: 'var(--ink-4)', fontSize: 12 }}>
                기지국 없음
              </div>
            )}
          </Card>
        </div>
      </div>
      </>}

      {subTab === 'network' && <>
      {/* ════════════════════════════════════════════════════
          네트워크 상세 (ported from tab-network.jsx)
          ════════════════════════════════════════════════════ */}
      <div style={{ margin: '4px 0 14px' }}>
        <div className="eyebrow">Infrastructure detail</div>
        <h2 style={{ fontSize: 17, fontWeight: 700, margin: '4px 0 2px' }}>
          네트워크 상세 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>Network</span>
        </h2>
        <div className="sub" style={{ fontSize: 12 }}>기지국 상태 및 엣지(도로구간) 혼잡도</div>
      </div>

      {hasLiveNet && connNode && (
        <Card title="현재 연결 기지국" en="Connected node" right={<Chip tone="good" dot>연결 중</Chip>} style={{ marginBottom: 18 }}>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(6, 1fr)', gap: 12 }}>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">기지국 명</div>
              <div className="value" style={{ fontSize: 16, fontFamily: 'var(--mono)' }}>{connNode}</div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">노드 타입</div>
              <div className="value" style={{ fontSize: 16 }}>{currentNodeType || '—'}</div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">거리</div>
              <div className="value" style={{ fontSize: 16 }}>{distanceM !== null ? distanceM.toFixed(0) : '—'}<span className="unit">m</span></div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">신호 손실</div>
              <div className="value" style={{ fontSize: 16 }}>{lossDb !== null ? lossDb.toFixed(1) : '—'}<span className="unit">{lossDb !== null ? 'dB' : ''}</span></div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">안정성</div>
              <div className="value" style={{ fontSize: 16 }}>{stability !== null ? stability.toFixed(2) : '—'}</div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">할당 RB</div>
              <div className="value" style={{ fontSize: 16 }}>{egoAllocatedRb !== null ? egoAllocatedRb.toFixed(1) : '—'}</div>
            </div>
          </div>
          <div className="grid" style={{ gridTemplateColumns: '1.35fr 1fr', gap: 12, marginTop: 12 }}>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">선택 이유</div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.6 }}>
                <div><b style={{ color: 'var(--ink)' }}>Primary:</b> {primaryReason}</div>
                <div style={{ marginTop: 4 }}><b style={{ color: 'var(--ink)' }}>Secondary:</b> {secondaryReason}</div>
                <div style={{ marginTop: 4 }}><b style={{ color: 'var(--ink)' }}>Rejected alternative:</b> {bestAlt ? (bestAlt.name ?? bestAlt.id) : '—'}</div>
              </div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">즉시 효과</div>
              <div className="value" style={{ fontSize: 16 }}>{bestAltDelta !== null ? formatSignedDelta(-bestAltDelta, 'ms') : '—'}</div>
              <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6, marginTop: 6 }}>
                {bestAltDelta !== null
                  ? `차순위 후보 대비 ${bestAltDelta > 0 ? `${bestAltDelta.toFixed(1)}ms 낮은` : `${Math.abs(bestAltDelta).toFixed(1)}ms 높은`} 현재 지연`
                  : '차순위 후보와의 즉시 성능 차이를 계산하는 중입니다.'}
              </div>
            </div>
          </div>
          {buildings !== null && (
            <div className="row gap8" style={{ marginTop: 10 }}>
              <span className="muted" style={{ fontSize: 11.5 }}>교차 건물</span>
              <span className="num" style={{ fontWeight: 600 }}>{buildings}개</span>
              {networkTelemetry?.max_building_height_m !== null && (
                <>
                  <span className="muted" style={{ fontSize: 11.5 }}>최대 높이</span>
                  <span className="num" style={{ fontWeight: 600 }}>{networkTelemetry.max_building_height_m}m</span>
                </>
              )}
            </div>
          )}
        </Card>
      )}

      {hasLiveNet && (
        <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 18 }}>
          <Card title="현재 리스크 요약" en="Current risk summary">
            <div className="row" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
              <Chip tone={currentRisks[0]?.tone || 'brand'} dot>{currentRisks[0]?.tone === 'good' ? '안정 상태' : '주의 필요'}</Chip>
              {currentEdgeNet?.within_coverage === false && <Chip tone="bad" dot>Coverage edge</Chip>}
              {currentDeficit > 0 && <Chip tone="warn" dot>RB deficit</Chip>}
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {currentRisks.map((risk, idx) => (
                <div key={idx} className="row gap8" style={{ alignItems: 'flex-start' }}>
                  <Chip tone={risk.tone} dot>{idx === 0 ? 'Main' : 'Watch'}</Chip>
                  <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>{risk.text}</div>
                </div>
              ))}
            </div>
          </Card>
          <Card title="추천 액션" en="Recommended action" right={<Chip tone={actionUrgency === '높음' ? 'bad' : actionUrgency === '중간' ? 'warn' : 'good'}>{actionUrgency}</Chip>}>
            <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">권장 조치</div>
                <div className="value" style={{ fontSize: 16 }}>{recommendedAction}</div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{actionReason}</div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">적용 정책</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                  <div><span className="muted">Route</span> <span className="mono">{routingMode}</span></div>
                  <div><span className="muted">Select</span> <span className="mono">{activeSelectionAlgo}</span></div>
                  <div><span className="muted">Latency</span> <span className="mono">{activeLatencyAlgoNet}</span></div>
                  <div><span className="muted">Alloc</span> <span className="mono">{activeAllocAlgo}</span></div>
                </div>
              </div>
            </div>
          </Card>
        </div>
      )}

      {hasLiveNet && candidates.length > 0 ? (
        <Card title="기지국 후보 비교" en="Candidate nodes" right={<Chip>{candidates.length}개</Chip>} style={{ padding: 0, marginBottom: 18 }}>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th className="r">순위</th>
                  <th>기지국 ID</th>
                  <th>타입<span className="en">Type</span></th>
                  <th className="r">거리<span className="en">Distance</span></th>
                  <th className="r">부하<span className="en">Load</span></th>
                  <th className="r">예측 Latency<span className="en">ms</span></th>
                  <th className="r">Node Score</th>
                  <th className="r">신뢰도<span className="en">Conf</span></th>
                  <th>판단 근거<span className="en">Reason</span></th>
                  <th>상태<span className="en">Status</span></th>
                </tr>
              </thead>
              <tbody>
                {candidates.slice(0, 8).map((c, i) => {
                  const name = c.name ?? c.id ?? '—';
                  const isConn = name === connNode;
                  const ms = c.predicted_latency_ms ?? 0;
                  const tone = latencyTone(ms);
                  const nodeLoad = c.congestion_score ?? null;
                  const reasonTag =
                    (minLatency !== null && Math.abs(ms - minLatency) < 0.25) ? 'low latency' :
                    (nodeLoad !== null && minCongestion !== null && Math.abs(nodeLoad - minCongestion) < 0.03) ? 'low load' :
                    ((c.distance_m ?? Infinity) === minDistance) ? 'nearby' :
                    ((c.confidence ?? 0) >= 0.9) ? 'high confidence' :
                    'balanced';
                  return (
                    <tr key={i} style={isConn ? { background: 'var(--brand-tint)' } : {}}>
                      <td className="r"><span className="num" style={{ fontWeight: 700 }}>{i + 1}</span></td>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>{name}</span>
                        {isConn && <Chip tone="good" dot style={{ marginLeft: 8 }}>연결 중</Chip>}
                      </td>
                      <td><span className="mono" style={{ fontSize: 11 }}>{c.type || '—'}</span></td>
                      <td className="r"><span className="num">{(c.distance_m ?? 0).toFixed(0)}</span> <span className="muted" style={{ fontSize: 10 }}>m</span></td>
                      <td className="r">
                        {nodeLoad !== null
                          ? <><span className="num">{(nodeLoad * 100).toFixed(0)}</span><span className="muted" style={{ fontSize: 10 }}>%</span></>
                          : <span className="muted">—</span>}
                      </td>
                      <td className="r"><span className="num" style={{ color: `var(--${tone})`, fontWeight: 600 }}>{ms.toFixed(1)}</span></td>
                      <td className="r"><span className="num">{(c.node_score ?? 0).toFixed(2)}</span></td>
                      <td className="r"><span className="num">{((c.confidence ?? 0) * 100).toFixed(0)}</span><span className="muted" style={{ fontSize: 10 }}>%</span></td>
                      <td><Chip tone={reasonTag === 'low latency' ? 'good' : reasonTag === 'low load' ? 'brand' : 'warn'}>{reasonTag}</Chip></td>
                      <td><Chip tone={tone} dot>{tone === 'good' ? '양호' : tone === 'warn' ? '보통' : '위험'}</Chip></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <Card title="기지국 상태" en="Base stations" style={{ marginBottom: 18 }}>
          <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--ink-4)', fontSize: 12 }}>
            {hasLiveNet ? '연결 가능한 기지국 후보가 없습니다.' : '시뮬레이션이 실행되면 기지국 후보 비교가 표시됩니다.'}
          </div>
        </Card>
      )}

      {alloc?.available && (
        <div className="grid" style={{ gridTemplateColumns: '1.2fr 1fr', marginBottom: 18 }}>
          <Card title="자원배분 현황" en="Resource allocation" right={<Chip tone="brand">{alloc.algorithm_id}</Chip>}>
            {Object.keys(alloc.bs_load_after_allocation || {}).length === 0 ? (
              <div className="muted" style={{ padding: 8, fontSize: 12 }}>할당 결과가 없습니다.</div>
            ) : (
              <>
                <BarChart
                  items={Object.entries(alloc.bs_load_after_allocation).map(([bsId, load]) => {
                    const deficit = alloc.resource_deficit_by_bs?.[bsId] ?? 0;
                    const tone = load >= 0.8 ? 'bad' : load >= 0.5 ? 'warn' : 'good';
                    return {
                      label: bsLabel(bsId),
                      value: load * 100,
                      display: `${(load * 100).toFixed(0)}%${deficit > 0 ? ` (-${deficit.toFixed(1)})` : ''}`,
                      color: `var(--${tone})`,
                    };
                  })}
                  max={100}
                />
                <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginTop: 12 }}>
                  <div className="stat" style={{ padding: '12px 14px' }}>
                    <div className="label">최대 부하 노드</div>
                    <div className="value" style={{ fontSize: 15 }}>{hottestLoadEntry ? bsLabel(hottestLoadEntry[0]) : '—'}</div>
                    <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                      {hottestLoadEntry ? `${(hottestLoadEntry[1] * 100).toFixed(0)}% after allocation` : '—'}
                    </div>
                  </div>
                  <div className="stat" style={{ padding: '12px 14px' }}>
                    <div className="label">자원 부족 hotspot</div>
                    <div className="value" style={{ fontSize: 15 }}>{hottestDeficit ? bsLabel(hottestDeficit[0]) : '—'}</div>
                    <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                      {hottestDeficit ? `${hottestDeficit[1].toFixed(1)} RB deficit` : '부족 없음'}
                    </div>
                  </div>
                  <div className="stat" style={{ padding: '12px 14px' }}>
                    <div className="label">현재 노드 영향</div>
                    <div className="value" style={{ fontSize: 15 }}>{currentLatencyImpact !== null ? formatSignedDelta(currentLatencyImpact, 'ms') : '—'}</div>
                    <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>allocation 이후 예상 latency 변화</div>
                  </div>
                </div>
              </>
            )}
            <div className="muted" style={{ fontSize: 10.5, marginTop: 10, fontFamily: 'var(--mono)' }}>BS별 할당 후 부하율(%) · 괄호는 RB 부족분</div>
          </Card>
          <Card title="차폐 / 커버리지" en="Coverage & obstruction">
            <div className="grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', gap: 12 }}>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">교차 건물 수</div>
                <div className="value" style={{ fontSize: 16 }}>{buildings !== null ? buildings : '—'}<span className="unit">{buildings !== null ? '개' : ''}</span></div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">최대 높이</div>
                <div className="value" style={{ fontSize: 16 }}>{networkTelemetry?.max_building_height_m ?? '—'}<span className="unit">{networkTelemetry?.max_building_height_m !== null ? 'm' : ''}</span></div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">투과 손실</div>
                <div className="value" style={{ fontSize: 16 }}>{lossDb !== null ? lossDb.toFixed(1) : '—'}<span className="unit">{lossDb !== null ? 'dB' : ''}</span></div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">Coverage 상태</div>
                <div style={{ marginTop: 6 }}><Chip tone={coverageSeverity} dot>{currentEdgeNet?.within_coverage === false ? '약한 커버리지' : lossDb !== null && lossDb >= 15 ? '차폐 주의' : '안정'}</Chip></div>
              </div>
            </div>
            <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6, marginTop: 12 }}>
              현재 연결선 기준으로 건물 차폐와 커버리지 상태를 요약합니다. 장기 예측은 아래 미래 위험 예측에서 확인합니다.
            </div>
          </Card>
        </div>
      )}

      {mergedEdges.length > 0 && (
        <div className="grid" style={{ gridTemplateColumns: '1.5fr 1fr', marginBottom: 18 }}>
          <Card title="엣지 혼잡도" en="Edge congestion"
            right={
              <div className="row gap8">
                {hasLiveEdges && <Chip tone="good" dot>LIVE</Chip>}
                {nEdges > 0 && <Chip>{curEdgeIdx + 1} / {nEdges}</Chip>}
                {routeEdges?.routing_mode && <Chip>{routeEdges.routing_mode}</Chip>}
              </div>
            }
            style={{ padding: 0 }}>
            <div style={{ padding: '14px 16px 8px', borderBottom: '1px solid var(--border)' }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-2)' }}>경로 구간 스트립</div>
                  <div className="muted" style={{ fontSize: 11 }}>최근 완료 구간에서 현재 구간까지의 혼잡 흐름</div>
                </div>
                <div className="row gap8">
                  <Chip tone="good" dot>원활</Chip>
                  <Chip tone="warn" dot>보통</Chip>
                  <Chip tone="bad" dot>혼잡</Chip>
                </div>
              </div>
              <SegmentStrip items={stripItems} />
              <div className="muted" style={{ fontSize: 10.5, marginTop: 10, fontFamily: 'var(--mono)' }}>
                {hasLiveEdges ? '색상은 혼잡도, 숫자는 km/h · NOW는 현재 주행 중인 구간' : '색상과 숫자는 load ratio · NOW는 현재 주행 중인 구간'}
              </div>
            </div>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>도로명<span className="en">Road</span></th>
                    <th className="r">거리<span className="en">Dist</span></th>
                    <th className="r">{hasLiveEdges ? '속도' : '지연'}<span className="en">{hasLiveEdges ? 'Speed' : 'Lat'}</span></th>
                    <th className="r">{hasLiveEdges ? '차량' : '부하'}<span className="en">{hasLiveEdges ? 'Veh' : 'Load'}</span></th>
                    <th>혼잡도<span className="en">Level</span></th>
                  </tr>
                </thead>
                <tbody>
                  {mergedEdges.map((e, i) => {
                    const tone = edgeCongTone(e.load_ratio || 0);
                    return (
                      <tr key={e.edge_id || i} style={e.isCurrent ? { background: 'var(--brand-tint)', fontWeight: 500 } : {}}>
                        <td>
                          {e.street_name
                            ? <><span style={{ fontSize: 12 }}>{e.street_name}</span>{e.isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}</>
                            : <><span className="mono muted" style={{ fontSize: 10 }}>{e.edge_id}</span>{e.isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}</>}
                          {!e.within_coverage && <span className="chip" style={{ marginLeft: 4, fontSize: 9, background: 'var(--warn-tint)', color: 'var(--warn)' }}>미커버</span>}
                        </td>
                        <td className="r">
                          {e.distance_m != null
                            ? <><span className="num">{e.distance_m.toFixed(0)}</span><span className="muted" style={{ fontSize: 10 }}> m</span></>
                            : <span className="muted">—</span>}
                        </td>
                        <td className="r">
                          {hasLiveEdges && e.speed_kmh !== null ? (
                            <><span className="num" style={{ color: `var(--${edgeCongTone(1 - (e.speed_kmh / 50))})`, fontWeight: 600 }}>{e.speed_kmh.toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> km/h</span></>
                          ) : (
                            <><span className="num" style={{ color: `var(--${latencyTone(e.latency_ms || 0)})`, fontWeight: 600 }}>{(e.latency_ms || 0).toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> ms</span></>
                          )}
                        </td>
                        <td className="r">
                          {hasLiveEdges && e.vehicle_count !== null ? (
                            <span className="num" style={{ fontWeight: 600 }}>{e.vehicle_count}</span>
                          ) : (
                            <div className="row gap8" style={{ justifyContent: 'flex-end' }}>
                              <div className="pbar" style={{ width: 50 }}><i style={{ width: `${Math.min((e.load_ratio || 0) * 100, 100)}%`, background: `var(--${tone})` }} /></div>
                              <span className="num" style={{ fontSize: 11, fontWeight: 600, color: `var(--${tone})`, width: 32, textAlign: 'right' }}>{((e.load_ratio || 0) * 100).toFixed(0)}%</span>
                            </div>
                          )}
                        </td>
                        <td><Chip tone={tone} dot>{edgeCongLabel(e.load_ratio || 0)}</Chip></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title={hasLiveEdges ? '구간별 속도 프로파일' : '구간별 부하 프로파일'} en={hasLiveEdges ? 'Speed profile' : 'Load profile'}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink-2)' }}>{hasLiveEdges ? '속도 흐름' : '부하 흐름'}</div>
                <div className="muted" style={{ fontSize: 11 }}>{hasLiveEdges ? '최근 구간에서 현재 구간까지 속도 저하 패턴을 표시합니다.' : '최근 구간에서 현재 구간까지 부하 변화 패턴을 표시합니다.'}</div>
              </div>
              <div className="row gap8">
                <Chip tone="brand">{hasLiveEdges ? 'km/h' : '%'}</Chip>
                <Chip tone={hasLiveEdges ? 'warn' : 'bad'}>{hasLiveEdges ? '20km/h 임계선' : '65% 임계선'}</Chip>
              </div>
            </div>
            <LineChart
              series={profileSeries}
              height={196}
              yUnit={hasLiveEdges ? 'km/h' : '%'}
              yMax={hasLiveEdges ? 60 : 100}
              colors={[profileTone]}
              labels={profileLabels}
              threshold={profileThreshold}
            />
            <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginTop: 12 }}>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">현재 구간</div>
                <div className="value" style={{ fontSize: 15 }}>{currentEdgeName}</div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{currentEdgeNet?.isCurrent ? 'NOW marker applied' : '—'}</div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">{hasLiveEdges ? '최저 속도' : '최고 부하'}</div>
                <div className="value" style={{ fontSize: 15 }}>
                  {hasLiveEdges
                    ? (minProfileSpeed !== null ? `${minProfileSpeed.toFixed(0)}` : '—')
                    : (maxProfileLoad !== null ? `${maxProfileLoad.toFixed(0)}` : '—')}
                  <span className="unit">{hasLiveEdges ? 'km/h' : '%'}</span>
                </div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>최근~현재 구간 기준</div>
              </div>
              <div className="stat" style={{ padding: '12px 14px' }}>
                <div className="label">현재 판독</div>
                <div className="value" style={{ fontSize: 15 }}>
                  {hasLiveEdges
                    ? (currentEdgeNet?.speed_kmh !== null && currentEdgeNet?.speed_kmh !== undefined ? currentEdgeNet.speed_kmh.toFixed(1) : '—')
                    : (((currentEdgeNet?.load_ratio || 0) * 100).toFixed(0))}
                  <span className="unit">{hasLiveEdges ? 'km/h' : '%'}</span>
                </div>
                <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                  {hasLiveEdges ? edgeCongLabel(1 - ((currentEdgeNet?.speed_kmh || 0) / 50)) : edgeCongLabel(currentEdgeNet?.load_ratio || 0)}
                </div>
              </div>
            </div>
          </Card>
        </div>
      )}
      </>}

      {subTab === 'forecast' && <>
      {/* ════════════════════════════════════════════════════
          미래 위험 예측 (ported from tab-forecast.jsx)
          ════════════════════════════════════════════════════ */}
      <div style={{ margin: '4px 0 14px' }}>
        <div className="row between" style={{ alignItems: 'flex-start' }}>
          <div>
            <div className="eyebrow">Forecast & risk</div>
            <h2 style={{ fontSize: 17, fontWeight: 700, margin: '4px 0 2px' }}>
              미래 위험 예측 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>Forecast</span>
            </h2>
            <div className="sub" style={{ fontSize: 12 }}>진행 방향 기준 Look-ahead 커버리지 위험도 · 건물 차폐 영향</div>
          </div>
          <Seg value={hops} onChange={setHops} options={[1, 3, 5, 8].map(h => ({ v: h, label: `${h}홉` }))} />
        </div>
      </div>

      {!activeLookahead && !sim?.running && (
        <div style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--ink-4)' }}>
          <div style={{ fontSize: 28, opacity: 0.2, marginBottom: 10 }}>◎</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: 'var(--ink-3)' }}>시뮬레이션이 실행 중이 아닙니다</div>
          <div style={{ fontSize: 12 }}>차량이 이동 중일 때 진행 방향의 위험도를 예측합니다</div>
        </div>
      )}

      {!activeLookahead && sim?.running && (
        <div style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--ink-4)' }}>
          <div style={{ fontSize: 28, opacity: 0.2, marginBottom: 10 }}>◎</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: 'var(--ink-3)' }}>예측 기준 구간을 준비 중입니다</div>
          <div style={{ fontSize: 12 }}>current edge 또는 route telemetry가 들어오면 look-ahead 예측이 표시됩니다</div>
        </div>
      )}

      {activeLookahead && (
        <>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(5,1fr)', marginBottom: 18 }}>
            <Stat label="Look-ahead 범위" icon="route" value={activeLookahead?.lookahead_hops ?? policyLookahead} unit="hop" sub={activeLookahead ? `${activeLookahead.total_edges_scanned}개 edge 스캔` : '설정값 기반'} />
            <Stat label="Future Score" icon="radar" value={lookaheadScore !== null ? lookaheadScore.toFixed(2) : '—'} unit="" sub={lookaheadRisk ? `위험도 ${RISK_KO[lookaheadRisk]}` : '—'} accent />
            <Stat label="평균 Coverage" icon="net" value={avgCoverage !== null ? (avgCoverage * 100).toFixed(0) : '—'} unit={avgCoverage !== null ? '%' : ''} sub={weakHopCount ? `약한 홉 ${weakHopCount}개` : '안정 구간'} />
            <Stat label="Disconnect Risk" icon="warn" value={disconnectRisk ? RISK_KO[disconnectRisk] : '—'} unit="" sub={dangerHopCount ? `위험 홉 ${dangerHopCount}개` : '직접 단절 신호 없음'} />
            <Stat label="Route Policy" icon="spark" value={forecastRouteAlgo} unit="" sub={usingFallback ? 'route telemetry fallback' : (simConfig?.policy_options?.avoid_disconnection ? 'avoid_disconnection on' : '기본 정책')} />
          </div>

          <div className="grid" style={{ gridTemplateColumns: '1fr 2fr', marginBottom: 18, alignItems: 'stretch' }}>
            <Card title="미래 연결 신뢰도" en="Future connectivity score">
              {lookaheadLoading && !activeLookahead ? (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>스캔 중…</div>
              ) : lookaheadScore !== null ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0' }}>
                  {usingFallback && lookaheadError && (
                    <div style={{ marginBottom: 10 }}>
                      <Chip tone="warn" dot>API fallback active</Chip>
                    </div>
                  )}
                  <Donut value={Math.round(lookaheadScore * 100)} max={100} size={120} color={`var(--${RISK_TONE[lookaheadRisk] || 'brand-2'})`} label="연결 신뢰도" />
                  <div style={{ marginTop: 14 }}>
                    <Chip tone={RISK_TONE[lookaheadRisk] || 'brand'} dot>위험도 {RISK_KO[lookaheadRisk] || lookaheadRisk}</Chip>
                  </div>
                  {usingFallback && lookaheadError && (
                    <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.55, marginTop: 10, textAlign: 'center' }}>
                      route telemetry 기반 예측을 표시 중입니다. ({lookaheadError})
                    </div>
                  )}
                </div>
              ) : lookaheadError ? (
                <div style={{ padding: 16, fontSize: 12, color: usingFallback ? 'var(--warn)' : 'var(--bad)' }}>
                  {usingFallback ? `API look-ahead 대신 route telemetry 기반 예측을 표시합니다. (${lookaheadError})` : lookaheadError}
                </div>
              ) : (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>데이터 없음</div>
              )}
            </Card>
            <Card title="홉별 커버리지 비율" en="Per-hop coverage" right={activeLookahead ? <Chip>{activeLookahead.total_uncovered}개 미커버 / {activeLookahead.total_edges_scanned}개 스캔</Chip> : null}>
              {perHop.length > 0 ? (
                <BarChart
                  items={perHop.map(h => ({
                    label: `${h.hop}홉 앞`,
                    value: (h.coverage_fraction ?? 0) * 100,
                    display: `${((h.coverage_fraction ?? 0) * 100).toFixed(0)}% (${h.covered_count}/${h.edge_count})`,
                    color: (h.coverage_fraction ?? 0) < 0.5 ? 'var(--bad)' : (h.coverage_fraction ?? 0) < 0.8 ? 'var(--warn)' : 'var(--good)',
                  }))}
                  max={100}
                />
              ) : (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>데이터 없음</div>
              )}
            </Card>
          </div>

          <div className="grid" style={{ gridTemplateColumns: '1.15fr 1fr', marginBottom: 0 }}>
            <Card title="미래 위험 요약" en="Risk outlook">
              <div className="grid" style={{ gridTemplateColumns: 'repeat(2,1fr)', gap: 12 }}>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">Worst hop</div>
                  <div className="value" style={{ fontSize: 18 }}>{worstHop ? `${worstHop.hop}홉` : '—'}</div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                    {worstHop ? `coverage ${(worstHop.coverage_fraction * 100).toFixed(0)}%` : '데이터 없음'}
                  </div>
                </div>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">Future penalty</div>
                  <div className="value" style={{ fontSize: 18 }}>{futureRiskPenalty !== null ? futureRiskPenalty.toFixed(2) : '—'}</div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>1 - future connectivity score</div>
                </div>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">Reroute watch</div>
                  <div className="value" style={{ fontSize: 18 }}>{rerouteWatch}</div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>현재는 미래 위험 예측 기준 제안입니다</div>
                </div>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">Current blockage input</div>
                  <div className="value" style={{ fontSize: 18 }}>{lossDb !== null ? lossDb.toFixed(1) : '—'}<span className="unit">{lossDb !== null ? 'dB' : ''}</span></div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                    {buildings !== null ? `현재 연결선 교차 건물 ${buildings}개` : '건물 입력 없음'}
                  </div>
                </div>
              </div>
            </Card>

            <Card title="예측 해석" en="Forecast interpretation" style={{ marginBottom: 0 }}>
              <div className="row gap8" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
                <Chip tone={RISK_TONE[disconnectRisk] || 'brand'} dot>{disconnectRisk ? `Disconnect ${RISK_KO[disconnectRisk]}` : '대기 중'}</Chip>
                <Chip tone={RISK_TONE[lookaheadRisk] || 'brand'} dot>{lookaheadRisk ? `Coverage ${RISK_KO[lookaheadRisk]}` : 'coverage 대기'}</Chip>
                {dangerHopCount > 0 && <Chip tone="bad" dot>{dangerHopCount}개 위험 홉</Chip>}
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.65 }}>
                <div><b style={{ color: 'var(--ink)' }}>Risk summary:</b> {futureWatch}</div>
                <div style={{ marginTop: 6 }}><b style={{ color: 'var(--ink)' }}>Immediate watchpoint:</b> {worstHop ? `${worstHop.hop}홉 앞 구간의 커버리지가 가장 약합니다.` : 'look-ahead 데이터 없음'}</div>
                <div style={{ marginTop: 6 }}><b style={{ color: 'var(--ink)' }}>Policy note:</b> look-ahead {policyLookahead}홉 / route {forecastRouteAlgo}</div>
                <div style={{ marginTop: 6 }}><b style={{ color: 'var(--ink)' }}>Detail split:</b> 현재 노드 선택 이유와 자원 상태는 위 네트워크 상세에서 확인합니다.</div>
              </div>
            </Card>
          </div>
        </>
      )}
      </>}
    </div>
  );
}
window.Dashboard = Dashboard;
