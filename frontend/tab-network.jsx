/* ============================================================ Network tab */
function edgeCongTone(loadRatio) {
  if (loadRatio >= 0.65) return 'bad';
  if (loadRatio >= 0.35) return 'warn';
  return 'good';
}
function edgeCongLabel(loadRatio) {
  if (loadRatio >= 0.65) return '혼잡';
  if (loadRatio >= 0.35) return '보통';
  return '원활';
}

function NetworkTab({ networkTelemetry, routeEdges, vehiclePos }) {
  const hasLive = !!networkTelemetry;
  const connNode = networkTelemetry?.connected_node ?? null;

  // 자원배분 — 재계산 없는 읽기 전용 엔드포인트를 3초 간격으로 폴링
  const [alloc, setAlloc] = useState(null);
  useEffect(() => {
    let dead = false;
    function poll() {
      fetch('http://127.0.0.1:8001/api/resources/allocation-result')
        .then(r => r.json())
        .then(data => { if (!dead) setAlloc(data); })
        .catch(() => {});
    }
    poll();
    const t = setInterval(poll, 3000);
    return () => { dead = true; clearInterval(t); };
  }, []);

  const edgeStats = networkTelemetry?.edge_stats || [];
  const perEdge   = routeEdges?.per_edge || [];
  const perEdgeMap = {};
  perEdge.forEach(e => { perEdgeMap[e.edge_id] = e; });
  const edgeNames = networkTelemetry?.route_edge_names || routeEdges?.edge_names || {};
  const hasLiveEdges = edgeStats.length > 0;

  // Live stats as primary; fall back to route_cost per_edge (static snapshot) if live not yet available
  const _allEdgesRaw = hasLiveEdges
    ? edgeStats.map(e => {
        const meta = perEdgeMap[e.edge_id] || {};
        return {
          edge_id:         e.edge_id,
          street_name:     edgeNames[e.edge_id] || '',
          speed_kmh:       e.speed_kmh,
          occupancy:       e.occupancy,
          vehicle_count:   e.vehicle_count,
          load_ratio:      e.occupancy ?? meta.load_ratio ?? 0,
          distance_m:      meta.distance_m  ?? null,
          latency_ms:      meta.latency_ms  ?? null,
          within_coverage: meta.within_coverage ?? true,
        };
      })
    : perEdge.map(e => ({
        edge_id:         e.edge_id,
        street_name:     edgeNames[e.edge_id] || '',
        speed_kmh:       null,
        occupancy:       e.load_ratio,
        vehicle_count:   null,
        load_ratio:      e.load_ratio,
        distance_m:      e.distance_m  ?? null,
        latency_ms:      e.latency_ms  ?? null,
        within_coverage: e.within_coverage ?? true,
      }));
  // 도로명이 없는 엣지에 인접 엣지 이름 전파 (앞→뒤, 뒤→앞)
  const allEdges = (() => {
    const a = _allEdgesRaw.map(e => ({ ...e }));
    for (let i = 1; i < a.length; i++)
      if (!a[i].street_name && a[i - 1].street_name) a[i].street_name = a[i - 1].street_name;
    for (let i = a.length - 2; i >= 0; i--)
      if (!a[i].street_name && a[i + 1].street_name) a[i].street_name = a[i + 1].street_name;
    return a;
  })();

  // Current edge + last 6 completed edges (from backend edge_history)
  const edgeHistory   = networkTelemetry?.edge_history   || [];
  const edgeAvgSpeeds = networkTelemetry?.edge_avg_speeds || {};
  const n = allEdges.length;
  const progress = vehiclePos?.progress ?? 0;
  const curIdx = n > 0 ? Math.min(Math.floor(progress * n), n - 1) : 0;

  const currentEdge = n > 0 ? { ...allEdges[curIdx], isCurrent: true } : null;

  const allEdgeMap = {};
  allEdges.forEach(e => { allEdgeMap[e.edge_id] = e; });
  const completedEdges = edgeHistory.slice(-6).reverse().map((eid, i, arr) => {
    const base = allEdgeMap[eid] || { edge_id: eid, street_name: edgeNames[eid] || '', load_ratio: 0, occupancy: 0, vehicle_count: null, distance_m: null, latency_ms: null, within_coverage: true };
    // 이름 없으면 history 인접 엣지(allEdgeMap에 있는 것) 이름으로 보완
    const name = base.street_name
      || (allEdgeMap[arr[i - 1]]?.street_name)
      || (allEdgeMap[arr[i + 1]]?.street_name)
      || '';
    return { ...base, street_name: name, isCurrent: false, speed_kmh: edgeAvgSpeeds[eid] ?? base.speed_kmh ?? null };
  });

  const mergedEdges = [
    ...(currentEdge ? [currentEdge] : []),
    ...completedEdges,
  ];
  const candidates = networkTelemetry?.candidate_nodes ?? [];
  const connName = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? connNode?.name ?? null;

  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms ?? null;
  const distanceM = networkTelemetry?.distance_m ?? null;
  const lossDb = networkTelemetry?.estimated_penetration_loss_db ?? null;
  const stability = networkTelemetry?.stability_score ?? null;
  const buildings = networkTelemetry?.intersected_building_count ?? null;
  const congestion = connNode?.congestion_score ?? null;

  const statCount = hasLive ? (candidates.length || '—') : '—';
  const statLatency = latency !== null ? latency.toFixed(1) : '—';
  const statCong = congestion !== null ? (congestion * 100).toFixed(1) : '—';

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Infrastructure</div>
          <h1>네트워크 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Network</span></h1>
          <div className="sub">기지국 상태 및 엣지(도로구간) 혼잡도</div>
        </div>
        {hasLive && <Chip tone="good" dot>LIVE</Chip>}
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 18 }}>
        <Stat label="활성 기지국" icon="antenna" value={statCount} unit="" sub={hasLive ? '후보 노드 수' : '—'} />
        <Stat label="연결 기지국" icon="antenna" value={hasLive ? (connNode?.name ?? connName ?? '없음') : '—'} unit="" sub={hasLive ? `거리 ${distanceM !== null ? distanceM.toFixed(0) + 'm' : '—'}` : '—'} />
        <Stat label="혼잡도" icon="net" value={statCong} unit={congestion !== null ? '%' : ''} sub={hasLive ? (connName ?? '연결 기지국') : '—'} accent />
        <Stat label="Latency" icon="latency" value={statLatency} unit={latency !== null ? 'ms' : ''} sub={hasLive ? (latency !== null && latency > 20 ? '위험 수준' : '정상 범위') : '—'} />
      </div>

      {hasLive && connName && (
        <Card title="현재 연결 기지국" en="Connected node" right={<Chip tone="good" dot>연결 중</Chip>} style={{ marginBottom: 18 }}>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">기지국 명</div>
              <div className="value" style={{ fontSize: 16, fontFamily: 'var(--mono)' }}>{connName}</div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">거리</div>
              <div className="value" style={{ fontSize: 16 }}>{distanceM !== null ? distanceM.toFixed(0) : '—'}<span className="unit">m</span></div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">신호 손실</div>
              <div className="value" style={{ fontSize: 16 }}>{lossDb !== null ? lossDb.toFixed(1) : '—'}<span className="unit">{lossDb !== null ? 'dB' : ''}</span></div>
            </div>
            <div className="stat" style={{ padding: '12px 14px' }}>
              <div className="label">안정성</div>
              <div className="value" style={{ fontSize: 16 }}>{stability !== null ? stability.toFixed(2) : '—'}</div>
            </div>
          </div>
          {buildings !== null && (
            <div className="row gap8" style={{ marginTop: 10 }}>
              <span className="muted" style={{ fontSize: 11.5 }}>교차 건물</span>
              <span className="num" style={{ fontWeight: 600 }}>{buildings}개</span>
              {networkTelemetry?.max_building_height_m !== null && (
                <>
                  <span className="muted" style={{ fontSize: 11.5 }}>최대 높이</span>
                  <span className="num" style={{ fontWeight: 600 }}>{networkTelemetry.max_building_height_m}m</span>
                </>
              )}
            </div>
          )}
        </Card>
      )}

      {hasLive && candidates.length > 0 ? (
        <Card title="기지국 후보 목록" en="Candidate nodes" right={<Chip>{candidates.length}개</Chip>} style={{ padding: 0, marginBottom: 18 }}>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>기지국 ID</th>
                  <th className="r">거리<span className="en">Distance</span></th>
                  <th className="r">예측 Latency<span className="en">ms</span></th>
                  <th>상태<span className="en">Status</span></th>
                </tr>
              </thead>
              <tbody>
                {candidates.slice(0, 8).map((c, i) => {
                  const name = c.name ?? c.id ?? '—';
                  const isConn = name === connName;
                  const ms = c.predicted_latency_ms ?? 0;
                  const tone = latencyTone(ms);
                  return (
                    <tr key={i} style={isConn ? { background: 'var(--brand-tint)' } : {}}>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>{name}</span>
                        {isConn && <Chip tone="good" dot style={{ marginLeft: 8 }}>연결 중</Chip>}
                      </td>
                      <td className="r"><span className="num">{(c.distance_m ?? 0).toFixed(0)}</span> <span className="muted" style={{ fontSize: 10 }}>m</span></td>
                      <td className="r"><span className="num" style={{ color: `var(--${tone})`, fontWeight: 600 }}>{ms.toFixed(1)}</span></td>
                      <td><Chip tone={tone} dot>{tone === 'good' ? '양호' : tone === 'warn' ? '보통' : '위험'}</Chip></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <Card title="기지국 상태" en="Base stations" style={{ padding: 0, marginBottom: 18 }}>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>ID</th><th>위치<span className="en">Lat, Lng</span></th><th className="r">높이<span className="en">Height</span></th>
                  <th className="r">연결 차량<span className="en">Vehicles</span></th><th className="r">최대 용량<span className="en">Capacity</span></th>
                  <th>점유율 ρ<span className="en">Load</span></th><th className="r">L_queue<span className="en">ms</span></th><th>상태<span className="en">Status</span></th>
                </tr>
              </thead>
              <tbody>
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {alloc?.available && (
        <Card title="자원배분 현황" en="Resource allocation" right={<Chip tone="brand">{alloc.algorithm_id}</Chip>} style={{ marginBottom: 18 }}>
          {Object.keys(alloc.bs_load_after_allocation || {}).length === 0 ? (
            <div className="muted" style={{ padding: 8, fontSize: 12 }}>할당 결과가 없습니다.</div>
          ) : (
            <BarChart
              items={Object.entries(alloc.bs_load_after_allocation).map(([bsId, load]) => {
                const deficit = alloc.resource_deficit_by_bs?.[bsId] ?? 0;
                const tone = load >= 0.8 ? 'bad' : load >= 0.5 ? 'warn' : 'good';
                return {
                  label: bsId,
                  value: load * 100,
                  display: `${(load * 100).toFixed(0)}%${deficit > 0 ? ` (-${deficit.toFixed(1)})` : ''}`,
                  color: `var(--${tone})`,
                };
              })}
              max={100}
            />
          )}
          <div className="muted" style={{ fontSize: 10.5, marginTop: 10, fontFamily: 'var(--mono)' }}>BS별 할당 후 부하율(%) · 괄호는 RB 부족분</div>
        </Card>
      )}

      {mergedEdges.length > 0 && (
        <div className="grid" style={{ gridTemplateColumns: '1.5fr 1fr' }}>
          <Card title="엣지 혼잡도" en="Edge congestion"
            right={
              <div className="row gap8">
                {hasLiveEdges && <Chip tone="good" dot>LIVE</Chip>}
                {n > 0 && <Chip>{curIdx + 1} / {n}</Chip>}
                {routeEdges?.routing_mode && <Chip>{routeEdges.routing_mode}</Chip>}
              </div>
            }
            style={{ padding: 0 }}>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>도로명<span className="en">Road</span></th>
                    <th className="r">거리<span className="en">Dist</span></th>
                    <th className="r">{hasLiveEdges ? '속도' : '지연'}<span className="en">{hasLiveEdges ? 'Speed' : 'Lat'}</span></th>
                    <th className="r">{hasLiveEdges ? '차량' : '부하'}<span className="en">{hasLiveEdges ? 'Veh' : 'Load'}</span></th>
                    <th>혼잡도<span className="en">Level</span></th>
                  </tr>
                </thead>
                <tbody>
                  {mergedEdges.map((e, i) => {
                    const tone = edgeCongTone(e.load_ratio || 0);
                    return (
                      <tr key={e.edge_id || i} style={e.isCurrent ? { background: 'var(--brand-tint)', fontWeight: 500 } : {}}>
                        <td>
                          {e.street_name
                            ? <><span style={{ fontSize: 12 }}>{e.street_name}</span>{e.isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}</>
                            : <><span className="mono muted" style={{ fontSize: 10 }}>{e.edge_id}</span>{e.isCurrent && <span className="chip" style={{ marginLeft: 6, fontSize: 9, background: 'var(--brand)', color: '#fff' }}>현재</span>}</>}
                          {!e.within_coverage && <span className="chip" style={{ marginLeft: 4, fontSize: 9, background: 'var(--warn-tint)', color: 'var(--warn)' }}>미커버</span>}
                        </td>
                        <td className="r">
                          {e.distance_m != null
                            ? <><span className="num">{e.distance_m.toFixed(0)}</span><span className="muted" style={{ fontSize: 10 }}> m</span></>
                            : <span className="muted">—</span>}
                        </td>
                        <td className="r">
                          {hasLiveEdges && e.speed_kmh !== null ? (
                            <><span className="num" style={{ color: `var(--${edgeCongTone(1 - (e.speed_kmh / 50))})`, fontWeight: 600 }}>{e.speed_kmh.toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> km/h</span></>
                          ) : (
                            <><span className="num" style={{ color: `var(--${latencyTone(e.latency_ms || 0)})`, fontWeight: 600 }}>{(e.latency_ms || 0).toFixed(1)}</span><span className="muted" style={{ fontSize: 10 }}> ms</span></>
                          )}
                        </td>
                        <td className="r">
                          {hasLiveEdges && e.vehicle_count !== null ? (
                            <span className="num" style={{ fontWeight: 600 }}>{e.vehicle_count}</span>
                          ) : (
                            <div className="row gap8" style={{ justifyContent: 'flex-end' }}>
                              <div className="pbar" style={{ width: 50 }}><i style={{ width: `${Math.min((e.load_ratio || 0) * 100, 100)}%`, background: `var(--${tone})` }} /></div>
                              <span className="num" style={{ fontSize: 11, fontWeight: 600, color: `var(--${tone})`, width: 32, textAlign: 'right' }}>{((e.load_ratio || 0) * 100).toFixed(0)}%</span>
                            </div>
                          )}
                        </td>
                        <td><Chip tone={tone} dot>{edgeCongLabel(e.load_ratio || 0)}</Chip></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title={hasLiveEdges ? '구간별 평균 속도' : '구간별 부하율'} en={hasLiveEdges ? 'Speed by edge' : 'Load ratio by edge'}>
            {hasLiveEdges ? (
              <>
                <BarChart items={mergedEdges.map((e, i) => ({
                  label: e.street_name || (e.edge_id || `e${i}`).slice(-8),
                  value: e.speed_kmh ?? 0,
                  display: e.speed_kmh !== null ? `${e.speed_kmh.toFixed(0)}` : '—',
                  color: e.isCurrent ? 'var(--brand)' : `var(--${edgeCongTone(e.load_ratio || 0)})`,
                }))} max={60} />
                <div className="muted" style={{ fontSize: 10.5, marginTop: 14, fontFamily: 'var(--mono)' }}>km/h · 현재 구간 + 최근 완료 6구간</div>
              </>
            ) : (
              <>
                <BarChart items={mergedEdges.map((e, i) => ({
                  label: e.street_name || (e.edge_id || `e${i}`).slice(-8),
                  value: (e.load_ratio || 0) * 100,
                  display: `${((e.load_ratio || 0) * 100).toFixed(0)}%`,
                  color: e.isCurrent ? 'var(--brand)' : `var(--${edgeCongTone(e.load_ratio || 0)})`,
                }))} max={100} />
                <div className="muted" style={{ fontSize: 10.5, marginTop: 14, fontFamily: 'var(--mono)' }}>% · 현재 구간 + 최근 완료 6구간</div>
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
window.NetworkTab = NetworkTab;
