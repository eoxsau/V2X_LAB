/* ============================================================ Dashboard tab */

/* Latency 판정 기준 — V2X 실시간 요구예산 100ms를 기준으로 잡는다(보고서의 PIR P99 기준과 동일).
   ⚠️ 예전에는 12ms/20ms 고정값이었다. 그 값은 패킷 1Mbit 시절에 맞춰진 것으로,
   당시 5G는 셀 70% 지점 16.9ms · 90% 지점 43.0ms라 두 구간이 실제로 갈렸다.
   패킷이 100Kbit로 바뀐 뒤 같은 지점이 2.0ms · 4.6ms가 되면서 노랑/빨강이 한 번도
   뜨지 않고 초록에서 곧장 불통(1000ms)으로 떨어졌다 — 경고 역할을 잃었다.
   지금 모델에서 5G가 거리만으로 나빠지는 폭은 실제로 5ms 미만이다. 없는 중간 구간을
   억지로 만들지 않고, 대신 (a) 예산 대비 비율로 기준을 잡고 (b) 불통을 별도 상태로 표시한다.
   측정(2026-08-06): 5G 60%지점 차량 10대 1.9ms · 400대 3.9ms · 800대 이상 51.4ms(큐 포화),
                     4G 80%지점 22.8ms, 커버리지 밖 1000ms. */
const V2X_LATENCY_BUDGET_MS = 100;          // 3GPP/ETSI 실시간 V2X 예산
const LAT_WARN_MS = V2X_LATENCY_BUDGET_MS * 0.10;   // 10ms — 예산의 10%
const LAT_BAD_MS  = V2X_LATENCY_BUDGET_MS * 0.50;   // 50ms — 예산의 절반
const LAT_OUTAGE_MS = 1000;                 // formula_v31.L_OUTAGE_MS — 접속 불가

/** 지연값 → 'good' | 'warn' | 'bad'. 불통(1000ms)은 bad에 포함되며 latencyIsOutage로 구분한다. */
function latencyTone(ms) {
  if (ms === null || ms === undefined) return 'good';
  return ms >= LAT_BAD_MS ? 'bad' : ms >= LAT_WARN_MS ? 'warn' : 'good';
}
const latencyIsOutage = (ms) => ms !== null && ms !== undefined && ms >= LAT_OUTAGE_MS;

