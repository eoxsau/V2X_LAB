"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BaseStationMarker } from "./BaseStationMarker";
import { ConnectionLine } from "./ConnectionLine";
import {
  initialMockUsers,
  mockBaseStations,
  researchMockUsers,
  seoulCenter,
  type BaseStation,
  type EnvironmentMode,
  type ProjectedPoint,
  type UserMarkerData
} from "./map-data";
import { UserMarker } from "./UserMarker";
import { VWorld2DMapView } from "./VWorld2DMapView";

type EventLog = {
  id: number;
  message: string;
  tone: "info" | "success" | "warning";
};

type RouteAlgorithm =
  | "dijkstra"
  | "astar"
  | "k_shortest_path"
  | "network_aware_routing"
  | "look_ahead_routing"
  | "rl_routing";

type LatencyAlgorithm =
  | "distance_based_latency"
  | "load_aware_latency"
  | "blockage_aware_latency"
  | "mec_aware_latency"
  | "full_composite_latency";

type BaseStationSelectionAlgorithm =
  | "nearest_bs"
  | "lowest_latency_bs"
  | "strongest_signal_bs"
  | "load_balanced_bs"
  | "look_ahead_bs_selection"
  | "rl_based_bs_selection";

type ResourceAllocationAlgorithm =
  | "equal_allocation"
  | "proportional_allocation"
  | "traffic_aware_allocation"
  | "load_balancing_allocation"
  | "latency_minimizing_allocation"
  | "priority_based_allocation";

type SimulationConfig = {
  routeAlgorithm: RouteAlgorithm;
  latencyAlgorithm: LatencyAlgorithm;
  baseStationSelectionAlgorithm: BaseStationSelectionAlgorithm;
  resourceAllocationAlgorithm: ResourceAllocationAlgorithm;
  lookaheadK: number;
  useCustomPolicy: boolean;
};

const environmentProfiles = {
  current: {
    congestionSensitivity: 1,
    label: "Current Wireless",
    latencyTarget: 22,
    signalDecay: 1,
    userDensity: "standard"
  },
  research: {
    congestionSensitivity: 1.45,
    label: "6G-like Research",
    latencyTarget: 14,
    signalDecay: 1.35,
    userDensity: "dense"
  }
};

const routeAlgorithmOptions: Array<{ label: string; value: RouteAlgorithm }> = [
  { value: "dijkstra", label: "Dijkstra" },
  { value: "astar", label: "A*" },
  { value: "k_shortest_path", label: "K-Shortest" },
  { value: "network_aware_routing", label: "Network-Aware" },
  { value: "look_ahead_routing", label: "Look-Ahead" },
  { value: "rl_routing", label: "RL Routing" }
];

const latencyAlgorithmOptions: Array<{ label: string; value: LatencyAlgorithm }> = [
  { value: "distance_based_latency", label: "Distance" },
  { value: "load_aware_latency", label: "Load-Aware" },
  { value: "blockage_aware_latency", label: "Blockage-Aware" },
  { value: "mec_aware_latency", label: "MEC-Aware" },
  { value: "full_composite_latency", label: "Composite" }
];

const baseStationSelectionOptions: Array<{ label: string; value: BaseStationSelectionAlgorithm }> = [
  { value: "nearest_bs", label: "Nearest BS" },
  { value: "lowest_latency_bs", label: "Lowest Latency" },
  { value: "strongest_signal_bs", label: "Strongest Signal" },
  { value: "load_balanced_bs", label: "Load Balanced" },
  { value: "look_ahead_bs_selection", label: "Look-Ahead BS" },
  { value: "rl_based_bs_selection", label: "RL BS" }
];

const resourceAllocationOptions: Array<{ label: string; value: ResourceAllocationAlgorithm }> = [
  { value: "equal_allocation", label: "Equal" },
  { value: "proportional_allocation", label: "Proportional" },
  { value: "traffic_aware_allocation", label: "Traffic-Aware" },
  { value: "load_balancing_allocation", label: "Load Balance" },
  { value: "latency_minimizing_allocation", label: "Latency-Min" },
  { value: "priority_based_allocation", label: "Priority" }
];

