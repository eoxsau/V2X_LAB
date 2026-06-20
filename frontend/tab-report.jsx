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

function ReportTab({ sim, simLogs, vehiclePos, networkTelemetry }) {
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
                <><span className="mono" style={{ fontWeight: 600 }}>{it.edge_id}</span><span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}%</span>{it.severity && <Chip tone="warn">{it.severity}</Chip>}</>
              )} />
              <ReportSectionList title="과부하 기지국" items={summary.overloaded_base_stations} render={it => (
                <><span className="mono" style={{ fontWeight: 600 }}>{it.bs_name}</span><span className="muted">부하 {((it.load_ratio ?? 0) * 100).toFixed(0)}%</span></>
              )} />
              <ReportSectionList title="빈번한 핸드오버 구간" items={summary.frequent_handover_sections} render={it => (
                <><span className="mono" style={{ fontWeight: 600 }}>{it.edge_id}</span><span className="muted">{it.from_bs_name} → {it.to_bs_name}</span></>
              )} />
              <ReportSectionList title="고지연 구간" items={summary.high_latency_sections} render={it => (
                <><span className="mono" style={{ fontWeight: 600 }}>{it.edge_id}</span><span className="muted">{(it.latency_ms ?? 0).toFixed(1)}ms (+{(it.excess_ms ?? 0).toFixed(1)}ms)</span></>
              )} />
            </div>
            <ReportSectionList title="미래 연결 위험 구간" items={summary.future_connectivity_risk_sections} render={it => (
              <><span className="mono" style={{ fontWeight: 600 }}>{it.edge_id}</span>{it.severity && <Chip tone="bad">{it.severity}</Chip>}</>
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
            {llmProviders.length > 1 && (
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
    </div>
  );
}
window.ReportTab = ReportTab;
