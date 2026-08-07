"""
Universal GNN Policy for V2X Routing — GATv2Conv Actor-Critic (Pointer Network).

Architecture
------------
                   ┌──────────────────────────────────────┐
Node features[N,15] │  GATv2Conv(15, 128, heads=8)         │
Edge features [E,8] │  → GATv2Conv(1024, 128, heads=8)     │
                   └──────────────────────────────────────┘
                           ↓  h[i] per node [N, 256]
              ┌────────────────────────────────────────────┐
              │  Pointer Network Actor (per neighbor)      │
              │  score_k = MLP(h_cur ‖ h_neighbor_k)      │
              │  logits [MAX_CANDIDATES]                   │
              └────────────────────────────────────────────┘
              ┌────────────────────────────────────────────┐
              │  Critic                                    │
              │  V = MLP(h_cur ‖ h_dest ‖ h_global ‖ ctx)│
              │  ctx = [step, snr, lat, aoi, spd, dist]   │
              └────────────────────────────────────────────┘

Node features (relative, no absolute coords) — 15 dims:
  0: Δlat  (lat − current_lat) / span_deg
  1: Δlng  (lng − current_lng) / span_deg
  2: dist_m / 5000.0  (from current vehicle position)
  3: bearing_sin
  4: bearing_cos
  5: is_current  (1 if this node is the vehicle's current position)
  6: is_dest     (1 if this is the destination)
  7: bs_load     (load ratio of nearest BS; 0 if out of coverage)
  8: rsu_cover   (1 if within RSU sidelink coverage)
  9: snr_norm    (SNR at this node from nearest BS, ∈ [0,1])
 10: handover_risk (1 if nearest BS here ≠ nearest BS at current node)
 11: latency_norm  (expected access latency at this node / 50 ms)
 12: dist_to_dest_norm (haversine to dest / initial_dist)
 13: cov_prob    (P(SNR > SNR_min) = Φ((SNR_mean − SNR_min) / σ_shadow))
 14: hw_rank_max (max hw_rank of edges adjacent to this node)

Edge features — 8 dims:
  0: length_m / 5000.0
  1: bearing_sin  (from → to)
  2: bearing_cos
  3: hw_rank      (0 service → 1 motorway)
  4: latency_norm (estimated latency at edge midpoint / 50 ms)
  5: handover_on_edge (1 if src and dst have different nearest BS)
  6: snr_midpoint (SNR at edge midpoint from nearest BS, ∈ [0,1])
  7: coverage_continuity (avg coverage prob of src and dst)

Global context injected into Critic — 6 dims:
  0: step_progress   (steps_done / max_steps)
  1: cur_snr_norm    (SNR at current node, ∈ [0,1])
  2: cur_latency_norm (latency at current BS / 50 ms)
  3: cur_aoi_norm    (AoI ∈ [0,1], from AoITracker.aoi_normalized())
  4: vehicle_speed_norm (speed_kmh / 120)
  5: dist_to_dest_norm  (current dist to dest / initial_dist)

References:
  [1] Brody, S. et al., "How Attentive are Graph Attention Networks?",
      ICLR 2022. (GATv2Conv — fixes rank-1 attention of GAT v1)
  [2] Fernandez, H. et al., "Path Loss Characterization for Vehicular
      Communications at 700 MHz and 5.9 GHz", IEEE WCL 3(6), 2014.
  [3] Schulman, J. et al., "Proximal Policy Optimization Algorithms",
      arXiv:1707.06347. (PPO actor-critic used during MAML outer loop)
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

NODE_FEAT_DIM: int = 15
EDGE_FEAT_DIM: int = 8
GLOBAL_CTX_DIM: int = 6
GNN_HIDDEN: int = 128
GNN_HEADS: int = 8
GNN_OUT: int = GNN_HIDDEN * GNN_HEADS     # 1024
MLP_HIDDEN: int = 512
MAX_CANDIDATES: int = 5

# ── Path loss model parameters per network mode (Fernandez 2014, Table I–II) ──
PATH_LOSS_PARAMS: dict[str, dict] = {
    "4G": {"freq_ghz": 0.7,  "n": 2.10, "sigma": 5.0, "d0": 10.0, "p_tx_dbm": 23.0},
    "5G": {"freq_ghz": 3.5,  "n": 2.75, "sigma": 5.5, "d0": 10.0, "p_tx_dbm": 23.0},
    "6G": {"freq_ghz": 28.0, "n": 2.00, "sigma": 4.0, "d0": 10.0, "p_tx_dbm": 23.0},
}
_P_NOISE_DBM = -95.0   # receiver noise floor at 10 MHz bandwidth
_SNR_REF_DB  = 30.0    # normalization reference (excellent link)
_SNR_MIN_DB  = -5.0    # minimum operational SNR threshold

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

        input  : PyG Data (x [N,15], edge_index, edge_attr [E,8],
                           current_node, dest_node, neighbor_idx [1,K],
                           global_ctx [1,6])
        output : (action_logits [MAX_CANDIDATES], value [1])
        """

        def __init__(
            self,
            node_feat_dim: int = NODE_FEAT_DIM,
            edge_feat_dim: int = EDGE_FEAT_DIM,
            hidden: int = GNN_HIDDEN,
            heads: int = GNN_HEADS,
            max_candidates: int = MAX_CANDIDATES,
            global_ctx_dim: int = GLOBAL_CTX_DIM,
            z_dim: int = 0,
        ) -> None:
            """
            z_dim > 0 enables PEARL task-latent conditioning on the critic.
            When z_dim=0 (default), behaviour is identical to the MAML version.
            The actor (Pointer Network) is intentionally NOT conditioned on z —
            keeping the actor general avoids overfitting to the support context.
            """
            super().__init__()

            self.max_candidates = max_candidates
            self.global_ctx_dim = global_ctx_dim
            self.z_dim          = z_dim
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

            # Pointer Network actor: score each candidate neighbor independently
            # input = [h_cur ‖ h_neighbor_k] → 1 scalar score
            self.actor_score = nn.Sequential(
                nn.Linear(2 * gnn_out, MLP_HIDDEN),
                nn.LayerNorm(MLP_HIDDEN),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN, MLP_HIDDEN // 2),
                nn.ReLU(),
                nn.Linear(MLP_HIDDEN // 2, 1),
            )
            # Critic: h_cur + h_dest + h_global + global_ctx [+ z (PEARL)]
            # z_dim=0 → same architecture as MAML (fully backward compatible)
            self.critic_mlp = nn.Sequential(
                nn.Linear(3 * gnn_out + global_ctx_dim + z_dim, MLP_HIDDEN),
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
            z: Optional["torch.Tensor"] = None,
        ) -> tuple["torch.Tensor", "torch.Tensor"]:
            """
            Single:  returns logits [MAX_CANDIDATES], value [1]
            Batched: returns logits [B, MAX_CANDIDATES], value [B]

            z : task latent from PEARL TaskEncoder [B, z_dim] or [z_dim].
                When None (MAML mode), critic uses only h_cur/dest/global + ctx.
                When provided, z is appended to critic input (PEARL mode).
            """
            from torch_geometric.nn import global_mean_pool

            x, edge_index, edge_attr = data.x, data.edge_index, data.edge_attr
            K = self.max_candidates

            h = F.elu(self.gnn1(x, edge_index, edge_attr))
            h = F.elu(self.gnn2(h, edge_index, edge_attr))   # [total_N, gnn_out]

            batch_vec = getattr(data, "batch", None)
            if batch_vec is not None:
                # ── Batched mode (PyG Batch) ───────────────────────────────────
                h_global = global_mean_pool(h, batch_vec)     # [B, gnn_out]
                B = h_global.shape[0]
                ptr = data.ptr.to(x.device)                   # [B+1]

                def _to_long(attr):
                    v = attr
                    if not isinstance(v, torch.Tensor):
                        v = torch.tensor(v, dtype=torch.long, device=x.device)
                    return v.long().to(x.device)

                cur_nodes  = _to_long(data.current_node)      # [B]
                dest_nodes = _to_long(data.dest_node)         # [B]
                cur_global  = ptr[:-1] + cur_nodes            # [B]
                dest_global = ptr[:-1] + dest_nodes           # [B]
                h_cur  = h[cur_global]                        # [B, gnn_out]
                h_dest = h[dest_global]                       # [B, gnn_out]

                # Pointer network: per-neighbor scoring
                nbr_idx = data.neighbor_idx.to(x.device)          # [B, K]
                nbr_global = ptr[:-1].unsqueeze(1) + nbr_idx      # [B, K]
                gnn_out = h.shape[-1]
                h_nbr = h[nbr_global.view(-1)].view(B, K, gnn_out)  # [B, K, gnn_out]
                h_cur_exp = h_cur.unsqueeze(1).expand(-1, K, -1)    # [B, K, gnn_out]
                combined_a = torch.cat([h_cur_exp, h_nbr], dim=-1)  # [B, K, 2*gnn_out]
                logits = self.actor_score(combined_a).squeeze(-1)    # [B, K]

                # Global context for critic (+ optional PEARL task latent z)
                if hasattr(data, "global_ctx") and data.global_ctx is not None:
                    ctx = data.global_ctx.to(x.device)              # [B, global_ctx_dim]
                else:
                    ctx = torch.zeros(B, self.global_ctx_dim, device=x.device)
                critic_parts = [h_cur, h_dest, h_global, ctx]
                if z is not None:
                    # Broadcast z across batch if provided as [z_dim]
                    z_b = z.to(x.device)
                    if z_b.dim() == 1:
                        z_b = z_b.unsqueeze(0).expand(B, -1)
                    critic_parts.append(z_b)
                elif self.z_dim > 0:
                    critic_parts.append(torch.zeros(B, self.z_dim, device=x.device))
                combined_c = torch.cat(critic_parts, dim=-1)
                value = self.critic_mlp(combined_c).squeeze(-1)     # [B]

                if action_mask is not None:
                    logits = logits.masked_fill(~action_mask, float("-inf"))

                return logits, value
            else:
                # ── Single-graph mode ──────────────────────────────────────────
                cur  = int(data.current_node) if hasattr(data, "current_node") else 0
                dest = int(data.dest_node)    if hasattr(data, "dest_node")    else 0

                h_cur    = h[cur]          # [gnn_out]
                h_dest   = h[dest]         # [gnn_out]
                h_global = h.mean(dim=0)   # [gnn_out]

                nbr_idx = data.neighbor_idx.squeeze(0).to(x.device)  # [K]
                h_nbr = h[nbr_idx]                                    # [K, gnn_out]
                h_cur_exp = h_cur.unsqueeze(0).expand(K, -1)         # [K, gnn_out]
                combined_a = torch.cat([h_cur_exp, h_nbr], dim=-1)   # [K, 2*gnn_out]
                logits = self.actor_score(combined_a).squeeze(-1)     # [K]

                if hasattr(data, "global_ctx") and data.global_ctx is not None:
                    ctx = data.global_ctx.squeeze(0).to(x.device)    # [global_ctx_dim]
                else:
                    ctx = torch.zeros(self.global_ctx_dim, device=x.device)
                critic_parts = [h_cur, h_dest, h_global, ctx]
                if z is not None:
                    z_s = z.to(x.device)
                    if z_s.dim() == 2:
                        z_s = z_s.squeeze(0)
                    critic_parts.append(z_s)
                elif self.z_dim > 0:
                    critic_parts.append(torch.zeros(self.z_dim, device=x.device))
                combined_c = torch.cat(critic_parts, dim=-1)
                value = self.critic_mlp(combined_c)                   # [1]

                if action_mask is not None:
                    logits = logits.masked_fill(~action_mask, float("-inf"))

                return logits, value

        # ── Convenience methods ────────────────────────────────────────────────
        def act(
            self,
            data: "Data",
            action_mask: Optional["torch.Tensor"] = None,
            deterministic: bool = False,
            z: Optional["torch.Tensor"] = None,
        ) -> tuple[int, "torch.Tensor", "torch.Tensor"]:
            """Sample or argmax an action. Returns (action_idx, log_prob, value).

            z: PEARL task latent [z_dim] for critic conditioning. None → MAML mode.
            """
            logits, value = self.forward(data, action_mask, z=z)
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
            z: Optional["torch.Tensor"] = None,
        ) -> tuple["torch.Tensor", "torch.Tensor", "torch.Tensor"]:
            """Compute log_probs, values, entropy for PPO update.

            z: PEARL task latent [B, z_dim] or None (MAML mode).
            """
            logits, values = self.forward(data, action_mask, z=z)
            dist = torch.distributions.Categorical(logits=logits)
            log_probs = dist.log_prob(actions)
            entropy = dist.entropy()
            return log_probs, values.squeeze(-1), entropy

        def encode_nodes(self, data: "Data") -> "torch.Tensor":
            """Run GNN layers and return per-node embeddings [N, gnn_out]."""
            with torch.no_grad():
                x, ei, ea = data.x, data.edge_index, data.edge_attr
                h = F.elu(self.gnn1(x, ei, ea))
                h = F.elu(self.gnn2(h, ei, ea))
            return h

        def select_bs(
            self,
            node_embs: "torch.Tensor",
            pos_emb: "torch.Tensor",
            bs_indices: "torch.Tensor",
        ) -> int:
            """Dot-product selection among BS nodes. Used by V4InferenceModule."""
            bs_embs = node_embs[bs_indices]
            scores = torch.mv(bs_embs, pos_emb)
            return int(scores.argmax().item())

        def save(self, path: str) -> None:
            torch.save({"state_dict": self.state_dict()}, path)

        @classmethod
        def load(cls, path: str, **kwargs) -> "UniversalGNNPolicy":
            ckpt = torch.load(path, map_location="cpu")
            model = cls(**kwargs)
            model.load_state_dict(ckpt["state_dict"])
            return model

