/* ============================================================ Report tab
   5-section analysis report: Overview / Compare / Explain / Export / Metadata
   ============================================================ */

// ── constants ──────────────────────────────────────────────────────────────
const API_BASE = 'http://127.0.0.1:8001';

const LOG_STYLE = {
  info:       { tone: 'brand', ko: '정보',    ic: 'spark'    },
  handover:   { tone: 'brand', ko: '핸드오버', ic: 'antenna'  },
  warn:       { tone: 'warn',  ko: '경고',    ic: 'warn'     },
  risk:       { tone: 'bad',   ko: '위험',    ic: 'warn'     },
  done:       { tone: 'good',  ko: '완료',    ic: 'check'    },
  reroute:    { tone: 'brand', ko: '재경로',  ic: 'route'    },
  sys:        { tone: '',      ko: '시스템',  ic: 'sliders'  },
  disconnect: { tone: 'bad',   ko: '단절',    ic: 'warn'     },
};

const PROVIDER_LABELS = {
  vertex:  { name: 'Gemini',  color: 'var(--brand-2)' },
  azure:   { name: 'GPT-5.4', color: '#0078d4'        },
  bedrock: { name: 'Claude',  color: 'var(--brand-2)' },
};

const ALT_PATH_COLORS = ['#F6A623', '#A855F7', '#22C1A8', '#E45C8A', '#5B8DEF'];

const CMP_HISTORY_KEY  = 'v2x_run_history';
const SCB_BATCH_KEY    = 'v2x_scenario_batches';

function loadRunHistory()    { try { return JSON.parse(localStorage.getItem(CMP_HISTORY_KEY)  || '[]'); } catch { return []; } }
function saveRunHistory(l)   { try { localStorage.setItem(CMP_HISTORY_KEY,  JSON.stringify(l.slice(-20))); } catch {} }
function loadScenarioBatches() { try { return JSON.parse(localStorage.getItem(SCB_BATCH_KEY) || '[]'); } catch { return []; } }
function saveScenarioBatches(l){ try { localStorage.setItem(SCB_BATCH_KEY, JSON.stringify(l));            } catch {} }

// P-ile (nearest-rank, no interpolation)
function pct(sortedNums, p) {
  if (!sortedNums?.length) return null;
  return sortedNums[Math.min(sortedNums.length - 1, Math.max(0, Math.ceil(p / 100 * sortedNums.length) - 1))];
}

function algoLabel(key) {
  if (!key) return '—';
  // Handle k_path_rank_N for any rank N ≥ 0
  const km = /^k_path_rank_(\d+)$/.exec(key);
  if (km) {
    const r = parseInt(km[1], 10);
    return r === 0 ? 'K-경로 최적' : `K-경로 ${r + 1}위`;
  }
  const MAP = {
    tech_latency_v31: '기술모델 v3.1', rsrp_max: 'RSRP 최대',
    dijkstra: 'Dijkstra', astar: 'A*', baseline_dijkstra: '기본 Dijkstra',
    network_aware: '네트워크 가중치', network_weighted: '네트워크 가중치',
    lowest_latency_bs: 'Lowest Latency', highest_confidence_bs: 'Highest Confidence',
    load_balanced_bs: 'Load Balanced', nearest_bs: 'Nearest BS',
    full_composite_latency: 'Full Composite', simple_distance_latency: 'Simple Distance',
    distance_based_latency: 'Distance Based', load_aware_latency: 'Load Aware',
    blockage_aware_latency: 'Blockage Aware', mec_aware_latency: 'MEC Aware',
    traffic_aware_allocation: 'Traffic Aware', equal_allocation: 'Equal Alloc',
    proportional_allocation: 'Proportional', load_balancing_allocation: 'Load Balancing',
    latency_minimizing_allocation: 'Latency Minimizing', priority_based_allocation: 'Priority Based',
    lookahead_resource_allocation: 'Look-ahead',
    // GNN-MAML RL
    rl_routing: 'GNN-MAML 경로', rl_based_bs_selection: 'GNN-MAML BS',
    v4_gnn: 'GNN-MAML', rl_bs_placement: 'GNN-MAML 배치',
  };
  return MAP[key] ?? key;
}

function inferBatchKind(batch) {
  const label = batch.label || '';
  if (label.startsWith('파라미터 스윕')) return { tone: 'brand', text: '파라미터 스윕' };
  if (label.startsWith('RL 정책 비교'))  return { tone: 'good',  text: 'GNN-MAML 비교' };
  if (label.startsWith('시뮬레이션 시트 비교')) return { tone: 'warn', text: '시트 비교' };
  if ((batch.results || []).some(r => r.mode === 'rl_episode')) return { tone: 'good', text: 'RL 배치' };
  return { tone: '', text: '시나리오 배치' };
}

function parseSweepBatch(batch) {
  const m = /^파라미터 스윕 — (.+)$/.exec(batch.label || '');
  if (!m) return null;
  const paramLabel = m[1];
  const points = (batch.results || [])
    .filter(r => r.status === 'done' && r.mode === 'route_metrics')
    .map(r => { const vm = /=(-?[\d.]+)$/.exec(r.label || ''); return { x: vm ? parseFloat(vm[1]) : null, totalCost: r.route_cost_result?.total_cost ?? null, avgLatency: r.route_cost_result?.avg_latency_ms ?? null }; })
    .filter(p => p.x !== null).sort((a, b) => a.x - b.x);
  return points.length > 0 ? { paramLabel, points } : null;
}

// ── math rendering helpers ─────────────────────────────────────────────────

function MathFrac({ n, d }) {
  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', verticalAlign: 'middle', margin: '0 3px', lineHeight: 1.3 }}>
      <span style={{ borderBottom: '1px solid currentColor', padding: '0 5px 2px', textAlign: 'center', whiteSpace: 'nowrap' }}>{n}</span>
      <span style={{ padding: '2px 5px 0', textAlign: 'center', whiteSpace: 'nowrap' }}>{d}</span>
    </span>
  );
}

// ── shared helpers ─────────────────────────────────────────────────────────

function fmt1(v)      { return v != null ? Number(v).toFixed(1) : '—'; }
function fmt2(v)      { return v != null ? Number(v).toFixed(2) : '—'; }
function fmtPct(v)    { return v != null ? (Number(v) * 100).toFixed(1) + '%' : '—'; }
function fmtMs(v)     { return v != null ? Number(v).toFixed(1) + ' ms' : '—'; }
function fmtKm(m)     { return m != null ? (Number(m) / 1000).toFixed(2) + ' km' : '—'; }

function DeltaBadge({ value, unit = '', lowerBetter = true, digits = 1 }) {
  if (value == null) return null;
  const v = Number(value);
  const good   = lowerBetter ? v < 0 : v > 0;
  const neutral = Math.abs(v) < 1e-6;
  const color   = neutral ? 'var(--ink-3)' : good ? 'var(--good)' : 'var(--bad)';
  return (
    <span style={{ fontWeight: 700, color, fontSize: 11.5 }}>
      {v > 0 ? '+' : ''}{v.toFixed(digits)}{unit}
    </span>
  );
}

function SectionEmpty({ msg }) {
  return (
    <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--ink-4)', fontSize: 12.5 }}>
      {msg || '시뮬레이션을 먼저 실행하세요.'}
    </div>
  );
}

function FindingRow({ tone, icon, label, detail, badge }) {
  return (
    <div className="row gap10" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)', fontSize: 12 }}>
      {icon && <Icon.warn size={13} style={{ color: tone === 'bad' ? 'var(--bad)' : tone === 'warn' ? 'var(--warn)' : 'var(--ink-3)', flexShrink: 0 }} />}
      <span style={{ flex: 1 }}>{label}</span>
      {detail && <span className="muted" style={{ fontSize: 11 }}>{detail}</span>}
      {badge && badge}
    </div>
  );
}

// ── Overview section ───────────────────────────────────────────────────────

function SectionOverview({ bundle, simLogs, vehiclePos, networkTelemetry, sim }) {
  const rs   = bundle?.run_summary;
  const summ = bundle?.simulation_summary;
  const seed = summ?.recommendation_text_seed;

  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name ?? '—';
  const liveLat  = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms ?? null;
  const hasLive  = simLogs?.length > 0;

  const imp = summ?.improvement_over_baseline;

  if (!bundle?.available) {
    return (
      <Card title="개요" en="Overview" style={{ marginBottom: 18 }}>
        <SectionEmpty msg="시뮬레이션을 실행하면 KPI 요약이 표시됩니다." />
      </Card>
    );
  }

  return (
    <>
      {/* ── live stats strip (when simulation is running) ── */}
      {hasLive && vehiclePos && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 16 }}>
          <Stat label="경과 시간"  icon="clock"   value={fmtClock(sim?.elapsed ?? 0)} sub="시뮬레이션 시작 후" accent />
          <Stat label="현재 속도"  icon="speed"   value={fmt1(vehiclePos.speed ?? 0)} unit="km/h" sub="EGO-001" />
          <Stat label="연결 기지국" icon="antenna" value={connNode} sub={liveLat != null ? `Latency ${fmt1(liveLat)} ms` : '—'} />
          <Stat label="이벤트 수"  icon="chart"   value={simLogs.length} unit="건" sub={`핸드오버 ${simLogs.filter(l => l.kind === 'handover').length}건`} />
        </div>
      )}

      {/* ── primary finding banner ── */}
      {seed?.primary_finding && (
        <div style={{ padding: '12px 16px', background: 'var(--brand-tint)', borderRadius: 10, marginBottom: 16, fontSize: 13, lineHeight: 1.55, border: '1px solid var(--brand-tint2)' }}>
          <b style={{ marginRight: 8, color: 'var(--brand-2)' }}>핵심 발견</b>{seed.primary_finding}
        </div>
      )}

      {/* ── KPI grid ── */}
      <Card title="핵심 지표" en="Key metrics" style={{ marginBottom: 16 }}>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
          <Stat label="총 비용"      icon="chart"  value={fmt2(rs?.total_cost)}        accent />
          <Stat label="평균 지연"    icon="speed"  value={fmtMs(rs?.avg_latency_ms)}   sub={`최대 ${fmtMs(rs?.max_latency_ms)}`} />
          <Stat label="핸드오버"     icon="route"  value={rs?.handover_count ?? '—'}  unit="회" />
          <Stat label="PRR (근사)"   icon="check"  value={rs?.prr_approx != null ? (rs.prr_approx * 100).toFixed(1) + '%' : '—'} sub="시간가중 연결유지율" />
        </div>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginTop: 12 }}>
          <Stat label="이동 거리"    icon="route"  value={fmtKm(rs?.total_distance_m)}  sub={rs?.total_travel_time_s != null ? `${fmt1(rs.total_travel_time_s)} s` : ''} />
          <Stat label="미커버 비율" icon="warn"   value={rs?.coverage_risk != null ? (rs.coverage_risk * 100).toFixed(1) + '%' : '—'} sub="단절 위험" />
          <Stat label="커버리지"     icon="check"  value={rs?.covered_pct != null ? (rs.covered_pct * 100).toFixed(1) + '%' : '—'} />
          <Stat label="BS 부하 결손" icon="sliders" value={rs?.resource_deficit_cost != null ? fmt2(rs.resource_deficit_cost) : '—'} />
        </div>
        {(rs?.hit_total_ms != null || rs?.cbr_avg != null || rs?.urllc_compliance_ratio != null) && (
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginTop: 12 }}>
            <Stat label="HIT 합산" icon="route"
              value={rs?.hit_total_ms != null ? rs.hit_total_ms + ' ms' : '—'}
              sub="핸드오버 중단 시간 (IEEE 2023)" />
            <Stat label="CBR 평균" icon="speed"
              value={rs?.cbr_avg != null ? (rs.cbr_avg * 100).toFixed(1) + '%' : '—'}
              tone={rs?.cbr_avg > 0.65 ? 'bad' : 'good'}
              sub="채널 점유율 (임계 65%)" />
            <Stat label="URLLC 준수율" icon="check"
              value={rs?.urllc_compliance_ratio != null ? (rs.urllc_compliance_ratio * 100).toFixed(1) + '%' : '—'}
              sub="P(L ≤ 10 ms) · 3GPP TS 22.261" />
            <Stat label="PIR P99" icon="speed"
              value={rs?.pir_p99_ms != null ? fmtMs(rs.pir_p99_ms) : '—'}
              tone={rs?.pir_compliant === false ? 'bad' : 'good'}
              sub="3GPP TR 37.885 (≤100 ms)" />
          </div>
        )}
      </Card>

      {/* ── algorithm identity + improvement ── */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <Card title="알고리즘" en="Algorithm">
          <div className="col gap8" style={{ fontSize: 12.5 }}>
            <div className="row between">
              <span className="muted">선택 알고리즘</span>
              <span style={{ fontWeight: 700 }}>{algoLabel(rs?.selected_algorithm)}</span>
            </div>
            <div className="row between">
              <span className="muted">기준 알고리즘</span>
              <span>{algoLabel(rs?.baseline_algorithm)}</span>
            </div>
            <div className="row between">
              <span className="muted">네트워크 모드</span>
              <Chip tone="brand">{rs?.network_mode ?? '—'}</Chip>
            </div>
            <div className="row between">
              <span className="muted">시뮬레이션 모드</span>
              <Chip>{rs?.sim_mode ?? '—'}</Chip>
            </div>
          </div>
        </Card>

        <Card title="기준 대비 개선" en="vs Baseline">
          {!imp ? (
            <div className="muted" style={{ fontSize: 12, padding: '8px 0' }}>알고리즘 비교 데이터 없음</div>
          ) : (
            <div className="col gap8" style={{ fontSize: 12.5 }}>
              <div className="row between">
                <span className="muted">비용 개선</span>
                <DeltaBadge value={imp.cost_improvement_pct} unit="%" lowerBetter={false} digits={1} />
              </div>
              <div className="row between">
                <span className="muted">지연 변화</span>
                <DeltaBadge value={imp.latency_delta_ms} unit=" ms" lowerBetter={true} digits={1} />
              </div>
              <div className="row between">
                <span className="muted">거리 변화</span>
                <DeltaBadge value={imp.distance_delta_m} unit=" m" lowerBetter={true} digits={0} />
              </div>
              <div className="row between">
                <span className="muted">핸드오버 변화</span>
                <DeltaBadge value={imp.handover_delta} unit="회" lowerBetter={true} digits={0} />
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* ── Chart 6: improvement-over-baseline delta bars ── */}
      {imp && imp.cost_improvement_pct != null && (
        <Card title="선택 알고리즘 vs 기준선 — 변화 비교" en="Δ vs baseline" style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
            선택 알고리즘 성능에서 기준 알고리즘 성능을 뺀 차이값. 초록 막대 = 개선, 빨간 막대 = 악화.
            기준 알고리즘: <b>{algoLabel(rs?.baseline_algorithm)}</b>
          </div>
          <DeltaBarChart deltas={[
            { label: '비용 개선%',  value: imp.cost_improvement_pct ?? 0,         unit: '%',  lowerBetter: false, digits: 1 },
            { label: '지연 변화',   value: imp.latency_delta_ms ?? 0,              unit: ' ms', lowerBetter: true,  digits: 1 },
            { label: '거리 변화',   value: (imp.distance_delta_m ?? 0) / 1000,     unit: ' km', lowerBetter: true,  digits: 2 },
            { label: '핸드오버',    value: imp.handover_delta ?? 0,                unit: '회',  lowerBetter: true,  digits: 0 },
          ]} />
        </Card>
      )}

      {/* ── trade-offs + risk factors ── */}
      {seed && (seed.trade_offs?.length > 0 || seed.risk_factors?.length > 0 || seed.improvement_highlights?.length > 0) && (
        <Card title="주요 발견 사항" en="Key findings" style={{ marginBottom: 16 }}>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
            {seed.improvement_highlights?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 11.5, marginBottom: 6, color: 'var(--good)' }}>개선 사항</div>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, lineHeight: 1.7, listStyle: 'disc' }}>
                  {seed.improvement_highlights.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              </div>
            )}
            {seed.trade_offs?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 11.5, marginBottom: 6 }}>트레이드오프</div>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, lineHeight: 1.7, listStyle: 'disc' }}>
                  {seed.trade_offs.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              </div>
            )}
            {seed.risk_factors?.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 11.5, marginBottom: 6, color: 'var(--bad)' }}>위험 요인</div>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, lineHeight: 1.7, listStyle: 'disc', color: 'var(--bad)' }}>
                  {seed.risk_factors.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* ── compact experiment setup (replaces Metadata tab) ── */}
      {bundle?.scenario_metadata && (() => {
        const meta = bundle.scenario_metadata;
        const cfg  = summ?.used_config;
        const originStr = meta.origin_lat != null ? `${Number(meta.origin_lat).toFixed(5)}, ${Number(meta.origin_lng).toFixed(5)}` : null;
        const destStr   = meta.dest_lat   != null ? `${Number(meta.dest_lat).toFixed(5)}, ${Number(meta.dest_lng).toFixed(5)}`   : null;
        return (
          <details style={{ marginBottom: 16 }}>
            <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: 12.5, padding: '10px 0', color: 'var(--ink-2)', listStyle: 'none', display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}>
              <svg width="12" height="12" viewBox="0 0 12 12" style={{ flex: '0 0 auto', transition: 'transform 0.15s' }} className="details-chevron">
                <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              실험 구성 <span className="muted" style={{ fontWeight: 400, fontSize: 11 }}>Run Setup</span>
            </summary>
            <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
              <Card title="런 식별자" en="Run identity" style={{ marginBottom: 0 }}>
                <div className="col gap4" style={{ fontSize: 12 }}>
                  {[
                    ['런 ID',     meta.run_id || rs?.run_id,               true],
                    ['시나리오 ID', meta.scenario_id || summ?.scenario_id, true],
                    ['생성 시각',  summ?.generated_at ? new Date(summ.generated_at).toLocaleString('ko-KR') : null, false],
                    ['시드(seed)', meta.seed ?? null,                       true],
                    ['차량 수',   meta.vehicle_count != null ? `${meta.vehicle_count}대` : null, false],
                    ['출발 좌표', originStr,                                 true],
                    ['도착 좌표', destStr,                                   true],
                  ].filter(([, v]) => v != null).map(([label, value, mono]) => (
                    <div key={label} className="row between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
                      <span className="muted" style={{ fontSize: 11 }}>{label}</span>
                      <span style={{ fontFamily: mono ? 'var(--mono)' : undefined, fontSize: 11, fontWeight: 500 }}>{value}</span>
                    </div>
                  ))}
                </div>
              </Card>
              <Card title="알고리즘 설정" en="Algorithm config" style={{ marginBottom: 0 }}>
                <div className="col gap4" style={{ fontSize: 12 }}>
                  {[
                    ['네트워크 모드',      meta.network_mode],
                    ['교통 시간대',        meta.traffic_time_period === 'peak' ? '첨두시 (peak)' : meta.traffic_time_period === 'off_peak' ? '비첨두시' : meta.traffic_time_period],
                    ['경로 알고리즘',      algoLabel(meta.route_algorithm)],
                    ['지연 알고리즘',      algoLabel(meta.latency_algorithm)],
                    ['BS 선택 알고리즘',   algoLabel(meta.bs_selection_algorithm)],
                    ['자원 할당 알고리즘', algoLabel(meta.resource_allocation_algorithm || meta.allocation_algorithm)],
                  ].filter(([, v]) => v && v !== '—').map(([label, value]) => (
                    <div key={label} className="row between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
                      <span className="muted" style={{ fontSize: 11 }}>{label}</span>
                      <span style={{ fontSize: 11, fontWeight: 500 }}>{value}</span>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          </details>
        );
      })()}
    </>
  );
}

