/* ============================================================ Dashboard tab */
function WorkflowDiagram({ current = 4 }) {
  return (
    <div className="row gap8" style={{ alignItems: 'stretch', overflowX: 'auto', paddingBottom: 4 }}>
      {DATA.workflow.map((s, i) => {
        const active = i + 1 === current;
        const done = i + 1 < current;
        return (
          <React.Fragment key={s.n}>
            <div style={{
              flex: '1 1 0', minWidth: 150, padding: '15px 16px', borderRadius: 12,
              background: active ? 'linear-gradient(155deg, var(--brand) 0%, var(--brand-2) 130%)' : 'var(--surface)',
              border: '1px solid ' + (active ? 'var(--brand)' : 'var(--border)'),
              color: active ? '#fff' : 'var(--ink)', position: 'relative', boxShadow: active ? 'var(--sh-2)' : 'none',
            }}>
              <div className="row between" style={{ marginBottom: 9 }}>
                <span style={{ fontSize: 19, fontWeight: 600, opacity: active ? 1 : 0.45 }}>{s.n}</span>
                {done && <span style={{ color: 'var(--good)' }}><Icon.check size={16} /></span>}
                {active && <span className="chip" style={{ background: 'rgba(255,255,255,0.2)', color: '#fff', height: 19, fontSize: 9.5 }}>현재 단계</span>}
              </div>
              <div style={{ fontWeight: 600, fontSize: 13.5, marginBottom: 2 }}>{s.ko}</div>
              <div className="mono" style={{ fontSize: 9, opacity: 0.7, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 7 }}>{s.en}</div>
              <div style={{ fontSize: 11, lineHeight: 1.4, color: active ? 'rgba(255,255,255,0.82)' : 'var(--ink-3)' }}>{s.desc}</div>
            </div>
            {i < DATA.workflow.length - 1 && (
              <div style={{ display: 'grid', placeItems: 'center', color: 'var(--ink-4)', flex: '0 0 auto' }}>
                <Icon.route size={16} style={{ transform: 'rotate(0deg)', opacity: 0.5 }} />
              </div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function Dashboard({ sim, go, vehiclePos, networkTelemetry, simHistory }) {
  const hasLive = !!vehiclePos;
  const connNode = networkTelemetry?.ego_vehicle?.connected_network_node_name
    ?? networkTelemetry?.connected_node?.name
    ?? null;
  const latency = networkTelemetry?.ego_vehicle?.current_latency_ms
    ?? networkTelemetry?.latency_ms
    ?? null;
  const speed = vehiclePos?.speed ?? null;
  const progress = vehiclePos?.progress ?? 0;

  const latencyTxt = latency !== null ? latency.toFixed(1) : '—';
  const latencyTone = latency !== null ? (latency >= 20 ? 'bad' : latency >= 12 ? 'warn' : 'good') : null;

  const cards = [
    {
      label: '시뮬레이션 상태', icon: 'clock',
      value: sim.running ? '실행 중' : sim.elapsed > 0 ? '일시정지' : '미시작',
      sub: fmtClock(sim.elapsed) + ' 경과', accent: true,
    },
    {
      label: '차량 수', icon: 'car',
      value: hasLive ? '1' : '12', unit: '대',
      sub: hasLive ? (vehiclePos.arrived ? '도착 완료' : `진행 ${(progress * 100).toFixed(0)}%`) : '이동 7 · 정지 5',
    },
    {
      label: '현재 속도', icon: 'speed',
      value: speed !== null ? speed.toFixed(1) : '34.7', unit: 'km/h',
      sub: speed !== null ? (hasLive ? '실시간 SUMO 데이터' : 'SUMO 데이터') : 'σ ±11.2 km/h',
    },
    {
      label: '네트워크 모드', icon: 'net',
      value: sim.mode === '6G' ? '6G-like' : '5G NR',
      sub: sim.mode === '6G' ? 'L_base 0.1ms' : 'L_base 1.0ms',
    },
    {
      label: '연결 기지국', icon: 'antenna',
      value: connNode ?? '—', unit: '',
      sub: connNode
        ? `거리 ${(networkTelemetry?.distance_m ?? 0).toFixed(0)}m`
        : (hasLive ? '기지국 없음' : 'BS-02 혼잡 (62.4%)'),
    },
    {
      label: '현재 Latency', icon: 'latency',
      value: latencyTxt, unit: latency !== null ? 'ms' : '',
      sub: latency !== null
        ? (latency > 20 ? '위험 상태 (>20ms)' : latency > 12 ? '보통' : '정상 범위')
        : (hasLive ? '기지국 미연결' : '위험 이벤트 3건'),
    },
  ];

  const latencyHistory = simHistory.length >= 2
    ? simHistory.map(h => h.latency ?? 0)
    : DATA.series.latencyAvg;
  const latencyLabels = simHistory.length >= 2
    ? simHistory.map((h, i) => i % Math.max(1, Math.floor(simHistory.length / 6)) === 0 ? fmtClock(h.t) : '')
    : ['0:00','','0:30','','1:00','','1:30','','2:00','','3:00','','3:42'];

  const congestionScore = networkTelemetry?.connected_node?.congestion_score ?? null;
  const donutValue = congestionScore !== null ? Math.round(congestionScore * 100) : 29;

  const candidates = networkTelemetry?.candidate_nodes ?? [];
  const bsItems = candidates.length > 0
    ? candidates.slice(0, 5).map(c => {
        const ms = c.predicted_latency_ms ?? 0;
        const pct = Math.min(100, Math.round(ms / 0.3));
        return {
          label: (c.name ?? c.id ?? '').replace(/기지국\s*/, 'BS-'),
          value: pct,
          display: ms.toFixed(1) + 'ms',
          color: pct > 50 ? 'var(--bad)' : pct > 25 ? 'var(--warn)' : 'var(--good)',
        };
      })
    : DATA.baseStations.map(b => ({
        label: b.id, value: b.rho, display: b.rho + '%',
        color: b.rho > 50 ? 'var(--bad)' : b.rho > 25 ? 'var(--warn)' : 'var(--good)',
      }));

  return (
    <div className="page-pad fade">
      <div className="page-head">
        <div>
          <div className="eyebrow">Overview</div>
          <h1>대시보드</h1>
          <div className="sub">자율주행 V2X AI 라우팅 시뮬레이션 전체 현황</div>
        </div>
        <div className="row gap12">
          {hasLive && <Chip tone="good" dot>LIVE</Chip>}
          <button className="btn" onClick={() => go('analysis')}><Icon.chart size={15} /> 분석 보기</button>
          <button className="btn primary" onClick={() => go('simulation')}><Icon.map size={15} /> 시뮬레이션 열기</button>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: 22 }}>
        {cards.map((c, i) => <Stat key={i} {...c} />)}
      </div>

      <Card title="워크플로우" en="Workflow" right={<span className="muted" style={{ fontSize: 11.5 }}>5단계 중 <b className="num" style={{ color: 'var(--brand-2)' }}>4</b>단계 진행 중</span>}>
        <WorkflowDiagram current={4} />
      </Card>

      <div className="grid" style={{ gridTemplateColumns: '1.4fr 1fr', marginTop: 18 }}>
        <Card
          title="Latency 추이"
          en={simHistory.length >= 2 ? '실시간 · ms' : 'Avg latency / time'}
          right={
            latency !== null
              ? <Chip tone={latencyTone} dot>{latencyTxt}ms</Chip>
              : <Chip tone="good" dot>안정</Chip>
          }
        >
          <LineChart series={[latencyHistory]} height={190} yUnit="ms" yMax={32} threshold={20} labels={latencyLabels} />
          <div className="row gap16" style={{ marginTop: 8, fontSize: 11 }}>
            <span className="row gap8"><span style={{ width: 18, height: 3, background: 'var(--brand-2)', borderRadius: 2 }} /> {simHistory.length >= 2 ? '실시간 Latency' : '평균 Latency'}</span>
            <span className="row gap8"><span style={{ width: 18, height: 0, borderTop: '2px dashed var(--bad)' }} /> 위험 임계 20ms</span>
          </div>
        </Card>
        <Card
          title="네트워크 부하"
          en={candidates.length > 0 ? '후보 기지국 · latency' : 'Network load'}
          right={<span className="mono muted" style={{ fontSize: 10 }}>{candidates.length > 0 ? 'ms' : '% capacity'}</span>}
        >
          <div className="row" style={{ justifyContent: 'center', padding: '6px 0 14px' }}>
            <Donut
              value={donutValue}
              label={congestionScore !== null ? '혼잡도' : '전체 점유율'}
              color="var(--brand-2)"
              size={120}
            />
          </div>
          <BarChart items={bsItems} max={100} />
        </Card>
      </div>
    </div>
  );
}
window.Dashboard = Dashboard;
