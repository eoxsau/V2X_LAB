"""
Behavior Cloning (BC) warm-start for MAML/PEARL V4 policy.

Teacher policy: V2X-weighted Dijkstra (not pure distance Dijkstra).

Why V2X-weighted Dijkstra, not pure distance:
  Pure distance Dijkstra teaches "geographic shortest path" — agnostic to
  BS coverage, latency, or handover cost.  The RL objective is V2X quality,
  so BC should teach V2X-quality-aware routing from the start.

  V2X edge weight = 0.3·dist_km + 0.5·coverage_penalty + 0.2·latency_proxy
    - coverage_penalty: large if destination node is outside BS/RSU coverage radius
    - latency_proxy:    proportional to distance to nearest BS (path loss proxy)
                        [Fernandez et al., IEEE WCL 2014, log-distance model §III]

Paper comparison baselines (all runnable via evaluate_dijkstra_baselines()):
  - Baseline A: pure distance Dijkstra   (use_v2x_weights=False)
  - Baseline B: V2X-weighted Dijkstra    (use_v2x_weights=True)  ← BC teacher
  - Ours:       Reptile meta-RL policy

Both baselines are executed through the same UniversalV2XEnv as the RL policy,
ensuring fair KPI comparison (E2E latency, PRR, AoI, handover count, arrival rate).

References:
  [1] Pomerleau, D., "ALVINN: An Autonomous Land Vehicle in a Neural Net",
      NeurIPS 1988.
  [2] Nair et al., "Overcoming Exploration in RL with Demonstrations", ICRA 2018.
  [3] Fernandez et al., "Path Loss Characterization for Vehicular Communications
      at 700 MHz and 5.9 GHz", IEEE Wireless Communications Letters, Vol.3, 2014.
"""
from __future__ import annotations

import heapq
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5   # must match universal_v2x_env.MAX_CANDIDATES


# ── V2X edge weight computation ───────────────────────────────────────────────

def _v2x_edge_weight(
    dst_id: str,
    dist_m: float,
    nodes: Dict,
    bs_rsu_list: List[Dict],
    coverage_radius_m: float,
    w_dist: float,
    w_cov: float,
    w_lat: float,
    haversine_fn,
) -> float:
    """
    Composite V2X cost for a single directed edge → dst_id.

    bs_rsu_list: list of BS/RSU dicts {lat, lng, coverage_radius_m, ...}
                 (task.bs_nodes + task.rsu_nodes from DomainRandomizer)

    Components (normalized to comparable scale):
      dist_km      : geographic distance [km]
      cov_penalty  : 0 if dst within nearest BS/RSU coverage radius, else
                     proportional to excess distance (capped at 3.0)
      latency_proxy: min_dist_to_BS / nearest_bs_radius (capped at 3.0).
                     [Fernandez et al. 2014, PL(d)=PL(d0)+10n·log10(d/d0)]
    """
    dst = nodes.get(dst_id, {})
    d_lat = float(dst.get("lat", 0.0))
    d_lng = float(dst.get("lng", 0.0))

    dist_cost = dist_m / 1000.0   # metres → km

    if bs_rsu_list:
        min_bs_dist = float("inf")
        nearest_radius = coverage_radius_m
        for b in bs_rsu_list:
            b_lat = float(b.get("lat", 0.0))
            b_lng = float(b.get("lng", 0.0))
            d = haversine_fn(d_lat, d_lng, b_lat, b_lng)
            if d < min_bs_dist:
                min_bs_dist = d
                nearest_radius = float(b.get("coverage_radius_m", coverage_radius_m))
        nearest_radius = max(nearest_radius, 1.0)
        cov_penalty  = max(0.0, (min_bs_dist - nearest_radius) / nearest_radius)
        cov_penalty  = min(cov_penalty, 3.0)
        latency_norm = min(min_bs_dist / nearest_radius, 3.0)
    else:
        cov_penalty  = 0.0
        latency_norm = 1.0

    return w_dist * dist_cost + w_cov * cov_penalty + w_lat * latency_norm


