/* ============================================================ Settings tab */
const TECH_PRESETS = {
  '4G': { L_base: '10', P_tx: '43', beta: '3.5', alpha: '120', N_max: '6', T_retx: '8', C_tech: '100' },
  '5G': { L_base: '1.0', P_tx: '46', beta: '3.0', alpha: '110', N_max: '4', T_retx: '1.0', C_tech: '500' },
  '6G': { L_base: '0.1', P_tx: '48', beta: '2.5', alpha: '100', N_max: '3', T_retx: '0.1', C_tech: '2000' },
};

// ── Stage-1 config helpers ────────────────────────────────────────────────────

const DEFAULT_SIM_CONFIG = {
  cost_weights: {
    w_distance: 1.0, w_time: 2.0, w_latency: 3.0, w_load: 1.5,
    w_resource: 1.0, w_handover: 1.0, w_blockage: 1.5, w_future: 2.5,
  },
  algorithm_selection: {
    route_algorithm: 'dijkstra',
    latency_algorithm: 'full_composite_latency',
    base_station_selection_algorithm: 'lowest_latency_bs',
    resource_allocation_algorithm: 'traffic_aware_allocation',
  },
  policy_options: {
    lookahead_k: 3, lookahead_time: 10.0, max_handover_allowed: 10,
    prefer_low_latency: true, prefer_load_balance: false, avoid_disconnection: true,
  },
};

const WEIGHT_BOUNDS = { min: 0.0, max: 20.0 };
const VALID_ROUTE_ALGORITHMS = ['dijkstra', 'astar', 'k_shortest_path', 'network_aware', 'lookahead', 'rl_routing'];
const VALID_LATENCY_ALGORITHMS = ['full_composite_latency', 'simple_latency', 'mec_aware_latency', 'distance_based_latency', 'load_aware_latency'];
const VALID_BS_ALGORITHMS = ['lowest_latency_bs', 'nearest_bs', 'load_balanced_bs'];
const VALID_ALLOC_ALGORITHMS = ['traffic_aware_allocation', 'equal_allocation', 'proportional_demand_allocation', 'load_balancing_allocation', 'latency_minimizing_allocation', 'priority_based_allocation', 'lookahead_resource_allocation'];

function validateSimulationConfig(config) {
  const errors = [];
  if (!config || typeof config !== 'object') return { valid: false, errors: ['config must be an object'], sanitized: DEFAULT_SIM_CONFIG };

  const cw = config.cost_weights || {};
  const weightKeys = ['w_distance', 'w_time', 'w_latency', 'w_load', 'w_resource', 'w_handover', 'w_blockage', 'w_future'];
  for (const k of weightKeys) {
    const v = cw[k];
    if (v !== undefined && (typeof v !== 'number' || isNaN(v) || v < 0))
      errors.push(`cost_weights.${k}: must be a non-negative number`);
  }

  const algo = config.algorithm_selection || {};
  if (algo.route_algorithm && !VALID_ROUTE_ALGORITHMS.includes(algo.route_algorithm))
    errors.push(`algorithm_selection.route_algorithm: unknown value '${algo.route_algorithm}'`);
  if (algo.latency_algorithm && !VALID_LATENCY_ALGORITHMS.includes(algo.latency_algorithm))
    errors.push(`algorithm_selection.latency_algorithm: unknown value '${algo.latency_algorithm}'`);
  if (algo.base_station_selection_algorithm && !VALID_BS_ALGORITHMS.includes(algo.base_station_selection_algorithm))
    errors.push(`algorithm_selection.base_station_selection_algorithm: unknown value`);
  if (algo.resource_allocation_algorithm && !VALID_ALLOC_ALGORITHMS.includes(algo.resource_allocation_algorithm))
    errors.push(`algorithm_selection.resource_allocation_algorithm: unknown value`);

  const pol = config.policy_options || {};
  if (pol.lookahead_k !== undefined && (!Number.isInteger(pol.lookahead_k) || pol.lookahead_k < 1 || pol.lookahead_k > 10))
    errors.push('policy_options.lookahead_k: must be integer 1–10');
  if (pol.max_handover_allowed !== undefined && (!Number.isInteger(pol.max_handover_allowed) || pol.max_handover_allowed < 0))
    errors.push('policy_options.max_handover_allowed: must be non-negative integer');

  return { valid: errors.length === 0, errors, sanitized: mergeWithDefaultConfig(config) };
}

