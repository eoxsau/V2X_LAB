import type {
  AssistantRecommendation,
  BaseStation,
  CongestionZone,
  EdgeNode,
  ObstacleZone,
  RoutePath,
  Vehicle
} from "./map-types";

export const baseStations: BaseStation[] = [
  {
    id: "BS-01",
    name: "City Hall RSU",
    latitude: 37.5669,
    longitude: 126.9782,
    frequency: 3500,
    tx_power: 43,
    capacity: 1200,
    current_connected_vehicles: 18,
    congestion_score: 44,
    source: "synthetic"
  },
  {
    id: "BS-02",
    name: "Euljiro Edge Cell",
    latitude: 37.5661,
    longitude: 126.9912,
    frequency: 3500,
    tx_power: 41,
    capacity: 980,
    current_connected_vehicles: 34,
    congestion_score: 68,
    source: "synthetic"
  },
  {
    id: "BS-03",
    name: "Namdaemun Relay",
    latitude: 37.5598,
    longitude: 126.9771,
    frequency: 2800,
    tx_power: 39,
    capacity: 850,
    current_connected_vehicles: 41,
    congestion_score: 82,
    source: "synthetic"
  },
  {
    id: "BS-04",
    name: "Gwanghwamun Node",
    latitude: 37.5759,
    longitude: 126.9768,
    frequency: null,
    tx_power: null,
    capacity: 1350,
    current_connected_vehicles: 14,
    congestion_score: 37,
    source: "synthetic"
  },
  {
    id: "BS-05",
    name: "Jongno RSU",
    latitude: 37.5702,
    longitude: 126.9831,
    frequency: 3500,
    tx_power: 40,
    capacity: 1040,
    current_connected_vehicles: 29,
    congestion_score: 55,
    source: "synthetic"
  },
  {
    id: "BS-06",
    name: "Seoul Station V2X Cell",
    latitude: 37.5547,
    longitude: 126.9706,
    frequency: 2800,
    tx_power: 42,
    capacity: 1180,
    current_connected_vehicles: 22,
    congestion_score: 48,
    source: "synthetic"
  },
  {
    id: "BS-07",
    name: "Myeongdong RSU",
    latitude: 37.5636,
    longitude: 126.9858,
    frequency: 3500,
    tx_power: 39,
    capacity: 920,
    current_connected_vehicles: 38,
    congestion_score: 73,
    source: "synthetic"
  },
  {
    id: "BS-08",
    name: "Chungmuro Relay",
    latitude: 37.5611,
    longitude: 126.9943,
    frequency: 2800,
    tx_power: 38,
    capacity: 760,
    current_connected_vehicles: 31,
    congestion_score: 64,
    source: "synthetic"
  },
  {
    id: "BS-09",
    name: "Anguk Research Cell",
    latitude: 37.5768,
    longitude: 126.9872,
    frequency: null,
    tx_power: 37,
    capacity: 700,
    current_connected_vehicles: 12,
    congestion_score: 34,
    source: "synthetic"
  },
  {
    id: "BS-10",
    name: "Sogong Edge RSU",
    latitude: 37.5639,
    longitude: 126.9738,
    frequency: 3500,
    tx_power: null,
    capacity: 880,
    current_connected_vehicles: 26,
    congestion_score: 51,
    source: "synthetic"
  },
  {
    id: "BS-11",
    name: "Dongdaemun Corridor Cell",
    latitude: 37.5708,
    longitude: 127.0095,
    frequency: 3500,
    tx_power: 44,
    capacity: 1240,
    current_connected_vehicles: 45,
    congestion_score: 79,
    source: "synthetic"
  },
  {
    id: "BS-12",
    name: "Mapo Gateway RSU",
    latitude: 37.5569,
    longitude: 126.9455,
    frequency: 2800,
    tx_power: 41,
    capacity: 970,
    current_connected_vehicles: 17,
    congestion_score: 43,
    source: "synthetic"
  }
];

