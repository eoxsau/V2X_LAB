import { project } from "./map-utils";
import type { MapViewport, Vehicle } from "./map-types";

export function VehicleMarker({
  tick,
  vehicle,
  vehicleIndex,
  viewport
}: Readonly<{
  tick: number;
  vehicle: Vehicle;
  vehicleIndex: number;
  viewport: MapViewport;
}>) {
  const basePoint = project([vehicle.latitude, vehicle.longitude], viewport);
  const point = {
    x: basePoint.x + Math.sin(tick / 3 + vehicleIndex) * 18,
    y: basePoint.y + Math.cos(tick / 4 + vehicleIndex) * 14
  };
  const isAffected = vehicle.obstacle_risk >= 0.55 || Boolean(vehicle.blocking_obstacles?.length);
  const isEgo = vehicle.vehicle_type === "ego_vehicle" || vehicleIndex === 0;
  const markerSize = isEgo ? "size-12" : "size-7";
  const markerTone = isEgo
    ? "border-cyan-100 bg-cyan-300/30 shadow-[0_0_42px_rgba(34,211,238,0.82)]"
    : isAffected
      ? "border-orange-200 bg-emerald-400/25 shadow-[0_0_28px_rgba(249,115,22,0.62)]"
      : "border-white/80 bg-slate-200/15 shadow-[0_0_20px_rgba(148,163,184,0.32)]";

  return (
    <div
      className={`group absolute grid ${markerSize} -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 transition-all duration-700 ${markerTone}`}
      style={{ left: point.x, top: point.y }}
      title={`${vehicle.id}
speed ${vehicle.speed} km/h
route ${vehicle.current_route_id}
connected ${vehicle.connected_base_station_id}
distance ${vehicle.distance_to_connected_base_station_km} km
latency ${vehicle.current_latency_ms} ms
obstacle risk ${vehicle.obstacle_risk}
network blockage ${vehicle.network_blockage_risk ?? 0}
status ${vehicle.route_status}`}
    >
      <span className={`${isEgo ? "size-3 bg-cyan-50" : "size-2 bg-white"} rounded-full`} />
      {isEgo ? (
        <span className="absolute -bottom-6 rounded-md border border-cyan-300/35 bg-[#050816]/90 px-2 py-0.5 font-mono text-[10px] font-semibold text-cyan-100">
          EGO
        </span>
      ) : null}
      <div className="pointer-events-none absolute left-7 top-0 z-20 w-72 rounded-md border border-emerald-300/30 bg-[#050816]/95 p-3 text-[11px] text-slate-200 opacity-0 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur transition-opacity group-hover:opacity-100">
        <p className={`font-semibold ${isEgo ? "text-cyan-100" : "text-emerald-100"}`}>{isEgo ? "Ego Vehicle" : vehicle.vehicle_type ?? "surrounding_vehicle"} · {vehicle.id}</p>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-slate-300">
          <span>speed</span>
          <span className="text-right">{vehicle.speed} km/h</span>
          <span>route</span>
          <span className="text-right">{vehicle.current_route_id}</span>
          <span>connected BS</span>
          <span className="text-right">{vehicle.connected_base_station_id}</span>
          <span>BS distance</span>
          <span className="text-right">{vehicle.distance_to_connected_base_station_km} km</span>
          <span>latency</span>
          <span className="text-right">{vehicle.current_latency_ms} ms</span>
          <span>obstacle risk</span>
          <span className="text-right">{vehicle.obstacle_risk}</span>
          <span>blockage risk</span>
          <span className="text-right">{vehicle.network_blockage_risk ?? 0}</span>
          <span>route status</span>
          <span className="text-right">{vehicle.route_status}</span>
          {vehicle.distance_to_ego !== undefined ? (
            <>
              <span>distance to ego</span>
              <span className="text-right">{Math.round(vehicle.distance_to_ego * 1000)} m</span>
              <span>relative speed</span>
              <span className="text-right">{vehicle.relative_speed} km/h</span>
              <span>heading delta</span>
              <span className="text-right">{vehicle.heading_difference}°</span>
              <span>proximity risk</span>
              <span className="text-right">{vehicle.risk_level}</span>
            </>
          ) : null}
          {isEgo ? (
            <>
              <span>nearest vehicle</span>
              <span className="text-right">{vehicle.nearest_vehicle_distance ? `${Math.round(vehicle.nearest_vehicle_distance * 1000)} m` : "n/a"}</span>
              <span>collision risk</span>
              <span className="text-right">{vehicle.collision_proximity_risk ?? 0}</span>
              <span>network stability</span>
              <span className="text-right">{vehicle.network_stability_score ?? "n/a"}</span>
            </>
          ) : null}
        </div>
        {vehicle.blocking_obstacles?.length ? (
          <div className="mt-3 rounded-sm border border-orange-300/30 bg-orange-400/10 px-2 py-1 text-orange-100">
            Link affected by {vehicle.blocking_obstacles.map((item) => item.id).join(", ")}
          </div>
        ) : null}
      </div>
    </div>
  );
}
