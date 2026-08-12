"""
Universal V2X Routing Environment — domain-randomized, GNN-compatible.

Key differences from V2XRoutingEnv (v3):
  • observation = PyG Data (variable graph) instead of fixed 37-dim vector
  • On reset(), a fresh V2XTask is sampled from DomainRandomizer
  • AoI tracker uses sim-step time (BUG-1 fix); tick() called every step
  • GraphTaskCache reused across episodes for the same task (BUG-6 fix)
  • Neighbor sort: by dist(neighbor→dest), not edge length (G11 fix)
  • resample_od=True samples fresh O/D pair per episode (D1 fix)
  • global_ctx (6-dim) injected into Critic observation every step

Reward decomposition (10 components):
  r = w_progress   × Δprogress
    − w_latency    × latency_norm          (includes M/M/1 queuing delay)
    − w_handover   × I(handover)
    − w_aoi        × aoi_normalized
    − w_coverage   × I(not covered)
    − w_speed_pen  × I(speed_over_limit)
    + w_prr        × prr_estimate
    + w_sinr_bonus × sinr_bonus
    + w_goal       × I(arrived)
    − w_revisit    × I(node_already_visited)

References:
  [1] Finn et al., MAML, ICML 2017
  [2] 3GPP TR 22.886 §6.3  (latency / reliability KPIs)
  [3] Fernandez et al., IEEE WCL 2014 (path loss model)
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any, Optional

from .aoi_tracker import AoITracker
from .domain_randomizer import DomainRandomizer, V2XTask, _haversine_m, _bfs_reachable

try:
    import torch
    from torch_geometric.data import Data
    from .universal_gnn_policy import (
        build_graph_obs, GraphTaskCache, MAX_CANDIDATES,
        PATH_LOSS_PARAMS, _P_NOISE_DBM, _SNR_REF_DB, _SNR_MIN_DB,
        _compute_snr_db, _coverage_prob,
    )
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
_W_PROGRESS   = 3.0    # directional signal; dominant per-step reward
_W_LATENCY    = 0.05   # e2e latency (normalized)
_W_HANDOVER   = 0.3    # 3GPP TS 22.186: ≤200ms interruption
_W_AOI        = 0.1    # AoI penalty (no internal 0.5 scale — BUG-5 fix)
_W_COVERAGE   = 0.1    # out-of-coverage step penalty
_W_SPEED_PEN  = 0.05   # penalty for high speed causing link instability
_W_PRR        = 0.3    # packet reception ratio bonus
_W_SINR_BONUS = 0.1    # bonus for good SINR margin
_W_GOAL       = 20.0   # terminal goal reward; must dominate sum of per-step costs
_W_REVISIT    = 0.3    # penalty for revisiting a node — set to 0 when ICM is active (ICM replaces this)

_LATENCY_NORM_MS  = 50.0
_MAX_STEPS        = 200     # default; override via UniversalV2XEnv(max_steps=N) to match MAMLConfig
_SPEED_HIGH_KMPH  = 90.0    # threshold for speed penalty
_PRR_THRESHOLD    = 0.9     # minimum acceptable PRR


class UniversalV2XEnv:
    """
    Gym-like environment for Universal Policy training.

    observation: PyG Data  (if torch_geometric available)
                 or dict   (fallback for non-GPU environments)

    action: int in [0, MAX_CANDIDATES)
            — index into the sorted neighbor list (sorted by dist-to-dest)
    """

    def __init__(
        self,
        randomizer: DomainRandomizer,
        channel_lookup=None,
        seed: Optional[int] = None,
        reward_weights: Optional[dict] = None,
        max_steps: int = _MAX_STEPS,
        use_icm: bool = False,
    ) -> None:
        self._randomizer  = randomizer
        self._channel_lookup = channel_lookup
        self._rng         = random.Random(seed)
        self._aoi         = AoITracker()
        self._max_steps   = max_steps

        w = reward_weights or {}
        self._w_progress   = float(w.get("progress",   _W_PROGRESS))
        self._w_latency    = float(w.get("latency",    _W_LATENCY))
        self._w_handover   = float(w.get("handover",   _W_HANDOVER))
        self._w_aoi        = float(w.get("aoi",        _W_AOI))
        self._w_coverage   = float(w.get("coverage",   _W_COVERAGE))
        self._w_speed_pen  = float(w.get("speed_pen",  _W_SPEED_PEN))
        self._w_prr        = float(w.get("prr",        _W_PRR))
        self._w_sinr_bonus = float(w.get("sinr_bonus", _W_SINR_BONUS))
        self._w_goal       = float(w.get("goal",       _W_GOAL))
        # ICM replaces the heuristic cycle penalty — when ICM is active, set revisit weight=0
        self._w_revisit    = 0.0 if use_icm else float(w.get("revisit", _W_REVISIT))

        # episode state — set in reset()
        self._task:          Optional[V2XTask]       = None
        self._current_node:  Optional[str]           = None
        self._dest_node_id:  Optional[str]           = None
        self._prev_bs_id:    Optional[str]           = None
        self._step_count:    int                     = 0
        self._initial_dist:  float                   = 1.0
        self._total_latency: float                   = 0.0
        self._handover_count: int                    = 0
        self._visited_nodes: set                     = set()
        self._graph_cache:    Optional["GraphTaskCache"] = None
        self._last_task_hash: Optional[str]           = None  # BUG-6: cache reuse key

    # ── Gym API ────────────────────────────────────────────────────────────────
    def reset(
        self,
        task: Optional[V2XTask] = None,
        resample_od: bool = True,
    ) -> tuple[Any, dict]:
        """
        Reset the environment for a new episode.

        Parameters
        ----------
        task : V2XTask | None
            Provide a fixed task for MAML inner-loop episodes. None = sample fresh.
        resample_od : bool
            True  → sample a new O/D pair within the task graph each episode.
                    This is essential for real meta-learning: inner loop episodes
                    differ by O/D, not by graph topology.
            False → reuse the task's origin_id / dest_id (legacy behaviour).
        """
        if task is None:
            task = self._randomizer.sample_task()
        if task is None:
            raise RuntimeError("DomainRandomizer returned no valid task.")
        self._task = task

        # BUG-6 fix: only rebuild GraphTaskCache when graph + BS config actually changes.
        # key includes network_mode and BS positions — same region_id with different
        # BS placement or mode yields stale RF features without this.
        if _TORCH_GEO_AVAILABLE:
            task_hash = self._task_cache_key(task)
            if task_hash != self._last_task_hash:
                self._graph_cache = self._build_cache(task)
                self._last_task_hash = task_hash

        # D1 fix: resample O/D per episode within same graph, respecting curriculum.
        # task.max_od_dist_m carries the curriculum constraint from sample_task().
        if resample_od:
            od = self._sample_od_in_graph(task, max_dist_m=task.max_od_dist_m)
            self._current_node = od[0] if od else task.origin_id
            self._dest_node_id = od[1] if od else task.dest_id
        else:
            self._current_node = task.origin_id
            self._dest_node_id = task.dest_id

        self._prev_bs_id    = None
        self._step_count    = 0
        self._total_latency = 0.0
        self._handover_count = 0
        self._visited_nodes = set()
        self._aoi.reset()

        dest = task.graph["nodes"][self._dest_node_id]
        cur  = task.graph["nodes"][self._current_node]
        self._initial_dist = max(
            _haversine_m(cur["lat"], cur["lng"], dest["lat"], dest["lng"]),
            1.0,
        )

        obs = self._build_obs()
        return obs, {
            "region":       task.region_name,
            "network_mode": task.network_mode,
            "n_nodes":      len(task.graph["nodes"]),
            "n_bs":         len(task.bs_nodes),
            "n_rsu":        len(task.rsu_nodes),
            "origin":       self._current_node,
            "dest":         self._dest_node_id,
            "initial_dist": round(self._initial_dist, 1),
        }

    def step(self, action: int) -> tuple[Any, float, bool, bool, dict]:
        """
        Take action (neighbor index 0..MAX_CANDIDATES-1).

        Returns (obs, reward, terminated, truncated, info).
        """
        if self._task is None or self._current_node is None:
            raise RuntimeError("Call reset() before step().")

        # Tick sim clock first (BUG-1 fix: all AoI uses sim time)
        self._aoi.tick()

        neighbors = self._sorted_neighbors(self._current_node)
        if action < 0 or action >= len(neighbors):
            obs = self._build_obs()
            return obs, -2.0, False, True, {"invalid_action": True}

        next_node_id, edge_dist_m = neighbors[action]
        self._step_count += 1

        # ── Node positions ────────────────────────────────────────────────────
        nodes  = self._task.graph["nodes"]
        dest   = nodes[self._dest_node_id]
        prev   = nodes[self._current_node]
        nxt    = nodes[next_node_id]

        prev_dist = _haversine_m(prev["lat"], prev["lng"], dest["lat"], dest["lng"])
        next_dist = _haversine_m(nxt["lat"],  nxt["lng"],  dest["lat"], dest["lng"])
        progress_delta = (prev_dist - next_dist) / self._initial_dist

        # ── Latency + Coverage ────────────────────────────────────────────────
        mid_lat = (prev["lat"] + nxt["lat"]) / 2
        mid_lng = (prev["lng"] + nxt["lng"]) / 2
        bs, bs_dist, latency_ms, is_rsu = self._find_best_node(mid_lat, mid_lng)
        covered = bs is not None and bs_dist <= float((bs or {}).get("coverage_radius_m", 0))

        # ── SINR / PRR estimates ──────────────────────────────────────────────
        sinr_norm, prr = self._estimate_prr(bs, bs_dist, latency_ms)

        # ── Handover ──────────────────────────────────────────────────────────
        bs_id = (bs or {}).get("id")
        handover = (self._prev_bs_id is not None and bs_id != self._prev_bs_id)
        if handover:
            self._handover_count += 1
        self._prev_bs_id = bs_id

        # ── AoI (BUG-5 fix: use aoi_normalized, no internal 0.5 scale) ───────
        if covered:
            self._aoi.update(bs_id or "default")
        aoi_n = self._aoi.aoi_normalized(bs_id or "default")

        # ── Speed penalty ─────────────────────────────────────────────────────
        speed_kmh = self._task.vehicle_speed_kmh if self._task else 60.0
        speed_over = 1.0 if speed_kmh > _SPEED_HIGH_KMPH else 0.0

        # ── Cycle detection ───────────────────────────────────────────────────
        revisit = next_node_id in self._visited_nodes
        self._visited_nodes.add(next_node_id)

        # ── Move ──────────────────────────────────────────────────────────────
        self._current_node   = next_node_id
        self._total_latency += latency_ms

        # ── 10-Component Reward ───────────────────────────────────────────────
        reward = (
              self._w_progress   * progress_delta
            - self._w_latency    * (latency_ms / _LATENCY_NORM_MS)
            - self._w_handover   * float(handover)
            - self._w_aoi        * aoi_n
            - self._w_coverage   * (0.0 if covered else 1.0)
            - self._w_speed_pen  * speed_over
            + self._w_prr        * max(prr - _PRR_THRESHOLD, 0.0)   # pure bonus; no double-penalty
            + self._w_sinr_bonus * sinr_norm                  # good SINR bonus
            - self._w_revisit    * float(revisit)             # cycle penalty
        )

        # ── Termination ───────────────────────────────────────────────────────
        arrived = (next_node_id == self._dest_node_id) or (next_dist < 20.0)
        if arrived:
            reward += self._w_goal

        terminated = arrived
        truncated  = self._step_count >= self._max_steps

        obs = self._build_obs()
        global_ctx = self._compute_global_ctx()   # ICM uses this as the state representation
        info = {
            "node_id":         next_node_id,
            "latency_ms":      round(latency_ms, 2),
            "covered":         covered,
            "handover":        handover,
            "handover_count":  self._handover_count,
            "aoi_normalized":  round(aoi_n, 4),
            "prr":             round(prr, 4),
            "sinr_norm":       round(sinr_norm, 4),
            "progress":        round(1.0 - next_dist / self._initial_dist, 3),
            "arrived":         arrived,
            "step":            self._step_count,
            "avg_latency_ms":  round(self._total_latency / self._step_count, 2),
            "aoi_stats":       self._aoi.episode_stats(),
            "global_ctx":      global_ctx,   # [step_p, snr_n, lat_n, aoi_n, speed_n, d2d_n]
            "revisit":         revisit,
        }
        return obs, reward, terminated, truncated, info

    def her_relabel_rewards(
        self,
        visited_nodes: list[str],
        rewards: list[float],
        initial_dist: float,
        min_od_m: float = 200.0,
    ) -> tuple[list[float], Optional[str]]:
        """
        HER (Hindsight Experience Replay) reward relabeling.

        Treats the last visited node as the hindsight goal and recomputes
        the progress-based reward for each step.  Only the extrinsic
        (progress + goal) component is relabeled; ICM intrinsic rewards
        stored separately are left unchanged.

        Parameters
        ----------
        visited_nodes : ordered list of node IDs visited this episode
        rewards       : original per-step extrinsic rewards (list copy)
        initial_dist  : haversine distance from origin to hindsight goal
        min_od_m      : minimum O/D distance for HER to apply (avoids
                        trivially short relabeled tasks at Stage 0)

        Returns (relabeled_rewards, hindsight_goal_id) or (rewards, None)
        if the episode is too short or goal is too close.
        """
        if len(visited_nodes) < 2 or self._task is None:
            return rewards, None

        hindsight_goal_id = visited_nodes[-1]
        origin_id = visited_nodes[0]
        nodes = self._task.graph["nodes"]

        if hindsight_goal_id not in nodes or origin_id not in nodes:
            return rewards, None

        o = nodes[origin_id]
        h = nodes[hindsight_goal_id]
        h_dist = _haversine_m(o["lat"], o["lng"], h["lat"], h["lng"])
        if h_dist < min_od_m:
            return rewards, None

        relabeled = list(rewards)
        for step_i, (prev_id, next_id) in enumerate(
            zip(visited_nodes[:-1], visited_nodes[1:])
        ):
            if prev_id not in nodes or next_id not in nodes:
                continue
            prev_n = nodes[prev_id]
            next_n = nodes[next_id]
            prev_d = _haversine_m(prev_n["lat"], prev_n["lng"], h["lat"], h["lng"])
            next_d = _haversine_m(next_n["lat"], next_n["lng"], h["lat"], h["lng"])
            her_progress = (prev_d - next_d) / max(h_dist, 1.0)
            # Add the HER progress signal on top of the original reward.
            # We cannot subtract the original progress component because ep.rewards
            # is a composite of 10+ terms (including ICM) and the original progress
            # delta is not stored separately. Additive HER is slightly optimistic but
            # mathematically correct and preserves the ICM component intact.
            relabeled[step_i] += self._w_progress * her_progress

        # Hindsight terminal reward — last step arrived at hindsight goal
        relabeled[-1] += self._w_goal
        return relabeled, hindsight_goal_id

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

        global_ctx = self._compute_global_ctx()

        # Fast path: pre-built cache
        if self._graph_cache is not None and self._current_node is not None:
            try:
                return self._graph_cache.build_obs(
                    self._current_node,
                    dest_node_id=self._dest_node_id,
                    initial_dist=self._initial_dist,
                    global_ctx=global_ctx,
                )
            except Exception:
                self._graph_cache = None

        # Slow fallback
        try:
            return build_graph_obs(
                graph=self._task.graph,
                bs_nodes=self._task.bs_nodes + self._task.rsu_nodes,
                rsu_nodes=self._task.rsu_nodes,
                current_node_id=self._current_node,
                dest_node_id=self._dest_node_id,
                channel_lookup=self._channel_lookup,
                network_mode=self._task.network_mode,
                initial_dist=self._initial_dist,
                global_ctx=global_ctx,
            )
        except Exception:
            return self._build_dict_obs()

    def _compute_global_ctx(self) -> list:
        """6-dim per-step context for the Critic."""
        if self._task is None or self._current_node is None:
            return [0.0] * 6

        nodes = self._task.graph["nodes"]
        cur   = nodes.get(self._current_node, {})
        dest  = nodes.get(self._dest_node_id, {})
        dist_now = _haversine_m(
            cur.get("lat", 0), cur.get("lng", 0),
            dest.get("lat", 0), dest.get("lng", 0),
        )

        # SNR at current position from nearest BS
        pure_bs = [b for b in self._task.bs_nodes
                   if str(b.get("type", "")).lower() != "roadside_unit"]
        if not pure_bs:
            pure_bs = self._task.bs_nodes
        params = PATH_LOSS_PARAMS.get(self._task.network_mode, PATH_LOSS_PARAMS["5G"])

        cur_snr_n, cur_lat_n = 0.0, 1.0
        if pure_bs:
            best_d  = float("inf")
            best_bs = None
            for b in pure_bs:
                d = _haversine_m(cur.get("lat", 0), cur.get("lng", 0), b["lat"], b["lng"])
                if d < best_d:
                    best_d  = d
                    best_bs = b
            snr_db    = _compute_snr_db(best_d, params)
            cur_snr_n = float(min(max(snr_db / _SNR_REF_DB, 0.0), 1.0))
            bs_elat   = float(best_bs.get("edge_latency_ms", 3.0)) if best_bs else 3.0
            bs_load   = float(best_bs.get("load", 0.0))             if best_bs else 0.0
            # M/M/1 queuing delay: must match _find_best_node() and GraphTaskCache paths
            rho       = min(bs_load, 0.95)
            q_ms      = (rho / max(1.0 - rho, 0.05)) * 0.5
            cur_lat_n = (4.0 + (1.0 - cur_snr_n) * 46.0 + bs_elat + q_ms) / 50.0

        aoi_n  = self._aoi.aoi_normalized(self._prev_bs_id or "default")
        speed_n = min(self._task.vehicle_speed_kmh / 120.0, 1.0)
        d2d_n   = min(dist_now / self._initial_dist, 4.0)
        step_p  = self._step_count / _MAX_STEPS

        return [step_p, cur_snr_n, cur_lat_n, aoi_n, speed_n, d2d_n]

    def _build_dict_obs(self) -> dict:
        if self._task is None or self._current_node is None:
            return {}
        nd   = self._task.graph["nodes"].get(self._current_node, {})
        dest = self._task.graph["nodes"].get(self._dest_node_id, {})
        dist = _haversine_m(nd.get("lat", 0), nd.get("lng", 0),
                            dest.get("lat", 0), dest.get("lng", 0))
        return {"lat": nd.get("lat", 0), "lng": nd.get("lng", 0),
                "dist_to_dest_m": dist, "step": self._step_count}

    # ── Network helpers ────────────────────────────────────────────────────────
    def _sorted_neighbors(self, node_id: str) -> list[tuple[str, float]]:
        """
        G11 fix: sort neighbors by dist(neighbor → dest), not edge distance.
        Consistent with GraphTaskCache.build_obs() neighbor ordering.
        """
        if self._task is None or self._dest_node_id is None:
            return []
        adj      = self._task.graph["adjacency"]
        nodes    = self._task.graph["nodes"]
        dest     = nodes.get(self._dest_node_id, {})
        dest_lat = dest.get("lat", 0.0)
        dest_lng = dest.get("lng", 0.0)
        raw      = adj.get(node_id, [])

        def _d2d(entry):
            nb_id, _ = entry
            nd = nodes.get(nb_id, {})
            return _haversine_m(nd.get("lat", 0), nd.get("lng", 0), dest_lat, dest_lng)

        return sorted(raw, key=_d2d)[:MAX_CANDIDATES]

    def _find_best_node(
        self,
        mid_lat: float,
        mid_lng: float,
    ) -> tuple[Optional[dict], float, float, bool]:
        """Find the best BS or RSU for the given midpoint."""
        if self._task is None:
            return None, float("inf"), 20.0, False

        all_nodes = self._task.bs_nodes + self._task.rsu_nodes
        if not all_nodes:
            return None, float("inf"), 20.0, False

        best_node   = None
        best_score  = float("inf")
        best_dist   = float("inf")
        best_lat    = 20.0
        best_is_rsu = False
        net         = self._task.network_mode

        for nd in all_nodes:
            d   = _haversine_m(mid_lat, mid_lng, nd["lat"], nd["lng"])
            cov = float(nd.get("coverage_radius_m", 400.0))
            if d > cov * 1.5:
                continue
            is_rsu   = str(nd.get("type", "")).lower() in ("rsu", "roadside_unit")
            edge_lat = float(nd.get("edge_latency_ms") or (0.5 if is_rsu else 3.0))

            if _BA_AVAILABLE:
                n_veh = (1 + int(nd.get("n_background_vehicles") or 0)
                           + int(nd.get("n_other_devices") or 0)
                           + int(nd.get("n_its_load") or 0))
                if is_rsu:
                    lat_ms = _ba_L_rsu(d, cov) + edge_lat
                else:
                    l_air, _, _, _ = _ba_L_total(d, 0.0, n_veh, net)
                    lat_ms = l_air + edge_lat
            else:
                # M/M/1 queuing delay: E[W] = ρ/(1−ρ) × mean_service (0.5ms for 5G NR slot)
                # Clip ρ to 0.95 to avoid singularity; matches GNN feature bs_load semantics
                rho = min(float(nd.get("load", 0.0)), 0.95)
                q_delay_ms = (rho / max(1.0 - rho, 0.05)) * 0.5
                lat_ms = 4.0 + (d / max(cov, 1.0)) * 15.0 + edge_lat + q_delay_ms

            score = lat_ms + (100.0 if d > cov else 0.0)
            if score < best_score:
                best_score = score
                best_node  = nd
                best_dist  = d
                best_lat   = lat_ms
                best_is_rsu = is_rsu

        return best_node, best_dist, best_lat, best_is_rsu

    def _estimate_prr(
        self,
        bs: Optional[dict],
        bs_dist: float,
        latency_ms: float,
    ) -> tuple[float, float]:
        """
        Estimate (sinr_norm, prr) at current edge midpoint.

        PRR ≈ 1 − TWDR. When out of coverage, PRR = 0.
        SINR ≈ SNR (ignoring interference for single-BS tasks).
        """
        if bs is None or not self._task:
            return 0.0, 0.0

        params = PATH_LOSS_PARAMS.get(self._task.network_mode, PATH_LOSS_PARAMS["5G"])
        snr_db = _compute_snr_db(bs_dist, params)

        cov = float(bs.get("coverage_radius_m", 400.0))
        if bs_dist > cov:
            return 0.0, 0.0

        sinr_n = float(max(snr_db / _SNR_REF_DB, 0.0))
        cov_p  = _coverage_prob(snr_db, params["sigma"])
        prr    = float(cov_p)           # P(packet received) ≈ P(SNR > SNR_min)

        return min(sinr_n, 1.0), min(prr, 1.0)

    def _build_cache(self, task: V2XTask) -> Optional["GraphTaskCache"]:
        try:
            return GraphTaskCache(
                graph=task.graph,
                bs_nodes=task.bs_nodes + task.rsu_nodes,
                rsu_nodes=task.rsu_nodes,
                dest_node_id=task.dest_id,
                channel_lookup=self._channel_lookup,
                network_mode=task.network_mode,
            )
        except Exception:
            return None

    @staticmethod
    def _task_cache_key(task: V2XTask) -> str:
        """Stable hash for GraphTaskCache identity — includes BS positions and network mode."""
        bs_sig = tuple(sorted(
            (b.get("id", ""), round(b.get("lat", 0.0), 6), round(b.get("lng", 0.0), 6))
            for b in task.bs_nodes
        ))
        return f"{task.region_id}_{task.network_mode}_{hash(bs_sig)}"

    def _sample_od_in_graph(
        self,
        task: V2XTask,
        min_hops: int = 4,
        max_dist_m: float = float("inf"),
        max_tries: int = 30,
    ) -> Optional[tuple[str, str]]:
        """
        Sample a fresh O/D pair (BFS-reachable, distance-bounded) within the task's graph.

        max_dist_m enforces the curriculum constraint (task.max_od_dist_m) so that
        stage-0 episodes stay ≤500 m apart even after O/D resampling.
        """
        nodes      = list(task.graph["nodes"].keys())
        adj        = task.graph["adjacency"]
        graph_nodes = task.graph["nodes"]
        for _ in range(max_tries):
            origin = self._rng.choice(nodes)
            dest   = self._rng.choice(nodes)
            if origin == dest:
                continue
            if max_dist_m < float("inf"):
                on = graph_nodes[origin]
                dn = graph_nodes[dest]
                if _haversine_m(on["lat"], on["lng"], dn["lat"], dn["lng"]) > max_dist_m:
                    continue
            if _bfs_reachable(adj, origin, dest, min_hops):
                return origin, dest
        return None

    # ── Properties ────────────────────────────────────────────────────────────
    @property
    def current_task(self) -> Optional[V2XTask]:
        return self._task

    @property
    def n_actions(self) -> int:
        return MAX_CANDIDATES
