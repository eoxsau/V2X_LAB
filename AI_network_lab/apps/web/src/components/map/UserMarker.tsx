"use client";

import type { ProjectedPoint, UserMarkerData } from "./map-data";

export function UserMarker({
  point,
  user
}: Readonly<{
  point: ProjectedPoint;
  user: UserMarkerData;
}>) {
  if (!point.visible) {
    return null;
  }

  return (
    <div
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={{ left: point.x, top: point.y }}
      title={`${user.id} connected to ${user.connectedTo}`}
    >
      <div className="relative grid size-7 place-items-center rounded-full border-2 border-white bg-emerald-400/25 shadow-[0_0_24px_rgba(16,185,129,0.46)] transition-transform duration-700">
        <span className="absolute -inset-2 rounded-full border border-emerald-300/20" />
        <span className="size-2 rounded-full bg-white" />
      </div>
    </div>
  );
}