const lookaheadOptions = [3, 5, 8, 12];

const defaultSimulationConfig: SimulationConfig = {
  routeAlgorithm: "dijkstra",
  latencyAlgorithm: "full_composite_latency",
  baseStationSelectionAlgorithm: "lowest_latency_bs",
  resourceAllocationAlgorithm: "equal_allocation",
  lookaheadK: 5,
  useCustomPolicy: false
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const MAX_CONNECTION_CANDIDATES = 6;
const MAX_CONNECTION_DISTANCE_DEGREES = 0.018;

function getSeedUsers(environment: EnvironmentMode) {
  return environment === "research" ? researchMockUsers : initialMockUsers;
}

function approximateDistanceDegrees(
  [latA, lngA]: [number, number],
  [latB, lngB]: [number, number]
) {
  const latScale = 111_000;
  const lngScale = Math.cos((latA * Math.PI) / 180) * 111_000;
  const deltaLatM = (latA - latB) * latScale;
  const deltaLngM = (lngA - lngB) * lngScale;
  return Math.sqrt(deltaLatM ** 2 + deltaLngM ** 2);
}

function formatOptionLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function applyResourceAllocationLoad(
  baseLoad: number,
  algorithm: ResourceAllocationAlgorithm,
  environment: EnvironmentMode
) {
  const densityPenalty = environment === "research" ? 6 : 2;

  switch (algorithm) {
    case "proportional_allocation":
      return baseLoad * 0.96;
    case "traffic_aware_allocation":
      return baseLoad + densityPenalty;
    case "load_balancing_allocation":
      return baseLoad * 0.9;
    case "latency_minimizing_allocation":
      return baseLoad * 0.88;
    case "priority_based_allocation":
      return baseLoad * 0.92;
    default:
      return baseLoad;
  }
}

function getRouteDriftFactor(routeAlgorithm: RouteAlgorithm) {
  switch (routeAlgorithm) {
    case "astar":
      return 0.92;
    case "k_shortest_path":
      return 1.08;
    case "network_aware_routing":
      return 0.9;
    case "look_ahead_routing":
      return 0.86;
    case "rl_routing":
      return 0.88;
    default:
      return 1;
  }
}

function estimateLatencyMs({
  distanceM,
  environment,
  stationLoad,
  tick,
  index,
  latencyAlgorithm,
  useCustomPolicy
}: {
  distanceM: number;
  environment: EnvironmentMode;
  stationLoad: number;
  tick: number;
  index: number;
  latencyAlgorithm: LatencyAlgorithm;
  useCustomPolicy: boolean;
}) {
  const baseLatency = environmentProfiles[environment].latencyTarget;
  const distancePenalty = distanceM / 140;
  const loadPenalty = stationLoad * 0.18;
  const blockagePenalty = Math.abs(Math.sin(tick / 2 + index)) * (environment === "research" ? 10 : 6);

  let latencyEstimate = baseLatency + distancePenalty;
  switch (latencyAlgorithm) {
    case "distance_based_latency":
      latencyEstimate = baseLatency + distancePenalty * 1.6;
      break;
    case "load_aware_latency":
      latencyEstimate = baseLatency + loadPenalty * 1.3 + distancePenalty * 0.6;
      break;
    case "blockage_aware_latency":
      latencyEstimate = baseLatency + distancePenalty * 0.8 + blockagePenalty + loadPenalty * 0.65;
      break;
    case "mec_aware_latency":
      latencyEstimate = baseLatency - 3 + distancePenalty * 0.7 + loadPenalty * 0.75;
      break;
    default:
      latencyEstimate = baseLatency + distancePenalty + loadPenalty + blockagePenalty;
      break;
  }

  if (useCustomPolicy) {
    latencyEstimate *= 0.94;
  }

  return Math.round(latencyEstimate);
}

function getNearbyStations(position: [number, number], stations: BaseStation[]) {
  return stations
    .map((station) => ({
      distanceM: approximateDistanceDegrees(position, station.position),
      station
    }))
    .filter(({ distanceM }) => Number.isFinite(distanceM))
    .sort((left, right) => left.distanceM - right.distanceM)
    .filter(({ distanceM }, index) => {
      if (index < MAX_CONNECTION_CANDIDATES) {
        return true;
      }

      return distanceM <= MAX_CONNECTION_DISTANCE_DEGREES * 111_000;
    });
}

function pickConnectedStation({
  baseStationSelectionAlgorithm,
  environment,
  lookaheadK,
  resourceAllocationAlgorithm,
  seedStationId,
  stations,
  tick,
  useCustomPolicy,
  userPosition
}: {
  baseStationSelectionAlgorithm: BaseStationSelectionAlgorithm;
  environment: EnvironmentMode;
  lookaheadK: number;
  resourceAllocationAlgorithm: ResourceAllocationAlgorithm;
  seedStationId: string;
  stations: BaseStation[];
  tick: number;
  useCustomPolicy: boolean;
  userPosition: [number, number];
}) {
  const nearbyStations = getNearbyStations(userPosition, stations);
  const seededStation = stations.find((station) => station.id === seedStationId);

  const candidatePool = nearbyStations.slice(0, Math.max(1, Math.min(lookaheadK, nearbyStations.length)));
  if (candidatePool.length === 0) {
    return seededStation ?? stations[0];
  }

  const rankedCandidates = candidatePool
    .map(({ distanceM, station }) => {
      const effectiveLoad = applyResourceAllocationLoad(
        station.load,
        resourceAllocationAlgorithm,
        environment
      );
      const strongestSignalScore = distanceM / 28 + effectiveLoad * 0.08;
      const lowestLatencyScore = distanceM / 120 + effectiveLoad * 0.2;
      const loadBalancedScore = effectiveLoad + distanceM / 200;
      const lookAheadPenalty = (tick % Math.max(lookaheadK, 1)) * 0.35;
      let score = lowestLatencyScore;

      switch (baseStationSelectionAlgorithm) {
        case "nearest_bs":
          score = distanceM;
          break;
        case "strongest_signal_bs":
          score = strongestSignalScore;
          break;
        case "load_balanced_bs":
          score = loadBalancedScore;
          break;
        case "look_ahead_bs_selection":
          score = lowestLatencyScore + lookAheadPenalty;
          break;
        case "rl_based_bs_selection":
          score = distanceM / 150 + effectiveLoad * 0.16 + lookAheadPenalty * 0.75;
          break;
        default:
          score = lowestLatencyScore;
          break;
      }

      if (useCustomPolicy) {
        score *= 0.96;
      }

      return { score, station };
    })
    .sort((left, right) => left.score - right.score);

  if (environment === "current") {
    return (
      rankedCandidates.find(({ station }) => station.id === seedStationId)?.station ??
      rankedCandidates[0]?.station ??
      seededStation ??
      stations[0]
    );
  }

  if (tick % 6 !== 0) {
    return (
      rankedCandidates.find(({ station }) => station.id === seedStationId)?.station ??
      rankedCandidates[0]?.station ??
      seededStation ??
      stations[0]
    );
  }

  return rankedCandidates[Math.floor(tick / 6) % rankedCandidates.length]?.station ?? rankedCandidates[0]?.station;
}

function getNextUserPositions({
  config,
  environment,
  stations,
  tick
}: {
  config: SimulationConfig;
  environment: EnvironmentMode;
  stations: BaseStation[];
  tick: number;
}): UserMarkerData[] {
  const seedUsers = getSeedUsers(environment);
  const sensitivity = environmentProfiles[environment].congestionSensitivity;
  const routeDriftFactor = getRouteDriftFactor(config.routeAlgorithm);

  return seedUsers.map((user, index) => {
    const latDrift = Math.sin(tick / 3 + index) * 0.00035 * sensitivity * routeDriftFactor;
    const lngDrift = Math.cos(tick / 4 + index * 0.8) * 0.00028 * sensitivity * routeDriftFactor;
    const nextPosition: [number, number] = [
      seedUsers[index].position[0] + latDrift,
      seedUsers[index].position[1] + lngDrift
    ];
    const connectedStation = pickConnectedStation({
      baseStationSelectionAlgorithm: config.baseStationSelectionAlgorithm,
      environment,
      lookaheadK: config.lookaheadK,
      resourceAllocationAlgorithm: config.resourceAllocationAlgorithm,
      seedStationId: user.connectedTo,
      stations,
      tick,
      useCustomPolicy: config.useCustomPolicy,
      userPosition: nextPosition
    });
    const stationLoad = applyResourceAllocationLoad(
      connectedStation?.load ?? 50,
      config.resourceAllocationAlgorithm,
      environment
    );
    const distanceM = connectedStation
      ? approximateDistanceDegrees(nextPosition, connectedStation.position)
      : 0;

    return {
      ...user,
      connectedTo: connectedStation?.id ?? user.connectedTo,
      latencyMs: estimateLatencyMs({
        distanceM,
        environment,
        stationLoad,
        tick,
        index,
        latencyAlgorithm: config.latencyAlgorithm,
        useCustomPolicy: config.useCustomPolicy
      }),
      position: nextPosition
    };
  });
}

function mapPublicStation(record: Record<string, unknown>, index: number): BaseStation | null {
  const latitude = Number(record.latitude);
  const longitude = Number(record.longitude);

  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    return null;
  }

  const capacity = Number(record.capacity) || 1200;
  const load = Math.max(20, Math.min(95, Math.round(100 - capacity / 30)));

  return {
    id: String(record.id ?? `PUBLIC-${index + 1}`),
    label: String(record.name ?? `Public Station ${index + 1}`),
    load,
    position: [latitude, longitude],
    source: "public",
    status: load > 82 ? "danger" : load > 68 ? "warning" : "stable"
  };
}

