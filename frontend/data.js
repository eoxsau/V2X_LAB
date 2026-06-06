/* ============================================================
   V2X Routing Lab — Mock data (window.DATA)
   Coordinates centred on Gangnam, Seoul (~37.501, 127.032)
   ============================================================ */
window.DATA = (function () {
  const CENTER = [37.5013, 127.0325];

  // Base stations -----------------------------------------------------------
  const baseStations = [
    { id: 'BS-01', lat: 37.5010, lng: 127.0300, height: 30, vehicles: 47,  capacity: 500, rho: 9.4,  lqueue: 2.1, status: 'normal' },
    { id: 'BS-02', lat: 37.5025, lng: 127.0330, height: 25, vehicles: 312, capacity: 500, rho: 62.4, lqueue: 8.7, status: 'busy'   },
    { id: 'BS-03', lat: 37.5005, lng: 127.0355, height: 35, vehicles: 89,  capacity: 500, rho: 17.8, lqueue: 3.2, status: 'normal' },
  ];

  // Vehicle routes (poly-lines roughly following the Gangnam grid) ----------
  const routes = {
    veh_001: [[37.5013,127.0312],[37.5018,127.0322],[37.5024,127.0333],[37.5030,127.0341],[37.5035,127.0342]],
    veh_002: [[37.4994,127.0290],[37.5001,127.0298],[37.5009,127.0309],[37.5016,127.0320],[37.5021,127.0331]],
    veh_003: [[37.5036,127.0345],[37.5032,127.0352],[37.5025,127.0357],[37.5017,127.0360]],
    veh_004: [[37.5026,127.0298],[37.5022,127.0307],[37.5016,127.0316],[37.5009,127.0324],[37.5002,127.0331]],
    veh_005: [[37.4986,127.0322],[37.4992,127.0329],[37.4999,127.0336],[37.5007,127.0344],[37.5014,127.0351]],
  };

  const vehicles = [
    { id: 'veh_001', lat: 37.5012, lng: 127.0318, speed: 38.4, path: ['E12','E15','E18'], edge: 'edge_E12', mode: '5G', latency: 7.1,  bs: 'BS-01', state: 'moving', progress: 0.30 },
    { id: 'veh_002', lat: 37.4998, lng: 127.0295, speed: 29.1, path: ['E07','E09','E12'], edge: 'edge_E09', mode: '5G', latency: 12.4, bs: 'BS-02', state: 'moving', progress: 0.42 },
    { id: 'veh_003', lat: 37.5034, lng: 127.0341, speed: 0.0,  path: ['E20','E22','E25'], edge: 'edge_E20', mode: '5G', latency: 6.8,  bs: 'BS-03', state: 'stopped', progress: 0.05 },
    { id: 'veh_004', lat: 37.5021, lng: 127.0307, speed: 45.2, path: ['E03','E07','E09'], edge: 'edge_E07', mode: '5G', latency: 9.3,  bs: 'BS-01', state: 'moving', progress: 0.20 },
    { id: 'veh_005', lat: 37.4989, lng: 127.0328, speed: 22.7, path: ['E15','E18','E21'], edge: 'edge_E15', mode: '5G', latency: 15.6, bs: 'BS-02', state: 'moving', progress: 0.15 },
  ];

  // Edge congestion ---------------------------------------------------------
  const edges = [
    { id: 'edge_E07', name: '도산대로 북측',     nameEn: 'Dosan-daero N',  vehicles: 8,  speed: 43.2, level: 'low'  },
    { id: 'edge_E09', name: '학동로 교차구간',   nameEn: 'Hakdong-ro Jct', vehicles: 23, speed: 18.7, level: 'mid'  },
    { id: 'edge_E12', name: '테헤란로 동측',     nameEn: 'Teheran-ro E',   vehicles: 41, speed: 9.1,  level: 'high' },
    { id: 'edge_E15', name: '강남대로 남측',     nameEn: 'Gangnam-daero S', vehicles: 5,  speed: 51.4, level: 'low'  },
    { id: 'edge_E18', name: '선릉로 서측',       nameEn: 'Seolleung-ro W', vehicles: 17, speed: 28.3, level: 'mid'  },
  ];

  // Route comparison (Dijkstra vs RL) — minimap geometry --------------------
  const routeCompare = {
    rows: [
      { metric: '총 이동 거리',  en: 'Distance',     dij: '3.42 km',        rl: '3.61 km',        better: 'dij' },
      { metric: '예상 소요 시간', en: 'ETA',          dij: '6분 12초',        rl: '5분 48초',        better: 'rl' },
      { metric: '평균 Latency',  en: 'Avg latency',  dij: '14.3 ms',        rl: '7.2 ms',         better: 'rl' },
      { metric: '최대 Latency',  en: 'Peak latency', dij: '31.7 ms',        rl: '11.4 ms',        better: 'rl' },
      { metric: '통신 위험 구간', en: 'Risk segments', dij: '2개',            rl: '0개',            better: 'rl' },
      { metric: '경유 기지국 수', en: 'BS traversed',  dij: '2개',            rl: '3개',            better: 'rl' },
      { metric: '경로 엣지',     en: 'Edge path',    dij: 'E07→E09→E12→E15', rl: 'E03→E07→E15→E18→E21', better: '-' },
    ],
    dijPath: [[37.4994,127.0292],[37.5002,127.0305],[37.5010,127.0320],[37.5018,127.0335],[37.5024,127.0348]],
    rlPath:  [[37.4994,127.0292],[37.5005,127.0300],[37.5013,127.0316],[37.5008,127.0334],[37.5016,127.0345],[37.5024,127.0352]],
    dijRisk: [[37.5018,127.0335],[37.5010,127.0320]],
  };

  // Simulation log ----------------------------------------------------------
  const logs = [
    { t: '00:00:01', target: 'veh_001', kind: 'info',  ko: '시뮬레이션 시작. 출발지 edge_E12 진입.' },
    { t: '00:00:15', target: 'veh_002', kind: 'handover', ko: 'BS-02 핸드오버 발생 (BS-01 → BS-02). Latency 6.1ms → 9.4ms.' },
    { t: '00:00:43', target: 'veh_003', kind: 'warn',  ko: 'edge_E20 정체 감지. 평균 속도 8.3 km/h.' },
    { t: '00:01:12', target: 'veh_001', kind: 'risk',  ko: '통신 위험 구간 진입 (edge_E12). Latency 28.3ms 초과.' },
    { t: '00:02:07', target: 'veh_004', kind: 'done',  ko: '목적지 도착. 총 이동시간 2분 7초.' },
    { t: '00:03:21', target: 'veh_002', kind: 'reroute', ko: 'RL 경로 재계산. 추천 경로로 전환.' },
    { t: '00:03:42', target: '시스템',   kind: 'sys',   ko: '전체 평균 Latency: 8.2ms. 통신 위험 이벤트: 3건.' },
  ];

  const llmSummary = [
    '전체 시뮬레이션 3분 42초 동안 총 3건의 통신 위험 이벤트가 발생했습니다.',
    'BS-02 기지국의 점유율이 62.4%로 가장 높아 혼잡 상태이며, 이 구간 차량의 Latency가 평균 대비 2.1배 높습니다.',
    'RL 추천 경로를 적용한 차량은 Dijkstra 경로 대비 평균 Latency가 49.7% 감소했습니다.',
    'edge_E12 구간은 차량 밀도가 가장 높고 통신 품질이 가장 낮아 인프라 보강이 권장됩니다.',
  ];

  // Settings parameters -----------------------------------------------------
  const params = [
    { name: '네트워크 모드',        v: 'tech',       def: '5G NR', unit: '',   note: '4G LTE / 5G NR / 6G-like', type: 'tech' },
    { name: 'L_base',             v: 'L_base',     def: '1.0',   unit: 'ms', note: '기술별 최솟값: 4G=10, 5G=1, 6G=0.1' },
    { name: '송신 출력 P_tx',      v: 'P_tx',       def: '46',    unit: 'dBm',note: '기지국 송신 출력' },
    { name: '경로 손실 지수 β',     v: 'beta',       def: '3.0',   unit: '',   note: '4G=3.5, 5G=3.0, 6G=2.5' },
    { name: '유효 경로 손실 α',     v: 'alpha',      def: '110',   unit: 'dB', note: '4G=120, 5G=110, 6G=100' },
    { name: '최대 재전송 N_max',    v: 'N_max',      def: '4',     unit: '',   note: 'HARQ 상한' },
    { name: '재전송 소요 T_retx',   v: 'T_retx',     def: '1.0',   unit: 'ms', note: '4G=8, 5G=1, 6G=0.1' },
    { name: '기지국 용량 C_tech',   v: 'C_tech',     def: '500',   unit: '',   note: '4G=100, 5G=500, 6G=2000' },
    { name: '비용함수 가중치 α',    v: 'alpha_cost', def: '0.01',  unit: '',   note: 'cost = t × (1 + α × L_total)' },
    { name: '핸드오버 히스테리시스', v: 'hysteresis', def: '5',     unit: 'ms', note: '핑퐁 방지' },
    { name: '핸드오버 TTT',        v: 'TTT',        def: '3',     unit: 'step',note: '3GPP A3 이벤트' },
    { name: '시뮬레이션 스텝 간격',  v: 'step',       def: '1.0',   unit: 's',  note: 'TraCI 제어 주기' },
    { name: 'WebSocket 전송 주기',  v: 'ws_interval',def: '500',   unit: 'ms', note: '프론트엔드 업데이트 주기' },
  ];

  // Workflow steps ----------------------------------------------------------
  const workflow = [
    { n: '①', ko: '구역 선택',       en: 'Select area',    desc: '지도 드래그로 bbox 선택' },
    { n: '②', ko: '기지국 설정',     en: 'Place base stn', desc: '클릭으로 위치 + 높이 설정' },
    { n: '③', ko: '경로 지정',       en: 'Set route',      desc: '출발지 / 도착지 마커 지정' },
    { n: '④', ko: '시뮬레이션 실행', en: 'Run sim',        desc: '시작 → 실시간 차량 이동' },
    { n: '⑤', ko: '결과 분석',       en: 'Analyse',        desc: 'Analysis 탭에서 로그·Export' },
  ];

  // Pre-baked time series for charts (latency over time, ms) ---------------
  const series = {
    latencyAvg: [9.1,8.4,9.8,12.2,10.5,8.9,7.6,8.2,9.4,11.1,8.7,7.9,8.2],
    networkLoad: [22,28,31,44,52,48,39,34,41,55,47,38,34],
    perVeh: {
      veh_001: { speed: [36,38,40,37,12,28,38,41,39,38], lat: [6.8,7.1,9.2,14.0,28.3,18.1,9.4,7.2,7.0,7.1] },
      veh_002: { speed: [30,29,25,22,28,31,29,27,29,29], lat: [6.1,9.4,11.2,12.4,12.0,10.8,9.9,12.4,11.6,12.4] },
      veh_003: { speed: [12,8,4,0,0,0,2,6,3,0],          lat: [6.8,6.5,6.9,6.8,7.0,6.7,6.8,6.6,6.8,6.8] },
      veh_004: { speed: [42,44,46,45,43,47,45,44,46,45], lat: [8.9,9.3,9.0,9.6,9.1,8.8,9.3,9.5,9.2,9.3] },
      veh_005: { speed: [20,22,24,23,21,22,24,23,22,23], lat: [14.0,15.2,15.6,16.1,15.0,14.8,15.6,15.9,15.3,15.6] },
    },
  };

  return { CENTER, baseStations, routes, vehicles, edges, routeCompare, logs, llmSummary, params, workflow, series };
})();
