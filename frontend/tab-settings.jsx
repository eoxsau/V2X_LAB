/* ============================================================ Settings tab */
const TECH_PRESETS = {
  '4G': { L_base: '10', P_tx: '43', beta: '3.5', alpha: '45', N_max: '6', T_retx: '8', C_tech: '100' },
  '5G': { L_base: '1.0', P_tx: '46', beta: '3.0', alpha: '55', N_max: '4', T_retx: '1.0', C_tech: '500' },
  '6G': { L_base: '0.1', P_tx: '48', beta: '2.5', alpha: '68', N_max: '3', T_retx: '0.1', C_tech: '2000' },
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
    traffic_lambda: 5.0, other_device_lambda: 300.0, network_mode: '5G',
    demand_scale_pct: 100, bg_reroute_prob: 0.02, bg_reroute_mode: 'random',
  },
};

const WEIGHT_BOUNDS = { min: 0.0, max: 20.0 };
const VALID_ROUTE_ALGORITHMS = ['dijkstra', 'astar', 'k_shortest_path', 'network_aware', 'lookahead', 'rl_routing'];
const VALID_LATENCY_ALGORITHMS = ['full_composite_latency', 'blockage_aware_latency', 'mec_aware_latency', 'distance_based_latency', 'load_aware_latency'];
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
  if (pol.traffic_lambda !== undefined && (typeof pol.traffic_lambda !== 'number' || pol.traffic_lambda < 0 || pol.traffic_lambda > 200))
    errors.push('policy_options.traffic_lambda: must be a number 0–200');
  if (pol.other_device_lambda !== undefined && (typeof pol.other_device_lambda !== 'number' || pol.other_device_lambda < 0 || pol.other_device_lambda > 2000))
    errors.push('policy_options.other_device_lambda: must be a number 0–2000');
  if (pol.network_mode !== undefined && !['4G', '5G', '6G'].includes(pol.network_mode))
    errors.push('policy_options.network_mode: must be "4G", "5G", or "6G"');
  if (pol.demand_scale_pct !== undefined &&
      (typeof pol.demand_scale_pct !== 'number' || pol.demand_scale_pct < 10 || pol.demand_scale_pct > 300))
    errors.push('policy_options.demand_scale_pct: must be a number between 10 and 300');
  if (pol.bg_reroute_prob !== undefined && (typeof pol.bg_reroute_prob !== 'number' || pol.bg_reroute_prob < 0 || pol.bg_reroute_prob > 1))
    errors.push('policy_options.bg_reroute_prob: must be a number 0–1');
  if (pol.bg_reroute_mode !== undefined && !['random', 'congestion'].includes(pol.bg_reroute_mode))
    errors.push('policy_options.bg_reroute_mode: must be "random" or "congestion"');

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
    if (typeof pol.traffic_lambda === 'number' && pol.traffic_lambda >= 0)
      merged.policy_options.traffic_lambda = Math.min(pol.traffic_lambda, 200);
    if (typeof pol.other_device_lambda === 'number' && pol.other_device_lambda >= 0)
      merged.policy_options.other_device_lambda = Math.min(pol.other_device_lambda, 2000);
    if (typeof pol.network_mode === 'string' && ['4G', '5G', '6G'].includes(pol.network_mode))
      merged.policy_options.network_mode = pol.network_mode;
    if (typeof pol.demand_scale_pct === 'number')
      merged.policy_options.demand_scale_pct = Math.max(10, Math.min(300, pol.demand_scale_pct));
    if (typeof pol.bg_reroute_prob === 'number' && pol.bg_reroute_prob >= 0)
      merged.policy_options.bg_reroute_prob = Math.min(pol.bg_reroute_prob, 1);
    if (typeof pol.bg_reroute_mode === 'string' && ['random', 'congestion'].includes(pol.bg_reroute_mode))
      merged.policy_options.bg_reroute_mode = pol.bg_reroute_mode;
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

// ── Stage-2 custom policy helpers ────────────────────────────────────────────

const CUSTOM_POLICY_FEATURES = {
  custom_cost_policy:         ['distance', 'time', 'latency', 'load', 'handover', 'blockage', 'future_risk'],
  custom_bs_selection_policy: ['distance', 'latency', 'load', 'resource_deficit', 'future_risk'],
  custom_resource_policy:     ['demand', 'load', 'priority', 'distance'],
};