function mergeWithDefaultConfig(userConfig) {
  if (!userConfig || typeof userConfig !== 'object') return DEFAULT_SIM_CONFIG;
  const merged = JSON.parse(JSON.stringify(DEFAULT_SIM_CONFIG));

  if (userConfig.cost_weights && typeof userConfig.cost_weights === 'object') {
    for (const k of Object.keys(merged.cost_weights)) {
      const v = userConfig.cost_weights[k];
      if (typeof v === 'number' && !isNaN(v) && v >= 0)
        merged.cost_weights[k] = Math.min(v, WEIGHT_BOUNDS.max);
    }
  }
  if (userConfig.algorithm_selection && typeof userConfig.algorithm_selection === 'object') {
    if (VALID_ROUTE_ALGORITHMS.includes(userConfig.algorithm_selection.route_algorithm))
      merged.algorithm_selection.route_algorithm = userConfig.algorithm_selection.route_algorithm;
    if (VALID_LATENCY_ALGORITHMS.includes(userConfig.algorithm_selection.latency_algorithm))
      merged.algorithm_selection.latency_algorithm = userConfig.algorithm_selection.latency_algorithm;
    if (VALID_BS_ALGORITHMS.includes(userConfig.algorithm_selection.base_station_selection_algorithm))
      merged.algorithm_selection.base_station_selection_algorithm = userConfig.algorithm_selection.base_station_selection_algorithm;
    if (VALID_ALLOC_ALGORITHMS.includes(userConfig.algorithm_selection.resource_allocation_algorithm))
      merged.algorithm_selection.resource_allocation_algorithm = userConfig.algorithm_selection.resource_allocation_algorithm;
  }
  if (userConfig.policy_options && typeof userConfig.policy_options === 'object') {
    const pol = userConfig.policy_options;
    if (Number.isInteger(pol.lookahead_k) && pol.lookahead_k >= 1 && pol.lookahead_k <= 10)
      merged.policy_options.lookahead_k = pol.lookahead_k;
    if (typeof pol.lookahead_time === 'number' && pol.lookahead_time >= 1)
      merged.policy_options.lookahead_time = Math.min(pol.lookahead_time, 120);
    if (Number.isInteger(pol.max_handover_allowed) && pol.max_handover_allowed >= 0)
      merged.policy_options.max_handover_allowed = Math.min(pol.max_handover_allowed, 50);
    if (typeof pol.prefer_low_latency === 'boolean') merged.policy_options.prefer_low_latency = pol.prefer_low_latency;
    if (typeof pol.prefer_load_balance === 'boolean') merged.policy_options.prefer_load_balance = pol.prefer_load_balance;
    if (typeof pol.avoid_disconnection === 'boolean') merged.policy_options.avoid_disconnection = pol.avoid_disconnection;
  }
  return merged;
}

function sanitizeAlgorithmSelection(config) {
  const algo = (config && config.algorithm_selection) ? { ...config.algorithm_selection } : {};
  const def = DEFAULT_SIM_CONFIG.algorithm_selection;
  return {
    route_algorithm:                  VALID_ROUTE_ALGORITHMS.includes(algo.route_algorithm) ? algo.route_algorithm : def.route_algorithm,
    latency_algorithm:                VALID_LATENCY_ALGORITHMS.includes(algo.latency_algorithm) ? algo.latency_algorithm : def.latency_algorithm,
    base_station_selection_algorithm: VALID_BS_ALGORITHMS.includes(algo.base_station_selection_algorithm) ? algo.base_station_selection_algorithm : def.base_station_selection_algorithm,
    resource_allocation_algorithm:    VALID_ALLOC_ALGORITHMS.includes(algo.resource_allocation_algorithm) ? algo.resource_allocation_algorithm : def.resource_allocation_algorithm,
  };
}

