"""
RL Episode Runner — minimal policy executor for V2XRoutingEnv.

Provides three baseline policies that require no training:
  random   — uniformly random valid action (exploration baseline)
  greedy   — picks the candidate edge with the lowest total_cost
  coverage — picks the candidate edge with the best BS signal (lowest loss_db)

These policies serve two purposes:
  1. Validate the RL environment without a trained agent.
  2. Provide heuristic baselines to compare against future DQN/PPO results.

DQN / PPO training is implemented in stage 13+.

Usage
-----
    env = V2XRoutingEnv(graph, road_nodes, bs_nodes, origin_id, dest_id)
    result = run_episode(env, policy="greedy")
    print(result.arrived, result.total_reward)

    stats = run_episodes(env, n_episodes=10, policy="greedy")
    print(stats["arrival_rate"])
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from typing import Optional

from app.services.rl.v2x_routing_env import V2XRoutingEnv, StepResult


SUPPORTED_POLICIES = ("random", "greedy", "coverage")


# ── Result types ──────────────────────────────────────────────────────────────
@dataclass
class StepLog:
    step: int
    node_id: str
    action: int
    edge_id: Optional[str]
    reward: float
    cumulative_reward: float
    dist_to_dest_m: float
    latency_ms: float
    bs_id: Optional[str]
    within_coverage: bool
    handover: bool


@dataclass
class EpisodeResult:
    policy: str
    origin_id: str
    dest_id: str
    total_reward: float
    steps: int
    arrived: bool
    timed_out: bool
    dead_end: bool
    final_dist_m: float
    handover_count: int
    avg_latency_ms: float
    disconnection_steps: int        # steps where within_coverage=False
    trajectory: list[StepLog] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Policy selector ───────────────────────────────────────────────────────────
def _select_action(env: V2XRoutingEnv, policy: str) -> int:
    """
    Select an action index given the current env state and policy.

    Reads env._cached_edge_costs (populated by _build_state after every
    reset/step call) for cost-aware policies.
    """
    valid = env.valid_actions()
    if not valid:
        return 0

    if policy == "random":
        return random.choice(valid)

    costs = env._cached_edge_costs  # pre-computed in _build_state, safe to read

    if policy == "greedy":
        best_action = valid[0]
        best_cost = float("inf")
        for a in valid:
            c = costs[a].get("total_cost", float("inf")) if a < len(costs) else float("inf")
            if c < best_cost:
                best_cost = c
                best_action = a
        return best_action

    if policy == "coverage":
        # Prefer edges with lowest signal loss (strongest BS connection)
        best_action = valid[0]
        best_loss = float("inf")
        for a in valid:
            loss = costs[a].get("loss_db", float("inf")) if a < len(costs) else float("inf")
            if loss < best_loss:
                best_loss = loss
                best_action = a
        return best_action

    return random.choice(valid)


# ── Episode runner ─────────────────────────────────────────────────────────────
def run_episode(
    env: V2XRoutingEnv,
    policy: str = "greedy",
    seed: Optional[int] = None,
    record_trajectory: bool = True,
) -> EpisodeResult:
    """
    Run a single episode using the specified policy.

    Args
    ----
    env    : Initialised V2XRoutingEnv (already knows origin and dest).
    policy : "random" | "greedy" | "coverage"
    seed   : RNG seed (affects random policy only; None = non-deterministic).
    record_trajectory : If False, trajectory list is empty (faster for batch runs).

    Returns
    -------
    EpisodeResult with step log and aggregate metrics.
    """
    if policy not in SUPPORTED_POLICIES:
        raise ValueError(f"Unknown policy '{policy}'. Choose from {SUPPORTED_POLICIES}.")
    if seed is not None:
        random.seed(seed)

    env.reset()
    done = False
    cum_reward = 0.0
    trajectory: list[StepLog] = []
    handovers = 0
    latencies: list[float] = []
    disc_steps = 0
    last_result: Optional[StepResult] = None

    while not done:
        action = _select_action(env, policy)
        result = env.step(action)
        last_result = result

        info = result.info
        lat = float(info.get("latency_ms", 0.0))
        cum_reward += result.reward
        latencies.append(lat)
        if info.get("handover"):
            handovers += 1
        if not info.get("within_coverage", True):
            disc_steps += 1

        if record_trajectory:
            trajectory.append(StepLog(
                step=info.get("step", 0),
                node_id=str(info.get("node_id", "")),
                action=action,
                edge_id=info.get("edge_id"),
                reward=round(result.reward, 4),
                cumulative_reward=round(cum_reward, 4),
                dist_to_dest_m=round(float(info.get("dist_to_dest_m", 0.0)), 2),
                latency_ms=round(lat, 2),
                bs_id=info.get("bs_id"),
                within_coverage=bool(info.get("within_coverage", True)),
                handover=bool(info.get("handover", False)),
            ))

        done = result.done

    last_info = last_result.info if last_result is not None else {}
    s = env._env_state
    final_dist = float(s.dist_to_dest_m) if s else 0.0
    arrived = bool(last_info.get("arrived", False))
    timed_out = bool(last_info.get("timed_out", False))

    return EpisodeResult(
        policy=policy,
        origin_id=env._origin_id,
        dest_id=env._dest_id,
        total_reward=round(cum_reward, 4),
        steps=len(latencies),
        arrived=arrived,
        timed_out=timed_out,
        dead_end=(not arrived and not timed_out and len(latencies) > 0),
        final_dist_m=round(final_dist, 2),
        handover_count=handovers,
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        disconnection_steps=disc_steps,
        trajectory=trajectory,
    )


def run_episodes(
    env: V2XRoutingEnv,
    n_episodes: int,
    policy: str = "greedy",
    seed: Optional[int] = None,
    record_trajectory: bool = False,
) -> dict:
    """
    Run ``n_episodes`` with the given policy and return aggregate statistics.

    Args
    ----
    env             : Initialised V2XRoutingEnv.
    n_episodes      : Number of episodes to run.
    policy          : "random" | "greedy" | "coverage"
    seed            : Base RNG seed (episode i uses seed+i when set).
    record_trajectory : Include per-step logs in result (heavy; default False).

    Returns
    -------
    dict with keys:
      policy, n_episodes, n_arrived, n_timed_out, n_dead_end,
      arrival_rate, mean_reward, mean_steps, mean_handovers,
      mean_latency_ms, mean_disconnection_steps, episodes.
    """
    results: list[EpisodeResult] = []
    for i in range(n_episodes):
        ep_seed = (seed + i) if seed is not None else None
        r = run_episode(env, policy=policy, seed=ep_seed, record_trajectory=record_trajectory)
        results.append(r)

    n = len(results)
    n_arrived = sum(1 for r in results if r.arrived)
    n_to = sum(1 for r in results if r.timed_out)
    n_de = sum(1 for r in results if r.dead_end)

    def _mean(key):
        vals = [getattr(r, key) for r in results]
        return round(sum(vals) / n, 4) if n else 0.0

    return {
        "policy": policy,
        "n_episodes": n,
        "n_arrived": n_arrived,
        "n_timed_out": n_to,
        "n_dead_end": n_de,
        "arrival_rate": round(n_arrived / n, 4) if n else 0.0,
        "mean_reward": _mean("total_reward"),
        "mean_steps": _mean("steps"),
        "mean_handovers": _mean("handover_count"),
        "mean_latency_ms": _mean("avg_latency_ms"),
        "mean_disconnection_steps": _mean("disconnection_steps"),
        "episodes": [r.to_dict() for r in results],
    }
