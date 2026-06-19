"use client";

import type { BaseStation, ProjectedPoint } from "./map-data";

const statusClasses = {
  danger: "border-red-300 bg-red-400/20 text-red-200 shadow-red-400/30",
  stable: "border-cyan-300 bg-cyan-400/20 text-cyan-100 shadow-cyan-400/30",
  warning: "border-amber-300 bg-amber-400/20 text-amber-100 shadow-amber-400/30"
};

export function BaseStationMarker({
  point,
  station
}: Readonly<{
  point: ProjectedPoint;
  station: BaseStation;
}>) {
  if (!point.visible) {
    return null;
  }

  return (
    <div
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={{ left: point.x, top: point.y }}
    >
      <div
        className={`grid size-9 place-items-center rounded-full border-2 shadow-[0_0_28px] backdrop-blur ${statusClasses[station.status]}`}
        title={`${station.id} ${station.label}`}
      >
        <span className="size-2.5 rounded-full bg-current shadow-[0_0_18px_currentColor]" />
      </div>
      <div className="mt-1 rounded-sm border border-[#1F2937] bg-[#050816]/80 px-2 py-0.5 text-center font-mono text-[10px] text-cyan-100">
        {station.id}
      </div>
    </div>
  );
}
