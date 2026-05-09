import { project } from "./map-utils";
import type { MapViewport, VehicleRelationship } from "./map-types";

function riskStroke(risk: string) {
  if (risk === "high") {
    return "#f97316";
  }
  if (risk === "medium") {
    return "#fbbf24";
  }
  return "#67e8f9";
}

export function VehicleRelationshipLine({
  relationship,
  viewport
}: Readonly<{
  relationship: VehicleRelationship;
  viewport: MapViewport;
}>) {
  const [start, end] = relationship.points.map((point) => project(point, viewport));
  const midX = (start.x + end.x) / 2;
  const midY = (start.y + end.y) / 2;
  const stroke = riskStroke(relationship.risk_level);

  return (
    <g>
      <line
        stroke={stroke}
        strokeDasharray="5 7"
        strokeLinecap="round"
        strokeOpacity="0.68"
        strokeWidth={relationship.risk_level === "high" ? 2.2 : 1.35}
        x1={start.x}
        x2={end.x}
        y1={start.y}
        y2={end.y}
      />
      <foreignObject height="54" width="142" x={midX - 71} y={midY - 27}>
        <div className="rounded-md border border-cyan-300/25 bg-[#050816]/88 px-2 py-1 text-center font-mono text-[10px] leading-4 text-cyan-50 shadow-[0_10px_28px_rgba(0,0,0,0.38)] backdrop-blur">
          <div>{Math.round(relationship.distance_to_ego * 1000)} m</div>
          <div className="text-slate-300">
            Δv {relationship.relative_speed > 0 ? "+" : ""}
            {relationship.relative_speed} km/h · Δθ {relationship.heading_difference}°
          </div>
          <div style={{ color: stroke }}>risk {relationship.risk_level}</div>
        </div>
      </foreignObject>
    </g>
  );
}
