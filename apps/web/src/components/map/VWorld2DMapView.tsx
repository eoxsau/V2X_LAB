"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent, type WheelEvent } from "react";
import { ConnectionLine } from "./ConnectionLine";
import { CongestionOverlay } from "./CongestionOverlay";
import {
  aiRecommendedRoute as mockAiRecommendedRoute,
  baseStations as mockBaseStations,
  congestionZones as mockCongestionZones,
  currentRoute as mockCurrentRoute,
  edgeNodes as mockEdgeNodes,
  obstacles as mockObstacles,
  shortestRoute as mockShortestRoute,
  trafficAwareRoute as mockTrafficAwareRoute,
  vehicles as mockVehicles
} from "./mock-map-data";
import { center, clampZoom, latLngToPixel, pixelToLatLng, project, tileSize, zoom as defaultZoom } from "./map-utils";
import type { BaseStation, CongestionZone, EdgeNode, LatLng, MapViewport, NetworkNode, ObstacleZone, ProjectedPoint, RoutePath, SelectedMapPoint, Vehicle, VehicleRelationship } from "./map-types";
import { NetworkConnectionLine } from "./NetworkConnectionLine";
import { NetworkNodeMarker } from "./NetworkNodeMarker";
import { ObstacleMarker } from "./ObstacleMarker";
import { RoutePolyline } from "./RoutePolyline";
import { VehicleMarker } from "./VehicleMarker";
import { VehicleRelationshipLine } from "./VehicleRelationshipLine";

const apiKey = process.env.NEXT_PUBLIC_VWORLD_API_KEY;

type Tile = {
  key: string;
  left: number;
  top: number;
  url: string;
};

type DragState = {
  centerPixel: ProjectedPoint;
  clientX: number;
  clientY: number;
  pointerId: number;
  zoom: number;
};

function getTiles(viewport: MapViewport): Tile[] {
  if (!apiKey) {
    return [];
  }

  const centerPoint = latLngToPixel(viewport.center, viewport.zoom);
  const topLeft = { x: centerPoint.x - viewport.width / 2, y: centerPoint.y - viewport.height / 2 };
  const startX = Math.floor(topLeft.x / tileSize) - 1;
  const startY = Math.floor(topLeft.y / tileSize) - 1;
  const endX = Math.floor((topLeft.x + viewport.width) / tileSize) + 1;
  const endY = Math.floor((topLeft.y + viewport.height) / tileSize) + 1;
  const tiles: Tile[] = [];

  for (let x = startX; x <= endX; x += 1) {
    for (let y = startY; y <= endY; y += 1) {
      tiles.push({
        key: `${viewport.zoom}-${x}-${y}`,
        left: x * tileSize - topLeft.x,
        top: y * tileSize - topLeft.y,
        url: `https://api.vworld.kr/req/wmts/1.0.0/${apiKey}/Base/${viewport.zoom}/${y}/${x}.png`
      });
    }
  }

  return tiles;
}

