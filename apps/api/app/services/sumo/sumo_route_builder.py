from __future__ import annotations

from app.services.sumo.sumo_network_loader import SumoEdge, SumoNetwork


def build_sumo_route(
    origin_match: dict[str, object],
    destination_match: dict[str, object],
    network: SumoNetwork,
    origin: dict[str, object],
    destination: dict[str, object],
) -> dict[str, object]:
    origin_edge = origin_match["edge"]
    destination_edge = destination_match["edge"]
    if not isinstance(origin_edge, SumoEdge) or not isinstance(destination_edge, SumoEdge):
        raise RuntimeError("Could not match selected coordinates to SUMO road network.")

    edge_ids = _shortest_edge_sequence(origin_edge, destination_edge, network)
    edge_by_id = {edge.id: edge for edge in network.edges}
    route_geometry = [{"lat": float(origin["lat"]), "lng": float(origin["lng"])}]
    for edge_id in edge_ids:
        route_geometry.extend(edge_by_id[edge_id].shape_lat_lng)
    route_geometry.append({"lat": float(destination["lat"]), "lng": float(destination["lng"])})
    route_geometry = _dedupe(route_geometry)

    return {
        "route_id": "sumo-route-user-selected",
        "sumo_edge_ids": edge_ids,
        "route_geometry": route_geometry,
        "route_length_m": round(_route_length_m(route_geometry), 1),
        "source": network.source,
    }


def _shortest_edge_sequence(origin: SumoEdge, destination: SumoEdge, network: SumoNetwork) -> list[str]:
    if origin.id == destination.id:
        return [origin.id]
    try:
        import networkx as nx

        graph = nx.DiGraph()
        for edge in network.edges:
            if edge.from_node and edge.to_node:
                graph.add_edge(edge.from_node, edge.to_node, edge_id=edge.id, weight=max(edge.length, 1))
        nodes = nx.shortest_path(graph, origin.to_node, destination.from_node, weight="weight") if origin.to_node and destination.from_node else []
        edge_ids = [origin.id]
        for start, end in zip(nodes, nodes[1:]):
            edge_ids.append(graph[start][end]["edge_id"])
        edge_ids.append(destination.id)
        return _dedupe_ids(edge_ids)
    except Exception:
        return _dedupe_ids([origin.id, destination.id])


def _route_length_m(points: list[dict[str, float]]) -> float:
    total = 0.0
    for start, end in zip(points, points[1:]):
        total += (((start["lat"] - end["lat"]) * 111_000) ** 2 + ((start["lng"] - end["lng"]) * 88_000) ** 2) ** 0.5
    return total


def _dedupe(points: list[dict[str, float]]) -> list[dict[str, float]]:
    output = []
    for point in points:
        if not output or abs(output[-1]["lat"] - point["lat"]) > 1e-7 or abs(output[-1]["lng"] - point["lng"]) > 1e-7:
            output.append(point)
    return output


def _dedupe_ids(values: list[str]) -> list[str]:
    output = []
    for value in values:
        if not output or output[-1] != value:
            output.append(value)
    return output
