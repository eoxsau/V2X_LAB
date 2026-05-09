export type LatLng = [number, number];

export type SelectedMapPoint = {
  lat: number;
  lng: number;
  label?: string;
};

export type ProjectedPoint = {
  x: number;
  y: number;
};

export type MapViewport = {
  center: LatLng;
  height: number;
  width: number;
  zoom: number;
};

export type BaseStation = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  frequency: number | null;
  tx_power: number | null;
  capacity: number;
  current_connected_vehicles: number;
  congestion_score: number;
  source: "mock" | "public_api" | "synthetic";
};

export type NetworkNode = {
  id: string;
  name: string;
  node_type: "base_station" | "roadside_unit" | "edge_node" | string;
  latitude: number;
  longitude: number;
  capacity: number;
  current_connected_vehicles: number;
  congestion_score: number;
  edge_latency_ms: number;
  coverage_radius_m: number;
  source: "mock" | "public_api" | "synthetic" | string;
};

export type NetworkNodeEstimate = {
  id: string;
  name: string;
  node_type: string;
  distance_km: number;
  load: number;
  estimated_latency_ms: number;
  baseline_score: number;
  recommendation_status: string;
};

export type Vehicle = {
  id: string;
  name?: string;
  vehicle_type?: "ego_vehicle" | "surrounding_vehicle" | "emergency_vehicle" | "heavy_vehicle" | string;
  speed: number;
  heading?: number;
  current_route_id: string;
  connected_base_station_id: string;
  connected_base_station_name?: string;
  connected_edge_node_id?: string;
  connected_edge_node_name?: string;
  connected_network_node_id?: string;
  connected_network_node_name?: string;
  connected_network_node_type?: string;
  distance_to_connected_base_station_km: number;
  distance_to_connected_node?: number;
  distance_to_nearest_node?: number;
  base_station_congestion_score: number;
  network_node_congestion_score?: number;
  current_latency_ms: number;
  nearest_network_nodes?: NetworkNodeEstimate[];
  alternative_node_latency_estimates?: NetworkNodeEstimate[];
  network_node_recommendation_status?: string;
  obstacle_risk: number;
  network_blockage_risk?: number;
  nearest_vehicle_distance?: number | null;
  collision_proximity_risk?: number;
  network_stability_score?: number;
  distance_to_ego?: number;
  relative_speed?: number;
  heading_difference?: number;
  direction_relation?: "same_direction" | "opposite_direction" | "crossing" | string;
  proximity_risk?: number;
  risk_level?: "low" | "medium" | "high" | string;
  route_status: "following_ai_route" | "evaluating" | "rule_baseline" | "moving_mock_simulation" | "moving_sumo_traci";
  nearby_base_stations: Array<{
    id: string;
    name: string;
    distance_km: number;
    congestion_score: number;
    baseline_score: number;
  }>;
  nearby_obstacles?: Array<{
    id: string;
    type: ObstacleType;
    distance_km: number;
    severity: number;
    proximity_score: number;
  }>;
  blocking_obstacles?: Array<{
    id: string;
    type: ObstacleType;
    path_distance_km: number;
    severity: number;
    blockage_score: number;
  }>;
  latitude: number;
  longitude: number;
};

export type EdgeNode = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  capacity: number;
  compute_load: number;
  edge_latency_ms: number;
  source: "mock" | "public_api" | "synthetic";
};

export type ObstacleType =
  | "building_zone"
  | "accident_zone"
  | "construction_zone"
  | "low_visibility_zone"
  | "network_blockage_zone";

export type ObstacleZone = {
  id: string;
  type: ObstacleType;
  latitude: number;
  longitude: number;
  radius_m: number;
  severity: number;
  source: "mock" | "public_api" | "synthetic";
  affected_vehicle_ids?: string[];
};

export type RoutePath = {
  id: string;
  label: string;
  points: LatLng[];
  qualityScore?: number;
  totalScore?: number;
  scoreBreakdown?: RouteScoreBreakdown;
  riskySegments?: LatLng[][];
};

export type RouteScoreBreakdown = {
  travelTime: number;
  roadCongestion: number;
  predictedNetworkLatency: number;
  baseStationCongestion: number;
  obstacleRisk: number;
  edgeLatency: number;
};

export type AssistantRecommendation = {
  assistantRecommendation: string;
  mainCause: string;
  expectedImprovement: string;
  recommendedAction: string;
  confidence: {
    label: string;
    value: number;
  };
  answers: {
    whyThisRouteIsRecommended: string;
    overloadedBaseStation: string;
    obstacleRiskStatus: string;
    dominantFactor: string;
  };
};

