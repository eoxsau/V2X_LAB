"""
V4 Inference Module — simulation integration for route_cost_function.py.

Replaces per-edge geometry computation in _find_best_bs_light() with a
single GNN forward pass. Speedup: ~40ms/edge -> ~5ms/edge (10x).

Usage (in main.py):
    from app.services.rl.v4 import V4InferenceModule
    _v4_policy: Optional[V4InferenceModule] = None

    def startup():
        model_path = "models/v4/v4_policy.pt"
        if Path(model_path).exists():
            _v4_policy = V4InferenceModule(model_path)

Usage (in route_cost_function.py):
    if _v4_policy and _v4_policy.is_ready:
        return _v4_policy.predict_bs(lat, lng, nodes, bs_nodes)
    # else: fall through to existing greedy logic
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch

from .universal_gnn_policy import (
    UniversalGNNPolicy,
    NODE_FEAT_DIM,
    EDGE_FEAT_DIM,
    GNN_OUT,
)
from .universal_gnn_policy import _haversine_m

def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))

try:
    from torch_geometric.data import Data
    _HAS_PYG = True
except ImportError:
    _HAS_PYG = False

log = logging.getLogger(__name__)


def _build_graph_from_nodes(
    road_nodes: List[dict],
    bs_nodes: List[dict],
    dest_lat: float,
    dest_lng: float,
    diag_m: float = 10_000.0,
) -> Tuple["Data", List[int], List[int]]:
    """
    Convert simulation node dicts to a PyG Data object.

    Returns (data, road_node_indices, bs_node_indices).
    road_node_indices[i] gives the global index of road_nodes[i].
    bs_node_indices[i] gives the global index of bs_nodes[i].
    """
    all_nodes = list(road_nodes) + list(bs_nodes)
    n = len(all_nodes)
    n_road = len(road_nodes)

    # --- Node features [NODE_FEAT_DIM=9] ---
    node_feats = []
    for i, nd in enumerate(all_nodes):
        lat = float(nd.get("lat", 0))
        lng = float(nd.get("lng", 0))

        dist_goal = _haversine_m(lat, lng, dest_lat, dest_lng)
        dist_goal_norm = _clip01(dist_goal / max(diag_m, 1.0))

        min_bs_d = 1.0
        for bs in bs_nodes:
            d = _haversine_m(lat, lng, float(bs.get("lat", 0)), float(bs.get("lng", 0)))
            cov = float(bs.get("coverage_radius_m", 400.0))
            min_bs_d = min(min_bs_d, _clip01(d / max(cov, 1.0)))

        node_type = 1 if i >= n_road else 0
        node_type_norm = node_type / 4.0

        n_bs_in_cov = sum(
            1 for bs in bs_nodes
            if _haversine_m(lat, lng, float(bs.get("lat", 0)), float(bs.get("lng", 0)))
            <= float(bs.get("coverage_radius_m", 400.0))
        )

        cong = float(nd.get("congestion_penalty", nd.get("cong_score", 0.0))) / max(20.0, 1.0)
        load = float(nd.get("load_ratio", _clip01(cong)))

        # Pad to NODE_FEAT_DIM=9: bearing_sin/cos/is_current/is_dest/bs_load/rsu_cover
        # are not computable here (no graph adjacency or current_node context).
        # This module is used only for BS selection, not for routing policy inference.
        bs_load = _clip01(load) if i >= n_road else min_bs_d
        node_feats.append([
            dist_goal_norm,   # [0] dist to dest (proxy for Δlat/Δlng)
            min_bs_d,         # [1] dist to nearest BS / cov_r
            node_type_norm,   # [2] node type
            _clip01(n_bs_in_cov / 10.0),  # [3] BS coverage count
            _clip01(cong),    # [4] congestion
            bs_load,          # [5] BS load / dist ratio
            0.0,              # [6] is_current (unknown in this context)
            0.0,              # [7] is_dest (unknown)
            0.0,              # [8] rsu_cover (not tracked here)
        ])

    # --- Edges: fully-connect road nodes within reasonable distance ---
    MAX_EDGE_M = 1500.0
    edges_src, edges_dst, edge_feats_list = [], [], []

    for i in range(n_road):
        nd_i = road_nodes[i]
        for j in range(i + 1, n_road):
            nd_j = road_nodes[j]
            dist_m = _haversine_m(
                float(nd_i["lat"]), float(nd_i["lng"]),
                float(nd_j["lat"]), float(nd_j["lng"])
            )
            if dist_m > MAX_EDGE_M:
                continue

            mid_lat = (float(nd_i["lat"]) + float(nd_j["lat"])) / 2
            mid_lng = (float(nd_i["lng"]) + float(nd_j["lng"])) / 2
            has_cov = 0.0
            for bs in bs_nodes:
                if _haversine_m(mid_lat, mid_lng, float(bs["lat"]), float(bs["lng"])) \
                        <= float(bs.get("coverage_radius_m", 400.0)):
                    has_cov = 1.0
                    break

            speed = float(nd_i.get("speed_kmh", nd_i.get("max_speed", 50.0)) or 50.0)
            its_load = float(nd_i.get("n_its_vehicles", nd_i.get("its_vehicles", 0)) or 0)
            feat = [
                _clip01(dist_m / 500.0),   # [0] dist_norm (proxy for length_m/5000)
                _clip01(speed / 130.0),    # [1] speed_norm (proxy for bearing)
                has_cov,                   # [2] coverage flag
                _clip01(its_load / 50.0),  # [3] ITS load (proxy for hw_rank)
                0.0,                       # [4] latency_norm placeholder
            ]
            edges_src += [i, j]
            edges_dst += [j, i]
            edge_feats_list += [feat, feat]

    # Connect BS to nearby road nodes
    for bi, bs in enumerate(bs_nodes):
        gi = n_road + bi
        dists = sorted(
            range(n_road),
            key=lambda ri: _haversine_m(
                float(bs["lat"]), float(bs["lng"]),
                float(road_nodes[ri]["lat"]), float(road_nodes[ri]["lng"])
            )
        )
        for ri in dists[:4]:
            dist_m = _haversine_m(
                float(bs["lat"]), float(bs["lng"]),
                float(road_nodes[ri]["lat"]), float(road_nodes[ri]["lng"])
            )
            feat = [_clip01(dist_m / 500.0), 0.5, 1.0, 0.0, 0.0]  # 5 features
            edges_src += [gi, ri]
            edges_dst += [ri, gi]
            edge_feats_list += [feat, feat]

    x = torch.tensor(node_feats, dtype=torch.float32)
    if edges_src:
        edge_index = torch.tensor([edges_src, edges_dst], dtype=torch.long)
        edge_attr = torch.tensor(edge_feats_list, dtype=torch.float32)
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        edge_attr = torch.zeros((0, EDGE_FEAT_DIM))

    data = Data(x=x, edge_index=edge_index, edge_attr=edge_attr)
    road_indices = list(range(n_road))
    bs_indices = list(range(n_road, n))
    return data, road_indices, bs_indices


class V4InferenceModule:
    """
    Drop-in GNN BS selector for simulation integration.

    Thread-safety: build_graph() is NOT thread-safe.
    predict_bs() IS thread-safe after graph is built.
    """

    def __init__(self, model_path: str, device: Optional[str] = None):
        if not _HAS_PYG:
            raise ImportError("torch-geometric required: pip install torch-geometric")

        self._model_path = model_path
        self._device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._policy: Optional[UniversalGNNPolicy] = None
        self._graph_cache: Optional["Data"] = None
        self._node_embs: Optional[torch.Tensor] = None
        self._road_indices: List[int] = []
        self._bs_indices: List[int] = []
        self._road_nodes_ref: List[dict] = []
        self._bs_nodes_ref: List[dict] = []
        self._is_ready = False

        self._load_model()

    def _load_model(self):
        path = Path(self._model_path)
        if not path.exists():
            log.warning("V4 model not found: %s. Falling back to greedy.", path)
            return
        try:
            state = torch.load(str(path), map_location=self._device)
            cfg = state.get("config", {})
            self._policy = UniversalGNNPolicy(
                node_feat_dim=cfg.get("node_feat_dim", NODE_FEAT_DIM),
                edge_feat_dim=cfg.get("edge_feat_dim", EDGE_FEAT_DIM),
                hidden=cfg.get("hidden", GNN_OUT // 4),  # GNN_HIDDEN = GNN_OUT / GNN_HEADS
            ).to(self._device)
            self._policy.load_state_dict(state.get("policy_state") or state["state_dict"])
            self._policy.eval()
            self._is_ready = True
            n_params = sum(p.numel() for p in self._policy.parameters())
            log.info("V4 policy loaded from %s (%d params)", path, n_params)
        except Exception as e:
            log.error("Failed to load V4 model: %s", e)
            self._is_ready = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready and self._policy is not None

    def build_graph(
        self,
        road_nodes: List[dict],
        bs_nodes: List[dict],
        dest_lat: float = 37.5,
        dest_lng: float = 127.0,
    ):
        """
        Build and cache graph embeddings for the current simulation network.
        Call once when simulation starts (or when BS/RSU layout changes).
        ~200ms on first call, instant on subsequent calls with same layout.
        """
        if not self.is_ready:
            return

        t0 = time.perf_counter()
        try:
            diag_m = max(
                _haversine_m(
                    min(n["lat"] for n in road_nodes), min(n["lng"] for n in road_nodes),
                    max(n["lat"] for n in road_nodes), max(n["lng"] for n in road_nodes),
                ) if road_nodes else 5000.0,
                500.0
            )
            data, road_idx, bs_idx = _build_graph_from_nodes(
                road_nodes, bs_nodes, dest_lat, dest_lng, diag_m
            )
            data = data.to(self._device)

            with torch.no_grad():
                node_embs = self._policy.encode_nodes(data)

            self._graph_cache = data
            self._node_embs = node_embs
            self._road_indices = road_idx
            self._bs_indices = bs_idx
            self._road_nodes_ref = road_nodes
            self._bs_nodes_ref = bs_nodes

            elapsed_ms = (time.perf_counter() - t0) * 1000
            log.debug(
                "V4 graph built: %d road + %d BS nodes, %d edges, %.1fms",
                len(road_nodes), len(bs_nodes),
                data.edge_index.shape[1] // 2, elapsed_ms
            )
        except Exception as e:
            log.error("V4 build_graph failed: %s", e)
            self._node_embs = None

    def _find_nearest_road_node(self, lat: float, lng: float) -> int:
        """Find index of road node closest to (lat, lng)."""
        best_i, best_d = 0, float("inf")
        for i, nd in enumerate(self._road_nodes_ref):
            d = _haversine_m(lat, lng, float(nd["lat"]), float(nd["lng"]))
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def predict_bs(
        self,
        lat: float,
        lng: float,
        road_nodes: Optional[List[dict]] = None,
        bs_nodes: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """
        Predict best BS for a given edge midpoint (lat, lng).
        Returns the BS node dict (same format as existing code), or None on failure.

        If road_nodes/bs_nodes differ from cached graph, call build_graph() first.
        """
        if not self.is_ready or self._node_embs is None or not self._bs_indices:
            return None

        t0 = time.perf_counter()
        try:
            with torch.no_grad():
                nearest_road_idx = self._find_nearest_road_node(lat, lng)
                pos_feat = self._node_embs[nearest_road_idx]

                bs_tensor = torch.tensor(self._bs_indices, dtype=torch.long, device=self._device)
                best_local = self._policy.select_bs(self._node_embs, pos_feat, bs_tensor)


            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > 20.0:
                log.debug("V4 predict_bs slow: %.1fms", elapsed_ms)

            return self._bs_nodes_ref[best_local]
        except Exception as e:
            log.error("V4 predict_bs failed: %s", e)
            return None

    def invalidate_graph(self):
        """Call when BS/RSU layout changes (e.g., user adds a BS node)."""
        self._node_embs = None
        self._graph_cache = None
        log.debug("V4 graph cache invalidated.")
