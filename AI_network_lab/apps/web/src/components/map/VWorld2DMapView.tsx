"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  TILE_SIZE,
  latLngToWorldPixel,
  normalizeTileX,
  pixelToLatLng,
  projectToViewport,
  type BaseStation,
  type LatLng,
  type ProjectedPoint,
  type UserMarkerData
} from "./map-data";

type Tile = {
  key: string;
  left: number;
  top: number;
  url: string;
};

const VWORLD_API_KEY = process.env.NEXT_PUBLIC_VWORLD_API_KEY;
const INITIAL_ZOOM = 14;
const MIN_ZOOM = 8;
const MAX_ZOOM = 18;

function clampZoom(zoom: number) {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
}

function areProjectedPointsEqual(
  currentPoints: Record<string, ProjectedPoint>,
  nextPoints: Record<string, ProjectedPoint>
) {
  const currentKeys = Object.keys(currentPoints);
  const nextKeys = Object.keys(nextPoints);

  if (currentKeys.length !== nextKeys.length) {
    return false;
  }

  return nextKeys.every((key) => {
    const currentPoint = currentPoints[key];
    const nextPoint = nextPoints[key];

    return (
      currentPoint !== undefined &&
      nextPoint !== undefined &&
      currentPoint.visible === nextPoint.visible &&
      Math.abs(currentPoint.x - nextPoint.x) < 0.1 &&
      Math.abs(currentPoint.y - nextPoint.y) < 0.1
    );
  });
}

function getTiles(
  center: LatLng,
  zoom: number,
  size: { height: number; width: number },
  dragOffset: { x: number; y: number }
) {
  if (!VWORLD_API_KEY || size.width === 0 || size.height === 0) {
    return [];
  }

  const centerPixel = latLngToWorldPixel(center, zoom);
  const topLeft = {
    x: centerPixel.x - size.width / 2 - dragOffset.x,
    y: centerPixel.y - size.height / 2 - dragOffset.y
  };
  const startX = Math.floor(topLeft.x / TILE_SIZE);
  const startY = Math.floor(topLeft.y / TILE_SIZE);
  const endX = Math.floor((topLeft.x + size.width) / TILE_SIZE);
  const endY = Math.floor((topLeft.y + size.height) / TILE_SIZE);
  const maxTile = 2 ** zoom;
  const tiles: Tile[] = [];

  for (let tileX = startX; tileX <= endX; tileX += 1) {
    for (let tileY = startY; tileY <= endY; tileY += 1) {
      if (tileY < 0 || tileY >= maxTile) {
        continue;
      }

      const normalizedX = normalizeTileX(tileX, zoom);
      tiles.push({
        key: `${zoom}-${normalizedX}-${tileY}`,
        left: tileX * TILE_SIZE - topLeft.x,
        top: tileY * TILE_SIZE - topLeft.y,
        url: `https://api.vworld.kr/req/wmts/1.0.0/${VWORLD_API_KEY}/Base/${zoom}/${tileY}/${normalizedX}.png`
      });
    }
  }

  return tiles;
}

