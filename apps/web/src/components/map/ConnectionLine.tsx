import { project } from "./map-utils";
import type { BaseStation, MapViewport, Vehicle } from "./map-types";

export function ConnectionLine({
  station,
  tick,
  vehicle,
  vehicleIndex,
  viewport
}: Readonly<{
  station: BaseStation;
  tick: number;
  vehicle: Vehicle;
  vehicleIndex: number;
  viewport: MapViewport;
}>) {
  const stationPoint = project([station.latitude, station.longitude], viewport);
  const baseVehiclePoint = project([vehicle.latitude, vehicle.longitude], viewport);
  const vehiclePoint = {
    x: baseVehiclePoint.x + Math.sin(tick / 3 + vehicleIndex) * 18,
    y: baseVehiclePoint.y + Math.cos(tick / 4 + vehicleIndex) * 14
  };

  return (
    <line
      stroke="#38bdf8"
      strokeDasharray="2 6"
      strokeOpacity="0.58"
      strokeWidth="1.5"
      x1={stationPoint.x}
      x2={vehiclePoint.x}
      y1={stationPoint.y}
      y2={vehiclePoint.y}
    />
  );
}