def build_v2x_adjacency(
    adjacency: Dict[str, List[Tuple[str, float]]],
    nodes: Dict,
    bs_rsu_list: List[Dict],
    coverage_radius_m: float = 300.0,
    w_dist: float = 0.3,
    w_cov: float  = 0.5,
    w_lat: float  = 0.2,
) -> Dict[str, List[Tuple[str, float]]]:
    """
    Build V2X-weighted adjacency dict for Dijkstra teacher.

    Numpy-vectorized haversine: entire (n_nodes × n_bs) distance matrix in one
    call — ~1000× faster than Python loops.
    Complexity: O(n_nodes × n_bs) with numpy BLAS-level speed.

    For a 5000-node graph with 15 BS/RSUs:
      Python loop: ~75,000 function calls → 50s/task
      Numpy:       1 matrix op (75K elements) → <1ms/task
    """
    import numpy as np

    node_ids  = list(nodes.keys())
    n_nodes   = len(node_ids)

    if n_nodes == 0 or not bs_rsu_list:
        # No BS/RSU data — fall back to pure distance (cov/lat terms = 0)
        v2x_adj: Dict[str, List[Tuple[str, float]]] = {}
        for src_id, neighbors in adjacency.items():
            v2x_adj[src_id] = [(dst_id, dist_m / 1000.0 * w_dist)
                                for dst_id, dist_m in neighbors]
        return v2x_adj

    # ── Step 1: vectorized haversine (n_nodes × n_bs) ────────────────────────
    R = 6_371_000.0  # Earth radius [m]

    node_lats = np.radians([float(nodes[nid].get("lat", 0.0)) for nid in node_ids])
    node_lngs = np.radians([float(nodes[nid].get("lng", 0.0)) for nid in node_ids])

    bs_lats   = np.radians([float(b.get("lat",              0.0)) for b in bs_rsu_list])
    bs_lngs   = np.radians([float(b.get("lng",              0.0)) for b in bs_rsu_list])
    bs_radii  = np.array(  [float(b.get("coverage_radius_m", coverage_radius_m))
                             for b in bs_rsu_list], dtype=np.float64)

    # Broadcast: (n_nodes, n_bs)
    dlat = bs_lats[None, :] - node_lats[:, None]
    dlng = bs_lngs[None, :] - node_lngs[:, None]
    a    = (np.sin(dlat * 0.5) ** 2
            + np.cos(node_lats)[:, None] * np.cos(bs_lats)[None, :]
            * np.sin(dlng * 0.5) ** 2)
    dist_mat = R * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))  # (n_nodes, n_bs)

    # Nearest BS per node
    nearest_idx  = np.argmin(dist_mat, axis=1)                       # (n_nodes,)
    min_bs_dists = dist_mat[np.arange(n_nodes), nearest_idx]         # (n_nodes,)
    near_radii   = np.maximum(bs_radii[nearest_idx], 1.0)            # (n_nodes,)

    # V2X cost terms per node
    cov_pen   = np.clip((min_bs_dists - near_radii) / near_radii, 0.0, 3.0)
    lat_norm  = np.clip(min_bs_dists / near_radii,               0.0, 3.0)

    # {node_id: (cov_penalty, latency_norm)} lookup
    node_v2x = {nid: (float(cov_pen[i]), float(lat_norm[i]))
                for i, nid in enumerate(node_ids)}

    # ── Step 2: build adjacency with precomputed per-node V2X costs ───────────
    v2x_adj = {}
    for src_id, neighbors in adjacency.items():
        weighted = []
        for dst_id, dist_m in neighbors:
            cp, ln = node_v2x.get(dst_id, (0.0, 1.0))
            w = w_dist * (dist_m / 1000.0) + w_cov * cp + w_lat * ln
            weighted.append((dst_id, w))
        v2x_adj[src_id] = weighted
    return v2x_adj


# ── Dijkstra on weighted adjacency ────────────────────────────────────────────

