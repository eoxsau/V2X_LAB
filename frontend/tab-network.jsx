/* ============================================================ Network tab */
function NetworkTab() {
  const totalVeh = DATA.baseStations.reduce((a, b) => a + b.vehicles, 0);
  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Infrastructure</div>
          <h1>네트워크 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Network</span></h1>
          <div className="sub">기지국 상태 및 엣지(도로구간) 혼잡도</div>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 18 }}>
        <Stat label="활성 기지국" icon="antenna" value="3" unit="개" sub="1개 혼잡" />
        <Stat label="연결 차량 합계" icon="car" value={totalVeh} unit="대" sub="용량 1,500대 중" />
        <Stat label="평균 점유율 ρ" icon="net" value="29.9" unit="%" sub="BS-02 최대 62.4%" accent />
        <Stat label="평균 큐 지연" icon="latency" value="4.7" unit="ms" sub="L_queue" />
      </div>

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