function createSyntheticStations(existingCount: number): BaseStation[] {
  return Array.from({ length: 4 }, (_, index) => {
    const angle = (Math.PI * 2 * index) / 4 + existingCount * 0.27;
    const radius = 0.0055 + index * 0.0013;
    const load = 48 + index * 9;

    return {
      id: `SYN-${existingCount + index + 1}`,
      label: `Synthetic Sector ${index + 1}`,
      load,
      position: [seoulCenter[0] + Math.sin(angle) * radius, seoulCenter[1] + Math.cos(angle) * radius],
      source: "synthetic",
      status: load > 82 ? "danger" : load > 68 ? "warning" : "stable"
    };
  });
}

export function MapContainer() {
  const [environment, setEnvironment] = useState<EnvironmentMode>("current");
  const [isRunning, setIsRunning] = useState(true);
  const [simulationConfig, setSimulationConfig] =
    useState<SimulationConfig>(defaultSimulationConfig);
  const [searchQuery, setSearchQuery] = useState("");
  const [stations, setStations] = useState(mockBaseStations);
  const [tick, setTick] = useState(0);
  const [users, setUsers] = useState(initialMockUsers);
  const [stationPoints, setStationPoints] = useState<Record<string, ProjectedPoint>>({});
  const [userPoints, setUserPoints] = useState<Record<string, ProjectedPoint>>({});
  const [events, setEvents] = useState<EventLog[]>([
    { id: 1, message: "Stable 2D VWorld network layer initialized", tone: "success" },
    { id: 2, message: "Mock wireless simulation stream active", tone: "info" }
  ]);

  const stationsById = useMemo(
    () => new Map(stations.map((station) => [station.id, station])),
    [stations]
  );
  const visibleStations = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();

    if (!normalizedQuery) {
      return stations;
    }

    return stations.filter(
      (station) =>
        station.id.toLowerCase().includes(normalizedQuery) ||
        station.label.toLowerCase().includes(normalizedQuery)
    );
  }, [searchQuery, stations]);
  const visibleStationIds = useMemo(
    () => new Set(visibleStations.map((station) => station.id)),
    [visibleStations]
  );
  const averageLatency = useMemo(
    () => Math.round(users.reduce((total, user) => total + user.latencyMs, 0) / users.length),
    [users]
  );
  const congestionScore = useMemo(
    () => Math.round(stations.reduce((total, station) => total + station.load, 0) / stations.length),
    [stations]
  );
  const simulationMetrics = useMemo(
    () => ({
      averageLatencyMs: averageLatency,
      congestionScore,
      lookaheadK: simulationConfig.lookaheadK,
      routeAlgorithm: simulationConfig.routeAlgorithm,
      latencyAlgorithm: simulationConfig.latencyAlgorithm,
      baseStationSelectionAlgorithm: simulationConfig.baseStationSelectionAlgorithm,
      resourceAllocationAlgorithm: simulationConfig.resourceAllocationAlgorithm,
      useCustomPolicy: simulationConfig.useCustomPolicy
    }),
    [averageLatency, congestionScore, simulationConfig]
  );
  const analysisSummary = useMemo(
    () => ({
      environment,
      selectedAlgorithms: {
        route_algorithm: simulationConfig.routeAlgorithm,
        latency_algorithm: simulationConfig.latencyAlgorithm,
        base_station_selection_algorithm: simulationConfig.baseStationSelectionAlgorithm,
        resource_allocation_algorithm: simulationConfig.resourceAllocationAlgorithm
      },
      lookahead_k: simulationConfig.lookaheadK,
      custom_policy_enabled: simulationConfig.useCustomPolicy,
      summary_text: `${formatOptionLabel(simulationConfig.routeAlgorithm)} · ${formatOptionLabel(
        simulationConfig.latencyAlgorithm
      )} · ${formatOptionLabel(simulationConfig.baseStationSelectionAlgorithm)}`
    }),
    [environment, simulationConfig]
  );
  const handleProjectionChange = useCallback(
    (
      nextStationPoints: Record<string, ProjectedPoint>,
      nextUserPoints: Record<string, ProjectedPoint>
    ) => {
      setStationPoints((currentPoints) =>
        currentPoints === nextStationPoints ? currentPoints : nextStationPoints
      );
      setUserPoints((currentPoints) =>
        currentPoints === nextUserPoints ? currentPoints : nextUserPoints
      );
    },
    []
  );

  const addEvent = useCallback((message: string, tone: EventLog["tone"] = "info") => {
    setEvents((currentEvents) => [
      { id: Date.now(), message, tone },
      ...currentEvents.slice(0, 4)
    ]);
  }, []);

  useEffect(() => {
    setUsers(getSeedUsers(environment));
    addEvent(
      environment === "research"
        ? "Abstract 6G-like research mode enabled"
        : "Current wireless environment enabled",
      environment === "research" ? "warning" : "success"
    );
  }, [addEvent, environment]);

  useEffect(() => {
    if (!isRunning) {
      return;
    }

    const interval = window.setInterval(() => {
      setTick((currentTick) => currentTick + 1);
    }, environment === "research" ? 850 : 1200);

    return () => window.clearInterval(interval);
  }, [environment, isRunning]);

  useEffect(() => {
    setUsers(getNextUserPositions({ config: simulationConfig, environment, stations, tick }));

    if (tick > 0 && tick % 8 === 0) {
      addEvent(
        `Tick ${tick}: latency ${simulationMetrics.averageLatencyMs} ms, congestion ${simulationMetrics.congestionScore}% · ${formatOptionLabel(simulationConfig.routeAlgorithm)} · ${formatOptionLabel(simulationConfig.baseStationSelectionAlgorithm)}`
      );
    }
  }, [addEvent, environment, simulationConfig, simulationMetrics, stations, tick]);

  useEffect(() => {
    addEvent(`Config updated: ${analysisSummary.summary_text}`, "info");
  }, [addEvent, analysisSummary]);

  async function syncPublicData() {
    try {
      const response = await fetch(`${apiBaseUrl}/base-stations/sync-public-data`, {
        method: "POST"
      });

      if (!response.ok) {
        throw new Error("Public data sync failed");
      }

      const payload = (await response.json()) as Record<string, unknown>[];
      const mappedStations = payload
        .map((station, index) => mapPublicStation(station, index))
        .filter((station): station is BaseStation => station !== null);

      if (mappedStations.length === 0) {
        throw new Error("No usable public station records");
      }

      setStations(mappedStations);
      addEvent(`Synced ${mappedStations.length} public wireless stations`, "success");
    } catch {
      setStations(mockBaseStations);
      addEvent("Public data unavailable; using local mock stations", "warning");
    }
  }

  function generateSyntheticStations() {
    const syntheticStations = createSyntheticStations(stations.length);
    setStations((currentStations) => [...currentStations, ...syntheticStations]);
    addEvent(`Generated ${syntheticStations.length} synthetic stations`, "success");
  }

  return (
    <div className="relative h-full min-h-[480px] overflow-hidden bg-[#07111f]">
      <VWorld2DMapView
        baseStations={stations}
        center={seoulCenter}
        onProjectionChange={handleProjectionChange}
        users={users}
      />

      <div className="absolute left-4 right-4 top-4 z-[60] rounded-md border border-[#1F2937] bg-[#050816]/88 p-3 shadow-[0_16px_40px_rgba(0,0,0,0.35)] backdrop-blur">
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              ["current", "Current Wireless"],
              ["research", "6G-like Research"]
            ] as const
          ).map(([value, label]) => (
            <button
              className={`rounded-sm px-3 py-2 text-xs font-semibold transition ${
                environment === value
                  ? "bg-cyan-400/18 text-cyan-100 shadow-[0_0_22px_rgba(34,211,238,0.18)]"
                  : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
              }`}
              key={value}
              onClick={() => setEnvironment(value)}
              type="button"
            >
              {label}
            </button>
          ))}

          <input
            className="min-w-44 flex-1 rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-400/50"
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search stations"
            value={searchQuery}
          />

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                routeAlgorithm: event.target.value as RouteAlgorithm
              }))
            }
            value={simulationConfig.routeAlgorithm}
          >
            {routeAlgorithmOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                latencyAlgorithm: event.target.value as LatencyAlgorithm
              }))
            }
            value={simulationConfig.latencyAlgorithm}
          >
            {latencyAlgorithmOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                baseStationSelectionAlgorithm: event.target.value as BaseStationSelectionAlgorithm
              }))
            }
            value={simulationConfig.baseStationSelectionAlgorithm}
          >
            {baseStationSelectionOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                resourceAllocationAlgorithm: event.target.value as ResourceAllocationAlgorithm
              }))
            }
            value={simulationConfig.resourceAllocationAlgorithm}
          >
            {resourceAllocationOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                lookaheadK: Number(event.target.value)
              }))
            }
            value={simulationConfig.lookaheadK}
          >
            {lookaheadOptions.map((value) => (
              <option key={value} value={value}>
                K {value}
              </option>
            ))}
          </select>

          <select
            className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 text-xs text-slate-100 outline-none"
            onChange={(event) =>
              setSimulationConfig((current) => ({
                ...current,
                useCustomPolicy: event.target.value === "enabled"
              }))
            }
            value={simulationConfig.useCustomPolicy ? "enabled" : "disabled"}
          >
            <option value="disabled">Policy Off</option>
            <option value="enabled">Policy On</option>
          </select>

          <button
            className="rounded-sm border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-100 hover:bg-cyan-400/16"
            onClick={syncPublicData}
            type="button"
          >
            Sync Public Data
          </button>
          <button
            className="rounded-sm border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs font-semibold text-emerald-100 hover:bg-emerald-400/16"
            onClick={generateSyntheticStations}
            type="button"
          >
            Generate Synthetic Stations
          </button>
          <button
            className="rounded-sm border border-blue-400/30 bg-blue-400/10 px-3 py-2 text-xs font-semibold text-blue-100 hover:bg-blue-400/16"
            onClick={() => {
              setIsRunning(true);
              addEvent(
                `Simulation started · ${analysisSummary.summary_text} · K ${simulationConfig.lookaheadK}`,
                "success"
              );
            }}
            type="button"
          >
            Start Simulation
          </button>
          <button
            className="rounded-sm border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs font-semibold text-red-100 hover:bg-red-400/16"
            onClick={() => {
              setIsRunning(false);
              addEvent("Simulation stopped", "warning");
            }}
            type="button"
          >
            Stop Simulation
          </button>
          <span className="rounded-sm border border-[#1F2937] bg-[#07111f] px-3 py-2 font-mono text-xs text-slate-300">
            tick {tick}
          </span>
        </div>
      </div>

      <svg className="pointer-events-none absolute inset-0 z-40 h-full w-full">
        {users.map((user) => {
          const station = stationsById.get(user.connectedTo);
          const stationPoint = station ? stationPoints[station.id] : undefined;
          const userPoint = userPoints[user.id];

          return station &&
            visibleStationIds.has(station.id) &&
            stationPoint &&
            userPoint ? (
            <ConnectionLine from={stationPoint} key={`${user.id}-${station.id}`} to={userPoint} />
          ) : null;
        })}
      </svg>

      <div className="pointer-events-none absolute inset-0 z-50">
        {visibleStations.map((station) =>
          stationPoints[station.id] ? (
            <BaseStationMarker
              key={station.id}
              point={stationPoints[station.id]}
              station={station}
            />
          ) : null
        )}

        {users.map((user) =>
          userPoints[user.id] ? <UserMarker key={user.id} point={userPoints[user.id]} user={user} /> : null
        )}
      </div>

      <div className="pointer-events-none absolute inset-0 z-30 bg-[linear-gradient(rgba(34,211,238,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,0.08)_1px,transparent_1px)] bg-[size:44px_44px] mix-blend-screen" />
      <div className="pointer-events-none absolute inset-0 z-30 ring-1 ring-inset ring-cyan-300/10" />

      <div className="pointer-events-none absolute bottom-5 left-5 right-5 z-[60] grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div
          className="rounded-md border border-[#1F2937] bg-[#050816]/82 p-4 backdrop-blur"
          data-simulation-metrics={JSON.stringify(simulationMetrics)}
        >
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Stable 2D Mode</p>
          <p className="mt-1 text-sm text-slate-300">
            VWorld public spatial map with realtime mock wireless simulation.
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-sm bg-cyan-400/10 px-2 py-1 text-cyan-300">
              {visibleStations.length}/{stations.length} stations
            </span>
            <span className="rounded-sm bg-emerald-400/10 px-2 py-1 text-emerald-300">
              {users.length} users
            </span>
            <span className="rounded-sm bg-amber-400/10 px-2 py-1 text-amber-300">
              latency {averageLatency} ms
            </span>
            <span className="rounded-sm bg-red-400/10 px-2 py-1 text-red-300">
              congestion {congestionScore}%
            </span>
          </div>
        </div>

        <div
          className="rounded-md border border-[#1F2937] bg-[#050816]/82 p-4 font-mono text-xs backdrop-blur"
          data-analysis-summary={JSON.stringify(analysisSummary)}
        >
          <p className="mb-3 uppercase tracking-[0.18em] text-slate-500">Realtime Events</p>
          <div className="space-y-2">
            {events.slice(0, 4).map((event) => (
              <p
                className={
                  event.tone === "warning"
                    ? "text-amber-300"
                    : event.tone === "success"
                      ? "text-emerald-300"
                      : "text-slate-300"
                }
                key={event.id}
              >
                {event.message}
              </p>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
