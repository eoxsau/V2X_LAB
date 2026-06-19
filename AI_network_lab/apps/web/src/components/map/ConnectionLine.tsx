"use client";

import type { ProjectedPoint } from "./map-data";

export function ConnectionLine({
  from,
  to
}: Readonly<{
  from: ProjectedPoint;
  to: ProjectedPoint;
}>) {
  if (!from.visible || !to.visible) {
    return null;
  }

  return (
    <line
      stroke="#38bdf8"
      strokeDasharray="2 5"
      strokeLinecap="round"
      strokeOpacity={0.48}
      strokeWidth={1.4}
      x1={from.x}
      x2={to.x}
      y1={from.y}
      y2={to.y}
    />
  );
}
