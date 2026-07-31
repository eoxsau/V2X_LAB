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

from .universal_gnn_policy import (
    UniversalGNNPolicy,
    build_graph_obs,
    MAX_CANDIDATES,
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
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self._graph is not None and self._dest_node_id is not None

    def build_graph(
        self,
        graph: dict,
        bs_nodes: List[dict],
        dest_node_id: str,
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
        self._ready = True
        log.debug(
            "V4 adapter ready: %d road nodes, %d BS, dest=%s",
            len(graph.get("nodes", {})), len(bs_nodes), dest_node_id,
        )

    def select_action(
        self,
        current_node_id: str,
        candidate_edges: list,
        bs_nodes: Optional[List[dict]] = None,
    ) -> int:
        """
        Build a graph observation at the current position, run policy.act(),
        and return the best candidate edge index.

        Parameters
        ----------
        current_node_id : str
            The node the vehicle is currently at.
        candidate_edges : list[EdgeInfo]
            Outgoing candidate edges, sorted by distance ascending
            (same order as the training env after the _get_candidates sort fix).
        bs_nodes : optional
            Override for BS list (if None, uses the list from build_graph()).

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

        try:
            data = build_graph_obs(
                self._graph,
                effective_bs,
                [],           # rsu_nodes — not available in sim mode
                current_node_id,
                self._dest_node_id,
            )
            data = data.to(self.device)

            mask = torch.zeros(MAX_CANDIDATES, dtype=torch.bool, device=self.device)
            mask[:n_valid] = True

            self.policy.eval()
            with torch.no_grad():
                action, _, _ = self.policy.act(data, action_mask=mask, deterministic=True)

            return min(int(action), n_valid - 1)

        except Exception as e:
            log.error("V4RoutingAdapter.select_action failed: %s", e)
            return 0

    def invalidate(self) -> None:
        """Invalidate stored graph (call when BS placement changes)."""
        self._ready = False
        self._graph = None
        self._dest_node_id = None
        log.debug("V4 adapter invalidated.")
