"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { VWorld2DMapView } from "@/components/map/VWorld2DMapView";
import {
  aiRecommendedRoute,
  assistantRecommendation,
  baseStations,
  congestionZones,
  currentRoute,
  edgeNodes,
  obstacles,
  routeOptimizationSummary,
  shortestRoute,
  trafficAwareRoute,
  vehicles
} from "@/components/map/mock-map-data";
import type { RealtimeRoute, RoutePath, SelectedMapPoint } from "@/components/map/map-types";
import { useSimulationStream } from "@/hooks/useSimulationStream";

type PageKind =
  | "dashboard"
  | "simulation"
  | "vehicles"
  | "map"
  | "network"
  | "routes"
  | "analysis"
  | "rl-lab"
  | "assistant"
  | "experiments"
  | "settings";

const navItems: Array<[PageKind, string, string]> = [
  ["dashboard", "Dashboard", "/dashboard"],
  ["simulation", "Simulation", "/simulation"],
  ["vehicles", "Vehicles", "/vehicles"],
  ["map", "Map", "/map"],
  ["network", "Network", "/network"],
  ["routes", "Routes", "/routes"],
  ["analysis", "Analysis", "/analysis"],
  ["rl-lab", "RL Lab", "/rl-lab"],
  ["assistant", "Assistant", "/assistant"],
  ["experiments", "Experiments", "/experiments"],
  ["settings", "Settings", "/settings"]
];

const pageMeta: Record<PageKind, { title: string; eyebrow: string; summary: string }> = {
  analysis: {
    eyebrow: "AI-ready analysis",
    summary: "Latency, congestion, obstacle, blockage, and route scoring modules prepared for model integration.",
    title: "Analysis Workbench"
  },
  assistant: {
    eyebrow: "LLM-ready assistant",
    summary: "Natural-language scenario setup and result explanation surface. No external LLM is connected yet.",
    title: "Assistant Console"
  },
  dashboard: {
    eyebrow: "Main command center",
    summary: "Operational overview for vehicles, roads, network infrastructure, route recommendations, and assistant alerts.",
    title: "Autonomous V2X AI Routing Lab"
  },
  experiments: {
    eyebrow: "Saved runs",
    summary: "Experiment registry for comparing simulations, scenarios, parameters, and outcomes.",
    title: "Experiments"
  },
  map: {
    eyebrow: "VWorld 2D spatial view",
    summary: "Full-screen public spatial map for vehicles, infrastructure, obstacles, congestion, and route overlays.",
    title: "Network-Aware Map"
  },
  network: {
    eyebrow: "Infrastructure operations",
    summary: "Base stations, roadside units, edge nodes, load, capacity, congestion, and latency.",
    title: "Network Infrastructure"
  },
  "rl-lab": {
    eyebrow: "Reinforcement learning preparation",
    summary: "State, action, reward, policy comparison, and episode logs. Training is not enabled yet.",
    title: "RL Lab"
  },
  routes: {
    eyebrow: "Route comparison",
    summary: "Compare shortest, traffic-aware, network-aware, and AI-ready recommended route candidates.",
    title: "Routes"
  },
  settings: {
    eyebrow: "Environment status",
    summary: "Configuration presence checks only. Raw API keys are never displayed.",
    title: "Settings"
  },
  simulation: {
    eyebrow: "Simulation controls",
    summary: "Start/stop mock or SUMO-ready simulation and inspect integration status.",
    title: "Simulation Control"
  },
  vehicles: {
    eyebrow: "Fleet monitoring",
    summary: "Ego vehicle and surrounding vehicle telemetry, route state, network node, latency, and risk.",
    title: "Vehicles"
  }
};

function Card({ children, className = "" }: Readonly<{ children: React.ReactNode; className?: string }>) {
  return <section className={`rounded-xl border border-[#1F2937] bg-[#111827] p-5 ${className}`}>{children}</section>;
}

function Stat({ label, value }: Readonly<{ label: string; value: string | number }>) {
  return (
    <div className="rounded-lg border border-[#1F2937] bg-[#050816] p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className="mt-2 font-mono text-lg text-cyan-100">{value}</p>
    </div>
  );
}

function StatusPill({ label, tone = "neutral" }: Readonly<{ label: string; tone?: "good" | "warn" | "neutral" }>) {
  const toneClass = {
    good: "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
    neutral: "border-slate-300/20 bg-slate-300/10 text-slate-200",
    warn: "border-amber-300/35 bg-amber-300/10 text-amber-100"
  }[tone];
  return <span className={`rounded-md border px-2.5 py-1 font-mono text-xs ${toneClass}`}>{label}</span>;
}

function trafficSourceLabel(source: string | undefined) {
  if (source === "its_api") {
    return "ITS API";
  }
  if (source === "sumo") {
    return "SUMO";
  }
  return "Synthetic";
}

function routeLabel(role: string | undefined, fallback: string) {
  if (role === "shortest_route_baseline") {
    return "Shortest Route";
  }
  if (role === "traffic_aware_route") {
    return "Traffic-Aware Route";
  }
  if (role === "network_aware_ai_route") {
    return "AI-Ready Network Route";
  }
  if (role === "current_route") {
    return "Current Route";
  }
  return fallback;
}

function toRoutePath(route: RealtimeRoute | undefined, fallback: RoutePath): RoutePath {
  if (!route) {
    return fallback;
  }
  return {
    id: route.id,
    label: routeLabel(route.role, route.name),
    points: route.geometry,
    qualityScore: route.quality_score ?? route.score_breakdown?.quality_score,
    riskySegments: route.risky_segments,
    totalScore: route.total_score ?? route.score_breakdown?.total_score
  };
}

