"""
V4 Routing Adapter — bridges UniversalGNNPolicy with V2XRoutingEnv.

At each routing step, builds a graph observation using the same
build_graph_obs() path as training, then calls policy.act().
This guarantees the feature space seen at inference matches training exactly.

Usage:
    policy = UniversalGNNPolicy.load("models/v4/v4_policy.pt")
    adapter = V4RoutingAdapter(policy)
    adapter.build_graph(mock_graph, bs_nodes, dest_node_id)

    # Inside rl_trainer._select_action:
    action = adapter.select_action(current_node_id, candidate_edges)
"""
from __future__ import annotations

import logging
from typing import List, Optional

import torch

import math

from .universal_gnn_policy import (
    UniversalGNNPolicy,
    build_graph_obs,
    MAX_CANDIDATES,
    PATH_LOSS_PARAMS,
    _SNR_REF_DB,
    _node_rf_features,
    _haversine_m,
)

try:
    import torch_geometric  # noqa: F401
    _HAS_PYG = True
except ImportError:
    _HAS_PYG = False

log = logging.getLogger(__name__)


class V4RoutingAdapter:
    """
    Connects GATv2Conv policy to V2XRoutingEnv routing decisions.

    build_graph(): store graph + BS config (once per destination change).
    select_action(): rebuild obs at current node → policy.act(deterministic=True).

    candidate_edges must be sorted by distance (ascending) so action index 0
    corresponds to the nearest candidate — matching the training environment.
    """

    def __init__(self, policy: UniversalGNNPolicy, device: Optional[torch.device] = None):
        if not _HAS_PYG:
            raise ImportError("torch-geometric required: pip install torch-geometric")
        self.policy = policy
        self.device = device or torch.device("cpu")
        self._graph: Optional[dict] = None
        self._bs_nodes: List[dict] = []
        self._dest_node_id: Optional[str] = None
        self._dest_lat: float = 0.0
        self._dest_lng: float = 0.0
        self._initial_dist: float = 0.0   # O→D distance set at episode start
        self._task_z: Optional[torch.Tensor] = None  # PEARL task latent (None = MAML mode)
        self._network_mode: str = "5G"
        self._params: dict = PATH_LOSS_PARAMS["5G"]
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self._graph is not None and self._dest_node_id is not None

    def set_task_z(self, z: Optional[torch.Tensor]) -> None:
        """Inject PEARL task latent. Call after infer_task_z() in PEARLTrainer."""
        self._task_z = z.to(self.device) if z is not None else None

    def set_episode_start(self, origin_node_id: str) -> None:
        """Record the O→D initial distance for this episode's d2d_n normalization."""
        if self._graph is None or self._dest_node_id is None:
            return
        nodes = self._graph.get("nodes", {})
        orig = nodes.get(origin_node_id)
        dest = nodes.get(self._dest_node_id)
        if orig and dest:
            self._initial_dist = max(
                _haversine_m(orig["lat"], orig["lng"], dest["lat"], dest["lng"]), 1.0
            )

    def build_graph(
        self,
        graph: dict,
        bs_nodes: List[dict],
        dest_node_id: str,
        network_mode: str = "5G",
    ) -> None:
        """
        Store graph and BS config for subsequent select_action() calls.
        Call once per destination (or when BS placement changes).

        graph format: {"nodes": {node_id: {"lat", "lng", ...}},
                       "adjacency": {node_id: [(nbr_id, dist_m), ...]}}
        """
        self._graph = graph
        self._bs_nodes = bs_nodes
        self._dest_node_id = dest_node_id
        self._initial_dist = 0.0  # reset; caller sets via set_episode_start()
        self._network_mode = network_mode
        self._params = PATH_LOSS_PARAMS.get(network_mode, PATH_LOSS_PARAMS["5G"])

        # Cache destination coordinates for global_ctx computation
        nodes = graph.get("nodes", {})
        dest = nodes.get(dest_node_id, {})
        self._dest_lat = float(dest.get("lat", 0.0))
        self._dest_lng = float(dest.get("lng", 0.0))

        self._ready = True
        log.debug(
            "V4 adapter ready: %d road nodes, %d BS, dest=%s, mode=%s",
            len(nodes), len(bs_nodes), dest_node_id, network_mode,
        )

    def _compute_global_ctx(
        self,
        current_node_id: str,
        effective_bs: List[dict],
        speed_kmh: float = 50.0,
    ) -> Optional[list]:
        """
        Compute the 6-dim global context vector matching training:
          [snr_n, cov_p, lat_n, d2d_n, speed_n, ho_risk]
        Returns None if graph nodes are unavailable (falls back to zeros in policy).
        """
        nodes = self._graph.get("nodes", {}) if self._graph else {}
        cur = nodes.get(current_node_id)
        if cur is None or not effective_bs:
            return None

        cur_lat, cur_lng = float(cur["lat"]), float(cur["lng"])
        _, snr_db, cov_p, lat_n = _node_rf_features(cur_lat, cur_lng, effective_bs, self._params)
        snr_n = float(max(0.0, min(1.0, snr_db / _SNR_REF_DB)))

        d2d = _haversine_m(cur_lat, cur_lng, self._dest_lat, self._dest_lng)
        init_d = self._initial_dist if self._initial_dist > 0 else max(d2d, 1.0)
        d2d_n = min(d2d / init_d, 4.0)

        speed_n = speed_kmh / 120.0

        # ho_risk: SNR below 50% of reference suggests borderline coverage
        ho_risk = float(max(0.0, 1.0 - snr_n * 2.0))

        return [snr_n, float(cov_p), float(lat_n), float(d2d_n), speed_n, ho_risk]

    def select_action(
        self,
        current_node_id: str,
        candidate_edges: list,
        dest_lat: float = 0.0,
        dest_lng: float = 0.0,
        bs_nodes: Optional[List[dict]] = None,
        speed_kmh: float = 50.0,
        z: Optional[torch.Tensor] = None,
    ) -> int:
        """
        Build a graph observation at the current position, run policy.act(),
        and return the best candidate edge index.

        Parameters
        ----------
        current_node_id : str
            The node the vehicle is currently at.
        candidate_edges : list[EdgeInfo]
            Outgoing candidate edges. MUST be pre-sorted by dist(neighbor→dest)
            ascending — matching the training env's _sorted_neighbors() order (G11 fix).
            Sorting by edge distance would cause action index mismatch.
        dest_lat, dest_lng : float
            Destination coordinates for sorting. If unknown, pass 0/0 (no reorder).
        bs_nodes : optional
            Override for BS list (if None, uses the list from build_graph()).
        speed_kmh : float
            Vehicle speed in km/h for global_ctx feature [4]. Default 50 km/h.
        z : optional torch.Tensor
            PEARL task latent [z_dim]. If None, uses stored _task_z (or None for MAML).

        Returns
        -------
        int : index into candidate_edges [0, len-1]
        """
        if not self.is_ready:
            return 0

        effective_bs = bs_nodes if bs_nodes is not None else self._bs_nodes
        n_valid = min(len(candidate_edges), MAX_CANDIDATES)
        if n_valid == 0:
            return 0

        effective_z = z if z is not None else self._task_z

        try:
            global_ctx = self._compute_global_ctx(current_node_id, effective_bs, speed_kmh)

            data = build_graph_obs(
                self._graph,
                effective_bs,
                [],           # rsu_nodes — not available in sim mode
                current_node_id,
                self._dest_node_id,
                network_mode=self._network_mode,
                initial_dist=self._initial_dist,
                global_ctx=global_ctx,
            )
            data = data.to(self.device)

            mask = torch.zeros(MAX_CANDIDATES, dtype=torch.bool, device=self.device)
            mask[:n_valid] = True

            self.policy.eval()
            with torch.no_grad():
                action, _, _ = self.policy.act(
                    data, action_mask=mask, deterministic=True, z=effective_z
                )

            return min(int(action), n_valid - 1)

        except Exception as e:
            log.error("V4RoutingAdapter.select_action failed: %s", e)
            return 0

    def adapt_to_task(
        self,
        task,                        # V2XTask from DomainRandomizer
        randomizer,                  # DomainRandomizer (for env creation)
        inner_steps: int = 5,
        inner_lr: float = 1e-3,
        support_episodes: int = 6,
        channel_lookup=None,
    ) -> None:
        """
        Run MAML inner-loop adaptation on the customer's task.

        Clones the meta-policy, collects support_episodes on the task graph,
        runs inner_steps SGD updates, then replaces self.policy with the
        adapted version. Call once per new customer graph before routing.

        This delivers the core MAML value: few-shot specialisation to any
        customer scenario without retraining the meta-model.
        """
        import copy
        import torch.optim as optim
        from .universal_v2x_env import UniversalV2XEnv
        from .universal_gnn_policy import build_graph_obs

        adapted = copy.deepcopy(self.policy).to(self.device)
        adapted.train()
        opt = optim.SGD(adapted.parameters(), lr=inner_lr)

        env = UniversalV2XEnv(
            randomizer,
            channel_lookup=channel_lookup,
            max_steps=200,
            use_icm=False,   # inference: no ICM needed
        )

        try:
            from torch_geometric.data import Batch as _Batch
        except ImportError:
            log.warning("adapt_to_task: torch_geometric not available, skipping adaptation")
            return

        for _step in range(inner_steps):
            episodes_data = []
            for _ in range(support_episodes):
                obs, _ = env.reset(task=task, resample_od=True)
                done = False
                step_obs, step_acts, step_lps, step_rews, step_vals, step_masks = [], [], [], [], [], []
                while not done and len(step_obs) < 200:
                    data = obs.to(self.device) if hasattr(obs, "to") else obs
                    if hasattr(data, "node_ids"):
                        del data.node_ids
                    mask_list = env.action_masks()
                    mask = torch.tensor(mask_list, dtype=torch.bool, device=self.device)
                    if not mask.any():
                        break
                    with torch.no_grad():
                        logits, value = adapted(data, action_mask=mask)
                    dist   = torch.distributions.Categorical(logits=logits)
                    action = dist.sample()
                    lp     = dist.log_prob(action)
                    obs, rew, terminated, truncated, _ = env.step(action.item())
                    done = terminated or truncated
                    step_obs.append(data); step_acts.append(action.item())
                    step_lps.append(lp.item()); step_rews.append(rew)
                    step_vals.append(value.item()); step_masks.append(mask_list)
                episodes_data.append((step_obs, step_acts, step_lps, step_rews, step_vals, step_masks))

            if not any(ep[0] for ep in episodes_data):
                continue

            # Simple policy-gradient loss for adaptation (no PPO clip for speed)
            all_obs, all_acts, all_advs, all_masks_t = [], [], [], []
            for obs_l, act_l, _, rew_l, val_l, mask_l in episodes_data:
                if not obs_l:
                    continue
                T = len(rew_l)
                ret = 0.0; rets = []
                for r in reversed(rew_l):
                    ret = r + 0.99 * ret; rets.insert(0, ret)
                rets_t = torch.tensor(rets, dtype=torch.float32, device=self.device)
                vals_t = torch.tensor(val_l, dtype=torch.float32, device=self.device)
                advs   = (rets_t - vals_t).detach()
                all_obs.extend(obs_l); all_acts.extend(act_l)
                all_advs.extend(advs.tolist()); all_masks_t.extend(mask_l)

            if not all_obs:
                continue
            batch = _Batch.from_data_list(all_obs)
            acts_t  = torch.tensor(all_acts,  dtype=torch.long,    device=self.device)
            advs_t  = torch.tensor(all_advs,  dtype=torch.float32, device=self.device)
            masks_t = torch.tensor(all_masks_t, dtype=torch.bool,  device=self.device)
            if advs_t.numel() > 1:
                advs_t = (advs_t - advs_t.mean()) / (advs_t.std() + 1e-8)

            logits_b, vals_b = adapted(batch, action_mask=masks_t)
            lp_b = torch.distributions.Categorical(logits=logits_b).log_prob(acts_t)
            loss = -(lp_b * advs_t).mean()

            if torch.isnan(loss):
                continue
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(adapted.parameters(), max_norm=1.0)
            opt.step()

        adapted.eval()
        self.policy = adapted
        log.info("adapt_to_task: %d inner steps complete", inner_steps)

    def invalidate(self) -> None:
        """Invalidate stored graph (call when BS placement changes)."""
        self._ready = False
        self._graph = None
        self._dest_node_id = None
        log.debug("V4 adapter invalidated.")
