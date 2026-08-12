/* ============================================================ Scenario Assistant tab */
// 검증에 쓰는 "허용되는 값" 목록의 **유일한 출처는 서버**(/api/scenarios/options)다.
// ⚠️ 예전에는 이 파일이 목록을 손으로 들고 있었고 서버와 어긋나 있었다 — 백엔드 기본값인
//    rsrp_max(기지국 선택)와 tech_latency_v31(지연 계산)이 아래 목록에 아예 없어서,
//    LLM이 옳은 값을 골라도 화면이 빨간 "검증 실패"로 거절했다(2026-08-12 실측).
//    아래 값은 서버를 못 불렀을 때만 쓰는 최소 폴백이다.
let SC_OPTIONS = {
  cost_weights: {
    keys: ['w_distance', 'w_time', 'w_latency', 'w_load', 'w_resource', 'w_handover', 'w_blockage', 'w_future'],
    min: 0, max: 20,
  },
  algorithm_selection: {
    route_algorithm: ['dijkstra', 'astar', 'k_shortest_path', 'network_aware', 'lookahead', 'rl_routing'],
    latency_algorithm: ['tech_latency_v31', 'full_composite_latency', 'blockage_aware_latency', 'mec_aware_latency', 'distance_based_latency', 'load_aware_latency'],
    base_station_selection_algorithm: ['rsrp_max', 'nearest_bs', 'lowest_latency_bs', 'strongest_signal_bs', 'load_balanced_bs', 'look_ahead_bs_selection', 'rl_based_bs_selection'],
    resource_allocation_algorithm: ['traffic_aware_allocation', 'equal_allocation', 'proportional_demand_allocation', 'load_balancing_allocation', 'latency_minimizing_allocation', 'priority_based_allocation', 'lookahead_resource_allocation'],
  },
  policy_options: {
    lookahead_k: { type: 'int', min: 1, max: 10 },
    lookahead_time: { type: 'float', min: 1, max: 120 },
    max_handover_allowed: { type: 'int', min: 0, max: 50 },
    prefer_low_latency: { type: 'bool' },
    prefer_load_balance: { type: 'bool' },
    avoid_disconnection: { type: 'bool' },
    traffic_lambda: { type: 'float', min: 0, max: 200 },
    other_device_lambda: { type: 'float', min: 0, max: 2000 },
    demand_scale_pct: { type: 'float', min: 10, max: 300 },
    bg_reroute_prob: { type: 'float', min: 0, max: 1 },
    network_mode: { type: 'enum', values: ['4G', '5G', '6G'] },
    bg_reroute_mode: { type: 'enum', values: ['random', 'congestion'] },
  },
};

function scValidateField(section, key, value) {
  if (section === 'cost_weights') {
    const cw = SC_OPTIONS.cost_weights || {};
    if (!(cw.keys || []).includes(key)) return { valid: false, reason: '알 수 없는 키' };
    if (typeof value !== 'number' || isNaN(value) || value < cw.min || value > cw.max)
      return { valid: false, reason: `${cw.min}~${cw.max} 범위의 숫자여야 함` };
    return { valid: true };
  }
  if (section === 'algorithm_selection') {
    const opts = (SC_OPTIONS.algorithm_selection || {})[key];
    if (!Array.isArray(opts)) return { valid: false, reason: '알 수 없는 키' };
    if (!opts.includes(value)) return { valid: false, reason: `허용된 값: ${opts.join(', ')}` };
    return { valid: true };
  }
  if (section === 'policy_options') {
    const rule = (SC_OPTIONS.policy_options || {})[key];
    if (!rule) return { valid: false, reason: '알 수 없는 키' };
    if (rule.type === 'bool') return typeof value === 'boolean' ? { valid: true } : { valid: false, reason: 'true/false여야 함' };
    if (rule.type === 'enum') return (rule.values || []).includes(value) ? { valid: true } : { valid: false, reason: `${(rule.values || []).join('/')} 중 하나` };
    if (rule.type === 'int') return Number.isInteger(value) && value >= rule.min && value <= rule.max ? { valid: true } : { valid: false, reason: `정수 ${rule.min}~${rule.max}` };
    return typeof value === 'number' && !isNaN(value) && value >= rule.min && value <= rule.max
      ? { valid: true } : { valid: false, reason: `숫자 ${rule.min}~${rule.max}` };
  }
  return { valid: false, reason: '알 수 없는 섹션' };
}

