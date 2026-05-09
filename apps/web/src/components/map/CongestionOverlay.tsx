import { routePoints } from "./map-utils";
import type { CongestionZone, MapViewport } from "./map-types";

export function CongestionOverlay({
  viewport,
  zone
}: Readonly<{
  viewport: MapViewport;
  zone: CongestionZone;
}>) {
  const color = zone.label.includes("Network") ? "#60a5fa" : "#f59e0b";

  return (
    <polygon
      fill={color}
      fillOpacity={0.12 + zone.intensity * 0.16}
      points={routePoints(zone.points, viewport)}
      stroke={color}
      strokeDasharray="5 7"
      strokeOpacity={0.38}
      strokeWidth={1.4}
    />
  );
}
