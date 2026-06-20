/* ============================================================ Comparison tab */
const CMP_METRIC_COLS = [
  { key: 'total_cost',              label: '총 비용',        fmt: v => v.toFixed(2) },
  { key: 'average_latency_ms',      label: '평균 Latency',   fmt: v => v.toFixed(1) + 'ms' },
  { key: 'handover_count',          label: '핸드오버',       fmt: v => v + '회' },
  { key: 'disconnection_ratio',     label: '단절율',         fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'average_bs_load',         label: '평균 BS 부하',   fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'future_connectivity_risk', label: '미래 위험도',   fmt: v => (v * 100).toFixed(0) + '%' },
  { key: 'edge_count',              label: '구간 수',        fmt: v => v + '개' },
];

const CMP_HISTORY_KEY = 'v2x_run_history';

function loadRunHistory() {
  try { return JSON.parse(localStorage.getItem(CMP_HISTORY_KEY) || '[]'); } catch { return []; }
}
function saveRunHistory(list) {
  try { localStorage.setItem(CMP_HISTORY_KEY, JSON.stringify(list.slice(-20))); } catch {}
}

function ComparisonTab({ vehiclePos, simConfig }) {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState(() => loadRunHistory());
  const [checkedRuns, setCheckedRuns] = useState([]);
  const [savedFlash, setSavedFlash] = useState(false);

  function fetchMetrics() {
    setLoading(true); setError(null);
    fetch('http://127.0.0.1:8001/api/route/metrics')
      .then(r => r.json())
      .then(data => { setMetrics(data); setLoading(false); })
      .catch(e => { setError(e.message || '불러오기 실패'); setLoading(false); });
  }
  useEffect(() => { fetchMetrics(); }, []);

  const algorithms = metrics?.available ? metrics.algorithms : {};
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

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Decision Support</div>
          <h1>비교 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Comparison</span></h1>
          <div className="sub">실행 전 알고리즘 비교 · 과거 실행 이력 비교</div>
        </div>
        <button className="btn" onClick={fetchMetrics}><Icon.reset size={14} /> 새로고침</button>
      </div>

      {/* ── 실행 전 비교 ─────────────────────────────── */}
      <Card title="알고리즘 비교" en="Candidate algorithms" right={algoEntries.length > 0 ? <Chip>{algoEntries.length}개 후보</Chip> : null} style={{ marginBottom: 18 }}>
        {loading && <div className="muted" style={{ padding: 16, fontSize: 12 }}>불러오는 중…</div>}
        {!loading && error && <div style={{ padding: 16, fontSize: 12, color: 'var(--bad)' }}>{error}</div>}
        {!loading && !error && algoEntries.length === 0 && (
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
                    {CMP_METRIC_COLS.map(c => <th key={c.key} className="r">{c.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {sortedByCost.map(([algo, m]) => (
                    <tr key={algo}>
                      <td><span className="mono" style={{ fontWeight: 600 }}>{algo}</span></td>
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
                  ))}
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

      {/* ── 실행 후 비교 (히스토리) ─────────────────────────────── */}
      <Card title="실행 이력 비교" en="Run history" right={<Chip>{history.length}개 저장됨</Chip>}>
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
      </Card>
    </div>
  );
}
window.ComparisonTab = ComparisonTab;
