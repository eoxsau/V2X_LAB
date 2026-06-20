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

const SC_SECTION_LABEL = { cost_weights: '비용 가중치', algorithm_selection: '알고리즘 선택', policy_options: '정책 옵션' };

function ScenarioTab({ simConfig, setSimConfig }) {
  const [inputText, setInputText] = useState('');
  const [inputType, setInputType] = useState('nl');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [diff, setDiff] = useState(null);
  const [rationale, setRationale] = useState({});
  const [applied, setApplied] = useState(false);
  const [history, setHistory] = useState(() => scLoadHistory());

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
          <div className="sub">자연어 또는 JSON/코드로 시나리오를 설명하면 시뮬레이션 설정 변경안을 제안합니다</div>
        </div>
        <Seg value={inputType} onChange={setInputType} options={[{ v: 'nl', label: '자연어' }, { v: 'code', label: '코드/JSON' }]} />
      </div>

      <Card title="시나리오 입력" en="Scenario input" style={{ marginBottom: 18 }}>
        <textarea
          className="input"
          style={{ width: '100%', minHeight: 110, fontFamily: inputType === 'code' ? 'var(--mono)' : 'inherit', fontSize: 12.5, resize: 'vertical' }}
          placeholder={inputType === 'nl'
            ? '예: 혼잡 시간대라 latency에 민감하게, 핸드오버는 최소화해서 설정해줘'
            : '예: { "policy_options": { "network_mode": "5G", "avoid_disconnection": true } }'}
          value={inputText}
          onChange={e => setInputText(e.target.value)}
        />
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
    </div>
  );
}
window.ScenarioTab = ScenarioTab;