const ALGO_LABELS = {
  tech_latency_v31:          '기술모델 v3.1',
  rsrp_max:                  'RSRP 최대',
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
  // GNN-MAML RL
  rl_routing:              'GNN-MAML 경로',
  rl_based_bs_selection:   'GNN-MAML BS',
  v4_gnn:                  'GNN-MAML',
  rl_bs_placement:         'GNN-MAML 배치',
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
    if (hasCoverage && latency >= LAT_BAD_MS) coverageFraction = 0.55;
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
  /* ⚠️ 이름표는 백엔드가 실제로 담아 보내는 값에 맞춘 것이다.
     백엔드 필드명이 값과 어긋나 있다(building_obstruction_analyzer 주석 참조):
       l_base_ms   = L_base        = TTI×0.5   → 슬롯을 배정받기까지 기다리는 시간
       l_signal_ms = L_transmission = 패킷/(SE×BW) → **전송** 지연 (이름만 signal)
       l_queue_ms  = L_queue       = TTI×ρ/(1−ρ) → 대기열(혼잡) 지연
     예전 화면은 앞의 둘을 서로 바꿔 달아, 슬롯 대기를 "전송 지연"으로,
     실제 전송 지연을 "신호 처리"로 보여주고 있었다. */
  const items = [
    { label: <><i>L</i><sub>base</sub> — 슬롯 대기</>, value: lBase,   color: 'var(--brand-2)' },
    { label: <><i>L</i><sub>tx</sub> — 전송 지연</>,   value: lSignal, color: 'var(--warn)'    },
    { label: <><i>L</i><sub>q</sub> — 대기열 지연</>,  value: lQueue,  color: 'var(--bad)'     },
  ];
  const outage = latencyIsOutage(total);
  return (
    <Card
      title="Latency 분해"
      en="Breakdown · ms"
      right={<Chip tone={latencyTone(total)}>{outage ? '불통 (커버리지 밖)' : `${total.toFixed(1)}ms 합계`}</Chip>}
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
function Dashboard({ sim, go, vehiclePos: liveVehiclePos, networkTelemetry: liveNetworkTelemetry, simHistory: liveSimHistory, simLogs: liveSimLogs, simConfig: liveSimConfig, routeEdges: liveRouteEdges, sheets, activeSheetIdx, mode }) {
  const [subTab, setSubTab] = useState('summary'); // 'summary' | 'network' | 'forecast' — 한 화면에 다 펼치지 않고 묶음별로 분리
  // Lite 모드에서는 'network'/'forecast'를 선택할 UI 자체가 없다 — 그 상태에서 Pro로
  // 들어왔다가 다시 Lite로 바뀌는 경우를 대비해 강제로 'summary'로 되돌린다.
  useEffect(() => { if (mode === 'lite' && subTab !== 'summary') setSubTab('summary'); }, [mode, subTab]);

  // 시트별 대시보드 분리 — 시뮬레이션 탭에서 "지금 실행 중인" 시트(activeSheetIdx)만 실시간
  // props를 쓰고, 다른 시트를 고르면 그 시트가 캡처해둔 result(읽기전용 스냅샷)를 보여준다.
  // 마운트 시점에는 항상 "현재 실행 시트"를 기본으로 보여준다(대시보드는 탭 전환마다 새로 마운트됨).
  const [viewSheetIdx, setViewSheetIdx] = useState(() => activeSheetIdx ?? 0);
  // 시뮬레이션 탭에서 활성 시트가 바뀔 때(GNN-MAML 비교 실행 등) 대시보드도 따라감
  useEffect(() => {
    setViewSheetIdx(activeSheetIdx ?? 0);
  }, [activeSheetIdx]);
  const sheetList = sheets || [];
  const isLiveSheet = viewSheetIdx === activeSheetIdx;
  const viewedSheet = sheetList[viewSheetIdx];
  const _rlResult        = viewedSheet?.result?.rl_episode_result || null;
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
  const handoverCount  = logs_safe.filter(l => l.kind === 'handover').length
                         || _rlResult?.handover_count || 0;
  const disconnCount   = logs_safe.filter(l => l.kind === 'disconnect').length
                         || (_rlResult ? _rlResult.disconnection_steps : 0);
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
    : (_rlResult?.avg_latency_ms ?? null);  // rl_episode 결과 폴백
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
    const isRsu = c.type === 'rsu';
    return {
      label:   isRsu ? `[RSU] ${c.name ?? c.id ?? ''}` : (c.name ?? c.id ?? '').replace(/기지국\s*/, 'BS-'),
      value:   pct,
      display: ms.toFixed(1) + 'ms',
      color:   isRsu ? '#FF8F00' : (pct > 50 ? 'var(--bad)' : pct > 25 ? 'var(--warn)' : 'var(--good)'),
    };
  });

  /* latency chip */
  const latTone = latency !== null ? latencyTone(latency) : null;
  const latTxt  = latency !== null ? latency.toFixed(1) : '—';

  /* CBR — 백엔드 계산값을 그대로 쓴다 (report_builder.compute_cbr_per_edge).
     2026-07-27 이전에는 여기서 자체 계산했는데 두 가지가 모두 틀렸다:
       ρ  : vehicle_count / route_distance_m — 경로에 차가 고르게 깔렸다는 가정.
            실측에서 상위 10% 엣지가 교통량의 75%를 점유하므로 병목을 크게 과소평가한다.
       공식: 1 − exp(−ρ·R_tx·f_CAM·T_RRI) — 인용한 논문(Gonzalez-Martin, IEEE TVT 2019)에
            **존재하지 않는 식**이다. 논문의 CBR은 식 (34) N_E/N(자원 점유율)이고,
            f_CAM×T_RRI = 10Hz×0.1s = 1이라 지수가 사실상 이웃 차량 수가 되어 포화됐다.
     백엔드는 엣지별 실측 밀도 + 논문 식으로 고쳤고, 논문의 검증값(ρ=0.1 → 0.23)을 재현한다. */
  const cbrVal = routeEdges?.cbr_avg ?? null;
  const cbrTone = cbrVal === null ? null : cbrVal > 0.65 ? 'bad' : cbrVal > 0.45 ? 'warn' : 'good';

  /* PIR P99 — geometric distribution upper bound, 3GPP TR 37.885 §A.2.4
     PIR_P99 = T_CAM / PRR,  PRR = 1 − TWDR,  threshold ≤ 100 ms */
  const _twdr  = routeEdges?.coverage_risk ?? null;
  const _prr   = _twdr !== null ? Math.max(0.01, 1 - _twdr) : null;
  const pirP99 = _prr !== null ? Math.round(100 / _prr) : null;
  const pirTone = pirP99 === null ? null : pirP99 > 100 ? 'bad' : pirP99 > 80 ? 'warn' : 'good';

  /* route distance for progress sub */
  const totalDistM = routeEdges?.total_distance_m > 0 ? routeEdges.total_distance_m : null;

  /* ====== ported from tab-network.jsx — 네트워크 상세 ====== */
  const hasLiveNet = !!networkTelemetry;
  const connNodeObj = networkTelemetry?.connected_node ?? null;

  const [liveAlloc, setLiveAlloc] = useState(null);
  useEffect(() => {
    if (!isLiveSheet) { setLiveAlloc(null); return; }
    let dead = false;
    function poll() {
      fetch('http://127.0.0.1:8001/api/resources/allocation-result')
        .then(r => r.json())
        .then(data => { if (!dead) setLiveAlloc(data); })
        .catch(() => {});
    }
    poll();
    const t = setInterval(poll, 3000);
    return () => { dead = true; clearInterval(t); };
  }, [isLiveSheet]);

  // 비 라이브 시트: 배치 결과에 저장된 allocation_result를 사용
  const sheetAlloc = !isLiveSheet ? (() => {
    const ar = viewedSheet?.result?.allocation_result;
    if (!ar) return null;
    return { available: true, ...ar };
  })() : null;
  const alloc = isLiveSheet ? liveAlloc : sheetAlloc;

  // 커버리지 영역(중첩보정) — shapely union은 텔레메트리 tick(약 10Hz)마다 돌리기엔 무겁기 때문에,
  // 'network' 서브탭에 처음 들어올 때 한 번만 불러오고 이후엔 수동 새로고침으로만 갱신한다.
  const [coverageKpi, setCoverageKpi] = useState(null);
  const [coverageKpiLoading, setCoverageKpiLoading] = useState(false);
  function fetchCoverageKpi() {
    setCoverageKpiLoading(true);
    fetch('http://127.0.0.1:8001/network-nodes/coverage')
      .then(r => r.json())
      .then(data => { setCoverageKpi(data); setCoverageKpiLoading(false); })
      .catch(() => setCoverageKpiLoading(false));
  }
  useEffect(() => {
    if (mode === 'pro' && subTab === 'network') fetchCoverageKpi();
  }, [mode, subTab]);

  // ── SA 배치 최적화 ──────────────────────────────────────────────────────────
  const [placementConfig, setPlacementConfig] = useState({ n_bs: 3, n_rsu: 4, network_mode: '5G' });
  const [placementRunning, setPlacementRunning] = useState(false);
  const [placementResult, setPlacementResult] = useState(null);
  const [placementError, setPlacementError] = useState(null);

  // 교통 준비 상태 — 최적화 배치는 실측 교통을 수요로 쓰므로, 교통이 만들어지기 전에는
  // 누르지 못하게 막는다(같은 이유의 주석은 tab-simulation.jsx의 최적화 배치 버튼 참조).
  // `calibrated`가 true여야 교통 시나리오가 실제로 존재한다.
  const [demandStatus, setDemandStatus] = useState(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8001/api/demand/status');
        if (!res.ok) return;
        const body = await res.json();
        if (alive) setDemandStatus(body);
      } catch { /* 준비 중 네트워크 오류는 무시 — 다음 틱에 다시 본다 */ }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const placementReady = !!demandStatus?.calibrated;

  async function runPlacementOptimize() {
    setPlacementRunning(true);
    setPlacementError(null);
    try {
      // joint SA(BS+RSU 동시)는 auto-place가 제공한다 — method:'sa'
      const res = await fetch('http://127.0.0.1:8001/network-nodes/auto-place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          n_bs: placementConfig.n_bs,
          n_rsu: placementConfig.n_rsu,
          method: 'sa',
          network_mode: placementConfig.network_mode,
          replace_existing: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '최적화 실패');
      setPlacementResult(data);
      fetchCoverageKpi();
    } catch (e) {
      setPlacementError(e.message);
    } finally {
      setPlacementRunning(false);
    }
  }

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
  const routingMode = networkTelemetry?.selected_algorithms?.route
    || routeEdges?.selected_route_algorithm
    || networkTelemetry?.routing_mode
    || routeEdges?.routing_mode
    || simConfig?.algorithm_selection?.route_algorithm
    || '—';
  const activeSelectionAlgo = networkTelemetry?.selected_algorithms?.base_station_selection
    || simConfig?.algorithm_selection?.base_station_selection_algorithm || '—';
  const activeLatencyAlgoNet = networkTelemetry?.selected_algorithms?.latency
    || simConfig?.algorithm_selection?.latency_algorithm || '—';
  const activeAllocAlgo = alloc?.algorithm_id
    || networkTelemetry?.selected_algorithms?.resource_allocation
    || simConfig?.algorithm_selection?.resource_allocation_algorithm || '—';

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
  if (latencyIsOutage(latency)) currentRisks.push({ tone: 'bad', text: '현재 위치가 커버리지 밖입니다 — 접속이 끊긴 상태로 계산됩니다.' });
  else if (latency !== null && latency >= LAT_WARN_MS) currentRisks.push({ tone: latencyTone(latency), text: `현재 지연시간 ${latency.toFixed(1)}ms — V2X 예산 ${V2X_LATENCY_BUDGET_MS}ms의 ${((latency / V2X_LATENCY_BUDGET_MS) * 100).toFixed(0)}%를 쓰고 있습니다.` });
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
  const [aiForecast, setAiForecast] = useState({ loading: false, sections: [], error: null, provider: null, revealed: 0 });

  const aheadNodeId = vehiclePos?.ahead_node_id
    ?? (currentEdgeId && currentEdgeId.includes('_') ? currentEdgeId.split('_')[1] : null);
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

  async function runAIForecast() {
    setAiForecast({ loading: true, sections: [], error: null, provider: null, revealed: 0 });
    try {
      const payload = {
        hops,
        lookahead_score: lookaheadScore ?? null,
        risk_level: lookaheadRisk ?? null,
        per_hop: perHop ?? [],
        avg_latency_ms: avgLatency ?? null,
        speed_kmh: speed ?? null,
        handover_count: handoverCount ?? 0,
        weak_hops: weakHopCount ?? 0,
        danger_hops: dangerHopCount ?? 0,
        disconnect_risk: disconnectRisk ?? null,
      };
      const res = await fetch('http://127.0.0.1:8001/api/analysis/llm/forecast', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || res.statusText); }
      const data = await res.json();
      const sections = data.sections || [];
      setAiForecast({ loading: false, sections, error: null, provider: data.provider || null, revealed: 0 });
      sections.forEach((_, i) => setTimeout(() => setAiForecast(prev => ({ ...prev, revealed: i + 1 })), i * 380));
    } catch (e) {
      setAiForecast({ loading: false, sections: [], error: e.message || '오류', provider: null, revealed: 0 });
    }
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
          {/* Algorithm badges — Pro 전용 (Lite는 알고리즘 선택 자체를 노출하지 않으므로 뱃지도 의미가 없음) */}
          {mode === 'pro' && (
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
          )}
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
        <Seg value={subTab} onChange={setSubTab} options={
          mode === 'pro'
            ? [{ v: 'summary', label: '현황 요약' }, { v: 'network', label: '네트워크 상세' }, { v: 'forecast', label: '미래 위험' }]
            : [{ v: 'summary', label: '현황 요약' }]
        } />
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

      {/* ── 결과 1줄 해설 — Lite 전용(학부생이 숫자만 보고 좋은 결과인지 직접 판단하기 어려우므로,
           이미 위 배너에 쓰인 동일 latency/handover/disconnect 임계값으로 한 줄 요약을 덧붙인다) ── */}
      {mode === 'lite' && arrived && (() => {
        const lat = avgLatency ?? 0;
        const tone = (latencyTone(lat) === 'bad' || disconnCount > 0) ? 'bad'
                   : (latencyTone(lat) === 'warn' || handoverCount > 3) ? 'warn' : 'good';
        const text = tone === 'good'
          ? `평균 지연 ${lat.toFixed(1)}ms로 안정적인 연결을 유지하며 목적지에 도착했습니다.`
          : tone === 'warn'
          ? `평균 지연 ${lat.toFixed(1)}ms · 핸드오버 ${handoverCount}회 — 일부 구간에서 다소 불안정했지만 정상적으로 도착했습니다.`
          : `평균 지연 ${lat.toFixed(1)}ms · 단절 ${disconnCount}회 — 통신 품질이 좋지 않았던 구간이 있었습니다. 설정 탭에서 기지국 배치를 확인해보세요.`;
        return (
          <div className="row gap8" style={{ marginBottom: 14, padding: '10px 14px', background: 'var(--brand-tint)', borderRadius: 9, fontSize: 12.5, lineHeight: 1.5, alignItems: 'flex-start' }}>
            <Icon.spark size={14} style={{ color: 'var(--brand-2)', flex: '0 0 auto', marginTop: 1 }} />
            <span>{text}</span>
          </div>
        );
      })()}

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
      <div className="grid" style={{ gridTemplateColumns: 'repeat(6, 1fr)', marginBottom: 14 }}>
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
        {/* CBR — 채널 점유율 */}
        <div className="stat" title="채널 점유율 CBR — Gonzalez-Martin et al., IEEE TVT 2019 §IV.A&#10;CBR = 1 − exp(−ρ·R_tx·f_CAM·T_RRI)&#10;임계값 0.65 (ETSI TS 102 687 §5.2.2)">
          <div className="label">CBR <span className="muted" style={{ fontSize: 10 }}>(채널 점유율)</span></div>
          <div className="value num">
            {cbrVal !== null ? (cbrVal * 100).toFixed(1) : '—'}
            {cbrVal !== null && <span className="unit">%</span>}
          </div>
          <div style={{ marginTop: 8 }}>
            {cbrTone && <Chip tone={cbrTone}>{cbrVal > 0.65 ? '혼잡' : cbrVal > 0.45 ? '주의' : '정상'}</Chip>}
            <div className="sub" style={{ marginTop: 4, fontSize: 10 }}>임계 65% [ETSI]</div>
          </div>
        </div>
        {/* PIR P99 */}
        <div className="stat" title="패킷 재수신 간격 P99 (PIR)&#10;PIR_P99 = T_CAM / PRR (기하분포 상한)&#10;기준: ≤ 100 ms (3GPP TR 37.885 Table A.1)&#10;[Eckermann et al., IEEE VTC Fall 2019]">
          <div className="label">PIR P99 <span className="muted" style={{ fontSize: 10 }}>(재수신 간격)</span></div>
          <div className="value num">
            {pirP99 !== null ? pirP99 : '—'}
            {pirP99 !== null && <span className="unit">ms</span>}
          </div>
          <div style={{ marginTop: 8 }}>
            {pirTone && <Chip tone={pirTone}>{pirP99 > 100 ? '기준 초과' : pirP99 > 80 ? '주의' : '준수'}</Chip>}
            <div className="sub" style={{ marginTop: 4, fontSize: 10 }}>기준 100ms [3GPP]</div>
          </div>
        </div>
        {/* 핸드오버 횟수 */}
        <div className="stat" title="5G NR 핸드오버 중단 시간: 200–400 ms (IEEE 5G NR V2X, doc 10320318, 2023)">
          <div className="label">핸드오버 횟수<span style={{ marginLeft: 4 }}><Icon.antenna size={13} style={{ opacity: 0.4 }} /></span></div>
          <div className="value num">{handoverCount}<span className="unit">회</span></div>
          <div className="sub" style={{ marginTop: 4 }}>
            {connNode ? `현재: ${connNode}` : (hasLive ? 'BS 없음' : '—')}
          </div>
          {handoverCount > 0 && (
            <div className="sub" style={{ fontSize: 10, marginTop: 2, opacity: 0.7 }}>
              중단 200–400ms/회 [5G NR]
            </div>
          )}
        </div>
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

      {/* ── Latency breakdown + Info row — Pro 전용(알고리즘 내부 동작 진단용) ── */}
      {mode === 'pro' && <>
      <LatencyBreakdown total={latency} lBase={lBase} lSignal={lSignal} lQueue={lQueue} />
      {(lBase !== null || lSignal !== null || lQueue !== null) && (
        <div style={{ fontSize: 10.5, color: 'var(--ink-4)', marginTop: -6, marginBottom: 10, paddingLeft: 4, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 3 }}>
          <span>E2E 지연 = <i>L</i><sub>tx</sub> + <i>L</i><sub>q</sub> + <i>L</i><sub>comp</sub></span>
          <span style={{ opacity: 0.6 }}>—</span>
          <span style={{ fontStyle: 'italic' }}>Hung et al., IEEE VTM 2017; 3GPP TS 22.186 §5.1</span>
        </div>
      )}
      <InfoRow networkTelemetry={networkTelemetry} routeEdges={routeEdges} />
      </>}

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

          {/* BS 후보 비교 — Pro 전용(기지국 선택 알고리즘 비교 개념) */}
          {mode === 'pro' && (
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
          )}
        </div>
      </div>
      </>}

      {subTab === 'network' && mode === 'pro' && <>
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
                  <div><span className="muted">Route</span> <span className="mono">{algoLabel(routingMode)}</span></div>
                  <div>
                    <span className="muted">BS 선택</span>{' '}
                    {networkTelemetry?.bs_selector_method === 'v4_gnn'
                      ? <span className="chip sm good" style={{ marginLeft: 4 }}>GNN-MAML</span>
                      : <span className="mono">{algoLabel(activeSelectionAlgo)}</span>}
                  </div>
                  <div><span className="muted">Latency</span> <span className="mono">{algoLabel(activeLatencyAlgoNet)}</span></div>
                  <div><span className="muted">Alloc</span> <span className="mono">{algoLabel(activeAllocAlgo)}</span></div>
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
                      <td>
                        {c.type === 'rsu'
                          ? <Chip style={{ background: '#FF8F00', color: '#fff', fontSize: 10 }}>RSU</Chip>
                          : <Chip tone="brand" style={{ fontSize: 10 }}>BS</Chip>}
                      </td>
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

      <div className="grid" style={{ gridTemplateColumns: '1.2fr 1fr', marginBottom: 18 }}>
        {alloc?.available ? (
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
        ) : (
          <Card title="자원배분 현황" en="Resource allocation">
            <div className="muted" style={{ padding: '20px 8px', fontSize: 12, textAlign: 'center' }}>
              {isLiveSheet
                ? '자원배분 데이터를 불러오는 중입니다.'
                : '이 시트의 자원배분 현황은 시뮬레이션 실행 중에만 표시됩니다.'}
            </div>
          </Card>
        )}
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

      {/* ── 커버리지 영역 추정 KPI — Pro 전용. 비용 데이터가 없으므로 ROI/비용 추정은 만들지 않고,
           면적 추정만 보여준다. "합산 상한"(중첩 미보정, π×반경² 합)과 "중첩보정"(shapely union,
           백엔드 /network-nodes/coverage)을 함께 보여줘 둘의 차이(overlap_fraction)까지 드러낸다. ── */}
      {mode === 'pro' && hasLiveNet && (networkTelemetry?.network_nodes?.length > 0) && (() => {
        const nodes = networkTelemetry.network_nodes;
        const fallbackUpperKm2 = nodes.reduce((sum, n) => {
          const r = n.coverage_radius_m || 0;
          return sum + Math.PI * (r / 1000) * (r / 1000);
        }, 0);
        const avgRadius = nodes.reduce((s, n) => s + (n.coverage_radius_m || 0), 0) / nodes.length;
        const kpi = coverageKpi?.available ? coverageKpi : null;
        return (
          <Card title="커버리지 영역 추정" en="Coverage area estimate"
            right={<button className="btn sm" onClick={fetchCoverageKpi} disabled={coverageKpiLoading}>
              {coverageKpiLoading ? <><Icon.reset size={13} className="spin" /> 계산 중…</> : <><Icon.reset size={13} /> 새로고침</>}
            </button>}
            style={{ marginBottom: 18 }}>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
              <Stat label="기지국 수" icon="antenna" value={nodes.length} unit="개" />
              <Stat label="평균 커버리지 반경" icon="layers" value={avgRadius.toFixed(0)} unit="m" />
              <Stat label="합산 면적 (상한, 중첩 미보정)" icon="route" value={(kpi?.upper_bound_km2 ?? fallbackUpperKm2).toFixed(2)} unit="km²" />
              <Stat label="중첩보정 면적" icon="route" value={kpi ? kpi.union_km2.toFixed(2) : '—'} unit={kpi ? 'km²' : ''} accent />
            </div>
            <div className="muted" style={{ fontSize: 10.5, lineHeight: 1.6, marginTop: 12 }}>
              {kpi
                ? `중첩보정 면적은 shapely union 기반으로 기지국 커버리지 원들이 겹치는 부분을 한 번만 센 실제 면적입니다. 상한 대비 ${(kpi.overlap_fraction * 100).toFixed(0)}%가 중첩으로 줄어들었습니다.`
                : '중첩보정 면적을 불러오지 못해 합산 상한값만 표시 중입니다 — "새로고침"을 눌러 다시 시도하세요.'}
            </div>
          </Card>
        );
      })()}

      {/* ── SA 배치 최적화 (v2) ──────────────────────────────────────
          2026-07-27 개편: 첨두↔비첨두 두 벌 비교를 없앴다. 교통 수요가 생성 교통
          한 벌로 통일되면서 두 버킷이라는 개념 자체가 사라졌기 때문(백엔드도 단일 실행).
          대신 BS·RSU를 함께 지정하고(설계 A안), 무작위 배치 대비 이득을 보여준다. */}
      <Card title="기지국·RSU 배치 최적화" en="SA Placement Optimizer" style={{ marginBottom: 18 }}>
        <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6, marginBottom: 14 }}>
          생성 교통의 가장 붐비는 시점을 차량 수요로 삼아, BS(건물 옥상)와 RSU(교차로)를
          <b> 함께</b> 최적화합니다. 건물 차폐(A_seg)와 큐 혼잡을 모두 반영합니다.
          초기화: greedy warm-start + random 병렬 실행 후 최선 선택.
        </div>
        <div className="row gap8" style={{ flexWrap: 'wrap', marginBottom: 14, alignItems: 'center' }}>
          <label style={{ fontSize: 12, color: 'var(--ink-2)' }}>BS</label>
          <input type="number" min={0} max={20} value={placementConfig.n_bs}
            onChange={e => setPlacementConfig(c => ({ ...c, n_bs: Math.max(0, Math.min(20, +e.target.value)) }))}
            style={{ width: 60, padding: '4px 8px', fontSize: 12, border: '1px solid var(--border)', borderRadius: 6 }}
          />
          <label style={{ fontSize: 12, color: 'var(--ink-2)' }}>RSU</label>
          <input type="number" min={0} max={20} value={placementConfig.n_rsu}
            onChange={e => setPlacementConfig(c => ({ ...c, n_rsu: Math.max(0, Math.min(20, +e.target.value)) }))}
            style={{ width: 60, padding: '4px 8px', fontSize: 12, border: '1px solid var(--border)', borderRadius: 6 }}
          />
          <label style={{ fontSize: 12, color: 'var(--ink-2)' }}>기술</label>
          <Seg value={placementConfig.network_mode} onChange={v => setPlacementConfig(c => ({ ...c, network_mode: v }))}
            options={[{ v: '4G', label: '4G' }, { v: '5G', label: '5G' }, { v: '6G', label: '6G' }]} />
          <button className="btn primary sm" onClick={runPlacementOptimize}
            disabled={placementRunning || (placementConfig.n_bs + placementConfig.n_rsu) === 0 || !placementReady}
            style={{ marginLeft: 8 }}>
            {placementRunning
              ? <><Icon.reset size={12} className="spin" /> 최적화 중…</>
              : !placementReady
              ? <><Icon.antenna size={12} /> 교통량 계산 대기 중…</>
              : <><Icon.antenna size={12} /> 최적화 실행</>}
          </button>
        </div>
        {!placementReady && (
          <div className="muted" style={{ fontSize: 11, marginBottom: 10, color: 'var(--warn)' }}>
            {demandStatus?.preparing
              ? `교통량 계산이 끝나야 실행할 수 있습니다${demandStatus?.stage ? ` (${demandStatus.stage} 단계)` : ''} — 실측 교통을 수요로 써야 배치가 의미가 있습니다.`
              : '교통이 아직 준비되지 않았습니다. 구역을 설정하고 교통 준비가 끝날 때까지 기다려 주세요.'}
          </div>
        )}
        {placementError && (
          <div style={{ color: 'var(--bad)', fontSize: 12, marginBottom: 10 }}>{placementError}</div>
        )}
        {placementResult && (() => {
          const o = placementResult.optimization || {};
          const placed = placementResult.placed || [];
          const rows = [
            { label: '무작위 배치 대비 개선', value: o.gain_vs_random_pct != null ? o.gain_vs_random_pct.toFixed(1) + '%' : '—', good: true },
            { label: '최종 평균 지연', value: o.cost_final_ms != null ? o.cost_final_ms.toFixed(2) + ' ms' : '—' },
            { label: '무작위 기준선', value: o.random_baseline_ms != null ? o.random_baseline_ms.toFixed(2) + ' ms' : '—' },
            { label: '음영(outage) 비율', value: o.outage_pct != null ? o.outage_pct.toFixed(1) + '%' : '—' },
            { label: '미커버 수요', value: o.uncovered_pct != null ? o.uncovered_pct.toFixed(1) + '%' : '—' },
            { label: '탐색 후보 (BS / RSU)', value: `${o.n_candidates_bs ?? '—'} / ${o.n_candidates_rsu ?? '—'}` },
            { label: '채점 횟수', value: o.n_evaluations != null ? o.n_evaluations.toLocaleString() : '—' },
          ];
          return (
            <div>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', marginBottom: 12 }}>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.label} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 4px', color: 'var(--ink-2)' }}>{r.label}</td>
                      <td style={{ padding: '6px 4px', textAlign: 'right', fontFamily: 'var(--mono)',
                                   fontWeight: r.good ? 700 : 400, color: r.good ? 'var(--good)' : 'inherit' }}>
                        {r.value}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: 11.5, fontWeight: 700, marginBottom: 6 }}>배치 위치 ({placed.length}개)</div>
              <div style={{ maxHeight: 220, overflowY: 'auto' }}>
                {placed.map((p, i) => (
                  <div key={p.id || i} className="row" style={{ fontSize: 11.5, gap: 8, marginBottom: 4 }}>
                    <span className="chip" style={{ fontSize: 10, padding: '1px 6px' }}>
                      {p.node_type === 'rsu' ? 'RSU' : 'BS'}
                    </span>
                    <span style={{ fontFamily: 'var(--mono)', color: 'var(--ink-2)' }}>
                      {p.lat?.toFixed(5)}, {p.lng?.toFixed(5)}
                    </span>
                    {p.height_m != null && (
                      <span className="muted" style={{ fontSize: 10 }}>h={p.height_m.toFixed(0)}m</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })()}
      </Card>

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

      {subTab === 'forecast' && mode === 'pro' && <>
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
              </div>
              {/* forecast score formula */}
              <div style={{ marginTop: 14, padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 8, border: '1px solid var(--border)', fontSize: 11 }}>
                <div style={{ color: 'var(--brand-2)', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4, marginBottom: 5 }}>
                  <i>S</i><sub>lookahead</sub> =
                  <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', verticalAlign: 'middle', margin: '0 3px', lineHeight: 1.3 }}>
                    <span style={{ borderBottom: '1px solid currentColor', padding: '0 4px 2px', whiteSpace: 'nowrap' }}>Σ<sub><i>k</i>=1</sub><sup><i>H</i></sup> (1/<i>k</i>) · cov(<i>k</i>)</span>
                    <span style={{ padding: '2px 4px 0', whiteSpace: 'nowrap' }}>Σ<sub><i>k</i>=1</sub><sup><i>H</i></sup> (1/<i>k</i>)</span>
                  </span>
                </div>
                <div className="muted" style={{ lineHeight: 1.5 }}>
                  조화급수 감쇠 가중 커버리지 점수. <i>H</i> = look-ahead 홉 수, cov(<i>k</i>) = <i>k</i>홉 커버리지 비율.
                  <br />근거: Sliwa &amp; Wietfeld, <i>IEEE Commun. Mag.</i> 2019; Sutton &amp; Barto, <i>RL: An Introduction</i>, §3.4, 2018
                </div>
              </div>
            </Card>
          </div>

          {/* ── AI forecast prediction ── */}
          <div style={{ marginTop: 18 }}>
            <Card title="AI 연결성 위험 예측" en="AI connectivity forecast"
              right={
                <div className="row gap8">
                  <Chip tone="brand"><Icon.spark size={11} /> AI 예측</Chip>
                  <button className="btn sm primary" onClick={runAIForecast} disabled={aiForecast.loading || !activeLookahead}>
                    {aiForecast.loading
                      ? <><span className="spin" style={{ display: 'inline-block', width: 10, height: 10, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', marginRight: 5 }} />분석 중…</>
                      : <><Icon.spark size={13} /> AI 위험 예측</>}
                  </button>
                </div>
              }>
              {aiForecast.sections.length === 0 && !aiForecast.loading && (
                <div style={{ textAlign: 'center', padding: '20px 16px' }}>
                  <div style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--brand-tint)', display: 'grid', placeItems: 'center', margin: '0 auto 10px', color: 'var(--brand-2)' }}><Icon.spark size={20} /></div>
                  <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 3 }}>AI 미래 연결성 예측</div>
                  <div className="muted" style={{ fontSize: 11.5, marginBottom: 12 }}>
                    look-ahead {hops}홉 데이터를 분석해 단절 위험·권고 조치를 예측합니다.
                    {!activeLookahead && <span style={{ color: 'var(--warn)' }}> (시뮬레이션 실행 후 활성화)</span>}
                  </div>
                  {aiForecast.error && (
                    <div style={{ fontSize: 11.5, color: 'var(--bad)', background: 'var(--bad-tint)', padding: '7px 10px', borderRadius: 8, marginBottom: 10, textAlign: 'left' }}>{aiForecast.error}</div>
                  )}
                </div>
              )}
              {aiForecast.loading && (
                <div style={{ textAlign: 'center', padding: '28px 16px' }}>
                  <div className="spin" style={{ width: 26, height: 26, border: '3px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%', margin: '0 auto 10px' }} />
                  <div className="muted" style={{ fontSize: 12 }}>AI가 미래 위험을 분석 중입니다…</div>
                </div>
              )}
              {aiForecast.revealed > 0 && (
                <div className="col gap8">
                  {aiForecast.sections.slice(0, aiForecast.revealed).map((t, i) => (
                    <div key={i} className="fade row gap10" style={{ padding: '10px 12px', background: 'var(--surface-2)', borderRadius: 8, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                      <span className="num" style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--brand-2)', flexShrink: 0, marginTop: 2 }}>{String(i + 1).padStart(2, '0')}</span>
                      <span style={{ fontSize: 12.5, lineHeight: 1.55 }}>{t}</span>
                    </div>
                  ))}
                  {aiForecast.revealed === aiForecast.sections.length && aiForecast.sections.length > 0 && (
                    <button className="btn sm" onClick={runAIForecast} style={{ alignSelf: 'flex-start' }}><Icon.reset size={12} /> 다시 예측</button>
                  )}
                </div>
              )}
            </Card>
          </div>
        </>
      )}
      </>}
    </div>
  );
}
window.Dashboard = Dashboard;
