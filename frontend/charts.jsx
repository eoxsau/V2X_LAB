/* ============================================================
   SVG charts — lightweight, dependency-free (exported to window)
   Sparkline · LineChart (area) · BarChart · Donut · MiniMap · SegmentStrip
   ============================================================ */

function niceMax(v) {const p = Math.pow(10, Math.floor(Math.log10(v)));return Math.ceil(v / p) * p;}

/* ---- Sparkline (inline, table cells) ----------------------- */
function Sparkline({ data, w = 80, h = 24, color = 'var(--brand-2)', fill = true }) {
  const max = Math.max(...data),min = Math.min(...data);
  const rng = max - min || 1;
  const pts = data.map((v, i) => [i / (data.length - 1) * w, h - 2 - (v - min) / rng * (h - 4)]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L${w} ${h} L0 ${h} Z`;
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {fill && <path d={area} fill={color} opacity="0.12" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2" fill={color} />
    </svg>);

}

/* ---- LineChart with area, axes, grid ----------------------- */
function LineChart({ series, height = 200, yUnit = '', yMax, colors, labels, threshold, xLabel, yLabel }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(560);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((es) => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);
  // extra bottom padding when xLabel present
  const padL = 42, padR = 12, padT = 14, padB = xLabel ? 32 : 22;
  const H = height, innerW = w - padL - padR, innerH = H - padT - padB;
  const allVals = series.flat();
  const max = yMax || niceMax(Math.max(...allVals) * 1.1);
  const n = series[0].length;
  const x = (i) => padL + i / (n - 1) * innerW;
  const y = (v) => padT + innerH - v / max * innerH;
  const ticks = 4;
  const palette = colors || ['var(--brand-2)', 'var(--good)', 'var(--warn)', 'var(--bad)'];

  return (
    <div ref={wrapRef} style={{ width: '100%' }}>
      <svg width={w} height={H}>
        {/* y-axis gridlines + tick labels */}
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const v = max / ticks * i;
          return (
            <g key={i}>
              <line x1={padL} x2={w - padR} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" />
              <text x={padL - 6} y={y(v) + 3.5} textAnchor="end" fontSize="9.5" fill="var(--ink-4)" fontFamily="var(--mono)">{v % 1 === 0 ? v : v.toFixed(1)}</text>
            </g>);
        })}
        {/* x-axis tick labels */}
        {labels && labels.map((l, i) =>
          <text key={i} x={x(i)} y={H - (xLabel ? 18 : 6)} textAnchor="middle" fontSize="9" fill="var(--ink-4)" fontFamily="var(--mono)">{l}</text>
        )}
        {/* threshold line */}
        {threshold != null &&
          <line x1={padL} x2={w - padR} y1={y(threshold)} y2={y(threshold)} stroke="var(--bad)" strokeWidth="1.2" strokeDasharray="4 3" opacity="0.7" />
        }
        {/* series paths */}
        {series.map((s, si) => {
          const c = palette[si % palette.length];
          const d = s.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
          const area = d + ` L${x(n - 1)} ${padT + innerH} L${padL} ${padT + innerH} Z`;
          return (
            <g key={si}>
              {si === 0 && series.length === 1 && <path d={area} fill={c} opacity="0.10" />}
              <path d={d} fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              {s.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="2.4" fill="#fff" stroke={c} strokeWidth="1.5" />)}
            </g>);
        })}
        {/* y-axis label (rotated) */}
        {yLabel && (
          <text x={11} y={(padT + innerH / 2).toFixed(1)} textAnchor="middle" dominantBaseline="middle"
                fontSize="9" fill="var(--ink-4)" fontFamily="var(--sans)"
                transform={`rotate(-90 11 ${padT + innerH / 2})`}>{yLabel}</text>
        )}
        {/* x-axis label */}
        {xLabel && (
          <text x={(padL + innerW / 2).toFixed(1)} y={H - 3} textAnchor="middle"
                fontSize="9" fill="var(--ink-4)" fontFamily="var(--sans)">{xLabel}</text>
        )}
      </svg>
      {yUnit && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-4)', marginTop: -4, marginLeft: padL }}>{yUnit}</div>}
    </div>);
}

/* ---- BarChart (horizontal load bars) ----------------------- */
function BarChart({ items, max, height = 180 }) {
  const m = max || niceMax(Math.max(...items.map((i) => i.value)));
  return (
    <div className="col gap12">
      {items.map((it, i) =>
      <div key={i} className="row gap12" style={{ alignItems: 'center' }}>
          <div style={{ width: 86, fontSize: 11.5, color: 'var(--ink-2)', textAlign: 'right', fontWeight: 500 }}>{it.label}</div>
          <div style={{ flex: 1, height: 18, background: 'var(--surface-3)', borderRadius: 5, overflow: 'hidden', position: 'relative' }}>
            <div style={{ width: it.value / m * 100 + '%', height: '100%', background: it.color || 'var(--brand-2)', borderRadius: 5, transition: 'width .5s' }} />
          </div>
          <div className="num" style={{ width: 56, fontSize: 12, fontWeight: 600, textAlign: 'right' }}>{it.display ?? it.value}</div>
        </div>
      )}
    </div>);

}

/* ---- SegmentStrip (route corridor / status strip) --------- */
function SegmentStrip({ items, height = 74 }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(480);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((es) => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);
  const gap = 8;
  const count = Math.max(items.length, 1);
  const segW = Math.max((w - gap * (count - 1)) / count, 48);
  return (
    <div ref={wrapRef} style={{ width: '100%' }}>
      <div className="row" style={{ gap, alignItems: 'stretch' }}>
        {items.map((it, i) => (
          <div key={i} style={{ width: segW, minWidth: 48 }}>
            <div
              title={`${it.title || it.label || ''}${it.meta ? ` · ${it.meta}` : ''}`}
              style={{
                height: height - 24,
                borderRadius: 10,
                background: 'var(--surface-2)',
                border: `1.5px solid ${it.isCurrent ? 'var(--brand)' : 'var(--border)'}`,
                borderTop: `4px solid ${it.accent || 'var(--border)'}`,
                boxShadow: it.isCurrent ? '0 0 0 2px var(--brand-tint)' : 'none',
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'space-between',
                padding: '8px 8px 7px',
                color: it.textColor || 'var(--ink)',
              }}
            >
              <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.04em', opacity: 0.75 }}>{it.badge || `#${i + 1}`}</span>
              {it.value != null && <span className="num" style={{ fontSize: 11.5, fontWeight: 700 }}>{it.value}</span>}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--ink-3)', textAlign: 'center', marginTop: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {it.label || `edge ${i + 1}`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---- Donut / gauge ----------------------------------------- */
function Donut({ value, max = 100, size = 96, stroke = 11, color = 'var(--brand-2)', label, unit = '%' }) {
  const r = (size - stroke) / 2,c = 2 * Math.PI * r;
  const pct = Math.min(value / max, 1);
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
        strokeDasharray={c} strokeDashoffset={c * (1 - pct)} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dashoffset .6s' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', flexDirection: 'column' }}>
        <div className="num" style={{ fontSize: size * 0.24, fontWeight: 600, height: "25px", lineHeight: "2" }}>{value}<span style={{ fontSize: size * 0.12, color: 'var(--ink-3)' }}>{unit}</span></div>
        {label && <div style={{ fontSize: 9.5, color: 'var(--ink-4)', marginTop: 2 }}>{label}</div>}
      </div>
    </div>);

}