def dijkstra(
    adjacency: Dict[str, List[Tuple[str, float]]],
    src: str,
    dst: str,
) -> Optional[List[str]]:
    """
    Dijkstra shortest path.

    Works on any weighted adjacency: {node_id: [(neighbor_id, weight), ...]}
    Pass raw adjacency for pure distance, or build_v2x_adjacency() for V2X cost.

    Returns ordered node ID list from src to dst (inclusive), or None if no path.
    """
    if src == dst:
        return [src]
    if src not in adjacency:
        return None

    dist: Dict[str, float] = {src: 0.0}
    prev: Dict[str, Optional[str]] = {src: None}
    heap: List[Tuple[float, str]] = [(0.0, src)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if u == dst:
            break
        for v, w in adjacency.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if dst not in dist:
        return None

    path: List[str] = []
    node: Optional[str] = dst
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()
    return path if path[0] == src else None


def _sorted_neighbors_bc(
    adjacency: Dict[str, List[Tuple[str, float]]],
    node_id: str,
    dest_id: str,
    nodes: Dict,
    max_candidates: int = MAX_CANDIDATES,
) -> List[str]:
    """
    Return neighbor node IDs sorted by dist(neighbor→dest).

    Matches env._sorted_neighbors() ordering (G11 fix) so BC action indices
    align with the training environment's action space exactly.
    """
    from .domain_randomizer import _haversine_m
    nbrs = adjacency.get(node_id, [])
    dest = nodes.get(dest_id, {})
    d_lat, d_lng = float(dest.get("lat", 0)), float(dest.get("lng", 0))

    def dist_to_dest(nbr_id: str) -> float:
        nd = nodes.get(nbr_id, {})
        return _haversine_m(float(nd.get("lat", 0)), float(nd.get("lng", 0)), d_lat, d_lng)

    sorted_nbrs = sorted(nbrs, key=lambda x: dist_to_dest(x[0]))
    return [n for n, _ in sorted_nbrs[:max_candidates]]


# ── BC dataset ────────────────────────────────────────────────────────────────

@dataclass
class BCTransition:
    """A single (obs, teacher_action_index) pair for BC training."""
    obs: object
    action_idx: int
    mask: List[bool]


@dataclass
class BCDataset:
    """Collected BC demonstrations."""
    transitions: List[BCTransition] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.transitions)

    def sample_batch(self, batch_size: int) -> List[BCTransition]:
        return random.sample(self.transitions, min(batch_size, len(self.transitions)))


# ── BC dataset collection ──────────────────────────────────────────────────────