const CUSTOM_POLICY_SAMPLES = {
  custom_cost_policy: JSON.stringify({
    type: 'weighted_sum',
    weights: { distance: 0.15, time: 0.20, latency: 0.30, load: 0.15, handover: 0.10, blockage: 0.05, future_risk: 0.05 },
    constraints: { max_handover: 5, max_disconnection_ratio: 0.05 },
  }, null, 2),
  custom_bs_selection_policy: JSON.stringify({
    type: 'weighted_sum',
    weights: { distance: 0.30, latency: 0.30, load: 0.20, resource_deficit: 0.15, future_risk: 0.05 },
    constraints: { max_disconnection_ratio: 0.05 },
  }, null, 2),
  custom_resource_policy: JSON.stringify({
    type: 'weighted_sum',
    weights: { demand: 0.40, load: 0.30, priority: 0.20, distance: 0.10 },
  }, null, 2),
};

function parseCustomPolicy(text) {
  return JSON.parse(text);
}

function validateCustomPolicy(policyKey, policy) {
  const errors = [];
  if (!policy || typeof policy !== 'object') return { valid: false, errors: ['JSON 오브젝트이어야 합니다'] };
  if (policy.type !== 'weighted_sum') errors.push('type은 "weighted_sum"이어야 합니다');
  const allowed = CUSTOM_POLICY_FEATURES[policyKey] || [];
  const weights = policy.weights;
  if (!weights || typeof weights !== 'object') {
    errors.push('weights 필드가 필요합니다');
  } else {
    const entries = Object.entries(weights);
    if (entries.length === 0) errors.push('weights에 최소 하나 이상의 항목이 필요합니다');
    let total = 0;
    for (const [k, v] of entries) {
      if (!allowed.includes(k)) errors.push(`weights.${k}: 허용되지 않는 feature (허용: ${allowed.join(', ')})`);
      else if (typeof v !== 'number' || v < 0 || !isFinite(v)) errors.push(`weights.${k}: 0 이상의 유한한 숫자이어야 합니다`);
      else total += v;
    }
    if (!errors.length && total === 0) errors.push('weights 합이 0입니다');
  }
  const constraints = policy.constraints || {};
  for (const [k, v] of Object.entries(constraints)) {
    if (k === 'max_handover') { if (!Number.isInteger(v) || v < 0) errors.push('constraints.max_handover: 0 이상의 정수이어야 합니다'); }
    else if (k === 'max_disconnection_ratio') { if (typeof v !== 'number' || v < 0 || v > 1) errors.push('constraints.max_disconnection_ratio: 0.0–1.0 범위이어야 합니다'); }
    else errors.push(`constraints.${k}: 알 수 없는 제약 조건 키`);
  }
  return { valid: errors.length === 0, errors };
}

function runCustomWeightedPolicy(policy, features) {
  if (!policy || policy.type !== 'weighted_sum') return 0;
  const weights = policy.weights || {};
  let score = 0;
  for (const [feat, w] of Object.entries(weights)) {
    score += w * (features[feat] ?? 0);
  }
  return Math.max(0, score);
}

