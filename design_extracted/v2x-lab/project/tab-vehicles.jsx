/* ============================================================ Vehicles tab */
function VehicleDetail({ v, onClose }) {
  if (!v) return null;
  const s = DATA.series.perVeh[v.id] || { speed: [], lat: [] };
  const tone = latencyTone(v.latency);
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(20,40,60,0.18)', zIndex: 90 }} />
      <div className="fade" style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 420, background: 'var(--surface)', borderLeft: '1px solid var(--border)', boxShadow: 'var(--sh-3)', zIndex: 91, overflowY: 'auto' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 2 }}>
          <div className="row between">
            <div className="row gap12">
              <div style={{ width: 38, height: 38, borderRadius: 10, background: 'var(--brand-tint)', display: 'grid', placeItems: 'center', color: 'var(--brand)' }}><Icon.car size={20} /></div>
              <div>
                <div className="mono" style={{ fontSize: 15, fontWeight: 600 }}>{v.id}</div>
                <div className="row gap8" style={{ marginTop: 2 }}>
                  <Chip tone={v.state === 'moving' ? 'good' : ''} dot>{v.state === 'moving' ? '이동 중' : '정지'}</Chip>
                  <Chip tone="brand">{v.mode}</Chip>
                </div>
              </div>
            </div>
            <button className="btn icon sm" onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="stat" style={{ padding: '13px 14px' }}><div className="label">현재 속도</div><div className="value" style={{ fontSize: 22 }}>{v.speed}<span className="unit">km/h</span></div></div>
            <div className="stat" style={{ padding: '13px 14px' }}><div className="label">Latency</div><div className="value" style={{ fontSize: 22, color: `var(--${tone})` }}>{v.latency}<span className="unit">ms</span></div></div>
          </div>

          <div>
            <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>실시간 속도</b><span className="mono muted" style={{ fontSize: 10 }}>km/h · last 10 steps</span></div>
            <LineChart series={[s.speed]} height={130} yMax={60} colors={['var(--brand-2)']} />
          </div>

          <div>
            <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>Latency 추이</b><span className="mono muted" style={{ fontSize: 10 }}>ms · 위험 20ms</span></div>
            <LineChart series={[s.lat]} height={130} yMax={32} colors={[`var(--${tone})`]} threshold={20} />
          </div>

          <div>
            <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>경로 (엣지)</b><span className="mono muted" style={{ fontSize: 10 }}>EDGE PATH</span></div>
            <div className="row gap8 wrap">
              {v.path.map((e, i) => (
                <React.Fragment key={e}>
                  <span className={'chip ' + (DATA.routes[v.id] && i === 0 ? 'brand' : '')} style={{ fontFamily: 'var(--mono)' }}>{e}</span>
                  {i < v.path.length - 1 && <Icon.route size={12} style={{ color: 'var(--ink-4)' }} />}
                </React.Fragment>
              ))}
            </div>
          </div>

          <div className="row between" style={{ padding: '13px 15px', background: 'var(--surface-2)', borderRadius: 10, border: '1px solid var(--border)' }}>
            <div className="row gap12">
              <div style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--brand-2-tint, var(--brand-tint2))', display: 'grid', placeItems: 'center', color: 'var(--brand-2)' }}><Icon.antenna size={17} /></div>
              <div><div style={{ fontSize: 11, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>연결 기지국</div><div className="mono" style={{ fontSize: 14, fontWeight: 600 }}>{v.bs}</div></div>
            </div>
            <div className="num" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{v.lat.toFixed(4)}, {v.lng.toFixed(4)}</div>
          </div>
        </div>
      </div>
    </>
  );
}

function VehiclesTab() {
  const [sel, setSel] = useState(null);
  const moving = DATA.vehicles.filter(v => v.state === 'moving').length;
  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Fleet</div>
          <h1>차량 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Vehicles</span></h1>
          <div className="sub">차량별 위치 · 속도 · 경로 · 연결 기지국 — 행을 클릭하면 상세 정보가 열립니다</div>
        </div>
        <div className="row gap8">
          <Chip tone="brand">전체 {DATA.vehicles.length}대</Chip>
          <Chip tone="good" dot>이동 {moving}</Chip>
          <Chip dot>정지 {DATA.vehicles.length - moving}</Chip>
        </div>
      </div>

      <Card title="차량 목록" en="Vehicle list" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="tbl-wrap" style={{ maxHeight: 'calc(100vh - 280px)' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>차량 ID<span className="en">Vehicle</span></th>
                <th>현재 위치<span className="en">Lat, Lng</span></th>
                <th className="r">속도<span className="en">Speed</span></th>
                <th>경로<span className="en">Edge path</span></th>
                <th>현재 엣지<span className="en">Current edge</span></th>
                <th>모드<span className="en">Mode</span></th>
                <th className="r">Latency<span className="en">ms</span></th>
                <th>신호<span className="en">Signal</span></th>
              </tr>
            </thead>
            <tbody>
              {DATA.vehicles.map(v => {
                const tone = latencyTone(v.latency);
                return (
                  <tr key={v.id} className={'clickable ' + (sel === v.id ? 'selected' : '')} onClick={() => setSel(v.id)}>
                    <td><span className="mono" style={{ fontWeight: 600 }}>{v.id}</span></td>
                    <td><span className="num muted">{v.lat.toFixed(4)}, {v.lng.toFixed(4)}</span></td>
                    <td className="r"><span className="num" style={{ fontWeight: 600, color: v.speed === 0 ? 'var(--ink-4)' : 'var(--ink)' }}>{v.speed.toFixed(1)}</span> <span className="muted" style={{ fontSize: 10 }}>km/h</span></td>
                    <td><span className="mono muted" style={{ fontSize: 11.5 }}>{v.path.join(' → ')}</span></td>
                    <td><span className="chip" style={{ fontFamily: 'var(--mono)' }}>{v.edge}</span></td>
                    <td><Chip tone="brand">{v.mode}</Chip></td>
                    <td className="r"><span className="num" style={{ fontWeight: 600, color: `var(--${tone})` }}>{v.latency.toFixed(1)}</span></td>
                    <td><Chip tone={tone} dot>{tone === 'good' ? '양호' : tone === 'warn' ? '보통' : '위험'}</Chip></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <VehicleDetail v={DATA.vehicles.find(v => v.id === sel)} onClose={() => setSel(null)} />
    </div>
  );
}
window.VehiclesTab = VehiclesTab;
