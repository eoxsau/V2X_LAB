import { project } from "./map-utils";
import type { MapViewport, NetworkNode } from "./map-types";

function nodeTone(nodeType: string) {
  if (nodeType === "roadside_unit") {
    return {
      dot: "bg-emerald-300",
      frame: "border-emerald-300 bg-emerald-400/20 text-emerald-100",
      label: "text-emerald-100"
    };
  }
  if (nodeType === "edge_node") {
    return {
      dot: "bg-blue-300",
      frame: "border-blue-300 bg-blue-400/20 text-blue-100",
      label: "text-blue-100"
    };
  }
  return {
    dot: "bg-cyan-300",
    frame: "border-cyan-300 bg-cyan-400/20 text-cyan-100",
    label: "text-cyan-100"
  };
}

export function NetworkNodeMarker({
  node,
  viewport
}: Readonly<{
  node: NetworkNode;
  viewport: MapViewport;
}>) {
  const point = project([node.latitude, node.longitude], viewport);
  const tone = nodeTone(node.node_type);
  const radiusPx = Math.max(22, Math.min(82, node.coverage_radius_m / 13));

  return (
    <div className="group absolute -translate-x-1/2 -translate-y-1/2" style={{ left: point.x, top: point.y }}>
      <div
        className="absolute rounded-full border border-current opacity-20"
        style={{
          height: radiusPx * 2,
          left: -radiusPx + 20,
          top: -radiusPx + 20,
          width: radiusPx * 2
        }}
      />
      <div
        className={`grid size-10 place-items-center rounded-full border-2 shadow-[0_0_28px_rgba(34,211,238,0.28)] ${tone.frame}`}
        title={`${node.name}
type ${node.node_type}
capacity ${node.capacity}
connected ${node.current_connected_vehicles}
load ${node.congestion_score}
edge latency ${node.edge_latency_ms} ms
coverage ${node.coverage_radius_m} m
source ${node.source}`}
      >
        <span className={`size-2.5 rounded-full ${tone.dot}`} />
      </div>
      <div className={`mt-1 rounded-sm bg-[#050816]/85 px-2 py-0.5 font-mono text-[10px] ${tone.label}`}>
        {node.id}
      </div>
      <div className="pointer-events-none absolute left-8 top-0 z-20 w-72 rounded-md border border-cyan-300/30 bg-[#050816]/95 p-3 text-[11px] text-slate-200 opacity-0 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur transition-opacity group-hover:opacity-100">
        <p className="font-semibold text-cyan-100">{node.name}</p>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-slate-300">
          <span>node type</span>
          <span className="text-right">{node.node_type}</span>
          <span>capacity</span>
          <span className="text-right">{node.capacity}</span>
          <span>connected</span>
          <span className="text-right">{node.current_connected_vehicles}</span>
          <span>load</span>
          <span className="text-right">{node.congestion_score}</span>
          <span>edge latency</span>
          <span className="text-right">{node.edge_latency_ms} ms</span>
          <span>coverage</span>
          <span className="text-right">{node.coverage_radius_m} m</span>
          <span>source</span>
          <span className="text-right">{node.source}</span>
        </div>
      </div>
    </div>
  );
}