function isRealtimeRoute(route: RealtimeRoute | RoutePath): route is RealtimeRoute {
  return "role" in route || "quality_score" in route || "score_breakdown" in route;
}

async function syncItsTraffic() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  await fetch(`${apiBaseUrl.replace(/\/$/, "")}/traffic/sync-its`, { method: "POST" });
}

async function startSimulation(mode: "mock" | "sumo_traci") {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  await fetch(`${apiBaseUrl.replace(/\/$/, "")}/simulations/start`, {
    body: JSON.stringify({ mode }),
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
}

async function stopSimulation() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  await fetch(`${apiBaseUrl.replace(/\/$/, "")}/simulations/stop`, { method: "POST" });
}

async function parseAssistantScenario(text: string) {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/assistant/scenario/parse`, {
    body: JSON.stringify({ text }),
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
  return response.json();
}

async function explainAssistantResults() {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/assistant/results/explain`, {
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
  return response.json();
}

async function geocodeAddress(query: string) {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/scenarios/geocode`, {
    body: JSON.stringify({ query }),
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
  if (!response.ok) {
    throw new Error("Geocoding failed");
  }
  return response.json() as Promise<SelectedMapPoint>;
}

async function startScenarioFromCoordinates(payload: {
  origin: SelectedMapPoint;
  destination: SelectedMapPoint;
  surrounding_vehicle_count: number;
  traffic_density: "low" | "medium" | "high";
  mode: "urban_mobility" | "six_g_like_v2x";
  use_its_api: boolean;
}) {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/scenarios/from-coordinates`, {
    body: JSON.stringify({ ...payload, ego_vehicle_count: 1 }),
    headers: { "Content-Type": "application/json" },
    method: "POST"
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail ?? "Scenario start failed");
  }
  return data;
}

function DataTable({
  columns,
  rows
}: Readonly<{
  columns: string[];
  rows: Array<Array<string | number>>;
}>) {
  return (
    <div className="overflow-hidden rounded-lg border border-[#1F2937]">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="bg-[#050816] text-[10px] uppercase tracking-[0.16em] text-slate-500">
          <tr>{columns.map((column) => <th className="px-3 py-3" key={column}>{column}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-[#1F2937]">
          {rows.map((row) => (
            <tr className="text-slate-300" key={row.join("-")}>
              {row.map((cell, index) => <td className="px-3 py-3" key={`${cell}-${index}`}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Shell({ children, kind }: Readonly<{ children: React.ReactNode; kind: PageKind }>) {
  const meta = pageMeta[kind];
  return (
    <main className="min-h-screen bg-[#050816] p-3 text-slate-100">
      <div className="grid min-h-screen gap-3 lg:grid-cols-[260px_1fr]">
        <aside className="rounded-xl border border-[#1F2937] bg-[#0b1020] p-4">
          <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-300">Autonomous V2X</p>
          <nav className="mt-5 space-y-1">
            {navItems.map(([id, label, href]) => (
              <Link
                className={`block rounded-lg px-3 py-2.5 text-sm ${id === kind ? "border border-cyan-400/40 bg-cyan-400/10 text-cyan-100" : "text-slate-400 hover:bg-white/5"}`}
                href={href}
                key={id}
              >
                {label}
              </Link>
            ))}
          </nav>
        </aside>
        <div className="space-y-3">
          <header className="rounded-xl border border-[#1F2937] bg-[#0b1020] p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">{meta.eyebrow}</p>
            <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
              <div>
                <h1 className="text-2xl font-semibold text-white">{meta.title}</h1>
                <p className="mt-1 max-w-3xl text-sm text-slate-400">{meta.summary}</p>
              </div>
              <StatusPill label="service module" tone="neutral" />
            </div>
          </header>
          {children}
        </div>
      </div>
    </main>
  );
}

function useServiceState() {
  const { payload, status } = useSimulationStream();
  return useMemo(() => {
    const liveVehicles = payload?.vehicles ?? vehicles;
    const liveStations = payload?.base_stations ?? baseStations;
    const liveEdges = payload?.edge_nodes ?? edgeNodes;
    const liveNetworkNodes = payload?.network_nodes ?? [
      ...liveStations.map((station) => ({
        id: station.id,
        name: station.name,
        node_type: station.name.toUpperCase().includes("RSU") ? "roadside_unit" : "base_station",
        latitude: station.latitude,
        longitude: station.longitude,
        capacity: station.capacity,
        current_connected_vehicles: station.current_connected_vehicles,
        congestion_score: station.congestion_score,
        edge_latency_ms: station.name.toUpperCase().includes("RSU") ? 9.5 : 14,
        coverage_radius_m: station.name.toUpperCase().includes("RSU") ? 650 : 950,
        source: station.source
      })),
      ...liveEdges.map((edgeNode) => ({
        id: edgeNode.id,
        name: edgeNode.name,
        node_type: "edge_node",
        latitude: edgeNode.latitude,
        longitude: edgeNode.longitude,
        capacity: edgeNode.capacity,
        current_connected_vehicles: Math.round(edgeNode.compute_load * edgeNode.capacity / 20),
        congestion_score: Math.round(edgeNode.compute_load * 100),
        edge_latency_ms: edgeNode.edge_latency_ms,
        coverage_radius_m: 480,
        source: edgeNode.source
      }))
    ];
    const liveObstacles = payload?.obstacles ?? obstacles;
    const liveMetrics = payload?.metrics;
    const avgLatency = liveMetrics?.avg_vehicle_latency ?? Math.round(liveVehicles.reduce((sum, item) => sum + item.current_latency_ms, 0) / liveVehicles.length);
    const avgRoad = liveMetrics?.avg_road_congestion ?? 52;
    const avgBs = liveMetrics?.avg_base_station_congestion ?? Math.round(liveStations.reduce((sum, item) => sum + item.congestion_score, 0) / liveStations.length);
    const avgRisk = liveMetrics?.avg_obstacle_risk ?? Math.round(liveVehicles.reduce((sum, item) => sum + item.obstacle_risk, 0) / liveVehicles.length * 100) / 100;
    return {
      avgBs,
      avgLatency,
      avgRisk,
      avgRoad,
      aiReady: payload?.ai_ready,
      edgeNodes: liveEdges,
      eventLogs: payload?.event_logs ?? [
        "Simulation stream ready for mock or SUMO adapter.",
        "AI-ready modules are deterministic until real models are connected.",
        "Assistant explanations are generated from structured route analysis."
      ],
      llmStatus: payload?.llm_status ?? {
        active_provider: "template",
        configured_provider: "template",
        enabled: false,
        fallback_active: true,
        keys: { gemini: "missing", huggingface: "missing" },
        policy: "Template fallback active."
      },
      obstacles: liveObstacles,
      payload,
      networkNodes: liveNetworkNodes,
      simulator: payload?.simulator ?? {
        adapter: "mock",
        label: "Mock Simulation Mode",
        mode: "mock",
        status: "mock",
        sumo_enabled: false,
        sumo_gui_enabled: false,
        using_mock_fallback: true
      },
      status,
      stations: liveStations,
      egoVehicle: payload?.ego_vehicle ?? { ...liveVehicles[0], vehicle_type: "ego_vehicle" },
      surroundingVehicles: payload?.surrounding_vehicles ?? liveVehicles.slice(1).map((vehicle, index) => ({
        ...vehicle,
        direction_relation: index % 3 === 0 ? "crossing" : "same_direction",
        distance_to_ego: Math.round((0.22 + index * 0.14) * 1000) / 1000,
        heading_difference: Math.round(Math.abs((vehicle.heading ?? 0) - (liveVehicles[0]?.heading ?? 0))),
        proximity_risk: Math.max(0.08, Math.round((0.7 - index * 0.12) * 100) / 100),
        relative_speed: Math.round((vehicle.speed - (liveVehicles[0]?.speed ?? vehicle.speed)) * 10) / 10,
        risk_level: index < 1 ? "medium" : "low",
        vehicle_type: "surrounding_vehicle"
      })),
      tick: payload?.tick ?? 0,
      traffic: payload?.traffic ?? {
        last_sync_at: null,
        source: "synthetic",
        status: "fallback"
      },
      currentRoutes: payload?.current_routes ?? [],
      recommendedRoutes: payload?.recommended_routes ?? [],
      vehicleRelationships: payload?.vehicle_relationships ?? [],
      vehicles: liveVehicles,
      rl: payload?.rl
    };
  }, [payload, status]);
}

function DashboardPage() {
  const state = useServiceState();
  const egoMapVehicles = state.egoVehicle
    ? [state.egoVehicle, ...state.vehicles.filter((vehicle) => vehicle.id !== state.egoVehicle?.id)]
    : state.vehicles;
  const recommendedRoute = state.recommendedRoutes.find((route) => route.role === "network_aware_ai_route");
  const assistantAlert = state.payload?.assistant_messages?.[0]?.content;
  return (
    <div className="grid gap-3 xl:grid-cols-[1.4fr_0.9fr]">
      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-4">
          <Stat label="Vehicles" value={state.vehicles.length} />
          <Stat label="Avg latency" value={`${state.avgLatency} ms`} />
          <Stat label="Road congestion" value={`${state.avgRoad}%`} />
          <Stat label="BS load" value={`${state.avgBs}%`} />
          <Stat label="Traffic source" value={trafficSourceLabel(state.traffic.source)} />
        </div>
        <Card className="overflow-hidden p-0">
          <div className="border-b border-[#1F2937] px-5 py-4">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Command map</p>
          </div>
          <VWorld2DMapView
            aiRecommendedRoute={toRoutePath(recommendedRoute, aiRecommendedRoute)}
            currentRoute={toRoutePath(state.currentRoutes[0], currentRoute)}
            networkNodes={state.networkNodes}
            tick={state.tick}
            vehicleRelationships={state.vehicleRelationships}
            vehicles={egoMapVehicles}
          />
        </Card>
      </div>
      <div className="space-y-3">
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Ego vehicle context</p>
          <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-xs text-slate-300">
            <span>vehicle id</span>
            <span className="text-right text-cyan-100">{state.egoVehicle?.id ?? "n/a"}</span>
            <span>speed / heading</span>
            <span className="text-right">{state.egoVehicle?.speed ?? "n/a"} km/h · {state.egoVehicle?.heading ?? 0}°</span>
            <span>connected node</span>
            <span className="text-right">{state.egoVehicle?.connected_network_node_id ?? state.egoVehicle?.connected_base_station_id ?? "n/a"}</span>
            <span>node type</span>
            <span className="text-right">{state.egoVehicle?.connected_network_node_type ?? "base_station"}</span>
            <span>node distance</span>
            <span className="text-right">{state.egoVehicle?.distance_to_connected_node ? `${state.egoVehicle.distance_to_connected_node} km` : "n/a"}</span>
            <span>node load</span>
            <span className="text-right">{state.egoVehicle?.network_node_congestion_score ?? state.egoVehicle?.base_station_congestion_score ?? "n/a"}</span>
            <span>latency</span>
            <span className="text-right">{state.egoVehicle?.current_latency_ms ?? "n/a"} ms</span>
            <span>nearest vehicle</span>
            <span className="text-right">{state.egoVehicle?.nearest_vehicle_distance ? `${Math.round(state.egoVehicle.nearest_vehicle_distance * 1000)} m` : "n/a"}</span>
            <span>collision risk</span>
            <span className="text-right">{state.egoVehicle?.collision_proximity_risk ?? 0}</span>
            <span>network stability</span>
            <span className="text-right">{state.egoVehicle?.network_stability_score ?? "n/a"}</span>
          </div>
          <p className="mt-3 text-xs text-slate-500">Visualization only; no real collision avoidance is implemented.</p>
        </Card>
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">NetworkNode alternatives</p>
          <div className="mt-3 space-y-2">
            {(state.egoVehicle?.nearest_network_nodes ?? []).slice(0, 5).map((node) => (
              <div className="rounded-lg border border-[#1F2937] bg-[#050816] p-3" key={node.id}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-mono text-cyan-100">{node.id}</span>
                  <span className="text-slate-400">{node.node_type}</span>
                </div>
                <div className="mt-2 grid grid-cols-4 gap-2 font-mono text-[10px] text-slate-400">
                  <span>{node.distance_km} km</span>
                  <span>load {node.load}</span>
                  <span>{node.estimated_latency_ms} ms</span>
                  <span>{node.recommendation_status}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Route recommendation</p>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            {recommendedRoute ? `${routeLabel(recommendedRoute.role, recommendedRoute.name)} is active with score ${recommendedRoute.total_score ?? recommendedRoute.score_breakdown?.total_score}.` : routeOptimizationSummary.explanation}
          </p>
          <div className="mt-4 grid grid-cols-3 gap-2 text-xs">
            {(state.recommendedRoutes.length ? state.recommendedRoutes : [shortestRoute, trafficAwareRoute, aiRecommendedRoute]).slice(0, 3).map((route) => (
              <div className="rounded-lg border border-[#1F2937] bg-[#050816] p-3" key={route.id}>
                <p className="text-slate-300">{isRealtimeRoute(route) ? routeLabel(route.role, route.name) : route.label}</p>
                <p className="mt-2 font-mono text-cyan-200">{isRealtimeRoute(route) ? route.quality_score ?? route.score_breakdown?.quality_score ?? "n/a" : route.qualityScore ?? "n/a"} / 100</p>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Assistant alerts</p>
          <p className="mt-3 text-sm leading-6 text-slate-300">{typeof assistantAlert === "string" ? assistantAlert : assistantRecommendation.assistantRecommendation}</p>
        </Card>
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Event log</p>
          <div className="mt-3 space-y-2 font-mono text-xs text-slate-300">
            {state.eventLogs.map((event) => <p key={event}>{event}</p>)}
          </div>
        </Card>
        <Card>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Traffic data</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <StatusPill label={trafficSourceLabel(state.traffic.source)} tone={state.traffic.source === "its_api" ? "good" : "warn"} />
            <StatusPill label={`status ${state.traffic.status}`} tone={state.traffic.status === "connected" ? "good" : "warn"} />
          </div>
          <p className="mt-3 text-xs text-slate-400">Last sync: {state.traffic.last_sync_at ?? "not synced"}</p>
        </Card>
      </div>
    </div>
  );
}

function SimulationPage() {
  const state = useServiceState();
  const [mode, setMode] = useState<"urban_mobility" | "six_g_like_v2x">("urban_mobility");
  const simulatorTone = state.simulator.status === "sumo_connected" ? "good" : "warn";
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Controls</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className={`rounded-lg border px-4 py-2 text-sm ${mode === "urban_mobility" ? "border-cyan-400/40 bg-cyan-400/10 text-cyan-100" : "border-[#1F2937] bg-[#050816] text-slate-300"}`} onClick={() => setMode("urban_mobility")} type="button">Urban Mobility</button>
          <button className={`rounded-lg border px-4 py-2 text-sm ${mode === "six_g_like_v2x" ? "border-amber-400/40 bg-amber-400/10 text-amber-100" : "border-[#1F2937] bg-[#050816] text-slate-300"}`} onClick={() => setMode("six_g_like_v2x")} type="button">6G-like V2X Research</button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="rounded-lg border border-blue-400/30 bg-blue-400/10 px-4 py-2 text-sm text-blue-100" onClick={() => void startSimulation("mock")} type="button">Start mock simulation</button>
          <button className="rounded-lg border border-amber-400/30 bg-amber-400/10 px-4 py-2 text-sm text-amber-100" onClick={() => void startSimulation("sumo_traci")} type="button">Start SUMO/TraCI</button>
          <button className="rounded-lg border border-red-400/30 bg-red-400/10 px-4 py-2 text-sm text-red-100" onClick={() => void stopSimulation()} type="button">Stop simulation</button>
          <button className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-sm text-cyan-100" onClick={() => void syncItsTraffic()} type="button">Sync ITS traffic</button>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          <Stat label="Mode" value="Urban / abstract V2X research" />
          <Stat label="Selected mode" value={mode} />
          <Stat label="Active simulation" value={state.payload?.running ? "running" : "stopped"} />
          <Stat label="Tick" value={state.tick} />
          <Stat label="Simulator" value={state.simulator.label} />
          <Stat label="SUMO GUI" value={state.simulator.sumo_gui_enabled ? "enabled" : "disabled"} />
        </div>
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Integration status</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <StatusPill label={`backend ws ${state.status}`} tone={state.status === "connected" ? "good" : "warn"} />
          <StatusPill label={state.simulator.using_mock_fallback ? "Mock Simulation Mode" : "SUMO connected"} tone={simulatorTone} />
          <StatusPill
            label={`ITS traffic ${state.traffic.status}`}
            tone={state.traffic.source === "its_api" ? "good" : "warn"}
          />
          <StatusPill label={`LLM ${state.llmStatus.active_provider}`} tone={state.llmStatus.fallback_active ? "warn" : "good"} />
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Simulator: {state.simulator.label} · Adapter: {state.simulator.adapter}
        </p>
        <p className="mt-2 text-xs text-slate-500">{state.simulator.message ?? "SUMO is optional; mock fallback remains available."}</p>
        <p className="mt-3 text-xs text-slate-400">
          Traffic source: {trafficSourceLabel(state.traffic.source)} · Last sync: {state.traffic.last_sync_at ?? "not synced"}
        </p>
      </Card>
    </div>
  );
}

function VehiclesPage() {
  const { egoVehicle, surroundingVehicles, vehicles: liveVehicles } = useServiceState();
  const ego = egoVehicle ?? liveVehicles[0];
  const surrounding = surroundingVehicles.slice(0, 5);
  return (
    <div className="grid gap-3 xl:grid-cols-[0.8fr_1.2fr]">
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Ego vehicle</p>
        <div className="mt-4 grid gap-3">
          <Stat label="Vehicle" value={ego.id} />
          <Stat label="Speed" value={`${ego.speed} km/h`} />
          <Stat label="Heading" value={`${ego.heading ?? 0} deg`} />
          <Stat label="Route" value={ego.current_route_id} />
          <Stat label="Network node" value={`${ego.connected_base_station_id} / ${ego.connected_edge_node_id ?? "edge n/a"}`} />
          <Stat label="Connected NetworkNode" value={ego.connected_network_node_id ?? ego.connected_base_station_id} />
          <Stat label="Node distance/load" value={`${ego.distance_to_connected_node ?? ego.distance_to_connected_base_station_km} km / ${ego.network_node_congestion_score ?? ego.base_station_congestion_score}`} />
          <Stat label="Latency / risk" value={`${ego.current_latency_ms} ms / ${ego.obstacle_risk}`} />
          <Stat label="Nearest vehicle" value={ego.nearest_vehicle_distance ? `${Math.round(ego.nearest_vehicle_distance * 1000)} m` : "n/a"} />
          <Stat label="Collision proximity" value={ego.collision_proximity_risk ?? 0} />
          <Stat label="Network stability" value={ego.network_stability_score ?? "n/a"} />
        </div>
      </Card>
      <Card>
        <DataTable
          columns={["vehicle", "distance", "relative speed", "direction", "heading diff", "risk"]}
          rows={surrounding.map((vehicle) => [
            vehicle.id,
            vehicle.distance_to_ego ? `${Math.round(vehicle.distance_to_ego * 1000)} m` : "n/a",
            `${vehicle.relative_speed ?? 0} km/h`,
            vehicle.direction_relation ?? "n/a",
            `${vehicle.heading_difference ?? 0}°`,
            `${vehicle.risk_level ?? "low"} (${vehicle.proximity_risk ?? 0})`
          ])}
        />
      </Card>
    </div>
  );
}

function MapPage() {
  const state = useServiceState();
  const [selectionMode, setSelectionMode] = useState<"origin" | "destination" | null>("origin");
  const [origin, setOrigin] = useState<SelectedMapPoint | null>(state.payload?.debug?.selected_origin_coordinate ?? null);
  const [destination, setDestination] = useState<SelectedMapPoint | null>(state.payload?.debug?.selected_destination_coordinate ?? null);
  const [originQuery, setOriginQuery] = useState("");
  const [destinationQuery, setDestinationQuery] = useState("");
  const [scenarioStatus, setScenarioStatus] = useState<string>("Select origin and destination to synchronize SUMO route.");
  const shortest = state.recommendedRoutes.find((route) => route.role === "shortest_route_baseline");
  const trafficAware = state.recommendedRoutes.find((route) => route.role === "traffic_aware_route");
  const networkAware = state.recommendedRoutes.find((route) => route.role === "network_aware_ai_route");
  const debug = state.payload?.debug;

  function handleCoordinateSelect(point: SelectedMapPoint) {
    if (selectionMode === "origin") {
      setOrigin(point);
      setSelectionMode("destination");
    } else if (selectionMode === "destination") {
      setDestination(point);
      setSelectionMode(null);
    }
  }

  async function geocodeInto(target: "origin" | "destination") {
    const query = target === "origin" ? originQuery : destinationQuery;
    const point = await geocodeAddress(query);
    if (target === "origin") {
      setOrigin(point);
      setSelectionMode("destination");
    } else {
      setDestination(point);
      setSelectionMode(null);
    }
  }

  async function startCoordinateScenario() {
    if (!origin || !destination) {
      setScenarioStatus("Origin and destination are required.");
      return;
    }
    try {
      const result = await startScenarioFromCoordinates({
        destination,
        mode: "urban_mobility",
        origin,
        surrounding_vehicle_count: 80,
        traffic_density: "medium",
        use_its_api: true
      });
      setScenarioStatus(`Started: ${result.debug?.matched_origin_edge?.edge_id ?? "origin"} -> ${result.debug?.matched_destination_edge?.edge_id ?? "destination"}`);
    } catch (error) {
      setScenarioStatus(error instanceof Error ? error.message : "Scenario start failed");
    }
  }

  return (
    <div className="grid gap-3 xl:grid-cols-[1fr_360px]">
    <Card className="min-h-[calc(100vh-160px)] overflow-hidden p-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-[#1F2937] bg-[#0b1020] px-5 py-3">
        <StatusPill label={`traffic ${trafficSourceLabel(state.traffic.source)}`} tone={state.traffic.source === "its_api" ? "good" : "warn"} />
        <StatusPill label={`last sync ${state.traffic.last_sync_at ?? "not synced"}`} tone="neutral" />
        <StatusPill label={`select ${selectionMode ?? "ready"}`} tone={selectionMode ? "warn" : "good"} />
      </div>
      <VWorld2DMapView
        aiRecommendedRoute={toRoutePath(networkAware, aiRecommendedRoute)}
        baseStations={state.stations}
        congestionZones={congestionZones}
        currentRoute={toRoutePath(state.currentRoutes[0], currentRoute)}
        destination={destination ?? state.payload?.debug?.selected_destination_coordinate ?? null}
        edgeNodes={state.edgeNodes}
        networkNodes={state.networkNodes}
        onCoordinateSelect={handleCoordinateSelect}
        obstacles={state.obstacles}
        origin={origin ?? state.payload?.debug?.selected_origin_coordinate ?? null}
        selectionMode={selectionMode}
        shortestRoute={toRoutePath(shortest, shortestRoute)}
        tick={state.tick}
        trafficAwareRoute={toRoutePath(trafficAware, trafficAwareRoute)}
        vehicleRelationships={state.vehicleRelationships}
        vehicles={state.vehicles}
      />
    </Card>
    <div className="space-y-3">
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">SUMO-VWorld route setup</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className={`rounded-lg border px-3 py-2 text-sm ${selectionMode === "origin" ? "border-emerald-300/40 bg-emerald-400/10 text-emerald-100" : "border-[#1F2937] bg-[#050816] text-slate-300"}`} onClick={() => setSelectionMode("origin")} type="button">Click origin</button>
          <button className={`rounded-lg border px-3 py-2 text-sm ${selectionMode === "destination" ? "border-red-300/40 bg-red-400/10 text-red-100" : "border-[#1F2937] bg-[#050816] text-slate-300"}`} onClick={() => setSelectionMode("destination")} type="button">Click destination</button>
        </div>
        <input className="mt-4 w-full rounded-lg border border-[#1F2937] bg-[#050816] px-3 py-2 text-sm outline-none" onChange={(event) => setOriginQuery(event.target.value)} placeholder="Search origin with VWorld Geocoder" value={originQuery} />
        <button className="mt-2 rounded-lg border border-emerald-300/30 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100" onClick={() => void geocodeInto("origin").catch((error) => setScenarioStatus(String(error)))} type="button">Set origin from search</button>
        <input className="mt-4 w-full rounded-lg border border-[#1F2937] bg-[#050816] px-3 py-2 text-sm outline-none" onChange={(event) => setDestinationQuery(event.target.value)} placeholder="Search destination with VWorld Geocoder" value={destinationQuery} />
        <button className="mt-2 rounded-lg border border-red-300/30 bg-red-400/10 px-3 py-2 text-sm text-red-100" onClick={() => void geocodeInto("destination").catch((error) => setScenarioStatus(String(error)))} type="button">Set destination from search</button>
        <button className="mt-4 w-full rounded-lg border border-cyan-300/35 bg-cyan-400/10 px-3 py-2 text-sm font-semibold text-cyan-100" onClick={() => void startCoordinateScenario()} type="button">Start synchronized SUMO scenario</button>
        <p className="mt-3 text-xs text-slate-400">{scenarioStatus}</p>
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Debug panel</p>
        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-xs text-slate-300">
          <span>origin</span><span className="text-right">{origin ? `${origin.lat}, ${origin.lng}` : debug?.selected_origin_coordinate ? `${debug.selected_origin_coordinate.lat}, ${debug.selected_origin_coordinate.lng}` : "n/a"}</span>
          <span>destination</span><span className="text-right">{destination ? `${destination.lat}, ${destination.lng}` : debug?.selected_destination_coordinate ? `${debug.selected_destination_coordinate.lat}, ${debug.selected_destination_coordinate.lng}` : "n/a"}</span>
          <span>origin edge</span><span className="text-right">{String(debug?.matched_sumo_origin_edge?.edge_id ?? "n/a")}</span>
          <span>destination edge</span><span className="text-right">{String(debug?.matched_sumo_destination_edge?.edge_id ?? "n/a")}</span>
          <span>route length</span><span className="text-right">{debug?.route_length_m ? `${debug.route_length_m} m` : "n/a"}</span>
          <span>conversion</span><span className="text-right">{debug?.coordinate_conversion_status ?? "n/a"}</span>
          <span>SUMO</span><span className="text-right">{debug?.sumo_connection_status ?? state.simulator.status}</span>
          <span>ITS</span><span className="text-right">{debug?.its_api_status ?? state.traffic.status}</span>
        </div>
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Live metrics</p>
        <div className="mt-3 grid gap-2">
          <Stat label="Current latency" value={`${state.payload?.metrics.current_latency_ms ?? state.avgLatency} ms`} />
          <Stat label="Road congestion" value={`${state.payload?.metrics.road_congestion_score ?? state.avgRoad}`} />
          <Stat label="Route score" value={state.payload?.metrics.route_score ?? "n/a"} />
        </div>
      </Card>
    </div>
    </div>
  );
}

function NetworkPage() {
  const state = useServiceState();
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card>
        <DataTable
          columns={["NetworkNode", "type", "capacity", "connected", "load", "latency"]}
          rows={state.networkNodes.map((node) => [
            node.id,
            node.node_type,
            node.capacity,
            node.current_connected_vehicles,
            `${node.congestion_score}%`,
            `${node.edge_latency_ms} ms`
          ])}
        />
      </Card>
      <Card>
        <DataTable
          columns={["edge node", "capacity", "compute load", "latency", "source"]}
          rows={state.edgeNodes.map((edge) => [
            edge.id,
            edge.capacity,
            `${Math.round(edge.compute_load * 100)}%`,
            `${edge.edge_latency_ms} ms`,
            edge.source
          ])}
        />
      </Card>
    </div>
  );
}

function RoutesPage() {
  const state = useServiceState();
  const routes = state.recommendedRoutes.length ? state.recommendedRoutes : [];
  return (
    <div className="grid gap-3 xl:grid-cols-[1fr_1.1fr]">
      <div className="grid gap-3 lg:grid-cols-2">
        {(routes.length ? routes : state.currentRoutes).map((route) => (
          <Card key={`${route.role}-${route.id}`}>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">{routeLabel(route.role, route.name)}</p>
            <p className="mt-3 font-mono text-2xl text-white">{route.quality_score ?? route.score_breakdown?.quality_score ?? "n/a"} / 100</p>
            <p className="mt-2 text-sm text-slate-400">Route id {route.id} · total score {route.total_score ?? route.score_breakdown?.total_score ?? "n/a"}</p>
          </Card>
        ))}
      </div>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Score breakdown</p>
        <DataTable
          columns={["route", "travel", "road", "latency", "node load", "obstacle", "edge"]}
          rows={(routes.length ? routes : state.currentRoutes).map((route) => [
            routeLabel(route.role, route.id),
            route.score_breakdown?.travel_time_score ?? "n/a",
            route.score_breakdown?.road_congestion_score ?? "n/a",
            route.score_breakdown?.predicted_network_latency_score ?? "n/a",
            route.score_breakdown?.base_station_congestion_score ?? "n/a",
            route.score_breakdown?.obstacle_risk_score ?? "n/a",
            route.score_breakdown?.edge_latency_score ?? "n/a"
          ])}
        />
      </Card>
    </div>
  );
}

function AnalysisPage() {
  const state = useServiceState();
  const feature = state.aiReady?.features?.[0];
  const latency = state.aiReady?.latency_predictions?.[0];
  const congestion = state.aiReady?.congestion_predictions?.[0];
  const blockage = state.aiReady?.blockage_predictions?.[0];
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card><Stat label="Latency analysis" value={`${latency?.predicted_latency_ms ?? state.avgLatency} ms`} /></Card>
      <Card><Stat label="Congestion analysis" value={`${congestion?.predicted_congestion_score ?? state.avgRoad} score`} /></Card>
      <Card><Stat label="Blockage analysis" value={`${blockage?.predicted_blockage_risk ?? state.avgRisk} risk`} /></Card>
      <Card><Stat label="Nearby vehicle risk" value={`${state.egoVehicle?.collision_proximity_risk ?? 0}`} /></Card>
      <Card className="xl:col-span-2">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Feature vector preview</p>
        <pre className="mt-4 max-h-80 overflow-auto rounded-lg border border-[#1F2937] bg-[#050816] p-4 text-xs leading-5 text-slate-300">
          {JSON.stringify(feature ?? state.aiReady?.features ?? {}, null, 2)}
        </pre>
      </Card>
    </div>
  );
}

function RlLabPage() {
  const state = useServiceState();
  const rlState = state.rl?.state ?? {
    vehicle_state: {
      current_latency_ms: state.egoVehicle?.current_latency_ms,
      id: state.egoVehicle?.id,
      speed: state.egoVehicle?.speed
    }
  };
  const rewardComponents = state.rl?.reward.components ?? {};
  const actions = state.rl?.action_candidates ?? [
    { action: "keep_current_route", route_id: state.egoVehicle?.current_route_id },
    { action: "switch_network_node", status: "available_when_backend_stream_is_connected" },
    { action: "reduce_speed_recommendation", status: "placeholder_only" }
  ];

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Current state vector</p>
        <pre className="mt-4 max-h-80 overflow-auto rounded-lg border border-[#1F2937] bg-[#050816] p-4 text-xs leading-5 text-slate-300">
          {JSON.stringify(rlState, null, 2)}
        </pre>
      </Card>
      <Card>
        <DataTable
          columns={["action", "target/status"]}
          rows={actions.map((action) => [
            String(action.action),
            String(action.route_id ?? action.status ?? action.reason ?? "candidate")
          ])}
        />
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Reward components</p>
        <div className="mt-4 grid gap-2">
          {Object.entries(rewardComponents).map(([key, value]) => (
            <div className="flex items-center justify-between rounded-lg border border-[#1F2937] bg-[#050816] px-3 py-2 font-mono text-xs" key={key}>
              <span className="text-slate-400">{key}</span>
              <span className={value >= 0 ? "text-emerald-200" : "text-red-200"}>{value}</span>
            </div>
          ))}
        </div>
        <p className="mt-4 font-mono text-sm text-cyan-100">reward {state.rl?.reward.reward ?? "n/a"}</p>
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Policy comparison</p>
        <pre className="mt-4 max-h-56 overflow-auto rounded-lg border border-[#1F2937] bg-[#050816] p-4 text-xs leading-5 text-slate-300">
          {JSON.stringify(state.rl?.policy_comparison ?? {
            baseline_policy: "interface ready",
            future_rl_policy: "Stable-Baselines3 placeholder; no training"
          }, null, 2)}
        </pre>
      </Card>
      <Card className="xl:col-span-2">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Episode logs</p>
        <div className="mt-3 space-y-2 font-mono text-xs text-slate-300">
          {(state.rl?.episode_logs ?? []).slice(-6).map((log) => (
            <p key={`${log.timestamp}-${log.event}`}>{log.timestamp} · {log.event}</p>
          ))}
          {!state.rl?.episode_logs?.length ? <p>No RL episodes have been run. This page is prepared for future training integration.</p> : null}
        </div>
      </Card>
    </div>
  );
}

function AssistantPage() {
  const state = useServiceState();
  const [scenarioText, setScenarioText] = useState("강남역 주변에서 사용자 차량 1대와 주변 차량 80대를 배치하고, 사고 구간 하나를 추가한 뒤 6G-like V2X 모드로 실행해줘.");
  const [scenarioResult, setScenarioResult] = useState<object | null>(null);
  const [explanation, setExplanation] = useState<object | null>(null);

  return (
    <div className="grid gap-3 xl:grid-cols-[1fr_0.9fr]">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Scenario Builder chat</p>
          <StatusPill label={`provider ${state.llmStatus.active_provider}`} tone={state.llmStatus.fallback_active ? "warn" : "good"} />
        </div>
        <textarea
          className="mt-4 min-h-44 w-full rounded-lg border border-[#1F2937] bg-[#050816] p-4 text-sm outline-none"
          onChange={(event) => setScenarioText(event.target.value)}
          placeholder="Describe a scenario for LLM/template parsing..."
          value={scenarioText}
        />
        <button
          className="mt-3 rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-sm text-cyan-100"
          onClick={() => void parseAssistantScenario(scenarioText).then(setScenarioResult)}
          type="button"
        >
          Parse scenario safely
        </button>
        <pre className="mt-4 max-h-72 overflow-auto rounded-lg border border-[#1F2937] bg-[#050816] p-4 text-xs leading-5 text-slate-300">
          {JSON.stringify(scenarioResult ?? {
            status: "waiting_for_input",
            note: "LLM output is validated before it can become scenario setup."
          }, null, 2)}
        </pre>
      </Card>
      <Card>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">Result Explanation panel</p>
        <button
          className="mt-4 rounded-lg border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-sm text-emerald-100"
          onClick={() => void explainAssistantResults().then(setExplanation)}
          type="button"
        >
          Explain current result
        </button>
        <p className="mt-4 text-sm leading-6 text-slate-300">
          {String((explanation as { explanation?: { content?: string } } | null)?.explanation?.content ?? assistantRecommendation.assistantRecommendation)}
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <StatusPill label={`LLM enabled ${state.llmStatus.enabled}`} tone={state.llmStatus.enabled ? "good" : "warn"} />
          <StatusPill label={`fallback ${state.llmStatus.fallback_active}`} tone={state.llmStatus.fallback_active ? "warn" : "good"} />
          <StatusPill label={`Gemini ${state.llmStatus.keys.gemini}`} tone={state.llmStatus.keys.gemini === "configured" ? "good" : "neutral"} />
          <StatusPill label={`HF ${state.llmStatus.keys.huggingface}`} tone={state.llmStatus.keys.huggingface === "configured" ? "good" : "neutral"} />
        </div>
        <p className="mt-3 text-xs text-amber-200">{state.llmStatus.policy}</p>
      </Card>
    </div>
  );
}

function ExperimentsPage() {
  const state = useServiceState();
  const bestRoute = state.recommendedRoutes.find((route) => route.role === "network_aware_ai_route");
  const shortest = state.recommendedRoutes.find((route) => route.role === "shortest_route_baseline");
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Card>
        <DataTable
          columns={["run", "simulation", "tick", "status", "traffic"]}
          rows={[
            ["current-live-run", state.payload?.simulation_id ?? "sim-local", state.tick, state.payload?.running ? "running" : "stopped", trafficSourceLabel(state.traffic.source)],
            ["latest-ai-ready-snapshot", state.payload?.simulation_id ?? "sim-local", state.tick, state.aiReady?.status ?? "waiting", state.simulator.label]
          ]}
        />
      </Card>
      <Card>
        <DataTable
          columns={["comparison", "baseline", "candidate", "delta"]}
          rows={[
            [
              "shortest vs network-aware",
              shortest?.total_score ?? shortest?.score_breakdown?.total_score ?? "n/a",
              bestRoute?.total_score ?? bestRoute?.score_breakdown?.total_score ?? "n/a",
              shortest && bestRoute ? Math.round(((shortest.total_score ?? 0) - (bestRoute.total_score ?? 0)) * 100) / 100 : "n/a"
            ],
            ["avg latency", `${state.avgLatency} ms`, `${state.payload?.metrics?.avg_predicted_latency ?? "n/a"} ms`, state.payload?.metrics?.route_change_count ?? "n/a"]
          ]}
        />
      </Card>
    </div>
  );
}

function SettingsPage() {
  const state = useServiceState();
  const rows = [
    ["Backend WebSocket", state.status],
    ["Simulation adapter", state.simulator.adapter],
    ["SUMO status", state.simulator.status],
    ["ITS traffic", `${trafficSourceLabel(state.traffic.source)} / ${state.traffic.status}`],
    ["LLM provider", state.llmStatus.active_provider],
    ["LLM fallback", state.llmStatus.fallback_active ? "active" : "inactive"],
    ["VWorld public map", process.env.NEXT_PUBLIC_VWORLD_API_KEY ? "configured" : "missing"]
  ];
  return (
    <Card>
      <DataTable
        columns={["connection", "status"]}
        rows={rows}
      />
      <p className="mt-4 text-xs text-slate-500">Raw API keys and secret values are never displayed in the frontend.</p>
    </Card>
  );
}

export function ServicePage({ kind }: Readonly<{ kind: PageKind }>) {
  const content = {
    analysis: <AnalysisPage />,
    assistant: <AssistantPage />,
    dashboard: <DashboardPage />,
    experiments: <ExperimentsPage />,
    map: <MapPage />,
    network: <NetworkPage />,
    "rl-lab": <RlLabPage />,
    routes: <RoutesPage />,
    settings: <SettingsPage />,
    simulation: <SimulationPage />,
    vehicles: <VehiclesPage />
  }[kind];

  return <Shell kind={kind}>{content}</Shell>;
}