def collect_bc_dataset(
    randomizer,
    env_cls,
    n_tasks: int = 200,
    n_episodes_per_task: int = 4,
    max_candidates: int = MAX_CANDIDATES,
    seed: int = 0,
    use_v2x_weights: bool = True,
    coverage_radius_m: float = 300.0,
    w_dist: float = 0.3,
    w_cov: float  = 0.5,
    w_lat: float  = 0.2,
    max_steps_per_episode: int = 5,
) -> BCDataset:
    """
    Collect BC demonstrations using V2X-weighted Dijkstra as teacher.

    use_v2x_weights=True  (default): V2X-aware teacher — coverage + latency aware
    use_v2x_weights=False           : pure distance Dijkstra — paper baseline A

    The resulting dataset teaches the policy:
      - Navigate toward goal (topological intuition)
      - Stay within BS/RSU coverage (V2X connectivity)
      - Prefer nodes close to infrastructure (lower latency)
    """
    try:
        from .universal_gnn_policy import build_graph_obs
        _has_pyg = True
    except ImportError:
        log.warning("BC dataset collection skipped: torch_geometric not available.")
        return BCDataset()

    rng = random.Random(seed)
    dataset = BCDataset()
    skipped_tasks = 0
    skipped_steps = 0

    for task_i in range(n_tasks):
        task = randomizer.sample_task()
        if task is None:
            skipped_tasks += 1
            continue

        graph     = task.graph
        adjacency = graph.get("adjacency", {})
        nodes     = graph.get("nodes", {})
        node_ids  = list(nodes.keys())

        if len(node_ids) < 10:
            skipped_tasks += 1
            continue

        if use_v2x_weights:
            bs_rsu_list = list(task.bs_nodes) + list(task.rsu_nodes)
            teacher_adj = build_v2x_adjacency(
                adjacency, nodes, bs_rsu_list,
                coverage_radius_m=coverage_radius_m,
                w_dist=w_dist, w_cov=w_cov, w_lat=w_lat,
            )
        else:
            teacher_adj = adjacency

        for _ in range(n_episodes_per_task):
            src = rng.choice(node_ids)
            dst = rng.choice(node_ids)
            if src == dst:
                continue

            path = dijkstra(teacher_adj, src, dst)
            if path is None or len(path) < 2:
                skipped_steps += 1
                continue

            n_steps = len(path) - 1
            if max_steps_per_episode > 0 and n_steps > max_steps_per_episode:
                # sample evenly-spaced steps so we see early AND late path decisions
                step_indices = [int(i * n_steps / max_steps_per_episode)
                                for i in range(max_steps_per_episode)]
            else:
                step_indices = list(range(n_steps))

            for step_i in step_indices:
                cur_id  = path[step_i]
                next_id = path[step_i + 1]

                try:
                    obs = build_graph_obs(
                        graph=graph,
                        bs_nodes=task.bs_nodes + task.rsu_nodes,
                        rsu_nodes=task.rsu_nodes,
                        current_node_id=cur_id,
                        dest_node_id=dst,
                        network_mode=task.network_mode,
                        initial_dist=1000.0,
                        global_ctx=[0.0] * 6,
                    )
                except Exception:
                    skipped_steps += 1
                    continue

                # Action indices use geographic sort (same as env._sorted_neighbors)
                sorted_nbrs = _sorted_neighbors_bc(adjacency, cur_id, dst, nodes, max_candidates)
                if next_id not in sorted_nbrs:
                    skipped_steps += 1
                    continue

                action_idx = sorted_nbrs.index(next_id)
                n_valid    = len(sorted_nbrs)
                mask       = [i < n_valid for i in range(max_candidates)]
                dataset.transitions.append(BCTransition(obs=obs, action_idx=action_idx, mask=mask))

        if (task_i + 1) % 10 == 0:
            mode_str = "V2X-weighted" if use_v2x_weights else "dist-only"
            log.info("BC collection [%s]: %d/%d tasks, %d transitions",
                     mode_str, task_i + 1, n_tasks, len(dataset))

    mode_str = "V2X-weighted Dijkstra" if use_v2x_weights else "pure distance Dijkstra"
    log.info("BC dataset ready [%s]: %d transitions from %d tasks",
             mode_str, len(dataset), n_tasks - skipped_tasks)
    return dataset


# ── BC pretraining loop ───────────────────────────────────────────────────────

@dataclass
class BCConfig:
    """Behavior Cloning pretraining configuration."""
    n_tasks:             int   = 300
    n_episodes_per_task: int   = 4
    n_epochs:            int   = 30
    batch_size:          int   = 128
    lr:                  float = 3e-4
    weight_decay:        float = 1e-4
    log_every:           int   = 5
    seed:                int   = 42
    maml_entropy_coef_start_after_bc: float = 0.02

    max_steps_per_episode: int = 5  # cap build_graph_obs calls/episode (0=no limit)

    # V2X-weighted Dijkstra teacher
    use_v2x_weights:   bool  = True    # False → pure distance (Baseline A)
    coverage_radius_m: float = 300.0   # BS/RSU coverage radius [m]
    w_dist:            float = 0.3     # distance component weight
    w_cov:             float = 0.5     # coverage penalty weight
    w_lat:             float = 0.2     # latency proxy weight