// ── Compare section ────────────────────────────────────────────────────────

const CMP_COLS = [
  { key: 'total_cost',                          label: '총 비용',      fmt: v => Number(v).toFixed(2),                  lowerBetter: true  },
  { key: 'average_latency_ms',                  label: '평균 지연',    fmt: v => Number(v).toFixed(1) + 'ms',           lowerBetter: true  },
  { key: 'max_latency_ms',                      label: '최대 지연',    fmt: v => Number(v).toFixed(1) + 'ms',           lowerBetter: true  },
  { key: 'handover_count',                      label: '핸드오버',     fmt: v => v + '회',                              lowerBetter: true  },
  { key: 'disconnection_ratio',                 label: '단절율',       fmt: v => (Number(v) * 100).toFixed(0) + '%',    lowerBetter: true  },
  { key: 'prr_approx',                          label: 'PRR(근사)',    fmt: v => (Number(v) * 100).toFixed(1) + '%',    lowerBetter: false },
  { key: 'average_bs_load',                     label: 'BS 부하',     fmt: v => (Number(v) * 100).toFixed(0) + '%',    lowerBetter: true  },
  { key: 'future_connectivity_risk',            label: '미래 위험',    fmt: v => (Number(v) * 100).toFixed(0) + '%',    lowerBetter: true  },
  { key: 'resource_deficit_ratio',              label: '자원 결손',    fmt: v => (Number(v) * 100).toFixed(0) + '%',    lowerBetter: true  },
  { key: 'edge_count',                          label: '구간 수',      fmt: v => v + '개',                              lowerBetter: null  },
];