/* ---- MiniMap: schematic route on a grid (no tiles) --------- */
function MiniMap({ path, risk, color = 'var(--brand-2)', height = 180, bs = [], label, extraPaths = [] }) {
  // normalise lat/lng to box — include extraPaths so alternates always fit in frame
  const all = path.concat(bs.map((b) => [b.lat, b.lng])).concat(extraPaths.flatMap((p) => p.path));
  const lats = all.map((p) => p[0]),lngs = all.map((p) => p[1]);
  const minLat = Math.min(...lats),maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs),maxLng = Math.max(...lngs);
  const padX = 24,padY = 20,W = 320,H = height;
  const sx = (v) => padX + (v - minLng) / (maxLng - minLng || 1) * (W - padX * 2);
  const sy = (v) => H - padY - (v - minLat) / (maxLat - minLat || 1) * (H - padY * 2);
  const toD = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + sx(p[1]).toFixed(1) + ' ' + sy(p[0]).toFixed(1)).join(' ');
  const d = toD(path);
  const riskD = risk ? toD(risk) : null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block', background: 'var(--surface-2)', borderRadius: 10 }}>
      <defs>
        <pattern id={'grid' + label} width="26" height="26" patternUnits="userSpaceOnUse">
          <path d="M26 0H0V26" fill="none" stroke="var(--border)" strokeWidth="1" />
        </pattern>
      </defs>
      <rect x="0" y="0" width={W} height={H} fill={`url(#grid${label})`} />
      {/* alternate candidates drawn first (dashed, thinner) so the main path stays on top */}
      {extraPaths.map((ep, i) => (
        <path key={'extra' + i} d={toD(ep.path)} fill="none" stroke={ep.color} strokeWidth="2.5"
              strokeDasharray="6 4" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
      ))}
      <path d={d} fill="none" stroke={color} strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
      {riskD && <path d={riskD} fill="none" stroke="var(--bad)" strokeWidth="5" strokeLinecap="round" opacity="0.85" />}
      {bs.map((b, i) =>
      <g key={i}>
          <circle cx={sx(b.lng)} cy={sy(b.lat)} r="11" fill="var(--brand-2)" opacity="0.13" />
          <circle cx={sx(b.lng)} cy={sy(b.lat)} r="4" fill="var(--brand-2)" stroke="#fff" strokeWidth="1.5" />
        </g>
      )}
      {/* origin / dest */}
      <circle cx={sx(path[0][1])} cy={sy(path[0][0])} r="5.5" fill="var(--m-origin)" stroke="#fff" strokeWidth="2" />
      <circle cx={sx(path[path.length - 1][1])} cy={sy(path[path.length - 1][0])} r="5.5" fill="var(--m-dest)" stroke="#fff" strokeWidth="2" />
    </svg>);

}

