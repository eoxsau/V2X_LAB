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
function LineChart({ series, height = 200, yUnit = '', yMax, colors, labels, threshold }) {
  const wrapRef = useRef(null);
  const [w, setW] = useState(560);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((es) => setW(es[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);
  const padL = 38,padR = 12,padT = 12,padB = 22;
  const H = height,innerW = w - padL - padR,innerH = H - padT - padB;
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
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const v = max / ticks * i;
          return (
            <g key={i}>
              <line x1={padL} x2={w - padR} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" />
              <text x={padL - 7} y={y(v) + 3.5} textAnchor="end" fontSize="9.5" fill="var(--ink-4)" fontFamily="var(--mono)">{v % 1 === 0 ? v : v.toFixed(1)}</text>
            </g>);

        })}
        {labels && labels.map((l, i) =>
        <text key={i} x={x(i)} y={H - 6} textAnchor="middle" fontSize="9" fill="var(--ink-4)" fontFamily="var(--mono)">{l}</text>
        )}
        {threshold != null &&
        <line x1={padL} x2={w - padR} y1={y(threshold)} y2={y(threshold)} stroke="var(--bad)" strokeWidth="1.2" strokeDasharray="4 3" opacity="0.7" />
        }
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
        <text x={padL - 30} y={padT + 4} fontSize="9" fill="var(--ink-4)" fontFamily="var(--mono)" transform={`rotate(-90 ${padL - 30} ${padT + 4})`}></text>
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

Object.assign(window, { Sparkline, LineChart, BarChart, Donut, MiniMap, SegmentStrip });
