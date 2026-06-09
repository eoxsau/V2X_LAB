/* ============================================================ Routes tab */
function RoutesTab({ sim, vehiclePos, routeCoords, networkTelemetry, simHistory }) {
  const hasRoute = routeCoords && routeCoords.length >= 2;
  const rc = DATA.routeCompare;

  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms ?? null;
  const distM = networkTelemetry?.distance_m ?? null;
  const progress = vehiclePos?.progress ?? 0;
  const speed = vehiclePos?.speed ?? null;
  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name ?? null;

  const avgLatency = simHistory.length >= 2
    ? (simHistory.reduce((s, h) => s + (h.latency ?? 0), 0) / simHistory.length).toFixed(1)
    : null;
  const maxSpeed = simHistory.length >= 2
    ? Math.max(...simHistory.map(h => h.speed ?? 0)).toFixed(1)
    : null;

  // Convert routeCoords [[lat,lng],...] for MiniMap (already correct format)
  const livePath = hasRoute ? routeCoords : null;

  // Base stations for map overlay from candidates
  const bsPoints = (networkTelemetry?.candidate_nodes ?? [])
    .filter(c => c.lat != null && c.lng != null)
    .map(c => ({ lat: c.lat, lng: c.lng }));

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Path Comparison</div>
          <h1>경로 비교 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Routes</span></h1>
          <div className="sub">
            {hasRoute ? '실시간 SUMO 경로 vs Dijkstra · RL 추천 경로' : '최단경로(Dijkstra) vs 통신품질 반영 강화학습(RL) 추천 경로'}
          </div>
        </div>
        <div className="row gap8">
          {hasRoute && <Chip tone="good" dot>LIVE 경로</Chip>}
          <Chip>Dijkstra · 현재</Chip>
          <Chip tone="brand" dot>RL 추천</Chip>
        </div>
      </div>

      {hasRoute ? (
        <>
          {/* Live stats */}
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 18 }}>
            <Stat label="진행률" icon="route" value={(progress * 100).toFixed(0)} unit="%" sub={vehiclePos?.arrived ? '도착 완료' : '이동 중'} accent />
            <Stat label="현재 속도" icon="speed" value={speed !== null ? speed.toFixed(1) : '—'} unit="km/h" sub="SUMO 실시간" />
            <Stat label="현재 Latency" icon="latency" value={latency !== null ? latency.toFixed(1) : '—'} unit={latency !== null ? 'ms' : ''} sub={latency !== null && latency > 20 ? '위험 수준' : '정상 범위'} />
            <Stat label="평균 Latency" icon="net" value={avgLatency ?? '—'} unit={avgLatency ? 'ms' : ''} sub={`기록 ${simHistory.length}개`} />
          </div>

          {/* Live route map */}
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 18 }}>
            <Card title="실시간 경로" en="Live SUMO route" right={<Chip tone="good" dot>실시간</Chip>}>
              <MiniMap path={livePath} color="var(--brand-2)" bs={bsPoints} label="live" height={210} />
              <div className="row gap16" style={{ marginTop: 12, fontSize: 11 }}>
                <span className="row gap8"><span style={{ width: 16, height: 3, background: 'var(--brand-2)', borderRadius: 2 }} /> 실제 경로</span>
                {connNode && <span className="row gap8"><span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--brand-2)' }} /> {connNode}</span>}
                {speed !== null && <span className="row gap8" style={{ marginLeft: 'auto' }}><span className="num" style={{ fontWeight: 600 }}>{speed.toFixed(1)}</span> km/h</span>}
              </div>
            </Card>
            <Card title="Latency 추이" en="Latency history" right={latency !== null ? <Chip tone={latencyTone(latency)} dot>{latency.toFixed(1)}ms</Chip> : null}>
              {simHistory.length >= 2 ? (
                <>
                  <LineChart series={[simHistory.map(h => h.latency ?? 0)]} height={210} yUnit="ms" yMax={32} threshold={20} />
                  <div className="row gap16" style={{ marginTop: 8, fontSize: 11 }}>
                    <span className="row gap8"><span style={{ width: 18, height: 3, background: 'var(--brand-2)', borderRadius: 2 }} /> Latency</span>
                    <span className="row gap8"><span style={{ width: 18, height: 0, borderTop: '2px dashed var(--bad)' }} /> 임계 20ms</span>
                    {avgLatency && <span className="row gap8" style={{ marginLeft: 'auto' }}>평균 <span className="num" style={{ fontWeight: 600 }}>{avgLatency}</span>ms</span>}
                  </div>
                </>
              ) : (
                <div style={{ display: 'grid', placeItems: 'center', height: 210, color: 'var(--ink-4)', fontSize: 12 }}>
                  데이터 수집 중…
                </div>
              )}
            </Card>
          </div>

          {/* speed history */}
          {simHistory.length >= 2 && (
            <Card title="속도 추이" en="Speed history" right={maxSpeed ? <span className="num muted" style={{ fontSize: 11 }}>최대 {maxSpeed}km/h</span> : null}>
              <LineChart series={[simHistory.map(h => h.speed ?? 0)]} height={130} yMax={60} colors={['var(--good)']} />
              <div className="row gap16" style={{ marginTop: 8, fontSize: 11 }}>
                <span className="row gap8"><span style={{ width: 18, height: 3, background: 'var(--good)', borderRadius: 2 }} /> 속도 km/h</span>
              </div>
            </Card>
          )}
        </>
      ) : (
        <>
          {/* Mock comparison (no live data) */}
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
                      <td><span className="num" style={{ fontWeight: r.better === 'dij' ? 700 : 500, color: r.better === 'dij' ? 'var(--good)' : 'var(--ink)' }}>{r.dij}</span></td>
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
        </>
      )}
    </div>
  );
}
window.RoutesTab = RoutesTab;