/* ---- RadarChart (normalised spider — higher axis = better on all axes) ─ */
function RadarChart({ algorithms, metrics, colors, size = 210, legendRight = false }) {
  // algorithms: [{key, label, values: {metricKey: 0..1}}]
  // metrics:    [{key, label}]
  if (!algorithms?.length || metrics?.length < 3) return null;

  const cx = size / 2, cy = size / 2;
  const r  = size * 0.34;
  const N  = metrics.length;
  const ang = i => -Math.PI / 2 + (2 * Math.PI * i) / N;
  const pt  = (i, v) => [cx + r * v * Math.cos(ang(i)), cy + r * v * Math.sin(ang(i))];
  const palette = colors || ['var(--brand-2)', 'var(--good)', 'var(--warn)', 'var(--bad)', '#A855F7', '#F6A623'];

  return (
    <svg width={size} height={size} style={{ display: 'block', maxWidth: '100%' }}>
      {/* grid rings */}
      {[0.25, 0.5, 0.75, 1.0].map(lv => {
        const pts = metrics.map((_, i) => pt(i, lv));
        const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ') + 'Z';
        return <path key={lv} d={d} fill="none" stroke="var(--border)"
                     strokeWidth={lv === 1.0 ? 1.5 : 0.7}
                     strokeDasharray={lv < 1.0 ? '3 2' : undefined} />;
      })}
      {/* spokes */}
      {metrics.map((_, i) => {
        const [x2, y2] = pt(i, 1.0);
        return <line key={i} x1={cx.toFixed(1)} y1={cy.toFixed(1)} x2={x2.toFixed(1)} y2={y2.toFixed(1)}
                     stroke="var(--border)" strokeWidth="0.8" />;
      })}
      {/* axis labels */}
      {metrics.map((m, i) => {
        const [x, y] = pt(i, 1.30);
        return <text key={i} x={x.toFixed(1)} y={y.toFixed(1)} textAnchor="middle" dominantBaseline="middle"
                     fontSize="9" fill="var(--ink-3)" fontFamily="var(--sans)">{m.label}</text>;
      })}
      {/* algorithm polygons */}
      {algorithms.map((algo, ai) => {
        const pts = metrics.map((m, i) => pt(i, Math.max(0, Math.min(1, algo.values[m.key] ?? 0))));
        const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ') + 'Z';
        const c = palette[ai % palette.length];
        return (
          <g key={algo.key}>
            <path d={d} fill={c} fillOpacity="0.13" stroke={c} strokeWidth="2" strokeLinejoin="round" />
            {pts.map((p, i) => <circle key={i} cx={p[0].toFixed(1)} cy={p[1].toFixed(1)} r="2.5" fill={c} />)}
          </g>
        );
      })}
    </svg>
  );
}