else:
    class UniversalGNNPolicy:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "UniversalGNNPolicy requires torch and torch_geometric. "
                "Install with: pip install torch torch-geometric"
            )


# ── Intrinsic Curiosity Module ─────────────────────────────────────────────────
if _TORCH_GEO_AVAILABLE:
    class IntrinsicCuriosityModule(nn.Module):
        """
        ICM for V2X graph navigation (Pathak et al., ICML 2017, adapted).

        State space: 6-dim global context [step_p, snr_n, lat_n, aoi_n, speed_n, d2d_n].
        This is the same context the Critic uses — no extra computation at collection time.

        Forward model  f : R^(ctx_dim + n_actions) → R^ctx_dim
            Predicts the next global context given current context + action (one-hot).
        Inverse model  g : R^(2·ctx_dim) → R^n_actions
            Predicts which action was taken given (ctx_t, ctx_{t+1}).

        Intrinsic reward  r_i = β_fwd · ||f(ctx_t, a) - ctx_{t+1}||²

        Loss  L_ICM = β · L_forward + (1-β) · L_inverse
            where L_forward = MSE,  L_inverse = CrossEntropy.

        Why global_ctx (not raw node features)?
          - ctx is always 6-dim regardless of graph size → no padding/masking
          - Captures the most dynamics-relevant signals: d2d_n decreases toward goal,
            snr_n drops in dead zones, aoi_n rises without BS coverage
          - Avoids re-running GNN for curiosity computation

        References:
          Pathak, D. et al., "Curiosity-driven Exploration by Self-supervised Prediction",
          ICML 2017, arXiv:1705.05363.
        """

        CTX_DIM    = GLOBAL_CTX_DIM    # 6
        N_ACTIONS  = MAX_CANDIDATES    # 5

        def __init__(
            self,
            ctx_dim:   int = GLOBAL_CTX_DIM,
            n_actions: int = MAX_CANDIDATES,
            hidden:    int = 64,
        ) -> None:
            super().__init__()
            self.ctx_dim   = ctx_dim
            self.n_actions = n_actions

            # Forward model: (ctx_t ‖ action_onehot) → ctx_{t+1}
            self.forward_net = nn.Sequential(
                nn.Linear(ctx_dim + n_actions, hidden),
                nn.ReLU(),
                nn.Linear(hidden, hidden),
                nn.ReLU(),
                nn.Linear(hidden, ctx_dim),
            )

            # Inverse model: (ctx_t ‖ ctx_{t+1}) → action logits
            self.inverse_net = nn.Sequential(
                nn.Linear(ctx_dim * 2, hidden),
                nn.ReLU(),
                nn.Linear(hidden, n_actions),
            )

        def forward(
            self,
            ctx_t:    "torch.Tensor",   # [B, ctx_dim]
            actions:  "torch.Tensor",   # [B] long
            ctx_t1:   "torch.Tensor",   # [B, ctx_dim]
        ) -> tuple["torch.Tensor", "torch.Tensor", "torch.Tensor"]:
            """
            Returns (forward_loss, inverse_loss, intrinsic_rewards [B]).
            """
            a_onehot = F.one_hot(actions, num_classes=self.n_actions).float()  # [B, n_actions]
            fwd_input  = torch.cat([ctx_t, a_onehot], dim=-1)   # [B, ctx+act]
            ctx_pred   = self.forward_net(fwd_input)             # [B, ctx_dim]
            action_pred = self.inverse_net(torch.cat([ctx_t, ctx_t1], dim=-1))  # [B, n_actions]

            forward_loss  = F.mse_loss(ctx_pred, ctx_t1.detach())
            inverse_loss  = F.cross_entropy(action_pred, actions)
            intrinsic_rew = ((ctx_pred.detach() - ctx_t1) ** 2).mean(dim=-1)   # [B]
            return forward_loss, inverse_loss, intrinsic_rew

        @torch.no_grad()
        def compute_intrinsic_rewards(
            self,
            ctx_t:   "torch.Tensor",   # [B, ctx_dim]
            actions: "torch.Tensor",   # [B] long
            ctx_t1:  "torch.Tensor",   # [B, ctx_dim]
        ) -> "torch.Tensor":
            """Intrinsic reward only (no gradient), used during episode collection."""
            a_onehot = F.one_hot(actions, num_classes=self.n_actions).float()
            ctx_pred = self.forward_net(torch.cat([ctx_t, a_onehot], dim=-1))
            return ((ctx_pred - ctx_t1) ** 2).mean(dim=-1)   # [B]

