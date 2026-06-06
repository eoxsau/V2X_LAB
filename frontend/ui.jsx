/* ============================================================
   Shared UI — icons + primitives (exported to window)
   ============================================================ */
const { useState, useEffect, useRef, useMemo, useCallback } = React;

/* ---- Minimal line icons (stroke, currentColor) ------------- */
function Ic({ d, size = 16, fill, ...p }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill || 'none'}
      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
      {d}
    </svg>
  );
}
const Icon = {
  dash:    (p) => <Ic {...p} d={<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>} />,
  map:     (p) => <Ic {...p} d={<><path d="M9 3 4 5v16l5-2 6 2 5-2V3l-5 2-6-2Z"/><path d="M9 3v16M15 5v16"/></>} />,
  car:     (p) => <Ic {...p} d={<><path d="M5 13l1.5-4.5A2 2 0 0 1 8.4 7h7.2a2 2 0 0 1 1.9 1.5L19 13"/><path d="M4 13h16v4a1 1 0 0 1-1 1h-1.5a1 1 0 0 1-1-1v-1h-9v1a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4Z"/><circle cx="7.5" cy="15.5" r="0.6" fill="currentColor"/><circle cx="16.5" cy="15.5" r="0.6" fill="currentColor"/></>} />,
  antenna: (p) => <Ic {...p} d={<><path d="M12 12v8"/><path d="M9 20h6"/><circle cx="12" cy="9" r="1.6"/><path d="M7.5 13.5a6 6 0 0 1 0-9M16.5 4.5a6 6 0 0 1 0 9M9.7 11.3a3 3 0 0 1 0-4.6M14.3 6.7a3 3 0 0 1 0 4.6"/></>} />,
  route:   (p) => <Ic {...p} d={<><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h6a3 3 0 0 0 3-3V8"/><path d="M16 5h-6a3 3 0 0 0-3 3v8" strokeDasharray="0.1 3"/></>} />,
  chart:   (p) => <Ic {...p} d={<><path d="M4 19V5M4 19h16"/><path d="M8 16v-4M12 16V8M16 16v-6"/></>} />,
  sliders: (p) => <Ic {...p} d={<><path d="M4 7h10M18 7h2M4 12h4M12 12h8M4 17h12M18 17h2"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="17" r="2"/></>} />,
  play:    (p) => <Ic {...p} fill="currentColor" stroke="none" d={<path d="M7 5.5v13l11-6.5z"/>} />,
  pause:   (p) => <Ic {...p} fill="currentColor" stroke="none" d={<><rect x="6.5" y="5.5" width="3.5" height="13" rx="1"/><rect x="14" y="5.5" width="3.5" height="13" rx="1"/></>} />,
  reset:   (p) => <Ic {...p} d={<><path d="M4 12a8 8 0 1 1 2.5 5.8"/><path d="M4 20v-5h5"/></>} />,
  clock:   (p) => <Ic {...p} d={<><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/></>} />,
  speed:   (p) => <Ic {...p} d={<><path d="M12 14a5 5 0 0 1 9-3"/><path d="M3 12a9 9 0 0 1 18 0"/><path d="M12 14l3-2.5"/></>} />,
  net:     (p) => <Ic {...p} d={<><path d="M5 18h14"/><path d="M8 18v-3M12 18V9M16 18v-6"/><circle cx="8" cy="13" r="1.3"/><circle cx="12" cy="7" r="1.3"/><circle cx="16" cy="10" r="1.3"/></>} />,
  latency: (p) => <Ic {...p} d={<><path d="M3 12h4l2-5 4 12 2-5 2 2h4"/></>} />,
  download:(p) => <Ic {...p} d={<><path d="M12 4v10m0 0 4-4m-4 4-4-4"/><path d="M5 18h14"/></>} />,
  spark:   (p) => <Ic {...p} d={<><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/></>} />,
  check:   (p) => <Ic {...p} d={<path d="M5 13l4 4 10-11"/>} />,
  pin:     (p) => <Ic {...p} d={<><path d="M12 21s7-6.3 7-11a7 7 0 1 0-14 0c0 4.7 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/></>} />,
  flag:    (p) => <Ic {...p} d={<><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></>} />,
  warn:    (p) => <Ic {...p} d={<><path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17.5v.5"/></>} />,
  layers:  (p) => <Ic {...p} d={<><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/></>} />,
};

const NAV = [
  { id: 'dashboard',  ko: '대시보드',     en: 'Dashboard',  icon: 'dash' },
  { id: 'simulation', ko: '시뮬레이션',   en: 'Simulation', icon: 'map'  },
  { id: 'vehicles',   ko: '차량',         en: 'Vehicles',   icon: 'car'  },
  { id: 'network',    ko: '네트워크',     en: 'Network',    icon: 'antenna' },
  { id: 'routes',     ko: '경로',         en: 'Routes',     icon: 'route' },
  { id: 'analysis',   ko: '분석',         en: 'Analysis',   icon: 'chart' },
  { id: 'settings',   ko: '설정',         en: 'Settings',   icon: 'sliders' },
];

/* ---- Primitives -------------------------------------------- */
function Card({ title, en, right, children, className = '', style }) {
  return (
    <div className={'card ' + className} style={style}>
      {(title || right) && (
        <div className="card-h">
          <h3>{title}{en && <span className="en">{en}</span>}</h3>
          {right}
        </div>
      )}
      <div className="card-b">{children}</div>
    </div>
  );
}

function Chip({ tone, dot, children }) {
  return <span className={'chip ' + (tone || '')}>{dot && <span className="dot" />}{children}</span>;
}

function Toggle({ on, onChange, labels }) {
  return (
    <div className={'toggle ' + (on ? 'on' : '')} onClick={() => onChange(!on)}>
      <span className="track"><span className="knob" /></span>
      {labels && <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>{on ? labels[1] : labels[0]}</span>}
    </div>
  );
}

function Seg({ value, onChange, options }) {
  return (
    <div className="seg">
      {options.map(o => (
        <button key={o.v} className={value === o.v ? 'active' : ''} onClick={() => onChange(o.v)}>{o.label}</button>
      ))}
    </div>
  );
}

function Stat({ label, icon, value, unit, sub, accent }) {
  const I = icon && Icon[icon];
  return (
    <div className={'stat' + (accent ? ' accent' : '')}>
      <div className="label">{label}</div>
      {I && <div className="ic"><I size={20} /></div>}
      <div className="value">{value}{unit && <span className="unit">{unit}</span>}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

// congestion / status helpers
const levelTone = { low: 'good', mid: 'warn', high: 'bad' };
const levelKo = { low: '낮음', mid: '보통', high: '높음' };
const statusTone = { normal: 'good', busy: 'bad' };
const statusKo = { normal: '정상', busy: '혼잡' };
function latencyTone(ms) { return ms >= 20 ? 'bad' : ms >= 12 ? 'warn' : 'good'; }

function fmtClock(sec) {
  const h = String(Math.floor(sec / 3600)).padStart(2, '0');
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
  const s = String(Math.floor(sec % 60)).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

Object.assign(window, {
  Icon, NAV, Card, Chip, Toggle, Seg, Stat,
  levelTone, levelKo, statusTone, statusKo, latencyTone, fmtClock,
  useState, useEffect, useRef, useMemo, useCallback,
});