class BCPretrainer:
    """
    V2X-weighted Dijkstra supervised pretraining for UniversalGNNPolicy.

    Teacher: Dijkstra on composite V2X edge weights (coverage + latency + distance).
    Also usable with use_v2x_weights=False for pure-distance comparison baseline.

    Usage:
        bc_cfg = BCConfig(n_tasks=1000, n_epochs=50, use_v2x_weights=True)
        bc     = BCPretrainer(policy, randomizer, bc_cfg)
        result = bc.run()
        trainer.bc_apply(result)
    """

    def __init__(self, policy, randomizer, config=None, device=None):
        try:
            import torch
            import torch.nn as nn
            import torch.optim as optim
            self._torch = torch
            self._nn    = nn
            self._optim = optim
        except ImportError:
            raise ImportError("BCPretrainer requires torch. pip install torch torch-geometric")

        self.cfg        = config or BCConfig()
        self.policy     = policy
        self.randomizer = randomizer
        self.device     = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.policy.to(self.device)

    def run(self) -> dict:
        """Run BC pretraining. Modifies self.policy in-place."""
        import torch
        from torch_geometric.data import Batch as _Batch

        mode_str = "V2X-weighted Dijkstra" if self.cfg.use_v2x_weights else "pure distance Dijkstra"
        log.info("[BC] Collecting demonstrations [%s] …", mode_str)
        t0 = time.perf_counter()
        dataset = collect_bc_dataset(
            self.randomizer,
            env_cls=None,
            n_tasks=self.cfg.n_tasks,
            n_episodes_per_task=self.cfg.n_episodes_per_task,
            max_candidates=MAX_CANDIDATES,
            seed=self.cfg.seed,
            use_v2x_weights=self.cfg.use_v2x_weights,
            coverage_radius_m=self.cfg.coverage_radius_m,
            w_dist=self.cfg.w_dist,
            w_cov=self.cfg.w_cov,
            w_lat=self.cfg.w_lat,
            max_steps_per_episode=self.cfg.max_steps_per_episode,
        )
        if len(dataset) == 0:
            log.warning("[BC] Empty dataset — skipping BC pretraining.")
            return {"skipped": True, "reason": "empty_dataset"}

        log.info("[BC] %d transitions collected in %.1fs.", len(dataset), time.perf_counter() - t0)

        optimizer = self._optim.AdamW(
            self.policy.parameters(),
            lr=self.cfg.lr,
            weight_decay=self.cfg.weight_decay,
            foreach=False,
        )

        self.policy.train()
        history = []

        for epoch in range(self.cfg.n_epochs):
            random.shuffle(dataset.transitions)
            epoch_loss = 0.0
            epoch_acc  = 0.0
            n_batches  = 0

            for i in range(0, len(dataset), self.cfg.batch_size):
                batch_trans = dataset.transitions[i: i + self.cfg.batch_size]
                if not batch_trans:
                    continue

                obs_dev = []
                for t in batch_trans:
                    d = t.obs.to(self.device) if hasattr(t.obs, "to") else t.obs
                    if hasattr(d, "node_ids"):
                        del d.node_ids
                    obs_dev.append(d)

                try:
                    batch         = _Batch.from_data_list(obs_dev)
                    acts_t        = torch.tensor([t.action_idx for t in batch_trans],
                                                 dtype=torch.long, device=self.device)
                    masks_t       = torch.tensor([t.mask for t in batch_trans],
                                                 dtype=torch.bool, device=self.device)
                    logits, _     = self.policy(batch, action_mask=masks_t)
                    logits_masked = logits.masked_fill(~masks_t, float("-inf"))
                    loss = self._nn.functional.cross_entropy(logits_masked, acts_t)

                    if torch.isnan(loss) or torch.isinf(loss):
                        continue
                    optimizer.zero_grad()
                    loss.backward()
                    params_with_grad = [p for p in self.policy.parameters()
                                        if p.grad is not None]
                    if params_with_grad:
                        torch.nn.utils.clip_grad_norm_(params_with_grad, max_norm=5.0)
                        optimizer.step()

                    with torch.no_grad():
                        acc = (logits_masked.argmax(dim=-1) == acts_t).float().mean().item()

                    epoch_loss += loss.item()
                    epoch_acc  += acc
                    n_batches  += 1

                except Exception as e:
                    log.debug("[BC] batch skip: %s", e)
                    continue

            mean_loss = epoch_loss / max(n_batches, 1)
            mean_acc  = epoch_acc  / max(n_batches, 1)
            history.append({"epoch": epoch, "loss": mean_loss, "acc": mean_acc})

            if (epoch + 1) % self.cfg.log_every == 0:
                log.info("[BC] epoch %d/%d  loss=%.4f  acc=%.1f%%",
                         epoch + 1, self.cfg.n_epochs, mean_loss, mean_acc * 100)

        final_acc = history[-1]["acc"] if history else 0.0
        log.info("[BC] Pretraining complete [%s]. Final accuracy %.1f%%.",
                 mode_str, final_acc * 100)

        return {
            "n_transitions":  len(dataset),
            "n_epochs":       self.cfg.n_epochs,
            "final_loss":     history[-1]["loss"] if history else None,
            "final_acc":      final_acc,
            "history":        history,
            "teacher":        mode_str,
            "maml_overrides": {
                "ppo_entropy_coef_start": self.cfg.maml_entropy_coef_start_after_bc,
            },
        }


