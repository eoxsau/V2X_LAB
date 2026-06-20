/* ============================================================ Vehicles tab */
function VehicleDetail({ v, simHistory, onClose }) {
  if (!v) return null;
  const isLive = !!v._live;
  const tone = latencyTone(v.latency);

  const hasHistory = isLive && simHistory.length >= 2;
  const speedSeries = hasHistory
    ? [simHistory.map(h => h.speed ?? 0)]
    : (isLive ? null : [DATA.series.perVeh[v.id]?.speed ?? []]);
  const latSeries = hasHistory
    ? [simHistory.map(h => h.latency ?? 0)]
    : (isLive ? null : [DATA.series.perVeh[v.id]?.lat ?? []]);

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
                  {isLive && <Chip tone="good">LIVE</Chip>}
                </div>
              </div>
            </div>
            <button className="btn icon sm" onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="stat" style={{ padding: '13px 14px' }}><div className="label">현재 속도</div><div className="value" style={{ fontSize: 22 }}>{v.speed.toFixed(1)}<span className="unit">km/h</span></div></div>
            <div className="stat" style={{ padding: '13px 14px' }}><div className="label">Latency</div><div className="value" style={{ fontSize: 22, color: `var(--${tone})` }}>{v.latency.toFixed(1)}<span className="unit">ms</span></div></div>
          </div>

          {isLive && (
            <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="stat" style={{ padding: '13px 14px' }}><div className="label">진행률</div><div className="value" style={{ fontSize: 22 }}>{(v.progress * 100).toFixed(0)}<span className="unit">%</span></div></div>
              <div className="stat" style={{ padding: '13px 14px' }}><div className="label">신호 손실</div><div className="value" style={{ fontSize: 22 }}>{v.lossDb !== null ? v.lossDb.toFixed(1) : '—'}<span className="unit">{v.lossDb !== null ? 'dB' : ''}</span></div></div>
            </div>
          )}

          <div>
            <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>실시간 속도</b><span className="mono muted" style={{ fontSize: 10 }}>km/h · {isLive ? 'SUMO 실시간' : 'last 10 steps'}</span></div>
            {speedSeries
              ? <LineChart series={speedSeries} height={130} yMax={60} colors={['var(--brand-2)']} />
              : <div style={{ height: 130, display: 'grid', placeItems: 'center', color: 'var(--ink-4)', fontSize: 12, background: 'var(--surface-2)', borderRadius: 8 }}>데이터 수집 중…</div>
            }
          </div>

          <div>
            <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>Latency 추이</b><span className="mono muted" style={{ fontSize: 10 }}>ms · 위험 20ms</span></div>
            {latSeries
              ? <LineChart series={latSeries} height={130} yMax={32} colors={[`var(--${tone})`]} threshold={20} />
              : <div style={{ height: 130, display: 'grid', placeItems: 'center', color: 'var(--ink-4)', fontSize: 12, background: 'var(--surface-2)', borderRadius: 8 }}>데이터 수집 중…</div>
            }
          </div>

          {!isLive && (
            <div>
              <div className="row between" style={{ marginBottom: 8 }}><b style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>경로 (엣지)</b><span className="mono muted" style={{ fontSize: 10 }}>EDGE PATH</span></div>
              <div className="row gap8 wrap">
                {(v.path || []).map((e, i) => (
                  <React.Fragment key={e}>
                    <span className={'chip ' + (DATA.routes[v.id] && i === 0 ? 'brand' : '')} style={{ fontFamily: 'var(--mono)' }}>{e}</span>
                    {i < v.path.length - 1 && <Icon.route size={12} style={{ color: 'var(--ink-4)' }} />}
                  </React.Fragment>
                ))}
              </div>
            </div>
          )}

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

function VehiclesTab({ sim, vehiclePos, networkTelemetry, simHistory }) {
  const [sel, setSel] = useState(null);

  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name
    ?? '--';
  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms
    ?? 0;
  const lossDb = networkTelemetry?.estimated_penetration_loss_db ?? null;

  const liveVehicle = vehiclePos ? {
    _live: true,
    id: 'EGO-001',
    lat: vehiclePos.lat,
    lng: vehiclePos.lng,
    speed: vehiclePos.speed ?? 0,
    latency,
    lossDb,
    progress: vehiclePos.progress ?? 0,
    bs: connNode,
    mode: sim?.mode ?? '5G',
    state: vehiclePos.arrived ? 'stopped' : 'moving',
    edge: '--',
    path: [],
  } : null;

  const hasLive = !!liveVehicle;
  const vehicles = Array.isArray(liveVehicle) ? liveVehicle : (hasLive ? [liveVehicle] : []);
  const moving = vehicles.filter(v => v.state === 'moving').length;
  const selVehicle = vehicles.find(v => v.id === sel);

  // 플릿 집계 — 현재는 차량 1대뿐이라도 N대로 확장 시 자연스럽게 집계됨
  const fleetSize = vehicles.length;
  const avgLatency = fleetSize > 0 ? vehicles.reduce((s, v) => s + (v.latency ?? 0), 0) / fleetSize : null;
  const totalHandover = fleetSize > 0 ? vehicles.filter(v => v._handoverActive).length : 0;
  const disconnectedCount = vehicles.filter(v => (v.latency ?? 0) === 0 && v.state === 'moving').length;

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Fleet</div>
          <h1>차량 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>Vehicles (Fleet)</span></h1>
          <div className="sub">차량별 위치 · 속도 · 경로 · 연결 기지국 — 행을 클릭하면 상세 정보가 열립니다</div>
        </div>
        <div className="row gap8">
          {hasLive && <Chip tone="good" dot>LIVE</Chip>}
          <Chip tone="brand">전체 {vehicles.length}대</Chip>
          <Chip tone="good" dot>이동 {moving}</Chip>
          <Chip dot>정지 {vehicles.length - moving}</Chip>
        </div>
      </div>

      {/* 플릿 집계 바 — 다중차량 확장 시 N대 평균/합계로 자연 확장 */}
      <div className="grid" style={{ gridTemplateColumns: 'repeat(4,1fr)', marginBottom: 18 }}>
        <Stat label="플릿 규모" icon="car" value={fleetSize} unit="대" sub={hasLive ? '실시간 추적 중' : '—'} accent />
        <Stat label="평균 Latency" icon="latency" value={avgLatency !== null ? avgLatency.toFixed(1) : '—'} unit={avgLatency !== null ? 'ms' : ''} sub={`${fleetSize}대 평균`} />
        <Stat label="총 핸드오버" icon="antenna" value={totalHandover} unit="회" sub="전체 차량 합계" />
        <Stat label="동시 단절" icon="warn" value={disconnectedCount} unit="대" sub={disconnectedCount > 0 ? '연결 끊김 감지' : '단절 없음'} />
      </div>

      <Card title="차량 목록" en="Vehicle list" style={{ padding: 0, overflow: 'hidden' }}>
        <div className="tbl-wrap" style={{ maxHeight: 'calc(100vh - 280px)' }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>차량 ID<span className="en">Vehicle</span></th>
                <th>현재 위치<span className="en">Lat, Lng</span></th>
                <th className="r">속도<span className="en">Speed</span></th>
                {hasLive
                  ? <th className="r">진행률<span className="en">Progress</span></th>
                  : <th>경로<span className="en">Edge path</span></th>
                }
                <th>현재 엣지<span className="en">Current edge</span></th>
                <th>모드<span className="en">Mode</span></th>
                <th className="r">Latency<span className="en">ms</span></th>
                <th>신호<span className="en">Signal</span></th>
              </tr>
            </thead>
            <tbody>
              {vehicles.map(v => {
                const tone = latencyTone(v.latency);
                return (
                  <tr key={v.id} className={'clickable ' + (sel === v.id ? 'selected' : '')} onClick={() => setSel(v.id)}>
                    <td>
                      <span className="mono" style={{ fontWeight: 600 }}>{v.id}</span>
                      {v._live && <span className="chip good" style={{ marginLeft: 6, fontSize: 9.5, padding: '1px 5px' }}>LIVE</span>}
                    </td>
                    <td><span className="num muted">{v.lat.toFixed(4)}, {v.lng.toFixed(4)}</span></td>
                    <td className="r"><span className="num" style={{ fontWeight: 600, color: v.speed === 0 ? 'var(--ink-4)' : 'var(--ink)' }}>{v.speed.toFixed(1)}</span> <span className="muted" style={{ fontSize: 10 }}>km/h</span></td>
                    {hasLive
                      ? <td className="r"><span className="num" style={{ fontWeight: 600 }}>{(v.progress * 100).toFixed(0)}</span> <span className="muted" style={{ fontSize: 10 }}>%</span></td>
                      : <td><span className="mono muted" style={{ fontSize: 11.5 }}>{(v.path || []).join(' → ')}</span></td>
                    }
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

      <VehicleDetail v={selVehicle} simHistory={simHistory} onClose={() => setSel(null)} />
    </div>
  );
}
window.VehiclesTab = VehiclesTab;