else:
    class IntrinsicCuriosityModule:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "IntrinsicCuriosityModule requires torch. "
                "Install with: pip install torch torch-geometric"
            )


# ── Graph observation builder (slow fallback path) ────────────────────────────
def build_graph_obs(
    graph: dict,
    bs_nodes: list[dict],
    rsu_nodes: list[dict],
    current_node_id: str,
    dest_node_id: str,
    channel_lookup=None,
    network_mode: str = "5G",
    initial_dist: float = 0.0,
    global_ctx: Optional[list] = None,
) -> "Data":
    """
    Convert a V2XTask graph into a PyG Data observation (slow path).

    All node/edge features are relative (no absolute coordinates).
    Produces NODE_FEAT_DIM=15 and EDGE_FEAT_DIM=8 tensors.
    """
    if not _TORCH_GEO_AVAILABLE:
        raise ImportError("torch_geometric required for build_graph_obs()")

    params    = PATH_LOSS_PARAMS.get(network_mode, PATH_LOSS_PARAMS["5G"])
    nodes_data = graph["nodes"]
    adjacency  = graph["adjacency"]
    node_ids   = list(nodes_data.keys())
    n_idx      = {nid: i for i, nid in enumerate(node_ids)}
    way_tags   = graph.get("way_tags", {})

    cur_nd  = nodes_data[current_node_id]
    dest_nd = nodes_data[dest_node_id]
    cur_lat, cur_lng   = cur_nd["lat"],  cur_nd["lng"]
    dest_lat, dest_lng = dest_nd["lat"], dest_nd["lng"]

    lats = [d["lat"] for d in nodes_data.values()]
    lngs = [d["lng"] for d in nodes_data.values()]
    span_deg = max(max(lats) - min(lats), max(lngs) - min(lngs), 1e-5)

    # Precompute nearest-BS for each node (needed for handover_risk + SNR)
    nearest_bs_idx = {}   # nid → index into bs_nodes
    snr_db_map     = {}   # nid → SNR in dB
    cov_prob_map   = {}   # nid → coverage probability
    lat_norm_map   = {}   # nid → latency_norm

    pure_bs = [b for b in bs_nodes if str(b.get("type", "")).lower() != "roadside_unit"]
    for nid, nd in nodes_data.items():
        (nb_idx, snr_db, cov_p, lat_n) = _node_rf_features(
            nd["lat"], nd["lng"], pure_bs or bs_nodes, params
        )
        nearest_bs_idx[nid] = nb_idx
        snr_db_map[nid]     = snr_db
        cov_prob_map[nid]   = cov_p
        lat_norm_map[nid]   = lat_n

    # hw_rank per node = max hw_rank of adjacent edges
    hw_rank_node = {}
    for nid in nodes_data:
        hw_max = 0.2
        for nb, _ in adjacency.get(nid, []):
            hw = float(way_tags.get((nid, nb), {}).get("hw_rank", 0.2))
            hw_max = max(hw_max, hw)
        hw_rank_node[nid] = hw_max

    cur_bs_idx = nearest_bs_idx.get(current_node_id, -1)
    init_d = initial_dist if initial_dist > 0 else max(
        _haversine_m(cur_lat, cur_lng, dest_lat, dest_lng), 1.0
    )

    # --- Node features [N, 15] ------------------------------------------------
    node_feats = []
    for nid in node_ids:
        nd = nodes_data[nid]
        dlat  = (nd["lat"] - cur_lat) / span_deg
        dlng  = (nd["lng"] - cur_lng) / span_deg
        dm    = _haversine_m(cur_lat, cur_lng, nd["lat"], nd["lng"])
        bear  = _bearing(cur_lat, cur_lng, nd["lat"], nd["lng"])
        d2d   = _haversine_m(nd["lat"], nd["lng"], dest_lat, dest_lng)

        bs_load, rsu_cover = _node_network_features(nd, bs_nodes, rsu_nodes, channel_lookup)
        snr_n   = float(np.clip(snr_db_map.get(nid, 0.0) / _SNR_REF_DB, 0.0, 1.0))
        ho_risk = 1.0 if (nearest_bs_idx.get(nid, -2) != cur_bs_idx) else 0.0
        lat_n   = lat_norm_map.get(nid, 1.0)
        d2d_n   = d2d / init_d
        cov_p   = cov_prob_map.get(nid, 0.0)
        hw_n    = hw_rank_node.get(nid, 0.2)

        node_feats.append([
            dlat, dlng, dm / 5000.0,
            math.sin(bear), math.cos(bear),
            1.0 if nid == current_node_id else 0.0,
            1.0 if nid == dest_node_id else 0.0,
            bs_load, rsu_cover,
            snr_n, ho_risk, lat_n,
            min(d2d_n, 4.0),   # cap at 4× initial_dist
            cov_p, hw_n,
        ])

    # --- Edge features [E, 8] -------------------------------------------------
    edge_src, edge_dst, edge_feats = [], [], []
    for nid, neighbors in adjacency.items():
        if nid not in n_idx:
            continue
        src = nodes_data[nid]
        for nb, dist_m in neighbors:
            if nb not in n_idx:
                continue
            dst  = nodes_data[nb]
            bear = _bearing(src["lat"], src["lng"], dst["lat"], dst["lng"])
            tags = way_tags.get((nid, nb), {})
            hw   = float(tags.get("hw_rank", 0.3))

            # Midpoint RF features
            mid_lat = (src["lat"] + dst["lat"]) / 2
            mid_lng = (src["lng"] + dst["lng"]) / 2
            (_, mid_snr_db, mid_cov_p, mid_lat_n) = _node_rf_features(
                mid_lat, mid_lng, pure_bs or bs_nodes, params
            )
            mid_snr_n = float(np.clip(mid_snr_db / _SNR_REF_DB, 0.0, 1.0))

            # Handover on edge: src and dst have different nearest BS
            ho_on_edge = 1.0 if (nearest_bs_idx.get(nid, -1) != nearest_bs_idx.get(nb, -2)) else 0.0
            cov_cont = (cov_prob_map.get(nid, 0.0) + cov_prob_map.get(nb, 0.0)) / 2.0

            edge_src.append(n_idx[nid])
            edge_dst.append(n_idx[nb])
            edge_feats.append([
                dist_m / 5000.0,
                math.sin(bear), math.cos(bear),
                hw,
                min(mid_lat_n, 2.0),
                ho_on_edge,
                mid_snr_n,
                cov_cont,
            ])

    x          = torch.tensor(node_feats,                           dtype=torch.float32)
    edge_index = torch.tensor([edge_src, edge_dst],                 dtype=torch.long)
    edge_attr  = torch.tensor(edge_feats or [[0.0] * EDGE_FEAT_DIM], dtype=torch.float32)

    # G11 fix: sort neighbors by dist-to-dest, not edge distance
    cur_local = n_idx[current_node_id]
    dest_local = n_idx.get(dest_node_id, 0)
    raw_nb = adjacency.get(current_node_id, [])

    def _dist_to_dest(nb_entry):
        nb_id, _ = nb_entry
        nd = nodes_data.get(nb_id, {})
        return _haversine_m(nd.get("lat", 0), nd.get("lng", 0), dest_lat, dest_lng)

    sorted_nb = sorted(raw_nb, key=_dist_to_dest)[:MAX_CANDIDATES]
    nbr_local = [n_idx.get(nb[0], cur_local) for nb in sorted_nb]
    while len(nbr_local) < MAX_CANDIDATES:
        nbr_local.append(cur_local)

    data = Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
    data.current_node = cur_local
    data.dest_node    = dest_local
    data.node_ids     = node_ids
    data.neighbor_idx = torch.tensor(nbr_local, dtype=torch.long).unsqueeze(0)

    if global_ctx is not None:
        data.global_ctx = torch.tensor(global_ctx, dtype=torch.float32).unsqueeze(0)

    return data