function SettingsTab({ sim, dispatch, api, simConfig, setSimConfig, mode, setAppMode }) {
  // 실행 설정 잠금 — 시뮬레이션이 시작된 뒤에는 초기화 전까지 변경 불가.
  // 이 탭의 저장(PUT /api/simulation/config)은 백엔드 전역에 "즉시" 반영되므로,
  // 실행 중 변경하면 주행 경로와 지연·자원할당 계산이 서로 다른 설정을 쓰게 된다.
  const isConfigLocked = configLocked(sim);
  const [tech, setTech] = useState(sim.mode === '6G' ? '6G' : '5G');
  const [buildingStatus, setBuildingStatus] = useState(null);
  // Stage-1: local draft of simulation config (applied on Save)
  const [cfgDraft, setCfgDraft] = useState(() => mergeWithDefaultConfig(simConfig));
  const [cfgErrors, setCfgErrors] = useState([]);
  const [cfgSaved, setCfgSaved] = useState(false);
  // Stage-2: custom policy state
  const [customPolicyText, setCustomPolicyText] = useState(CUSTOM_POLICY_SAMPLES);
  const [customPolicyErrors, setCustomPolicyErrors] = useState({});
  const [customPolicyParsed, setCustomPolicyParsed] = useState({});
  const [customPolicySaved, setCustomPolicySaved] = useState(false);

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
    if (t !== '4G') dispatch({ type: 'mode', v: t });
    setCfgDraft(d => ({ ...d, policy_options: { ...d.policy_options, network_mode: t } }));
  }
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

  function handlePolicyText(key, text) {
    setCustomPolicyText(t => ({ ...t, [key]: text }));
    setCustomPolicyErrors(e => { const n = { ...e }; delete n[key]; return n; });
    setCustomPolicyParsed(p => { const n = { ...p }; delete n[key]; return n; });
  }

  function validatePolicyLocal(key) {
    const text = customPolicyText[key] || '';
    try {
      const parsed = parseCustomPolicy(text);
      const { valid, errors } = validateCustomPolicy(key, parsed);
      setCustomPolicyErrors(e => ({ ...e, [key]: valid ? [] : errors }));
      setCustomPolicyParsed(p => ({ ...p, [key]: valid ? parsed : null }));
      return valid ? parsed : null;
    } catch (err) {
      setCustomPolicyErrors(e => ({ ...e, [key]: [err.message] }));
      setCustomPolicyParsed(p => ({ ...p, [key]: null }));
      return null;
    }
  }

  async function applyCustomPolicies() {
    const policyKeys = Object.keys(CUSTOM_POLICY_FEATURES);
    const allErrors = {};
    const validPolicies = {};
    for (const key of policyKeys) {
      const parsed = validatePolicyLocal(key);
      if (parsed) validPolicies[key] = parsed;
    }
    if (Object.keys(validPolicies).length === 0) return;
    try {
      const res = await fetch(`${api}/api/simulation/custom-policy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ policies: validPolicies }),
      });
      const data = await res.json();
      if (data.errors && Object.keys(data.errors).length > 0) {
        setCustomPolicyErrors(e => ({ ...e, ...data.errors }));
      }
      setCustomPolicySaved(true);
      setTimeout(() => setCustomPolicySaved(false), 2200);
    } catch (err) {
      setCustomPolicyErrors(e => ({ ...e, _network: [err.message] }));
    }
  }

  async function removeCustomPolicy(key) {
    try {
      await fetch(`${api}/api/simulation/custom-policy/${key}`, { method: 'DELETE' });
    } catch (_) {}
    setCustomPolicyText(t => ({ ...t, [key]: CUSTOM_POLICY_SAMPLES[key] }));
    setCustomPolicyErrors(e => { const n = { ...e }; delete n[key]; return n; });
    setCustomPolicyParsed(p => { const n = { ...p }; delete n[key]; return n; });
  }

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Configuration</div>
          <h1>설정 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Settings</span></h1>
          <div className="sub">네트워크 모드 및 시뮬레이션 알고리즘 설정</div>
        </div>
      </div>

      {/* 모드 전환 — Lite/Professional, 항상 표시(처음 선택을 나중에 바꿀 수 있는 유일한 통로) */}
      <Card title="모드" en="Lite / Professional" style={{ marginBottom: 18 }}>
        <div className="row between" style={{ alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <div className="muted" style={{ fontSize: 12, maxWidth: 460, lineHeight: 1.5 }}>
            Lite는 핵심 기능만 간단하게, Professional은 전체 알고리즘·실험 기능을 보여줍니다.
            언제든 바꿀 수 있고, 시뮬레이션 결과는 그대로 유지됩니다.
          </div>
          <Seg value={mode} onChange={setAppMode} options={[{ v: 'lite', label: 'Lite' }, { v: 'pro', label: 'Professional' }]} />
        </div>
      </Card>

      {/* tech selector */}
      <Card title="네트워크 모드" en="Network technology" style={{ marginBottom: 18 }}
        right={isConfigLocked ? <span className="cfg-lock-note">실행 중 잠김 — 초기화 후 변경</span> : null}>
        <fieldset className={'cfg-lock' + (isConfigLocked ? ' locked' : '')} disabled={isConfigLocked}>
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
        </fieldset>
        <div className="row gap8" style={{ marginTop: 14, padding: '10px 13px', background: 'var(--warn-tint)', borderRadius: 9, fontSize: 11, color: 'var(--warn-ink, var(--warn))' }}>
          <Icon.warn size={14} style={{ flex: '0 0 auto', marginTop: 1 }} />
          <span style={{ lineHeight: 1.45 }}>6G 수치는 확정 표준이 아닌 연구 목표값입니다 (표준 확정 예정: Release 21, ~2029년). C_tech는 설계 파라미터로 3GPP/ITU 규정값이 아닙니다.</span>
        </div>
      </Card>

      {mode === 'pro' && buildingStatus && (
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

      {/* ── Stage-1: Simulation Algorithm Config — Pro 전용 ──────────────── */}
      {mode === 'pro' && (
      <Card title="시뮬레이션 알고리즘 설정" en="Simulation config (Stage 1)"
        right={
          <div className="row gap8">
            {isConfigLocked
              ? <span className="cfg-lock-note">실행 중 잠김 — 초기화 후 변경</span>
              : (<>
                  <button className="btn" onClick={resetConfig}>초기화</button>
                  <button className={'btn ' + (cfgSaved ? 'good' : 'primary')} onClick={saveConfig}>
                    {cfgSaved ? <><Icon.check size={13} /> 저장됨</> : <><Icon.check size={13} /> 저장</>}
                  </button>
                </>)}
          </div>
        }
        style={{ marginTop: 18 }}
      >
        {isConfigLocked && (
          <div className="row gap8" style={{ marginBottom: 14, padding: '9px 12px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 11, color: 'var(--warn-ink, var(--warn))', flexWrap: 'wrap' }}>
            <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
            <span style={{ lineHeight: 1.5 }}>
              시뮬레이션이 시작된 상태라 설정을 변경할 수 없습니다. 실행 중 변경하면 주행 경로(시작 시점 설정)와
              지연·자원할당(변경된 설정)이 뒤섞여 결과를 신뢰할 수 없게 됩니다.
              시뮬레이션 탭에서 <b>전체 초기화</b> 후 다시 설정하세요.
            </span>
          </div>
        )}
        {cfgErrors.length > 0 && (
          <div className="row gap8" style={{ marginBottom: 14, padding: '9px 12px', background: 'var(--err-tint,#fff0f0)', borderRadius: 8, fontSize: 11, color: 'var(--err,#c00)', flexWrap: 'wrap' }}>
            <Icon.warn size={13} style={{ flex: '0 0 auto' }} />
            <span>{cfgErrors.join(' · ')}</span>
          </div>
        )}
        <fieldset className={'cfg-lock' + (isConfigLocked ? ' locked' : '')} disabled={isConfigLocked}>

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
              <tr>
                <td><b style={{ fontWeight: 600 }}>트래픽 밀도 (λ)</b></td>
                <td><div className="input-suffix" style={{ width: 130 }}>
                  <input className="input" style={{ height: 32 }} type="number" min="0.1" max="200" step="0.5"
                    value={cfgDraft.policy_options.traffic_lambda ?? 5.0}
                    onChange={e => setPolicy('traffic_lambda', Math.max(0.1, Math.min(200, parseFloat(e.target.value) || 5.0)))} />
                  <span className="sfx">v/km²</span>
                </div></td>
                <td><span className="muted" style={{ fontSize: 11 }}>배경 차량 밀도 — Poisson 배경 부하 기대치 (0.1–200)</span></td>
              </tr>
              <tr>
                <td><b style={{ fontWeight: 600 }}>기타 기기 밀도 (λ)</b></td>
                <td><div className="input-suffix" style={{ width: 130 }}>
                  <input className="input" style={{ height: 32 }} type="number" min="0" max="2000" step="10"
                    value={cfgDraft.policy_options.other_device_lambda ?? 300.0}
                    onChange={e => setPolicy('other_device_lambda', Math.max(0, Math.min(2000, parseFloat(e.target.value) || 0)))} />
                  <span className="sfx">대/km²</span>
                </div></td>
                <td><span className="muted" style={{ fontSize: 11 }}>차량 외 기기(폰·IoT) 밀도 — 같은 기지국 capacity를 나눠 쓰는 비차량 부하 (0–2000)</span></td>
              </tr>
              <tr>
                <td><b style={{ fontWeight: 600 }}>배경 차량 실시간 재경로</b></td>
                <td><div className="input-suffix" style={{ width: 130 }}>
                  <input className="input" style={{ height: 32 }} type="number" min="0" max="100" step="1"
                    value={Math.round((cfgDraft.policy_options.bg_reroute_prob ?? 0.02) * 100)}
                    onChange={e => setPolicy('bg_reroute_prob', Math.max(0, Math.min(100, parseInt(e.target.value, 10) || 0)) / 100)} />
                  <span className="sfx">%/초</span>
                </div></td>
                <td><span className="muted" style={{ fontSize: 11 }}>SUMO 모드 한정 — 배경 차량이 초당 이 확률로 주행 도중 무작위 목적지로 재경로(고정 경로 대신 동적 행태). 0이면 도착 시에만 새 목적지로 교체</span></td>
              </tr>
              <tr>
                <td><b style={{ fontWeight: 600 }}>재경로 트리거 방식</b></td>
                <td>
                  <Seg value={cfgDraft.policy_options.bg_reroute_mode ?? 'random'} onChange={v => setPolicy('bg_reroute_mode', v)}
                    options={[{ v: 'random', label: '무작위' }, { v: 'congestion', label: '혼잡 기반' }]} />
                </td>
                <td><span className="muted" style={{ fontSize: 11 }}>
                  무작위: 모든 배경 차량에 균일 확률 적용. 혼잡 기반: 차량 위치의 기지국 혼잡도(load/capacity)가
                  높을수록 위 확률이 최대 4배까지 증폭 — "혼잡한 곳에서 우회가 늘어난다"는 검증 가능한 가설로,
                  자원할당 알고리즘의 혼잡 대응을 스트레스 테스트하는 데 적합
                </span></td>
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
        </fieldset>
      </Card>
      )}

      {/* ── Stage-2: Custom Policy Editor — Pro 전용 ─────────────────────── */}
      {mode === 'pro' && (
      <Card title="커스텀 정책" en="Custom Policy (Stage 2)"
        right={
          <div className="row gap8">
            <button className={'btn ' + (customPolicySaved ? 'good' : 'primary')} onClick={applyCustomPolicies}>
              {customPolicySaved ? <><Icon.check size={13} /> 적용됨</> : <><Icon.check size={13} /> 정책 적용</>}
            </button>
          </div>
        }
        style={{ marginTop: 18 }}
      >
        <div className="row gap8" style={{ marginBottom: 16, padding: '9px 12px', background: 'var(--warn-tint)', borderRadius: 8, fontSize: 11, color: 'var(--warn-ink, var(--warn))' }}>
          <Icon.warn size={13} style={{ flex: '0 0 auto', marginTop: 1 }} />
          <span style={{ lineHeight: 1.45 }}>JSON 형식으로 가중치를 정의합니다. <span className="mono">type: "weighted_sum"</span>만 지원. 가중치 값은 0 이상의 실수이며 feature 키는 정책별로 고정됩니다.</span>
        </div>
        {customPolicyErrors._network && (
          <div style={{ fontSize: 11, color: 'var(--err,#c00)', marginBottom: 10 }}>{customPolicyErrors._network.join(' ')}</div>
        )}
        {[
          ['custom_cost_policy',         '경로 비용 정책',    'distance · time · latency · load · handover · blockage · future_risk'],
          ['custom_bs_selection_policy', '기지국 선택 정책',  'distance · latency · load · resource_deficit · future_risk'],
          ['custom_resource_policy',     '자원 할당 정책',    'demand · load · priority · distance'],
        ].map(([key, label, features]) => {
          const errs = customPolicyErrors[key] || [];
          const parsed = customPolicyParsed[key];
          return (
            <div key={key} style={{ marginBottom: 20 }}>
              <div className="row between" style={{ marginBottom: 6 }}>
                <div>
                  <b style={{ fontWeight: 600, fontSize: 12.5 }}>{label}</b>
                  <span className="muted mono" style={{ fontSize: 10.5, marginLeft: 8 }}>{features}</span>
                </div>
                <div className="row gap8">
                  <button className="btn sm" onClick={() => validatePolicyLocal(key)}>검증</button>
                  <button className="btn sm" onClick={() => removeCustomPolicy(key)}>초기화</button>
                </div>
              </div>
              <textarea
                style={{ width: '100%', height: 130, fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.55,
                  padding: '8px 10px', boxSizing: 'border-box', resize: 'vertical',
                  background: 'var(--surface-2)', border: '1px solid ' + (errs.length ? 'var(--err,#c00)' : parsed ? 'var(--good,#1a9)' : 'var(--border)'),
                  borderRadius: 8, color: 'var(--ink)', outline: 'none' }}
                value={customPolicyText[key] || ''}
                onChange={e => handlePolicyText(key, e.target.value)}
                spellCheck={false}
              />
              {errs.length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--err,#c00)', marginTop: 4, lineHeight: 1.5 }}>
                  {errs.map((e, i) => <div key={i}>• {e}</div>)}
                </div>
              )}
              {parsed && errs.length === 0 && (
                <div style={{ fontSize: 11, color: 'var(--good,#1a9)', marginTop: 4 }}>✓ 유효한 정책</div>
              )}
            </div>
          );
        })}
      </Card>
      )}
    </div>
  );
}
window.SettingsTab = SettingsTab;
