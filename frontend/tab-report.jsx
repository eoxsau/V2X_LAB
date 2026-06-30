/* ============================================================ Report tab */
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
  vertex:  { name: 'Gemini', color: 'var(--brand-2)' },
  azure:   { name: 'GPT-5.4', color: '#0078d4' },
  bedrock: { name: 'Claude', color: 'var(--brand-2)' },
};

/* ---- ported from tab-routes.jsx ----------------------------- */
const ALT_PATH_COLORS = ['#F6A623', '#A855F7', '#22C1A8', '#E45C8A', '#5B8DEF'];

/* ---- ported from tab-comparison.jsx -------------------------- */
const CMP_METRIC_COLS = [
  { key: 'total_cost',              label: '총 비용',        fmt: v => v.toFixed(2) },
  { key: 'average_latency_ms',      label: '평균 Latency',   fmt: v => v.toFixed(1) + 'ms' },
  { key: 'handover_count',          label: '핸드오버',       fmt: v => v + '회' },
  { key: 'disconnection_ratio',     label: '단절율',         fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'prr_approx',              label: 'PRR(시간가중근사)', fmt: v => (v * 100).toFixed(1) + '%' },
  { key: 'average_bs_load',         label: '평균 BS 부하',   fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'future_connectivity_risk', label: '미래 위험도',   fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'edge_count',              label: '구간 수',        fmt: v => v + '개' },
];

// P50/P90/P95/P99 — 정렬된 숫자 배열에서 선형보간 없는 nearest-rank 백분위수
function percentile(sortedNums, p) {
  if (!sortedNums || sortedNums.length === 0) return null;
  const idx = Math.min(sortedNums.length - 1, Math.ceil((p / 100) * sortedNums.length) - 1);
  return sortedNums[Math.max(0, idx)];
}
const CMP_HISTORY_KEY = 'v2x_run_history';
function loadRunHistory() {
  try { return JSON.parse(localStorage.getItem(CMP_HISTORY_KEY) || '[]'); } catch { return []; }
}
function saveRunHistory(list) {
  try { localStorage.setItem(CMP_HISTORY_KEY, JSON.stringify(list.slice(-20))); } catch {}
}

// 시나리오 어시스턴트 탭의 "시나리오 생성·배치" 모드(Phase 3/4)가 쓰는 키와 동일 —
// 여기서는 읽기만 한다(쓰기는 tab-scenario.jsx의 scbSaveBatches가 담당).
const SCB_BATCH_KEY = 'v2x_scenario_batches';
function loadScenarioBatches() {
  try { return JSON.parse(localStorage.getItem(SCB_BATCH_KEY) || '[]'); } catch { return []; }
}
function saveScenarioBatches(list) {
  try { localStorage.setItem(SCB_BATCH_KEY, JSON.stringify(list)); } catch {}
}

// 배치 레이블 prefix(각 생성처에서 고정 문구로 시작)로 종류를 추정 — 카드에 종류 Chip을 달아
// 스윕/RL비교/시트비교/생성배치를 한눈에 구분할 수 있게 한다.
function inferBatchKind(batch) {
  const label = batch.label || '';
  if (label.startsWith('파라미터 스윕')) return { tone: 'brand', text: '파라미터 스윕' };
  if (label.startsWith('RL 정책 비교')) return { tone: 'good', text: 'RL 정책 비교' };
  if (label.startsWith('시뮬레이션 시트 비교')) return { tone: 'warn', text: '시트 비교' };
  if ((batch.results || []).some(r => r.mode === 'rl_episode')) return { tone: 'good', text: 'RL 배치' };
  return { tone: '', text: '시나리오 배치' };
}

// 배치/실행이력 등 "선택 가능한 카드 묶음" 공통 카드 — 클릭으로 선택, 우상단 ✕로 삭제.
// 기존 테이블 기반 선택 목록(라디오/체크박스 + 테이블 행)을 대체해 한눈에 훑어보기 쉽게 한다.
function PickerCard({ selected, onClick, onRemove, kindChip, title, metaLeft, metaRight }) {
  return (
    <div
      onClick={onClick}
      style={{
        flex: '1 1 230px', maxWidth: 300, cursor: 'pointer', padding: '12px 14px', borderRadius: 12,
        background: selected ? 'var(--brand-tint)' : 'var(--surface-2)',
        border: '1.5px solid ' + (selected ? 'var(--brand-2)' : 'var(--border)'),
        transition: 'border-color .15s, background .15s',
      }}
    >
      <div className="row between" style={{ marginBottom: 7, alignItems: 'flex-start' }}>
        {kindChip}
        {onRemove && (
          <button className="btn icon sm" onClick={(e) => { e.stopPropagation(); onRemove(); }} style={{ marginTop: -2, flex: '0 0 auto' }}>✕</button>
        )}
      </div>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {title}
      </div>
      <div className="row between" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
        <span>{metaLeft}</span>
        <span className="num">{metaRight}</span>
      </div>
    </div>
  );
}

function ReportSectionList({ title, items, render, empty }) {
  if (!items || items.length === 0) return null;
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="row gap8" style={{ marginBottom: 6 }}>
        <b style={{ fontSize: 12 }}>{title}</b>
        <Chip>{items.length}건</Chip>
      </div>
      <div className="col gap6">
        {items.slice(0, 6).map((it, i) => (
          <div key={i} className="row gap8" style={{ fontSize: 11.5, padding: '6px 10px', background: 'var(--surface-2)', borderRadius: 7, border: '1px solid var(--border)' }}>
            {render(it)}
          </div>
        ))}
      </div>
    </div>
  );
}

