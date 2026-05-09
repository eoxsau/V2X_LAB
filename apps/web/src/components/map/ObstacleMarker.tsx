import { project } from "./map-utils";
import type { MapViewport, ObstacleZone } from "./map-types";

const typeLabels: Record<ObstacleZone["type"], string> = {
  accident_zone: "ACC",
  building_zone: "BLD",
  construction_zone: "CON",
  low_visibility_zone: "VIS",
  network_blockage_zone: "NET"
};

export function ObstacleMarker({
  obstacle,
  viewport
}: Readonly<{
  obstacle: ObstacleZone;
  viewport: MapViewport;
}>) {
  const point = project([obstacle.latitude, obstacle.longitude], viewport);
  const radius = obstacle.radius_m * 2 ** (viewport.zoom - 14);
  const isHighSeverity = obstacle.severity >= 0.7;
  const zoneColor = isHighSeverity ? "#ef4444" : "#f97316";
  const affectedVehicles = obstacle.affected_vehicle_ids ?? [];

  return (
    <>
      <div
        className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border"
        style={{
          backgroundColor: isHighSeverity ? "rgba(239,68,68,0.16)" : "rgba(249,115,22,0.14)",
          borderColor: isHighSeverity ? "rgba(252,165,165,0.52)" : "rgba(251,146,60,0.48)",
          height: radius * 2,
          left: point.x,
          top: point.y,
          width: radius * 2
        }}
      />
      <div
        className="group absolute grid min-w-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-md border px-2 py-1 font-mono text-[10px] shadow-[0_0_24px_rgba(248,113,113,0.3)]"
        style={{ left: point.x, top: point.y }}
        title={`${obstacle.type}
severity ${obstacle.severity}
radius ${obstacle.radius_m} m
affected ${affectedVehicles.length ? affectedVehicles.join(", ") : "none"}
source ${obstacle.source}`}
      >
        <span
          className="rounded-sm px-1.5 py-0.5 text-white"
          style={{ backgroundColor: zoneColor }}
        >
          {typeLabels[obstacle.type]} {Math.round(obstacle.severity * 100)}
        </span>
        {affectedVehicles.length ? (
          <span className="mt-1 rounded-sm bg-[#050816]/85 px-1.5 py-0.5 text-[9px] text-red-100">
            affects {affectedVehicles.join(", ")}
          </span>
        ) : null}
      </div>
    </>
  );
}
