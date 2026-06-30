/* ============================================================ Landing — Lite/Professional mode gate */

const MODE_CARDS = [
  {
    v: 'lite',
    title: 'Lite',
    en: 'For coursework & first runs',
    desc: '학부생 · 처음 V2X 시뮬레이션을 다뤄보는 분께 추천합니다.',
    bullets: [
      '핵심 시뮬레이션 실행 + 현황 요약 대시보드',
      '자연어로 설정을 바꾸는 시나리오 어시스턴트',
      '로그 + AI 자연어 분석이 담긴 분석 보고서',
      '네트워크 모드(4G/5G/6G) 선택',
    ],
  },
  {
    v: 'pro',
    title: 'Professional',
    en: 'For research & evaluation',
    desc: '스타트업 · 대학원 · 연구실 — 전체 알고리즘/실험 기능이 필요한 분께 추천합니다.',
    bullets: [
      '알고리즘 선택, 비용가중치, 커스텀 정책 등 전체 설정',
      '경로·알고리즘 비교, 시나리오 배치 실행/비교',
      '네트워크 상세 진단 + 미래 위험 예측',
      'ITS 실시간 교통(첨두/비첨두) 동기화 및 선택',
    ],
  },
];

function LandingPage({ onSelect }) {
  return (
    <div className="page-pad fade" style={{ minHeight: '100vh', display: 'flex', alignItems: 'center' }}>
      <div style={{ width: '100%', maxWidth: 920, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: 36 }}>
          <div className="eyebrow">Get started</div>
          <h1 style={{ fontSize: 26, margin: '6px 0 8px' }}>V2X AI Routing Lab</h1>
          <div className="sub" style={{ fontSize: 13.5 }}>
            시작하기 전에, 어떤 용도로 사용할지 선택해주세요. 설정 탭에서 언제든 바꿀 수 있습니다.
          </div>
        </div>

        <div className="row gap16 wrap" style={{ alignItems: 'stretch' }}>
          {MODE_CARDS.map(m => (
            <button
              key={m.v}
              onClick={() => onSelect(m.v)}
              style={{
                flex: '1 1 380px', textAlign: 'left', cursor: 'pointer',
                padding: '22px 24px', borderRadius: 16,
                background: 'var(--surface)', border: '1.5px solid var(--border)',
                transition: 'border-color .15s, transform .15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--brand-2)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              <div className="row between" style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 19, fontWeight: 700 }}>{m.title}</span>
                <span className="en" style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink-4)' }}>{m.en}</span>
              </div>
              <div className="muted" style={{ fontSize: 12, lineHeight: 1.7, marginBottom: 18 }}>{m.desc}</div>
              <ul style={{ margin: 0, padding: '0 0 0 18px', fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.7 }}>
                {m.bullets.map((b, i) => <li key={i} style={{ marginBottom: i < m.bullets.length - 1 ? 10 : 0 }}>{b}</li>)}
              </ul>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LandingPage });
