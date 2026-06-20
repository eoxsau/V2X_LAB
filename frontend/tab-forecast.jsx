/* ============================================================ Forecast/Risk tab */
const RISK_TONE = { low: 'good', medium: 'warn', high: 'bad' };
const RISK_KO = { low: '낮음', medium: '보통', high: '높음' };

function ForecastTab({ vehiclePos, networkTelemetry }) {
  const [hops, setHops] = useState(3);
  const [lookahead, setLookahead] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const currentEdgeId = vehiclePos?.current_edge_id ?? null;
  // edge_id 형식 "{nodeA}_{nodeB}" — 진행 방향 노드(B)를 기준으로 lookahead 스캔
  const aheadNodeId = currentEdgeId ? currentEdgeId.split('_')[1] : null;

  function fetchLookahead() {
    if (!aheadNodeId) return;
    setLoading(true); setError(null);
    fetch(`http://127.0.0.1:8001/api/route/lookahead?node_id=${encodeURIComponent(aheadNodeId)}&hops=${hops}`)
      .then(r => r.json())
      .then(data => {
        if (!data.available) { setError(data.detail || '데이터 없음'); setLookahead(null); }
        else setLookahead(data);
        setLoading(false);
      })
      .catch(e => { setError(e.message || '조회 실패'); setLoading(false); });
  }

  useEffect(() => {
    fetchLookahead();
    const t = setInterval(fetchLookahead, 4000);
    return () => clearInterval(t);
  }, [aheadNodeId, hops]);

  const buildings = networkTelemetry?.intersected_building_count ?? null;
  const maxHeight = networkTelemetry?.max_building_height_m ?? null;
  const lossDb = networkTelemetry?.estimated_penetration_loss_db ?? null;

  const score = lookahead?.future_connectivity_score ?? null;
  const risk = lookahead?.risk_level ?? null;
  const perHop = lookahead?.per_hop ?? [];

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Forecast & Risk</div>
          <h1>예측/위험 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Forecast</span></h1>
          <div className="sub">진행 방향 기준 Look-ahead 커버리지 위험도 · 건물 차폐 영향</div>
        </div>
        <div className="row gap8">
          <Seg value={hops} onChange={setHops} options={[1, 3, 5, 8].map(h => ({ v: h, label: `${h}홉` }))} />
        </div>
      </div>

      {!aheadNodeId && (
        <div style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--ink-4)' }}>
          <div style={{ fontSize: 32, opacity: 0.2, marginBottom: 14 }}>◎</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6, color: 'var(--ink-3)' }}>시뮬레이션이 실행 중이 아닙니다</div>
          <div style={{ fontSize: 12 }}>차량이 이동 중일 때 진행 방향의 위험도를 예측합니다</div>
        </div>
      )}

      {aheadNodeId && (
        <>
          <div className="grid" style={{ gridTemplateColumns: '1fr 2fr', marginBottom: 18, alignItems: 'stretch' }}>
            <Card title="미래 연결 신뢰도" en="Future connectivity score">
              {loading && !lookahead ? (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>스캔 중…</div>
              ) : error ? (
                <div style={{ padding: 16, fontSize: 12, color: 'var(--bad)' }}>{error}</div>
              ) : score !== null ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0' }}>
                  <Donut value={Math.round(score * 100)} max={100} size={120} color={`var(--${RISK_TONE[risk] || 'brand-2'})`} label="연결 신뢰도" />
                  <div style={{ marginTop: 14 }}>
                    <Chip tone={RISK_TONE[risk] || 'brand'} dot>위험도 {RISK_KO[risk] || risk}</Chip>
                  </div>
                </div>
              ) : (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>데이터 없음</div>
              )}
            </Card>
            <Card title="홉별 커버리지 비율" en="Per-hop coverage" right={lookahead ? <Chip>{lookahead.total_uncovered}개 미커버 / {lookahead.total_edges_scanned}개 스캔</Chip> : null}>
              {perHop.length > 0 ? (
                <BarChart
                  items={perHop.map(h => ({
                    label: `${h.hop}홉 앞`,
                    value: (h.coverage_fraction ?? 0) * 100,
                    display: `${((h.coverage_fraction ?? 0) * 100).toFixed(0)}% (${h.covered_count}/${h.edge_count})`,
                    color: (h.coverage_fraction ?? 0) < 0.5 ? 'var(--bad)' : (h.coverage_fraction ?? 0) < 0.8 ? 'var(--warn)' : 'var(--good)',
                  }))}
                  max={100}
                />
              ) : (
                <div className="muted" style={{ padding: 16, fontSize: 12 }}>데이터 없음</div>
              )}
            </Card>
          </div>

          <Card title="건물 차폐 영향" en="Building obstruction" style={{ marginBottom: 0 }}>
            {buildings !== null ? (
              <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', gap: 12 }}>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">교차 건물 수</div>
                  <div className="value" style={{ fontSize: 18 }}>{buildings}<span className="unit">개</span></div>
                </div>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">최대 건물 높이</div>
                  <div className="value" style={{ fontSize: 18 }}>{maxHeight !== null ? maxHeight : '—'}<span className="unit">{maxHeight !== null ? 'm' : ''}</span></div>
                </div>
                <div className="stat" style={{ padding: '12px 14px' }}>
                  <div className="label">투과 손실</div>
                  <div className="value" style={{ fontSize: 18 }}>{lossDb !== null ? lossDb.toFixed(1) : '—'}<span className="unit">{lossDb !== null ? 'dB' : ''}</span></div>
                </div>
              </div>
            ) : (
              <div className="muted" style={{ padding: 16, fontSize: 12 }}>현재 위치 주변 건물 데이터가 없습니다.</div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
window.ForecastTab = ForecastTab;