/* ---- HistogramChart (distribution bars + P50/P90/P95 lines) ----------- */
function HistogramChart({ values, bucketCount = 12, xLabel = '', height = 140 }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(400);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(es => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  if (!values?.length) {
    return <div className="muted" style={{ fontSize: 12, padding: '20px 0', textAlign: 'center' }}>분포 데이터 없음</div>;
  }

  const mn = Math.min(...values), mx = Math.max(...values);
  const span = mx - mn || 1;
  const step = span / bucketCount;
  const counts = Array(bucketCount).fill(0);
  values.forEach(v => {
    counts[Math.min(bucketCount - 1, Math.floor((v - mn) / step))]++;
  });
  const maxCnt = Math.max(...counts);

  const sorted = [...values].sort((a, b) => a - b);
  const qval = p => sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p / 100 * sorted.length) - 1))];

  const padL = 32, padR = 8, padT = 10, padB = xLabel ? 32 : 24;
  const IW = w - padL - padR, IH = height - padT - padB;
  const bw = IW / bucketCount;
  const bx = i => padL + i * bw;
  const bh = c => c / maxCnt * IH;
  const px = v => padL + (v - mn) / span * IW;

  // colour by bucket position: low latency = good, high = bad
  const bucketClr = i => {
    const f = i / (bucketCount - 1);
    return f < 0.45 ? 'var(--good)' : f < 0.75 ? 'var(--warn)' : 'var(--bad)';
  };

  return (
    <div ref={wrapRef} style={{ width: '100%' }}>
      <svg width={w} height={height}>
        {/* y gridlines */}
        {[0, 0.5, 1.0].map((f, ii) => {
          const yy = padT + IH - f * IH;
          return (
            <g key={ii}>
              <line x1={padL} x2={w - padR} y1={yy} y2={yy} stroke="var(--border)" strokeWidth="0.8" />
              <text x={padL - 4} y={yy + 3.5} textAnchor="end" fontSize="8.5" fill="var(--ink-4)" fontFamily="var(--mono)">
                {Math.round(f * maxCnt)}
              </text>
            </g>
          );
        })}
        {/* y-axis label */}
        <text x={9} y={(padT + IH / 2).toFixed(1)} textAnchor="middle" dominantBaseline="middle"
              fontSize="8.5" fill="var(--ink-4)" fontFamily="var(--sans)"
              transform={`rotate(-90 9 ${padT + IH / 2})`}>빈도</text>
        {/* bars */}
        {counts.map((c, i) => (
          <rect key={i} x={(bx(i) + 0.5).toFixed(1)} y={(padT + IH - bh(c)).toFixed(1)}
                width={Math.max(bw - 1, 1).toFixed(1)} height={bh(c).toFixed(1)}
                fill={bucketClr(i)} rx="2" opacity="0.82" />
        ))}
        {/* x-axis ticks */}
        {[0, 0.25, 0.5, 0.75, 1.0].map((f, ii) => (
          <text key={ii} x={(padL + f * IW).toFixed(1)} y={(padT + IH + 12).toFixed(1)}
                textAnchor="middle" fontSize="8.5" fill="var(--ink-4)" fontFamily="var(--mono)">
            {(mn + f * span).toFixed(0)}
          </text>
        ))}
        {/* x-axis label */}
        {xLabel && (
          <text x={(padL + IW / 2).toFixed(1)} y={(height - 2).toFixed(1)}
                textAnchor="middle" fontSize="9" fill="var(--ink-4)" fontFamily="var(--sans)">{xLabel}</text>
        )}
        {/* percentile lines */}
        {[[50, 'P50', 'var(--brand-2)'], [90, 'P90', 'var(--warn)'], [95, 'P95', 'var(--bad)']].map(([p, lbl, c]) => {
          const val = qval(p);
          const xp = px(val);
          if (xp < padL - 2 || xp > w - padR + 2) return null;
          return (
            <g key={p}>
              <line x1={xp.toFixed(1)} x2={xp.toFixed(1)} y1={padT} y2={(padT + IH).toFixed(1)}
                    stroke={c} strokeWidth="1.5" strokeDasharray="4 3" />
              <text x={(xp + 3).toFixed(1)} y={(padT + 11).toFixed(1)} fontSize="8.5" fill={c} fontFamily="var(--mono)">{lbl}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ---- DeltaBarChart (centred waterfall — improvement vs baseline) ------- */
function DeltaBarChart({ deltas, height }) {
  // deltas: [{label, value, unit, lowerBetter, digits}]
  //   value  = selected − baseline for that metric
  //   lowerBetter=true  → negative value = improvement  (plotted rightward)
  //   lowerBetter=false → positive value = improvement  (plotted rightward)
  // Improvements always extend RIGHT (green), regressions LEFT (red).
  const wrapRef = useRef(null);
  const [w, setW] = useState(400);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(es => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  if (!deltas?.length) return null;

  // plot value: positive = improvement regardless of direction convention
  const pv = d => d.lowerBetter ? -d.value : d.value;
  const maxAbs = Math.max(...deltas.map(d => Math.abs(pv(d))), 1e-9);

  const H = height || Math.max(42 * deltas.length + 24, 100);
  const labelW = 88, valueW = 62, padR = 8;
  const barAreaW = w - labelW - valueW - padR;
  const rowH = (H - 20) / deltas.length;
  const thick = Math.min(rowH - 10, 18);
  const cx = labelW + barAreaW / 2;

  return (
    <div ref={wrapRef} style={{ width: '100%' }}>
      <svg width={w} height={H}>
        {/* centre axis */}
        <line x1={cx.toFixed(1)} x2={cx.toFixed(1)} y1={6} y2={H - 14}
              stroke="var(--border)" strokeWidth="1.5" />
        {/* legend arrows */}
        <text x={(cx - 6).toFixed(1)} y={H - 3} textAnchor="end"
              fontSize="8.5" fill="var(--bad)" fontFamily="var(--sans)">← 악화</text>
        <text x={(cx + 6).toFixed(1)} y={H - 3} textAnchor="start"
              fontSize="8.5" fill="var(--good)" fontFamily="var(--sans)">개선 →</text>
        {deltas.map((d, i) => {
          const yy = 12 + i * rowH + (rowH - thick) / 2;
          const plotted = pv(d);
          const neutral = Math.abs(plotted) < 1e-9;
          const color = neutral ? 'var(--border)' : plotted > 0 ? 'var(--good)' : 'var(--bad)';
          const norm  = plotted / maxAbs;
          const blen  = Math.abs(norm) * (barAreaW / 2 - 6);
          const bx    = norm >= 0 ? cx : cx - blen;
          const disp  = (d.value > 0 ? '+' : '') + Number(d.value).toFixed(d.digits ?? 1) + (d.unit || '');
          const valX  = norm >= 0 ? (bx + blen + 5) : (bx - 4);

          return (
            <g key={i}>
              <text x={(labelW - 5).toFixed(1)} y={(yy + thick / 2 + 4).toFixed(1)}
                    textAnchor="end" fontSize="10.5" fill="var(--ink-2)" fontFamily="var(--sans)">{d.label}</text>
              {!neutral && (
                <rect x={bx.toFixed(1)} y={yy.toFixed(1)}
                      width={Math.max(blen, 2).toFixed(1)} height={thick}
                      fill={color} rx="2" opacity="0.85" />
              )}
              {neutral && (
                <line x1={(cx - 3)} x2={(cx + 3)} y1={(yy + thick / 2)} y2={(yy + thick / 2)}
                      stroke="var(--border)" strokeWidth="2" />
              )}
              <text x={valX.toFixed(1)} y={(yy + thick / 2 + 4).toFixed(1)}
                    textAnchor={norm >= 0 ? 'start' : 'end'}
                    fontSize="10" fill={color} fontFamily="var(--mono)" fontWeight="600">{disp}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ---- CDFChart — Latency Cumulative Distribution Function ---- */
/* Threshold lines: 10ms (URLLC), 100ms (safety), 500ms (tolerant) */
function CDFChart({ latencies, height = 220 }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(500);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver(es => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  if (!latencies || latencies.length === 0) {
    return <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ink-4)', fontSize: 12 }}>데이터 없음</div>;
  }

  const sorted = [...latencies].filter(v => v != null && v >= 0).sort((a, b) => a - b);
  if (sorted.length === 0) return <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ink-4)', fontSize: 12 }}>데이터 없음</div>;

  const padL = 44, padR = 16, padT = 16, padB = 32;
  const innerW = w - padL - padR;
  const innerH = height - padT - padB;

  // x axis: 0 to max(sorted) * 1.1, y axis: 0 to 100%
  const xMax = Math.ceil(Math.max(...sorted) * 1.1) || 200;
  const xScale = v => padL + (v / xMax) * innerW;
  const yScale = p => padT + innerH - (p / 100) * innerH;

  // CDF points
  const cdfPts = sorted.map((v, i) => [xScale(v), yScale((i + 1) / sorted.length * 100)]);
  const linePath = cdfPts.map((p, i) => (i === 0 ? `M${p[0].toFixed(1)},${p[1].toFixed(1)}` : `L${p[0].toFixed(1)},${p[1].toFixed(1)}`)).join(' ');
  const areaPath = linePath + ` L${xScale(sorted[sorted.length - 1]).toFixed(1)},${yScale(0).toFixed(1)} L${padL},${yScale(0).toFixed(1)} Z`;

  // Threshold lines: 10ms URLLC, 100ms safety, 500ms tolerant
  const thresholds = [
    { ms: 10,  label: 'URLLC 10ms',      color: '#22c55e' },
    { ms: 100, label: '안전 100ms',       color: '#f59e0b' },
    { ms: 500, label: '고내성 500ms',     color: '#ef4444' },
  ];

  // Y-axis ticks 0,25,50,75,100
  const yTicks = [0, 25, 50, 75, 100];
  const xTicks = [0, Math.round(xMax * 0.25), Math.round(xMax * 0.5), Math.round(xMax * 0.75), xMax];

  return (
    <div ref={wrapRef} style={{ width: '100%' }}>
      <svg width={w} height={height} style={{ display: 'block', overflow: 'visible' }}>
        {/* y-axis gridlines */}
        {yTicks.map(p => (
          <g key={p}>
            <line x1={padL} x2={padL + innerW} y1={yScale(p)} y2={yScale(p)}
                  stroke="var(--border)" strokeWidth={p === 0 ? 1 : 0.5} strokeDasharray={p === 0 ? '' : '4 3'} />
            <text x={padL - 5} y={yScale(p) + 4} textAnchor="end" fontSize="10" fill="var(--ink-4)">{p}%</text>
          </g>
        ))}
        {/* x-axis ticks */}
        {xTicks.map(v => (
          <g key={v}>
            <line x1={xScale(v)} x2={xScale(v)} y1={padT + innerH} y2={padT + innerH + 4}
                  stroke="var(--border)" strokeWidth="1" />
            <text x={xScale(v)} y={padT + innerH + 15} textAnchor="middle" fontSize="10" fill="var(--ink-4)">{v}</text>
          </g>
        ))}
        {/* x-axis label */}
        <text x={padL + innerW / 2} y={height - 2} textAnchor="middle" fontSize="10" fill="var(--ink-4)">지연 (ms)</text>
        {/* CDF area + line */}
        <path d={areaPath} fill="var(--brand-2)" opacity="0.10" />
        <path d={linePath} fill="none" stroke="var(--brand-2)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {/* Threshold vertical lines */}
        {thresholds.map(({ ms, label, color }) => {
          if (xScale(ms) < padL || xScale(ms) > padL + innerW) return null;
          return (
            <g key={ms}>
              <line x1={xScale(ms)} x2={xScale(ms)} y1={padT} y2={padT + innerH}
                    stroke={color} strokeWidth="1.5" strokeDasharray="5 3" opacity="0.8" />
              <text x={xScale(ms) + 3} y={padT + 11} fontSize="9" fill={color} fontWeight="600">{label}</text>
            </g>
          );
        })}
        {/* Axes borders */}
        <line x1={padL} x2={padL} y1={padT} y2={padT + innerH} stroke="var(--border)" strokeWidth="1" />
        <line x1={padL} x2={padL + innerW} y1={padT + innerH} y2={padT + innerH} stroke="var(--border)" strokeWidth="1" />
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6, fontSize: 11, color: 'var(--ink-3)' }}>
        {thresholds.map(({ ms, label, color }) => (
          <span key={ms} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 16, height: 0, borderTop: `2px dashed ${color}`, display: 'inline-block' }} />
            {label}
          </span>
        ))}
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 16, height: 3, background: 'var(--brand-2)', borderRadius: 2, display: 'inline-block' }} />
          측정 CDF
        </span>
      </div>
    </div>
  );
}

Object.assign(window, { Sparkline, LineChart, BarChart, Donut, MiniMap, SegmentStrip, RadarChart, HistogramChart, DeltaBarChart });