function ReportTab({ sim, simLogs, vehiclePos, networkTelemetry, routeCoords, routeEdges, simHistory, simConfig, mode }) {
  const [subTab, setSubTab] = useState(() => mode === 'lite' ? 'logs' : 'compare'); // 'compare' | 'batch' | 'logs' — 한 화면에 다 펼치지 않고 묶음별로 분리
  // Lite는 'logs'만 선택 가능 — Pro에서 'compare'/'batch'를 보던 중 Lite로 전환되면 되돌린다.
  useEffect(() => { if (mode === 'lite' && subTab !== 'logs') setSubTab('logs'); }, [mode, subTab]);
  const [analyzing, setAnalyzing] = useState(false);
  const [revealed, setRevealed] = useState(0);
  const [exported, setExported] = useState(false);
  const [llmResult, setLlmResult] = useState([]);
  const [llmError, setLlmError] = useState(null);
  const [llmProviders, setLlmProviders] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState('');
  const [usedProvider, setUsedProvider] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  useEffect(() => {
    fetch('http://127.0.0.1:8001/api/analysis/llm/providers')
      .then(r => r.json())
      .then(data => {
        const ps = data.providers || [];
        setLlmProviders(ps);
        const active = ps.find(p => p.active);
        if (active && !selectedProvider) setSelectedProvider(active.id);
      })
      .catch(() => {});
  }, []);

  function fetchSummary() {
    setSummaryLoading(true);
    fetch('http://127.0.0.1:8001/api/analysis/summary')
      .then(r => r.json())
      .then(data => { setSummary(data); setSummaryLoading(false); })
      .catch(() => setSummaryLoading(false));
  }
  useEffect(() => { fetchSummary(); }, []);

  const hasLiveLogs = simLogs && simLogs.length > 0;
  const logs = hasLiveLogs ? [...simLogs].reverse() : [];

  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name ?? '--';
  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms ?? null;

  /* ====== ported from tab-routes.jsx — 최적 경로 비교 ====== */
  const hasRoute = routeCoords && routeCoords.length >= 2;

  // 경로 대안(K-path) — 시뮬레이션 시작 후 백그라운드에서 계산되므로, route가 잡힌
  // 직후 바로 fetch하면 아직 준비 전일 수 있어 준비될 때까지 짧게 재시도한다.
  const [kCandidates, setKCandidates] = useState(null);
  const [visibleRanks, setVisibleRanks] = useState({});
  useEffect(() => {
    if (!hasRoute) return;
    let stopped = false;
    let tries = 0;
    const tryFetch = () => {
      fetch('http://127.0.0.1:8001/api/route/candidates')
        .then(r => r.json())
        .then(data => {
          if (stopped) return;
          if (data?.available) {
            setKCandidates(data);
            setVisibleRanks(Object.fromEntries((data.candidates || []).map(c => [c.rank, true])));
          } else if (tries++ < 8) {
            setTimeout(tryFetch, 2000);
          }
        })
        .catch(() => {});
    };
    tryFetch();
    return () => { stopped = true; };
  }, [hasRoute, routeCoords]);

  const currentEdgeId = vehiclePos?.current_edge_id ?? null;
  const edgeNames = networkTelemetry?.route_edge_names ?? routeEdges?.edge_names ?? {};
  const perEdge = routeEdges?.per_edge ?? [];

  // Convert routeCoords [[lat,lng],...] for MiniMap (already correct format)
  const livePath = hasRoute ? routeCoords : null;

  // Base stations for map overlay from candidates
  const bsPoints = (networkTelemetry?.candidate_nodes ?? [])
    .filter(c => c.lat != null && c.lng != null)
    .map(c => ({ lat: c.lat, lng: c.lng }));

  // K-path 대안 — 보이기로 체크된 것만 지도에 겹쳐 그림
  const kCandidateList = kCandidates?.candidates ?? [];
  const altPaths = kCandidateList
    .filter(c => visibleRanks[c.rank] && (c.per_edge?.length ?? 0) >= 2)
    .map(c => ({
      path: c.per_edge.map(e => [e.midpoint_lat, e.midpoint_lng]),
      color: ALT_PATH_COLORS[c.rank % ALT_PATH_COLORS.length],
      rank: c.rank,
    }));

  /* ====== ported from tab-comparison.jsx — 알고리즘 비교 ====== */
  const [metrics, setMetrics] = useState(null);
  const [cmpLoading, setCmpLoading] = useState(false);
  const [cmpError, setCmpError] = useState(null);
  const [history, setHistory] = useState(() => loadRunHistory());
  const [checkedRuns, setCheckedRuns] = useState([]);
  const [savedFlash, setSavedFlash] = useState(false);
  const [routeEval, setRouteEval] = useState(null);
  const [cmp, setCmp] = useState(null);

  /* ====== 시나리오 배치 비교 (Phase 3/4) ====== */
  const [scenarioBatches, setScenarioBatches] = useState(() => loadScenarioBatches());
  const [selectedBatch, setSelectedBatch] = useState(0); // index into reversed(most-recent-first) list

  /* ====== 배치 비교 AI 분석 (Phase 6) — 단일 실행용 runAI()와 별개 ====== */
  const [batchAiLoading, setBatchAiLoading] = useState(false);
  const [batchAiError, setBatchAiError] = useState(null);
  const [batchAiSections, setBatchAiSections] = useState([]);
  const [batchAiProvider, setBatchAiProvider] = useState(null);
  const [batchAiRevealed, setBatchAiRevealed] = useState(0);

  async function runBatchAnalysis(batch) {
    setBatchAiLoading(true); setBatchAiError(null); setBatchAiSections([]); setBatchAiRevealed(0); setBatchAiProvider(null);
    try {
      const res = await fetch('http://127.0.0.1:8001/api/analysis/llm/batch-compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: batch.label, results: batch.results, provider: selectedProvider || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const sections = data.sections || [];
      setBatchAiSections(sections);
      setBatchAiProvider(data.provider || null);
      setBatchAiLoading(false);
      sections.forEach((_, i) => setTimeout(() => setBatchAiRevealed(i + 1), i * 450));
    } catch (e) {
      setBatchAiLoading(false);
      setBatchAiError(e.message || '배치 비교 분석 중 오류가 발생했습니다.');
    }
  }

  /* ====== 팀 공유 실행 기록 — DB 연동 (Phase 0.5/4) ====== */
  const [dbRuns, setDbRuns] = useState([]);
  const [dbRunsAvailable, setDbRunsAvailable] = useState(null); // null=확인중, true/false
  const [dbRunsError, setDbRunsError] = useState(null);

  useEffect(() => {
    fetch('http://127.0.0.1:8001/api/simulation/runs?limit=20')
      .then(r => r.json())
      .then(data => {
        setDbRunsAvailable(!!data.available);
        setDbRuns(data.runs || []);
        if (!data.available) setDbRunsError(data.reason || null);
      })
      .catch(e => { setDbRunsAvailable(false); setDbRunsError(e.message || 'DB 실행 기록을 불러오지 못했습니다.'); });
  }, []);
  const cmpPollRef = useRef(null);

  function fetchMetrics() {
    setCmpLoading(true); setCmpError(null);
    fetch('http://127.0.0.1:8001/api/route/metrics')
      .then(r => r.json())
      .then(data => { setMetrics(data); setCmpLoading(false); })
      .catch(e => { setCmpError(e.message || '불러오기 실패'); setCmpLoading(false); });
    fetch('http://127.0.0.1:8001/api/route/evaluate')
      .then(r => r.json())
      .then(data => setRouteEval(data?.available ? data : null))
      .catch(() => {});
  }
  useEffect(() => { fetchMetrics(); }, []);

  // algo key("k_path_rank_2" 또는 baseline routing_mode)로 해당 후보의 도로명 시퀀스 조회
  function streetNamesFor(algo) {
    const m = /^k_path_rank_(\d+)$/.exec(algo || '');
    if (m) {
      const idx = parseInt(m[1], 10);
      return kCandidates?.candidates?.[idx]?.street_names || null;
    }
    return routeEval?.street_names || null;
  }

  function pollCmp() {
    fetch('http://127.0.0.1:8001/api/route/compare-algorithms')
      .then(r => r.json())
      .then(data => {
        setCmp(data);
        if (data.status !== 'running' && cmpPollRef.current) {
          clearInterval(cmpPollRef.current);
          cmpPollRef.current = null;
        }
      })
      .catch(() => {});
  }
  useEffect(() => {
    pollCmp();
    return () => { if (cmpPollRef.current) clearInterval(cmpPollRef.current); };
  }, []);
  function runComparison() {
    fetch('http://127.0.0.1:8001/api/route/compare-algorithms', { method: 'POST' })
      .then(r => r.json())
      .then(() => {
        setCmp({ status: 'running' });
        if (cmpPollRef.current) clearInterval(cmpPollRef.current);
        cmpPollRef.current = setInterval(pollCmp, 2000);
      })
      .catch(() => {});
  }

  // PRR(연결 유지율) 근사치 — 패킷 단위 시뮬레이션이 없어 실측 PRR은 계산할 수 없으므로,
  // time_weighted_disconnection_ratio(구간 길이가 아니라 각 구간의 "이동 시간"으로 가중한
  // 단절 비율)의 보수(1 - 비율)를 근사치로 쓴다 — edge 개수로 단순 평균하면 짧은 구간과 긴
  // 고속도로 구간이 똑같이 취급되어 왜곡되므로, 시간 가중이 "연결 유지 시간 비율"에 더 가깝다.
  // (구버전 백엔드 호환을 위해 필드가 없으면 disconnection_ratio로 폴백)
  const algorithmsRaw = metrics?.available ? metrics.algorithms : {};
  const algorithms = Object.fromEntries(Object.entries(algorithmsRaw).map(([k, v]) => {
    const discRatio = v.time_weighted_disconnection_ratio ?? v.disconnection_ratio;
    return [k, { ...v, prr_approx: discRatio != null ? 1 - discRatio : null }];
  }));
  const algoEntries = Object.entries(algorithms);
  const comparison = metrics?.available ? metrics.comparison : null;
  const bestPerMetric = comparison?.best_per_metric || {};
  const summaryRank = comparison?.summary_rank || {};

  const sortedByCost = [...algoEntries].sort((a, b) => (a[1].total_cost ?? Infinity) - (b[1].total_cost ?? Infinity));
  const rankItems = Object.entries(summaryRank)
    .sort((a, b) => a[1] - b[1])
    .map(([algo, score]) => ({ label: algo, value: score, display: score.toFixed(2) }));

  function saveCurrentRun() {
    if (!algoEntries.length) return;
    const entry = {
      timestamp: new Date().toISOString(),
      config: simConfig ?? null,
      algorithms,
    };
    const next = [...history, entry];
    setHistory(next);
    saveRunHistory(next);
    setSavedFlash(true);
    setTimeout(() => setSavedFlash(false), 1800);
  }
  function removeRun(idx) {
    const next = history.filter((_, i) => i !== idx);
    setHistory(next);
    saveRunHistory(next);
    setCheckedRuns(checkedRuns.filter(i => i !== idx));
  }
  function toggleCheck(idx) {
    setCheckedRuns(prev => prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx].slice(-3));
  }
  const selectedRuns = checkedRuns.map(i => history[i]).filter(Boolean);

  const reversedBatches = [...scenarioBatches].reverse(); // 최신 배치가 먼저 보이게
  const currentBatch = reversedBatches[selectedBatch] || null;
  useEffect(() => { setBatchAiSections([]); setBatchAiError(null); setBatchAiRevealed(0); }, [selectedBatch]);
  function removeBatch(reversedIdx) {
    const origIdx = scenarioBatches.length - 1 - reversedIdx;
    const next = scenarioBatches.filter((_, i) => i !== origIdx);
    setScenarioBatches(next);
    saveScenarioBatches(next);
    setSelectedBatch(0);
  }

  async function runAI() {
    setAnalyzing(true); setRevealed(0); setLlmError(null); setLlmResult([]); setUsedProvider(null);
    try {
      const payload = {
        sim_elapsed: sim?.elapsed ?? 0,
        vehicle_pos: vehiclePos ?? null,
        edge_history: networkTelemetry?.edge_history ?? [],
        edge_avg_speeds: networkTelemetry?.edge_avg_speeds ?? {},
        route_edge_names: networkTelemetry?.route_edge_names ?? {},
        sim_logs: simLogs ?? [],
        algorithm: networkTelemetry?.routing_mode ?? null,
        handover_count: (simLogs ?? []).filter(l => l.kind === 'handover').length,
        latency_ms: networkTelemetry?.ego_vehicle?.current_latency_ms
          ?? networkTelemetry?.latency_ms ?? null,
        connected_node: networkTelemetry?.ego_vehicle?.connected_network_node_name
          ?? networkTelemetry?.connected_node?.name ?? null,
        provider: selectedProvider || null,
      };
      const res = await fetch('http://127.0.0.1:8001/api/analysis/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      const sections = data.sections || [];
      setLlmResult(sections);
      setUsedProvider(data.provider || null);
      setAnalyzing(false);
      sections.forEach((_, i) => setTimeout(() => setRevealed(i + 1), i * 450));
    } catch (e) {
      setAnalyzing(false);
      setLlmError(e.message || 'AI 분석 중 오류가 발생했습니다.');
    }
  }

  function exportCSV() {
    let csv;
    if (hasLiveLogs && vehiclePos) {
      const header = ['시각', '차량ID', '위치', '속도', '진행률', '연결기지국', 'Latency', '이벤트'];
      const rows = [
        [fmtClock(sim.elapsed), 'EGO-001',
         `"${vehiclePos.lat.toFixed(5)},${vehiclePos.lng.toFixed(5)}"`,
         (vehiclePos.speed ?? 0).toFixed(1),
         ((vehiclePos.progress ?? 0) * 100).toFixed(0) + '%',
         connNode,
         latency !== null ? latency.toFixed(1) : '—',
         vehiclePos.arrived ? '도착' : '이동 중'],
      ];
      const logRows = simLogs.map(l => [l.t, l.target, '—', '—', '—', '—', '—', l.ko]);
      csv = [header.join(','), ...rows.map(r => r.join(',')), ...logRows.map(r => r.join(','))].join('\n');
    } else {
      const header = ['시각', '차량ID', '위치', '속도', '현재엣지', '연결기지국', 'Latency', '이벤트'];
      csv = header.join(',');
    }
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = hasLiveLogs ? `v2x_live_${fmtClock(sim.elapsed).replace(/:/g, '')}.csv` : 'v2x_simulation_log.csv';
    a.click();
    setExported(true); setTimeout(() => setExported(false), 2200);
  }

  const [jsonExported, setJsonExported] = useState(false);
  function exportJSON() {
    // CSV(exportCSV)는 실시간 스냅샷 1행 + 로그뿐 — 원시 시계열/배치 데이터를 그대로 보존해
    // 외부 도구(노트북, R, Excel 피벗 등)에서 재분석하려는 대학원생 수요를 위해 전체를 묶어 내보낸다.
    const payload = {
      exported_at: new Date().toISOString(),
      sim_elapsed_s: sim?.elapsed ?? 0,
      vehicle_pos: vehiclePos ?? null,
      sim_history: simHistory ?? [],
      sim_logs: simLogs ?? [],
      route_edges: routeEdges ?? null,
      network_telemetry: networkTelemetry ?? null,
      route_metrics: metrics?.available ? metrics : null,
      algorithm_compare: cmp ?? null,
      scenario_batches: scenarioBatches ?? [],
      sim_config: simConfig ?? null,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `v2x_full_export_${fmtClock(sim?.elapsed ?? 0).replace(/:/g, '')}.json`;
    a.click();
    setJsonExported(true); setTimeout(() => setJsonExported(false), 2200);
  }

  const seed = summary?.available ? summary.recommendation_text_seed : null;

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Post-run Report</div>
          <h1>분석 보고서 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Report</span></h1>
          <div className="sub">구조화 요약 · 시뮬레이션 로그 · LLM 자연어 요약 · CSV 내보내기</div>
        </div>
        <div className="row gap8">
          {hasLiveLogs && <Chip tone="good" dot>LIVE</Chip>}
          <button className={'btn ' + (exported ? 'good' : 'accent')} onClick={exportCSV}>
            {exported ? <><Icon.check size={15} /> 저장됨</> : <><Icon.download size={15} /> CSV로 내보내기</>}
          </button>
          {mode === 'pro' && (
            <button className={'btn ' + (jsonExported ? 'good' : '')} onClick={exportJSON} title="시계열·배치·비교 원시 데이터를 통째로 JSON으로 내보냅니다">
              {jsonExported ? <><Icon.check size={15} /> 저장됨</> : <><Icon.download size={15} /> 전체 JSON 다운로드</>}
            </button>
          )}
        </div>
      </div>

      {/* ── 구조화 요약 ─────────────────────────────── */}
      <Card title="구조화 요약" en="Structured summary" right={
        <button className="btn sm" onClick={fetchSummary}><Icon.reset size={13} /> 새로고침</button>
      } style={{ marginBottom: 18 }}>
        {summaryLoading && <div className="muted" style={{ padding: 16, fontSize: 12 }}>불러오는 중…</div>}
        {!summaryLoading && !summary?.available && (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>{summary?.reason || '시뮬레이션을 먼저 실행하세요.'}</div>
        )}
        {!summaryLoading && summary?.available && (
          <>
            {seed?.primary_finding && (
              <div style={{ padding: '12px 14px', background: 'var(--brand-tint)', borderRadius: 9, marginBottom: 14, fontSize: 12.5, lineHeight: 1.5 }}>
                <b style={{ marginRight: 6 }}>핵심 발견:</b>{seed.primary_finding}
              </div>
            )}
            <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <ReportSectionList title="병목 구간" items={summary.bottleneck_sections} render={it => (
                <><span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span><span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}%</span>{it.severity && <Chip tone="warn">{it.severity}</Chip>}</>
              )} />
              <ReportSectionList title="과부하 기지국" items={summary.overloaded_base_stations} render={it => (
                <><span className="mono" style={{ fontWeight: 600 }}>{it.bs_name}</span><span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}%</span></>
              )} />
              <ReportSectionList title="빈번한 핸드오버 구간" items={summary.frequent_handover_sections} render={it => (
                <><span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span><span className="muted">{it.from_bs_name} → {it.to_bs_name}</span></>
              )} />
              <ReportSectionList title="고지연 구간" items={summary.high_latency_sections} render={it => (
                <><span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span><span className="muted">{(it.latency_ms ?? 0).toFixed(1)}ms (+{(it.excess_ms ?? 0).toFixed(1)}ms)</span></>
              )} />
            </div>
            <ReportSectionList title="미래 연결 위험 구간" items={summary.future_connectivity_risk_sections} render={it => (
              <><span style={{ fontWeight: 600 }}>{it.street_name || it.edge_id}</span>{it.severity && <Chip tone="bad">{it.severity}</Chip>}</>
            )} />
            {seed && (seed.risk_factors?.length > 0 || seed.improvement_highlights?.length > 0) && (
              <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 4 }}>
                {seed.improvement_highlights?.length > 0 && (
                  <div>
                    <b style={{ fontSize: 12 }}>개선 사항</b>
                    <ul style={{ margin: '6px 0 0 16px', fontSize: 11.5, lineHeight: 1.6 }}>
                      {seed.improvement_highlights.map((t, i) => <li key={i}>{t}</li>)}
                    </ul>
                  </div>
                )}
                {seed.risk_factors?.length > 0 && (
                  <div>
                    <b style={{ fontSize: 12 }}>위험 요인</b>
                    <ul style={{ margin: '6px 0 0 16px', fontSize: 11.5, lineHeight: 1.6, color: 'var(--bad)' }}>
                      {seed.risk_factors.map((t, i) => <li key={i}>{t}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Card>

      {hasLiveLogs && vehiclePos && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 18 }}>
          <Stat label="경과 시간" icon="clock" value={fmtClock(sim.elapsed)} sub="시뮬레이션 시작 후" accent />
          <Stat label="현재 속도" icon="speed" value={(vehiclePos.speed ?? 0).toFixed(1)} unit="km/h" sub="EGO-001" />
          <Stat label="연결 기지국" icon="antenna" value={connNode} sub={latency !== null ? `Latency ${latency.toFixed(1)}ms` : '—'} />
          <Stat label="이벤트 수" icon="chart" value={simLogs.length} unit="건" sub={`핸드오버 ${simLogs.filter(l => l.kind === 'handover').length}건`} />
        </div>
      )}

      <div className="row gap8" style={{ marginBottom: 18 }}>
        <Seg value={subTab} onChange={setSubTab} options={
          mode === 'pro'
            ? [{ v: 'compare', label: '경로·알고리즘 비교' }, { v: 'batch', label: '시나리오 배치 비교' }, { v: 'logs', label: '로그·AI분석' }]
            : [{ v: 'logs', label: '로그·AI분석' }]
        } />
      </div>

      {subTab === 'compare' && mode === 'pro' && <>
      {/* ════════════════════════════════════════════════════
          최적 경로 비교 (ported from tab-routes.jsx)
          ════════════════════════════════════════════════════ */}
      <div style={{ margin: '8px 0 14px' }}>
        <div className="eyebrow">Path Comparison</div>
        <h2 style={{ fontSize: 17, fontWeight: 700, margin: '4px 0 2px' }}>
          최적 경로 비교 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>Routes</span>
        </h2>
        <div className="sub" style={{ fontSize: 12 }}>
          {hasRoute ? '실시간 SUMO 경로 vs 대안 경로(K-path)' : '시뮬레이션 실행 후 실제 경로와 대안 경로를 비교합니다'}
        </div>
      </div>

      {hasRoute ? (
        <>
          <Card title="실시간 경로" en="Live SUMO route" right={<Chip tone="good" dot>실시간</Chip>} style={{ marginBottom: 18 }}>
            <MiniMap path={livePath} color="var(--brand-2)" bs={bsPoints} label="live" height={210} extraPaths={altPaths} />
            <div className="row gap16" style={{ marginTop: 12, fontSize: 11, flexWrap: 'wrap' }}>
              <span className="row gap8"><span style={{ width: 16, height: 3, background: 'var(--brand-2)', borderRadius: 2 }} /> 실제 경로</span>
              {connNode !== '--' && <span className="row gap8"><span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--brand-2)' }} /> {connNode}</span>}
              {latency !== null && <span className="row gap8" style={{ marginLeft: 'auto' }}>평균 <span className="num" style={{ fontWeight: 600 }}>{latency.toFixed(1)}</span>ms</span>}
            </div>
            {kCandidateList.length > 0 && (
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                <div className="muted" style={{ fontSize: 10.5, marginBottom: 6 }}>
                  경로 대안(K-path) — 점선으로 지도에 겹쳐 표시, 체크 해제 시 숨김
                </div>
                <div className="col gap6">
                  {kCandidateList.map(c => {
                    const names = c.street_names || [];
                    const summary = names.length > 3
                      ? `${names.slice(0, 2).join(' → ')} → … → ${names[names.length - 1]}`
                      : names.join(' → ');
                    const color = ALT_PATH_COLORS[c.rank % ALT_PATH_COLORS.length];
                    return (
                      <label key={c.rank} className="row gap8" style={{ fontSize: 10.5, cursor: 'pointer', alignItems: 'center' }}>
                        <input
                          type="checkbox"
                          checked={!!visibleRanks[c.rank]}
                          onChange={() => setVisibleRanks(prev => ({ ...prev, [c.rank]: !prev[c.rank] }))}
                        />
                        <span style={{ width: 14, height: 3, background: color, borderRadius: 2, flex: '0 0 auto' }} />
                        <span style={{ flex: '0 0 auto', fontWeight: 600 }}>대안 {c.rank + 1}</span>
                        <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summary}</span>
                        <span className="num muted" style={{ marginLeft: 'auto', flex: '0 0 auto' }}>{c.avg_latency_ms?.toFixed(1)}ms</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </Card>

          {perEdge.length > 0 && (
            <Card title="구간별 비용 분해" en="Edge cost breakdown" right={<Chip>{perEdge.length}개 구간</Chip>} style={{ marginBottom: 18 }}>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>구간</th>
                      <th className="r">거리</th>
                      <th className="r">Latency</th>
                      <th className="r">부하율</th>
                      <th className="r">총 비용</th>
                      <th>커버리지</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perEdge.map((e, i) => {
                      const isCurrent = e.edge_id === currentEdgeId;
                      const name = e.best_node_name || edgeNames[e.edge_id] || e.edge_id;
                      return (
                        <tr key={e.edge_id || i} style={isCurrent ? { background: 'var(--brand-tint)', fontWeight: 500 } : {}}>
                          <td>
                            <span className="mono" style={{ fontSize: 11.5 }}>{name}</span>
                            {isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}
                          </td>
                          <td className="r"><span className="num">{e.distance_m != null ? e.distance_m.toFixed(0) : '—'}</span><span className="muted" style={{ fontSize: 10 }}> m</span></td>
                          <td className="r"><span className="num" style={{ color: `var(--${latencyTone(e.latency_ms || 0)})`, fontWeight: 600 }}>{(e.latency_ms || 0).toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> ms</span></td>
                          <td className="r">
                            <div className="row gap8" style={{ justifyContent: 'flex-end' }}>
                              <div className="pbar" style={{ width: 44 }}><i style={{ width: `${Math.min((e.load_ratio || 0) * 100, 100)}%`, background: 'var(--brand-2)' }} /></div>
                              <span className="num" style={{ fontSize: 11 }}>{((e.load_ratio || 0) * 100).toFixed(0)}%</span>
                            </div>
                          </td>
                          <td className="r"><span className="num" style={{ fontWeight: 600 }}>{(e.total_cost || 0).toFixed(2)}</span></td>
                          <td>{e.within_coverage === false
                            ? <Chip tone="bad" dot>미커버</Chip>
                            : <Chip tone="good" dot>커버됨</Chip>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {(perEdge.length > 0 || simHistory?.length > 0) && (() => {
            // 시간축 반복측정(simHistory — 라이브 SUMO 실행 중 약 1Hz로 누적된 latency 샘플)이
            // 충분하면 그걸 우선 쓴다 — "여러 번 측정값의 분포"라는 백분위수의 본래 정의에 더
            // 가깝다. 표본이 부족하면(배치 평가 등 라이브 실행이 없던 경우) 구간(edge)별 공간
            // 분포로 폴백 — 단, 이건 "반복 측정"이 아니라 "한 경로 안 구간 간 분포"이므로 출처를
            // Chip으로 명확히 구분해서 표시한다(두 값을 섞어 같은 의미인 것처럼 보이지 않게).
            const liveSamples = (simHistory || []).map(h => h.latency).filter(v => v !== null && v !== undefined);
            const useLive = liveSamples.length >= 10;
            const samples = useLive ? liveSamples : perEdge.map(e => e.latency_ms || 0);
            const sortedLat = [...samples].sort((a, b) => a - b);
            const p50 = percentile(sortedLat, 50);
            const p90 = percentile(sortedLat, 90);
            const p95 = percentile(sortedLat, 95);
            const p99 = percentile(sortedLat, 99);
            return (
              <Card title="Latency 백분위수" en="Latency percentiles"
                right={<Chip tone={useLive ? 'good' : ''}>{useLive ? `시간축 반복측정 ${sortedLat.length}건` : `구간(edge) 분포 ${sortedLat.length}건`}</Chip>}
                style={{ marginBottom: 18 }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 12 }}>
                  {useLive
                    ? '라이브 실행 중 약 1Hz로 누적된 latency 시계열(최근 최대 60건)의 백분위수 — 평균만으로는 가려지는 꼬리 구간의 지연을 확인합니다.'
                    : '시간축 반복측정 표본이 부족해(10건 미만) 현재 경로의 구간별(edge) latency 공간 분포로 대체했습니다 — "반복 측정"이 아닌 "구간 간 분포"이므로 인용 시 구분해서 표기하세요.'}
                </div>
                <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
                  <Stat label="P50 (중앙값)" icon="speed" value={p50 != null ? p50.toFixed(1) : '—'} unit="ms" />
                  <Stat label="P90" icon="speed" value={p90 != null ? p90.toFixed(1) : '—'} unit="ms" />
                  <Stat label="P95" icon="warn" value={p95 != null ? p95.toFixed(1) : '—'} unit="ms" />
                  <Stat label="P99" icon="warn" value={p99 != null ? p99.toFixed(1) : '—'} unit="ms" accent />
                </div>
              </Card>
            );
          })()}
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--ink-4)', marginBottom: 18 }}>
          <div style={{ fontSize: 28, opacity: 0.2, marginBottom: 10 }}>⇢</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: 'var(--ink-3)' }}>경로가 설정되지 않았습니다</div>
          <div style={{ fontSize: 12 }}>시뮬레이션 탭에서 출발지와 도착지를 지정하고 시뮬레이션을 시작하세요</div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════
          알고리즘 비교 (ported from tab-comparison.jsx)
          ════════════════════════════════════════════════════ */}
      <div style={{ margin: '8px 0 14px' }}>
        <div className="eyebrow">Decision Support</div>
        <h2 style={{ fontSize: 17, fontWeight: 700, margin: '4px 0 2px' }}>
          알고리즘 비교 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>Comparison</span>
        </h2>
        <div className="sub" style={{ fontSize: 12 }}>경로 대안 비교 · 자원할당/기지국 선택 알고리즘 비교 · 실행 이력 비교</div>
      </div>

      <Card title="경로 대안 비교" en="Path alternatives" right={algoEntries.length > 0 ? <Chip>{algoEntries.length}개 후보</Chip> : null} style={{ marginBottom: 18 }}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
          같은 알고리즘 설정으로 찾은 대안 경로(K-path)들을 비교합니다. 도로망이 단순하면 대안 경로가
          적게 나올 수 있습니다 — 서로 다른 알고리즘 설정을 비교하려면 아래 "알고리즘 설정 비교"를 사용하세요.
          baseline 항목은 시뮬레이션 탭에서 실제로 선택한 경로 탐색 알고리즘(Dijkstra/A*/
          K-shortest-path/Network-aware/Look-ahead)을 반영합니다. "경유 도로" 칸에서 각
          후보가 실제로 어떤 도로를 지나는지 확인할 수 있습니다.
          <br />PRR(근사)은 패킷 단위 시뮬레이션 없이 "이동 시간으로 가중한 단절 비율"의 보수
          (1 - time_weighted_disconnection_ratio)로 근사한 값입니다 — 실측 패킷 수신율이 아니라
          "연결 유지 시간 비율" proxy이므로, 인용 시 PRR이 아닌 "연결유지율(시간가중 근사)"으로 표기하세요.
        </div>
        {cmpLoading && <div className="muted" style={{ padding: 16, fontSize: 12 }}>불러오는 중…</div>}
        {!cmpLoading && cmpError && <div style={{ padding: 16, fontSize: 12, color: 'var(--bad)' }}>{cmpError}</div>}
        {!cmpLoading && !cmpError && algoEntries.length === 0 && (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>시뮬레이션을 먼저 실행하면 후보 알고리즘 비교가 표시됩니다.</div>
        )}
        {algoEntries.length > 0 && (
          <>
            {rankItems.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>종합 순위 (낮을수록 우수)</div>
                <BarChart items={rankItems} />
              </div>
            )}
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>알고리즘</th>
                    <th>경유 도로</th>
                    {CMP_METRIC_COLS.map(c => <th key={c.key} className="r">{c.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {sortedByCost.map(([algo, m]) => {
                    const names = streetNamesFor(algo);
                    const shown = names && names.length > 4
                      ? [...names.slice(0, 3), '…', names[names.length - 1]]
                      : names;
                    return (
                    <tr key={algo}>
                      <td><span className="mono" style={{ fontWeight: 600 }}>{algo}</span></td>
                      <td style={{ maxWidth: 260 }}>
                        {shown
                          ? <div className="row gap6 wrap">
                              {shown.map((nm, i) => nm === '…'
                                ? <span key={i} className="muted" style={{ fontSize: 11 }}>…</span>
                                : <Chip key={i} style={{ fontSize: 10 }}>{nm}</Chip>)}
                            </div>
                          : <span className="muted">—</span>}
                      </td>
                      {CMP_METRIC_COLS.map(c => {
                        const v = m[c.key];
                        const isBest = bestPerMetric[c.key] === algo;
                        return (
                          <td key={c.key} className="r">
                            <span className="num" style={{ fontWeight: isBest ? 700 : 400, color: isBest ? 'var(--good)' : 'inherit' }}>
                              {v != null ? c.fmt(v) : '—'}
                            </span>
                            {isBest && <Chip tone="good" style={{ marginLeft: 6, fontSize: 9 }}>최적</Chip>}
                          </td>
                        );
                      })}
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row gap8" style={{ marginTop: 14 }}>
              <button className={'btn sm ' + (savedFlash ? 'good' : '')} onClick={saveCurrentRun}>
                {savedFlash ? <><Icon.check size={13} /> 저장됨</> : <><Icon.download size={13} /> 현재 결과를 히스토리에 저장</>}
              </button>
            </div>
          </>
        )}
      </Card>

      <Card title="알고리즘 설정 비교" en="Algorithm settings"
        right={<button className="btn sm" onClick={runComparison} disabled={cmp?.status === 'running' || !metrics?.available}>
          {cmp?.status === 'running' ? <><Icon.reset size={13} className="spin" /> 실행 중…</> : <><Icon.spark size={13} /> 비교 실행</>}
        </button>}
        style={{ marginBottom: 18 }}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
          같은 경로를 latency / 기지국 선택 / 자원할당 알고리즘별로 다시 평가해서 비교합니다. 자원할당
          최적, 기지국 선택 최적 등 시뮬레이션 결과 기반 알고리즘 비교가 여기에 해당합니다.
          경로 탐색 알고리즘(Dijkstra/A*)은 현재 SUMO 모드에서 A* 구현이 없어 비교 대상에서 제외했습니다.
        </div>
        {!metrics?.available && (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>시뮬레이션을 먼저 실행하세요.</div>
        )}
        {metrics?.available && !cmp?.by_latency && cmp?.status !== 'running' && (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>
            {cmp?.status === 'error' ? (cmp.reason || '비교 실행에 실패했습니다.') : '"비교 실행"을 눌러 알고리즘 설정별 결과를 확인하세요.'}
          </div>
        )}
        {cmp?.status === 'running' && !cmp?.by_latency && (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>비교 실행 중… (경로 길이에 따라 수 초~수십 초 걸릴 수 있습니다)</div>
        )}
        {cmp?.by_latency && (
          <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
            <div>
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>지연시간 알고리즘별 (평균 ms) · <span style={{ color: 'var(--good)' }}>녹색 = 최저</span></div>
              <BarChart items={Object.entries(cmp.by_latency).map(([id, v]) => {
                const isLowest = v.avg_latency_ms === Math.min(...Object.values(cmp.by_latency).map(x => x.avg_latency_ms));
                return {
                  label: algoLabel(id),
                  value: v.avg_latency_ms,
                  display: `${v.avg_latency_ms.toFixed(1)}ms${isLowest ? ' · 최저' : ''}`,
                  color: isLowest ? 'var(--good)' : (id === simConfig?.algorithm_selection?.latency_algorithm ? 'var(--brand)' : 'var(--brand-2)'),
                };
              })} />
            </div>
            <div>
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>기지국 선택 알고리즘별 (handover 수)</div>
              <BarChart items={Object.entries(cmp.by_bs_selection || {}).map(([id, v]) => ({
                label: algoLabel(id),
                value: v.handover_count,
                display: `${v.handover_count}회 · ${v.total_cost.toFixed(1)}`,
                color: id === simConfig?.algorithm_selection?.base_station_selection_algorithm ? 'var(--brand)' : 'var(--brand-2)',
              }))} />
            </div>
            <div>
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>자원할당 알고리즘별 (사용률 %)</div>
              <BarChart items={Object.entries(cmp.by_allocation || {}).map(([id, v]) => ({
                label: algoLabel(id),
                value: v.total_utilization * 100,
                display: `${(v.total_utilization * 100).toFixed(0)}%${v.overloaded_bs_count > 0 ? ` · 과부하 ${v.overloaded_bs_count}` : ''}`,
                color: id === simConfig?.algorithm_selection?.resource_allocation_algorithm ? 'var(--brand)' : 'var(--brand-2)',
              }))} max={100} />
            </div>
          </div>
        )}
      </Card>

      <Card title="실행 이력 비교" en="Run history" right={<Chip>{history.length}개 저장됨</Chip>} style={{ marginBottom: 18 }}>
        {history.length === 0 ? (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>아직 저장된 실행이 없습니다. 위에서 "현재 결과를 히스토리에 저장"을 눌러 비교를 시작하세요.</div>
        ) : (
          <>
            <div className="tbl-wrap" style={{ marginBottom: 14 }}>
              <table className="tbl">
                <thead>
                  <tr><th></th><th>시각</th><th>알고리즘 수</th><th>네트워크 모드</th><th></th></tr>
                </thead>
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i} className={checkedRuns.includes(i) ? 'selected' : ''}>
                      <td><input type="checkbox" checked={checkedRuns.includes(i)} onChange={() => toggleCheck(i)} /></td>
                      <td><span className="num muted" style={{ fontSize: 11.5 }}>{new Date(h.timestamp).toLocaleString('ko-KR')}</span></td>
                      <td><span className="num">{Object.keys(h.algorithms || {}).length}</span></td>
                      <td><Chip tone="brand">{h.config?.policy_options?.network_mode ?? '—'}</Chip></td>
                      <td className="r"><button className="btn icon sm" onClick={() => removeRun(i)}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selectedRuns.length >= 2 && (
              <div className="tbl-wrap">
                <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>선택한 {selectedRuns.length}개 실행 비교 (각 실행의 최저 비용 알고리즘 기준)</div>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>실행 시각</th>
                      {CMP_METRIC_COLS.map(c => <th key={c.key} className="r">{c.label}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {selectedRuns.map((h, i) => {
                      const best = Object.values(h.algorithms || {}).sort((a, b) => (a.total_cost ?? Infinity) - (b.total_cost ?? Infinity))[0];
                      return (
                        <tr key={i}>
                          <td><span className="num muted" style={{ fontSize: 11.5 }}>{new Date(h.timestamp).toLocaleString('ko-KR')}</span></td>
                          {CMP_METRIC_COLS.map(c => (
                            <td key={c.key} className="r"><span className="num">{best && best[c.key] != null ? c.fmt(best[c.key]) : '—'}</span></td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {selectedRuns.length === 1 && (
              <div className="muted" style={{ fontSize: 11.5, padding: '8px 2px' }}>비교하려면 2개 이상 선택하세요.</div>
            )}
          </>
        )}

        <div className="muted" style={{ fontSize: 10.5, marginTop: 14, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          팀 공유 실행 기록(DB) — {dbRunsAvailable === null ? '확인 중…' : dbRunsAvailable ? `${dbRuns.length}건` : (dbRunsError || 'DB가 연결되어 있지 않습니다')}
        </div>
        {dbRunsAvailable && dbRuns.length > 0 && (
          <div className="tbl-wrap" style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
            <table className="tbl">
              <thead><tr><th>시각</th><th>모드</th><th>시나리오/배치</th><th className="r">seed</th></tr></thead>
              <tbody>
                {dbRuns.map(r => (
                  <tr key={r.id}>
                    <td><span className="num muted" style={{ fontSize: 11 }}>{r.started_at ? new Date(r.started_at).toLocaleString('ko-KR') : '—'}</span></td>
                    <td><Chip tone={r.mode === 'sumo' ? 'brand' : ''}>{r.mode}</Chip></td>
                    <td className="muted" style={{ fontSize: 11 }}>{r.scenario_id || '—'}{r.batch_id ? ` · ${r.batch_id.slice(0, 8)}` : ''}</td>
                    <td className="r"><span className="num muted">{r.seed ?? '—'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      </>}

      {subTab === 'batch' && mode === 'pro' && <>
      <Card title="시나리오 배치 비교" en="Scenario batch comparison" right={<Chip>{scenarioBatches.length}개 배치</Chip>} style={{ marginBottom: 18 }}>
        {scenarioBatches.length === 0 ? (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>
            아직 저장된 배치가 없습니다. 시나리오 어시스턴트 탭의 "시나리오 생성·배치" 모드에서 배치를 실행하면 여기에 표시됩니다.
          </div>
        ) : (
          <>
            <div className="row gap10 wrap" style={{ marginBottom: 16 }}>
              {reversedBatches.map((b, i) => {
                const kind = inferBatchKind(b);
                const successCount = (b.results || []).filter(r => r.status === 'done').length;
                const total = b.results?.length ?? 0;
                return (
                  <PickerCard
                    key={b.batch_id}
                    selected={i === selectedBatch}
                    onClick={() => setSelectedBatch(i)}
                    onRemove={() => removeBatch(i)}
                    kindChip={<Chip tone={kind.tone}>{kind.text}</Chip>}
                    title={b.label || '(레이블 없음)'}
                    metaLeft={b.started_at ? new Date(b.started_at).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                    metaRight={`${successCount}/${total} 성공`}
                  />
                );
              })}
            </div>

            {currentBatch && currentBatch.results?.length > 0 && (() => {
              const doneResults = currentBatch.results.filter(r => r.status === 'done');
              const isRlBatch = doneResults.some(r => r.mode === 'rl_episode');
              // route_metrics는 비용 낮을수록, rl_episode는 reward 높을수록 좋음 — best 판정 방향이 반대
              let bestIdx = -1;
              if (doneResults.length > 0) {
                bestIdx = doneResults.reduce((bestI, r, i) => {
                  const cur = r.mode === 'rl_episode' ? (r.mean_reward ?? r.total_reward ?? -Infinity) : -(r.route_cost_result?.total_cost ?? Infinity);
                  const best = doneResults[bestI];
                  const bestVal = best.mode === 'rl_episode' ? (best.mean_reward ?? best.total_reward ?? -Infinity) : -(best.route_cost_result?.total_cost ?? Infinity);
                  return cur > bestVal ? i : bestI;
                }, 0);
              }
              const bestId = bestIdx >= 0 ? doneResults[bestIdx].id : null;
              return (
              <>
                <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                  {isRlBatch ? '평균 reward 비교(높을수록 좋음, 막대 위 ±는 표준편차)' : '총비용 비교(낮을수록 좋음)'} · 가장 좋은 결과에 "최적" 표시
                </div>
                <BarChart items={doneResults.map(r => {
                  const isRl = r.mode === 'rl_episode';
                  const val = isRl ? (r.mean_reward ?? r.total_reward ?? 0) : (r.route_cost_result?.total_cost ?? 0);
                  const std = isRl && r.std_reward != null ? ` ±${r.std_reward.toFixed(2)}` : '';
                  return {
                    label: r.label || r.id,
                    value: val,
                    display: isRl ? `reward ${val.toFixed(2)}${std}` : `비용 ${val.toFixed(1)}`,
                    color: isRl ? 'var(--good)' : 'var(--brand-2)',
                  };
                })} />

                <div className="tbl-wrap" style={{ marginTop: 14 }}>
                  <table className="tbl">
                    <thead>
                      <tr><th>시나리오</th><th>모드</th><th className="r">차량수/정책</th><th className="r">seed</th><th className="r">결과</th></tr>
                    </thead>
                    <tbody>
                      {currentBatch.results.map((r, i) => {
                        const isRl = r.mode === 'rl_episode';
                        const isBest = r.status === 'done' && r.id === bestId;
                        return (
                        <tr key={i} style={isBest ? { background: 'var(--good-tint)' } : {}}>
                          <td style={{ borderLeft: `3px solid ${r.status !== 'done' ? 'var(--bad)' : isRl ? 'var(--good)' : 'var(--brand-2)'}`, paddingLeft: 9 }}>
                            {r.label || r.id}
                          </td>
                          <td><Chip tone={r.status === 'done' ? (isRl ? 'good' : 'brand') : 'bad'}>{r.mode}</Chip></td>
                          <td className="r"><span className="num">{isRl ? (r.policy || '—') : (r.vehicle_count ?? '—')}</span></td>
                          <td className="r"><span className="num muted">{r.seed ?? '—'}</span></td>
                          <td className="r">
                            {r.status !== 'done' ? (
                              <span style={{ color: 'var(--bad)' }}>{r.error || '실패'}</span>
                            ) : isRl ? (
                              <span className="num">
                                reward {(r.mean_reward ?? r.total_reward ?? 0).toFixed(2)}
                                {r.std_reward != null && <span className="muted"> ±{r.std_reward.toFixed(2)} (n={r.n_episodes ?? 1})</span>}
                              </span>
                            ) : (
                              <span className="num">비용 {(r.route_cost_result?.total_cost ?? 0).toFixed(2)} · {(r.route_cost_result?.avg_latency_ms ?? 0).toFixed(1)}ms</span>
                            )}
                            {isBest && <Chip tone="good" style={{ marginLeft: 6, fontSize: 9 }}>최적</Chip>}
                          </td>
                        </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="row gap8" style={{ marginTop: 14 }}>
                  <button className="btn sm primary" disabled={batchAiLoading} onClick={() => runBatchAnalysis(currentBatch)}>
                    {batchAiLoading ? <><Icon.reset size={13} className="spin" /> 분석 중…</> : <><Icon.spark size={13} /> 배치 비교 AI 분석</>}
                  </button>
                  {batchAiProvider && <Chip style={{ color: PROVIDER_LABELS[batchAiProvider]?.color }}>{PROVIDER_LABELS[batchAiProvider]?.name || batchAiProvider}</Chip>}
                </div>
                {batchAiError && (
                  <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--bad)', background: 'var(--bad-tint)', padding: '8px 10px', borderRadius: 8 }}>{batchAiError}</div>
                )}
                {batchAiRevealed > 0 && (
                  <div className="col gap12" style={{ marginTop: 14 }}>
                    {batchAiSections.slice(0, batchAiRevealed).map((t, i) => (
                      <div key={i} className="fade row gap12" style={{ padding: '12px 13px', background: 'var(--surface-2)', borderRadius: 10, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                        <span className="num" style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-2)', flex: '0 0 auto', marginTop: 1 }}>{String(i + 1).padStart(2, '0')}</span>
                        <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
              );
            })()}
          </>
        )}
      </Card>
      </>}

      {subTab === 'logs' && <>
      <div className="grid" style={{ gridTemplateColumns: '1.35fr 1fr', alignItems: 'start' }}>
        {/* log table */}
        <Card
          title="시뮬레이션 로그"
          en="Event log"
          right={
            <div className="row gap8">
              {hasLiveLogs && <Chip tone="good" dot>실시간</Chip>}
              <Chip>{logs.length}건</Chip>
            </div>
          }
          style={{ padding: 0 }}
        >
          <div className="tbl-wrap" style={{ maxHeight: 'calc(100vh - 250px)' }}>
            <table className="tbl">
              <thead><tr><th>시각<span className="en">Time</span></th><th>대상<span className="en">Target</span></th><th>유형<span className="en">Type</span></th><th>이벤트 내용<span className="en">Event</span></th></tr></thead>
              <tbody>
                {logs.map((l, i) => {
                  const st = LOG_STYLE[l.kind] ?? LOG_STYLE.info;
                  return (
                    <tr key={i}>
                      <td><span className="num muted" style={{ fontSize: 11.5 }}>{l.t}</span></td>
                      <td><span className="mono" style={{ fontWeight: 600, fontSize: 11.5 }}>{l.target}</span></td>
                      <td><Chip tone={st.tone} dot={st.tone !== ''}>{st.ko}</Chip></td>
                      <td style={{ whiteSpace: 'normal', maxWidth: 280, lineHeight: 1.4 }}>{l.ko}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* LLM panel */}
        <Card title="AI 자연어 분석" en="LLM summary" right={
          <div className="row gap8">
            {mode === 'pro' && llmProviders.length > 1 && (
              <select className="input" style={{ height: 28, fontSize: 11, minWidth: 110 }}
                value={selectedProvider}
                onChange={e => setSelectedProvider(e.target.value)}>
                {llmProviders.map(p => (
                  <option key={p.id} value={p.id}>{p.name.split('/')[1]?.trim() || p.name} — {p.model}</option>
                ))}
              </select>
            )}
            <Chip tone="brand">
              <Icon.spark size={11} />
              {usedProvider ? (PROVIDER_LABELS[usedProvider]?.name || usedProvider) : (PROVIDER_LABELS[selectedProvider]?.name || 'AI')}
            </Chip>
          </div>
        }>
          {revealed === 0 && !analyzing && (
            <div style={{ textAlign: 'center', padding: '26px 16px' }}>
              <div style={{ width: 52, height: 52, borderRadius: 14, background: 'var(--brand-tint)', display: 'grid', placeItems: 'center', margin: '0 auto 14px', color: 'var(--brand-2)' }}><Icon.spark size={26} /></div>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 5 }}>AI 분석 준비 완료</div>
              <div className="muted" style={{ fontSize: 11.5, marginBottom: 18, lineHeight: 1.5 }}>
                로그 {logs.length}건을 분석해<br />자연어 요약을 생성합니다
              </div>
              {llmError && (
                <div style={{ fontSize: 11.5, color: 'var(--bad)', marginBottom: 12, lineHeight: 1.5, textAlign: 'left', background: 'var(--bad-tint)', padding: '8px 10px', borderRadius: 8 }}>{llmError}</div>
              )}
              <button className="btn primary" onClick={runAI}><Icon.spark size={15} /> AI 분석 시작</button>
            </div>
          )}
          {analyzing && (
            <div style={{ textAlign: 'center', padding: '34px 16px' }}>
              <div className="spin" style={{ width: 30, height: 30, border: '3px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%', margin: '0 auto 14px' }} />
              <div className="muted" style={{ fontSize: 12 }}>AI 분석 중…</div>
            </div>
          )}
          {revealed > 0 && (
            <div className="col gap12">
              {llmResult.slice(0, revealed).map((t, i) => (
                <div key={i} className="fade row gap12" style={{ padding: '12px 13px', background: i === 6 ? 'var(--bad-tint)' : i === 7 ? 'var(--good-tint)' : 'var(--surface-2)', borderRadius: 10, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                  <span className="num" style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-2)', flex: '0 0 auto', marginTop: 1 }}>{String(i + 1).padStart(2, '0')}</span>
                  <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t}</span>
                </div>
              ))}
              {revealed === llmResult.length && llmResult.length > 0 && (
                <button className="btn sm" onClick={runAI} style={{ alignSelf: 'flex-start' }}><Icon.reset size={13} /> 다시 분석</button>
              )}
            </div>
          )}
        </Card>
      </div>
      </>}
    </div>
  );
}
window.ReportTab = ReportTab;