export const vehicles: Vehicle[] = [
  {
    id: "AV-104",
    name: "Autonomous Shuttle 104",
    speed: 42,
    heading: 38,
    current_route_id: "route-c",
    connected_base_station_id: "BS-05",
    connected_base_station_name: "Jongno RSU",
    connected_edge_node_id: "EDGE-B",
    connected_edge_node_name: "Myeongdong Edge",
    distance_to_connected_base_station_km: 0.31,
    base_station_congestion_score: 41.8,
    current_latency_ms: 29.4,
    obstacle_risk: 0.67,
    network_blockage_risk: 0.32,
    route_status: "following_ai_route",
    nearby_base_stations: [
      { id: "BS-05", name: "Jongno RSU", distance_km: 0.31, congestion_score: 41.8, baseline_score: 68.9 },
      { id: "BS-01", name: "City Hall RSU", distance_km: 0.42, congestion_score: 33.4, baseline_score: 66.1 },
      { id: "BS-07", name: "Myeongdong RSU", distance_km: 0.49, congestion_score: 55.7, baseline_score: 56.9 }
    ],
    nearby_obstacles: [
      { id: "OBS-1", type: "building_zone", distance_km: 0.22, severity: 0.74, proximity_score: 0.43 },
      { id: "OBS-5", type: "network_blockage_zone", distance_km: 0.34, severity: 0.82, proximity_score: 0.31 }
    ],
    blocking_obstacles: [
      { id: "OBS-5", type: "network_blockage_zone", path_distance_km: 0.06, severity: 0.82, blockage_score: 0.48 }
    ],
    latitude: 37.5658,
    longitude: 126.9821
  },
  {
    id: "AV-221",
    name: "Autonomous Taxi 221",
    speed: 36,
    heading: 54,
    current_route_id: "route-a",
    connected_base_station_id: "BS-10",
    connected_base_station_name: "Sogong Edge RSU",
    connected_edge_node_id: "EDGE-A",
    connected_edge_node_name: "City Hall Edge",
    distance_to_connected_base_station_km: 0.36,
    base_station_congestion_score: 39.0,
    current_latency_ms: 30.8,
    obstacle_risk: 0.41,
    network_blockage_risk: 0.18,
    route_status: "evaluating",
    nearby_base_stations: [
      { id: "BS-10", name: "Sogong Edge RSU", distance_km: 0.36, congestion_score: 39.0, baseline_score: 67.5 },
      { id: "BS-03", name: "Namdaemun Relay", distance_km: 0.58, congestion_score: 63.4, baseline_score: 50.5 },
      { id: "BS-06", name: "Seoul Station V2X Cell", distance_km: 0.84, congestion_score: 36.2, baseline_score: 49.2 }
    ],
    nearby_obstacles: [
      { id: "OBS-4", type: "low_visibility_zone", distance_km: 0.27, severity: 0.49, proximity_score: 0.51 }
    ],
    blocking_obstacles: [],
    latitude: 37.5615,
    longitude: 126.9762
  },
  {
    id: "AV-309",
    name: "Autonomous Delivery 309",
    speed: 51,
    heading: 124,
    current_route_id: "route-b",
    connected_base_station_id: "BS-04",
    connected_base_station_name: "Gwanghwamun Node",
    connected_edge_node_id: "EDGE-A",
    connected_edge_node_name: "City Hall Edge",
    distance_to_connected_base_station_km: 0.33,
    base_station_congestion_score: 28.0,
    current_latency_ms: 24.2,
    obstacle_risk: 0.24,
    network_blockage_risk: 0.08,
    route_status: "rule_baseline",
    nearby_base_stations: [
      { id: "BS-04", name: "Gwanghwamun Node", distance_km: 0.33, congestion_score: 28.0, baseline_score: 76.5 },
      { id: "BS-09", name: "Anguk Research Cell", distance_km: 0.71, congestion_score: 25.8, baseline_score: 67.5 },
      { id: "BS-05", name: "Jongno RSU", distance_km: 0.82, congestion_score: 41.8, baseline_score: 54.1 }
    ],
    nearby_obstacles: [
      { id: "OBS-2", type: "accident_zone", distance_km: 0.42, severity: 0.58, proximity_score: 0.22 }
    ],
    blocking_obstacles: [],
    latitude: 37.5732,
    longitude: 126.9796
  },
  {
    id: "AV-417",
    name: "Autonomous Fleet 417",
    speed: 47,
    heading: 72,
    current_route_id: "route-c",
    connected_base_station_id: "BS-02",
    connected_base_station_name: "Euljiro Edge Cell",
    connected_edge_node_id: "EDGE-C",
    connected_edge_node_name: "Jongno Research Edge",
    distance_to_connected_base_station_km: 0.36,
    base_station_congestion_score: 52.7,
    current_latency_ms: 31.5,
    obstacle_risk: 0.32,
    network_blockage_risk: 0.26,
    route_status: "following_ai_route",
    nearby_base_stations: [
      { id: "BS-02", name: "Euljiro Edge Cell", distance_km: 0.36, congestion_score: 52.7, baseline_score: 58.1 },
      { id: "BS-07", name: "Myeongdong RSU", distance_km: 0.64, congestion_score: 55.7, baseline_score: 50.4 },
      { id: "BS-05", name: "Jongno RSU", distance_km: 0.71, congestion_score: 41.8, baseline_score: 50.1 }
    ],
    nearby_obstacles: [
      { id: "OBS-3", type: "construction_zone", distance_km: 0.21, severity: 0.63, proximity_score: 0.58 }
    ],
    blocking_obstacles: [
      { id: "OBS-3", type: "construction_zone", path_distance_km: 0.04, severity: 0.63, blockage_score: 0.28 }
    ],
    latitude: 37.5689,
    longitude: 126.9878
  }
];