export type SimulationMetrics = {
  avg_vehicle_latency: number;
  avg_predicted_latency: number;
  avg_road_congestion: number;
  avg_base_station_congestion: number;
  avg_obstacle_risk: number;
  current_latency_ms?: number;
  road_congestion_score?: number;
  network_congestion_score?: number;
  route_score?: number;
  route_change_count: number;
  base_station_handoff_count: number;
};

export type RoadSegment = {
  id: string;
  name: string;
  geometry: LatLng[];
  average_speed: number;
  congestion_score: number;
  travel_time_estimate: number;
  obstacle_risk: number;
  source?: "its_api" | "sumo" | "synthetic" | string;
  raw_payload?: unknown;
};

export type RealtimeRoute = {
  id: string;
  name: string;
  geometry: LatLng[];
  role?: "current_route" | "shortest_route_baseline" | "traffic_aware_route" | "network_aware_ai_route";
  total_score?: number;
  quality_score?: number;
  score_breakdown?: {
    travel_time_score: number;
    road_congestion_score: number;
    predicted_network_latency_score: number;
    base_station_congestion_score: number;
    obstacle_risk_score: number;
    edge_latency_score: number;
    total_score: number;
    quality_score: number;
  };
  risky_segments?: LatLng[][];
};

export type SimulationStreamPayload = {
  simulation_id: string;
  simulator_mode: string;
  running: boolean;
  tick: number;
  updated_at: string;
  vehicles: Vehicle[];
  ego_vehicle?: Vehicle | null;
  surrounding_vehicles?: Vehicle[];
  vehicle_relationships?: VehicleRelationship[];
  roads: RoadSegment[];
  road_segments?: RoadSegment[];
  base_stations: BaseStation[];
  edge_nodes: EdgeNode[];
  network_nodes?: NetworkNode[];
  obstacles: ObstacleZone[];
  current_routes: RealtimeRoute[];
  recommended_routes: RealtimeRoute[];
  connection_lines: Array<{
    id: string;
    vehicle_id: string;
    base_station_id: string;
    latency_ms: number;
    points: LatLng[];
  }>;
  route_geometry?: Array<{ lat: number; lng: number }>;
  ai_analysis: unknown;
  ai_ready?: {
    status: string;
    features: Array<Record<string, string | number | null>>;
    latency_predictions: Array<Record<string, string | number>>;
    congestion_predictions: Array<Record<string, string | number>>;
    blockage_predictions: Array<Record<string, string | number>>;
    model_registry: Record<string, Record<string, string | boolean>>;
    note: string;
  };
  rl?: {
    status: string;
    state: Record<string, unknown>;
    action_candidates: Array<Record<string, unknown>>;
    reward: {
      formula: string;
      components: Record<string, number>;
      reward: number;
    };
    episode_logs: Array<{
      timestamp: string;
      event: string;
      payload: Record<string, unknown>;
    }>;
    policy_comparison: Record<string, unknown>;
    note: string;
  };
  metrics: SimulationMetrics;
  assistant_messages: Array<{
    role: string;
    title: string;
    content: string | { label: string; value: number; reason?: string };
  }>;
  llm_status?: {
    enabled: boolean;
    configured_provider: string;
    active_provider: string;
    fallback_active: boolean;
    keys: Record<string, "configured" | "missing" | string>;
    policy: string;
  };
  event_logs: string[];
  traffic?: {
    source: "its_api" | "sumo" | "synthetic" | string;
    status: "connected" | "fallback" | string;
    last_sync_at: string | null;
  };
  simulator?: {
    mode: string;
    adapter: string;
    label: string;
    status: "sumo_connected" | "mock" | string;
    message?: string;
    sumo_enabled: boolean;
    sumo_gui_enabled: boolean;
    using_mock_fallback: boolean;
  };
  debug?: {
    selected_origin_coordinate?: SelectedMapPoint;
    selected_destination_coordinate?: SelectedMapPoint;
    matched_sumo_origin_edge?: Record<string, unknown>;
    matched_sumo_destination_edge?: Record<string, unknown>;
    route_length_m?: number;
    coordinate_conversion_status?: string;
    sumo_connection_status?: string;
    its_api_status?: string;
    sumo_network_source?: string;
  } | null;
};

export type VehicleRelationship = {
  id: string;
  ego_vehicle_id: string;
  vehicle_id: string;
  distance_to_ego: number;
  relative_speed: number;
  heading_difference: number;
  direction_relation: "same_direction" | "opposite_direction" | "crossing" | string;
  proximity_risk: number;
  risk_level: "low" | "medium" | "high" | string;
  points: LatLng[];
};

export type CongestionZone = {
  id: string;
  intensity: number;
  label: string;
  points: LatLng[];
};