# ── Per-node RF feature helpers ───────────────────────────────────────────────
def _node_rf_features(
    lat: float, lng: float,
    bs_nodes: list[dict],
    params: dict,
) -> tuple[int, float, float, float]:
    """
    Compute RF features for a single position (lat, lng).

    Selects the BS with the highest SNR (not just nearest) to correctly
    model HetNet scenarios where a distant 4G macro may outperform a
    near 5G small cell at long range.

    Per-BS network_mode overrides the fallback `params` when present,
    enabling mixed 4G/5G/6G (HetNet) task sampling.

    Returns (best_bs_idx, snr_db, cov_prob, latency_norm).
    """
    if not bs_nodes:
        return -1, -30.0, 0.0, 1.0

    best_idx = -1
    best_snr = -float("inf")
    best_sigma = params["sigma"]

    for i, bs in enumerate(bs_nodes):
        # Per-BS network_mode (HetNet) → look up per-BS path-loss params
        bs_params = PATH_LOSS_PARAMS.get(bs.get("network_mode", ""), params)
        d = _haversine_m(lat, lng, float(bs["lat"]), float(bs["lng"]))
        snr = _compute_snr_db(d, bs_params)
        if snr > best_snr:
            best_snr = snr
            best_idx = i
            best_sigma = bs_params["sigma"]

    bs_node = bs_nodes[best_idx]
    cov_p   = _coverage_prob(best_snr, best_sigma)
    bs_elat = float(bs_node.get("edge_latency_ms", 3.0))
    bs_load = float(bs_node.get("load", 0.0))
    snr_n   = float(np.clip(best_snr / _SNR_REF_DB, 0.0, 1.0))
    # M/M/1 queuing delay — must match _find_best_node() fallback in universal_v2x_env.py
    rho     = min(bs_load, 0.95)
    q_ms    = (rho / max(1.0 - rho, 0.05)) * 0.5
    lat_ms  = 4.0 + (1.0 - snr_n) * 46.0 + bs_elat + q_ms
    lat_n   = lat_ms / 50.0

    return best_idx, best_snr, float(cov_p), float(lat_n)