# ── Paper comparison baseline evaluator ──────────────────────────────────────

def _dijkstra_rollout_episode(
    env,
    task,
    adjacency: Dict,
    nodes: Dict,
    bs_rsu_list: List[Dict],
    use_v2x_weights: bool,
    coverage_radius_m: float,
    w_dist: float,
    w_cov: float,
    w_lat: float,
    max_steps: int,
) -> Optional[Dict]:
    """
    Run one Dijkstra-policy episode through UniversalV2XEnv.

    The episode O/D pair is determined by env.reset() (resample_od=True).
    Dijkstra is then solved for that specific O/D on the appropriate adjacency.
    Action selection: find Dijkstra next-hop in env's sorted neighbor list.

    Returns dict of episode KPIs, or None if env reset failed.
    """
    obs, info = env.reset(task=task, resample_od=True)
    src = info.get("origin")
    dst = info.get("dest")
    if src is None or dst is None:
        return None

    # Compute teacher path for this O/D
    if use_v2x_weights and bs_rsu_list:
        teacher_adj = build_v2x_adjacency(
            adjacency, nodes, bs_rsu_list,
            coverage_radius_m=coverage_radius_m,
            w_dist=w_dist, w_cov=w_cov, w_lat=w_lat,
        )
    else:
        teacher_adj = adjacency

    path = dijkstra(teacher_adj, src, dst)

    # Build a lookup: node_id → position in path
    path_pos: Dict[str, int] = {}
    if path:
        for i, n in enumerate(path):
            path_pos[n] = i

    total_reward = 0.0
    sum_latency  = 0.0
    sum_prr      = 0.0
    sum_aoi      = 0.0
    handover     = 0
    n_steps      = 0
    arrived      = False
    current_node = src

    for _ in range(max_steps):
        mask = env.action_masks()
        if not any(mask):
            break

        # Determine next-hop from Dijkstra path
        pos = path_pos.get(current_node)
        if path is not None and pos is not None and pos + 1 < len(path):
            next_hop = path[pos + 1]
        else:
            next_hop = None

        # Find action index (geographic sort, same as env._sorted_neighbors)
        sorted_nbrs = _sorted_neighbors_bc(adjacency, current_node, dst, nodes)
        action = 0  # default: greedy (first valid neighbor, closest to dest)
        if next_hop is not None and next_hop in sorted_nbrs:
            candidate = sorted_nbrs.index(next_hop)
            if candidate < len(mask) and mask[candidate]:
                action = candidate
            else:
                # Dijkstra next-hop masked out → greedy fallback
                for i, valid in enumerate(mask):
                    if valid:
                        action = i
                        break
        else:
            for i, valid in enumerate(mask):
                if valid:
                    action = i
                    break

        obs, reward, term, trunc, info = env.step(action)
        done = term or trunc
        n_steps += 1
        total_reward += reward
        sum_latency  += info.get("latency_ms",       0.0)
        sum_prr      += info.get("prr",               0.0)
        sum_aoi      += info.get("aoi_normalized",    0.0)
        handover      = info.get("handover_count",    0)
        current_node  = info.get("node_id", current_node)

        if done:
            arrived = info.get("arrived", False)
            break

    n = max(n_steps, 1)
    return {
        "arrived":         arrived,
        "total_reward":    total_reward,
        "avg_latency_ms":  sum_latency / n,
        "avg_prr":         sum_prr     / n,
        "avg_aoi_norm":    sum_aoi     / n,
        "handover_count":  handover,
        "n_steps":         n_steps,
    }


