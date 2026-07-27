/* ============================================================ Scenario Assistant tab */
// tab-settings.jsx를 건드리지 않기 위해 검증 규칙을 이 파일에서 자체적으로 재구현 (동일 스키마)
const SC_WEIGHT_KEYS = ['w_distance', 'w_time', 'w_latency', 'w_load', 'w_resource', 'w_handover', 'w_blockage', 'w_future'];
const SC_ROUTE_ALGOS = ['dijkstra', 'astar', 'k_shortest_path', 'network_aware', 'lookahead', 'rl_routing'];
const SC_LATENCY_ALGOS = ['full_composite_latency', 'blockage_aware_latency', 'mec_aware_latency', 'distance_based_latency', 'load_aware_latency'];
const SC_BS_ALGOS = ['lowest_latency_bs', 'nearest_bs', 'load_balanced_bs'];
const SC_ALLOC_ALGOS = ['traffic_aware_allocation', 'equal_allocation', 'proportional_demand_allocation', 'load_balancing_allocation', 'latency_minimizing_allocation', 'priority_based_allocation', 'lookahead_resource_allocation'];

function scValidateField(section, key, value) {
  if (section === 'cost_weights') {
    if (!SC_WEIGHT_KEYS.includes(key)) return { valid: false, reason: '알 수 없는 키' };
    if (typeof value !== 'number' || isNaN(value) || value < 0 || value > 20) return { valid: false, reason: '0~20 범위의 숫자여야 함' };
    return { valid: true };
  }
  if (section === 'algorithm_selection') {
    const opts = { route_algorithm: SC_ROUTE_ALGOS, latency_algorithm: SC_LATENCY_ALGOS, base_station_selection_algorithm: SC_BS_ALGOS, resource_allocation_algorithm: SC_ALLOC_ALGOS }[key];
    if (!opts) return { valid: false, reason: '알 수 없는 키' };
    if (!opts.includes(value)) return { valid: false, reason: `허용된 값: ${opts.join(', ')}` };
    return { valid: true };
  }
  if (section === 'policy_options') {
    if (key === 'lookahead_k') return Number.isInteger(value) && value >= 1 && value <= 10 ? { valid: true } : { valid: false, reason: '정수 1~10' };
    if (key === 'lookahead_time') return typeof value === 'number' && value >= 1 && value <= 120 ? { valid: true } : { valid: false, reason: '숫자 1~120' };
    if (key === 'max_handover_allowed') return Number.isInteger(value) && value >= 0 && value <= 50 ? { valid: true } : { valid: false, reason: '정수 0~50' };
    if (['prefer_low_latency', 'prefer_load_balance', 'avoid_disconnection'].includes(key)) return typeof value === 'boolean' ? { valid: true } : { valid: false, reason: 'true/false여야 함' };
    if (key === 'traffic_lambda') return typeof value === 'number' && value >= 0 && value <= 200 ? { valid: true } : { valid: false, reason: '숫자 0~200' };
    if (key === 'network_mode') return ['4G', '5G', '6G'].includes(value) ? { valid: true } : { valid: false, reason: '4G/5G/6G 중 하나' };
    return { valid: false, reason: '알 수 없는 키' };
  }
  return { valid: false, reason: '알 수 없는 섹션' };
}

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