function SectionCompare({ bundle, routeCoords, routeEdges, networkTelemetry, vehiclePos, simHistory, simConfig, mode }) {
  const [cmp,        setCmp]        = useState(null);
  const [cmpLoading, setCmpLoading] = useState(false);
  const [kCandidates, setKCandidates] = useState(null);
  const [visibleRanks, setVisibleRanks] = useState({});
  const [history,    setHistory]    = useState(() => loadRunHistory());
  const [checkedRuns, setCheckedRuns] = useState([]);
  const [savedFlash, setSavedFlash] = useState(false);
  const [routeEval,  setRouteEval]  = useState(null);
  const [showAllCols, setShowAllCols] = useState(false);
  const cmpPollRef = useRef(null);

  const hasRoute = routeCoords?.length >= 2;

  // fetch k-path candidates once route is ready
  useEffect(() => {
    if (!hasRoute) return;
    let stopped = false, tries = 0;
    const tryFetch = () => {
      fetch(`${API_BASE}/api/route/candidates`)
        .then(r => r.json())
        .then(data => {
          if (stopped) return;
          if (data?.available) {
            setKCandidates(data);
            setVisibleRanks(Object.fromEntries((data.candidates || []).map(c => [c.rank, true])));
          } else if (tries++ < 8) setTimeout(tryFetch, 2000);
        }).catch(() => {});
    };
    tryFetch();
    return () => { stopped = true; };
  }, [hasRoute, routeCoords]);

  useEffect(() => {
    fetch(`${API_BASE}/api/route/evaluate`).then(r => r.json()).then(d => setRouteEval(d?.available ? d : null)).catch(() => {});
    pollCmp();
    return () => { if (cmpPollRef.current) clearInterval(cmpPollRef.current); };
  }, []);

  function pollCmp() {
    fetch(`${API_BASE}/api/route/compare-algorithms`).then(r => r.json()).then(data => {
      setCmp(data);
      if (data.status !== 'running' && cmpPollRef.current) { clearInterval(cmpPollRef.current); cmpPollRef.current = null; }
    }).catch(() => {});
  }
  function runComparison() {
    fetch(`${API_BASE}/api/route/compare-algorithms`, { method: 'POST' })
      .then(() => { setCmp({ status: 'running' }); if (cmpPollRef.current) clearInterval(cmpPollRef.current); cmpPollRef.current = setInterval(pollCmp, 2000); })
      .catch(() => {});
  }

  function streetNamesFor(algo) {
    const m = /^k_path_rank_(\d+)$/.exec(algo || '');
    if (m) return kCandidates?.candidates?.[parseInt(m[1], 10)]?.street_names || null;
    return routeEval?.street_names || null;
  }
  function saveCurrentRun() {
    if (!bundle?.algorithm_compare?.length) return;
    const entry = { timestamp: new Date().toISOString(), config: simConfig ?? null, algorithms: Object.fromEntries((bundle.algorithm_compare || []).map(r => [r.algorithm, r])) };
    const next = [...history, entry];
    setHistory(next); saveRunHistory(next); setSavedFlash(true); setTimeout(() => setSavedFlash(false), 1800);
  }
  function removeRun(idx) { const next = history.filter((_, i) => i !== idx); setHistory(next); saveRunHistory(next); setCheckedRuns(checkedRuns.filter(i => i !== idx)); }

  const algoRows   = bundle?.algorithm_compare || [];
  const comparison = bundle?.simulation_summary?.metric_summary?.comparison;
  const bestPer    = comparison?.best_per_metric || {};
  const summRank   = comparison?.summary_rank    || {};
  const rankItems  = Object.entries(summRank).sort((a, b) => a[1] - b[1]).map(([a, s]) => ({ label: algoLabel(a), value: s, display: s.toFixed(2) }));

  const kList = kCandidates?.candidates ?? [];
  const altPaths = kList.filter(c => visibleRanks[c.rank] && c.per_edge?.length >= 2).map(c => ({
    path: c.per_edge.map(e => [e.midpoint_lat, e.midpoint_lng]),
    color: ALT_PATH_COLORS[c.rank % ALT_PATH_COLORS.length], rank: c.rank,
  }));
  const bsPoints  = (networkTelemetry?.candidate_nodes ?? []).filter(c => c.lat != null).map(c => ({ lat: c.lat, lng: c.lng }));
  const perEdge   = routeEdges?.per_edge ?? [];
  const edgeNames = networkTelemetry?.route_edge_names ?? routeEdges?.edge_names ?? {};
  const curEdgeId = vehiclePos?.current_edge_id ?? null;

  const checkedSelected = checkedRuns.map(i => history[i]).filter(Boolean);

  // latency percentiles
  const liveSamples = (simHistory || []).map(h => h.latency).filter(v => v != null);
  const useLive = liveSamples.length >= 10;
  const pSamples = useLive ? liveSamples : perEdge.map(e => e.latency_ms || 0);
  const pSorted  = [...pSamples].sort((a, b) => a - b);

  return (
    <>
      {/* ── K-path map ── */}
      {hasRoute ? (
        <Card title="실시간 경로" en="Route map" right={<Chip tone="good" dot>실시간</Chip>} style={{ marginBottom: 16 }}>
          <MiniMap path={routeCoords} color="var(--brand-2)" bs={bsPoints} label="live" height={200} extraPaths={altPaths} />
          {kList.length > 0 && (
            <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
              <div className="muted" style={{ fontSize: 10.5, marginBottom: 6 }}>경로 대안(K-path) — 체크 해제 시 지도에서 숨김</div>
              <div className="col gap5">
                {kList.map(c => {
                  const names = c.street_names || [];
                  const shown = names.length > 3 ? [...names.slice(0, 2), '…', names[names.length - 1]] : names;
                  return (
                    <label key={c.rank} className="row gap8" style={{ fontSize: 10.5, cursor: 'pointer', alignItems: 'center' }}>
                      <input type="checkbox" checked={!!visibleRanks[c.rank]} onChange={() => setVisibleRanks(p => ({ ...p, [c.rank]: !p[c.rank] }))} />
                      <span style={{ width: 14, height: 3, background: ALT_PATH_COLORS[c.rank % ALT_PATH_COLORS.length], borderRadius: 2, flexShrink: 0 }} />
                      <span style={{ fontWeight: 600 }}>대안 {c.rank + 1}</span>
                      <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{shown.join(' → ')}</span>
                      <span className="num muted" style={{ marginLeft: 'auto', flexShrink: 0 }}>{c.avg_latency_ms?.toFixed(1)} ms</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </Card>
      ) : (
        <Card title="실시간 경로" en="Route map" style={{ marginBottom: 16 }}>
          <SectionEmpty msg="시뮬레이션 탭에서 경로를 설정하면 지도가 표시됩니다." />
        </Card>
      )}

      {/* ── algorithm comparison table ── */}
      {(() => {
        const ESSENTIAL_KEYS = ['total_cost', 'average_latency_ms', 'handover_count', 'prr_approx', 'average_bs_load', 'future_connectivity_risk'];
        const visibleCols = showAllCols ? CMP_COLS : CMP_COLS.filter(c => ESSENTIAL_KEYS.includes(c.key));
        return (
          <Card title="알고리즘 비교" en="Algorithm comparison" style={{ marginBottom: 16 }}
            right={
              <div className="row gap8">
                <Chip>{algoRows.length}개 알고리즘</Chip>
                <button className="btn sm" onClick={() => setShowAllCols(v => !v)} style={{ fontSize: 10, padding: '2px 8px' }}>
                  {showAllCols ? '간략 보기' : '전체 열 보기'}
                </button>
              </div>
            }>
            {algoRows.length === 0 ? (
              <SectionEmpty msg="시뮬레이션 실행 후 알고리즘 비교 데이터가 표시됩니다." />
            ) : (
              <>
                {rankItems.length > 0 && (
                  <div style={{ marginBottom: 14 }}>
                    <div className="muted" style={{ fontSize: 11, marginBottom: 7 }}>종합 순위 (낮을수록 우수)</div>
                    <BarChart items={rankItems} />
                  </div>
                )}
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>알고리즘</th>
                        <th>경유 도로</th>
                        {visibleCols.map(c => <th key={c.key} className="r">{c.label}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {algoRows.map(row => {
                        const names = streetNamesFor(row.algorithm);
                        const shown = names?.length > 4 ? [...names.slice(0, 3), '…', names[names.length - 1]] : names;
                        return (
                          <tr key={row.algorithm}>
                            <td><span className="mono" style={{ fontWeight: 600 }}>{algoLabel(row.algorithm)}</span></td>
                            <td style={{ maxWidth: 220 }}>
                              {shown
                                ? <div className="row gap5 wrap">{shown.map((nm, i) => nm === '…' ? <span key={i} className="muted" style={{ fontSize: 11 }}>…</span> : <Chip key={i} style={{ fontSize: 10 }}>{nm}</Chip>)}</div>
                                : <span className="muted">—</span>}
                            </td>
                            {visibleCols.map(c => {
                              const v = row[c.key];
                              const isBest = bestPer[c.key] === row.algorithm;
                              return (
                                <td key={c.key} className="r">
                                  <span className="num" style={{ fontWeight: isBest ? 700 : 400, color: isBest ? 'var(--good)' : 'inherit' }}>
                                    {v != null ? c.fmt(v) : '—'}
                                  </span>
                                  {isBest && <Chip tone="good" style={{ marginLeft: 5, fontSize: 9 }}>최적</Chip>}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="row gap8" style={{ marginTop: 12 }}>
                  <button className={'btn sm ' + (savedFlash ? 'good' : '')} onClick={saveCurrentRun}>
                    {savedFlash ? <><Icon.check size={13} /> 저장됨</> : <><Icon.download size={13} /> 현재 결과 히스토리 저장</>}
                  </button>
                </div>
              </>
            )}
          </Card>
        );
      })()}

      {/* ── Chart 1: KPI radar — multi-algorithm multi-metric ── */}
      {algoRows.length >= 2 && (() => {
        const RADAR_AXES = [
          { key: 'total_cost',               label: '비용',     lowerBetter: true  },
          { key: 'average_latency_ms',        label: '지연(ms)', lowerBetter: true  },
          { key: 'prr_approx',                label: 'PRR',      lowerBetter: false },
          { key: 'handover_count',            label: '핸드오버', lowerBetter: true  },
          { key: 'future_connectivity_risk',  label: '미래위험', lowerBetter: true  },
          { key: 'average_bs_load',           label: 'BS부하',   lowerBetter: true  },
        ];
        // normalise each axis to [0,1] — higher on chart = better
        const normVals = {};
        RADAR_AXES.forEach(ax => {
          const vals = algoRows.map(r => Number(r[ax.key] ?? 0));
          const mn = Math.min(...vals), mx = Math.max(...vals), sp = mx - mn || 1;
          algoRows.forEach(r => {
            if (!normVals[r.algorithm]) normVals[r.algorithm] = {};
            const raw = (Number(r[ax.key] ?? 0) - mn) / sp;
            normVals[r.algorithm][ax.key] = ax.lowerBetter ? 1 - raw : raw;
          });
        });
        const algoData = algoRows.map(r => ({ key: r.algorithm, label: algoLabel(r.algorithm), values: normVals[r.algorithm] }));
        const PALETTE = ['var(--brand-2)', 'var(--good)', 'var(--warn)', 'var(--bad)', '#A855F7', '#F6A623'];
        return (
          <Card title="KPI 레이더 — 알고리즘 다축 비교" en="KPI radar" style={{ marginBottom: 16 }}>
            <div className="row gap20" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ flex: '0 0 210px', display: 'flex', justifyContent: 'center' }}>
                <RadarChart algorithms={algoData} metrics={RADAR_AXES.map(a => ({ key: a.key, label: a.label }))} size={210} />
              </div>
              <div className="col gap6" style={{ flex: '1 1 140px' }}>
                {algoData.map((a, i) => (
                  <div key={a.key} className="row gap8" style={{ fontSize: 11.5 }}>
                    <span style={{ width: 12, height: 12, borderRadius: 2, background: PALETTE[i % PALETTE.length], flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontWeight: 600 }}>{a.label}</span>
                    {algoRows[i]?.is_best_total_cost && <Chip tone="good" style={{ fontSize: 9 }}>최저 비용</Chip>}
                  </div>
                ))}
                <div className="muted" style={{ fontSize: 10.5, marginTop: 8, lineHeight: 1.6 }}>
                  외곽 = 해당 지표에서 우수. 모든 축 [0, 1] 정규화, 높을수록 좋음.
                  <br />축 방향: PRR↑ 우수 / 비용·지연·핸드오버·BS부하·미래위험↓ 우수.
                </div>
              </div>
            </div>
          </Card>
        );
      })()}

      {/* ── per-edge breakdown ── */}
      {perEdge.length > 0 && (
        <Card title="구간별 비용" en="Edge breakdown" right={<Chip>{perEdge.length}개 구간</Chip>} style={{ marginBottom: 16 }}>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>구간</th><th className="r">거리</th><th className="r">지연</th><th className="r">부하율</th><th className="r">비용</th><th>커버리지</th></tr>
              </thead>
              <tbody>
                {perEdge.map((e, i) => {
                  const isCur = e.edge_id === curEdgeId;
                  const name  = e.best_node_name || edgeNames[e.edge_id] || e.edge_id;
                  return (
                    <tr key={e.edge_id || i} style={isCur ? { background: 'var(--brand-tint)', fontWeight: 500 } : {}}>
                      <td>
                        <span className="mono" style={{ fontSize: 11.5 }}>{name}</span>
                        {isCur && <Chip style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</Chip>}
                      </td>
                      <td className="r"><span className="num">{e.distance_m?.toFixed(0) ?? '—'}</span><span className="muted" style={{ fontSize: 10 }}> m</span></td>
                      <td className="r"><span className="num" style={{ color: `var(--${latencyTone(e.latency_ms || 0)})`, fontWeight: 600 }}>{(e.latency_ms || 0).toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> ms</span></td>
                      <td className="r">
                        <div className="row gap6" style={{ justifyContent: 'flex-end' }}>
                          <div className="pbar" style={{ width: 40 }}><i style={{ width: `${Math.min((e.load_ratio || 0) * 100, 100)}%`, background: 'var(--brand-2)' }} /></div>
                          <span className="num" style={{ fontSize: 11 }}>{((e.load_ratio || 0) * 100).toFixed(0)}%</span>
                        </div>
                      </td>
                      <td className="r"><span className="num" style={{ fontWeight: 600 }}>{(e.total_cost || 0).toFixed(2)}</span></td>
                      <td>{e.within_coverage === false ? <Chip tone="bad" dot>미커버</Chip> : <Chip tone="good" dot>커버됨</Chip>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ── latency percentiles ── */}
      {pSorted.length > 0 && (
        <Card title="Latency 백분위수" en="Percentiles"
          right={<Chip tone={useLive ? 'good' : ''}>{useLive ? `시간축 ${pSorted.length}건` : `구간 분포 ${pSorted.length}건`}</Chip>}
          style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
            {useLive ? '라이브 실행 중 누적된 latency 시계열의 백분위수.' : '라이브 표본 부족 — 구간별 공간 분포로 대체. 인용 시 구분 표기하세요.'}
          </div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
            <Stat label="P50 (중앙값)" icon="speed" value={pct(pSorted, 50)?.toFixed(1) ?? '—'} unit="ms" />
            <Stat label="P90"         icon="speed" value={pct(pSorted, 90)?.toFixed(1) ?? '—'} unit="ms" />
            <Stat label="P95"         icon="warn"  value={pct(pSorted, 95)?.toFixed(1) ?? '—'} unit="ms" />
            <Stat label="P99"         icon="warn"  value={pct(pSorted, 99)?.toFixed(1) ?? '—'} unit="ms" accent />
          </div>
        </Card>
      )}

      {/* ── Chart 3: latency distribution histogram ── */}
      {(() => {
        const latVals = (bundle?.per_edge_metrics || []).map(e => e.latency_ms).filter(v => v != null);
        const useVals = useLive && liveSamples.length >= 10 ? liveSamples : latVals;
        if (useVals.length < 2) return null;
        return (
          <Card title="Latency 분포 히스토그램" en="Latency distribution"
            right={<Chip tone={useLive && liveSamples.length >= 10 ? 'good' : ''}>{useVals.length}건 {useLive && liveSamples.length >= 10 ? '(시계열)' : '(구간별)'}</Chip>}
            style={{ marginBottom: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
              막대 색상: 초록 = 낮은 지연(양호), 주황 = 중간, 빨강 = 높은 지연(주의). 수직 점선: P50/P90/P95.
              {!(useLive && liveSamples.length >= 10) && <span style={{ color: 'var(--warn)' }}> ※ 라이브 표본 부족 — 구간 공간 분포로 대체.</span>}
            </div>
            <HistogramChart values={useVals} bucketCount={14} xLabel="Latency (ms)" height={150} />
          </Card>
        );
      })()}

      {/* ── Chart 4: base-station load comparison ── */}
      {(() => {
        const routeBS = (bundle?.per_bs_metrics || []).filter(bs => bs.affected_edge_count > 0);
        if (!routeBS.length) return null;
        const items = routeBS.slice(0, 12).map(bs => ({
          label: bs.bs_name || bs.bs_id,
          value: (bs.load_ratio ?? 0) * 100,
          display: `${((bs.load_ratio ?? 0) * 100).toFixed(0)}% ${bs.avg_latency_on_route_ms != null ? '· ' + bs.avg_latency_on_route_ms.toFixed(1) + 'ms' : ''}`,
          color: (bs.load_ratio ?? 0) > 0.9 ? 'var(--bad)' : (bs.load_ratio ?? 0) > 0.7 ? 'var(--warn)' : 'var(--good)',
        }));
        return (
          <Card title="기지국 부하 — 경로 경유 BS" en="BS load (on-route)"
            right={<Chip>{routeBS.length}개 BS</Chip>}
            style={{ marginBottom: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
              경로를 경유하는 BS/RSU만 표시. 표시값: 부하율% · 경로 평균 지연.
              색상: <span style={{ color: 'var(--good)' }}>■ 정상 ≤70%</span>
              &nbsp;<span style={{ color: 'var(--warn)' }}>■ 주의 ≤90%</span>
              &nbsp;<span style={{ color: 'var(--bad)' }}>■ 과부하 &gt;90%</span>
            </div>
            <BarChart items={items} max={100} />
          </Card>
        );
      })()}

      {/* ── algorithm settings sweep (Algo Cmp tab port) ── */}
      {mode === 'pro' && (
        <Card title="알고리즘 설정 비교" en="Settings sweep"
          right={<button className="btn sm" onClick={runComparison} disabled={cmp?.status === 'running' || !bundle?.available}>
            {cmp?.status === 'running' ? <><Icon.reset size={13} className="spin" /> 실행 중…</> : <><Icon.spark size={13} /> 비교 실행</>}
          </button>}
          style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>같은 경로를 latency/BS-선택/자원할당 알고리즘별로 재평가합니다.</div>
          {cmp?.status === 'running' && !cmp?.by_latency && <div className="muted" style={{ fontSize: 12 }}>실행 중…</div>}
          {cmp?.by_latency && (
            <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
              <div>
                <div className="muted" style={{ fontSize: 10.5, marginBottom: 6 }}>지연 알고리즘 (평균 ms)</div>
                <BarChart items={Object.entries(cmp.by_latency).map(([id, v]) => {
                  const isLow = v.avg_latency_ms === Math.min(...Object.values(cmp.by_latency).map(x => x.avg_latency_ms));
                  return { label: algoLabel(id), value: v.avg_latency_ms, display: `${v.avg_latency_ms.toFixed(1)}ms${isLow ? ' ·최저' : ''}`, color: isLow ? 'var(--good)' : 'var(--brand-2)' };
                })} />
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10.5, marginBottom: 6 }}>BS 선택 (핸드오버 수)</div>
                <BarChart items={Object.entries(cmp.by_bs_selection || {}).map(([id, v]) => ({
                  label: algoLabel(id), value: v.handover_count, display: `${v.handover_count}회`, color: 'var(--brand-2)',
                }))} />
              </div>
              <div>
                <div className="muted" style={{ fontSize: 10.5, marginBottom: 6 }}>자원 할당 (사용률)</div>
                <BarChart items={Object.entries(cmp.by_allocation || {}).map(([id, v]) => ({
                  label: algoLabel(id), value: v.total_utilization * 100, display: `${(v.total_utilization * 100).toFixed(0)}%`, color: 'var(--brand-2)',
                }))} max={100} />
              </div>
            </div>
          )}
          {!cmp?.by_latency && cmp?.status !== 'running' && (
            <div className="muted" style={{ fontSize: 12 }}>{cmp?.status === 'error' ? (cmp.reason || '실패') : '"비교 실행"을 눌러 결과를 확인하세요.'}</div>
          )}
        </Card>
      )}

      {/* ── Chart 7: Latency CDF ── */}
      {(() => {
        const latVals = (bundle?.per_edge_metrics || []).map(e => e.latency_ms).filter(v => v != null);
        if (latVals.length < 2) return null;
        return (
          <Card title="지연 누적분포 (CDF)" en="Latency CDF"
            right={<Chip tone="">{latVals.length}개 구간</Chip>}
            style={{ marginBottom: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
              누적분포함수 — 수직선: <span style={{ color: 'var(--good)' }}>■ URLLC 10ms</span>
              &nbsp;·&nbsp;<span style={{ color: 'var(--warn)' }}>■ 안전 100ms</span>
              &nbsp;·&nbsp;<span style={{ color: 'var(--bad)' }}>■ 고내성 500ms</span>
            </div>
            <CDFChart latencies={latVals} height={220} />
          </Card>
        );
      })()}

      {/* ── Jain's Fairness Index card ── */}
      {bundle?.run_summary?.jain_fairness_index != null && (() => {
        const jfi = bundle.run_summary.jain_fairness_index;
        const tone = jfi >= 0.9 ? 'good' : jfi >= 0.7 ? 'warn' : 'bad';
        const label = jfi >= 0.9 ? '공정' : jfi >= 0.7 ? '보통' : '불공정';
        return (
          <Card title="Jain 공정성 지수" en="Jain's Fairness Index"
            right={<Chip tone={tone}>{label} {jfi.toFixed(4)}</Chip>}
            style={{ marginBottom: 16 }}>
            <div className="row gap16" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div style={{ flex: '1 1 180px' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: jfi >= 0.9 ? 'var(--good)' : jfi >= 0.7 ? 'var(--warn)' : 'var(--bad)', marginBottom: 4 }}>
                  {jfi.toFixed(4)}
                </div>
                <div className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>
                  범위: [1/n, 1] — 1.0 = 완전 공정, 1/n = 최악 불공정
                </div>
              </div>
              <div style={{ flex: '2 1 220px', fontSize: 11, lineHeight: 1.6, background: 'var(--surface-2)', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}>
                <div style={{ color: 'var(--brand-2)', marginBottom: 8, display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, fontSize: 13 }}>
                  <i>J</i> =
                  <MathFrac
                    n={<>(Σ<sub><i>i</i></sub> <i>ρ</i><sub><i>i</i></sub>)<sup>2</sup></>}
                    d={<><i>n</i> · Σ<sub><i>i</i></sub> <i>ρ</i><sub><i>i</i></sub><sup>2</sup></>}
                  />
                </div>
                <div className="muted"><i>ρ</i><sub><i>i</i></sub> = BS <i>i</i> 부하율, <i>n</i> = BS 수</div>
                <div className="muted" style={{ marginTop: 4 }}>
                  출처: Jain, Chiu, Hawe, <i>DEC-TR-301</i>, 1984 §3.1
                </div>
              </div>
            </div>
          </Card>
        );
      })()}

      {/* ── run history comparison ── */}
      {mode === 'pro' && (
        <Card title="실행 이력" en="Run history" right={<Chip>{history.length}개 저장됨</Chip>} style={{ marginBottom: 16 }}>
          {history.length === 0 ? (
            <div className="muted" style={{ fontSize: 12 }}>저장된 실행 없음 — 위에서 "현재 결과 히스토리 저장"을 누르세요.</div>
          ) : (
            <>
              <div className="tbl-wrap" style={{ marginBottom: 12 }}>
                <table className="tbl">
                  <thead><tr><th></th><th>시각</th><th>알고리즘 수</th><th>네트워크 모드</th><th></th></tr></thead>
                  <tbody>
                    {history.map((h, i) => (
                      <tr key={i} className={checkedRuns.includes(i) ? 'selected' : ''}>
                        <td><input type="checkbox" checked={checkedRuns.includes(i)} onChange={() => setCheckedRuns(p => p.includes(i) ? p.filter(x => x !== i) : [...p, i].slice(-3))} /></td>
                        <td><span className="num muted" style={{ fontSize: 11.5 }}>{new Date(h.timestamp).toLocaleString('ko-KR')}</span></td>
                        <td><span className="num">{Object.keys(h.algorithms || {}).length}</span></td>
                        <td><Chip tone="brand">{h.config?.policy_options?.network_mode ?? '—'}</Chip></td>
                        <td className="r"><button className="btn icon sm" onClick={() => removeRun(i)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {checkedSelected.length >= 2 && (
                <div className="tbl-wrap">
                  <div className="muted" style={{ fontSize: 11, marginBottom: 7 }}>선택 {checkedSelected.length}개 비교</div>
                  <table className="tbl">
                    <thead><tr><th>시각</th>{CMP_COLS.map(c => <th key={c.key} className="r">{c.label}</th>)}</tr></thead>
                    <tbody>
                      {checkedSelected.map((h, i) => {
                        const best = Object.values(h.algorithms || {}).sort((a, b) => (a.total_cost ?? Infinity) - (b.total_cost ?? Infinity))[0];
                        return (
                          <tr key={i}>
                            <td><span className="num muted" style={{ fontSize: 11 }}>{new Date(h.timestamp).toLocaleString('ko-KR')}</span></td>
                            {CMP_COLS.map(c => <td key={c.key} className="r"><span className="num">{best && best[c.key] != null ? c.fmt(best[c.key]) : '—'}</span></td>)}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </Card>
      )}
    </>
  );
}

// ── Explain section ────────────────────────────────────────────────────────

function FindingList({ title, items, renderRow, emptyMsg, tone = '' }) {
  if (!items?.length) return null;
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="row gap8" style={{ marginBottom: 7 }}>
        <b style={{ fontSize: 12 }}>{title}</b>
        <Chip tone={tone || (items.length > 3 ? 'warn' : '')}>{items.length}건</Chip>
      </div>
      <div className="col gap5">
        {items.slice(0, 8).map((it, i) => (
          <div key={i} className="row gap10" style={{ fontSize: 11.5, padding: '7px 11px', background: 'var(--surface-2)', borderRadius: 7, border: '1px solid var(--border)', flexWrap: 'wrap' }}>
            {renderRow(it)}
          </div>
        ))}
        {items.length > 8 && <div className="muted" style={{ fontSize: 11, paddingLeft: 4 }}>… {items.length - 8}건 더 있음</div>}
      </div>
    </div>
  );
}

function SectionExplain({ bundle, simLogs, mode, simConfig }) {
  const [analyzing,     setAnalyzing]     = useState(false);
  const [llmResult,     setLlmResult]     = useState([]);
  const [llmError,      setLlmError]      = useState(null);
  const [revealed,      setRevealed]      = useState(0);
  const [llmProviders,  setLlmProviders]  = useState([]);
  const [selectedProv,  setSelectedProv]  = useState('');
  const [usedProvider,  setUsedProvider]  = useState(null);
  const [logOpen,       setLogOpen]       = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/analysis/llm/providers`).then(r => r.json()).then(d => {
      const ps = d.providers || [];
      setLlmProviders(ps);
      const active = ps.find(p => p.active);
      if (active && !selectedProv) setSelectedProv(active.id);
    }).catch(() => {});
  }, []);

  const summ = bundle?.simulation_summary;
  const seed = summ?.recommendation_text_seed;

  async function runAI() {
    setAnalyzing(true); setRevealed(0); setLlmError(null); setLlmResult([]); setUsedProvider(null);
    try {
      const payload = {
        sim_elapsed: 0, vehicle_pos: null,
        edge_history: [], edge_avg_speeds: {}, route_edge_names: {},
        sim_logs: simLogs ?? [],
        algorithm: summ?.selected_algorithm ?? null,
        handover_count: summ?.frequent_handover_sections?.length ?? 0,
        latency_ms: bundle?.run_summary?.avg_latency_ms ?? null,
        connected_node: null,
        provider: selectedProv || null,
      };
      const res = await fetch(`${API_BASE}/api/analysis/llm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || res.statusText); }
      const data = await res.json();
      const sections = data.sections || [];
      setLlmResult(sections); setUsedProvider(data.provider || null); setAnalyzing(false);
      sections.forEach((_, i) => setTimeout(() => setRevealed(i + 1), i * 420));
    } catch (e) { setAnalyzing(false); setLlmError(e.message || 'AI 분석 중 오류가 발생했습니다.'); }
  }

  if (!bundle?.available) {
    return (
      <Card title="원인 분석" en="Explain" style={{ marginBottom: 18 }}>
        <SectionEmpty msg="시뮬레이션을 실행하면 병목·위험 분석이 표시됩니다." />
      </Card>
    );
  }

  return (
    <>
      {/* ── primary finding + suggested focus ── */}
      {seed?.primary_finding && (
        <div style={{ padding: '12px 16px', background: 'var(--brand-tint)', borderRadius: 10, marginBottom: 16, fontSize: 12.5, lineHeight: 1.55 }}>
          <b style={{ marginRight: 8, color: 'var(--brand-2)' }}>핵심 발견</b>{seed.primary_finding}
          {seed.suggested_focus && <div className="muted" style={{ marginTop: 5, fontSize: 11.5 }}>분석 초점: {seed.suggested_focus}</div>}
        </div>
      )}

      {/* ── findings grid ── */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <Card title="병목 구간" en="Bottlenecks" right={<Chip tone={summ?.bottleneck_sections?.length ? 'warn' : ''}>{summ?.bottleneck_sections?.length ?? 0}건</Chip>}>
          <FindingList title="" items={summ?.bottleneck_sections} tone="warn" renderRow={it => (<>
            <span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span>
            <span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}% · {(it.latency_ms ?? 0).toFixed(1)} ms</span>
            <Chip tone={it.severity === 'critical' ? 'bad' : 'warn'} style={{ marginLeft: 'auto' }}>{it.severity}</Chip>
          </>)} />
          {!summ?.bottleneck_sections?.length && <div className="muted" style={{ fontSize: 12 }}>병목 없음</div>}
        </Card>

        <Card title="과부하 기지국" en="Overloaded BS" right={<Chip tone={summ?.overloaded_base_stations?.length ? 'bad' : ''}>{summ?.overloaded_base_stations?.length ?? 0}건</Chip>}>
          <FindingList title="" items={summ?.overloaded_base_stations} tone="bad" renderRow={it => (<>
            <span className="mono" style={{ fontWeight: 600 }}>{it.bs_name}</span>
            <span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}%</span>
            {it.affected_edge_count > 0 && <span className="muted">·경로 {it.affected_edge_count}구간 영향</span>}
            <Chip tone={it.severity === 'critical' ? 'bad' : 'warn'} style={{ marginLeft: 'auto' }}>{it.severity}</Chip>
          </>)} />
          {!summ?.overloaded_base_stations?.length && <div className="muted" style={{ fontSize: 12 }}>과부하 없음</div>}
        </Card>

        <Card title="고지연 구간" en="High latency" right={<Chip tone={summ?.high_latency_sections?.length ? 'bad' : ''}>{summ?.high_latency_sections?.length ?? 0}건</Chip>}>
          <FindingList title="" items={summ?.high_latency_sections} tone="bad" renderRow={it => (<>
            <span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span>
            <span className="num" style={{ color: 'var(--bad)' }}>{(it.latency_ms ?? 0).toFixed(1)} ms</span>
            <span className="muted">(+{(it.excess_ms ?? 0).toFixed(1)} ms 초과)</span>
          </>)} />
          {!summ?.high_latency_sections?.length && <div className="muted" style={{ fontSize: 12 }}>고지연 없음</div>}
        </Card>

        <Card title="핸드오버 구간" en="Handovers" right={<Chip>{summ?.frequent_handover_sections?.length ?? 0}건</Chip>}>
          <FindingList title="" items={summ?.frequent_handover_sections} renderRow={it => (<>
            <span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span>
            <span className="muted" style={{ fontSize: 11 }}>{it.from_bs_name} → {it.to_bs_name}</span>
            <span className="num muted" style={{ marginLeft: 'auto' }}>{(it.latency_ms ?? 0).toFixed(1)} ms</span>
          </>)} />
          {!summ?.frequent_handover_sections?.length && <div className="muted" style={{ fontSize: 12 }}>핸드오버 없음</div>}
        </Card>
      </div>

      {/* ── future connectivity risk ── */}
      {summ?.future_connectivity_risk_sections?.length > 0 && (
        <Card title="미래 연결 위험 구간" en="Future connectivity risk" right={<Chip tone="bad">{summ.future_connectivity_risk_sections.length}건</Chip>} style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>경로 후반부(마지막 25%)에서 기지국 커버리지가 끊기는 구간입니다.</div>
          <FindingList title="" items={summ.future_connectivity_risk_sections} tone="bad" renderRow={it => (<>
            <span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span>
            <Chip tone="bad" style={{ marginLeft: 'auto' }}>{it.severity}</Chip>
          </>)} />
        </Card>
      )}

      {/* ── degradation warnings ── */}
      {seed?.degradation_warnings?.length > 0 && (
        <Card title="성능 저하 경고" en="Degradation warnings" style={{ marginBottom: 16 }}>
          <div className="col gap6">
            {seed.degradation_warnings.map((t, i) => (
              <div key={i} className="row gap10" style={{ padding: '7px 12px', borderRadius: 8, background: 'var(--bad-tint)', border: '1px solid var(--bad)', fontSize: 12 }}>
                <Icon.warn size={13} style={{ color: 'var(--bad)', flexShrink: 0 }} />
                <span>{t}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ── Chart 5: route segment risk strip ── */}
      {bundle?.per_edge_metrics?.length > 0 && (() => {
        const segs = bundle.per_edge_metrics;
        const riskOf = e => {
          if (e.within_coverage === false) return 'bad';
          if ((e.load_ratio ?? 0) > 0.85) return 'bad';
          if ((e.latency_ms ?? 0) > 60 || (e.load_ratio ?? 0) > 0.6) return 'warn';
          return 'good';
        };
        const items = segs.map((e, i) => ({
          badge:   e.handover ? 'HO' : `E${i + 1}`,
          label:   (e.street_name || e.edge_id || `#${i + 1}`).slice(0, 14),
          value:   e.latency_ms?.toFixed(1),
          meta:    `${((e.load_ratio ?? 0) * 100).toFixed(0)}% 부하`,
          accent:  riskOf(e) === 'bad' ? 'var(--bad)' : riskOf(e) === 'warn' ? 'var(--warn)' : 'var(--good)',
          textColor: e.within_coverage === false ? 'var(--bad)' : undefined,
        }));
        const nBad = items.filter(it => it.accent === 'var(--bad)').length;
        const nWarn = items.filter(it => it.accent === 'var(--warn)').length;
        return (
          <Card title="경로 구간 위험도 시각화" en="Route segment risk"
            right={
              <div className="row gap6">
                {nBad > 0  && <Chip tone="bad">{nBad}개 위험</Chip>}
                {nWarn > 0 && <Chip tone="warn">{nWarn}개 주의</Chip>}
                <Chip>{segs.length}개 구간</Chip>
              </div>
            }
            style={{ marginBottom: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
              각 막대 상단 색상: 위험도 레벨. 수치: 지연(ms). 'HO' 배지 = 핸드오버 발생 구간.
              <span style={{ marginLeft: 10, color: 'var(--good)' }}>▬ 정상</span>
              <span style={{ marginLeft: 8, color: 'var(--warn)' }}> ▬ 주의(부하&gt;60% 또는 지연&gt;60ms)</span>
              <span style={{ marginLeft: 8, color: 'var(--bad)' }}> ▬ 위험(미커버 또는 부하&gt;85%)</span>
            </div>
            <SegmentStrip items={items} height={90} />
          </Card>
        );
      })()}

      {/* ── event log (collapsed by default — raw debug log) ── */}
      <Card title="시뮬레이션 이벤트 로그" en="Event log"
        right={
          <div className="row gap8">
            {simLogs?.length > 0 && <Chip tone="good" dot>실시간</Chip>}
            <Chip>{simLogs?.length ?? 0}건</Chip>
            <button className="btn icon sm" onClick={() => setLogOpen(o => !o)} title={logOpen ? '접기' : '펼치기'}>{logOpen ? '▲' : '▼'}</button>
          </div>
        }
        style={{ marginBottom: 16 }}>
        {!logOpen ? (
          <div className="muted" style={{ fontSize: 11.5 }}>
            시뮬레이션 원시 이벤트 로그 ({simLogs?.length ?? 0}건). ▼ 버튼을 눌러 펼치세요.
          </div>
        ) : (
          <div className="tbl-wrap" style={{ maxHeight: 320 }}>
            <table className="tbl">
              <thead><tr><th>시각</th><th>대상</th><th>유형</th><th>내용</th></tr></thead>
              <tbody>
                {[...(simLogs || [])].reverse().map((l, i) => {
                  const st = LOG_STYLE[l.kind] ?? LOG_STYLE.info;
                  return (
                    <tr key={i}>
                      <td><span className="num muted" style={{ fontSize: 11.5 }}>{l.t}</span></td>
                      <td><span className="mono" style={{ fontWeight: 600, fontSize: 11.5 }}>{l.target}</span></td>
                      <td><Chip tone={st.tone} dot={!!st.tone}>{st.ko}</Chip></td>
                      <td style={{ whiteSpace: 'normal', maxWidth: 280, lineHeight: 1.4 }}>{l.ko}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Formula reference panel ── */}
      {(() => {
        const [open, setOpen] = React.useState(false);
        const M = ({ c }) => <span style={{ fontStyle: 'italic' }}>{c}</span>;
        const FORMULAS = [
          {
            metric: 'E2E 지연',
            formula: <><M c="L" /> = <M c="L" /><sub>tx</sub> + <M c="L" /><sub>q</sub> + <M c="L" /><sub>comp</sub></>,
            threshold: '< 100 ms', ref: 'Hung et al., IEEE VTM 2017',
          },
          {
            metric: 'PRR',
            formula: <>PRR = 1 − TWDR</>,
            threshold: '≥ 0.9', ref: 'Ali et al., IEEE Access 2021 §II.B',
          },
          {
            metric: 'CBR',
            formula: <>CBR = 1 − exp(−<M c="ρ" /> · <M c="R" /><sub>tx</sub> · <M c="f" /><sub>CAM</sub> · <M c="T" /><sub>RRI</sub>)</>,
            threshold: '< 0.65', ref: 'Gonzalez-Martin et al., IEEE TVT 2019 §IV.A; ETSI TS 102 687 §5.2.2',
          },
          {
            metric: 'PIR P99',
            formula: <>PIR<sub>P99</sub> = <MathFrac n={<><M c="T" /><sub>CAM</sub></>} d="PRR" /></>,
            threshold: '≤ 100 ms', ref: '3GPP TR 37.885 §A.2.4; Eckermann et al., IEEE VTC 2019',
          },
          {
            metric: 'Jain FI',
            formula: <><M c="J" /> = <MathFrac n={<>(Σ<sub><M c="i" /></sub> <M c="ρ" /><sub><M c="i" /></sub>)<sup>2</sup></>} d={<><M c="n" /> · Σ<sub><M c="i" /></sub> <M c="ρ" /><sub><M c="i" /></sub><sup>2</sup></>} /></>,
            threshold: '≥ 0.9', ref: 'Jain, Chiu, Hawe, DEC-TR-301, 1984 §3.1',
          },
          {
            metric: '경로 손실',
            formula: <>PL(<M c="d" />) = PL(<M c="d" /><sub>0</sub>) + 10·<M c="n" />·log<sub>10</sub>(<M c="d" />/<M c="d" /><sub>0</sub>)</>,
            threshold: 'n: LOS 1.61 / Urban 2.75 / NLOS 3.50', ref: 'Fernandez et al., IEEE WCL 2014, Table I-II',
          },
          {
            metric: 'TWDR',
            formula: <>TWDR = <MathFrac n={<>Σ<sub><M c="e" /></sub> [<M c="t" /><sub><M c="e" /></sub> · 𝟙(¬cov(<M c="e" />))]</>} d={<>Σ<sub><M c="e" /></sub> <M c="t" /><sub><M c="e" /></sub></>} /></>,
            threshold: '< 0.1', ref: '자체 정의 (coverage risk)',
          },
          {
            metric: '핸드오버 중단',
            formula: <>HIT = Σ<sub><M c="e" /></sub> <M c="t" /><sub>ho</sub>(<M c="e" />) · 𝟙(HO)</>,
            threshold: '200–400 ms/회', ref: '5G NR, IEEE doc 10320318 (2023)',
          },
        ];
        return (
          <Card title="지표 정의 및 근거" en="Metric definitions & citations"
            right={<button className="btn icon sm" onClick={() => setOpen(o => !o)}>{open ? '▲' : '▼'}</button>}
            style={{ marginBottom: 16 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: open ? 10 : 0 }}>
              모든 해석 모델 기반 지표는 검증된 논문/표준 수식을 사용합니다. 클릭하여 펼치기.
            </div>
            {open && (
              <div className="tbl-wrap">
                <table className="tbl" style={{ fontSize: 11 }}>
                  <thead><tr><th>지표</th><th>수식</th><th>임계값</th><th>출처</th></tr></thead>
                  <tbody>
                    {FORMULAS.map((f, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{f.metric}</td>
                        <td>
                          <span style={{ fontSize: 12, color: 'var(--brand-2)', display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
                            {f.formula}
                          </span>
                        </td>
                        <td style={{ whiteSpace: 'nowrap', color: 'var(--muted)' }}>{f.threshold}</td>
                        <td style={{ fontSize: 10.5, color: 'var(--muted)', maxWidth: 260 }}>{f.ref}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        );
      })()}

      {/* ── LLM analysis ── */}
      <Card title="AI 자연어 분석" en="LLM summary" right={
        <div className="row gap8">
          {mode === 'pro' && llmProviders.length > 1 && (
            <select className="input" style={{ height: 28, fontSize: 11, minWidth: 110 }} value={selectedProv} onChange={e => setSelectedProv(e.target.value)}>
              {llmProviders.map(p => <option key={p.id} value={p.id}>{p.name.split('/')[1]?.trim() || p.name} — {p.model}</option>)}
            </select>
          )}
          <Chip tone="brand"><Icon.spark size={11} /> {usedProvider ? (PROVIDER_LABELS[usedProvider]?.name || usedProvider) : (PROVIDER_LABELS[selectedProv]?.name || 'AI')}</Chip>
        </div>
      } style={{ marginBottom: 16 }}>
        {revealed === 0 && !analyzing && (
          <div style={{ textAlign: 'center', padding: '22px 16px' }}>
            <div style={{ width: 48, height: 48, borderRadius: 14, background: 'var(--brand-tint)', display: 'grid', placeItems: 'center', margin: '0 auto 12px', color: 'var(--brand-2)' }}><Icon.spark size={24} /></div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>AI 분석 준비</div>
            <div className="muted" style={{ fontSize: 11.5, marginBottom: 16 }}>이벤트 로그 {simLogs?.length ?? 0}건을 분석해 자연어 요약을 생성합니다</div>
            {llmError && <div style={{ fontSize: 11.5, color: 'var(--bad)', marginBottom: 12, background: 'var(--bad-tint)', padding: '7px 10px', borderRadius: 8, textAlign: 'left' }}>{llmError}</div>}
            <button className="btn primary" onClick={runAI}><Icon.spark size={14} /> AI 분석 시작</button>
          </div>
        )}
        {analyzing && (
          <div style={{ textAlign: 'center', padding: '30px 16px' }}>
            <div className="spin" style={{ width: 28, height: 28, border: '3px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%', margin: '0 auto 12px' }} />
            <div className="muted" style={{ fontSize: 12 }}>AI 분석 중…</div>
          </div>
        )}
        {revealed > 0 && (
          <div className="col gap10">
            {llmResult.slice(0, revealed).map((t, i) => (
              <div key={i} className="fade row gap10" style={{ padding: '11px 13px', background: i === 6 ? 'var(--bad-tint)' : i === 7 ? 'var(--good-tint)' : 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                <span className="num" style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-2)', flexShrink: 0, marginTop: 1 }}>{String(i + 1).padStart(2, '0')}</span>
                <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t}</span>
              </div>
            ))}
            {revealed === llmResult.length && llmResult.length > 0 && (
              <button className="btn sm" onClick={runAI} style={{ alignSelf: 'flex-start' }}><Icon.reset size={12} /> 다시 분석</button>
            )}
          </div>
        )}
      </Card>
    </>
  );
}

// ── Sheet Comparison Table ─────────────────────────────────────────────────

function SectionSheetCompare() {
  const [batches, setBatches] = useState(() => loadScenarioBatches());
  const [selIdx, setSelIdx]   = useState(0);
  useEffect(() => {
    const t = setInterval(() => setBatches(loadScenarioBatches()), 2000);
    return () => clearInterval(t);
  }, []);

  const sheetBatches = [...batches].reverse().filter(b => {
    const lbl = b.label || '';
    return lbl.startsWith('시뮬레이션 시트 비교') || lbl.startsWith('GNN-MAML 비교');
  });

  if (sheetBatches.length === 0) {
    return (
      <Card title="시트 알고리즘 비교" en="Sheet comparison" style={{ marginBottom: 16 }}>
        <SectionEmpty msg="시뮬레이션 탭 → GNN-MAML 비교 실행 또는 전체 시트 비교를 눌러 결과를 비교하세요." />
      </Card>
    );
  }

  const cur        = sheetBatches[Math.min(selIdx, sheetBatches.length - 1)];
  const doneRows   = (cur.results || []).filter(r => r.status === 'done');

  // Build display rows
  const rows = doneRows.map(r => {
    const rc  = r.route_cost_result || {};
    const prr = rc.prr_approx != null ? rc.prr_approx
              : rc.coverage_risk != null ? +(1 - rc.coverage_risk).toFixed(4) : null;
    // P99 from per_edge latency values
    const lats = (rc.per_edge || []).map(e => e.latency_ms).filter(v => v != null).sort((a, b) => a - b);
    const p99  = lats.length ? lats[Math.min(lats.length - 1, Math.ceil(0.99 * lats.length) - 1)] : null;
    const algo = rc.routing_mode || r.simulation_summary?.selected_algorithm || null;
    return {
      label:           r.label || r.id,
      algo:            algo,
      avg_latency_ms:  rc.avg_latency_ms,
      p99_latency_ms:  p99,
      total_cost:      rc.total_cost,
      handover_count:  rc.handover_count,
      prr,
    };
  });

  // Best per metric
  const LB  = { avg_latency_ms: true, p99_latency_ms: true, total_cost: true, handover_count: true, prr: false };
  const best = {};
  Object.keys(LB).forEach(col => {
    const vals = rows.map(r => r[col]).filter(v => v != null);
    if (vals.length) best[col] = LB[col] ? Math.min(...vals) : Math.max(...vals);
  });

  // % improvement of each row over worst (lower-better cols) or best baseline
  const worstLatency = rows.length ? Math.max(...rows.map(r => r.avg_latency_ms ?? 0)) : 0;

  return (
    <Card title="시트 알고리즘 비교" en="Sheet comparison"
      right={<div className="row gap8">
        <Chip tone="good" dot>공유 환경 동일</Chip>
        <Chip>{doneRows.length}개 시트</Chip>
      </div>}
      style={{ marginBottom: 16 }}>

      {/* batch selector */}
      {sheetBatches.length > 1 && (
        <div className="row gap6 wrap" style={{ marginBottom: 12 }}>
          {sheetBatches.map((b, i) => (
            <button key={b.batch_id} className={'btn sm' + (i === selIdx ? ' primary' : '')}
              onClick={() => setSelIdx(i)}>
              {new Date(b.ended_at || b.started_at).toLocaleTimeString()} 실행
            </button>
          ))}
        </div>
      )}

      {/* shared env strip */}
      <div style={{ padding: '6px 10px', background: 'var(--brand-tint)', border: '1px solid var(--brand-2)', borderRadius: 7, fontSize: 11, color: 'var(--ink-2)', marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontWeight: 700, color: 'var(--brand-2)' }}>논문 통제 조건</span>
        <span>📍 출발지 고정</span><span>🏁 도착지 고정</span><span>📡 BS·RSU 공유</span><span>🗺 구역 공유</span>
        <span className="muted" style={{ fontSize: 10, marginLeft: 'auto' }}>시트마다 알고리즘 설정만 다름</span>
      </div>

      {/* comparison table */}
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>시트</th>
              <th>알고리즘</th>
              <th className="r">지연 avg (ms)</th>
              <th className="r">지연 P99 (ms)</th>
              <th className="r">총 비용</th>
              <th className="r">핸드오버</th>
              <th className="r">PRR</th>
              <th className="r">개선율†</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const isBestLat  = row.avg_latency_ms != null && row.avg_latency_ms === best.avg_latency_ms;
              const isBestCost = row.total_cost != null && row.total_cost === best.total_cost;
              const isBestHO   = row.handover_count != null && row.handover_count === best.handover_count;
              const isBestPrr  = row.prr != null && row.prr === best.prr;
              const improvPct  = worstLatency > 0 && row.avg_latency_ms != null
                ? (((worstLatency - row.avg_latency_ms) / worstLatency) * 100)
                : null;
              return (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{row.label}</td>
                  <td><span className="mono" style={{ fontSize: 11 }}>{row.algo ? algoLabel(row.algo) : '—'}</span></td>
                  <td className="r">
                    <span className="num" style={{ color: isBestLat ? 'var(--good)' : 'inherit', fontWeight: isBestLat ? 700 : 400 }}>
                      {fmt1(row.avg_latency_ms)}
                    </span>
                    {isBestLat && <Chip tone="good" style={{ marginLeft: 5, fontSize: 9 }}>최적</Chip>}
                  </td>
                  <td className="r"><span className="num">{fmt1(row.p99_latency_ms)}</span></td>
                  <td className="r">
                    <span className="num" style={{ color: isBestCost ? 'var(--good)' : 'inherit', fontWeight: isBestCost ? 700 : 400 }}>
                      {fmt2(row.total_cost)}
                    </span>
                    {isBestCost && <Chip tone="good" style={{ marginLeft: 5, fontSize: 9 }}>최적</Chip>}
                  </td>
                  <td className="r">
                    <span className="num" style={{ color: isBestHO ? 'var(--good)' : 'inherit', fontWeight: isBestHO ? 700 : 400 }}>
                      {row.handover_count ?? '—'}
                    </span>
                  </td>
                  <td className="r">
                    <span className="num" style={{ color: isBestPrr ? 'var(--good)' : 'inherit', fontWeight: isBestPrr ? 700 : 400 }}>
                      {row.prr != null ? row.prr.toFixed(3) : '—'}
                    </span>
                  </td>
                  <td className="r">
                    {improvPct != null
                      ? <span style={{ fontWeight: 700, color: improvPct > 0 ? 'var(--good)' : improvPct < 0 ? 'var(--bad)' : 'var(--ink-3)' }}>
                          {improvPct > 0 ? '−' : improvPct < 0 ? '+' : ''}{Math.abs(improvPct).toFixed(1)}%
                        </span>
                      : <span className="muted">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>
        † 개선율 = 시트 중 최고 지연(worst) 대비 감소 비율 · 녹색 = 해당 열 최선값
      </div>
    </Card>
  );
}


// ── Channel & Fairness section ─────────────────────────────────────────────

function SectionChannel({ bundle }) {
  const rs = bundle?.run_summary;

  if (!bundle?.available) {
    return (
      <Card title="채널·공정성" en="Channel & Fairness" style={{ marginBottom: 16 }}>
        <SectionEmpty msg="시뮬레이션을 실행하면 채널·공정성 분석이 표시됩니다." />
      </Card>
    );
  }

  const cbr      = rs?.cbr_avg;
  const pir      = rs?.pir_p99_ms;
  const pirOk    = rs?.pir_compliant;
  const hit      = rs?.hit_total_ms;
  const hCount   = rs?.handover_count;
  const jfi      = rs?.jain_fairness_index;
  const urllc    = rs?.urllc_compliance_ratio;

  const cbrTone  = cbr == null ? '' : cbr > 0.65 ? 'bad' : cbr > 0.45 ? 'warn' : 'good';
  const cbrLabel = cbr == null ? '—' : cbr > 0.65 ? '혼잡' : cbr > 0.45 ? '주의' : '양호';
  const jfiTone  = jfi == null ? '' : jfi >= 0.9 ? 'good' : jfi >= 0.7 ? 'warn' : 'bad';
  const jfiLabel = jfi == null ? '—' : jfi >= 0.9 ? '공정' : jfi >= 0.7 ? '보통' : '불공정';

  return (
    <>
      {/* ── CBR ── */}
      <Card title="CBR — Channel Busy Ratio" en="채널 점유율"
        right={cbr != null ? <Chip tone={cbrTone}>{cbrLabel} {cbr.toFixed(3)}</Chip> : null}
        style={{ marginBottom: 16 }}>
        <div className="row gap16" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ flex: '1 1 160px' }}>
            <div style={{ fontSize: 30, fontWeight: 700, color: `var(--${cbrTone || 'ink-2'})`, marginBottom: 4 }}>
              {cbr != null ? cbr.toFixed(3) : '—'}
            </div>
            {cbr != null && (
              <>
                <div className="pbar" style={{ width: '100%', height: 8, marginBottom: 4 }}>
                  <i style={{ width: `${Math.min(cbr * 100, 100)}%`, background: `var(--${cbrTone})` }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--ink-4)' }}>
                  <span>0.0</span><span style={{ color: 'var(--warn)' }}>혼잡 0.65</span><span>1.0</span>
                </div>
              </>
            )}
          </div>
          <div style={{ flex: '2 1 220px', fontSize: 11, lineHeight: 1.65, background: 'var(--surface-2)', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--brand-2)', marginBottom: 6, fontSize: 12.5 }}>
              CBR = 1 − exp(−ρ · R<sub>tx</sub> · f<sub>CAM</sub> · T<sub>RRI</sub>)
            </div>
            <div className="muted">혼잡 임계값: 0.65 (ETSI TS 102 687 §5.2.2)</div>
            <div className="muted">출처: Gonzalez-Martin et al., IEEE TVT 68(2), 2019 §IV.A †</div>
            <div className="muted" style={{ marginTop: 4, fontSize: 10.5 }}>† 해석 모델 기반 — C-V2X Mode 4 SPS 가정, 10Hz CAM, 75MHz ITS 대역</div>
          </div>
        </div>
      </Card>

      {/* ── PIR P99 ── */}
      <Card title="PIR P99 — Packet Inter-Reception Time" en="패킷 재수신 간격"
        right={pir != null ? <Chip tone={pirOk ? 'good' : 'bad'}>{pirOk ? '기준 충족' : '기준 초과'} {pir?.toFixed(1)} ms</Chip> : null}
        style={{ marginBottom: 16 }}>
        <div className="row gap16" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ flex: '1 1 160px' }}>
            <div style={{ fontSize: 30, fontWeight: 700, color: pir == null ? 'var(--ink-4)' : pirOk ? 'var(--good)' : 'var(--bad)', marginBottom: 4 }}>
              {pir != null ? pir.toFixed(1) : '—'} <span style={{ fontSize: 14, fontWeight: 400 }}>ms</span>
            </div>
            {pir != null && (
              <div style={{ padding: '5px 10px', borderRadius: 6, background: pirOk ? 'var(--good-tint)' : 'var(--bad-tint)', fontSize: 11, fontWeight: 600, color: pirOk ? 'var(--good)' : 'var(--bad)' }}>
                {pirOk ? '✓ 100ms 이하 — 안전 메시지 서비스 적합' : '✗ 100ms 초과 — 안전 서비스 부적합'}
              </div>
            )}
          </div>
          <div style={{ flex: '2 1 220px', fontSize: 11, lineHeight: 1.65, background: 'var(--surface-2)', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--brand-2)', marginBottom: 6, fontSize: 12.5 }}>
              PIR<sub>P99</sub> = T<sub>CAM</sub> / PRR<sub>edge</sub>
            </div>
            <div className="muted">기준: P99 ≤ 100 ms (3GPP TR 37.885 Table A.1)</div>
            <div className="muted">T<sub>CAM</sub> = 100 ms (ETSI EN 302 637-2 §6.1.2.3)</div>
            <div className="muted">출처: Eckermann et al., IEEE VTC Fall 2019 †</div>
            <div className="muted" style={{ marginTop: 4, fontSize: 10.5 }}>† 기하분포 상한 — PRR 공간 일정 가정 단순화</div>
          </div>
        </div>
      </Card>

      {/* ── HIT + URLLC ── */}
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <Card title="HIT — Handover Interruption Time" en="핸드오버 중단 시간">
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--warn)', marginBottom: 4 }}>
            {hit != null ? hit.toFixed(0) : '—'} <span style={{ fontSize: 14, fontWeight: 400 }}>ms</span>
          </div>
          <div className="muted" style={{ fontSize: 11, lineHeight: 1.6 }}>
            핸드오버 {hCount ?? 0}회 × 300 ms/회 (5G NR 실측 200–400 ms 중간값)<br />
            HIT = Σ<sub>e</sub> t<sub>ho</sub>(e)·𝟙(HO)<br />
            출처: IEEE doc 10320318, 2023 §III.B<br />
            목표: &lt;50 ms (DAPS, 3GPP TS 38.300 §9.2.6)
          </div>
        </Card>
        <Card title="URLLC 준수율" en="URLLC Compliance">
          <div style={{ fontSize: 28, fontWeight: 700, color: urllc != null && urllc >= 0.9 ? 'var(--good)' : 'var(--warn)', marginBottom: 4 }}>
            {urllc != null ? (urllc * 100).toFixed(1) : '—'}<span style={{ fontSize: 14, fontWeight: 400 }}>%</span>
          </div>
          <div className="muted" style={{ fontSize: 11, lineHeight: 1.6 }}>
            P(L ≤ 10 ms) — 구간 중 지연 10ms 이하 비율<br />
            기준: URLLC ≤ 10 ms (3GPP TS 22.261 §7.2)<br />
            ※ 고신뢰 저지연 서비스 적합성 지표
          </div>
        </Card>
      </div>

      {/* ── Jain FI ── */}
      {jfi != null && (() => {
        return (
          <Card title="Jain 공정성 지수" en="Jain's Fairness Index"
            right={<Chip tone={jfiTone}>{jfiLabel} {jfi.toFixed(4)}</Chip>}
            style={{ marginBottom: 16 }}>
            <div className="row gap16" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div style={{ flex: '1 1 180px' }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: `var(--${jfiTone || 'ink-2'})`, marginBottom: 4 }}>
                  {jfi.toFixed(4)}
                </div>
                <div className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>
                  범위: [1/n, 1] — 1.0 = 완전 공정
                </div>
              </div>
              <div style={{ flex: '2 1 220px', fontSize: 11, lineHeight: 1.6, background: 'var(--surface-2)', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}>
                <div style={{ color: 'var(--brand-2)', marginBottom: 8, display: 'inline-flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, fontSize: 13 }}>
                  <i>J</i> =
                  <MathFrac
                    n={<>(Σ<sub><i>i</i></sub> <i>ρ</i><sub><i>i</i></sub>)<sup>2</sup></>}
                    d={<><i>n</i> · Σ<sub><i>i</i></sub> <i>ρ</i><sub><i>i</i></sub><sup>2</sup></>}
                  />
                </div>
                <div className="muted"><i>ρ</i><sub><i>i</i></sub> = BS <i>i</i> 부하율, <i>n</i> = BS 수</div>
                <div className="muted" style={{ marginTop: 4 }}>
                  출처: Jain, Chiu, Hawe, <i>DEC-TR-301</i>, 1984 §3.1
                </div>
              </div>
            </div>
          </Card>
        );
      })()}
    </>
  );
}


// ── Batch sub-section (used inside Compare) ────────────────────────────────

function SectionBatch({ mode }) {
  const [scenarioBatches, setScenarioBatches] = useState(() => loadScenarioBatches());
  const [selectedBatch,   setSelectedBatch]   = useState(0);
  const [batchAiLoading,  setBatchAiLoading]  = useState(false);
  const [batchAiError,    setBatchAiError]    = useState(null);
  const [batchAiSections, setBatchAiSections] = useState([]);
  const [batchAiRevealed, setBatchAiRevealed] = useState(0);
  const [batchAiProvider, setBatchAiProvider] = useState(null);
  const [selectedProv,    setSelectedProv]    = useState('');

  useEffect(() => {
    const t = setInterval(() => setScenarioBatches(loadScenarioBatches()), 2000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { setBatchAiSections([]); setBatchAiError(null); setBatchAiRevealed(0); }, [selectedBatch]);

  const reversed     = [...scenarioBatches].reverse();
  const currentBatch = reversed[selectedBatch] || null;

  function removeBatch(ri) {
    const origIdx = scenarioBatches.length - 1 - ri;
    const next = scenarioBatches.filter((_, i) => i !== origIdx);
    setScenarioBatches(next); saveScenarioBatches(next); setSelectedBatch(0);
  }

  async function runBatchAI(batch) {
    setBatchAiLoading(true); setBatchAiError(null); setBatchAiSections([]); setBatchAiRevealed(0); setBatchAiProvider(null);
    try {
      const res = await fetch(`${API_BASE}/api/analysis/llm/batch-compare`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: batch.label, results: batch.results, provider: selectedProv || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const sections = data.sections || [];
      setBatchAiSections(sections); setBatchAiProvider(data.provider || null); setBatchAiLoading(false);
      sections.forEach((_, i) => setTimeout(() => setBatchAiRevealed(i + 1), i * 450));
    } catch (e) { setBatchAiLoading(false); setBatchAiError(e.message || '오류'); }
  }

  if (scenarioBatches.length === 0) return null;

  return (
    <Card title="시나리오 배치 비교" en="Batch compare" right={<Chip>{scenarioBatches.length}개 배치</Chip>} style={{ marginBottom: 16 }}>
      <div className="row gap8 wrap" style={{ marginBottom: 14 }}>
        {reversed.map((b, i) => {
          const kind = inferBatchKind(b);
          const done = (b.results || []).filter(r => r.status === 'done').length;
          const total = b.results?.length ?? 0;
          return (
            <div key={b.batch_id} onClick={() => setSelectedBatch(i)} style={{ flex: '1 1 200px', maxWidth: 270, cursor: 'pointer', padding: '10px 12px', borderRadius: 10, background: i === selectedBatch ? 'var(--brand-tint)' : 'var(--surface-2)', border: `1.5px solid ${i === selectedBatch ? 'var(--brand-2)' : 'var(--border)'}`, transition: 'border-color .15s' }}>
              <div className="row between" style={{ marginBottom: 5 }}>
                <Chip tone={kind.tone}>{kind.text}</Chip>
                <button className="btn icon sm" onClick={e => { e.stopPropagation(); removeBatch(i); }}>✕</button>
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.label || '(레이블 없음)'}</div>
              <div className="muted" style={{ fontSize: 10.5, marginTop: 3 }}>{done}/{total} 성공</div>
            </div>
          );
        })}
      </div>

      {currentBatch && (() => {
        const doneResults = (currentBatch.results || []).filter(r => r.status === 'done');
        const isRl = doneResults.some(r => r.mode === 'rl_episode');
        const sweepData = parseSweepBatch(currentBatch);
        let bestIdx = -1;
        if (doneResults.length > 0) {
          bestIdx = doneResults.reduce((bi, r, i) => {
            const cur  = r.mode === 'rl_episode' ? (r.mean_reward ?? r.total_reward ?? -Infinity) : -(r.route_cost_result?.total_cost ?? Infinity);
            const best = doneResults[bi];
            const bv   = best.mode === 'rl_episode' ? (best.mean_reward ?? best.total_reward ?? -Infinity) : -(best.route_cost_result?.total_cost ?? Infinity);
            return cur > bv ? i : bi;
          }, 0);
        }
        const bestId = bestIdx >= 0 ? doneResults[bestIdx].id : null;

        return (
          <>
            {sweepData ? (
              <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                <div>
                  <div className="muted" style={{ fontSize: 10.5, marginBottom: 5 }}>총 비용 추이 (파라미터 스윕)</div>
                  <LineChart
                    series={[sweepData.points.map(p => p.totalCost ?? 0)]}
                    height={150}
                    labels={sweepData.points.map(p => String(p.x))}
                    colors={['var(--brand-2)']}
                    yLabel="총 비용 (cost)"
                    xLabel={sweepData.paramLabel}
                  />
                </div>
                <div>
                  <div className="muted" style={{ fontSize: 10.5, marginBottom: 5 }}>평균 지연 추이 (파라미터 스윕)</div>
                  <LineChart
                    series={[sweepData.points.map(p => p.avgLatency ?? 0)]}
                    height={150}
                    yUnit="ms"
                    labels={sweepData.points.map(p => String(p.x))}
                    colors={['var(--warn)']}
                    yLabel="평균 지연 (ms)"
                    xLabel={sweepData.paramLabel}
                  />
                </div>
              </div>
            ) : !isRl ? (
              <div style={{ marginBottom: 14 }}><BarChart items={doneResults.map(r => ({ label: r.label || r.id, value: r.route_cost_result?.total_cost ?? 0, display: `비용 ${(r.route_cost_result?.total_cost ?? 0).toFixed(1)}`, color: 'var(--brand-2)' }))} /></div>
            ) : null}

            <div className="tbl-wrap">
              <table className="tbl">
                <thead><tr><th>시나리오</th><th>모드</th><th className="r">차량/정책</th><th className="r">seed</th><th className="r">결과</th></tr></thead>
                <tbody>
                  {currentBatch.results.map((r, i) => {
                    const isRlRow = r.mode === 'rl_episode';
                    const isBest  = r.status === 'done' && r.id === bestId;
                    return (
                      <tr key={i} style={isBest ? { background: 'var(--good-tint)' } : {}}>
                        <td style={{ borderLeft: `3px solid ${r.status !== 'done' ? 'var(--bad)' : isRlRow ? 'var(--good)' : 'var(--brand-2)'}`, paddingLeft: 9 }}>{r.label || r.id}</td>
                        <td><Chip tone={r.status === 'done' ? (isRlRow ? 'good' : 'brand') : 'bad'}>{r.mode}</Chip></td>
                        <td className="r"><span className="num">{isRlRow ? (r.policy || '—') : (r.vehicle_count ?? '—')}</span></td>
                        <td className="r"><span className="num muted">{r.seed ?? '—'}</span></td>
                        <td className="r">
                          {r.status !== 'done' ? <span style={{ color: 'var(--bad)' }}>{r.error || '실패'}</span>
                            : isRlRow ? <span className="num">reward {(r.mean_reward ?? r.total_reward ?? 0).toFixed(2)}</span>
                            : <span className="num">비용 {(r.route_cost_result?.total_cost ?? 0).toFixed(2)} · {(r.route_cost_result?.avg_latency_ms ?? 0).toFixed(1)} ms</span>}
                          {isBest && <Chip tone="good" style={{ marginLeft: 5, fontSize: 9 }}>최적</Chip>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row gap8" style={{ marginTop: 12 }}>
              <button className="btn sm primary" disabled={batchAiLoading} onClick={() => runBatchAI(currentBatch)}>
                {batchAiLoading ? <><Icon.reset size={12} className="spin" /> 분석 중…</> : <><Icon.spark size={12} /> 배치 비교 AI 분석</>}
              </button>
              {batchAiProvider && <Chip style={{ color: PROVIDER_LABELS[batchAiProvider]?.color }}>{PROVIDER_LABELS[batchAiProvider]?.name || batchAiProvider}</Chip>}
            </div>
            {batchAiError && <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--bad)', background: 'var(--bad-tint)', padding: '7px 10px', borderRadius: 8 }}>{batchAiError}</div>}
            {batchAiRevealed > 0 && (
              <div className="col gap10" style={{ marginTop: 12 }}>
                {batchAiSections.slice(0, batchAiRevealed).map((t, i) => (
                  <div key={i} className="fade row gap10" style={{ padding: '11px 13px', background: 'var(--surface-2)', borderRadius: 9, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                    <span className="num" style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-2)', flexShrink: 0 }}>{String(i + 1).padStart(2, '0')}</span>
                    <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        );
      })()}
    </Card>
  );
}

// ── Export section ─────────────────────────────────────────────────────────

function SectionExport({ bundle, simLogs, simHistory, simConfig, networkTelemetry, routeEdges, mode }) {
  const [csvFlash,    setCsvFlash]    = useState({});
  const [reportFlash, setReportFlash] = useState({});
  const [jsonFlash,   setJsonFlash]   = useState(false);
  const [docxError,   setDocxError]   = useState(null);
  const [scenarioBatches, setScenarioBatches] = useState(() => loadScenarioBatches());
  useEffect(() => {
    const t = setInterval(() => setScenarioBatches(loadScenarioBatches()), 2000);
    return () => clearInterval(t);
  }, []);

  const available = !!bundle?.available;

  function flash(key) { setCsvFlash(p => ({ ...p, [key]: true })); setTimeout(() => setCsvFlash(p => ({ ...p, [key]: false })), 2200); }

  async function downloadCsv(endpoint, filename) {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || '다운로드 실패'); return; }
      const blob = await res.blob();
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename; a.click();
      flash(filename);
    } catch (e) { alert(e.message || '다운로드 실패'); }
  }

  async function downloadEventLogs() {
    try {
      const res = await fetch(`${API_BASE}/api/export/csv/event-logs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ logs: simLogs ?? [], sim_elapsed_s: 0 }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || '실패'); return; }
      const blob = await res.blob();
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'event_logs.csv'; a.click();
      flash('event_logs.csv');
    } catch (e) { alert(e.message || '실패'); }
  }

  async function downloadBatchCompare() {
    if (!scenarioBatches.length) { alert('저장된 배치가 없습니다.'); return; }
    try {
      const res = await fetch(`${API_BASE}/api/export/csv/batch-compare`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batches: scenarioBatches }),
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || '실패'); return; }
      const blob = await res.blob();
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'batch_compare.csv'; a.click();
      flash('batch_compare.csv');
    } catch (e) { alert(e.message || '실패'); }
  }

  async function downloadReport(format) {
    setDocxError(null);
    const [fmt, qs] = format.split('?');
    try {
      const url = `${API_BASE}/api/export/report/${fmt}${qs ? '?' + qs : ''}`;
      const res = await fetch(url);
      if (res.status === 501) {
        const d = await res.json().catch(() => ({}));
        setDocxError(d.detail || 'python-docx가 설치되어 있지 않습니다.');
        return;
      }
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || '실패'); return; }
      const blob = await res.blob();
      const ext = fmt === 'markdown' ? 'md' : fmt === 'html' ? 'html' : fmt === 'docx' ? 'docx' : 'json';
      const langSuffix = qs?.includes('lang=ko') ? '_KO' : qs?.includes('lang=en') ? '_EN' : '';
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `v2x_report${langSuffix}.${ext}`; a.click();
      setReportFlash(p => ({ ...p, [format]: true }));
      setTimeout(() => setReportFlash(p => ({ ...p, [format]: false })), 2200);
    } catch (e) { alert(e.message || '실패'); }
  }

  function downloadFullJson() {
    const payload = {
      exported_at: new Date().toISOString(),
      report_bundle: bundle ?? null,
      sim_logs: simLogs ?? [],
      sim_history: simHistory ?? [],
      route_edges: routeEdges ?? null,
      network_telemetry: networkTelemetry ?? null,
      scenario_batches: scenarioBatches ?? [],
      sim_config: simConfig ?? null,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'v2x_full_report.json'; a.click();
    setJsonFlash(true); setTimeout(() => setJsonFlash(false), 2200);
  }

  const CSV_EXPORTS = [
    { key: 'run_summary',       label: 'run_summary.csv',       endpoint: '/api/export/csv/run-summary',       desc: '핵심 KPI 1행 요약',         needs: available },
    { key: 'algorithm_compare', label: 'algorithm_compare.csv', endpoint: '/api/export/csv/algorithm-compare', desc: '알고리즘별 전체 메트릭',     needs: available && !!bundle?.algorithm_compare?.length },
    { key: 'per_edge',          label: 'per_edge_metrics.csv',  endpoint: '/api/export/csv/per-edge',          desc: '구간별 상세 비용 데이터',    needs: available && !!bundle?.per_edge_metrics?.length },
    { key: 'per_bs',            label: 'per_bs_metrics.csv',    endpoint: '/api/export/csv/per-bs',            desc: 'BS/RSU 노드별 메트릭',      needs: available && !!bundle?.per_bs_metrics?.length },
    { key: 'scenario_metadata', label: 'scenario_metadata.csv', endpoint: '/api/export/csv/scenario-metadata', desc: '시뮬레이션 설정 및 식별자',  needs: true },
  ];

  return (
    <>
      {/* ── structured CSV exports ── */}
      <Card title="CSV 내보내기" en="CSV export" style={{ marginBottom: 16 }}>
        {!available && <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>시뮬레이션을 먼저 실행하면 데이터가 채워집니다.</div>}
        <div className="col gap8">
          {CSV_EXPORTS.map(({ key, label, endpoint, desc, needs }) => (
            <div key={key} className="row between" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</div>
                <div className="muted" style={{ fontSize: 11 }}>{desc}</div>
              </div>
              <button className={'btn sm' + (csvFlash[label] ? ' good' : '')} disabled={!needs} onClick={() => downloadCsv(endpoint, label)}>
                {csvFlash[label] ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 다운로드</>}
              </button>
            </div>
          ))}
          {/* event_logs — client-side POST */}
          <div className="row between" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>event_logs.csv</div>
              <div className="muted" style={{ fontSize: 11 }}>시뮬레이션 이벤트 로그 ({simLogs?.length ?? 0}건)</div>
            </div>
            <button className={'btn sm' + (csvFlash['event_logs.csv'] ? ' good' : '')} disabled={!simLogs?.length} onClick={downloadEventLogs}>
              {csvFlash['event_logs.csv'] ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 다운로드</>}
            </button>
          </div>
          {/* batch_compare — client-side POST */}
          <div className="row between" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>batch_compare.csv</div>
              <div className="muted" style={{ fontSize: 11 }}>시나리오 배치 비교 ({scenarioBatches.length}개 배치)</div>
            </div>
            <button className={'btn sm' + (csvFlash['batch_compare.csv'] ? ' good' : '')} disabled={!scenarioBatches.length} onClick={downloadBatchCompare}>
              {csvFlash['batch_compare.csv'] ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 다운로드</>}
            </button>
          </div>
        </div>
      </Card>

      {/* ── Structured report exports ── */}
      <Card title="보고서 내보내기" en="Report export" style={{ marginBottom: 16 }}>
        {!available && <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>시뮬레이션을 먼저 실행하면 보고서를 생성할 수 있습니다.</div>}
        <div className="col gap8">
          {[
            { fmt: 'markdown', label: 'Markdown 보고서', desc: '논문·GitHub 호환 — 모든 10개 섹션 포함', ext: '.md' },
            { fmt: 'html',     label: 'HTML 보고서',     desc: '독립형 HTML — 브라우저 열람·인쇄 가능', ext: '.html' },
            { fmt: 'json',     label: '보고서 JSON',     desc: '구조화된 ReportDocument 모델 (Python/R 분석용)', ext: '.json' },
          ].map(({ fmt, label, desc, ext }) => (
            <div key={fmt} className="row between" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}<span className="muted" style={{ fontWeight: 400, marginLeft: 6, fontSize: 11 }}>{ext}</span></div>
                <div className="muted" style={{ fontSize: 11 }}>{desc}</div>
              </div>
              <button className={'btn sm' + (reportFlash[fmt] ? ' good' : '')} disabled={!available} onClick={() => downloadReport(fmt)}>
                {reportFlash[fmt] ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 다운로드</>}
              </button>
            </div>
          ))}
          {/* DOCX — Korean + English (dual-language academic format) */}
          <div style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>DOCX 학술 보고서<span className="muted" style={{ fontWeight: 400, marginLeft: 6, fontSize: 11 }}>.docx</span></div>
              <div className="muted" style={{ fontSize: 11 }}>논문/연구보고서 형식 — Abstract·수식·참고문헌 포함 · python-docx 필요</div>
            </div>
            <div className="row gap8">
              <button className={'btn sm' + (reportFlash['docx?lang=ko'] ? ' good' : '')} disabled={!available} onClick={() => downloadReport('docx?lang=ko')}>
                {reportFlash['docx?lang=ko'] ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 워드 (한국어)</>}
              </button>
              <button className={'btn sm' + (reportFlash['docx?lang=en'] ? ' good' : '')} disabled={!available} onClick={() => downloadReport('docx?lang=en')}>
                {reportFlash['docx?lang=en'] ? <><Icon.check size={12} /> Saved</> : <><Icon.download size={12} /> Word (English)</>}
              </button>
            </div>
          </div>
          {docxError && (
            <div style={{ fontSize: 11, color: 'var(--bad)', padding: '6px 10px', borderRadius: 6, background: 'var(--surface-2)', border: '1px solid var(--bad)' }}>
              DOCX: {docxError}
            </div>
          )}
        </div>

        {/* full JSON export — Pro only */}
        {mode === 'pro' && (
          <div className="row between" style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)', marginTop: 8 }}>
            <div>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>전체 JSON 내보내기</div>
              <div className="muted" style={{ fontSize: 11 }}>시계열·배치·설정 원시 데이터 전체 (외부 분석 도구용)</div>
            </div>
            <button className={'btn sm' + (jsonFlash ? ' good' : '')} onClick={downloadFullJson}>
              {jsonFlash ? <><Icon.check size={12} /> 저장됨</> : <><Icon.download size={12} /> 다운로드</>}
            </button>
          </div>
        )}
      </Card>
    </>
  );
}

// ── Benchmark / Academic Comparison Sheet ─────────────────────────────────

const BS_ALGO_LABELS = {
  rl_bs_placement:       'RL 기지국 배치 최적화 (GNN-MAML)',
  lowest_latency_bs:     'Lowest Latency BS',
  nearest_bs:            'Nearest BS (위치 기반)',
  load_balanced_bs:      'Load Balanced BS',
  highest_confidence_bs: 'Highest Confidence BS',
};

// BS/Route 알고리즘 중 "제안 방법" 키 목록
const PROPOSED_BS_ALGOS    = new Set(['rl_bs_placement']);
const PROPOSED_ROUTE_ALGOS = new Set(['rl_routing']);
const PROPOSED_ALLOC_ALGOS = new Set(['traffic_aware_allocation']);

const ALLOC_ALGO_LABELS = {
  traffic_aware_allocation:      'Traffic-Aware 할당 (RL 기반)',
  equal_allocation:              'Equal Allocation (기준선)',
  load_balancing_allocation:     'Load Balancing',
  latency_minimizing_allocation: 'Latency Minimizing',
  priority_based_allocation:     'Priority-Based',
  lookahead_resource_allocation: 'Look-Ahead',
};

// rowCategories: 각 행이 'proposed' | 'baseline' | null
function BenchmarkTablePanel({ id, title, cite, headers, rows, rowCategories, lowerBetter, copiedKey, onCopy }) {
  // best-value 계산 시 제안 방법 행 제외하지 않음 (모든 행 포함)
  const bests = headers.slice(1).map((_, ci) => {
    const lb = lowerBetter?.[ci] ?? true;
    const vals = rows.map(r => { const v = parseFloat(r[ci + 1]); return isNaN(v) ? null : v; }).filter(v => v !== null);
    if (!vals.length) return null;
    return lb ? Math.min(...vals) : Math.max(...vals);
  });
  const isEmpty = rows.length === 0;

  // TSV 복사 시 "구분" 컬럼도 포함
  function handleCopyWithCategory() {
    const fullHeaders = rowCategories ? ['구분', ...headers] : headers;
    const fullRows = rows.map((r, ri) => {
      const cat = rowCategories?.[ri];
      const catLabel = cat === 'proposed' ? '제안 방법' : cat === 'baseline' ? '비교군' : '';
      return rowCategories ? [catLabel, ...r] : r;
    });
    onCopy(id, fullHeaders, fullRows);
  }

  return (
    <div>
      {(title || cite) && (
        <div className="row between" style={{ marginBottom: 6 }}>
          <div>
            {title && <span style={{ fontWeight: 600, fontSize: 12.5 }}>{title}</span>}
            {cite && <span style={{ fontSize: 10, color: 'var(--ink-4)', marginLeft: 8, fontStyle: 'italic' }}>ref: {cite}</span>}
          </div>
        </div>
      )}
      <div className="row" style={{ justifyContent: 'flex-end', marginBottom: 6 }}>
        <button
          className={'btn sm' + (copiedKey === id ? ' good' : '')}
          disabled={isEmpty}
          onClick={handleCopyWithCategory}
          title="탭 구분 텍스트로 복사 — Excel/Word 붙여넣기 가능">
          {copiedKey === id
            ? <><Icon.check size={12} /> 복사됨</>
            : <><Icon.download size={12} /> 표 복사 (TSV)</>}
        </button>
      </div>
      {isEmpty ? (
        <div style={{ padding: '16px 12px', background: 'var(--surface-2)', borderRadius: 8, textAlign: 'center', fontSize: 11.5, color: 'var(--ink-4)', border: '1px solid var(--border)' }}>
          비교 실행 버튼을 눌러 데이터를 생성하세요.
        </div>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                {rowCategories && <th style={{ width: 70 }}>구분</th>}
                {headers.map((h, i) => <th key={i} className={i > 0 ? 'r' : ''}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => {
                const cat = rowCategories?.[ri];
                const isProposed = cat === 'proposed';
                return (
                  <tr key={ri} style={isProposed ? { background: 'var(--brand-tint)', fontWeight: 700 } : {}}>
                    {rowCategories && (
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {isProposed
                          ? <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 4, background: 'var(--brand-2)', color: '#fff', fontWeight: 700 }}>제안</span>
                          : <span style={{ fontSize: 10, color: 'var(--ink-4)' }}>비교군</span>}
                      </td>
                    )}
                    {row.map((cell, ci) => {
                      if (ci === 0) return <td key={ci}><span style={{ fontSize: 11.5, fontWeight: isProposed ? 700 : 500 }}>{cell}</span></td>;
                      const numVal = parseFloat(cell);
                      const best = bests[ci - 1];
                      const isBest = best !== null && !isNaN(numVal) && Math.abs(numVal - best) < 0.005;
                      return (
                        <td key={ci} className="r">
                          <span className="num" style={{ fontWeight: isBest ? 700 : 400, color: isBest ? 'var(--good)' : 'inherit' }}>
                            {cell}
                          </span>
                          {isBest && <span style={{ marginLeft: 2, fontSize: 9.5, color: 'var(--good)', verticalAlign: 'super' }}>★</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SectionBenchmarkSheet({ bundle, simConfig, mode }) {
  const [cmpData, setCmpData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [copiedKey, setCopiedKey] = useState(null);
  const [saResult, setSaResult] = useState(null);
  const [saError, setSaError] = useState(null);
  const pollRef = useRef(null);

  function fetchCmp() {
    fetch(`${API_BASE}/api/route/compare-algorithms`)
      .then(r => r.json())
      .then(d => setCmpData(d?.status && d.status !== 'idle' ? d : null))
      .catch(() => {});
  }
  useEffect(() => { fetchCmp(); }, []);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function runComparison() {
    setLoading(true);
    setSaError(null);

    // Fire SA comparison in parallel (uses cached edge_data from last simulation)
    const netMode = simConfig?.policy_options?.network_mode || '5G';
    const trafficPeriod = simConfig?.policy_options?.traffic_time_period || 'peak';
    fetch(`${API_BASE}/api/placement/compare-with-sa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ network_mode: netMode, traffic_time_period: trafficPeriod, n_greedy: 2, n_random: 2, sa_iter: 2000 }),
    })
      .then(r => r.json())
      .then(d => { if (d && !d.detail) setSaResult(d); else setSaError(d?.detail || 'SA 비교 실패'); })
      .catch(e => setSaError(e.message));

    fetch(`${API_BASE}/api/route/compare-algorithms`, { method: 'POST' })
      .then(() => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = setInterval(() => {
          fetch(`${API_BASE}/api/route/compare-algorithms`)
            .then(r => r.json())
            .then(d => {
              if (d.status !== 'running') {
                clearInterval(pollRef.current);
                pollRef.current = null;
                setCmpData(d);
                setLoading(false);
              }
            }).catch(() => {});
        }, 1500);
      }).catch(() => setLoading(false));
  }

  function handleCopy(key, headers, rows) {
    const lines = [headers.join('\t'), ...rows.map(r => r.join('\t'))];
    try { navigator.clipboard.writeText(lines.join('\n')); } catch (_) {}
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2200);
  }

  const algoRows  = bundle?.algorithm_compare || [];
  const bySel     = cmpData?.by_bs_selection || {};
  const byAlloc   = cmpData?.by_allocation   || {};
  const perBsAll  = bundle?.per_bs_metrics   || [];
  const bsNodes   = perBsAll.filter(n => (n.node_type || 'bs').toLowerCase() !== 'rsu');
  const rsuNodes  = perBsAll.filter(n => (n.node_type || '').toLowerCase() === 'rsu');
  const hasData   = cmpData?.status === 'done';

  const statusText = loading ? '비교 실행 중…'
    : hasData  ? `완료 (${new Date((cmpData?.generated_at ?? 0) * 1000).toLocaleTimeString()})`
    : '실행 전 — 버튼을 누르면 동일 환경에서 모든 알고리즘을 평가합니다';

  // ① 경로 최적화 ─── proposed: rl_routing / baseline: 나머지 ─────────────
  const routeHeaders = ['경로 알고리즘', '총 비용', '평균 지연 (ms)', '최대 지연 (ms)', '핸드오버 (회)', 'PRR (%)', '커버리지 (%)'];
  const routeLB      = [true, true, true, true, false, false];

  const _rlRouteInAlgos = algoRows.some(r => r.algorithm === 'rl_routing');
  const _allRouteRows   = _rlRouteInAlgos ? algoRows : [
    // RL 결과가 없을 경우 플레이스홀더 행을 맨 앞에 삽입
    { algorithm: 'rl_routing', _placeholder: true },
    ...algoRows,
  ];
  const routeRows       = _allRouteRows.map(r => [
    r._placeholder ? 'RL 경로 최적화 (GNN-MAML)' : algoLabel(r.algorithm),
    r._placeholder ? '—' : (r.total_cost          != null ? r.total_cost.toFixed(2)                        : '—'),
    r._placeholder ? '—' : (r.average_latency_ms  != null ? r.average_latency_ms.toFixed(1)               : '—'),
    r._placeholder ? '—' : (r.max_latency_ms      != null ? r.max_latency_ms.toFixed(1)                   : '—'),
    r._placeholder ? '—' : (r.handover_count      != null ? String(r.handover_count)                      : '—'),
    r._placeholder ? '—' : (r.prr_approx          != null ? (r.prr_approx * 100).toFixed(1)              : '—'),
    r._placeholder ? '—' : (r.disconnection_ratio != null ? ((1 - r.disconnection_ratio) * 100).toFixed(1) : '—'),
  ]);
  const routeCategories = _allRouteRows.map(r =>
    (r.algorithm === 'rl_routing' || r._placeholder) ? 'proposed' : 'baseline'
  );

  // ② 기지국 선택·배치 ─── proposed: rl_bs_placement / baseline: 나머지 ──
  const bsHeaders = ['기지국 선택/배치 알고리즘', '평균 지연 (ms)', '총 비용', '핸드오버 (회)', '커버리지 위험 (%)'];
  const bsLB      = [true, true, true, true];

  // rl_bs_placement를 맨 앞으로, 나머지는 비교군으로
  const _bsEntries = Object.entries(bySel);
  const _rlBsEntry  = _bsEntries.find(([id]) => id === 'rl_bs_placement');
  const _bsBaselines = _bsEntries.filter(([id]) => id !== 'rl_bs_placement');
  const _bsOrdered   = _rlBsEntry
    ? [_rlBsEntry, ..._bsBaselines]
    : [['rl_bs_placement', { _placeholder: true }], ..._bsBaselines];

  const bsRows = _bsOrdered.map(([id, v]) => [
    BS_ALGO_LABELS[id] || id,
    v._placeholder ? '—' : (v.avg_latency_ms != null ? v.avg_latency_ms.toFixed(1)        : '—'),
    v._placeholder ? '—' : (v.total_cost     != null ? v.total_cost.toFixed(2)            : '—'),
    v._placeholder ? '—' : (v.handover_count != null ? String(v.handover_count)           : '—'),
    v._placeholder ? '—' : (v.coverage_risk  != null ? (v.coverage_risk * 100).toFixed(1) : '—'),
  ]);
  const bsCategories = _bsOrdered.map(([id]) =>
    PROPOSED_BS_ALGOS.has(id) ? 'proposed' : 'baseline'
  );

  // ③ 자원할당 ─── proposed: traffic_aware_allocation ─────────────────────
  const allocHeaders = ['자원할당 알고리즘', 'BS 활용률 (%)', '과부하 BS (개)', '자원 결손 (RB)'];
  const allocLB      = [false, true, true];

  const _allocEntries  = Object.entries(byAlloc);
  const _allocProposed = _allocEntries.filter(([id]) => PROPOSED_ALLOC_ALGOS.has(id));
  const _allocBaseline = _allocEntries.filter(([id]) => !PROPOSED_ALLOC_ALGOS.has(id));
  const _allocOrdered  = [..._allocProposed, ..._allocBaseline];

  const allocRows = _allocOrdered.map(([id, v]) => [
    ALLOC_ALGO_LABELS[id] || id,
    v.total_utilization   != null ? (v.total_utilization * 100).toFixed(1) : '—',
    v.overloaded_bs_count != null ? String(v.overloaded_bs_count)          : '—',
    v.total_deficit_rb    != null ? v.total_deficit_rb.toFixed(1)          : '—',
  ]);
  const allocCategories = _allocOrdered.map(([id]) =>
    PROPOSED_ALLOC_ALGOS.has(id) ? 'proposed' : 'baseline'
  );

  // ④ 기지국·RSU 배치 ─────────────────────────────────────────────────────
  function makeGroupRow(label, nodes) {
    if (!nodes.length) return null;
    const n       = nodes.length;
    const avgCovR = nodes.reduce((s, x) => s + (x.coverage_radius_m ?? 400), 0) / n;
    const avgLoad = nodes.reduce((s, x) => s + (x.load_ratio ?? 0), 0) / n;
    const latNodes = nodes.filter(x => x.avg_latency_on_route_ms != null);
    const avgLat   = latNodes.length
      ? latNodes.reduce((s, x) => s + x.avg_latency_on_route_ms, 0) / latNodes.length
      : null;
    const totalEdges = nodes.reduce((s, x) => s + (x.affected_edge_count ?? 0), 0);
    return [
      label,
      String(n),
      avgCovR.toFixed(0),
      (avgLoad * 100).toFixed(1),
      avgLat !== null ? avgLat.toFixed(1) : '—',
      String(totalEdges),
    ];
  }
  const placementHeaders = ['배치 유형', '수량 (개)', '커버리지 반경 (m)', '평균 부하율 (%)', '평균 지연 (ms)', '담당 구간 수'];
  const placementLB      = [null, false, true, true, false];
  const placementRows    = [
    makeGroupRow('기지국 (BS, Uu 인터페이스)', bsNodes),
    makeGroupRow('노변기지국 (RSU, PC5 사이드링크)', rsuNodes),
  ].filter(Boolean);

  return (
    <div>
      {/* ── 헤더 배너 ── */}
      <div style={{
        padding: '16px 18px', borderRadius: 10, marginBottom: 20,
        background: 'var(--brand-tint)', border: '1px solid var(--brand-tint2)',
      }}>
        <div className="row between" style={{ alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 260px' }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 1.2, color: 'var(--brand-2)', fontWeight: 700, marginBottom: 4 }}>
              Academic Benchmark
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>비교군 시트</div>
            <div style={{ fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.65 }}>
              동일 환경(구역·경로·BS 배치)에서 4개 최적화 차원의 알고리즘을 비교합니다.<br />
              <span style={{ color: 'var(--brand-2)', fontWeight: 600 }}>★</span>
              {' '}= 해당 지표 최우수값 &nbsp;·&nbsp; 각 표의 "표 복사(TSV)"로 Word·Excel 직접 붙여넣기 가능
            </div>
          </div>
          <div className="col gap8" style={{ alignItems: 'flex-end', flexShrink: 0 }}>
            <button className={'btn' + (loading ? ' disabled' : '')} onClick={runComparison} disabled={loading}>
              {loading
                ? <><Icon.reset size={13} className="spin" /> 비교 실행 중…</>
                : <><Icon.chart size={13} /> 비교군 실행</>}
            </button>
            <div style={{ fontSize: 10, color: 'var(--ink-4)', textAlign: 'right' }}>
              {statusText}
            </div>
          </div>
        </div>
      </div>

      {/* ── ① 경로 최적화 ── */}
      <Card title="① 경로 최적화 비교" en="Route Optimization" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 10, lineHeight: 1.6 }}>
          동일 기지국 배치·자원할당 조건에서 <b>경로탐색 알고리즘만</b> 변경. 평가 기준: 총 비용 · E2E 지연 · PRR.
          {!_rlRouteInAlgos && (
            <span style={{ display: 'block', marginTop: 4, color: 'var(--warn)', fontWeight: 600 }}>
              ※ RL 경로 최적화 결과를 포함하려면 시뮬레이션 탭 → 시트에서 RL 알고리즘 선택 후 <b>전체 비교 실행</b>하세요.
            </span>
          )}
          <span style={{ float: 'right', fontSize: 10, fontStyle: 'italic', color: 'var(--ink-4)' }}>
            ref: Hung et al. IEEE VTM 2017; Ali et al. IEEE Access 2021
          </span>
        </div>
        <BenchmarkTablePanel
          id="route" headers={routeHeaders} rows={routeRows} rowCategories={routeCategories} lowerBetter={routeLB}
          copiedKey={copiedKey} onCopy={handleCopy} />
      </Card>

      {/* ── ② 기지국 선택·배치 ── */}
      <Card title="② 기지국 배치 최적화 비교" en="BS Placement / Selection" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 10, lineHeight: 1.6 }}>
          동일 경로에서 <b>기지국 선택·배치 정책만</b> 변경.
          {' '}<span style={{ color: 'var(--brand-2)', fontWeight: 600 }}>제안 방법</span>은 GNN-MAML RL 기반 배치 최적화 (학습 전: Lowest Latency 폴백).
          <span style={{ float: 'right', fontSize: 10, fontStyle: 'italic', color: 'var(--ink-4)' }}>
            ref: 3GPP TS 22.186 §5.1; ETSI EN 302 637-2
          </span>
        </div>
        <BenchmarkTablePanel
          id="bs_sel" headers={bsHeaders} rows={bsRows} rowCategories={bsCategories} lowerBetter={bsLB}
          copiedKey={copiedKey} onCopy={handleCopy} />
      </Card>

      {/* ── ③ 자원할당 ── */}
      <Card title="③ 자원할당 최적화 비교" en="Resource Allocation" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 10, lineHeight: 1.6 }}>
          동일 경로·차량 밀도에서 <b>자원할당 알고리즘만</b> 변경.
          {' '}<span style={{ color: 'var(--brand-2)', fontWeight: 600 }}>제안 방법</span>은 RL 기반 Traffic-Aware 할당. 목표: BS 활용률↑ · 과부하↓ · 자원 결손↓.
          <span style={{ float: 'right', fontSize: 10, fontStyle: 'italic', color: 'var(--ink-4)' }}>
            ref: Jain et al. 1984; ETSI TS 102 687 V1.1.1 §5.2
          </span>
        </div>
        <BenchmarkTablePanel
          id="alloc" headers={allocHeaders} rows={allocRows} rowCategories={allocCategories} lowerBetter={allocLB}
          copiedKey={copiedKey} onCopy={handleCopy} />
      </Card>

      {/* ── ④ 기지국·RSU 배치 ── */}
      <Card title="④ 기지국·RSU 배치 분석 (SA 비교)" en="BS / RSU Placement vs SA Optimal" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 10, lineHeight: 1.6 }}>
          현재 배치된 노드를 유형별로 집계하고, SA 최적 배치와 성능을 비교합니다.
          {' '}비교군 실행 버튼을 누르면 SA 비교가 자동으로 함께 실행됩니다.
          <span style={{ float: 'right', fontSize: 10, fontStyle: 'italic', color: 'var(--ink-4)' }}>
            ref: 3GPP TS 22.186 §4.2 (Uu/RSU); ETSI EN 302 637-2 §6 (PC5)
          </span>
        </div>
        {placementRows.length === 0 ? (
          <SectionEmpty msg="시뮬레이션 탭에서 기지국/RSU를 배치하고 시뮬레이션을 실행하세요." />
        ) : (
          <BenchmarkTablePanel
            id="placement" headers={placementHeaders} rows={placementRows} lowerBetter={placementLB}
            copiedKey={copiedKey} onCopy={handleCopy} />
        )}
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginTop: 14 }}>
          <Stat label="기지국 (BS)" icon="antenna" value={bsNodes.length} unit="개" sub="Uu 인터페이스" />
          <Stat label="RSU" icon="route" value={rsuNodes.length} unit="개" sub="PC5 사이드링크" />
          <Stat label="총 커버 구간" icon="check"
            value={perBsAll.reduce((s, n) => s + (n.affected_edge_count ?? 0), 0)} unit="구간" />
          <Stat label="Jain 공정성" icon="chart"
            value={bundle?.run_summary?.jain_fairness_index != null
              ? bundle.run_summary.jain_fairness_index.toFixed(3) : '—'}
            sub="[0,1] → 1=완전 공정 (Jain 1984)" />
        </div>

        {/* SA 비교군 결과 */}
        {saError && (
          <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 8,
            border: '1px solid var(--border)', color: 'var(--warn)', fontSize: 11.5 }}>
            SA 비교 실패: {saError}
          </div>
        )}
        {saResult && (() => {
          const { user, sa_optimal, improvement } = saResult;
          const saRows = [
            ['평균 지연 (ms)', user?.avg_latency_ms, sa_optimal?.avg_latency_ms, 'low'],
            ['P95 지연 (ms)', user?.p95_latency_ms, sa_optimal?.p95_latency_ms, 'low'],
            ['PRR (%)',        user?.prr_pct,        sa_optimal?.prr_pct,        'high'],
            ['미커버 구간 (%)', user?.uncovered_pct, sa_optimal?.uncovered_pct,  'low'],
          ];
          return (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
                SA 최적 배치 vs 현재 배치 비교
                <span style={{ fontWeight: 400, color: 'var(--ink-3)', fontSize: 11, marginLeft: 8 }}>— 동일 BS/RSU 수, SA 2000회 탐색</span>
              </div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th style={{ fontSize: 11 }}>지표</th>
                      <th style={{ fontSize: 11 }}>실험군 (현재 배치)</th>
                      <th style={{ fontSize: 11 }}>비교군 (SA 최적)</th>
                      <th style={{ fontSize: 11 }}>개선</th>
                    </tr>
                  </thead>
                  <tbody>
                    {saRows.map(([label, uv, sv, better]) => {
                      const improved = better === 'low' ? sv < uv : sv > uv;
                      const diff = better === 'low' ? uv - sv : sv - uv;
                      return (
                        <tr key={label}>
                          <td style={{ fontSize: 11.5 }}>{label}</td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>{uv != null ? (+uv).toFixed(2) : '—'}</td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: improved ? 'var(--good)' : 'var(--bad)' }}>
                            {sv != null ? (+sv).toFixed(2) : '—'}
                          </td>
                          <td style={{ fontSize: 11, color: improved ? 'var(--good)' : 'var(--bad)' }}>
                            {diff != null ? (improved ? '▼' : '▲') + Math.abs(diff).toFixed(2) : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {improvement?.sa_cost_improvement_pct != null && (
                <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                  SA 배치 지연 개선율: <b style={{ color: 'var(--good)' }}>{improvement.sa_cost_improvement_pct.toFixed(1)}%</b>
                  {' '}({improvement.sa_n_candidates}개 후보 중 {improvement.sa_iter}회 탐색)
                </div>
              )}
            </div>
          );
        })()}
      </Card>

      {/* ── 논문 인용 가이드 ── */}
      <div style={{
        padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 8,
        border: '1px solid var(--border)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.7,
      }}>
        <div style={{ fontWeight: 700, color: 'var(--ink-2)', marginBottom: 4 }}>논문 인용 가이드</div>
        각 표의 <b>표 복사(TSV)</b>는 탭 구분 텍스트를 클립보드에 복사합니다.
        Word에 직접 붙여넣거나 Excel → LaTeX (booktabs) 변환에 활용하세요.
        비교는 동일 환경·동일 경로 조건에서 수행되므로 <i>controlled variable</i>로 기재 가능합니다.
        <br />
        <span style={{ color: 'var(--ink-4)' }}>
          통제 변수: 시뮬레이션 구역, 출발지·목적지, BS/RSU 배치, 차량 밀도, ITS 시간대
          &nbsp;·&nbsp; 독립 변수: 각 패널의 알고리즘 선택
        </span>
      </div>
    </div>
  );
}

// ── Root ReportTab component ───────────────────────────────────────────────

function ReportTab({ sim, simLogs, vehiclePos, networkTelemetry, routeCoords, routeEdges, simHistory, simConfig, mode }) {
  const [subTab,       setSubTab]       = useState(() => mode === 'lite' ? 'explain' : 'overview');
  const [bundle,       setBundle]       = useState(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError,  setBundleError]  = useState(null);

  // Lite can only see explain (logs + LLM) — reset if mode changes
  useEffect(() => { if (mode === 'lite' && subTab !== 'explain') setSubTab('explain'); }, [mode, subTab]);

  function fetchBundle() {
    setBundleLoading(true); setBundleError(null);
    // Push current sheet's routeEdges into backend _state first so the bundle
    // reflects the active sheet even when multiple sheets have been run.
    const prep = routeEdges
      ? fetch(`${API_BASE}/api/report/use-sheet`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ route_edges: routeEdges, network_telemetry: networkTelemetry || null }),
        }).catch(() => {})
      : Promise.resolve();
    prep.then(() =>
      fetch(`${API_BASE}/api/report/bundle`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(d => { setBundle(d); setBundleLoading(false); })
        .catch(e => { setBundleLoading(false); setBundleError(e.message || '번들 불러오기 실패'); })
    );
  }

  // 초기 로드
  useEffect(() => { fetchBundle(); }, []);

  // 시뮬레이션 종료 시 자동 재조회 — 이전 번들 데이터를 계속 보여주는 문제 방지
  const fetchBundleRef = useRef(null);
  fetchBundleRef.current = fetchBundle;
  const simWasRunningRef = useRef(false);
  useEffect(() => {
    if (simWasRunningRef.current && !sim.running && sim.elapsed > 0) {
      // 시뮬레이션이 종료(도착 또는 정지)된 직후 번들 자동 새로고침
      fetchBundleRef.current?.();
    }
    simWasRunningRef.current = sim.running;
  }, [sim.running, sim.elapsed]);

  const TAB_OPTIONS = mode === 'pro'
    ? [
        { v: 'overview',   label: 'Overview'  },
        { v: 'compare',    label: 'Compare'   },
        { v: 'channel',    label: '채널·공정성' },
        { v: 'benchmark',  label: '비교군 시트' },
        { v: 'export',     label: 'Export'    },
      ]
    : [{ v: 'explain', label: '분석' }];

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Analysis Report</div>
          <h1>분석 보고서 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Report</span></h1>
          <div className="sub">Overview · Compare · 채널 · 비교군 · Export</div>
        </div>
        <div className="row gap8">
          {simLogs?.length > 0 && <Chip tone="good" dot>LIVE</Chip>}
          <button className="btn sm" onClick={fetchBundle} disabled={bundleLoading}>
            {bundleLoading ? <><Icon.reset size={13} className="spin" /> 불러오는 중…</> : <><Icon.reset size={13} /> 새로고침</>}
          </button>
        </div>
      </div>

      <div className="row gap8" style={{ marginBottom: bundleError ? 8 : 20 }}>
        <Seg value={subTab} onChange={setSubTab} options={TAB_OPTIONS} />
        {bundle?.available && <Chip tone="good">데이터 있음</Chip>}
        {bundle && !bundle.available && <Chip tone="">{bundle.reason || '데이터 없음'}</Chip>}
      </div>

      {bundleError && (
        <div style={{ fontSize: 11.5, color: 'var(--bad)', marginBottom: 16, padding: '7px 12px', borderRadius: 8, background: 'var(--bad-tint)', border: '1px solid var(--bad)' }}>
          보고서 번들 불러오기 실패: {bundleError}
        </div>
      )}

      {bundleLoading && !bundle && (
        <div style={{ textAlign: 'center', padding: '40px 16px', color: 'var(--ink-4)' }}>
          <div className="spin" style={{ width: 24, height: 24, border: '2px solid var(--border)', borderTopColor: 'var(--brand-2)', borderRadius: '50%', margin: '0 auto 12px' }} />
          <div style={{ fontSize: 12.5 }}>보고서 데이터 불러오는 중…</div>
        </div>
      )}

      {(!bundleLoading || bundle) && subTab === 'overview' && mode === 'pro' && (
        <SectionOverview bundle={bundle} simLogs={simLogs} vehiclePos={vehiclePos} networkTelemetry={networkTelemetry} sim={sim} />
      )}

      {(!bundleLoading || bundle) && subTab === 'compare' && mode === 'pro' && (
        <>
          <SectionSheetCompare />
          <SectionCompare bundle={bundle} routeCoords={routeCoords} routeEdges={routeEdges} networkTelemetry={networkTelemetry} vehiclePos={vehiclePos} simHistory={simHistory} simConfig={simConfig} mode={mode} />
          <SectionBatch mode={mode} />
        </>
      )}

      {(!bundleLoading || bundle) && subTab === 'channel' && mode === 'pro' && (
        <SectionChannel bundle={bundle} />
      )}

      {(!bundleLoading || bundle) && subTab === 'benchmark' && mode === 'pro' && (
        <SectionBenchmarkSheet bundle={bundle} simConfig={simConfig} mode={mode} />
      )}

      {(!bundleLoading || bundle) && subTab === 'explain' && (
        <SectionExplain bundle={bundle} simLogs={simLogs} mode={mode} simConfig={simConfig} />
      )}

      {subTab === 'export' && mode === 'pro' && (
        <SectionExport bundle={bundle} simLogs={simLogs} simHistory={simHistory} simConfig={simConfig} networkTelemetry={networkTelemetry} routeEdges={routeEdges} mode={mode} />
      )}

    </div>
  );
}

window.ReportTab = ReportTab;