export const edgeNodes: EdgeNode[] = [
  { id: "EDGE-A", name: "City Hall Edge", capacity: 420, compute_load: 0.42, edge_latency_ms: 11.5, latitude: 37.5707, longitude: 126.9814, source: "synthetic" },
  { id: "EDGE-B", name: "Myeongdong Edge", capacity: 360, compute_load: 0.51, edge_latency_ms: 13.2, latitude: 37.5619, longitude: 126.9875, source: "synthetic" },
  { id: "EDGE-C", name: "Jongno Research Edge", capacity: 300, compute_load: 0.36, edge_latency_ms: 14.6, latitude: 37.5738, longitude: 126.9897, source: "synthetic" }
];

export const obstacles: ObstacleZone[] = [
  {
    id: "OBS-1",
    type: "building_zone",
    latitude: 37.5644,
    longitude: 126.9844,
    radius_m: 54,
    severity: 0.74,
    source: "synthetic",
    affected_vehicle_ids: ["AV-104"]
  },
  {
    id: "OBS-2",
    type: "accident_zone",
    latitude: 37.5711,
    longitude: 126.9778,
    radius_m: 42,
    severity: 0.58,
    source: "synthetic",
    affected_vehicle_ids: ["AV-309"]
  },
  {
    id: "OBS-3",
    type: "construction_zone",
    latitude: 37.5667,
    longitude: 126.9894,
    radius_m: 68,
    severity: 0.63,
    source: "synthetic",
    affected_vehicle_ids: ["AV-417"]
  },
  {
    id: "OBS-4",
    type: "low_visibility_zone",
    latitude: 37.5597,
    longitude: 126.9748,
    radius_m: 78,
    severity: 0.49,
    source: "synthetic",
    affected_vehicle_ids: ["AV-221"]
  },
  {
    id: "OBS-5",
    type: "network_blockage_zone",
    latitude: 37.5685,
    longitude: 126.9828,
    radius_m: 62,
    severity: 0.82,
    source: "synthetic",
    affected_vehicle_ids: ["AV-104"]
  }
];

export const currentRoute: RoutePath = {
  id: "route-current",
  label: "Current route",
  qualityScore: 72,
  totalScore: 38.9,
  scoreBreakdown: {
    travelTime: 22.7,
    roadCongestion: 17.3,
    predictedNetworkLatency: 20.6,
    baseStationCongestion: 8.4,
    obstacleRisk: 20.1,
    edgeLatency: 9.9
  },
  points: [
    [37.5752, 126.9734],
    [37.5714, 126.9781],
    [37.5667, 126.9828],
    [37.5621, 126.9862]
  ],
  riskySegments: [
    [
      [37.5714, 126.9781],
      [37.5667, 126.9828]
    ]
  ]
};

