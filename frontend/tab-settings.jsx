/* ============================================================ Settings tab */
const TECH_PRESETS = {
  '4G': { L_base: '10', P_tx: '43', beta: '3.5', alpha: '120', N_max: '6', T_retx: '8', C_tech: '100' },
  '5G': { L_base: '1.0', P_tx: '46', beta: '3.0', alpha: '110', N_max: '4', T_retx: '1.0', C_tech: '500' },
  '6G': { L_base: '0.1', P_tx: '48', beta: '2.5', alpha: '100', N_max: '3', T_retx: '0.1', C_tech: '2000' },
};

function SettingsTab({ sim, dispatch }) {
  const [tech, setTech] = useState(sim.mode === '6G' ? '6G' : '5G');
  const [vals, setVals] = useState(() => {
    const o = {}; DATA.params.forEach(p => { if (p.type !== 'tech') o[p.v] = p.def; }); return o;
  });
  const [saved, setSaved] = useState(false);

  function applyTech(t) {
    setTech(t);
    setVals(v => ({ ...v, ...TECH_PRESETS[t] }));
    if (t !== '4G') dispatch({ type: 'mode', v: t });
  }
  function save() { setSaved(true); setTimeout(() => setSaved(false), 2000); }

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
    </div>
  );
}
window.SettingsTab = SettingsTab;
