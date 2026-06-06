/* ============================================================ Routes tab */
function RoutesTab() {
  const rc = DATA.routeCompare;
  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Path Comparison</div>
          <h1>경로 비교 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Routes</span></h1>
          <div className="sub">최단경로(Dijkstra) vs 통신품질 반영 강화학습(RL) 추천 경로</div>
        </div>
        <div className="row gap8">
          <Chip>Dijkstra · 현재</Chip>
          <Chip tone="brand" dot>RL 추천</Chip>
        </div>
      </div>

      {/* headline winner */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(3,1fr)', marginBottom: 18 }}>
        <Stat label="평균 Latency 개선" icon="latency" value="−49.7" unit="%" sub="14.3ms → 7.2ms" accent />
        <Stat label="통신 위험 구간" icon="warn" value="2 → 0" sub="RL 경로 위험 0건" />
        <Stat label="예상 소요 시간" icon="clock" value="−24" unit="초" sub="6:12 → 5:48" />
      </div>

      <Card title="경로 비교" en="Dijkstra vs RL" right={<span className="mono muted" style={{ fontSize: 10 }}>* RL 열은 Mock</span>} style={{ padding: 0, marginBottom: 18 }}>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>비교 항목<span className="en">Metric</span></th>
                <th>Dijkstra (현재)<span className="en">Shortest path</span></th>
                <th>RL 추천 경로<span className="en">RL recommended</span></th>
                <th>우위<span className="en">Winner</span></th>
              </tr>
            </thead>
            <tbody>
              {rc.rows.map((r, i) => (
                <tr key={i}>
                  <td><b style={{ fontWeight: 600 }}>{r.metric}</b><div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-4)' }}>{r.en}</div></td>
                  <td><span className={'num' + (r.better === 'dij' ? '' : '')} style={{ fontWeight: r.better === 'dij' ? 700 : 500, color: r.better === 'dij' ? 'var(--good)' : 'var(--ink)' }}>{r.dij}</span></td>
                  <td><span className="num" style={{ fontWeight: r.better === 'rl' ? 700 : 500, color: r.better === 'rl' ? 'var(--good)' : 'var(--ink)' }}>{r.rl}</span></td>
                  <td>{r.better === 'rl' ? <Chip tone="brand" dot>RL</Chip> : r.better === 'dij' ? <Chip dot>Dijkstra</Chip> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <Card title="Dijkstra 경로" en="Shortest path" right={<Chip tone="bad" dot>위험 2구간</Chip>}>
          <MiniMap path={rc.dijPath} risk={rc.dijRisk} color="#7A8AA0" bs={DATA.baseStations} label="dij" height={210} />
          <div className="row gap16" style={{ marginTop: 12, fontSize: 11 }}>
            <span className="row gap8"><span style={{ width: 16, height: 3, background: '#7A8AA0', borderRadius: 2 }} /> 경로</span>
            <span className="row gap8"><span style={{ width: 16, height: 4, background: 'var(--bad)', borderRadius: 2 }} /> 통신 위험</span>
            <span className="row gap8" style={{ marginLeft: 'auto' }}><span className="num" style={{ fontWeight: 600 }}>3.42</span> km · <span className="num">14.3</span> ms</span>
          </div>
        </Card>
        <Card title="RL 추천 경로" en="RL recommended" right={<Chip tone="good" dot>위험 0구간</Chip>}>
          <MiniMap path={rc.rlPath} color="#2E75B6" bs={DATA.baseStations} label="rl" height={210} />
          <div className="row gap16" style={{ marginTop: 12, fontSize: 11 }}>
            <span className="row gap8"><span style={{ width: 16, height: 3, background: '#2E75B6', borderRadius: 2 }} /> 경로</span>
            <span className="row gap8"><span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--brand-2)' }} /> 기지국 경유</span>
            <span className="row gap8" style={{ marginLeft: 'auto' }}><span className="num" style={{ fontWeight: 600 }}>3.61</span> km · <span className="num" style={{ color: 'var(--good)' }}>7.2</span> ms</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
window.RoutesTab = RoutesTab;
