/* ============================================================ Network tab */
function NetworkTab({ networkTelemetry }) {
  const hasLive = !!networkTelemetry;
  const connNode = networkTelemetry?.connected_node ?? null;
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

  const totalVeh = DATA.baseStations.reduce((a, b) => a + b.vehicles, 0);
  const statCount = hasLive ? (liveStations.length || '—') : '3';
  const statConnected = hasLive ? (connName ?? '없음') : '3개';
  const statLatency = latency !== null ? latency.toFixed(1) : '4.7';
  const statCong = congestion !== null ? (congestion * 100).toFixed(1) : '29.9';

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
        <Stat label="활성 기지국" icon="antenna" value={statCount} unit={hasLive ? '' : '개'} sub={hasLive ? '후보 노드 수' : '1개 혼잡'} />
        <Stat label={hasLive ? '연결 기지국' : '연결 차량 합계'} icon={hasLive ? 'antenna' : 'car'} value={hasLive ? (connName ?? '없음') : totalVeh} unit={hasLive ? '' : '대'} sub={hasLive ? `거리 ${distanceM !== null ? distanceM.toFixed(0) + 'm' : '—'}` : '용량 1,500대 중'} />
        <Stat label={hasLive ? '혼잡도' : '평균 점유율 ρ'} icon="net" value={statCong} unit="%" sub={hasLive ? (connName ?? '연결 기지국') : 'BS-02 최대 62.4%'} accent />
        <Stat label={hasLive ? '현재 Latency' : '평균 큐 지연'} icon="latency" value={statLatency} unit="ms" sub={hasLive ? (latency !== null && latency > 20 ? '위험 수준' : '정상 범위') : 'L_queue'} />
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
        <Card title="기지국 상태" en="Base stations" right={<Chip tone="bad" dot>BS-02 혼잡</Chip>} style={{ padding: 0, marginBottom: 18 }}>
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
                {DATA.baseStations.map(b => {
                  const tone = b.rho > 50 ? 'bad' : b.rho > 25 ? 'warn' : 'good';
                  return (
                    <tr key={b.id}>
                      <td><span className="mono" style={{ fontWeight: 600 }}>{b.id}</span></td>
                      <td><span className="num muted">{b.lat.toFixed(4)}, {b.lng.toFixed(4)}</span></td>
                      <td className="r"><span className="num">{b.height}</span> <span className="muted" style={{ fontSize: 10 }}>m</span></td>
                      <td className="r"><span className="num" style={{ fontWeight: 600 }}>{b.vehicles}</span></td>
                      <td className="r"><span className="num muted">{b.capacity}</span></td>
                      <td>
                        <div className="row gap8" style={{ minWidth: 130 }}>
                          <div className="pbar" style={{ flex: 1 }}><i style={{ width: b.rho + '%', background: `var(--${tone})` }} /></div>
                          <span className="num" style={{ fontSize: 11.5, fontWeight: 600, color: `var(--${tone})`, width: 42, textAlign: 'right' }}>{b.rho}%</span>
                        </div>
                      </td>
                      <td className="r"><span className="num">{b.lqueue.toFixed(1)}</span></td>
                      <td><Chip tone={statusTone[b.status]} dot>{statusKo[b.status]}</Chip></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid" style={{ gridTemplateColumns: '1.5fr 1fr' }}>
        <Card title="엣지 혼잡도" en="Edge congestion" style={{ padding: 0 }}>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>엣지 ID<span className="en">Edge</span></th><th>도로명<span className="en">Road</span></th><th className="r">차량 수<span className="en">Count</span></th><th className="r">평균 속도<span className="en">Speed</span></th><th>혼잡도<span className="en">Level</span></th></tr>
              </thead>
              <tbody>
                {DATA.edges.map(e => (
                  <tr key={e.id}>
                    <td><span className="chip" style={{ fontFamily: 'var(--mono)' }}>{e.id}</span></td>
                    <td>{e.name}<div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-4)' }}>{e.nameEn}</div></td>
                    <td className="r"><span className="num" style={{ fontWeight: 600 }}>{e.vehicles}</span></td>
                    <td className="r"><span className="num">{e.speed.toFixed(1)}</span> <span className="muted" style={{ fontSize: 10 }}>km/h</span></td>
                    <td><Chip tone={levelTone[e.level]} dot>{levelKo[e.level]}</Chip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="구간별 평균 속도" en="Speed by edge">
          <BarChart items={DATA.edges.map(e => ({
            label: e.id.replace('edge_', ''), value: e.speed, display: e.speed.toFixed(1),
            color: `var(--${levelTone[e.level]})`,
          }))} max={60} />
          <div className="muted" style={{ fontSize: 10.5, marginTop: 14, fontFamily: 'var(--mono)' }}>km/h · 낮은 속도 = 높은 혼잡</div>
        </Card>
      </div>
    </div>
  );
}
window.NetworkTab = NetworkTab;