function SettingsTab({ sim, dispatch, api, simConfig, setSimConfig }) {
  const [tech, setTech] = useState(sim.mode === '6G' ? '6G' : '5G');
  const [vals, setVals] = useState(() => {
    const o = {}; DATA.params.forEach(p => { if (p.type !== 'tech') o[p.v] = p.def; }); return o;
  });
  const [saved, setSaved] = useState(false);
  const [buildingStatus, setBuildingStatus] = useState(null);
  // Stage-1: local draft of simulation config (applied on Save)
  const [cfgDraft, setCfgDraft] = useState(() => mergeWithDefaultConfig(simConfig));
  const [cfgErrors, setCfgErrors] = useState([]);
  const [cfgSaved, setCfgSaved] = useState(false);

  useEffect(() => {
    let dead = false;
    fetch(`${api}/admin/buildings/status`)
      .then(r => r.json())
      .then(data => { if (!dead) setBuildingStatus(data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [api]);

  function applyTech(t) {
    setTech(t);
    setVals(v => ({ ...v, ...TECH_PRESETS[t] }));
    if (t !== '4G') dispatch({ type: 'mode', v: t });
  }
  function save() { setSaved(true); setTimeout(() => setSaved(false), 2000); }

  function setWeight(k, raw) {
    const v = parseFloat(raw);
    setCfgDraft(d => ({ ...d, cost_weights: { ...d.cost_weights, [k]: isNaN(v) ? 0 : Math.max(0, Math.min(v, 20)) } }));
  }
  function setAlgo(k, v) {
    setCfgDraft(d => ({ ...d, algorithm_selection: { ...d.algorithm_selection, [k]: v } }));
  }
  function setPolicy(k, v) {
    setCfgDraft(d => ({ ...d, policy_options: { ...d.policy_options, [k]: v } }));
  }
  async function saveConfig() {
    const { valid, errors, sanitized } = validateSimulationConfig(cfgDraft);
    if (!valid) { setCfgErrors(errors); return; }
    setCfgErrors([]);
    setSimConfig(sanitized);
    try {
      await fetch(`${api}/api/simulation/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ simulation_config: sanitized }),
      });
    } catch (_) {}
    setCfgSaved(true);
    setTimeout(() => setCfgSaved(false), 2000);
  }
  function resetConfig() {
    setCfgDraft(mergeWithDefaultConfig(null));
    setCfgErrors([]);
  }

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Configuration</div>
          <h1>설정 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Settings</span></h1>
          <div className="sub">통신 기술 파라미터 및 시뮬레이션 시스템 설정</div>
        </div>
        <button className={'btn ' + (saved ? 'good' : 'primary')} onClick={save}>
          {saved ? <><Icon.check size={15} /> 저장 완료</> : <><Icon.check size={15} /> 설정 저장</>}
        </button>
      </div>

      {/* tech selector */}
      <Card title="네트워크 모드" en="Network technology" style={{ marginBottom: 18 }}>
        <div className="row gap12 wrap">
          {[['4G', '4G LTE', 'L_base 10ms · 표준'], ['5G', '5G NR', 'L_base 1ms · 현재'], ['6G', '6G-like', 'L_base 0.1ms · 연구목표']].map(([k, name, desc]) => (
            <button key={k} onClick={() => applyTech(k)} style={{
              flex: '1 1 200px', textAlign: 'left', padding: '15px 17px', borderRadius: 12, cursor: 'pointer',
              background: tech === k ? 'var(--brand-tint)' : 'var(--surface)',
              border: '1.5px solid ' + (tech === k ? 'var(--brand-2)' : 'var(--border)'),
            }}>
              <div className="row between" style={{ marginBottom: 6 }}>
                <span className="mono" style={{ fontSize: 16, fontWeight: 600, color: tech === k ? 'var(--brand)' : 'var(--ink)' }}>{name}</span>
                <span style={{ width: 18, height: 18, borderRadius: '50%', border: '2px solid ' + (tech === k ? 'var(--brand-2)' : 'var(--border-strong)'), display: 'grid', placeItems: 'center' }}>
                  {tech === k && <span style={{ width: 9, height: 9, borderRadius: '50%', background: 'var(--brand-2)' }} />}
                </span>
              </div>
              <div className="muted" style={{ fontSize: 11 }}>{desc}</div>
            </button>
          ))}
        </div>
        <div className="row gap8" style={{ marginTop: 14, padding: '10px 13px', background: 'var(--warn-tint)', borderRadius: 9, fontSize: 11, color: 'var(--warn-ink, var(--warn))' }}>
          <Icon.warn size={14} style={{ flex: '0 0 auto', marginTop: 1 }} />
          <span style={{ lineHeight: 1.45 }}>6G 수치는 확정 표준이 아닌 연구 목표값입니다 (표준 확정 예정: Release 21, ~2029년). C_tech는 설계 파라미터로 3GPP/ITU 규정값이 아닙니다.</span>
        </div>
      </Card>

      <Card title="기술 파라미터" en="Technical parameters" right={<span className="mono muted" style={{ fontSize: 10 }}>{tech} 프리셋 적용됨</span>} style={{ padding: 0 }}>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>파라미터<span className="en">Parameter</span></th><th>변수<span className="en">Variable</span></th><th style={{ width: 160 }}>값<span className="en">Value</span></th><th>설명<span className="en">Description</span></th></tr></thead>
            <tbody>
              {DATA.params.filter(p => p.type !== 'tech').map(p => (
                <tr key={p.v}>
                  <td><b style={{ fontWeight: 600 }}>{p.name}</b></td>
                  <td><span className="chip" style={{ fontFamily: 'var(--mono)' }}>{p.v}</span></td>
                  <td>
                    <div className="input-suffix" style={{ width: 140 }}>
                      <input className="input" style={{ height: 32 }} value={vals[p.v] ?? p.def}
                        onChange={e => setVals(v => ({ ...v, [p.v]: e.target.value }))} />
                      {p.unit && <span className="sfx">{p.unit}</span>}
                    </div>
                  </td>
                  <td><span className="muted" style={{ whiteSpace: 'normal', fontSize: 11.5, lineHeight: 1.4 }}>{p.note}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {buildingStatus && (
        <Card title="건물 데이터 상태" en="Building data status" style={{ marginTop: 18 }}>
          <div className="row between" style={{ marginBottom: 8 }}><span>처리 준비</span><b>{buildingStatus.processed_ready ? 'ready' : 'not ready'}</b></div>
          <div className="row between" style={{ marginBottom: 8 }}><span>건물 수</span><b className="num">{buildingStatus.building_count?.toLocaleString?.() || buildingStatus.building_count}</b></div>
          <div className="row between" style={{ marginBottom: 8 }}><span>HEIGHT 직접 사용</span><b className="num">{buildingStatus.height_available_count?.toLocaleString?.() || buildingStatus.height_available_count}</b></div>
          <div className="row between"><span>추정 높이</span><b className="num">{buildingStatus.height_estimated_count?.toLocaleString?.() || buildingStatus.height_estimated_count}</b></div>
          {buildingStatus.last_preprocess_time && (
            <div className="muted" style={{ marginTop: 10, fontSize: 11 }}>last preprocess: {buildingStatus.last_preprocess_time}</div>
          )}
        </Card>
      )}

      {/* ── Stage-1: Simulation Algorithm Config ─────────────────────────── */}
      <Card title="시뮬레이션 알고리즘 설정" en="Simulation config (Stage 1)"
        right={
          <div className="row gap8">
            <button className="btn" onClick={resetConfig}>초기화</button>
            <button className={'btn ' + (cfgSaved ? 'good' : 'primary')} onClick={saveConfig}>
              {cfgSaved ? <><Icon.check size={13} /> 저장됨</> : <><Icon.check size={13} /> 저장</>}
            </button>
          </div>
        }
        style={{ marginTop: 18 }}
      >
        {cfgErrors.length > 0 && (
          <div className="row gap8" style={{ marginBottom: 14, padding: '9px 12px', background: 'var(--err-tint,#fff0f0)', borderRadius: 8, fontSize: 11, color: 'var(--err,#c00)', flexWrap: 'wrap' }}>
            <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
            <span>{cfgErrors.join(' · ')}</span>
          </div>
        )}

        {/* Cost Weights */}
        <div className="muted" style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', marginBottom: 8 }}>COST WEIGHTS</div>
        <div className="tbl-wrap" style={{ marginBottom: 16 }}>
          <table className="tbl">
            <thead><tr><th>가중치</th><th>변수</th><th style={{ width: 130 }}>값 (0 – 20)</th><th>설명</th></tr></thead>
            <tbody>
              {[
                ['거리',     'w_distance', '이동 거리 비용 가중치'],
                ['시간',     'w_time',     '이동 시간 비용 가중치'],
                ['지연',     'w_latency',  '통신 지연 비용 가중치'],
                ['부하',     'w_load',     '기지국 부하 비용 가중치'],
                ['자원',     'w_resource', '자원 부족 비용 가중치'],
                ['핸드오버', 'w_handover', 'BS 전환 횟수 패널티'],
                ['차폐',     'w_blockage', '건물 차폐 손실 가중치'],
                ['미래',     'w_future',   '미래 연결성 위험 가중치'],
              ].map(([name, key, desc]) => (
                <tr key={key}>
                  <td><b style={{ fontWeight: 600 }}>{name}</b></td>
                  <td><span className="chip" style={{ fontFamily: 'var(--mono)' }}>{key}</span></td>
                  <td>
                    <input className="input" style={{ height: 32, width: 110 }}
                      type="number" min="0" max="20" step="0.1"
                      value={cfgDraft.cost_weights[key] ?? 0}
                      onChange={e => setWeight(key, e.target.value)} />
                  </td>
                  <td><span className="muted" style={{ fontSize: 11 }}>{desc}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Algorithm Selection */}
        <div className="muted" style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', marginBottom: 8 }}>ALGORITHM SELECTION</div>
        <div className="tbl-wrap" style={{ marginBottom: 16 }}>
          <table className="tbl">
            <thead><tr><th>범주</th><th>선택</th></tr></thead>
            <tbody>
              {[
                ['경로 알고리즘',    'route_algorithm',                  VALID_ROUTE_ALGORITHMS],
                ['지연시간 알고리즘', 'latency_algorithm',               VALID_LATENCY_ALGORITHMS],
                ['기지국 선택',      'base_station_selection_algorithm', VALID_BS_ALGORITHMS],
                ['자원할당',         'resource_allocation_algorithm',    VALID_ALLOC_ALGORITHMS],
              ].map(([label, key, options]) => (
                <tr key={key}>
                  <td><b style={{ fontWeight: 600 }}>{label}</b></td>
                  <td>
                    <select className="input" style={{ height: 32, minWidth: 220 }}
                      value={cfgDraft.algorithm_selection[key] || ''}
                      onChange={e => setAlgo(key, e.target.value)}>
                      {options.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Policy Options */}
        <div className="muted" style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', marginBottom: 8 }}>POLICY OPTIONS</div>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>정책</th><th style={{ width: 160 }}>값</th><th>설명</th></tr></thead>
            <tbody>
              <tr>
                <td><b style={{ fontWeight: 600 }}>룩어헤드 홉 수</b></td>
                <td><input className="input" style={{ height: 32, width: 80 }} type="number" min="1" max="10" step="1"
                  value={cfgDraft.policy_options.lookahead_k}
                  onChange={e => setPolicy('lookahead_k', Math.max(1, Math.min(10, parseInt(e.target.value) || 1)))} /></td>
                <td><span className="muted" style={{ fontSize: 11 }}>미래 BS 탐색 홉 수 (1–10)</span></td>
              </tr>
              <tr>
                <td><b style={{ fontWeight: 600 }}>룩어헤드 시간</b></td>
                <td><div className="input-suffix" style={{ width: 110 }}>
                  <input className="input" style={{ height: 32 }} type="number" min="1" max="120" step="1"
                    value={cfgDraft.policy_options.lookahead_time}
                    onChange={e => setPolicy('lookahead_time', Math.max(1, Math.min(120, parseFloat(e.target.value) || 1)))} />
                  <span className="sfx">s</span>
                </div></td>
                <td><span className="muted" style={{ fontSize: 11 }}>미래 예측 시간 창 (1–120s)</span></td>
              </tr>
              <tr>
                <td><b style={{ fontWeight: 600 }}>최대 핸드오버 수</b></td>
                <td><input className="input" style={{ height: 32, width: 80 }} type="number" min="0" max="50" step="1"
                  value={cfgDraft.policy_options.max_handover_allowed}
                  onChange={e => setPolicy('max_handover_allowed', Math.max(0, Math.min(50, parseInt(e.target.value) || 0)))} /></td>
                <td><span className="muted" style={{ fontSize: 11 }}>허용 BS 전환 최대 횟수 (0–50)</span></td>
              </tr>
              {[
                ['저지연 우선',    'prefer_low_latency',  '지연시간 최소화 경로 선호'],
                ['부하 균형 우선', 'prefer_load_balance', '기지국 부하 균형 경로 선호'],
                ['단절 회피',      'avoid_disconnection', '커버리지 공백 구간 회피'],
              ].map(([label, key, desc]) => (
                <tr key={key}>
                  <td><b style={{ fontWeight: 600 }}>{label}</b></td>
                  <td><Toggle on={cfgDraft.policy_options[key]} onChange={v => setPolicy(key, v)} /></td>
                  <td><span className="muted" style={{ fontSize: 11 }}>{desc}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
window.SettingsTab = SettingsTab;