function ScenarioTab({ simConfig, setSimConfig, mode: appMode }) {
  // 주의: 이 파일은 이미 로컬 state 이름으로 'mode'(config/generate 내부 모드 전환)를 쓰고
  // 있어서, App에서 내려오는 Lite/Pro 모드 prop은 destructure 시 appMode로 리네임해서 받는다.
  const [mode, setMode] = useState('config'); // 'config' | 'generate'
  const [inputText, setInputText] = useState('');
  const [inputType, setInputType] = useState('nl');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [diff, setDiff] = useState(null);
  const [rationale, setRationale] = useState({});
  const [applied, setApplied] = useState(false);
  const [history, setHistory] = useState(() => scLoadHistory());

  // ---- 시나리오 생성·배치 모드 (Phase 3/4) ----
  const [genDesc, setGenDesc] = useState('');
  const [genCount, setGenCount] = useState(5);
  const [genLoading, setGenLoading] = useState(false);
  const [genError, setGenError] = useState(null);
  const [genScenarios, setGenScenarios] = useState([]); // [{...spec, _selected}]
  const [genWarnings, setGenWarnings] = useState([]);
  const [batchId, setBatchId] = useState(null);
  const [batchInfo, setBatchInfo] = useState(null); // {status, total, completed, results, label}
  const [batchError, setBatchError] = useState(null);

  // Lite는 '설정 변경 제안'의 자연어 입력만 노출 — Pro에서 보던 중 Lite로 전환되면 되돌린다.
  useEffect(() => { if (appMode === 'lite' && mode !== 'config') setMode('config'); }, [appMode, mode]);
  useEffect(() => { if (appMode === 'lite' && inputType !== 'nl') setInputType('nl'); }, [appMode, inputType]);

  useEffect(() => {
    if (!batchId || batchInfo?.status === 'completed') return;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`http://127.0.0.1:8001/api/scenarios/batch/${batchId}`);
        const data = await res.json();
        setBatchInfo(data);
        if (data.status === 'completed') {
          const saved = scbLoadBatches();
          saved.push({ batch_id: batchId, label: data.label, started_at: data.started_at, ended_at: data.ended_at, results: data.results });
          scbSaveBatches(saved);
        }
      } catch {}
    }, 1200);
    return () => clearInterval(timer);
  }, [batchId, batchInfo?.status]);

  async function generateScenarios() {
    if (!genDesc.trim()) return;
    setGenLoading(true); setGenError(null); setGenScenarios([]); setGenWarnings([]);
    setBatchId(null); setBatchInfo(null); setBatchError(null);
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

  async function runScenarioBatch() {
    const selected = genScenarios.filter(s => s._selected).map(({ _selected, ...rest }) => rest);
    if (selected.length === 0) return;
    setBatchError(null);
    try {
      const res = await fetch('http://127.0.0.1:8001/api/scenarios/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: genDesc.slice(0, 40), scenarios: selected }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setBatchId(data.batch_id);
      setBatchInfo({ status: 'running', total: data.total, completed: 0, results: [], label: genDesc.slice(0, 40) });
    } catch (e) {
      setBatchError(e.message || '배치 실행 중 오류가 발생했습니다.');
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
                <tr><th></th><th>레이블</th><th>모드</th><th className="r">교통량</th><th className="r">seed</th></tr>
              </thead>
              <tbody>
                {genScenarios.map((s, i) => (
                  <tr key={s.id || i}>
                    <td><input type="checkbox" checked={!!s._selected} onChange={() => toggleGenSelect(i)} /></td>
                    <td>{s.label}</td>
                    <td><Chip tone="brand">{s.mode}</Chip></td>
                    <td className="r"><span className="num">{s.demand_scale_pct != null ? s.demand_scale_pct + '%' : (s.vehicle_count ?? '—')}</span></td>
                    <td className="r"><span className="num muted">{s.seed}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row gap8" style={{ marginTop: 10 }}>
            <button
              className="btn accent"
              disabled={!genScenarios.some(s => s._selected) || batchInfo?.status === 'running'}
              onClick={runScenarioBatch}
            >
              <Icon.play size={14} /> 선택한 {genScenarios.filter(s => s._selected).length}개 배치 실행
            </button>
            {batchError && <span style={{ fontSize: 11.5, color: 'var(--bad)' }}>{batchError}</span>}
          </div>
        </>}
      </Card>

      {batchInfo && (
        <Card title="배치 실행 결과" en="Batch run result"
          right={<Chip tone={batchInfo.status === 'completed' ? 'good' : 'brand'} dot>
            {batchInfo.status === 'completed' ? '완료' : '실행 중'} {batchInfo.completed}/{batchInfo.total}
          </Chip>}>
          {batchInfo.results?.length > 0 ? (
            <div className="col gap6">
              {batchInfo.results.map((r, i) => (
                <div key={i} className="row gap8" style={{ fontSize: 11.5, padding: '6px 10px', background: 'var(--surface-2)', borderRadius: 7, border: '1px solid var(--border)' }}>
                  <Chip tone={r.status === 'done' ? 'good' : 'bad'}>{r.status === 'done' ? '성공' : '실패'}</Chip>
                  <span style={{ flex: 1 }}>{r.label || r.id}</span>
                  {r.status === 'done' && r.mode === 'route_metrics' && (
                    <span className="num muted">총비용 {r.route_cost_result?.total_cost != null ? r.route_cost_result.total_cost.toFixed(2) : '—'}</span>
                  )}
                  {r.status === 'done' && r.mode === 'rl_episode' && (
                    <span className="num muted">reward {(r.total_reward ?? r.mean_reward) != null ? (r.total_reward ?? r.mean_reward).toFixed(2) : '—'}</span>
                  )}
                  {r.status === 'error' && <span style={{ color: 'var(--bad)' }}>{r.error}</span>}
                </div>
              ))}
            </div>
          ) : (
            <div className="muted" style={{ padding: 16, fontSize: 12 }}>실행 중… 결과가 들어오는 대로 표시됩니다.</div>
          )}
          {batchInfo.status === 'completed' && (
            <div className="muted" style={{ fontSize: 10.5, marginTop: 10 }}>
              결과가 저장되었습니다 — 분석 보고서 탭의 "시나리오 배치 비교"에서 자세히 볼 수 있습니다.
            </div>
          )}
        </Card>
      )}
      </>}
    </div>
  );
}
window.ScenarioTab = ScenarioTab;