export function VWorld2DMapView({
  baseStations,
  center,
  onProjectionChange,
  users
}: Readonly<{
  baseStations: BaseStation[];
  center: LatLng;
  onProjectionChange: (
    stationPoints: Record<string, ProjectedPoint>,
    userPoints: Record<string, ProjectedPoint>
  ) => void;
  users: UserMarkerData[];
}>) {
  const mapRef = useRef<HTMLDivElement | null>(null);
  const dragStartRef = useRef<{ center: LatLng; x: number; y: number } | null>(null);
  const lastStationPointsRef = useRef<Record<string, ProjectedPoint>>({});
  const lastUserPointsRef = useRef<Record<string, ProjectedPoint>>({});
  const [mapCenter, setMapCenter] = useState(center);
  const [zoom, setZoom] = useState(INITIAL_ZOOM);
  const [size, setSize] = useState({ height: 0, width: 0 });
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });

  useEffect(() => {
    if (!mapRef.current) {
      return;
    }

    const resizeObserver = new ResizeObserver(([entry]) => {
      const { height, width } = entry.contentRect;
      setSize({ height, width });
    });

    resizeObserver.observe(mapRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  const project = useCallback(
    (position: LatLng) => projectToViewport(position, mapCenter, zoom, size, dragOffset),
    [dragOffset, mapCenter, size, zoom]
  );

  useEffect(() => {
    const stationPoints = Object.fromEntries(
      baseStations.map((station) => [station.id, project(station.position)])
    );
    const userPoints = Object.fromEntries(users.map((user) => [user.id, project(user.position)]));

    const resolvedStationPoints = areProjectedPointsEqual(lastStationPointsRef.current, stationPoints)
      ? lastStationPointsRef.current
      : stationPoints;
    const resolvedUserPoints = areProjectedPointsEqual(lastUserPointsRef.current, userPoints)
      ? lastUserPointsRef.current
      : userPoints;

    lastStationPointsRef.current = resolvedStationPoints;
    lastUserPointsRef.current = resolvedUserPoints;
    onProjectionChange(resolvedStationPoints, resolvedUserPoints);
  }, [baseStations, onProjectionChange, project, users]);

  const tiles = useMemo(
    () => getTiles(mapCenter, zoom, size, dragOffset),
    [dragOffset, mapCenter, size, zoom]
  );

  function commitDrag(nextOffset = dragOffset) {
    if (!dragStartRef.current) {
      return;
    }

    const startCenterPixel = latLngToWorldPixel(dragStartRef.current.center, zoom);
    setMapCenter(pixelToLatLng(
      {
        x: startCenterPixel.x - nextOffset.x,
        y: startCenterPixel.y - nextOffset.y
      },
      zoom
    ));
    setDragOffset({ x: 0, y: 0 });
    dragStartRef.current = null;
  }

  return (
    <div
      className="absolute inset-0 cursor-grab overflow-hidden bg-[#07111f] active:cursor-grabbing"
      data-testid="vworld-2d-map"
      onMouseDown={(event) => {
        dragStartRef.current = {
          center: mapCenter,
          x: event.clientX,
          y: event.clientY
        };
      }}
      onMouseLeave={() => commitDrag()}
      onMouseMove={(event) => {
        if (!dragStartRef.current) {
          return;
        }

        setDragOffset({
          x: event.clientX - dragStartRef.current.x,
          y: event.clientY - dragStartRef.current.y
        });
      }}
      onMouseUp={() => commitDrag()}
      onWheel={(event) => {
        event.preventDefault();
        setZoom((currentZoom) => clampZoom(currentZoom + (event.deltaY < 0 ? 1 : -1)));
        setDragOffset({ x: 0, y: 0 });
      }}
      ref={mapRef}
    >
      {tiles.map((tile) => (
        <img
          alt=""
          className="absolute h-64 w-64 select-none object-cover opacity-90"
          draggable={false}
          key={tile.key}
          src={tile.url}
          style={{ left: tile.left, top: tile.top }}
        />
      ))}

      {!VWORLD_API_KEY ? (
        <div className="absolute inset-0 z-20 grid place-items-center bg-[#07111f]">
          <div className="rounded-md border border-[#1F2937] bg-[#050816]/94 px-5 py-4 text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">
              VWorld key missing
            </p>
            <p className="mt-2 text-sm text-slate-300">
              Set NEXT_PUBLIC_VWORLD_API_KEY in apps/web/.env.local.
            </p>
          </div>
        </div>
      ) : null}

      <div className="absolute left-5 top-5 rounded-md border border-[#1F2937] bg-[#050816]/80 px-3 py-2 font-mono text-xs text-cyan-100 backdrop-blur">
        Seoul / zoom {zoom}
      </div>

      <div className="absolute bottom-24 right-5 flex flex-col overflow-hidden rounded-md border border-[#1F2937] bg-[#050816]/86 backdrop-blur">
        <button
          className="px-3 py-2 text-cyan-100 hover:bg-cyan-400/10"
          onClick={() => setZoom((currentZoom) => clampZoom(currentZoom + 1))}
          type="button"
        >
          +
        </button>
        <button
          className="border-t border-[#1F2937] px-3 py-2 text-cyan-100 hover:bg-cyan-400/10"
          onClick={() => setZoom((currentZoom) => clampZoom(currentZoom - 1))}
          type="button"
        >
          -
        </button>
      </div>
    </div>
  );
}