def _compute_snr_db(dist_m: float, params: dict) -> float:
    """Log-distance path-loss SNR. Fernandez 2014."""
    d0     = params["d0"]
    n_exp  = params["n"]
    freq_hz = params["freq_ghz"] * 1e9
    lam    = 3e8 / freq_hz
    pl_d0  = 20.0 * math.log10(max(4.0 * math.pi * d0 / lam, 1e-9))
    dist   = max(dist_m, d0)
    pl     = pl_d0 + 10.0 * n_exp * math.log10(dist / d0)
    return params["p_tx_dbm"] - pl - _P_NOISE_DBM


def _coverage_prob(snr_db: float, sigma: float) -> float:
    """P(SNR > SNR_min) = Φ((snr_db − SNR_min) / σ), normal CDF approximation."""
    z = (snr_db - _SNR_MIN_DB) / max(sigma, 0.1)
    a = 0.147
    abs_z = abs(z) / math.sqrt(2.0)
    erf_approx = math.copysign(
        math.sqrt(1.0 - math.exp(-abs_z ** 2 * (4.0 / math.pi + a * abs_z ** 2) / (1.0 + a * abs_z ** 2))),
        z,
    )
    return 0.5 * (1.0 + erf_approx)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _build_edges_rf_vectorized(
    mid_lats: list, mid_lngs: list, active_bs: list, params: dict
) -> tuple:
    """Vectorized RF SNR + latency_norm for all edge midpoints in one numpy pass.

    Replaces the per-edge _node_rf_features() Python loop in _build_edges().
    Returns (snr_arr[E], lat_n_arr[E]) as float32 arrays.
    """
    E = len(mid_lats)
    M = len(active_bs)
    if M == 0:
        return np.full(E, -30.0, dtype=np.float32), np.full(E, 1.0, dtype=np.float32)

    p_tx_arr  = np.empty(M)
    n_arr     = np.empty(M)
    freq_arr  = np.empty(M)
    d0_arr    = np.empty(M)
    sigma_arr = np.empty(M)
    elat_arr  = np.empty(M)
    load_arr  = np.empty(M)
    bs_lats   = np.empty(M)
    bs_lngs   = np.empty(M)
    for m, bs in enumerate(active_bs):
        p = PATH_LOSS_PARAMS.get(bs.get("network_mode", ""), params)
        p_tx_arr[m]  = p["p_tx_dbm"]
        n_arr[m]     = p["n"]
        freq_arr[m]  = p["freq_ghz"]
        d0_arr[m]    = p["d0"]
        sigma_arr[m] = p["sigma"]
        elat_arr[m]  = float(bs.get("edge_latency_ms", 3.0))
        load_arr[m]  = float(bs.get("load", 0.0))
        bs_lats[m]   = float(bs["lat"])
        bs_lngs[m]   = float(bs["lng"])

    # Haversine [E, M]
    mid_lats_a = np.array(mid_lats, dtype=np.float64)
    mid_lngs_a = np.array(mid_lngs, dtype=np.float64)
    dlat_r = np.radians(bs_lats[None, :] - mid_lats_a[:, None])
    dlng_r = np.radians(bs_lngs[None, :] - mid_lngs_a[:, None])
    cos_mid = np.cos(np.radians(mid_lats_a))[:, None]
    cos_bs  = np.cos(np.radians(bs_lats))[None, :]
    a_mat   = np.sin(dlat_r / 2) ** 2 + cos_mid * cos_bs * np.sin(dlng_r / 2) ** 2
    dist_mat = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a_mat, 0.0, 1.0)))  # [E, M]

    # SNR [E, M] via log-distance path loss
    lam      = 3e8 / (freq_arr * 1e9)                                      # [M]
    pl_d0    = 20.0 * np.log10(np.maximum(4.0 * np.pi * d0_arr / lam, 1e-9))  # [M]
    dist_c   = np.maximum(dist_mat, d0_arr[None, :])                       # [E, M]
    pl       = pl_d0[None, :] + 10.0 * n_arr[None, :] * np.log10(dist_c / d0_arr[None, :])
    snr_mat  = p_tx_arr[None, :] - pl - _P_NOISE_DBM                      # [E, M]

    # Best BS per edge
    best_m   = np.argmax(snr_mat, axis=1)                                  # [E]
    best_snr = snr_mat[np.arange(E), best_m]                               # [E]

    # Latency norm [E] — matches _node_rf_features formula
    snr_n  = np.clip(best_snr / _SNR_REF_DB, 0.0, 1.0)
    rho    = np.minimum(load_arr[best_m], 0.95)
    q_ms   = (rho / np.maximum(1.0 - rho, 0.05)) * 0.5
    lat_ms = 4.0 + (1.0 - snr_n) * 46.0 + elat_arr[best_m] + q_ms
    lat_n  = lat_ms / 50.0

    return best_snr.astype(np.float32), lat_n.astype(np.float32)


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
    """Return (bs_load, rsu_cover) for a node. Kept for backward compat."""
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
            bs_load = float(nearest_bs.get("load", 0.0))

    rsu_cover = 0.0
    for rsu in rsu_nodes:
        d = _haversine_m(lat, lng, rsu["lat"], rsu["lng"])
        if d <= float(rsu.get("coverage_radius_m", 150.0)):
            rsu_cover = 1.0
            break

    return bs_load, rsu_cover


