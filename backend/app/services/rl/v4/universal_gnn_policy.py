"""
Universal GNN Policy for V2X Routing — GATv2Conv Actor-Critic.

Architecture
------------
                   ┌──────────────────────────────────┐
Node features [N,9] │  GATv2Conv(9, 64, heads=4)       │
Edge features [E,5] │  → GATv2Conv(256, 64, heads=4)   │
                   └──────────────────────────────────┘
                           ↓
              Global mean-pool + current-node embedding
                           ↓ [512]
              MLP → value [1]  +  action logits [MAX_CANDIDATES]

Node features (relative, no absolute coords):
  0: Δlat  (lat − current_lat) / SPAN_DEG
  1: Δlng  (lng − current_lng) / SPAN_DEG
  2: dist_m / 5000.0
  3: bearing_sin
  4: bearing_cos
  5: is_current  (1 if this node is the vehicle's current node)
  6: is_dest     (1 if this is the destination)
  7: bs_load     (load ratio of nearest BS; 0 if out of coverage)
  8: rsu_cover   (1 if within RSU coverage)

Edge features:
  0: length_m / 5000.0
  1: bearing_sin  (from → to)
  2: bearing_cos
  3: hw_rank      (0 service → 1 motorway)
  4: latency_norm (estimated latency / 50 ms)

References:
  [1] Brody, S. et al., "How Attentive are Graph Attention Networks?",
      ICLR 2022. (GATv2Conv — fixes rank-1 attention of GAT v1)
  [2] Schulman, J. et al., "Proximal Policy Optimization Algorithms",
      arXiv:1707.06347 (PPO actor-critic used during MAML outer loop)
"""
from __future__ import annotations

import math
from typing import Optional

NODE_FEAT_DIM: int = 9
EDGE_FEAT_DIM: int = 5
GNN_HIDDEN: int = 64
GNN_HEADS: int = 4
GNN_OUT: int = GNN_HIDDEN * GNN_HEADS     # 256
MLP_HIDDEN: int = 256
MAX_CANDIDATES: int = 5

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch_geometric.nn import GATv2Conv
    from torch_geometric.data import Data, Batch
    _TORCH_GEO_AVAILABLE = True
except ImportError:
    _TORCH_GEO_AVAILABLE = False
    torch = None  # type: ignore
    nn = None     # type: ignore