export const aiRecommendedRoute: RoutePath = {
  id: "route-ai",
  label: "AI-ready route candidate",
  qualityScore: 86,
  totalScore: 19.5,
  scoreBreakdown: {
    travelTime: 24.2,
    roadCongestion: 11.5,
    predictedNetworkLatency: 13.8,
    baseStationCongestion: 7.8,
    obstacleRisk: 9.6,
    edgeLatency: 9.2
  },
  points: [
    [37.5591, 126.9737],
    [37.5631, 126.978],
    [37.5674, 126.9842],
    [37.5704, 126.991]
  ],
  riskySegments: [
    [
      [37.5631, 126.978],
      [37.5674, 126.9842]
    ]
  ]
};

export const shortestRoute: RoutePath = {
  id: "route-shortest",
  label: "Shortest route baseline",
  qualityScore: 69,
  totalScore: 42.7,
  scoreBreakdown: {
    travelTime: 21.6,
    roadCongestion: 17.3,
    predictedNetworkLatency: 24.5,
    baseStationCongestion: 8.4,
    obstacleRisk: 20.1,
    edgeLatency: 9.9
  },
  points: [
    [37.5752, 126.9734],
    [37.5714, 126.9781],
    [37.5667, 126.9828],
    [37.5621, 126.9862]
  ],
  riskySegments: [
    [
      [37.5714, 126.9781],
      [37.5667, 126.9828]
    ]
  ]
};

export const trafficAwareRoute: RoutePath = {
  id: "route-traffic-aware",
  label: "Traffic-aware route",
  qualityScore: 78,
  totalScore: 30.6,
  scoreBreakdown: {
    travelTime: 23.5,
    roadCongestion: 9.4,
    predictedNetworkLatency: 22.3,
    baseStationCongestion: 7.4,
    obstacleRisk: 6.6,
    edgeLatency: 8.6
  },
  points: [
    [37.5584, 126.9722],
    [37.5623, 126.9768],
    [37.5663, 126.9814],
    [37.5704, 126.9866]
  ],
  riskySegments: []
};

export const baselineRoute = shortestRoute;

export const routeOptimizationSummary = {
  selectedVehicleId: "AV-104",
  shortestRoute,
  trafficAwareRoute,
  aiRecommendedRoute,
  explanation:
    "The deterministic network-aware route candidate chooses route C because its expected latency, base-station congestion, obstacle exposure, and edge latency are lower than the shortest-distance baseline.",
  scoreDirection: "Lower total score is better; quality score is normalized for display."
};

export const assistantRecommendation: AssistantRecommendation = {
  assistantRecommendation:
    "Vehicle AV-104 should switch to the AI-ready route candidate. The shortest route is more direct, but its risky central segment increases obstacle exposure and expected V2X latency.",
  mainCause:
    "Obstacle risk caused rerouting: the shortest route passes near OBS-5, which raises network blockage risk on the vehicle-to-base-station path.",
  expectedImprovement:
    "Expected improvement: route quality increases from 69 to 86, predicted network latency score improves by 10.7 points, and obstacle risk score improves by 10.5 points.",
  recommendedAction:
    "Apply the AI-ready route candidate for AV-104, keep monitoring BS-07 and OBS-5, and re-evaluate if base-station congestion rises above 75%.",
  confidence: {
    label: "medium-high",
    value: 0.81
  },
  answers: {
    whyThisRouteIsRecommended:
      "The AI-ready route candidate balances slightly longer travel time against lower V2X latency, lower obstacle exposure, and a steadier edge-node path.",
    overloadedBaseStation:
      "BS-07 is the highest nearby load for AV-104 at roughly 56%. It is not critically overloaded, but it is the station to monitor.",
    obstacleRiskStatus:
      "Obstacle risk is high for the current/shortest route because AV-104 is near building and network blockage zones.",
    dominantFactor:
      "Network latency and obstacle blockage are more important than road congestion for this recommendation."
  }
};

export const congestionZones: CongestionZone[] = [
  {
    id: "cg-road-1",
    intensity: 0.72,
    label: "Road congestion",
    points: [
      [37.5644, 126.9792],
      [37.5668, 126.9818],
      [37.5657, 126.9856],
      [37.5628, 126.983]
    ]
  },
  {
    id: "cg-network-1",
    intensity: 0.58,
    label: "Network congestion",
    points: [
      [37.5688, 126.986],
      [37.5707, 126.9892],
      [37.5681, 126.992],
      [37.5661, 126.9887]
    ]
  }
];
