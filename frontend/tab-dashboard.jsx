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
  full_composite_latency:    'Full Composite',
  simple_distance_latency:   'Simple Distance',
  traffic_aware_allocation:  'Traffic Aware',
  equal_allocation:          'Equal Alloc',
  proportional_allocation:   'Proportional',
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

/* ---- Latency breakdown bar --------------------------------- */
function LatencyBreakdown({ total, buildingMs, densityMs }) {
  if (total === null || buildingMs === null || densityMs === null) return null;
  const base = Math.max(0, total - buildingMs - densityMs);
  const items = [
    { label: '기본 지연',   value: base,       color: 'var(--brand-2)' },
    { label: '빌딩 간섭',   value: buildingMs, color: 'var(--warn)'    },
    { label: '밀도 페널티', value: densityMs,  color: 'var(--bad)'     },
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
function Dashboard({ sim, go, vehiclePos, networkTelemetry, simHistory, simLogs, simConfig, routeEdges }) {
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
  const buildingPenMs  = networkTelemetry?.latency_penalty_ms ?? null;
  const densityPenMs   = networkTelemetry?.vehicle_density_penalty_ms ?? null;

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
      <LatencyBreakdown total={latency} buildingMs={buildingPenMs} densityMs={densityPenMs} />

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
    </div>
  );
}
window.Dashboard = Dashboard;
