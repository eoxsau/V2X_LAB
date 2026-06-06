/* ============================================================ Analysis tab */
const LOG_STYLE = {
  info:     { tone: 'brand', ko: '정보',   ic: 'spark' },
  handover: { tone: 'brand', ko: '핸드오버', ic: 'antenna' },
  warn:     { tone: 'warn',  ko: '경고',   ic: 'warn' },
  risk:     { tone: 'bad',   ko: '위험',   ic: 'warn' },
  done:     { tone: 'good',  ko: '완료',   ic: 'check' },
  reroute:  { tone: 'brand', ko: '재경로', ic: 'route' },
  sys:      { tone: '',      ko: '시스템', ic: 'sliders' },
};

function AnalysisTab() {
  const [analyzing, setAnalyzing] = useState(false);
  const [revealed, setRevealed] = useState(0);
  const [exported, setExported] = useState(false);

  function runAI() {
    setAnalyzing(true); setRevealed(0);
    setTimeout(() => {
      setAnalyzing(false);
      DATA.llmSummary.forEach((_, i) => setTimeout(() => setRevealed(i + 1), i * 450));
    }, 1300);
  }

  function exportCSV() {
    const header = ['시각', '차량ID', '위치', '속도', '현재엣지', '연결기지국', 'Latency', '이벤트'];
    const rows = DATA.vehicles.map(v => [`00:03:42`, v.id, `"${v.lat},${v.lng}"`, v.speed, v.edge, v.bs, v.latency, v.state]);
    const csv = [header.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'v2x_simulation_log.csv'; a.click();
    setExported(true); setTimeout(() => setExported(false), 2200);
  }

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Analysis</div>
          <h1>분석 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Analysis</span></h1>
          <div className="sub">시뮬레이션 로그 · LLM 자연어 요약 · CSV 내보내기</div>
        </div>
        <button className={'btn ' + (exported ? 'good' : 'accent')} onClick={exportCSV}>
          {exported ? <><Icon.check size={15} /> 저장됨</> : <><Icon.download size={15} /> CSV로 내보내기</>}
        </button>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1.35fr 1fr', alignItems: 'start' }}>
        {/* log table */}
        <Card title="시뮬레이션 로그" en="Event log" right={<Chip>{DATA.logs.length}건</Chip>} style={{ padding: 0 }}>
          <div className="tbl-wrap" style={{ maxHeight: 'calc(100vh - 250px)' }}>
            <table className="tbl">
              <thead><tr><th>시각<span className="en">Time</span></th><th>대상<span className="en">Target</span></th><th>유형<span className="en">Type</span></th><th>이벤트 내용<span className="en">Event</span></th></tr></thead>
              <tbody>
                {DATA.logs.map((l, i) => {
                  const st = LOG_STYLE[l.kind];
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
        <Card title="AI 자연어 분석" en="LLM summary" right={<Chip tone="brand"><Icon.spark size={11} /> GPT</Chip>}>
          {revealed === 0 && !analyzing && (
            <div style={{ textAlign: 'center', padding: '26px 16px' }}>
              <div style={{ width: 52, height: 52, borderRadius: 14, background: 'var(--brand-tint)', display: 'grid', placeItems: 'center', margin: '0 auto 14px', color: 'var(--brand-2)' }}><Icon.spark size={26} /></div>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 5 }}>AI 분석 준비 완료</div>
              <div className="muted" style={{ fontSize: 11.5, marginBottom: 18, lineHeight: 1.5 }}>로그 7건을 분석해<br />자연어 요약을 생성합니다</div>
              <button className="btn primary" onClick={runAI}><Icon.spark size={15} /> AI 분석 시작</button>
            </div>
          )}
          {analyzing && (
            <div style={{ textAlign: 'center', padding: '34px 16px' }}>
              <div className="spin" style={{ width: 30, height: 30, border: '3px solid var(--brand-tint2)', borderTopColor: 'var(--brand-2)', borderRadius: '50%', margin: '0 auto 14px' }} />
              <div className="muted" style={{ fontSize: 12 }}>로그 분석 중…</div>
            </div>
          )}
          {revealed > 0 && (
            <div className="col gap12">
              {DATA.llmSummary.slice(0, revealed).map((t, i) => (
                <div key={i} className="fade row gap12" style={{ padding: '12px 13px', background: i === 1 ? 'var(--bad-tint)' : i === 2 ? 'var(--good-tint)' : 'var(--surface-2)', borderRadius: 10, border: '1px solid var(--border)', alignItems: 'flex-start' }}>
                  <span className="num" style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-2)', flex: '0 0 auto', marginTop: 1 }}>{String(i + 1).padStart(2, '0')}</span>
                  <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>{t}</span>
                </div>
              ))}
              {revealed === DATA.llmSummary.length && (
                <button className="btn sm" onClick={runAI} style={{ alignSelf: 'flex-start' }}><Icon.reset size={13} /> 다시 분석</button>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
window.AnalysisTab = AnalysisTab;