export function VWorld2DMapView({
  aiRecommendedRoute = mockAiRecommendedRoute,
  baseStations = mockBaseStations,
  congestionZones = mockCongestionZones,
  currentRoute = mockCurrentRoute,
  destination,
  edgeNodes = mockEdgeNodes,
  networkNodes,
  onCoordinateSelect,
  obstacles = mockObstacles,
  origin,
  shortestRoute = mockShortestRoute,
  selectionMode,
  tick,
  trafficAwareRoute = mockTrafficAwareRoute,
  vehicleRelationships = [],
  vehicles = mockVehicles
}: Readonly<{
  aiRecommendedRoute?: RoutePath;
  baseStations?: BaseStation[];
  congestionZones?: CongestionZone[];
  currentRoute?: RoutePath;
  edgeNodes?: EdgeNode[];
  networkNodes?: NetworkNode[];
  onCoordinateSelect?: (point: SelectedMapPoint) => void;
  obstacles?: ObstacleZone[];
  origin?: SelectedMapPoint | null;
  shortestRoute?: RoutePath;
  selectionMode?: "origin" | "destination" | null;
  tick: number;
  trafficAwareRoute?: RoutePath;
  vehicleRelationships?: VehicleRelationship[];
  vehicles?: Vehicle[];
  destination?: SelectedMapPoint | null;
}>) {
  const mapRef = useRef<HTMLDivElement>(null);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [mapCenter, setMapCenter] = useState<LatLng>(center);
  const [mapZoom, setMapZoom] = useState(defaultZoom);
  const [isMounted, setIsMounted] = useState(false);
  const [tileErrorCount, setTileErrorCount] = useState(0);
  const [size, setSize] = useState({ height: 620, width: 980 });
  const viewport = useMemo(
    () => ({ center: mapCenter, height: size.height, width: size.width, zoom: mapZoom }),
    [mapCenter, mapZoom, size.height, size.width]
  );
  const tiles = useMemo(() => getTiles(viewport), [viewport]);
  const mapErrorMessage = !apiKey
    ? "VWorld API key is missing. Map overlays are still available, but public map tiles cannot load."
    : tileErrorCount >= 3
      ? "VWorld tile loading failed. Check network connectivity or VWorld service availability."
      : null;
  const visibleNetworkNodes = useMemo(
    () =>
      networkNodes ??
      [
        ...baseStations.map((station) => ({
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
        ...edgeNodes.map((edgeNode) => ({
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
      ],
    [baseStations, edgeNodes, networkNodes]
  );

  useEffect(() => {
    setIsMounted(true);
  }, []);

  useEffect(() => {
    if (!mapRef.current) {
      return;
    }

    const observer = new ResizeObserver(([entry]) => {
      const { height, width } = entry.contentRect;
      setSize({
        height: Math.max(620, Math.round(height)),
        width: Math.max(720, Math.round(width))
      });
    });

    observer.observe(mapRef.current);

    return () => observer.disconnect();
  }, []);

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }

    event.currentTarget.setPointerCapture(event.pointerId);
    setDragState({
      centerPixel: latLngToPixel(mapCenter, mapZoom),
      clientX: event.clientX,
      clientY: event.clientY,
      pointerId: event.pointerId,
      zoom: mapZoom
    });
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragState) {
      return;
    }

    const deltaX = event.clientX - dragState.clientX;
    const deltaY = event.clientY - dragState.clientY;
    setMapCenter(
      pixelToLatLng(
        {
          x: dragState.centerPixel.x - deltaX,
          y: dragState.centerPixel.y - deltaY
        },
        dragState.zoom
      )
    );
  }

  function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    if (dragState?.pointerId === event.pointerId && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    setDragState(null);
  }

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    setMapZoom((value) => clampZoom(value + (event.deltaY < 0 ? 1 : -1)));
  }

  function handleMapClick(event: MouseEvent<HTMLDivElement>) {
    if (!selectionMode || !onCoordinateSelect || !mapRef.current) {
      return;
    }
    const rect = mapRef.current.getBoundingClientRect();
    const centerPixel = latLngToPixel(mapCenter, mapZoom);
    const latLng = pixelToLatLng(
      {
        x: centerPixel.x + (event.clientX - rect.left - size.width / 2),
        y: centerPixel.y + (event.clientY - rect.top - size.height / 2),
      },
      mapZoom,
    );
    onCoordinateSelect({
      lat: Number(latLng[0].toFixed(6)),
      lng: Number(latLng[1].toFixed(6)),
      label: selectionMode === "origin" ? "Selected origin" : "Selected destination",
    });
  }

  return (
    <div
      className={`relative h-full min-h-[620px] overflow-hidden bg-[#07111f] ${dragState ? "cursor-grabbing" : "cursor-grab"}`}
      data-testid="vworld-2d-map-view"
      onPointerCancel={handlePointerUp}
      onPointerDown={handlePointerDown}
      onPointerLeave={handlePointerUp}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onClick={handleMapClick}
      onWheel={handleWheel}
      ref={mapRef}
    >
      {!isMounted ? (
        <div className="absolute inset-0 bg-[linear-gradient(rgba(34,211,238,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,0.08)_1px,transparent_1px)] bg-[size:44px_44px]">
          <div className="absolute inset-0 bg-[#050816]" />
          <div className="absolute left-4 top-4 rounded-md border border-[#1F2937] bg-[#050816]/82 px-3 py-2 text-xs text-cyan-100">
            Initializing VWorld 2D public spatial layer
          </div>
        </div>
      ) : null}

      {isMounted ? (
        <>
      {mapErrorMessage ? (
        <div className="absolute inset-x-4 top-16 z-50 rounded-lg border border-red-300/35 bg-red-500/12 px-4 py-3 text-sm text-red-100 shadow-[0_18px_45px_rgba(0,0,0,0.32)] backdrop-blur">
          <p className="font-semibold">VWorld 2D map error state</p>
          <p className="mt-1 text-xs text-red-100/80">{mapErrorMessage}</p>
        </div>
      ) : null}
      {tiles.map((tile) => (
        <img
          alt=""
          className="absolute h-64 w-64 select-none object-cover opacity-80 saturate-[0.85]"
          draggable={false}
          key={tile.key}
          onError={() => setTileErrorCount((value) => Math.min(value + 1, 20))}
          src={tile.url}
          style={{ left: tile.left, top: tile.top }}
        />
      ))}

      <div className="absolute inset-0 bg-[#050816]/20" />
      <div className="absolute inset-0 bg-[linear-gradient(rgba(34,211,238,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(34,211,238,0.08)_1px,transparent_1px)] bg-[size:44px_44px] mix-blend-screen" />

      <svg className="absolute inset-0 h-full w-full">
        {congestionZones.map((zone) => (
          <CongestionOverlay key={zone.id} viewport={viewport} zone={zone} />
        ))}
        <RoutePolyline route={currentRoute} tone="current" viewport={viewport} />
        <RoutePolyline route={shortestRoute} tone="shortest" viewport={viewport} />
        <RoutePolyline route={trafficAwareRoute} tone="traffic" viewport={viewport} />
        <RoutePolyline route={aiRecommendedRoute} tone="ai" viewport={viewport} />
        {[...(currentRoute.riskySegments ?? []), ...(aiRecommendedRoute.riskySegments ?? [])].map((segment, index) => (
          <RoutePolyline
            key={`risky-segment-${index}`}
            route={{ id: `risky-segment-${index}`, label: "Risky segment", points: segment }}
            tone="risky"
            viewport={viewport}
          />
        ))}
        {vehicles.map((vehicle, index) => {
          const station = baseStations.find((item) => item.id === vehicle.connected_base_station_id);
          return station && !vehicle.connected_network_node_id ? (
            <ConnectionLine
              key={vehicle.id}
              station={station}
              tick={tick}
              vehicle={vehicle}
              vehicleIndex={index}
              viewport={viewport}
            />
          ) : null;
        })}
        {vehicleRelationships.map((relationship) => (
          <VehicleRelationshipLine key={relationship.id} relationship={relationship} viewport={viewport} />
        ))}
        {vehicles.slice(0, 1).flatMap((vehicle) =>
          visibleNetworkNodes
            .filter((node) => node.id === vehicle.connected_network_node_id || vehicle.alternative_node_latency_estimates?.some((item) => item.id === node.id))
            .map((node) => <NetworkConnectionLine key={`${vehicle.id}-${node.id}`} node={node} vehicle={vehicle} viewport={viewport} />)
        )}
      </svg>

      {visibleNetworkNodes.map((node) => (
        <NetworkNodeMarker key={node.id} node={node} viewport={viewport} />
      ))}
      {obstacles.map((obstacle) => (
        <ObstacleMarker key={obstacle.id} obstacle={obstacle} viewport={viewport} />
      ))}
      {origin ? <SelectedPointMarker label="Origin" point={origin} tone="origin" viewport={viewport} /> : null}
      {destination ? <SelectedPointMarker label="Destination" point={destination} tone="destination" viewport={viewport} /> : null}
      {vehicles.map((vehicle, index) => (
        <VehicleMarker key={vehicle.id} tick={tick} vehicle={vehicle} vehicleIndex={index} viewport={viewport} />
      ))}

      <div
        className="absolute right-4 top-4 z-40 flex flex-col gap-2"
        onPointerDown={(event) => event.stopPropagation()}
        onWheel={(event) => event.stopPropagation()}
      >
        <button
          aria-label="Zoom in"
          className="grid size-9 place-items-center rounded-md border border-cyan-300/30 bg-[#050816]/85 font-mono text-lg text-cyan-100 shadow-[0_10px_30px_rgba(0,0,0,0.28)] backdrop-blur hover:bg-cyan-400/15"
          onClick={() => setMapZoom((value) => clampZoom(value + 1))}
          type="button"
        >
          +
        </button>
        <button
          aria-label="Zoom out"
          className="grid size-9 place-items-center rounded-md border border-cyan-300/30 bg-[#050816]/85 font-mono text-lg text-cyan-100 shadow-[0_10px_30px_rgba(0,0,0,0.28)] backdrop-blur hover:bg-cyan-400/15"
          onClick={() => setMapZoom((value) => clampZoom(value - 1))}
          type="button"
        >
          -
        </button>
        <button
          aria-label="Reset map view"
          className="rounded-md border border-[#1F2937] bg-[#050816]/85 px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-300 backdrop-blur hover:bg-white/10"
          onClick={() => {
            setMapCenter(center);
            setMapZoom(defaultZoom);
          }}
          type="button"
        >
          reset
        </button>
      </div>

      <div className="absolute left-4 top-4 z-30 w-fit max-w-[calc(100%-6rem)] rounded-md border border-[#1F2937] bg-[#050816]/82 px-3 py-2 font-mono text-[11px] text-slate-300 backdrop-blur">
        zoom {mapZoom} · center {mapCenter[0].toFixed(4)}, {mapCenter[1].toFixed(4)} · base stations{" "}
        {baseStations.length} · realtime-ready stream
      </div>

      <div className="absolute bottom-4 left-4 rounded-md border border-[#1F2937] bg-[#050816]/82 p-3 text-xs text-slate-300 backdrop-blur">
        <span className="text-cyan-300">VWorld 2D:</span> gray current route, blue dashed shortest,
        amber traffic-aware, cyan AI-ready route candidate, orange risky segments · drag to pan, wheel to zoom
        {selectionMode ? <span className="ml-2 text-amber-200">· click map to set {selectionMode}</span> : null}
      </div>
        </>
      ) : null}
    </div>
  );
}

function SelectedPointMarker({
  label,
  point,
  tone,
  viewport
}: Readonly<{
  label: string;
  point: SelectedMapPoint;
  tone: "origin" | "destination";
  viewport: MapViewport;
}>) {
  const projected = project([point.lat, point.lng], viewport);
  const color = tone === "origin" ? "border-emerald-200 bg-emerald-400/25 text-emerald-100" : "border-red-200 bg-red-400/25 text-red-100";
  return (
    <div className="absolute z-40 -translate-x-1/2 -translate-y-1/2" style={{ left: projected.x, top: projected.y }}>
      <div className={`grid size-9 place-items-center rounded-full border-2 ${color} shadow-[0_0_28px_rgba(255,255,255,0.2)]`}>
        <span className="size-2 rounded-full bg-current" />
      </div>
      <div className="mt-1 rounded bg-[#050816]/90 px-2 py-0.5 font-mono text-[10px] text-white">{label}</div>
    </div>
  );
}
