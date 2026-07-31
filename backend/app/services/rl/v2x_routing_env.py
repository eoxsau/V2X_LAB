"""
V2X Network-Aware Routing Environment — Reinforcement Learning.

Gym-compatible interface (no gym/gymnasium dependency required).
Can be wrapped for DQN, PPO, or any discrete-action agent.

Fixes applied vs. v1
--------------------
1. [state]   Removed `future_risk_penalty` dimension (perfectly collinear with
             `future_connectivity_score`; sum = 1.0 caused gradient cancellation).
2. [state]   Removed `resource_deficit` from global features: it is a global
             network metric the agent cannot influence → pure reward noise.
3. [state]   Per-edge padding uses a validity flag (1.0 real / 0.0 padding) as the
             first element so the agent can distinguish "no edge" from a perfect edge.
4. [reward]  `w_goal_progress` reduced 5.0 → 1.0 so network penalties are not
             overwhelmed; balanced with potential-based shaping.
5. [reward]  `w_handover` reduced -2.0 → -0.5; previous value made the payback
             period ~4 steps, causing the agent to permanently avoid handovers.
6. [reward]  `r_resource_deficit` term removed (same reason as state fix #2).
7. [perf]    Edge costs for all candidates are pre-computed in `_build_state()` and
             cached in `self._cached_edge_costs`; `get_state()` and `step()` read
             the cache instead of re-calling `_edge_cost()` (was 6 calls/step).

State vector layout (STATE_DIM = 37):
  [0]   lat_norm               current lat normalised w.r.t. origin–dest span
  [1]   lng_norm
  [2]   dist_to_dest_norm      haversine distance / DIST_NORM_KM km
  [3]   progress               1 − dist / initial_dist  (0 → 1)
  [4]   current_bs_load        nearest BS load ratio  (0–1)
  [5]   avg_bs_load            mean load across all BS
  [6]   min_bs_load            minimum load among all BS
  [7]   latency_norm           predicted latency / LATENCY_NORM ms
  [8]   handover_flag          1 if handover occurred on last action, else 0
  [9]   blockage_score         distance-based obstruction proxy  (0–1)
  [10]  future_connectivity    fraction of forward edges within BS coverage  (0–1)
  [11]  steps_norm             current_step / max_steps
  [12:37] per-candidate edge features (MAX_CANDIDATES=5 × 5):
          [valid, dist_norm, latency_norm, bs_load_norm, blockage_norm]
          valid=1.0 for a real edge, 0.0 for padding (no edge available).

Action space:
  Discrete(MAX_CANDIDATES) — index into candidate outgoing edges.
  Invalid index (≥ len(candidate_edges)) stays in place + disconnection penalty.

gym wrapper example (stable-baselines3)::

    import gymnasium as gym
    import numpy as np

    class V2XGymEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self, graph, road_nodes, bs_nodes, origin_id, dest_id, **kw):
            super().__init__()
            self._env = V2XRoutingEnv(graph, road_nodes, bs_nodes,
                                      origin_id, dest_id, **kw)
            self.observation_space = gym.spaces.Box(
                low=0.0, high=1.0, shape=(STATE_DIM,), dtype=np.float32
            )
            self.action_space = gym.spaces.Discrete(MAX_CANDIDATES)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return np.array(self._env.reset(), dtype=np.float32), {}

        def step(self, action):
            r = self._env.step(int(action))
            obs = np.array(r.state_vector, dtype=np.float32)
            return obs, r.reward, r.done, False, r.info

        def action_masks(self) -> list[bool]:
            mask = [False] * MAX_CANDIDATES
            for i in self._env.valid_actions():
                mask[i] = True
            return mask
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.geo.spatial_grid import SpatialGrid

try:
    from app.services.buildings.building_obstruction_analyzer import (
        _L_total as _ba_L_total,
        _L_rsu   as _ba_L_rsu,
    )
    _BA_LATENCY_AVAILABLE = True
except ImportError:
    _BA_LATENCY_AVAILABLE = False

try:
    import app.services.routing.route_cost_function as _rcf
except ImportError:
    _rcf = None  # type: ignore


# ── Constants ──────────────────────────────────────────────────────────────────
MAX_CANDIDATES: int = 5       # maximum outgoing edges per step
GLOBAL_DIM: int = 12          # global feature count (reduced from 14)
EDGE_FEAT_DIM: int = 5        # features per candidate edge (added valid flag)
STATE_DIM: int = GLOBAL_DIM + MAX_CANDIDATES * EDGE_FEAT_DIM  # 37

LATENCY_NORM: float = 50.0    # ms
DIST_NORM_KM: float = 5.0     # km


# ── Data structures ────────────────────────────────────────────────────────────
@dataclass
class EdgeInfo:
    """One directed road edge — a candidate action."""
    edge_id: str
    from_node_id: str
    to_node_id: str
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
    distance_m: float
    speed_mps: float

    @property
    def midpoint_lat(self) -> float:
        return (self.from_lat + self.to_lat) / 2.0

    @property
    def midpoint_lng(self) -> float:
        return (self.from_lng + self.to_lng) / 2.0

    @property
    def travel_time_s(self) -> float:
        return self.distance_m / max(self.speed_mps, 0.1)


@dataclass
class EnvState:
    """
    Structured environment state.
    Use get_state() for the flat agent-facing vector.
    """
    step: int
    current_node_id: str
    current_lat: float
    current_lng: float
    dest_node_id: str
    dest_lat: float
    dest_lng: float
    dist_to_dest_m: float
    initial_dist_m: float
    progress: float
    current_bs_id: Optional[str]
    current_bs_load: float
    avg_bs_load: float
    min_bs_load: float
    predicted_latency_ms: float
    handover_occurred: bool
    blockage_score: float
    future_connectivity_score: float    # fraction of forward edges in BS coverage
    candidate_edges: list[EdgeInfo] = field(default_factory=list)
    # Note: future_risk_penalty removed (= 1 - future_connectivity_score, collinear)
    # Note: resource_deficit removed (global metric, not agent-controllable)


@dataclass
class StepResult:
    """Return value of step()."""
    state_vector: list[float]           # flat STATE_DIM vector
    reward: float
    done: bool
    info: dict


# ── Reward weights ─────────────────────────────────────────────────────────────
@dataclass
class RewardWeights:
    """
    Tune each reward component independently.

    Key changes from v1
    -------------------
    w_goal_progress  5.0 → 1.0  — prevents progress from drowning network signals
    w_handover      -2.0 → -0.5 — 4-step payback was too long; agent never learned
                                   to benefit from handover (V2X anti-pattern)
    w_arrival       100.0 → 20.0 — proportional reduction after w_goal_progress cut
    w_timeout       -20.0 → -10.0 — same ratio
    w_resource_deficit removed    — global metric, no credit assignment possible

    Tuning guide:
      Prioritise low latency    → increase |w_latency|
      Penalise weak signal      → increase |w_blockage| and |w_disconnection|
      Allow beneficial handover → keep |w_handover| ≤ per-step network gain
      Shape for sparse reward   → increase w_arrival relative to per-step rewards
    """
    w_goal_progress: float = 1.0     # per 100 m toward destination
    w_distance: float = -0.5         # per 100 m of edge length
    w_latency: float = -1.0          # per normalised latency unit (latency/LATENCY_NORM)
    w_bs_load: float = -0.5          # per BS load ratio unit
    w_handover: float = -0.5         # flat penalty per handover event
    w_blockage: float = -0.5         # per normalised blockage unit
    w_disconnection: float = -3.0    # flat penalty when outside all BS coverage
    w_future_risk: float = -1.0      # per future risk unit (1 − future_connectivity)
    w_arrival: float = 20.0          # flat reward on reaching destination
    w_timeout: float = -10.0         # flat penalty when max_steps exceeded


DEFAULT_REWARD_WEIGHTS = RewardWeights()


# ── Geometry ───────────────────────────────────────────────────────────────────
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# ── Main environment ───────────────────────────────────────────────────────────
class V2XRoutingEnv:
    """
    V2X network-aware routing environment with gym-compatible interface.

    Parameters
    ----------
    graph : dict
        Road graph with keys:
          "nodes"     → {node_id: {"lat": float, "lng": float}}
          "adjacency" → {node_id: [(neighbor_id, dist_m), ...]}
        Matches the format produced by main.py load_mock_graph().

    road_nodes : dict
        Pass graph["nodes"] directly when constructing from the mock graph.

    bs_nodes : list[dict]
        Base station list.  Each entry needs:
          id, name, lat, lng, capacity, load (or current_load), coverage_radius_m.

    origin_id, dest_id : str
        Start and goal node IDs (keys in road_nodes).

    buildings_gdf : optional GeoDataFrame
        When None, the fast no-building path is used everywhere.

    max_steps : int
        Episode terminates after this many steps if destination not reached.

    weights, norm_scales
        Forwarded to compute_edge_network_cost. Defaults match route_cost_function.

    reward_weights : RewardWeights
        Controls the magnitude of each reward component.
    """

    state_dim: int = STATE_DIM
    action_dim: int = MAX_CANDIDATES

    def __init__(
        self,
        graph: dict,
        road_nodes: dict,
        bs_nodes: list[dict],
        origin_id: str,
        dest_id: str,
        buildings_gdf: Any = None,
        max_steps: int = 200,
        weights: Any = None,
        norm_scales: Any = None,
        reward_weights: RewardWeights = DEFAULT_REWARD_WEIGHTS,
        allocation_output: Optional[dict] = None,
    ) -> None:
        self._graph = graph
        self._road_nodes = road_nodes
        self._bs_nodes = bs_nodes
        # 공간 인덱스 — BS는 에피소드 동안 고정이므로 그리드를 1회만 만들어 매 스텝
        # 최근접/커버리지 조회를 O(N)→O(1)로. 대규모 배치(수백~수천 노드)에서 학습·평가가
        # 노드 수에 선형으로 느려지던 병목을 제거한다. (셀 = 전형적 커버 반경 ~400m)
        self._bs_grid = SpatialGrid(
            bs_nodes,
            coords_fn=lambda bs: (float(bs.get("lat", 0.0)), float(bs.get("lng", 0.0))),
            cell_size_m=400.0,
        ) if bs_nodes else None
        self._bs_max_cov_m = max(
            (float(bs.get("coverage_radius_m", 400.0)) for bs in bs_nodes), default=400.0
        )
        # avg/min BS 부하는 에피소드 동안 불변(환경이 부하를 변경하지 않음) → 1회 선계산해
        # 매 스텝 전체 BS 순회 재계산을 제거한다.
        _loads = [
            float(bs.get("load", bs.get("current_load", 0.0)))
            / max(float(bs.get("capacity", 100.0)), 1.0)
            for bs in bs_nodes
        ]
        self._avg_bs_load = sum(_loads) / len(_loads) if _loads else 0.0
        self._min_bs_load = min(_loads) if _loads else 0.0
        self._origin_id = origin_id
        self._dest_id = dest_id
        self._buildings_gdf = buildings_gdf
        self._max_steps = max_steps
        self._rw = reward_weights

        try:
            from app.services.routing.route_cost_function import (
                compute_edge_network_cost,
                DEFAULT_WEIGHTS, DEFAULT_NORM_SCALES,
            )
            self._compute_edge = compute_edge_network_cost
            self._weights = weights or DEFAULT_WEIGHTS
            self._norm_scales = norm_scales or DEFAULT_NORM_SCALES
        except ImportError:
            self._compute_edge = None
            self._weights = weights
            self._norm_scales = norm_scales

        dest = road_nodes.get(dest_id, {})
        self._dest_lat = float(dest.get("lat", 0.0))
        self._dest_lng = float(dest.get("lng", 0.0))

        origin = road_nodes.get(origin_id, {})
        self._initial_dist_m = _haversine_m(
            float(origin.get("lat", 0.0)),
            float(origin.get("lng", 0.0)),
            self._dest_lat,
            self._dest_lng,
        )

        # Resource allocation output dict for enriching step info
        self._allocation_output: Optional[dict] = allocation_output

        self._env_state: Optional[EnvState] = None
        self._prev_bs_id: Optional[str] = None
        # Pre-computed edge costs for all current candidates (fix #7: cache)
        self._cached_edge_costs: list[dict] = []

    # ── Public interface ───────────────────────────────────────────────────────

    def reset(self) -> list[float]:
        """Reset to origin. Returns flat STATE_DIM state vector."""
        self._prev_bs_id = None
        self._cached_edge_costs = []
        self._env_state = self._build_state(
            node_id=self._origin_id,
            step=0,
            handover=False,
        )
        return self.get_state()

    def step(self, action: int) -> StepResult:
        """
        Move along the selected candidate edge.

        Invalid action (out of range): stays in place, applies disconnection penalty.
        """
        assert self._env_state is not None, "call reset() before step()"

        edges = self._env_state.candidate_edges
        current_step = self._env_state.step + 1
        prev_dist = self._env_state.dist_to_dest_m

        if action < 0 or action >= len(edges):
            self._env_state = self._build_state(
                node_id=self._env_state.current_node_id,
                step=current_step,
                handover=False,
            )
            return StepResult(
                state_vector=self.get_state(),
                reward=self._rw.w_disconnection,
                done=current_step >= self._max_steps,
                info={"reason": "invalid_action", "step": current_step},
            )

        chosen = edges[action]
        next_node_id = chosen.to_node_id

        # Use pre-computed edge cost (fix #7: no extra _edge_cost() call here)
        edge_cost = (
            self._cached_edge_costs[action]
            if action < len(self._cached_edge_costs)
            else self._edge_cost(chosen, self._prev_bs_id)
        )

        handover = bool(
            self._prev_bs_id
            and edge_cost.get("best_node_id")
            and self._prev_bs_id != edge_cost["best_node_id"]
        )
        self._prev_bs_id = edge_cost.get("best_node_id")

        self._env_state = self._build_state(
            node_id=next_node_id,
            step=current_step,
            handover=handover,
        )

        arrived = next_node_id == self._dest_id
        timed_out = (not arrived) and current_step >= self._max_steps
        done = self.is_done()

        reward = self.calculate_reward(
            prev_dist_m=prev_dist,
            edge_cost=edge_cost,
            arrived=arrived,
            timed_out=timed_out,
        )

        # Allocation info for the chosen BS (from pre-run allocation result)
        _alloc_bs_id = edge_cost.get("best_node_id") or ""
        _alloc_deficit = 0.0
        _alloc_latency_impact = 0.0
        if self._allocation_output and _alloc_bs_id:
            _alloc_deficit = float(
                self._allocation_output.get("resource_deficit_by_bs", {})
                .get(_alloc_bs_id, 0.0)
            )
            _alloc_latency_impact = float(
                self._allocation_output.get("expected_latency_impact", {})
                .get(_alloc_bs_id, 0.0)
            )

        return StepResult(
            state_vector=self.get_state(),
            reward=reward,
            done=done,
            info={
                "step": current_step,
                "node_id": next_node_id,
                "edge_id": chosen.edge_id,
                "handover": handover,
                "arrived": arrived,
                "timed_out": timed_out,
                "dist_to_dest_m": self._env_state.dist_to_dest_m,
                "latency_ms": edge_cost.get("latency_ms", 0.0),
                "bs_id": _alloc_bs_id,
                "bs_name": edge_cost.get("best_node_name"),
                "within_coverage": edge_cost.get("within_coverage", False),
                "edge_total_cost": edge_cost.get("total_cost", 0.0),
                # Resource allocation fields (0.0 when no allocation result available)
                "allocation_deficit_rb": _alloc_deficit,
                "allocation_latency_impact_ms": _alloc_latency_impact,
                "allocation_algorithm": (
                    self._allocation_output.get("algorithm_id", "")
                    if self._allocation_output else ""
                ),
            },
        )

    def get_state(self) -> list[float]:
        """
        Return current state as a flat list of STATE_DIM (37) floats.

        All values normalised to approximately [0, 1].
        Convert to numpy with: np.array(env.get_state(), dtype=np.float32)

        Layout
        ------
        [0:12]   Global features (see module docstring for full list)
        [12:37]  Per-candidate edge features:
                 5 candidates × [valid, dist_norm, latency_norm, load_norm, blockage_norm]
                 valid=0.0 means padding (no real edge at that index).
        """
        if self._env_state is None:
            return [0.0] * STATE_DIM

        s = self._env_state
        lat_span = abs(s.current_lat - self._dest_lat) + 1e-9
        lng_span = abs(s.current_lng - self._dest_lng) + 1e-9
        lat_min = min(s.current_lat, self._dest_lat)
        lng_min = min(s.current_lng, self._dest_lng)

        global_feat = [
            _clip01((s.current_lat - lat_min) / lat_span),         # [0]  lat_norm
            _clip01((s.current_lng - lng_min) / lng_span),         # [1]  lng_norm
            _clip01(s.dist_to_dest_m / (DIST_NORM_KM * 1000.0)),   # [2]  dist_norm
            _clip01(s.progress),                                     # [3]  progress
            _clip01(s.current_bs_load),                             # [4]  current_bs_load
            _clip01(s.avg_bs_load),                                 # [5]  avg_bs_load
            _clip01(s.min_bs_load),                                 # [6]  min_bs_load
            _clip01(s.predicted_latency_ms / LATENCY_NORM),         # [7]  latency_norm
            1.0 if s.handover_occurred else 0.0,                    # [8]  handover_flag
            _clip01(s.blockage_score),                              # [9]  blockage_score
            _clip01(s.future_connectivity_score),                   # [10] future_connectivity
            _clip01(s.step / max(self._max_steps, 1)),              # [11] steps_norm
        ]

        # Per-edge features — read from cache (fix #7)
        norm_loss = self._norm_scales.loss_db if self._norm_scales else 30.0
        edge_feat: list[float] = []
        for i in range(MAX_CANDIDATES):
            if i < len(s.candidate_edges) and i < len(self._cached_edge_costs):
                e = s.candidate_edges[i]
                ec = self._cached_edge_costs[i]
                edge_feat.extend([
                    1.0,                                                     # valid
                    _clip01(e.distance_m / (DIST_NORM_KM * 1000.0)),        # dist_norm
                    _clip01(ec.get("latency_ms", 0.0) / LATENCY_NORM),      # latency_norm
                    _clip01(ec.get("load_ratio", 0.0)),                      # bs_load_norm
                    _clip01(ec.get("loss_db", 0.0) / norm_loss),             # blockage_norm
                ])
            else:
                # Padding: valid=0 distinguishes from a real edge (fix #3)
                edge_feat.extend([0.0, 0.0, 0.0, 0.0, 0.0])

        return global_feat + edge_feat

    def calculate_reward(
        self,
        prev_dist_m: float,
        edge_cost: dict,
        arrived: bool,
        timed_out: bool,
    ) -> float:
        """
        Reward = Goal_Progress
               − Distance_Penalty
               − Latency_Penalty
               − BS_Load_Penalty
               − Handover_Penalty
               − Blockage_Penalty
               − Disconnection_Penalty
               − Future_Risk_Penalty
               + Destination_Arrival_Reward

        Note: Resource_Deficit_Penalty removed (fix #6) — the agent
        cannot influence global network load, making it pure reward noise
        that impedes credit assignment.
        """
        rw = self._rw
        s = self._env_state

        cur_dist = s.dist_to_dest_m if s else 0.0
        norm_loss = self._norm_scales.loss_db if self._norm_scales else 30.0
        norm_lat = self._norm_scales.latency_ms if self._norm_scales else 20.0

        delta_m = prev_dist_m - cur_dist

        r_progress = rw.w_goal_progress * (delta_m / 100.0)
        r_distance = rw.w_distance * (edge_cost.get("distance_m", 0.0) / 100.0)
        r_latency = rw.w_latency * (edge_cost.get("latency_ms", 0.0) / norm_lat)
        r_load = rw.w_bs_load * edge_cost.get("load_ratio", 0.0)
        r_handover = rw.w_handover if edge_cost.get("handover_occurred", False) else 0.0
        r_blockage = rw.w_blockage * (edge_cost.get("loss_db", 0.0) / norm_loss)
        r_disconnection = (
            rw.w_disconnection if not edge_cost.get("within_coverage", True) else 0.0
        )
        # Future risk: use future_connectivity_score directly (fix #1 avoids storing
        # the redundant `future_risk_penalty` field in EnvState)
        future_risk = 1.0 - (s.future_connectivity_score if s else 1.0)
        r_future = rw.w_future_risk * future_risk

        r_arrival = rw.w_arrival if arrived else 0.0
        r_timeout = rw.w_timeout if timed_out else 0.0

        total = (
            r_progress + r_distance + r_latency + r_load
            + r_handover + r_blockage + r_disconnection + r_future
            + r_arrival + r_timeout
        )
        return round(total, 4)

    def is_done(self) -> bool:
        """
        Terminal conditions:
          * Arrived at destination.
          * step >= max_steps.
          * Dead end (no outgoing edges).
        """
        if self._env_state is None:
            return False
        if self._env_state.current_node_id == self._dest_id:
            return True
        if self._env_state.step >= self._max_steps:
            return True
        if not self._env_state.candidate_edges:
            return True
        return False

    def valid_actions(self) -> list[int]:
        """
        Indices of valid actions for the current state.
        Use for action masking in PPO / MaskablePPO.
        """
        if self._env_state is None:
            return []
        return list(range(len(self._env_state.candidate_edges)))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_state(self, node_id: str, step: int, handover: bool) -> EnvState:
        """
        Construct EnvState and pre-compute all candidate edge costs (fix #7).
        """
        node = self._road_nodes.get(node_id, {})
        cur_lat = float(node.get("lat", 0.0))
        cur_lng = float(node.get("lng", 0.0))
        dist_m = _haversine_m(cur_lat, cur_lng, self._dest_lat, self._dest_lng)
        progress = _clip01(1.0 - dist_m / max(self._initial_dist_m, 1.0))

        # 정적 선계산값(부하는 에피소드 동안 불변)
        avg_load = self._avg_bs_load
        min_load = self._min_bs_load

        best_bs = self._nearest_bs(cur_lat, cur_lng)
        cur_bs_id = str(best_bs["id"]) if best_bs else None
        cur_bs_load = (
            float(best_bs.get("load", best_bs.get("current_load", 0.0)))
            / max(float(best_bs.get("capacity", 100.0)), 1.0)
            if best_bs else 0.0
        )
        pred_lat = self._estimate_latency(cur_lat, cur_lng, best_bs)
        blockage = self._blockage_score(cur_lat, cur_lng, best_bs)

        candidates = self._get_candidates(node_id)

        # Pre-compute edge costs for all candidates in one pass (fix #7)
        self._cached_edge_costs = [
            self._edge_cost(e, prev_best_node_id=self._prev_bs_id)
            for e in candidates
        ]

        future_conn = self._future_connectivity(candidates)

        return EnvState(
            step=step,
            current_node_id=node_id,
            current_lat=cur_lat,
            current_lng=cur_lng,
            dest_node_id=self._dest_id,
            dest_lat=self._dest_lat,
            dest_lng=self._dest_lng,
            dist_to_dest_m=dist_m,
            initial_dist_m=self._initial_dist_m,
            progress=progress,
            current_bs_id=cur_bs_id,
            current_bs_load=_clip01(cur_bs_load),
            avg_bs_load=_clip01(avg_load),
            min_bs_load=_clip01(min_load),
            predicted_latency_ms=pred_lat,
            handover_occurred=handover,
            blockage_score=blockage,
            future_connectivity_score=future_conn,
            candidate_edges=candidates,
        )

    def _get_candidates(self, node_id: str) -> list[EdgeInfo]:
        adjacency = (
            self._graph.get("adjacency", self._graph)
            if isinstance(self._graph, dict) else {}
        )
        cur_node = self._road_nodes.get(node_id, {})
        result: list[EdgeInfo] = []
        for entry in adjacency.get(node_id, []):
            if len(entry) >= 4:
                to_id, edge_id, dist_m, speed_mps = (
                    str(entry[0]), str(entry[1]), float(entry[2]), float(entry[3])
                )
            elif len(entry) >= 2:
                to_id, dist_m = str(entry[0]), float(entry[1])
                edge_id = f"{node_id}_{to_id}"
                speed_mps = 13.9
            else:
                continue
            to_node = self._road_nodes.get(to_id, {})
            result.append(EdgeInfo(
                edge_id=edge_id,
                from_node_id=node_id,
                to_node_id=to_id,
                from_lat=float(cur_node.get("lat", 0.0)),
                from_lng=float(cur_node.get("lng", 0.0)),
                to_lat=float(to_node.get("lat", 0.0)),
                to_lng=float(to_node.get("lng", 0.0)),
                distance_m=dist_m,
                speed_mps=speed_mps,
            ))
        result.sort(key=lambda e: e.distance_m)
        return result[:MAX_CANDIDATES]

    def _edge_cost(self, edge: EdgeInfo, prev_best_node_id: Optional[str] = None) -> dict:
        """
        Compute network cost for one edge. Returns a plain dict so this module
        never hard-fails when route_cost_function is absent.
        """
        if self._compute_edge is not None:
            try:
                r = self._compute_edge(
                    edge_id=edge.edge_id,
                    midpoint_lat=edge.midpoint_lat,
                    midpoint_lng=edge.midpoint_lng,
                    distance_m=edge.distance_m,
                    travel_time_s=edge.travel_time_s,
                    nodes=self._bs_nodes,
                    buildings_gdf=self._buildings_gdf,
                    prev_best_node_id=prev_best_node_id,
                    weights=self._weights,
                    norm_scales=self._norm_scales,
                    skip_buildings=(self._buildings_gdf is None),
                )
                return {
                    "best_node_id":      r.best_node_id,
                    "best_node_name":    r.best_node_name,
                    "latency_ms":        r.latency_ms,
                    "load_ratio":        r.load_ratio,
                    "handover_occurred": r.handover_occurred,
                    "loss_db":           r.loss_db,
                    "within_coverage":   r.within_coverage,
                    "total_cost":        r.total_cost,
                    "distance_m":        r.distance_m,
                }
            except Exception:
                pass

        # Lightweight fallback when route_cost_function unavailable
        best_bs = self._nearest_bs(edge.midpoint_lat, edge.midpoint_lng)
        latency = self._estimate_latency(edge.midpoint_lat, edge.midpoint_lng, best_bs)
        bs_id = str(best_bs["id"]) if best_bs else None
        load = (
            float(best_bs.get("load", best_bs.get("current_load", 0.0)))
            / max(float(best_bs.get("capacity", 100.0)), 1.0)
            if best_bs else 0.0
        )
        cov_r = float(best_bs.get("coverage_radius_m", 400.0)) if best_bs else 400.0
        dist_to_bs = _haversine_m(
            edge.midpoint_lat, edge.midpoint_lng,
            float(best_bs.get("lat", 0)) if best_bs else 0.0,
            float(best_bs.get("lng", 0)) if best_bs else 0.0,
        )
        handover = bool(prev_best_node_id and bs_id and prev_best_node_id != bs_id)
        return {
            "best_node_id":      bs_id,
            "best_node_name":    best_bs.get("name", bs_id) if best_bs else None,
            "latency_ms":        latency,
            "load_ratio":        _clip01(load),
            "handover_occurred": handover,
            "loss_db":           0.0,
            "within_coverage":   dist_to_bs <= cov_r,
            "total_cost":        0.0,
            "distance_m":        edge.distance_m,
        }

    def _nearest_bs(self, lat: float, lng: float) -> Optional[dict]:
        if self._bs_grid is None:
            return None
        bs, _d = self._bs_grid.nearest(lat, lng)
        return bs

    def _estimate_latency(self, lat: float, lng: float, bs: Optional[dict]) -> float:
        if bs is None:
            return 50.0
        dist_m = _haversine_m(lat, lng, float(bs.get("lat", 0)), float(bs.get("lng", 0)))
        cov_r = float(bs.get("coverage_radius_m", 400.0))
        is_rsu = str(bs.get("type") or "").lower() in ("rsu", "roadside_unit", "rsu_node")
        edge_lat = float(bs.get("edge_latency_ms") or (0.5 if is_rsu else 3.0))

        if _BA_LATENCY_AVAILABLE:
            n_veh = (
                1
                + int(bs.get("n_background_vehicles") or 0)
                + int(bs.get("n_other_devices") or 0)
                + int(bs.get("n_its_load") or 0)
            )
            net_mode = "5G"
            if _rcf is not None:
                net_mode = getattr(_rcf, "_active_network_mode", "5G")
            if is_rsu:
                return round(_ba_L_rsu(dist_m, cov_r) + edge_lat, 2)
            else:
                l_air, _, _, _ = _ba_L_total(dist_m, 0.0, n_veh, net_mode)
                return round(l_air + edge_lat, 2)
        else:
            dist_pen = dist_m / max(cov_r, 1.0) * 15.0
            cong = float(bs.get("congestion_penalty", 0.0))
            return round(4.0 + dist_pen + cong + edge_lat, 2)

    def _blockage_score(self, lat: float, lng: float, bs: Optional[dict]) -> float:
        if bs is None:
            return 1.0
        dist_m = _haversine_m(lat, lng, float(bs.get("lat", 0)), float(bs.get("lng", 0)))
        cov_r = float(bs.get("coverage_radius_m", 400.0))
        return _clip01(dist_m / max(2.0 * cov_r, 1.0))

    def _future_connectivity(self, edges: list[EdgeInfo]) -> float:
        if not edges or self._bs_grid is None:
            return 0.0
        covered = 0
        for e in edges:
            # 커버 반경이 BS마다 다르므로, 최대 반경 안의 BS만 추려(그리드) 각자의 반경으로 판정.
            for bs, d in self._bs_grid.within(e.midpoint_lat, e.midpoint_lng, self._bs_max_cov_m):
                if d <= float(bs.get("coverage_radius_m", 400.0)):
                    covered += 1
                    break
        return covered / len(edges)