def evaluate_dijkstra_baselines(
    holdout_randomizer,
    n_tasks: int = 30,
    n_episodes_per_task: int = 5,
    max_steps: int = 100,
    coverage_radius_m: float = 300.0,
    w_dist: float = 0.3,
    w_cov: float  = 0.5,
    w_lat: float  = 0.2,
    seed: int = 42,
) -> Dict:
    """
    Evaluate both Dijkstra baselines on holdout tasks.

    Runs BOTH baselines through UniversalV2XEnv so KPIs (latency, PRR, AoI,
    handover, arrival rate) are directly comparable to RL policy evaluation.

    Paper table usage:
        baselines = evaluate_dijkstra_baselines(holdout_rnd, n_tasks=50)
        # baselines["dist_dijkstra"]["arrival_rate"] → Baseline A arrival rate
        # baselines["v2x_dijkstra"]["arrival_rate"]  → Baseline B arrival rate

    Args:
        holdout_randomizer: DomainRandomizer(train=False, seed=42)
                            — must use the SAME seed as training split
        n_tasks:            holdout tasks to sample (30-50 for paper)
        n_episodes_per_task: episodes per task (5 for statistical stability)

    Returns:
        {
          "dist_dijkstra": {"arrival_rate", "avg_latency_ms", "avg_prr",
                            "avg_aoi_norm", "avg_handover", "n_episodes"},
          "v2x_dijkstra":  { same fields },
        }
    """
    try:
        from .universal_v2x_env import UniversalV2XEnv
    except ImportError:
        log.warning("[Baseline] UniversalV2XEnv not available — skipping baseline eval.")
        return {}

    rng = random.Random(seed)
    results: Dict[bool, List[Dict]] = {False: [], True: []}

    for use_v2x in [False, True]:
        tag = "V2X-Dijkstra" if use_v2x else "Dist-Dijkstra"
        env = UniversalV2XEnv(holdout_randomizer, channel_lookup=None,
                              max_steps=max_steps, use_icm=False)

        for task_i in range(n_tasks):
            task = holdout_randomizer.sample_task()
            if task is None:
                continue

            graph      = task.graph
            adjacency  = graph.get("adjacency", {})
            nodes      = graph.get("nodes", {})
            bs_rsu_list = list(task.bs_nodes) + list(task.rsu_nodes)

            for _ in range(n_episodes_per_task):
                ep = _dijkstra_rollout_episode(
                    env, task, adjacency, nodes, bs_rsu_list,
                    use_v2x_weights=use_v2x,
                    coverage_radius_m=coverage_radius_m,
                    w_dist=w_dist, w_cov=w_cov, w_lat=w_lat,
                    max_steps=max_steps,
                )
                if ep is not None:
                    results[use_v2x].append(ep)

            if (task_i + 1) % 10 == 0:
                log.info("[Baseline %s] %d/%d tasks, %d episodes",
                         tag, task_i + 1, n_tasks, len(results[use_v2x]))

    def _aggregate(eps: List[Dict]) -> Dict:
        if not eps:
            return {}
        n = len(eps)
        return {
            "arrival_rate":   round(sum(1.0 for e in eps if e["arrived"]) / n, 3),
            "avg_latency_ms": round(sum(e["avg_latency_ms"] for e in eps) / n, 2),
            "avg_prr":        round(sum(e["avg_prr"]        for e in eps) / n, 3),
            "avg_aoi_norm":   round(sum(e["avg_aoi_norm"]   for e in eps) / n, 4),
            "avg_handover":   round(sum(e["handover_count"] for e in eps) / n, 2),
            "n_episodes":     n,
        }

    out = {
        "dist_dijkstra": _aggregate(results[False]),
        "v2x_dijkstra":  _aggregate(results[True]),
    }

    log.info("[Baseline] dist-Dijkstra: arr=%.0f%%  lat=%.1fms  PRR=%.3f",
             out["dist_dijkstra"].get("arrival_rate", 0) * 100,
             out["dist_dijkstra"].get("avg_latency_ms", 0),
             out["dist_dijkstra"].get("avg_prr", 0))
    log.info("[Baseline] V2X-Dijkstra:  arr=%.0f%%  lat=%.1fms  PRR=%.3f",
             out["v2x_dijkstra"].get("arrival_rate", 0) * 100,
             out["v2x_dijkstra"].get("avg_latency_ms", 0),
             out["v2x_dijkstra"].get("avg_prr", 0))
    return out
