import { project } from "./map-utils";
import type { MapViewport, NetworkNode, Vehicle } from "./map-types";

export function NetworkConnectionLine({
  node,
  vehicle,
  viewport
}: Readonly<{
  node: NetworkNode;
  vehicle: Vehicle;
  viewport: MapViewport;
}>) {
  const nodePoint = project([node.latitude, node.longitude], viewport);
  const vehiclePoint = project([vehicle.latitude, vehicle.longitude], viewport);
  const isCurrent = node.id === vehicle.connected_network_node_id;

  return (
    <line
      stroke={isCurrent ? "#22d3ee" : "#94a3b8"}
      strokeDasharray={isCurrent ? "1 0" : "4 8"}
      strokeLinecap="round"
      strokeOpacity={isCurrent ? "0.76" : "0.36"}
      strokeWidth={isCurrent ? "2.5" : "1.25"}
      x1={nodePoint.x}
      x2={vehiclePoint.x}
      y1={nodePoint.y}
      y2={vehiclePoint.y}
    />
  );
}