// 앱이 뜨면 한 번 받아와 위 폴백을 덮어쓴다. 실패해도 폴백으로 계속 동작한다.
// (이 파일의 오래된 fetch들은 8001을 박아 두었지만, 앱은 백엔드가 서빙하므로 origin이 정답이다.)
fetch(`${window.location.origin}/api/scenarios/options`)
  .then(r => (r.ok ? r.json() : null))
  .then(d => { if (d && d.algorithm_selection) SC_OPTIONS = d; })
  .catch(() => {});

const SC_HISTORY_KEY = 'v2x_scenario_history';
function scLoadHistory() {
  try { return JSON.parse(localStorage.getItem(SC_HISTORY_KEY) || '[]'); } catch { return []; }
}
function scSaveHistory(list) {
  try { localStorage.setItem(SC_HISTORY_KEY, JSON.stringify(list.slice(-15))); } catch {}
}

// 시나리오 생성·배치 모드(Phase 3/4)가 완료한 배치 결과 — 분석 보고서 탭의
// "시나리오 배치 비교" 카드가 같은 키를 읽어 표로 보여준다.
const SCB_BATCH_KEY = 'v2x_scenario_batches';
function scbLoadBatches() {
  try { return JSON.parse(localStorage.getItem(SCB_BATCH_KEY) || '[]'); } catch { return []; }
}
function scbSaveBatches(list) {
  try { localStorage.setItem(SCB_BATCH_KEY, JSON.stringify(list.slice(-10))); } catch {}
}

// 작업 중이던 내용(입력글·변경안·생성 목록·돌고 있는 배치 번호)을 통째로 담아두는 초안.
// app.jsx가 이 탭을 언마운트하지 않게 바꿔서 탭 이동에는 이미 견디지만, 새로고침(F5)까지
// 살아남으려면 저장이 필요하다.
// ⚠️ 배치 **결과**는 넣지 않는다 — 시나리오 하나당 per_edge·telemetry까지 붙어 있어
//    금방 localStorage 용량을 넘긴다. 번호표(batch_id)만 남기면 폴러가 서버에서 다시 받아온다.
const SC_DRAFT_KEY = 'v2x_scenario_draft';
function scLoadDraft() {
  try { return JSON.parse(localStorage.getItem(SC_DRAFT_KEY) || '{}') || {}; } catch { return {}; }
}
function scSaveDraft(draft) {
  try { localStorage.setItem(SC_DRAFT_KEY, JSON.stringify(draft)); } catch {}
}

