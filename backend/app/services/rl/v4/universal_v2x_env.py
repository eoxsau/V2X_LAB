"""
Universal V2X Routing Environment — domain-randomized, GNN-compatible.

Key differences from V2XRoutingEnv (v3):
  • observation = PyG Data (variable graph) instead of fixed 37-dim vector
  • On reset(), a fresh V2XTask is sampled from DomainRandomizer
  • AoI penalty integrated into reward
  • Sionna channel lookup used when available (fallback: _L_total)
  • Handover penalty based on 3GPP TS 22.186 §5.1 (≤200ms interruption budget)

Reward decomposition (per step):
  r = w_progress × Δprogress
    − w_latency × latency_norm
    − w_handover × I(handover)
    − w_aoi × |AoI_penalty|
    − w_coverage × I(not covered)
    + w_goal × I(arrived)

Reference:
  [1] Finn et al., MAML, ICML 2017  (task = (graph, BS, RSU, O/D, density))
  [2] 3GPP TR 22.886 §6.3  (latency / reliability KPIs)
"""
from __future__ import annotations

import math
import random
from typing import Any, Optional

from .aoi_tracker import AoITracker
from .domain_randomizer import DomainRandomizer, V2XTask, _haversine_m

try:
    import torch
    from torch_geometric.data import Data
    from .universal_gnn_policy import build_graph_obs, MAX_CANDIDATES
    _TORCH_GEO_AVAILABLE = True
except ImportError:
    _TORCH_GEO_AVAILABLE = False
    MAX_CANDIDATES = 5

try:
    from app.services.buildings.building_obstruction_analyzer import (
        _L_total as _ba_L_total,
        _L_rsu   as _ba_L_rsu,
    )
    _BA_AVAILABLE = True
except ImportError:
    _BA_AVAILABLE = False

# ── Reward weights ─────────────────────────────────────────────────────────────
_W_PROGRESS  = 2.0
_W_LATENCY   = 0.3
_W_HANDOVER  = 1.0
_W_AOI       = 0.5
_W_COVERAGE  = 0.5
_W_GOAL      = 10.0

_LATENCY_NORM_MS = 50.0
_MAX_STEPS = 200