# ── Fast cached observation builder ──────────────────────────────────────────
class GraphTaskCache:
    """
    Per-task cache that pre-computes all fixed graph data once,
    leaving only position-relative features for per-step numpy vectorization.

    Fixed per task (precomputed in __init__):
      - lats, lngs, span_deg, node positions
      - bs_loads, rsu_covers (from task's density-informed BS load)
      - snr_per_node, nearest_bs_idx_arr, latency_per_node, cov_prob_per_node
      - hw_rank_per_node
      - edge_index, edge_attr (all 8 edge features)

    Per-step (computed in build_obs):
      - dlat, dlng, dist_from_cur, bearing (relative to current node)
      - is_current, is_dest (binary)
      - handover_risk (cur_node's nearest BS vs each node's nearest BS)
      - dist_to_dest_norm (each node's dist to dest / initial_dist)

    BUG-6 fix: the cache is built per-task, not per-episode. The env
    only rebuilds it when the task graph actually changes.
    """

    __slots__ = (
        "node_ids", "n_idx", "n", "span_deg",
        "lats", "lngs",                  # float64 [N]
        "bs_loads", "rsu_covers",        # float32 [N]
        "snr_per_node",                  # float32 [N]  — SNR at nearest BS, ∈ [0,1]
        "nearest_bs_idx_arr",            # int32   [N]  — index into bs_nodes
        "latency_per_node",              # float32 [N]  — expected latency_norm
        "cov_prob_per_node",             # float32 [N]  — P(BS coverage)
        "hw_rank_per_node",              # float32 [N]  — max hw_rank of adj edges
        "edge_index", "edge_attr",       # torch tensors — fixed per task
        "adjacency",                     # dict for per-step neighbor_idx
    )

    def __init__(
        self,
        graph: dict,
        bs_nodes: list[dict],
        rsu_nodes: list[dict],
        dest_node_id: str,      # kept for API compat; ignored (dest handled in build_obs)
        channel_lookup=None,    # unused; kept for API parity
        network_mode: str = "5G",
    ) -> None:
        nodes_data     = graph["nodes"]
        adjacency      = graph["adjacency"]
        self.adjacency = adjacency

        self.node_ids = list(nodes_data.keys())
        self.n_idx    = {nid: i for i, nid in enumerate(self.node_ids)}
        self.n        = len(self.node_ids)

        self.lats = np.array([nodes_data[nid]["lat"] for nid in self.node_ids], dtype=np.float64)
        self.lngs = np.array([nodes_data[nid]["lng"] for nid in self.node_ids], dtype=np.float64)
        self.span_deg = float(max(
            self.lats.max() - self.lats.min(),
            self.lngs.max() - self.lngs.min(),
            1e-5,
        ))

        params = PATH_LOSS_PARAMS.get(network_mode, PATH_LOSS_PARAMS["5G"])

        # Separate BS from RSU for SNR computation
        pure_bs = [b for b in bs_nodes
                   if str(b.get("type", "")).lower() not in ("rsu", "roadside_unit")]
        if not pure_bs:
            pure_bs = bs_nodes  # fallback: use all

        (self.bs_loads, self.rsu_covers,
         self.snr_per_node, self.nearest_bs_idx_arr,
         self.latency_per_node, self.cov_prob_per_node) = self._build_network_features(
            bs_nodes, rsu_nodes, pure_bs, params
        )
        self.hw_rank_per_node = self._build_hw_rank_per_node(
            adjacency, graph.get("way_tags", {})
        )
        self.edge_index, self.edge_attr = self._build_edges(
            graph, nodes_data, adjacency, bs_nodes, pure_bs, params
        )

        # Pre-compute trig arrays (constant across all steps) — avoids recomputing per step
        self._lats_rad  = np.radians(self.lats)
        self._lngs_rad  = np.radians(self.lngs)
        self._cos_lats  = np.cos(self._lats_rad)
        self._sin_lats  = np.sin(self._lats_rad)

        # Pre-allocated node feature buffer for in-place updates (avoids np.column_stack alloc)
        self._nf_buf = np.zeros((self.n, 15), dtype=np.float32)
        self._nf_buf[:, 7]  = self.bs_loads
        self._nf_buf[:, 8]  = self.rsu_covers
        self._nf_buf[:, 9]  = self.snr_per_node
        self._nf_buf[:, 11] = self.latency_per_node
        self._nf_buf[:, 13] = self.cov_prob_per_node
        self._nf_buf[:, 14] = self.hw_rank_per_node

        # Per-episode dest cache (invalidated when dest_node_id or initial_dist changes)
        self._dest_cache_key: tuple = ()
        self._dest_d2d_norm: np.ndarray = np.zeros(self.n, dtype=np.float32)
        self._dest_is_dest:  np.ndarray = np.zeros(self.n, dtype=np.float32)

    # ── Fixed-data builders (called once per task) ────────────────────────────

    def _build_network_features(
        self,
        bs_nodes: list[dict],
        rsu_nodes: list[dict],
        pure_bs: list[dict],
        params: dict,
    ) -> tuple:
        """
        Returns (bs_loads, rsu_covers, snr_per_node, nearest_bs_idx_arr,
                 latency_per_node, cov_prob_per_node).
        All arrays are [N] float32 except nearest_bs_idx_arr (int32).
        """
        N = self.n
        bs_loads   = np.zeros(N, dtype=np.float32)
        rsu_covers = np.zeros(N, dtype=np.float32)
        snr_per_node     = np.zeros(N, dtype=np.float32)
        nearest_bs_idx_arr = np.zeros(N, dtype=np.int32)
        latency_per_node = np.full(N, 1.0, dtype=np.float32)
        cov_prob_per_node = np.zeros(N, dtype=np.float32)

        # ── BS load / SNR / coverage ──────────────────────────────────────────
        if bs_nodes:
            bs_lats = np.array([b["lat"] for b in bs_nodes], dtype=np.float64)
            bs_lngs = np.array([b["lng"] for b in bs_nodes], dtype=np.float64)
            bs_cov  = np.array([float(b.get("coverage_radius_m", 400.0)) for b in bs_nodes])
            bs_load_vals = np.array([float(b.get("load", 0.0)) for b in bs_nodes])
            bs_edge_lats = np.array([float(b.get("edge_latency_ms", 3.0)) for b in bs_nodes])

            # Haversine [N, M]
            dlat_r = np.radians(bs_lats[None, :] - self.lats[:, None])
            dlng_r = np.radians(bs_lngs[None, :] - self.lngs[:, None])
            lat1_r = np.radians(self.lats)[:, None]
            lat2_r = np.radians(bs_lats)[None, :]
            a_mat  = (np.sin(dlat_r / 2) ** 2
                      + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlng_r / 2) ** 2)
            dist_bs = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a_mat, 0.0, 1.0)))

            nearest = dist_bs.argmin(axis=1)            # [N]
            nd_dist = dist_bs[np.arange(N), nearest]   # [N]

            bs_loads = np.where(
                nd_dist <= bs_cov[nearest], bs_load_vals[nearest], 0.0
            ).astype(np.float32)

            # SNR using pure_bs if available (avoids mixing RSU into radio model)
            if pure_bs:
                pbs_lats = np.array([b["lat"] for b in pure_bs], dtype=np.float64)
                pbs_lngs = np.array([b["lng"] for b in pure_bs], dtype=np.float64)
                pbs_elats = np.array([float(b.get("edge_latency_ms", 3.0)) for b in pure_bs])
                dlat_p = np.radians(pbs_lats[None, :] - self.lats[:, None])
                dlng_p = np.radians(pbs_lngs[None, :] - self.lngs[:, None])
                lat2_p = np.radians(pbs_lats)[None, :]
                a_p    = (np.sin(dlat_p / 2) ** 2
                          + np.cos(lat1_r) * np.cos(lat2_p) * np.sin(dlng_p / 2) ** 2)
                dist_pbs = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a_p, 0.0, 1.0)))
                nearest_pbs = dist_pbs.argmin(axis=1)         # [N]
                dist_to_near_pbs = dist_pbs[np.arange(N), nearest_pbs]
            else:
                nearest_pbs = nearest
                dist_to_near_pbs = nd_dist
                pbs_elats = bs_edge_lats

            # Path loss → SNR
            d0    = params["d0"]
            n_exp = params["n"]
            freq_hz = params["freq_ghz"] * 1e9
            lam   = 3e8 / freq_hz
            pl_d0 = 20.0 * np.log10(np.maximum(4.0 * np.pi * d0 / lam, 1e-9))
            dist_clamped = np.maximum(dist_to_near_pbs, d0)
            pl    = pl_d0 + 10.0 * n_exp * np.log10(dist_clamped / d0)
            snr_db = params["p_tx_dbm"] - pl - _P_NOISE_DBM       # [N]

            snr_per_node = np.clip(snr_db / _SNR_REF_DB, 0.0, 1.0).astype(np.float32)
            nearest_bs_idx_arr = nearest_pbs.astype(np.int32)

            # Coverage probability: Φ((SNR - SNR_min) / σ)
            sigma = params["sigma"]
            z     = (snr_db - _SNR_MIN_DB) / max(sigma, 0.1)
            a_val = 0.147
            abs_z = np.abs(z) / math.sqrt(2.0)
            erf_a = np.sign(z) * np.sqrt(
                np.clip(
                    1.0 - np.exp(-abs_z**2 * (4.0/np.pi + a_val*abs_z**2) / (1.0 + a_val*abs_z**2)),
                    0.0, 1.0,
                )
            )
            cov_prob_per_node = (0.5 * (1.0 + erf_a)).astype(np.float32)

            # Latency: 4ms + (1-snr_norm)*46ms + M/M/1 queuing delay + edge_latency_ms
            bs_elat_at_near = pbs_elats[nearest_pbs]
            # Vectorized M/M/1: E[W] = ρ/(1−ρ) × 0.5ms — matches scalar path in env/policy
            rho_per_node  = np.minimum(bs_loads, 0.95)
            q_delay_nodes = (rho_per_node / np.maximum(1.0 - rho_per_node, 0.05)) * 0.5
            lat_ms = 4.0 + (1.0 - snr_per_node) * 46.0 + bs_elat_at_near + q_delay_nodes
            latency_per_node = (np.clip(lat_ms, 0.0, 100.0) / 50.0).astype(np.float32)

        # ── RSU cover ─────────────────────────────────────────────────────────
        if rsu_nodes:
            rsu_lats = np.array([r["lat"] for r in rsu_nodes], dtype=np.float64)
            rsu_lngs = np.array([r["lng"] for r in rsu_nodes], dtype=np.float64)
            rsu_cov  = np.array([float(r.get("coverage_radius_m", 150.0)) for r in rsu_nodes])
            dlat_r = np.radians(rsu_lats[None, :] - self.lats[:, None])
            dlng_r = np.radians(rsu_lngs[None, :] - self.lngs[:, None])
            lat2_r = np.radians(rsu_lats)[None, :]
            a      = (np.sin(dlat_r / 2) ** 2
                      + np.cos(np.radians(self.lats))[:, None] * np.cos(lat2_r) * np.sin(dlng_r / 2) ** 2)
            dist_rsu = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
            rsu_covers = (dist_rsu <= rsu_cov[None, :]).any(axis=1).astype(np.float32)

        return (bs_loads, rsu_covers, snr_per_node, nearest_bs_idx_arr,
                latency_per_node, cov_prob_per_node)

    def _build_hw_rank_per_node(self, adjacency: dict, way_tags: dict) -> np.ndarray:
        hw_ranks = np.full(self.n, 0.2, dtype=np.float32)
        for nid, neighbors in adjacency.items():
            if nid not in self.n_idx:
                continue
            i = self.n_idx[nid]
            for nb, _ in neighbors:
                hw = float(way_tags.get((nid, nb), {}).get("hw_rank", 0.2))
                if hw > hw_ranks[i]:
                    hw_ranks[i] = hw
        return hw_ranks

    def _build_edges(
        self,
        graph: dict,
        nodes_data: dict,
        adjacency: dict,
        bs_nodes: list[dict],
        pure_bs: list[dict],
        params: dict,
    ) -> tuple:
        n_idx    = self.n_idx
        way_tags = graph.get("way_tags", {})
        edge_src, edge_dst = [], []
        fast_feats = []     # [dist_n, sin_bear, cos_bear, hw, ho_on, cov_cont]
        mid_lats, mid_lngs = [], []

        active_bs = pure_bs if pure_bs else bs_nodes

        for nid, neighbors in adjacency.items():
            if nid not in n_idx:
                continue
            src = nodes_data[nid]
            i   = n_idx[nid]
            for nb, dist_m in neighbors:
                if nb not in n_idx:
                    continue
                dst  = nodes_data[nb]
                j    = n_idx[nb]
                dλ   = math.radians(dst["lng"] - src["lng"])
                φ1   = math.radians(src["lat"])
                φ2   = math.radians(dst["lat"])
                yb   = math.sin(dλ) * math.cos(φ2)
                xb   = math.cos(φ1) * math.sin(φ2) - math.sin(φ1) * math.cos(φ2) * math.cos(dλ)
                bear = math.atan2(yb, xb)
                tags = way_tags.get((nid, nb), {})
                hw   = float(tags.get("hw_rank", 0.3))
                ho_on    = 1.0 if (int(self.nearest_bs_idx_arr[i]) != int(self.nearest_bs_idx_arr[j])) else 0.0
                cov_cont = float((self.cov_prob_per_node[i] + self.cov_prob_per_node[j]) / 2.0)

                edge_src.append(i)
                edge_dst.append(j)
                fast_feats.append([dist_m / 5000.0, math.sin(bear), math.cos(bear), hw, ho_on, cov_cont])
                mid_lats.append((src["lat"] + dst["lat"]) / 2)
                mid_lngs.append((src["lng"] + dst["lng"]) / 2)

        if not edge_src:
            edge_index = torch.tensor([[], []], dtype=torch.long)
            edge_attr  = torch.tensor([[0.0] * EDGE_FEAT_DIM], dtype=torch.float32)
            return edge_index, edge_attr

        # Vectorized RF for all edge midpoints at once (replaces per-edge _node_rf_features loop)
        mid_snr_arr, mid_lat_n_arr = _build_edges_rf_vectorized(mid_lats, mid_lngs, active_bs, params)

        ef        = np.array(fast_feats, dtype=np.float32)                # [E, 6]
        mid_snr_n = np.clip(mid_snr_arr / _SNR_REF_DB, 0.0, 1.0)         # [E]
        mid_lat_n = np.minimum(mid_lat_n_arr, 2.0)                        # [E]

        # Columns: dist_n, sin_bear, cos_bear, hw, lat_n, ho_on, snr_n, cov_cont
        edge_feats_np = np.column_stack([
            ef[:, 0], ef[:, 1], ef[:, 2], ef[:, 3],
            mid_lat_n, ef[:, 4], mid_snr_n, ef[:, 5],
        ])

        edge_index = torch.tensor([edge_src, edge_dst], dtype=torch.long)
        edge_attr  = torch.tensor(edge_feats_np, dtype=torch.float32)
        return edge_index, edge_attr

    # ── Per-step fast builder (numpy vectorized, releases GIL) ───────────────

    def build_obs(
        self,
        current_node_id: str,
        dest_node_id: Optional[str] = None,
        initial_dist: float = 1.0,
        global_ctx: Optional[list] = None,
    ) -> "Data":
        """
        Build PyG Data observation for the current step.

        Parameters
        ----------
        current_node_id : str
            The vehicle's current road node.
        dest_node_id : str | None
            Destination node. Required when O/D is resampled per episode.
            None is no longer valid — always pass dest_node_id.
        initial_dist : float
            Episode start dist (origin → dest) in meters, for normalization.
        global_ctx : list[float] | None
            6-dim per-step context: [step_progress, cur_snr, cur_lat,
            cur_aoi, speed_norm, dist_to_dest_norm].
        """
        cur_idx  = self.n_idx[current_node_id]
        cur_lat  = float(self.lats[cur_idx])
        cur_lng  = float(self.lngs[cur_idx])
        dest_idx = self.n_idx.get(dest_node_id, 0) if dest_node_id else 0

        # Per-episode dest cache: recompute only when dest or initial_dist changes
        dest_cache_key = (dest_idx, initial_dist)
        if dest_cache_key != self._dest_cache_key:
            dest_lat = float(self.lats[dest_idx])
            dest_lng = float(self.lngs[dest_idx])
            dlat_d   = self._lats_rad - np.radians(dest_lat)
            dlng_d   = self._lngs_rad - np.radians(dest_lng)
            a_d      = (np.sin(dlat_d / 2) ** 2
                        + self._cos_lats * np.cos(np.radians(dest_lat)) * np.sin(dlng_d / 2) ** 2)
            dist_to_dest = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a_d, 0.0, 1.0)))
            self._dest_d2d_norm[:] = np.clip(dist_to_dest / max(initial_dist, 1.0), 0.0, 4.0)
            self._dest_is_dest[:]  = 0.0
            self._dest_is_dest[dest_idx] = 1.0
            self._dest_cache_key   = dest_cache_key

        # Per-step: current-node-relative features (uses pre-computed trig arrays)
        cur_lat_r = np.radians(cur_lat)
        dlng_r    = self._lngs_rad - np.radians(cur_lng)
        dlat_r    = self._lats_rad - cur_lat_r
        cos_cur   = np.cos(cur_lat_r)
        sin_cur   = np.sin(cur_lat_r)
        a         = np.sin(dlat_r / 2) ** 2 + cos_cur * self._cos_lats * np.sin(dlng_r / 2) ** 2
        dist_cur  = 6_371_000.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
        y_b       = np.sin(dlng_r) * self._cos_lats
        x_b       = cos_cur * self._sin_lats - sin_cur * self._cos_lats * np.cos(dlng_r)
        bears     = np.arctan2(y_b, x_b)

        # Handover risk (current node's nearest BS vs all nodes')
        cur_bs  = int(self.nearest_bs_idx_arr[cur_idx])
        ho_risk = (self.nearest_bs_idx_arr != cur_bs).astype(np.float32)

        # In-place fill pre-allocated buffer (avoids np.column_stack allocation)
        buf = self._nf_buf
        buf[:, 0] = (self.lats - cur_lat) / self.span_deg
        buf[:, 1] = (self.lngs - cur_lng) / self.span_deg
        buf[:, 2] = dist_cur / 5000.0
        buf[:, 3] = np.sin(bears)
        buf[:, 4] = np.cos(bears)
        buf[:, 5] = 0.0;  buf[cur_idx, 5] = 1.0   # is_cur
        buf[:, 6] = self._dest_is_dest
        # cols 7-9, 11, 13-14 filled once in __init__ (static)
        buf[:, 10] = ho_risk
        buf[:, 12] = self._dest_d2d_norm

        node_feats = buf.copy()  # copy so stored obs is independent of buffer

        # G11 fix: sort neighbors by dist-to-dest, not by edge distance
        raw_nb = self.adjacency.get(current_node_id, [])

        def _nb_d2d(entry):
            nb_id, _ = entry
            nb_i = self.n_idx.get(nb_id, cur_idx)
            return dist_to_dest[nb_i]

        sorted_nb = sorted(raw_nb, key=_nb_d2d)[:MAX_CANDIDATES]
        nbr_local = [self.n_idx.get(nb[0], cur_idx) for nb in sorted_nb]
        while len(nbr_local) < MAX_CANDIDATES:
            nbr_local.append(cur_idx)   # pad with self; masked by action_mask

        data = Data(
            x=torch.from_numpy(node_feats.astype(np.float32)),
            edge_index=self.edge_index,
            edge_attr=self.edge_attr,
        )
        data.current_node = cur_idx
        data.dest_node    = dest_idx
        data.node_ids     = self.node_ids
        data.neighbor_idx = torch.tensor(nbr_local, dtype=torch.long).unsqueeze(0)

        if global_ctx is not None:
            data.global_ctx = torch.tensor(global_ctx, dtype=torch.float32).unsqueeze(0)

        return data
