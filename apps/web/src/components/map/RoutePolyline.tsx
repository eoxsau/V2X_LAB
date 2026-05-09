import { routePoints } from "./map-utils";
import type { MapViewport, RoutePath } from "./map-types";

export function RoutePolyline({
  route,
  tone,
  viewport
}: Readonly<{
  route: RoutePath;
  tone: "current" | "ai" | "shortest" | "traffic" | "risky";
  viewport: MapViewport;
}>) {
  const style = {
    ai: { dash: "0", opacity: 0.96, stroke: "#22d3ee", width: 5 },
    current: { dash: "0", opacity: 0.58, stroke: "#64748b", width: 5 },
    risky: { dash: "0", opacity: 0.94, stroke: "#f97316", width: 7 },
    shortest: { dash: "8 8", opacity: 0.78, stroke: "#60a5fa", width: 4 },
    traffic: { dash: "4 8", opacity: 0.68, stroke: "#f59e0b", width: 4 }
  }[tone];

  return (
    <polyline
      fill="none"
      points={routePoints(route.points, viewport)}
      stroke={style.stroke}
      strokeDasharray={style.dash}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeOpacity={style.opacity}
      strokeWidth={style.width}
    />
  );
}