# ── Model definition ───────────────────────────────────────────────────────────
if _TORCH_GEO_AVAILABLE:
    class UniversalGNNPolicy(nn.Module):
        """
        Actor-critic GNN policy.

        input  : PyG Data (x, edge_index, edge_attr, current_node, dest_node)
        output : (action_logits [MAX_CANDIDATES], value [1])
        """

        def __init__(
            self,
            node_feat_dim: int = NODE_FEAT_DIM,
            edge_feat_dim: int = EDGE_FEAT_DIM,
            hidden: int = GNN_HIDDEN,
            heads: int = GNN_HEADS,
            max_candidates: int = MAX_CANDIDATES,
        ) -> None:
            super().__init__()

            gnn_out = hidden * heads

            self.gnn1 = GATv2Conv(
                in_channels=node_feat_dim,
                out_channels=hidden,
                heads=heads,
                edge_dim=edge_feat_dim,
                concat=True,
                dropout=0.1,
                add_self_loops=True,
            )
            self.gnn2 = GATv2Conv(
                in_channels=gnn_out,
                out_channels=hidden,
                heads=heads,
                edge_dim=edge_feat_dim,
                concat=True,
                dropout=0.1,
                add_self_loops=True,
            )

            # Combined embedding: current-node + global mean-pool → 2 × gnn_out
            self.actor_mlp = nn.Sequential(
                nn.Linear(2 * gnn_out, MLP_HIDDEN),
                nn.LayerNorm(MLP_HIDDEN),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN, MLP_HIDDEN // 2),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN // 2, max_candidates),
            )
            self.critic_mlp = nn.Sequential(
                nn.Linear(2 * gnn_out, MLP_HIDDEN),
                nn.LayerNorm(MLP_HIDDEN),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN, MLP_HIDDEN // 2),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN // 2, 1),
            )

        def forward(
            self,
            data: "Data",
            action_mask: Optional["torch.Tensor"] = None,
        ) -> tuple["torch.Tensor", "torch.Tensor"]:
            """
            Parameters
            ----------
            data : PyG Data
                .x              [N, NODE_FEAT_DIM]
                .edge_index     [2, E]
                .edge_attr      [E, EDGE_FEAT_DIM]
                .current_node   int — index of the vehicle's current node
                .num_graphs     1 (single graph) or B (batched)
            action_mask : bool tensor [MAX_CANDIDATES]
                True = valid action. None = all valid.

            Returns
            -------
            action_logits : [MAX_CANDIDATES]  (masked, ready for softmax)
            value         : [1]
            """
            x, edge_index, edge_attr = data.x, data.edge_index, data.edge_attr

            h = F.elu(self.gnn1(x, edge_index, edge_attr))
            h = F.elu(self.gnn2(h, edge_index, edge_attr))   # [N, gnn_out]

            # Current-node embedding
            cur = int(data.current_node) if hasattr(data, "current_node") else 0
            h_cur = h[cur]                                    # [gnn_out]

            # Global context via mean-pool
            h_global = h.mean(dim=0)                          # [gnn_out]

            combined = torch.cat([h_cur, h_global], dim=-1)   # [2 * gnn_out]

            logits = self.actor_mlp(combined)                 # [MAX_CANDIDATES]
            value  = self.critic_mlp(combined)                # [1]

            if action_mask is not None:
                # −∞ on invalid actions so softmax gives 0 probability
                logits = logits.masked_fill(~action_mask, float("-inf"))

            return logits, value

        # ── Convenience methods ────────────────────────────────────────────────
        def act(
            self,
            data: "Data",
            action_mask: Optional["torch.Tensor"] = None,
            deterministic: bool = False,
        ) -> tuple[int, "torch.Tensor", "torch.Tensor"]:
            """
            Sample or argmax an action.

            Returns (action_idx, log_prob, value).
            """
            logits, value = self.forward(data, action_mask)
            dist = torch.distributions.Categorical(logits=logits)
            if deterministic:
                action = logits.argmax().item()
            else:
                action = dist.sample().item()
            log_prob = dist.log_prob(torch.tensor(action))
            return int(action), log_prob, value

        def evaluate_actions(
            self,
            data: "Data",
            actions: "torch.Tensor",
            action_mask: Optional["torch.Tensor"] = None,
        ) -> tuple["torch.Tensor", "torch.Tensor", "torch.Tensor"]:
            """
            Compute log_probs, values, and entropy for a batch of actions.

            Used during the PPO update step.
            """
            logits, values = self.forward(data, action_mask)
            dist = torch.distributions.Categorical(logits=logits)
            log_probs = dist.log_prob(actions)
            entropy = dist.entropy()
            return log_probs, values.squeeze(-1), entropy

        def save(self, path: str) -> None:
            torch.save({"state_dict": self.state_dict()}, path)

        @classmethod
        def load(cls, path: str, **kwargs) -> "UniversalGNNPolicy":
            ckpt = torch.load(path, map_location="cpu")
            model = cls(**kwargs)
            model.load_state_dict(ckpt["state_dict"])
            return model

else:
    # Stub for environments without PyTorch
    class UniversalGNNPolicy:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "UniversalGNNPolicy requires torch and torch_geometric. "
                "Install with: pip install torch torch-geometric"
            )


