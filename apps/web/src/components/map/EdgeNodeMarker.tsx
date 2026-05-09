import { project } from "./map-utils";
import type { EdgeNode, MapViewport } from "./map-types";

export function EdgeNodeMarker({
  edgeNode,
  viewport
}: Readonly<{
  edgeNode: EdgeNode;
  viewport: MapViewport;
}>) {
  const point = project([edgeNode.latitude, edgeNode.longitude], viewport);

  return (
    <div
      className="absolute -translate-x-1/2 -translate-y-1/2 rounded-sm border border-blue-300 bg-blue-400/20 px-2 py-1 font-mono text-[10px] text-blue-100 shadow-[0_0_20px_rgba(96,165,250,0.24)]"
      style={{ left: point.x, top: point.y }}
      title={`${edgeNode.name}
capacity ${edgeNode.capacity}
compute load ${Math.round(edgeNode.compute_load * 100)}%
edge latency ${edgeNode.edge_latency_ms} ms
source ${edgeNode.source}`}
    >
      {edgeNode.id}
    </div>
  );
}