const SC_SECTION_LABEL = { cost_weights: '비용 가중치', algorithm_selection: '알고리즘 선택', policy_options: '정책 옵션' };

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// 코드 모드 전용 — 투명 textarea(실제 입력) + 그 밑에 Prism로 강조된 <pre> 레이어를 겹쳐서
// VSCode 스타일 문법 강조를 흉내낸다. 자연어 모드는 평범한 textarea 그대로 사용.
function HighlightedCodeInput({ value, onChange, placeholder }) {
  const taRef = useRef(null);
  const preRef = useRef(null);
  const sharedStyle = {
    margin: 0, width: '100%', height: '100%', padding: '12px 14px',
    fontFamily: 'var(--mono)', fontSize: 12.5, lineHeight: 1.55,
    whiteSpace: 'pre-wrap', wordBreak: 'break-word', boxSizing: 'border-box', border: 'none',
  };
  const html = window.Prism
    ? window.Prism.highlight(value || '', window.Prism.languages.json, 'json')
    : escapeHtml(value || '');

  function syncScroll(e) {
    if (preRef.current) {
      preRef.current.scrollTop = e.target.scrollTop;
      preRef.current.scrollLeft = e.target.scrollLeft;
    }
  }

  return (
    <div style={{ position: 'relative', minHeight: 130, resize: 'vertical', overflow: 'hidden', border: '1px solid var(--border)', borderRadius: 9, background: 'var(--surface-2)' }}>
      <pre ref={preRef} className="sc-code-layer" aria-hidden style={{ ...sharedStyle, position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>
        {/* code 태그는 브라우저 기본 스타일(font-family: monospace)이 부모의 var(--mono) 상속을 가려버려서
            textarea(투명 텍스트)와 글자 너비가 달라지고 커서가 어긋나는 원인이 됨 — 명시적으로 다시 지정 */}
        <code style={{ fontFamily: 'inherit' }} dangerouslySetInnerHTML={{ __html: html + '\n' }} />
      </pre>
      <textarea
        ref={taRef}
        value={value}
        onChange={onChange}
        onScroll={syncScroll}
        placeholder={placeholder}
        spellCheck={false}
        style={{ ...sharedStyle, position: 'absolute', inset: 0, background: 'transparent', color: 'transparent', caretColor: 'var(--ink)', resize: 'none', outline: 'none' }}
      />
    </div>
  );
}

function ScenarioTab({ simConfig, setSimConfig, mode: appMode,
                       sheets, setSheets, setActiveSheetIdx, api, go }) {
  // 주의: 이 파일은 이미 로컬 state 이름으로 'mode'(config/generate 내부 모드 전환)를 쓰고
  // 있어서, App에서 내려오는 Lite/Pro 모드 prop은 destructure 시 appMode로 리네임해서 받는다.
  // 새로고침 직전에 저장해둔 초안 — **최초 1회만** 읽는다(lazy initializer).
  const [draft] = useState(scLoadDraft);

  const [mode, setMode] = useState(draft.mode || 'config'); // 'config' | 'generate'
  const [inputText, setInputText] = useState(draft.inputText || '');
  const [inputType, setInputType] = useState(draft.inputType || 'nl');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [diff, setDiff] = useState(draft.diff || null);
  const [rationale, setRationale] = useState(draft.rationale || {});
  const [applied, setApplied] = useState(false);
  const [history, setHistory] = useState(() => scLoadHistory());

  // ---- 시나리오 생성·배치 모드 (Phase 3/4) ----
  const [genDesc, setGenDesc] = useState(draft.genDesc || '');
  const [genCount, setGenCount] = useState(draft.genCount || 5);
  const [genLoading, setGenLoading] = useState(false);
  const [genError, setGenError] = useState(null);
  const [genScenarios, setGenScenarios] = useState(draft.genScenarios || []); // [{...spec, _selected}]
  const [genWarnings, setGenWarnings] = useState(draft.genWarnings || []);
  // ---- 시나리오 "적용" (2026-08-12 구조 변경) ----
  // 어시스턴트는 더 이상 시뮬레이션을 **실행하지 않는다.** 시나리오 1개를 시뮬레이션 탭의
  // 시트 1개로 만들어 두기만 하고, 실행은 사용자가 그 탭에서 직접 누른다.
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState(null);
  const [applyLog, setApplyLog] = useState([]);      // 진행 문구(줄 단위)
  const [applyDone, setApplyDone] = useState(null);  // {count} — 끝난 뒤 안내

  // 바뀔 때마다 초안을 덮어쓴다. loading/error 같은 일시적인 값은 담지 않는다 —
  // 새로고침 뒤에 "분석 중…"이나 지난 오류가 되살아나면 안 되기 때문.
  useEffect(() => {
    scSaveDraft({ mode, inputType, inputText, diff, rationale,
                  genDesc, genCount, genScenarios, genWarnings });
  }, [mode, inputType, inputText, diff, rationale, genDesc, genCount, genScenarios, genWarnings]);

  // Lite는 '설정 변경 제안'의 자연어 입력만 노출 — Pro에서 보던 중 Lite로 전환되면 되돌린다.
  useEffect(() => { if (appMode === 'lite' && mode !== 'config') setMode('config'); }, [appMode, mode]);
  useEffect(() => { if (appMode === 'lite' && inputType !== 'nl') setInputType('nl'); }, [appMode, inputType]);

  async function generateScenarios() {
    if (!genDesc.trim()) return;
    setGenLoading(true); setGenError(null); setGenScenarios([]); setGenWarnings([]);
    setApplyError(null); setApplyLog([]); setApplyDone(null);
    try {
      const res = await fetch('http://127.0.0.1:8001/api/scenarios/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: genDesc, count: genCount }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setGenScenarios((data.scenarios || []).map(s => ({ ...s, _selected: true })));
      setGenWarnings(data.warnings || []);
    } catch (e) {
      setGenError(e.message || '시나리오 생성 중 오류가 발생했습니다.');
    } finally {
      setGenLoading(false);
    }
  }

  function toggleGenSelect(i) {
    setGenScenarios(prev => prev.map((s, idx) => idx === i ? { ...s, _selected: !s._selected } : s));
  }

  // 시나리오의 algorithm_selection(긴 키)을 시뮬레이션 시트가 쓰는 짧은 키로 옮긴다.
  function scToSheetAlgorithms(algoSel) {
    const A = algoSel || {};
    const base = (typeof DEFAULT_ALGORITHM_SELECTION !== 'undefined')
      ? DEFAULT_ALGORITHM_SELECTION
      : { route: 'dijkstra', latency: 'tech_latency_v31', base_station_selection: 'rsrp_max', resource_allocation: 'equal_allocation' };
    const out = { ...base };
    if (A.route_algorithm) out.route = A.route_algorithm;
    if (A.latency_algorithm) out.latency = A.latency_algorithm;
    if (A.base_station_selection_algorithm) out.base_station_selection = A.base_station_selection_algorithm;
    if (A.resource_allocation_algorithm) out.resource_allocation = A.resource_allocation_algorithm;
    return out;
  }

  /**
   * 고른 시나리오를 시뮬레이션 탭의 **시트로 만들어 둔다** (실행하지 않는다).
   *
   * 시나리오 하나 = 시트 하나. 구역(OSM 도로망)은 모두가 공유하고, 그 위의 출발·도착점,
   * 기지국/RSU, 알고리즘, 교통량 배율만 시트마다 다르다.
   *
   * 기지국은 DB에 전역으로 한 벌뿐이라 순서가 중요하다 — 시나리오마다 배치한 **직후**
   * 그 목록을 시트에 담아 둔다. 마지막 시나리오의 배치가 DB에 남지만, 시트를 전환하면
   * 시뮬레이션 탭이 그 시트의 목록으로 되돌려 놓는다(replace-user-created).
   */
  async function applyScenarios() {
    const selected = genScenarios.filter(s => s._selected);
    if (selected.length === 0 || applying) return;
    const base = api || 'http://127.0.0.1:8001';
    setApplying(true); setApplyError(null); setApplyLog([]); setApplyDone(null);
    const note = (m) => setApplyLog(prev => [...prev, m]);
    try {
      const made = [];
      for (let i = 0; i < selected.length; i++) {
        const s = selected[i];
        const nBs = s.n_bs || 0, nRsu = s.n_rsu || 0;

        if (nBs + nRsu > 0) {
          const how = s.placement_method === 'sa' ? '최적화 배치(교통량 계산 후)' : '랜덤 배치';
          note(`[${i + 1}/${selected.length}] ${s.label}: 기지국 ${nBs}개 · RSU ${nRsu}개 ${how} 중…`);
          const res = await fetch(`${base}/network-nodes/auto-place`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              n_bs: nBs, n_rsu: nRsu,
              method: s.placement_method || 'random',
              network_mode: s.network_mode || '5G',
              spread: 10,
              seed: s.seed,
              replace_existing: true,   // 시트마다 독립된 배치 — 앞 시나리오 것을 물려받지 않는다
            }),
          });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(`${s.label}: ${body.detail || res.statusText}`);
        } else {
          note(`[${i + 1}/${selected.length}] ${s.label}: 기지국 개수 지정 없음 — 지금 배치를 그대로 사용`);
        }

        // 방금 깔린(또는 그대로인) 목록을 이 시트의 것으로 확정한다.
        const nres = await fetch(`${base}/network-nodes`);
        const nbody = await nres.json().catch(() => ({}));
        const stations = (nbody.nodes || []).filter(n => n.source === 'user_created');

        const sheetSimConfig = {
          ...(simConfig || {}),
          cost_weights: { ...((simConfig || {}).cost_weights || {}), ...((s.simulation_config || {}).cost_weights || {}) },
          algorithm_selection: { ...((simConfig || {}).algorithm_selection || {}), ...((s.simulation_config || {}).algorithm_selection || {}) },
          policy_options: { ...((simConfig || {}).policy_options || {}), ...((s.simulation_config || {}).policy_options || {}) },
        };

        made.push({
          id: `sheet-sc-${Date.now()}-${i}`,
          name: s.label || `시나리오 ${i + 1}`,
          config: {
            origin: s.origin, dest: s.dest,
            demandScalePct: s.demand_scale_pct ?? 100,
            selectedAlgorithms: scToSheetAlgorithms(s.algorithm_selection),
            networkGen: (s.network_mode || '5G').toLowerCase(),
            simConfig: sheetSimConfig,
            stations,
          },
          result: null,
          status: 'draft',
          source: 'scenario_assistant',
        });
        note(`[${i + 1}/${selected.length}] ${s.label}: 시트로 만들었습니다 (기지국 ${stations.length}개).`);
      }

      const firstNewIdx = (sheets || []).length;
      const next = [...(sheets || []), ...made];
      setSheets(next);
      if (typeof saveSimSheets === 'function') saveSimSheets(next);
      // 첫 새 시트를 활성화 — 시뮬레이션 탭이 이 변화를 보고 그 시트의 출발·도착점과
      // 기지국을 화면·DB에 복원한다.
      setActiveSheetIdx(firstNewIdx);
      setApplyDone({ count: made.length });
    } catch (e) {
      setApplyError(e.message || '적용 중 오류가 발생했습니다.');
    } finally {
      setApplying(false);
    }
  }

  // diff의 각 필드를 검증해 적용 가능한 변경 목록을 만든다
  const fields = [];
  if (diff) {
    for (const section of ['cost_weights', 'algorithm_selection', 'policy_options']) {
      const sectionDiff = diff[section];
      if (!sectionDiff || typeof sectionDiff !== 'object') continue;
      for (const [key, value] of Object.entries(sectionDiff)) {
        const current = simConfig?.[section]?.[key];
        const check = scValidateField(section, key, value);
        fields.push({ section, key, current, proposed: value, ...check, reason_text: rationale[key] || rationale[`${section}.${key}`] || null });
      }
    }
  }
  const validFields = fields.filter(f => f.valid);

  async function analyze() {
    if (!inputText.trim()) return;
    setLoading(true); setError(null); setDiff(null); setApplied(false);
    try {
      const res = await fetch('http://127.0.0.1:8001/api/scenarios/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_text: inputText, input_type: inputType, current_config: simConfig || {} }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      setDiff(data.diff || {});
      setRationale(data.rationale || {});
      setLoading(false);
    } catch (e) {
      setLoading(false);
      setError(e.message || '분석 중 오류가 발생했습니다.');
    }
  }

  function applyDiff() {
    if (!simConfig || validFields.length === 0) return;
    const next = JSON.parse(JSON.stringify(simConfig));
    for (const f of validFields) {
      next[f.section] = next[f.section] || {};
      next[f.section][f.key] = f.proposed;
    }
    setSimConfig(next);
    const entry = { timestamp: new Date().toISOString(), input_text: inputText, applied_diff: validFields.map(f => ({ section: f.section, key: f.key, from: f.current, to: f.proposed })) };
    const nextHistory = [...history, entry];
    setHistory(nextHistory);
    scSaveHistory(nextHistory);
    setApplied(true);
    setTimeout(() => setApplied(false), 2000);
  }

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">AI Config Assistant</div>
          <h1>시나리오 어시스턴트 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Scenario</span></h1>
          <div className="sub">
            {mode === 'config'
              ? '자연어 또는 JSON/코드로 시나리오를 설명하면 시뮬레이션 설정 변경안을 제안합니다'
              : '자연어로 시나리오 묶음을 설명하면 LLM이 출발지·목적지·교통량 배율을 제안하고, 실제 도로망에 맞춰 자동 보정한 뒤 즉시 일괄 평가합니다'}
          </div>
        </div>
        {mode === 'config' && appMode === 'pro'
          ? <Seg value={inputType} onChange={setInputType} options={[{ v: 'nl', label: '자연어' }, { v: 'code', label: '코드/JSON' }]} />
          : null}
      </div>

      <div className="row gap8" style={{ marginBottom: 18 }}>
        <Seg value={mode} onChange={setMode} options={
          appMode === 'pro'
            ? [{ v: 'config', label: '설정 변경 제안' }, { v: 'generate', label: '시나리오 생성·배치' }]
            : [{ v: 'config', label: '설정 변경 제안' }]
        } />
      </div>

      {mode === 'config' && <>
      <Card title="시나리오 입력" en="Scenario input" style={{ marginBottom: 18 }}>
        {inputType === 'code' ? (
          <HighlightedCodeInput
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            placeholder='예: { "policy_options": { "network_mode": "5G", "avoid_disconnection": true } }'
          />
        ) : (
          <textarea
            className="input"
            style={{ width: '100%', height: 'auto', minHeight: 130, fontFamily: 'var(--sans)', fontSize: 13, lineHeight: 1.7, padding: '12px 14px', resize: 'vertical' }}
            placeholder="예: 혼잡 시간대라 latency에 민감하게, 핸드오버는 최소화해서 설정해줘"
            value={inputText}
            onChange={e => setInputText(e.target.value)}
          />
        )}
        <div className="row gap8" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={loading || !inputText.trim()} onClick={analyze}>
            {loading ? '분석 중…' : <><Icon.spark size={15} /> 분석</>}
          </button>
          {error && <span style={{ fontSize: 11.5, color: 'var(--bad)' }}>{error}</span>}
        </div>
      </Card>

      {diff && (
        <Card title="변경안 미리보기" en="Diff preview" right={<Chip>{validFields.length}/{fields.length}개 적용 가능</Chip>} style={{ marginBottom: 18 }}>
          {fields.length === 0 ? (
            <div className="muted" style={{ padding: 16, fontSize: 12 }}>변경할 항목을 찾지 못했습니다.</div>
          ) : (
            <>
              <div className="col gap8">
                {fields.map((f, i) => (
                  <div key={i} className="row gap12" style={{ padding: '10px 12px', background: f.valid ? 'var(--surface-2)' : 'var(--bad-tint)', borderRadius: 8, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                    <Chip tone={f.valid ? 'brand' : 'bad'}>{SC_SECTION_LABEL[f.section]}</Chip>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 12 }}>
                        <span className="mono" style={{ fontWeight: 600 }}>{f.key}</span>{': '}
                        <span className="num muted">{String(f.current ?? '—')}</span>
                        <span style={{ margin: '0 6px', opacity: 0.5 }}>→</span>
                        <span className="num" style={{ fontWeight: 700, color: f.valid ? 'var(--brand-2)' : 'var(--bad)' }}>{String(f.proposed)}</span>
                      </div>
                      {f.reason_text && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{f.reason_text}</div>}
                      {!f.valid && <div style={{ fontSize: 11, marginTop: 3, color: 'var(--bad)' }}>검증 실패: {f.reason}</div>}
                    </div>
                  </div>
                ))}
              </div>
              <div className="row gap8" style={{ marginTop: 14 }}>
                <button className={'btn ' + (applied ? 'good' : 'accent')} disabled={validFields.length === 0} onClick={applyDiff}>
                  {applied ? <><Icon.check size={15} /> 적용됨</> : `적용 (${validFields.length}개)`}
                </button>
              </div>
            </>
          )}
        </Card>
      )}

      <Card title="적용 이력" en="Applied history" right={<Chip>{history.length}건</Chip>}>
        {history.length === 0 ? (
          <div className="muted" style={{ padding: 16, fontSize: 12 }}>아직 적용된 시나리오가 없습니다.</div>
        ) : (
          <div className="col gap8">
            {[...history].reverse().slice(0, 8).map((h, i) => (
              <div key={i} style={{ padding: '8px 10px', background: 'var(--surface-2)', borderRadius: 7, fontSize: 11.5 }}>
                <div className="row gap8" style={{ marginBottom: 3 }}>
                  <span className="num muted" style={{ fontSize: 10.5 }}>{new Date(h.timestamp).toLocaleString('ko-KR')}</span>
                  <Chip>{h.applied_diff.length}개 변경</Chip>
                </div>
                <div style={{ color: 'var(--ink-2)', marginBottom: 3 }}>{h.input_text}</div>
                <div className="muted" style={{ fontSize: 10.5 }}>
                  {h.applied_diff.map(d => `${d.key}: ${d.from}→${d.to}`).join(' · ')}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      </>}

      {mode === 'generate' && appMode === 'pro' && <>
      <Card title="시나리오 생성" en="LLM scenario generation" style={{ marginBottom: 18 }}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
          구역이 설정되어 있어야 합니다(snap-to-road에 실제 도로 그래프가 필요). 생성된 좌표는
          LLM의 추정값이 아니라 가장 가까운 실제 도로 노드로 보정된 값입니다.
        </div>
        <textarea
          className="input"
          style={{ width: '100%', height: 'auto', minHeight: 90, fontFamily: 'var(--sans)', fontSize: 13, lineHeight: 1.6, padding: '12px 14px', resize: 'vertical' }}
          placeholder="예: 혼잡 시나리오 5개를 만들어줘. 교통량은 50%부터 250%까지 다양하게 섞어줘."
          value={genDesc}
          onChange={e => setGenDesc(e.target.value)}
        />
        <div className="row gap8" style={{ marginTop: 10, alignItems: 'center' }}>
          <label className="muted" style={{ fontSize: 11.5 }}>생성 개수</label>
          <input
            type="number" className="input" min={1} max={20} value={genCount}
            onChange={e => setGenCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
            style={{ width: 64, padding: '4px 8px' }}
          />
          <button className="btn primary" disabled={genLoading || !genDesc.trim()} onClick={generateScenarios}>
            {genLoading ? '생성 중…' : <><Icon.spark size={15} /> 생성</>}
          </button>
          {genError && <span style={{ fontSize: 11.5, color: 'var(--bad)' }}>{genError}</span>}
        </div>

        {genWarnings.length > 0 && (
          <div className="col gap4" style={{ marginTop: 10 }}>
            {genWarnings.map((w, i) => (
              <div key={i} style={{ fontSize: 11, color: 'var(--warn)' }}>⚠ {w}</div>
            ))}
          </div>
        )}

        {genScenarios.length > 0 && <>
          <div className="tbl-wrap" style={{ marginTop: 14 }}>
            <table className="tbl">
              <thead>
                <tr><th></th><th>레이블</th><th className="r">교통량</th><th className="r">기지국·RSU</th><th>알고리즘</th><th className="r">seed</th></tr>
              </thead>
              <tbody>
                {genScenarios.map((s, i) => {
                  const nBs = s.n_bs || 0, nRsu = s.n_rsu || 0;
                  const algos = Object.values(s.algorithm_selection || {});
                  return (
                    <tr key={s.id || i}>
                      <td><input type="checkbox" checked={!!s._selected} onChange={() => toggleGenSelect(i)} /></td>
                      <td>{s.label}</td>
                      <td className="r"><span className="num">{s.demand_scale_pct != null ? s.demand_scale_pct + '%' : (s.vehicle_count ?? '—')}</span></td>
                      <td className="r">
                        {nBs + nRsu > 0
                          ? <span className="num">{nBs}·{nRsu}{' '}
                              <span className="muted" style={{ fontSize: 10 }}>
                                {s.placement_method === 'sa' ? '최적화' : '랜덤'}
                              </span>
                            </span>
                          : <span className="muted">현재 유지</span>}
                      </td>
                      <td>
                        {algos.length === 0
                          ? <span className="muted" style={{ fontSize: 10.5 }}>기본값</span>
                          : <span className="mono" style={{ fontSize: 10 }}>{algos.join(', ')}</span>}
                      </td>
                      <td className="r"><span className="num muted">{s.seed}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="row gap8" style={{ marginTop: 10, alignItems: 'center' }}>
            <button
              className="btn accent"
              disabled={!genScenarios.some(s => s._selected) || applying}
              onClick={applyScenarios}
            >
              <Icon.check size={14} /> {applying ? '적용 중…' : `선택한 ${genScenarios.filter(s => s._selected).length}개 배치 적용`}
            </button>
            <span className="muted" style={{ fontSize: 10.5 }}>
              적용만 합니다 — 실행은 시뮬레이션 탭에서 직접 시작하세요.
            </span>
            {applyError && <span style={{ fontSize: 11.5, color: 'var(--bad)' }}>{applyError}</span>}
          </div>
        </>}
      </Card>

      {(applying || applyLog.length > 0 || applyDone) && (
        <Card title="적용 결과" en="Applied to simulation"
          right={<Chip tone={applyDone ? 'good' : 'brand'} dot>{applyDone ? '적용됨' : '적용 중'}</Chip>}>
          {/* 최적화 배치를 고르면 교통량 계산을 먼저 기다리므로 수 분씩 조용할 수 있다.
              무엇을 하는 중인지 보여주지 않으면 사용자는 멈춘 것으로 본다. */}
          <div className="col gap4">
            {applyLog.map((line, i) => (
              <div key={i} className="mono muted" style={{ fontSize: 10.5, lineHeight: 1.5, wordBreak: 'break-word' }}>
                {line}
              </div>
            ))}
            {applying && (
              <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
                최적화 배치는 교통량 계산이 끝나야 해서 수 분이 걸릴 수 있습니다.
                다른 탭으로 이동해도 계속 진행됩니다.
              </div>
            )}
          </div>
          {applyDone && (
            <div style={{ marginTop: 12, padding: '10px 12px', background: 'var(--good-tint)', borderRadius: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                시트 {applyDone.count}개를 시뮬레이션 탭에 만들었습니다.
              </div>
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                출발·도착점, 기지국·RSU, 알고리즘, 교통량이 시트마다 들어가 있습니다.
                시뮬레이션 탭에서 시트를 고르고 <b>시작</b>을 누르세요.
              </div>
              <button className="btn accent sm" onClick={() => go && go('simulation')}>
                <Icon.play size={13} /> 시뮬레이션 탭으로 이동
              </button>
            </div>
          )}
        </Card>
      )}
      </>}
    </div>
  );
}
window.ScenarioTab = ScenarioTab;