# ── Graph observation builder ─────────────────────────────────────────────────
def build_graph_obs(
    graph: dict,
    bs_nodes: list[dict],
    rsu_nodes: list[dict],
    current_node_id: str,
    dest_node_id: str,
    channel_lookup=None,   # optional ChannelLookup from sionna/channel_lookup.py
) -> "Data":
    """
    Convert a V2XTask graph into a PyG Data observation.

    All node/edge features are relative (no absolute coordinates).
    Compatible with Universal GNN Policy forward().
    """
    if not _TORCH_GEO_AVAILABLE:
        raise ImportError("torch_geometric required for build_graph_obs()")

    nodes_data = graph["nodes"]
    adjacency  = graph["adjacency"]
    node_ids   = list(nodes_data.keys())
    n_idx      = {nid: i for i, nid in enumerate(node_ids)}

    cur_nd  = nodes_data[current_node_id]
    dest_nd = nodes_data[dest_node_id]
    cur_lat, cur_lng = cur_nd["lat"], cur_nd["lng"]

    # Span for relative coordinate normalisation
    lats = [d["lat"] for d in nodes_data.values()]
    lngs = [d["lng"] for d in nodes_data.values()]
    span_deg = max(max(lats) - min(lats), max(lngs) - min(lngs), 1e-5)

    # --- Node features --------------------------------------------------------
    node_feats = []
    for nid in node_ids:
        nd = nodes_data[nid]
        dlat = (nd["lat"] - cur_lat) / span_deg
        dlng = (nd["lng"] - cur_lng) / span_deg
        dist_m = _haversine_m(cur_lat, cur_lng, nd["lat"], nd["lng"])
        bear   = _bearing(cur_lat, cur_lng, nd["lat"], nd["lng"])

        bs_load, rsu_cover = _node_network_features(nd, bs_nodes, rsu_nodes, channel_lookup)

        node_feats.append([
            dlat,
            dlng,
            dist_m / 5000.0,
            math.sin(bear),
            math.cos(bear),
            1.0 if nid == current_node_id else 0.0,
            1.0 if nid == dest_node_id else 0.0,
            bs_load,
            rsu_cover,
        ])

    # --- Edge features --------------------------------------------------------
    edge_src, edge_dst = [], []
    edge_feats = []
    way_tags = graph.get("way_tags", {})

    for nid, neighbors in adjacency.items():
        if nid not in n_idx:
            continue
        src_nd = nodes_data[nid]
        for nb, dist_m in neighbors:
            if nb not in n_idx:
                continue
            dst_nd = nodes_data[nb]
            bear   = _bearing(src_nd["lat"], src_nd["lng"], dst_nd["lat"], dst_nd["lng"])
            tags   = way_tags.get((nid, nb), {})
            hw     = float(tags.get("hw_rank", 0.3))
            lat_ms = _quick_latency_ms(dist_m, bs_nodes)

            edge_src.append(n_idx[nid])
            edge_dst.append(n_idx[nb])
            edge_feats.append([
                dist_m / 5000.0,
                math.sin(bear),
                math.cos(bear),
                hw,
                lat_ms / 50.0,
            ])

    x          = torch.tensor(node_feats,                dtype=torch.float32)
    edge_index = torch.tensor([edge_src, edge_dst],      dtype=torch.long)
    edge_attr  = torch.tensor(edge_feats or [[0.0]*5],   dtype=torch.float32)

    data = Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
    data.current_node = n_idx[current_node_id]
    data.dest_node    = n_idx.get(dest_node_id, 0)
    data.node_ids     = node_ids   # needed for step() action → node mapping
    return data


# ── Helpers ───────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


def _bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dλ = math.radians(lng2 - lng1)
    y = math.sin(dλ) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dλ))
    return math.atan2(y, x)


def _node_network_features(
    nd: dict,
    bs_nodes: list[dict],
    rsu_nodes: list[dict],
    channel_lookup=None,
) -> tuple[float, float]:
    """Return (bs_load, rsu_cover) features for a node."""
    lat, lng = nd["lat"], nd["lng"]

    bs_load = 0.0
    min_bs_d = float("inf")
    nearest_bs = None
    for bs in bs_nodes:
        d = _haversine_m(lat, lng, bs["lat"], bs["lng"])
        if d < min_bs_d:
            min_bs_d = d
            nearest_bs = bs
    if nearest_bs is not None:
        cov = float(nearest_bs.get("coverage_radius_m", 400.0))
        if min_bs_d <= cov:
            bs_load = float(nearest_bs.get("load", 0.0)) / max(
                float(nearest_bs.get("capacity", 1.0)), 1.0
            )

    rsu_cover = 0.0
    for rsu in rsu_nodes:
        d = _haversine_m(lat, lng, rsu["lat"], rsu["lng"])
        if d <= float(rsu.get("coverage_radius_m", 150.0)):
            rsu_cover = 1.0
            break

    return bs_load, rsu_cover


def _quick_latency_ms(dist_m: float, bs_nodes: list[dict]) -> float:
    """Fast latency estimate for edge features (no full _L_total call)."""
    if not bs_nodes:
        return 20.0
    min_cov = min(
        (dist_m / max(float(bs.get("coverage_radius_m", 400.0)), 1.0))
        for bs in bs_nodes
    )
    return min(4.0 + min_cov * 15.0, 100.0)
