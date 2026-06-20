/* ============================================================ Routes tab */
function RoutesTab({ sim, vehiclePos, routeCoords, networkTelemetry, simHistory, routeEdges }) {
  const hasRoute = routeCoords && routeCoords.length >= 2;

  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms ?? null;
  const distM = networkTelemetry?.distance_m ?? null;
  const progress = vehiclePos?.progress ?? 0;
  const speed = vehiclePos?.speed ?? null;
  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name ?? null;
  const currentEdgeId = vehiclePos?.current_edge_id ?? null;
  const edgeNames = networkTelemetry?.route_edge_names ?? routeEdges?.edge_names ?? {};
  const perEdge = routeEdges?.per_edge ?? [];

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

          {/* 엣지별 비용 분해 */}
          {perEdge.length > 0 && (
            <Card title="구간별 비용 분해" en="Edge cost breakdown" right={<Chip>{perEdge.length}개 구간</Chip>} style={{ marginTop: 18 }}>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>구간</th>
                      <th className="r">거리</th>
                      <th className="r">Latency</th>
                      <th className="r">부하율</th>
                      <th className="r">총 비용</th>
                      <th>커버리지</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perEdge.map((e, i) => {
                      const isCurrent = e.edge_id === currentEdgeId;
                      const name = e.best_node_name || edgeNames[e.edge_id] || e.edge_id;
                      return (
                        <tr key={e.edge_id || i} style={isCurrent ? { background: 'var(--brand-tint)', fontWeight: 500 } : {}}>
                          <td>
                            <span className="mono" style={{ fontSize: 11.5 }}>{name}</span>
                            {isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}
                          </td>
                          <td className="r"><span className="num">{e.distance_m != null ? e.distance_m.toFixed(0) : '—'}</span><span className="muted" style={{ fontSize: 10 }}> m</span></td>
                          <td className="r"><span className="num" style={{ color: `var(--${latencyTone(e.latency_ms || 0)})`, fontWeight: 600 }}>{(e.latency_ms || 0).toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> ms</span></td>
                          <td className="r">
                            <div className="row gap8" style={{ justifyContent: 'flex-end' }}>
                              <div className="pbar" style={{ width: 44 }}><i style={{ width: `${Math.min((e.load_ratio || 0) * 100, 100)}%`, background: 'var(--brand-2)' }} /></div>
                              <span className="num" style={{ fontSize: 11 }}>{((e.load_ratio || 0) * 100).toFixed(0)}%</span>
                            </div>
                          </td>
                          <td className="r"><span className="num" style={{ fontWeight: 600 }}>{(e.total_cost || 0).toFixed(2)}</span></td>
                          <td>{e.within_coverage === false
                            ? <Chip tone="bad" dot>미커버</Chip>
                            : <Chip tone="good" dot>커버됨</Chip>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--ink-4)' }}>
          <div style={{ fontSize: 32, opacity: 0.2, marginBottom: 14 }}>⇢</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6, color: 'var(--ink-3)' }}>경로가 설정되지 않았습니다</div>
          <div style={{ fontSize: 12 }}>시뮬레이션 탭에서 출발지와 도착지를 지정하고 시뮬레이션을 시작하세요</div>
        </div>
      )}
    </div>
  );
}
window.RoutesTab = RoutesTab;