class UniversalV2XEnv:
    """
    Gym-like environment for Universal Policy training.

    observation: PyG Data  (if torch_geometric available)
                 or dict   (fallback for non-GPU environments)

    action: int in [0, MAX_CANDIDATES)
            — index into the sorted neighbor list of the current node
              (padded with -1 if fewer than MAX_CANDIDATES neighbors)
    """

    def __init__(
        self,
        randomizer: DomainRandomizer,
        channel_lookup=None,   # optional ChannelLookup (sionna)
        seed: Optional[int] = None,
        reward_weights: Optional[dict] = None,
    ) -> None:
        self._randomizer = randomizer
        self._channel_lookup = channel_lookup
        self._rng = random.Random(seed)
        self._aoi = AoITracker()

        # reward weights (allow per-experiment overrides)
        w = reward_weights or {}
        self._w_progress  = float(w.get("progress",  _W_PROGRESS))
        self._w_latency   = float(w.get("latency",   _W_LATENCY))
        self._w_handover  = float(w.get("handover",  _W_HANDOVER))
        self._w_aoi       = float(w.get("aoi",       _W_AOI))
        self._w_coverage  = float(w.get("coverage",  _W_COVERAGE))
        self._w_goal      = float(w.get("goal",      _W_GOAL))

        # will be set on reset()
        self._task: Optional[V2XTask] = None
        self._current_node: Optional[str] = None
        self._prev_bs_id: Optional[str] = None
        self._step_count: int = 0
        self._initial_dist: float = 1.0
        self._total_latency: float = 0.0
        self._handover_count: int = 0

    # ── Gym API ────────────────────────────────────────────────────────────────
    def reset(self, task: Optional[V2XTask] = None) -> tuple[Any, dict]:
        """
        Sample a new task (unless `task` is provided) and reset state.

        Returns (obs, info).
        """
        self._task = task or self._randomizer.sample_task()
        if self._task is None:
            raise RuntimeError("DomainRandomizer returned no valid task.")

        self._current_node = self._task.origin_id
        self._prev_bs_id = None
        self._step_count = 0
        self._total_latency = 0.0
        self._handover_count = 0
        self._aoi.reset()

        dest = self._task.graph["nodes"][self._task.dest_id]
        cur  = self._task.graph["nodes"][self._task.origin_id]
        self._initial_dist = _haversine_m(
            cur["lat"], cur["lng"], dest["lat"], dest["lng"]
        )

        obs = self._build_obs()
        info = {
            "region": self._task.region_name,
            "network_mode": self._task.network_mode,
            "n_nodes": len(self._task.graph["nodes"]),
            "n_bs": len(self._task.bs_nodes),
            "n_rsu": len(self._task.rsu_nodes),
        }
        return obs, info

    def step(self, action: int) -> tuple[Any, float, bool, bool, dict]:
        """
        Take action (neighbor index → 0..MAX_CANDIDATES-1).

        Returns (obs, reward, terminated, truncated, info).
        """
        if self._task is None or self._current_node is None:
            raise RuntimeError("Call reset() before step().")

        neighbors = self._sorted_neighbors(self._current_node)
        if action < 0 or action >= len(neighbors):
            # Invalid action → no movement, large penalty
            obs = self._build_obs()
            return obs, -2.0, False, True, {"invalid_action": True}

        next_node_id, edge_dist_m = neighbors[action]
        self._step_count += 1

        # ── Progress ──────────────────────────────────────────────────────────
        dest = self._task.graph["nodes"][self._task.dest_id]
        prev = self._task.graph["nodes"][self._current_node]
        nxt  = self._task.graph["nodes"][next_node_id]

        prev_dist = _haversine_m(prev["lat"], prev["lng"], dest["lat"], dest["lng"])
        next_dist = _haversine_m(nxt["lat"],  nxt["lng"],  dest["lat"], dest["lng"])
        progress_delta = (prev_dist - next_dist) / max(self._initial_dist, 1.0)

        # ── Latency ───────────────────────────────────────────────────────────
        mid_lat = (prev["lat"] + nxt["lat"]) / 2
        mid_lng = (prev["lng"] + nxt["lng"]) / 2
        bs, bs_dist, latency_ms, is_rsu = self._find_best_node(mid_lat, mid_lng)
        covered = bs is not None and bs_dist <= float(
            (bs or {}).get("coverage_radius_m", 0)
        )

        # ── Handover ──────────────────────────────────────────────────────────
        bs_id = (bs or {}).get("id")
        handover = (self._prev_bs_id is not None and bs_id != self._prev_bs_id)
        if handover:
            self._handover_count += 1
        self._prev_bs_id = bs_id

        # ── AoI ──────────────────────────────────────────────────────────────
        if covered:
            self._aoi.update(bs_id or "default")
        aoi_penalty = self._aoi.reward_penalty(bs_id or "default")

        # ── Move ─────────────────────────────────────────────────────────────
        self._current_node = next_node_id
        self._total_latency += latency_ms

        # ── Reward ────────────────────────────────────────────────────────────
        reward = (
            self._w_progress * progress_delta
            - self._w_latency * (latency_ms / _LATENCY_NORM_MS)
            - self._w_handover * float(handover)
            + self._w_aoi * aoi_penalty
            - self._w_coverage * (0.0 if covered else 1.0)
        )

        # ── Termination ───────────────────────────────────────────────────────
        arrived = (next_node_id == self._task.dest_id) or (next_dist < 20.0)
        if arrived:
            reward += self._w_goal

        terminated = arrived
        truncated  = self._step_count >= _MAX_STEPS

        obs = self._build_obs()
        info = {
            "node_id":         next_node_id,
            "latency_ms":      round(latency_ms, 2),
            "covered":         covered,
            "handover":        handover,
            "handover_count":  self._handover_count,
            "aoi_penalty":     round(aoi_penalty, 4),
            "progress":        round(1.0 - next_dist / max(self._initial_dist, 1.0), 3),
            "arrived":         arrived,
            "step":            self._step_count,
            "avg_latency_ms":  round(self._total_latency / self._step_count, 2),
            "aoi_stats":       self._aoi.episode_stats(),
        }
        return obs, reward, terminated, truncated, info

    def action_masks(self) -> list[bool]:
        """True for valid actions (neighbor exists), False for padding."""
        if self._current_node is None:
            return [False] * MAX_CANDIDATES
        neighbors = self._sorted_neighbors(self._current_node)
        mask = [False] * MAX_CANDIDATES
        for i in range(min(len(neighbors), MAX_CANDIDATES)):
            mask[i] = True
        return mask

    def valid_actions(self) -> list[int]:
        return [i for i, v in enumerate(self.action_masks()) if v]

    # ── Observation builder ────────────────────────────────────────────────────
    def _build_obs(self) -> Any:
        if not _TORCH_GEO_AVAILABLE or self._task is None:
            return self._build_dict_obs()
        try:
            return build_graph_obs(
                graph=self._task.graph,
                bs_nodes=self._task.bs_nodes + self._task.rsu_nodes,
                rsu_nodes=self._task.rsu_nodes,
                current_node_id=self._current_node,
                dest_node_id=self._task.dest_id,
                channel_lookup=self._channel_lookup,
            )
        except Exception:
            return self._build_dict_obs()

    def _build_dict_obs(self) -> dict:
        """Minimal dict observation for environments without PyG."""
        if self._task is None or self._current_node is None:
            return {}
        nd = self._task.graph["nodes"].get(self._current_node, {})
        dest_nd = self._task.graph["nodes"].get(self._task.dest_id, {})
        lat, lng = nd.get("lat", 0.0), nd.get("lng", 0.0)
        dist = _haversine_m(lat, lng, dest_nd.get("lat", 0.0), dest_nd.get("lng", 0.0))
        return {
            "lat": lat, "lng": lng,
            "dist_to_dest_m": dist,
            "step": self._step_count,
        }

    # ── Network helpers ────────────────────────────────────────────────────────
    def _sorted_neighbors(self, node_id: str) -> list[tuple[str, float]]:
        """Neighbors sorted by ascending distance, padded conceptually to MAX_CANDIDATES."""
        adj = self._task.graph["adjacency"] if self._task else {}
        raw = adj.get(node_id, [])
        # sort by distance and cap
        sorted_nb = sorted(raw, key=lambda t: t[1])[:MAX_CANDIDATES]
        return sorted_nb

    def _find_best_node(
        self,
        mid_lat: float,
        mid_lng: float,
    ) -> tuple[Optional[dict], float, float, bool]:
        """
        Find the best BS or RSU for the given midpoint.

        Returns (best_node, dist_m, latency_ms, is_rsu).
        """
        if self._task is None:
            return None, float("inf"), 20.0, False

        all_nodes = self._task.bs_nodes + self._task.rsu_nodes
        if not all_nodes:
            return None, float("inf"), 20.0, False

        best_node = None
        best_score = float("inf")
        best_dist = float("inf")
        best_lat = 20.0
        best_is_rsu = False

        net = self._task.network_mode

        for nd in all_nodes:
            d = _haversine_m(mid_lat, mid_lng, nd["lat"], nd["lng"])
            cov = float(nd.get("coverage_radius_m", 400.0))
            if d > cov * 1.5:   # skip obviously out-of-range nodes
                continue

            is_rsu = str(nd.get("type", "")).lower() in ("rsu", "roadside_unit")
            edge_lat = float(nd.get("edge_latency_ms") or (0.5 if is_rsu else 3.0))

            if _BA_AVAILABLE:
                n_veh = (
                    1
                    + int(nd.get("n_background_vehicles") or 0)
                    + int(nd.get("n_other_devices") or 0)
                    + int(nd.get("n_its_load") or 0)
                )
                if is_rsu:
                    lat_ms = _ba_L_rsu(d, cov) + edge_lat
                else:
                    l_air, _, _, _ = _ba_L_total(d, 0.0, n_veh, net)
                    lat_ms = l_air + edge_lat
            else:
                lat_ms = 4.0 + (d / max(cov, 1.0)) * 15.0 + edge_lat

            # Score = latency + heavy penalty if outside coverage
            score = lat_ms + (100.0 if d > cov else 0.0)
            if score < best_score:
                best_score = score
                best_node  = nd
                best_dist  = d
                best_lat   = lat_ms
                best_is_rsu = is_rsu

        return best_node, best_dist, best_lat, best_is_rsu

    # ── Properties ────────────────────────────────────────────────────────────
    @property
    def current_task(self) -> Optional[V2XTask]:
        return self._task

    @property
    def n_actions(self) -> int:
        return MAX_CANDIDATES
