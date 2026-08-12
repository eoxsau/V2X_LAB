"""
V4 Inference Module — simulation integration for BS selection.

Runs the full GATv2Conv forward pass at predict_bs() time with
training-compatible 15D node features and 8D edge features.

Key design:
  build_graph()  — precomputes per-node static RF features [7-14]
                   and edge structure from OSM adjacency.
  predict_bs()   — adds dynamic position-relative features [0-6]
                   at query time, runs GNN, returns best BS node dict.

The feature space matches build_graph_obs() in universal_gnn_policy.py
exactly, so a checkpoint trained with UniversalV2XEnv is directly usable.
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch

from .universal_gnn_policy import (
    UniversalGNNPolicy,
    NODE_FEAT_DIM,
    EDGE_FEAT_DIM,
    GNN_OUT,
    GLOBAL_CTX_DIM,
    PATH_LOSS_PARAMS,
    _SNR_REF_DB,
    _node_rf_features,
    _haversine_m,
    _bearing,
)

try:
    from torch_geometric.data import Data
    _HAS_PYG = True
except ImportError:
    _HAS_PYG = False

log = logging.getLogger(__name__)


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _detect_z_dim(policy_state: dict) -> int:
    """Detect z_dim from policy state dict weight shapes (0 = MAML, >0 = PEARL)."""
    w = policy_state.get("critic_mlp.0.weight")
    if w is None:
        return 0
    in_dim = w.shape[1]
    z_dim = in_dim - (3 * GNN_OUT + GLOBAL_CTX_DIM)
    return max(0, int(z_dim))


class V4InferenceModule:
    """
    GNN BS selector for simulation integration.

    build_graph() precomputes static node features and edge structure.
    predict_bs()  computes dynamic features and runs the GNN each call.

    Thread-safety: build_graph() is NOT thread-safe.
    predict_bs()  is thread-safe after build_graph() completes.
    """

    def __init__(self, model_path: str, device: Optional[str] = None):
        if not _HAS_PYG:
            raise ImportError("torch-geometric required: pip install torch-geometric")

        self._model_path = model_path
        self._device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._policy: Optional[UniversalGNNPolicy] = None
        self._is_ready = False

        # Graph state (populated by build_graph)
        self._road_nodes: List[dict] = []    # [{id, lat, lng, ...}]
        self._bs_nodes: List[dict] = []      # [{id, lat, lng, type, ...}]
        self._all_nodes: List[dict] = []     # road_nodes + bs_nodes
        self._n_road: int = 0
        self._n_total: int = 0

        # Per-node static features [N, 8] — indices into NODE_FEAT_DIM [7:15]
        # Layout: [bs_load, rsu_cover, snr_n, ho_risk_placeholder, lat_n, cov_p, hw_n, nearest_bs]
        self._static_feat: Optional[np.ndarray] = None  # [N, 8]
        self._nearest_bs_per_node: Optional[np.ndarray] = None  # [N] int

        # Precomputed graph connectivity (from OSM adjacency)
        self._edge_index: Optional[torch.Tensor] = None   # [2, E]
        self._edge_attr: Optional[torch.Tensor] = None    # [E, EDGE_FEAT_DIM]

        # Destination / bounding-box info (set at build time)
        self._dest_lat: float = 37.5
        self._dest_lng: float = 127.0
        self._dest_node_idx: int = -1   # index of road node nearest to destination
        self._span_deg: float = 0.01    # lat/lng bounding-box span for normalization
        self._initial_dist: float = 0.0  # O→D dist; 0 = use current dist (conservative)
        self._network_mode: str = "5G"
        self._params: dict = PATH_LOSS_PARAMS["5G"]

        self._load_model()

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        path = Path(self._model_path)
        if not path.exists():
            log.warning("V4 model not found: %s", path)
            return
        try:
            state = torch.load(str(path), map_location=self._device)
            cfg = state.get("config", {})

            # Resolve policy weights — MAML saves "state_dict", PEARL saves "policy"
            policy_state = (
                state.get("policy_state")   # legacy key
                or state.get("state_dict")  # MAML
                or state.get("policy")      # PEARL
            )
            if policy_state is None:
                raise KeyError("No policy weights found in checkpoint (tried 'state_dict', 'policy', 'policy_state')")

            z_dim = cfg.get("z_dim") or _detect_z_dim(policy_state)
            self._policy = UniversalGNNPolicy(
                node_feat_dim=cfg.get("node_feat_dim", NODE_FEAT_DIM),
                edge_feat_dim=cfg.get("edge_feat_dim", EDGE_FEAT_DIM),
                z_dim=int(z_dim),
            ).to(self._device)
            self._policy.load_state_dict(policy_state)
            self._policy.eval()
            self._is_ready = True
            n_params = sum(p.numel() for p in self._policy.parameters())
            log.info(
                "V4 policy loaded: %s  z_dim=%d  params=%d",
                path.name, z_dim, n_params,
            )
        except Exception as exc:
            log.error("Failed to load V4 model from %s: %s", path, exc)
            self._is_ready = False

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._is_ready and self._policy is not None

    def build_graph(
        self,
        road_nodes: List[dict],
        bs_nodes: List[dict],
        dest_lat: float = 37.5,
        dest_lng: float = 127.0,
        network_mode: str = "5G",
        adjacency: Optional[Dict[str, list]] = None,
    ) -> None:
        """
        Build the graph structure and precompute static per-node features.

        Call when the simulation network changes (new BS placement, new region).
        For best accuracy, also set dest_lat/dest_lng to the trip destination.

        Parameters
        ----------
        road_nodes  : list of {id, lat, lng, ...} dicts from OSM graph
        bs_nodes    : list of {id, lat, lng, type, load, ...} BS node dicts
        dest_lat/lng: default destination used for d2d_n feature in predict_bs()
        network_mode: "4G", "5G", or "6G" — selects path-loss model
        adjacency   : {node_id: [(neighbor_id, dist_m), ...]} from mock_graph
        """
        if not self.is_ready:
            return
        t0 = time.perf_counter()

        self._road_nodes = road_nodes
        self._bs_nodes = bs_nodes
        self._all_nodes = list(road_nodes) + list(bs_nodes)
        self._n_road = len(road_nodes)
        self._n_total = len(self._all_nodes)
        self._dest_lat = dest_lat
        self._dest_lng = dest_lng
        self._network_mode = network_mode
        self._params = PATH_LOSS_PARAMS.get(network_mode, PATH_LOSS_PARAMS["5G"])

        if self._n_total == 0:
            log.warning("V4 build_graph: no nodes provided")
            return

        # Compute bounding-box span for coordinate normalization (matches training)
        lats = [float(n["lat"]) for n in self._all_nodes]
        lngs = [float(n["lng"]) for n in self._all_nodes]
        self._span_deg = max(
            max(lats) - min(lats),
            max(lngs) - min(lngs),
            1e-5,
        )

        # Build node-id → global-index mapping (road nodes first, then BS)
        self._id_to_idx: Dict[str, int] = {}
        for i, nd in enumerate(self._all_nodes):
            nid = str(nd.get("id", i))
            self._id_to_idx[nid] = i

        self._precompute_static_features()
        self._build_edges(adjacency)

        # Identify destination node so is_dest feature matches training
        self._dest_node_idx = self._find_nearest_road_node(dest_lat, dest_lng)

        elapsed = (time.perf_counter() - t0) * 1000
        log.debug(
            "V4 graph built: %d road + %d BS nodes, %d edges, %.1fms",
            self._n_road, len(bs_nodes),
            (self._edge_index.shape[1] if self._edge_index is not None else 0) // 2,
            elapsed,
        )

    def predict_bs(
        self,
        lat: float,
        lng: float,
        dest_lat: Optional[float] = None,
        dest_lng: Optional[float] = None,
        initial_dist: float = 0.0,
        z: Optional[torch.Tensor] = None,
    ) -> Optional[dict]:
        """
        Predict the best BS for a vehicle at (lat, lng).

        Runs a full GNN forward pass with training-compatible 15D features.
        Returns the selected BS node dict, or None on error.

        Parameters
        ----------
        lat, lng     : vehicle position
        dest_lat/lng : destination (defaults to value from build_graph())
        initial_dist : O→D distance at episode start for d2d_n normalization.
                       Pass 0 to use current distance to dest.
        z            : PEARL task latent [z_dim]. None = MAML mode.
        """
        if not self.is_ready or self._static_feat is None or not self._bs_nodes:
            return None

        d_lat = dest_lat if dest_lat is not None else self._dest_lat
        d_lng = dest_lng if dest_lng is not None else self._dest_lng
        init_d = initial_dist if initial_dist > 0 else self._initial_dist
        if init_d <= 0:
            init_d = max(_haversine_m(lat, lng, d_lat, d_lng), 1.0)

        t0 = time.perf_counter()
        try:
            # Find nearest road node to vehicle (snap to road network)
            nearest_idx = self._find_nearest_road_node(lat, lng)

            # Find nearest BS to vehicle (for ho_risk)
            nearest_bs_to_veh = self._find_nearest_bs_idx(lat, lng)

            # Build full [N, NODE_FEAT_DIM] feature tensor
            x = self._build_dynamic_features(
                lat, lng, d_lat, d_lng, init_d,
                nearest_idx, nearest_bs_to_veh,
            )

            data = Data(
                x=x,
                edge_index=self._edge_index,
                edge_attr=self._edge_attr,
            ).to(self._device)

            with torch.no_grad():
                node_embs = self._policy.encode_nodes(data)  # [N, GNN_OUT]
                pos_emb = node_embs[nearest_idx]             # [GNN_OUT]
                bs_tensor = torch.arange(
                    self._n_road, self._n_total,
                    dtype=torch.long, device=self._device,
                )
                best_local = self._policy.select_bs(node_embs, pos_emb, bs_tensor)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > 30.0:
                log.debug("V4 predict_bs slow: %.1fms", elapsed_ms)

            return self._bs_nodes[best_local]

        except Exception as exc:
            log.error("V4 predict_bs failed: %s", exc)
            return None

    def invalidate_graph(self) -> None:
        """Call when BS/RSU layout changes."""
        self._static_feat = None
        self._edge_index = None
        self._edge_attr = None

    # ── Private: static feature precomputation ────────────────────────────────

    def _precompute_static_features(self) -> None:
        """
        Precompute per-node features [7-14] that do NOT depend on vehicle position:
          [7]  bs_load
          [8]  rsu_cover   (always 0 in BS-only networks)
          [9]  snr_n
          [10] ho_risk placeholder (set to 0; computed dynamically at predict time)
          [11] lat_n
          [12] d2d_n       (to self._dest_lat/lng, recomputed at predict time)
          [13] cov_p
          [14] hw_n        (highway rank; default 0.2)
        Also fills self._nearest_bs_per_node[i] for ho_risk computation.
        """
        N = self._n_total
        feat = np.zeros((N, 8), dtype=np.float32)   # 8 = dims [7..14]
        nearest_bs = np.full(N, -1, dtype=np.int32)

        pure_bs = [b for b in self._bs_nodes
                   if str(b.get("type", "")).lower() != "roadside_unit"]
        bs_list = pure_bs or self._bs_nodes

        for i, nd in enumerate(self._all_nodes):
            node_lat = float(nd.get("lat", 0.0))
            node_lng = float(nd.get("lng", 0.0))

            bs_idx, snr_db, cov_p, lat_n = _node_rf_features(
                node_lat, node_lng, bs_list, self._params
            )
            snr_n = _clip01(snr_db / _SNR_REF_DB)

            # bs_load: from the nearest BS node's load attribute
            bs_load = 0.0
            if 0 <= bs_idx < len(bs_list):
                bs_load = float(bs_list[bs_idx].get("load", 0.0))

            # rsu_cover: 1 if any RSU (type == "roadside_unit") covers this node
            rsu_cover = 0.0
            for b in self._bs_nodes:
                if str(b.get("type", "")).lower() == "roadside_unit":
                    d = _haversine_m(node_lat, node_lng, float(b["lat"]), float(b["lng"]))
                    if d <= float(b.get("coverage_radius_m", 150.0)):
                        rsu_cover = 1.0
                        break

            # d2d_n to default destination (recomputed at predict time with actual dest)
            d2d = _haversine_m(node_lat, node_lng, self._dest_lat, self._dest_lng)
            # use a unit initial_dist here; will be rescaled at predict time
            d2d_n = min(d2d / max(
                _haversine_m(self._road_nodes[0]["lat"] if self._road_nodes else 37.5,
                             self._road_nodes[0]["lng"] if self._road_nodes else 127.0,
                             self._dest_lat, self._dest_lng), 1.0
            ), 4.0) if self._road_nodes else 0.0

            feat[i] = [bs_load, rsu_cover, snr_n, 0.0, lat_n, d2d_n, cov_p, 0.2]
            nearest_bs[i] = bs_idx

        self._static_feat = feat
        self._nearest_bs_per_node = nearest_bs

    # ── Private: dynamic feature build (per predict_bs call) ──────────────────

    def _build_dynamic_features(
        self,
        cur_lat: float, cur_lng: float,
        dest_lat: float, dest_lng: float,
        init_d: float,
        nearest_road_idx: int,
        nearest_bs_veh: int,
    ) -> torch.Tensor:
        """
        Build [N, NODE_FEAT_DIM] tensor matching build_graph_obs() exactly.

        Features [0-6] depend on current vehicle position.
        Features [7-14] come from precomputed self._static_feat.
        """
        N = self._n_total
        x = np.empty((N, NODE_FEAT_DIM), dtype=np.float32)

        for i, nd in enumerate(self._all_nodes):
            node_lat = float(nd.get("lat", 0.0))
            node_lng = float(nd.get("lng", 0.0))

            # Dynamic features [0-6]
            dlat = (node_lat - cur_lat) / self._span_deg
            dlng = (node_lng - cur_lng) / self._span_deg
            dm   = _haversine_m(cur_lat, cur_lng, node_lat, node_lng) / 5000.0
            bear = _bearing(cur_lat, cur_lng, node_lat, node_lng)

            is_cur  = 1.0 if i == nearest_road_idx else 0.0
            is_dest = 1.0 if i == self._dest_node_idx else 0.0

            # Static features [7-14]
            sf = self._static_feat[i]  # [8]
            bs_load    = sf[0]
            rsu_cover  = sf[1]
            snr_n      = sf[2]
            # ho_risk [10]: 1 if this node's nearest BS differs from vehicle's nearest BS
            ho_risk    = 1.0 if (self._nearest_bs_per_node[i] != nearest_bs_veh) else 0.0
            lat_n      = sf[4]
            # d2d_n [12]: recompute with actual dest and init_d
            d2d = _haversine_m(node_lat, node_lng, dest_lat, dest_lng)
            d2d_n = min(d2d / init_d, 4.0)
            cov_p      = sf[6]
            hw_n       = sf[7]

            x[i] = [
                dlat, dlng, dm,
                math.sin(bear), math.cos(bear),
                is_cur, is_dest,
                bs_load, rsu_cover,
                snr_n, ho_risk, lat_n, d2d_n, cov_p, hw_n,
            ]

        return torch.from_numpy(x)

    # ── Private: edge construction ────────────────────────────────────────────

    def _build_edges(self, adjacency: Optional[Dict[str, list]]) -> None:
        """
        Build edge_index and edge_attr from OSM adjacency if available,
        otherwise fall back to distance-heuristic connectivity.
        """
        if adjacency and self._road_nodes:
            self._build_edges_from_adjacency(adjacency)
        else:
            self._build_edges_heuristic()

    def _build_edges_from_adjacency(self, adjacency: Dict[str, list]) -> None:
        """Build edges from OSM adjacency, adding BS→road connections."""
        src_list, dst_list, feat_list = [], [], []

        pure_bs = [b for b in self._bs_nodes
                   if str(b.get("type", "")).lower() != "roadside_unit"]
        bs_list = pure_bs or self._bs_nodes

        for nid, neighbors in adjacency.items():
            si = self._id_to_idx.get(str(nid))
            if si is None:
                continue
            src_nd = self._all_nodes[si]
            for nb, dist_m in neighbors:
                di = self._id_to_idx.get(str(nb))
                if di is None:
                    continue
                dst_nd = self._all_nodes[di]
                bear = _bearing(
                    float(src_nd["lat"]), float(src_nd["lng"]),
                    float(dst_nd["lat"]), float(dst_nd["lng"]),
                )
                mid_lat = (float(src_nd["lat"]) + float(dst_nd["lat"])) / 2
                mid_lng = (float(src_nd["lng"]) + float(dst_nd["lng"])) / 2

                mid_bs_idx, mid_snr_db, mid_cov_p, mid_lat_n = _node_rf_features(
                    mid_lat, mid_lng, bs_list, self._params
                )
                src_bs = self._nearest_bs_per_node[si] if self._nearest_bs_per_node is not None else -1
                dst_bs = self._nearest_bs_per_node[di] if self._nearest_bs_per_node is not None else -1
                ho_edge = 1.0 if (src_bs != dst_bs) else 0.0
                mid_snr_n = _clip01(mid_snr_db / _SNR_REF_DB)

                ef = [
                    _clip01(float(dist_m) / 5000.0),  # [0] dist_norm
                    math.sin(bear),                    # [1] sin_bear
                    math.cos(bear),                    # [2] cos_bear
                    0.2,                               # [3] hw_rank (default; no way_tags)
                    float(mid_lat_n),                  # [4] mid_latency_norm
                    float(ho_edge),                    # [5] handover_on_edge
                    float(mid_snr_n),                  # [6] mid_snr_norm
                    float(mid_cov_p),                  # [7] cov_continuity
                ]
                src_list.append(si)
                dst_list.append(di)
                feat_list.append(ef)

        # Connect BS nodes to 4 nearest road nodes
        for bi, bs in enumerate(self._bs_nodes):
            gi = self._n_road + bi
            dists = sorted(
                range(self._n_road),
                key=lambda ri: _haversine_m(
                    float(bs["lat"]), float(bs["lng"]),
                    float(self._road_nodes[ri]["lat"]),
                    float(self._road_nodes[ri]["lng"]),
                ),
            )
            for ri in dists[:4]:
                dist_m = _haversine_m(
                    float(bs["lat"]), float(bs["lng"]),
                    float(self._road_nodes[ri]["lat"]),
                    float(self._road_nodes[ri]["lng"]),
                )
                ef = [_clip01(dist_m / 5000.0), 0.0, 1.0, 0.0, 0.5, 0.0, 0.5, 1.0]
                src_list += [gi, ri]
                dst_list += [ri, gi]
                feat_list += [ef, ef]

        self._store_edges(src_list, dst_list, feat_list)

    def _build_edges_heuristic(self) -> None:
        """Fallback: connect road nodes within 1500m; BS to 4 nearest road nodes."""
        MAX_EDGE_M = 1500.0
        src_list, dst_list, feat_list = [], [], []

        pure_bs = [b for b in self._bs_nodes
                   if str(b.get("type", "")).lower() != "roadside_unit"]
        bs_list = pure_bs or self._bs_nodes

        for i in range(self._n_road):
            nd_i = self._road_nodes[i]
            for j in range(i + 1, self._n_road):
                nd_j = self._road_nodes[j]
                dist_m = _haversine_m(
                    float(nd_i["lat"]), float(nd_i["lng"]),
                    float(nd_j["lat"]), float(nd_j["lng"]),
                )
                if dist_m > MAX_EDGE_M:
                    continue
                bear = _bearing(
                    float(nd_i["lat"]), float(nd_i["lng"]),
                    float(nd_j["lat"]), float(nd_j["lng"]),
                )
                mid_lat = (float(nd_i["lat"]) + float(nd_j["lat"])) / 2
                mid_lng = (float(nd_i["lng"]) + float(nd_j["lng"])) / 2
                _, mid_snr_db, mid_cov_p, mid_lat_n = _node_rf_features(
                    mid_lat, mid_lng, bs_list, self._params
                )
                src_bs = self._nearest_bs_per_node[i] if self._nearest_bs_per_node is not None else -1
                dst_bs = self._nearest_bs_per_node[j] if self._nearest_bs_per_node is not None else -1
                ho_edge = 1.0 if src_bs != dst_bs else 0.0
                mid_snr_n = _clip01(mid_snr_db / _SNR_REF_DB)
                ef = [
                    _clip01(dist_m / 5000.0),
                    math.sin(bear), math.cos(bear),
                    0.2, float(mid_lat_n), float(ho_edge),
                    float(mid_snr_n), float(mid_cov_p),
                ]
                src_list += [i, j]
                dst_list += [j, i]
                feat_list += [ef, ef]

        # BS ↔ road connections
        for bi, bs in enumerate(self._bs_nodes):
            gi = self._n_road + bi
            dists = sorted(
                range(self._n_road),
                key=lambda ri: _haversine_m(
                    float(bs["lat"]), float(bs["lng"]),
                    float(self._road_nodes[ri]["lat"]),
                    float(self._road_nodes[ri]["lng"]),
                ),
            )
            for ri in dists[:4]:
                dist_m = _haversine_m(
                    float(bs["lat"]), float(bs["lng"]),
                    float(self._road_nodes[ri]["lat"]),
                    float(self._road_nodes[ri]["lng"]),
                )
                ef = [_clip01(dist_m / 5000.0), 0.0, 1.0, 0.0, 0.5, 0.0, 0.5, 1.0]
                src_list += [gi, ri]
                dst_list += [ri, gi]
                feat_list += [ef, ef]

        self._store_edges(src_list, dst_list, feat_list)

    def _store_edges(
        self,
        src: list,
        dst: list,
        feats: list,
    ) -> None:
        if src:
            self._edge_index = torch.tensor(
                [src, dst], dtype=torch.long, device=self._device
            )
            self._edge_attr = torch.tensor(
                feats, dtype=torch.float32, device=self._device
            )
        else:
            self._edge_index = torch.zeros((2, 0), dtype=torch.long, device=self._device)
            self._edge_attr = torch.zeros((0, EDGE_FEAT_DIM), device=self._device)

    # ── Private: lookup helpers ───────────────────────────────────────────────

    def _find_nearest_road_node(self, lat: float, lng: float) -> int:
        """Return index of road node closest to (lat, lng)."""
        if not self._road_nodes:
            return 0
        best_i, best_d = 0, float("inf")
        for i, nd in enumerate(self._road_nodes):
            d = _haversine_m(lat, lng, float(nd["lat"]), float(nd["lng"]))
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def _find_nearest_bs_idx(self, lat: float, lng: float) -> int:
        """Return index (within bs_nodes) of nearest BS to (lat, lng)."""
        if not self._bs_nodes:
            return -1
        best_i, best_d = 0, float("inf")
        for i, bs in enumerate(self._bs_nodes):
            d = _haversine_m(lat, lng, float(bs["lat"]), float(bs["lng"]))
            if d < best_d:
                best_d, best_i = d, i
        return best_i
