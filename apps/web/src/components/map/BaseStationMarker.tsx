import { project } from "./map-utils";
import type { BaseStation, MapViewport } from "./map-types";

export function BaseStationMarker({
  station,
  viewport
}: Readonly<{
  station: BaseStation;
  viewport: MapViewport;
}>) {
  const point = project([station.latitude, station.longitude], viewport);
  const tone =
    station.congestion_score > 78
      ? "border-red-300 bg-red-400/20 text-red-200"
      : "border-cyan-300 bg-cyan-400/20 text-cyan-100";

  return (
    <div className="group absolute -translate-x-1/2 -translate-y-1/2" style={{ left: point.x, top: point.y }}>
      <div
        className={`grid size-10 place-items-center rounded-full border-2 shadow-[0_0_28px_rgba(34,211,238,0.35)] ${tone}`}
        title={`${station.name}
lat ${station.latitude}, lng ${station.longitude}
frequency ${station.frequency ?? "n/a"} MHz
tx ${station.tx_power ?? "n/a"} dBm
capacity ${station.capacity}
connected ${station.current_connected_vehicles}
congestion ${station.congestion_score}
source ${station.source}`}
      >
        <span className="size-2.5 rounded-full bg-current" />
      </div>
      <div className="mt-1 rounded-sm bg-[#050816]/85 px-2 py-0.5 font-mono text-[10px] text-cyan-100">
        {station.id}
      </div>
      <div className="pointer-events-none absolute left-8 top-0 z-20 w-64 rounded-md border border-cyan-300/30 bg-[#050816]/95 p-3 text-[11px] text-slate-200 opacity-0 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur transition-opacity group-hover:opacity-100">
        <p className="font-semibold text-cyan-100">{station.name}</p>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-slate-300">
          <span>lat</span>
          <span className="text-right">{station.latitude}</span>
          <span>lng</span>
          <span className="text-right">{station.longitude}</span>
          <span>frequency</span>
          <span className="text-right">{station.frequency ?? "n/a"} MHz</span>
          <span>tx_power</span>
          <span className="text-right">{station.tx_power ?? "n/a"} dBm</span>
          <span>capacity</span>
          <span className="text-right">{station.capacity}</span>
          <span>connected</span>
          <span className="text-right">{station.current_connected_vehicles}</span>
          <span>congestion</span>
          <span className="text-right">{station.congestion_score}</span>
          <span>source</span>
          <span className="text-right">{station.source}</span>
        </div>
      </div>
    </div>
  );
}
