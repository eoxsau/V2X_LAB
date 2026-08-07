"""
Reptile Meta-RL Trainer for Universal V2X Policy.

Algorithm (Nichol et al. 2018, §2):
  θ  = meta-parameters (GNN policy weights, shared across all tasks)
  α  = inner learning rate (task adaptation)
  ε  = outer/meta step size (Reptile update)

  For each meta-iteration:
    1. Sample N tasks τ₁…τ_N from DomainRandomizer (curriculum)
    2. For each τ_i:
       a. Clone model: φ_i ← θ  (via shared memory, zero-copy)
       b. Run K inner SGD steps on support set of τ_i → φ'_i
          (no separate query phase — Reptile's key advantage)
    3. Meta-update: θ ← θ + ε × (1/N) × Σᵢ (φ'_i − θ)
       Implemented via Adam with pseudo-grad = −(φ'_i − θ)

Reptile vs FOMAML:
  - No second-order terms → simpler, same convergence (Nichol 2018 §3)
  - No query-set collection → ~35% fewer episodes per iteration
  - Shared-memory weight broadcast → eliminates 64×20MB IPC pickle/iter
  - Combined: ~2× faster iterations vs FOMAML with equal or better quality

References:
  [1] Nichol, A., Achiam, J., Schulman, J., "On First-Order Meta-Learning
      Algorithms", arXiv:1803.02999, 2018.
  [2] Finn, C., Abbeel, P., Levine, S., "Model-Agnostic Meta-Learning for
      Fast Adaptation of Deep Networks", ICML 2017, arXiv:1703.03400.
  [3] Schulman, J. et al., "Proximal Policy Optimization Algorithms",
      arXiv:1707.06347, 2017.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_log = logging.getLogger("v4.maml")

from .domain_randomizer import DomainRandomizer, V2XTask
from .universal_v2x_env import UniversalV2XEnv

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from .universal_gnn_policy import UniversalGNNPolicy, IntrinsicCuriosityModule
    _TORCH_GEO_AVAILABLE = True
except ImportError:
    _TORCH_GEO_AVAILABLE = False

_MAX_NODES = 2000   # must match domain_randomizer._MAX_NODES


# ── Hyper-parameters (A100 defaults) ──────────────────────────────────────────
@dataclass
class MAMLConfig:
    # Meta
    n_tasks_per_iter: int = 16
    meta_iterations: int = 10_000
    meta_lr: float = 5e-4      # 3e-4→5e-4: cosine decay handles end; faster early convergence
    meta_lr_schedule: str = "cosine"   # "constant" | "cosine" | "linear"

    # Inner loop (Reptile)
    inner_steps: int = 5        # K inner SGD steps per task
    inner_lr: float = 3e-3      # 1e-3→3e-3: Reptile needs larger inner steps for meaningful (φ'−θ)
    support_episodes: int = 8   # support set size (Reptile: no separate query set)
    query_episodes: int = 0     # unused in Reptile; kept for checkpoint compat

    # PPO (inner)
    ppo_clip_eps: float = 0.2
    ppo_value_coef: float = 0.5
    ppo_entropy_coef: float = 0.01
    ppo_entropy_coef_start: float = 0.05
    ppo_entropy_coef_end: float = 0.005
    ppo_entropy_coef_decay_iters: int = 5_000
    meta_grad_clip: float = 10.0
    ppo_epochs: int = 4
    ppo_mini_bsz: int = 60          # mini-batch size per PPO epoch (0 = full-batch)
    gamma: float = 0.99
    gae_lambda: float = 0.95

    max_episode_steps: int = 100   # 200→100: failed episodes 2× faster

    # Curriculum (Bengio et al. 2009)
    curriculum_warmup_iters: int = 1000   # 2000→1000: reach hard tasks faster with 5k iter budget
    curriculum_start_stage: int = 0

    # Persistence
    save_dir: str = "app/services/rl/models"
    save_every: int = 50
    log_every: int = 10

    # GPU throughput
    use_amp: bool = True
    holdout_eval_every: int = 100
    holdout_seed: int = 42

    # ICM (Pathak et al. 2017)
    use_icm: bool = True
    icm_hidden: int = 64
    icm_beta: float = 0.2
    icm_reward_scale: float = 0.05
    icm_lr: float = 1e-3

    # HER (Andrychowicz et al. 2017)
    use_her: bool = True
    her_ratio: float = 0.6     # 0.4→0.6: max_steps=100 increases failures, HER recycles them
    her_min_od_m: float = 200.0

    # Device
    device: str = (
        "cuda" if __import__("torch").cuda.is_available() else
        "mps"  if __import__("torch").backends.mps.is_available() else
        "cpu"
    )
    compile_model: bool = False
    collect_once: bool = False  # collect episodes once, reuse for all inner steps (3× faster collection)
    n_workers: int = 12   # 10→12 (Reptile lower VRAM → room for 2 more workers)


@dataclass
class _Episode:
    """One collected episode with V2X KPI fields."""
    obs_list: list        = field(default_factory=list)
    actions: list         = field(default_factory=list)
    rewards: list         = field(default_factory=list)
    log_probs: list       = field(default_factory=list)
    values: list          = field(default_factory=list)
    masks: list           = field(default_factory=list)
    arrived: bool         = False
    total_reward: float   = 0.0
    avg_latency_ms: float = 0.0
    avg_prr: float        = 0.0
    avg_aoi_norm: float   = 0.0
    handover_count: int   = 0
    n_steps: int          = 0
    global_ctxs: list     = field(default_factory=list)
    node_ids_visited: list = field(default_factory=list)


class _RunningStats:
    """Welford online algorithm for return normalization."""

    def __init__(self) -> None:
        self.n    = 0
        self.mean = 0.0
        self.M2   = 0.0

    def update(self, x: "torch.Tensor") -> None:
        batch = x.detach().cpu().float().flatten()
        m = int(batch.numel())
        if m == 0:
            return
        batch_mean = float(batch.mean())
        batch_var  = float(batch.var(unbiased=False)) if m > 1 else 0.0
        delta     = batch_mean - self.mean
        n_new     = self.n + m
        self.mean += delta * m / n_new
        self.M2   += batch_var * m + delta * delta * self.n * m / n_new
        self.n     = n_new

    @property
    def std(self) -> float:
        return (self.M2 / max(self.n - 1, 1)) ** 0.5

    def normalize(self, x: "torch.Tensor") -> "torch.Tensor":
        return (x - self.mean) / (self.std + 1e-8)


# ── Multiprocessing worker infrastructure ─────────────────────────────────────
# All functions must be module-level (picklable) for ProcessPoolExecutor spawn.

class _W:
    """Per-subprocess singleton — initialised once by _worker_init()."""
    device         = None
    model          = None
    icm            = None
    randomizer     = None
    use_amp: bool  = False
    # Zero-copy weight sharing: shared-memory CPU tensors updated by main process.
    # Workers call .clone() each task to get a mutable snapshot.
    shared_weights: dict = {}
    envs: list           = None   # persistent env pool (reused across tasks)
    thread_pool          = None   # ThreadPoolExecutor for parallel env.step


def _env_step(args):
    """Module-level helper so ThreadPoolExecutor can pickle it."""
    i, env, act = args
    return i, env.step(act)


def _worker_init(use_amp: bool, seed_base: int, icm_hidden: int) -> None:
    """Called once per worker subprocess at pool startup."""
    import torch
    torch.set_num_threads(2)   # prevent OMP thread explosion across workers
    _W.use_amp = use_amp
    _W.device  = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    from .universal_gnn_policy import UniversalGNNPolicy
    _W.model = UniversalGNNPolicy().to(_W.device)
    if _W.device.type == "cuda":
        torch.cuda.empty_cache()

    _W.thread_pool = None  # disabled — caused futex deadlock with shared CUDA context

    try:
        from .domain_randomizer import DomainRandomizer
        _W.randomizer = DomainRandomizer(train=True, seed=seed_base + os.getpid() % 100_000)
    except Exception:
        _W.randomizer = None


def _wgae(ep: "_Episode", gamma: float, gae_lambda: float) -> tuple:
    """GAE return computation (worker-side)."""
    T = len(ep.rewards)
    returns = [0.0] * T
    advs    = [0.0] * T
    gae     = 0.0
    truncated = (not ep.arrived) and bool(ep.values)
    next_val  = float(ep.values[-1]) if truncated else 0.0
    for t in reversed(range(T)):
        delta      = ep.rewards[t] + gamma * next_val - ep.values[t]
        gae        = delta + gamma * gae_lambda * gae
        returns[t] = gae + ep.values[t]
        advs[t]    = gae
        next_val   = ep.values[t]
    return returns, advs


def _wcollect(model, task: "V2XTask", n_ep: int, icm, cfg: dict,
              _reuse_envs: list = None) -> tuple:
    """
    Collect n_ep episodes in a worker subprocess.
    Returns (episodes, icm_triples).
    """
    import torch, random as _rnd
    from torch_geometric.data import Batch as _Batch
    from .universal_v2x_env import UniversalV2XEnv

    device      = _W.device
    use_amp     = _W.use_amp
    randomizer  = _W.randomizer
    max_steps   = cfg["max_episode_steps"]

    model.eval()
    if _reuse_envs is not None:
        envs = _reuse_envs[:n_ep]
    else:
        envs = [UniversalV2XEnv(randomizer, channel_lookup=None,
                                max_steps=max_steps, use_icm=cfg["use_icm"])
                for _ in range(n_ep)]
    episodes = [_Episode() for _ in range(n_ep)]
    obs_list = [None] * n_ep
    prev_ctx = [[0.0] * 6] * n_ep
    steps    = [0] * n_ep
    last_inf = [{} for _ in range(n_ep)]

    for i, (env, ep) in enumerate(zip(envs, episodes)):
        obs, info = env.reset(task=task, resample_od=True)
        obs_list[i] = obs
        start = info.get("origin")
        if start:
            ep.node_ids_visited.append(start)

    active = list(range(n_ep))
    _amp   = (torch.autocast(device_type="cuda", dtype=torch.bfloat16)
              if use_amp and device.type == "cuda"
              else torch.autocast(device_type="cpu", enabled=False))

    while active:
        vi, vo, vmt, vml = [], [], [], []
        for i in active:
            ml = envs[i].action_masks()
            mt = torch.tensor(ml, dtype=torch.bool, device=device)
            if not mt.any():
                last_inf[i] = {}
                continue
            vi.append(i)
            raw = obs_list[i]
            vo.append(raw.to(device, non_blocking=True) if hasattr(raw, "to") else raw)
            vmt.append(mt); vml.append(ml)

        if not vi:
            break

        batch    = _Batch.from_data_list(vo)
        stacked  = torch.stack(vmt)
        with _amp:
            logits_b, vals_b = model(batch, action_mask=stacked)

        if torch.isnan(logits_b.float()).any():
            break

        dists   = torch.distributions.Categorical(logits=logits_b)
        actions = dists.sample()
        lp      = dists.log_prob(actions)

        # Parallel env.step — numpy inside env releases GIL → real thread parallelism
        step_args = [(i, envs[i], actions[j].item()) for j, i in enumerate(vi)]
        if _W.thread_pool is not None and len(step_args) > 1:
            step_map = dict(_W.thread_pool.map(_env_step, step_args))
        else:
            step_map = {i: envs[i].step(act) for i, _, act in step_args}

        new_active = []
        for j, i in enumerate(vi):
            ep    = episodes[i]
            act_v = actions[j].item()
            ep.global_ctxs.append(list(prev_ctx[i]))
            obs_new, rew, term, trunc, info = step_map[i]
            done = term or trunc
            steps[i] += 1
            last_inf[i] = info
            prev_ctx[i] = info.get("global_ctx", [0.0] * 6)
            ep.obs_list.append(obs_new);   ep.actions.append(act_v)
            ep.rewards.append(rew);        ep.log_probs.append(lp[j].item())
            ep.values.append(vals_b[j].item()); ep.masks.append(vml[j])
            ep.total_reward   += rew
            ep.avg_latency_ms += info.get("latency_ms", 0.0)
            ep.avg_prr        += info.get("prr", 0.0)
            ep.avg_aoi_norm   += info.get("aoi_normalized", 0.0)
            ep.handover_count  = info.get("handover_count", 0)
            nid = info.get("node_id")
            if nid:
                ep.node_ids_visited.append(nid)
            if not done and steps[i] < max_steps:
                obs_list[i] = obs_new; new_active.append(i)
        active = new_active

    model.train()

    result      = []
    icm_triples = []
    for i, (ep, env) in enumerate(zip(episodes, envs)):
        inf = last_inf[i]
        ep.arrived = inf.get("arrived", False)
        ep.n_steps = steps[i]
        n = max(len(ep.obs_list), 1)
        ep.avg_latency_ms /= n; ep.avg_prr /= n; ep.avg_aoi_norm /= n

        if icm is not None and len(ep.global_ctxs) >= 2:
            ct  = torch.tensor(ep.global_ctxs[:-1], dtype=torch.float32, device=device)
            ct1 = torch.tensor(ep.global_ctxs[1:],  dtype=torch.float32, device=device)
            ats = torch.tensor(ep.actions[:len(ep.global_ctxs) - 1],
                               dtype=torch.long, device=device)
            r_i = icm.compute_intrinsic_rewards(ct, ats, ct1).cpu().tolist()
            mu  = sum(r_i) / max(len(r_i), 1)
            sig = (sum((x - mu) ** 2 for x in r_i) / max(len(r_i) - 1, 1)) ** 0.5
            for s, ri in enumerate(r_i):
                if s < len(ep.rewards):
                    ep.rewards[s] += cfg["icm_reward_scale"] * (ri - mu) / (sig + 1e-8)
            n_p = min(len(ep.global_ctxs) - 1, len(ep.actions))
            icm_triples.extend(
                (ep.global_ctxs[s], ep.global_ctxs[s + 1], ep.actions[s])
                for s in range(n_p)
            )

        if (cfg["use_her"] and not ep.arrived and len(ep.node_ids_visited) >= 3
                and _rnd.random() < cfg["her_ratio"]):
            relabeled, h_goal = env.her_relabel_rewards(
                visited_nodes=ep.node_ids_visited, rewards=list(ep.rewards),
                initial_dist=env._initial_dist, min_od_m=cfg["her_min_od_m"],
            )
            if h_goal is not None:
                result.append(_Episode(
                    obs_list=ep.obs_list, actions=ep.actions, rewards=relabeled,
                    log_probs=ep.log_probs, values=ep.values, masks=ep.masks,
                    arrived=True, total_reward=sum(relabeled),
                    avg_latency_ms=ep.avg_latency_ms, avg_prr=ep.avg_prr,
                    avg_aoi_norm=ep.avg_aoi_norm, handover_count=ep.handover_count,
                    n_steps=ep.n_steps, global_ctxs=ep.global_ctxs,
                    node_ids_visited=ep.node_ids_visited,
                ))
        result.append(ep)

    return result, icm_triples


def _wppo(model, episodes: list, cfg: dict, current_iter: int):
    """PPO surrogate loss (worker-side, standalone)."""
    import torch
    from torch_geometric.data import Batch as _Batch

    device  = _W.device
    use_amp = _W.use_amp
    gamma   = cfg["gamma"]; lam = cfg["gae_lambda"]

    all_obs, all_acts, all_lps = [], [], []
    all_rets, all_advs, all_ms = [], [], []
    for ep in episodes:
        if not ep.obs_list:
            continue
        rets, advs = _wgae(ep, gamma, lam)
        for obs, act, lp, ret, adv, ml in zip(
            ep.obs_list, ep.actions, ep.log_probs, rets, advs, ep.masks
        ):
            all_obs.append(obs); all_acts.append(act); all_lps.append(lp)
            all_rets.append(ret); all_advs.append(adv); all_ms.append(ml)

    if not all_obs:
        return torch.tensor(0.0, device=device, requires_grad=True)

    mini_bsz = cfg.get("ppo_mini_bsz", 0)
    if mini_bsz > 0 and len(all_obs) > mini_bsz:
        import random as _rand
        idx = sorted(_rand.sample(range(len(all_obs)), mini_bsz))
        all_obs   = [all_obs[i]   for i in idx]
        all_acts  = [all_acts[i]  for i in idx]
        all_lps   = [all_lps[i]   for i in idx]
        all_rets  = [all_rets[i]  for i in idx]
        all_advs  = [all_advs[i]  for i in idx]
        all_ms    = [all_ms[i]    for i in idx]

    obs_d = []
    for o in all_obs:
        d = o.to(device, non_blocking=True) if hasattr(o, "to") else o
        if hasattr(d, "node_ids"):
            del d.node_ids
        obs_d.append(d)
    batch   = _Batch.from_data_list(obs_d)
    acts_t  = torch.tensor(all_acts, dtype=torch.long,    device=device)
    lps_t   = torch.tensor(all_lps,  dtype=torch.float32, device=device)
    rets_t  = torch.tensor(all_rets, dtype=torch.float32, device=device)
    advs_t  = torch.tensor(all_advs, dtype=torch.float32, device=device)
    masks_t = torch.tensor(all_ms,   dtype=torch.bool,    device=device)

    if advs_t.numel() > 1:
        advs_t = (advs_t - advs_t.mean()) / (advs_t.std() + 1e-8)
    rets_n = ((rets_t - rets_t.mean()) / (rets_t.std() + 1e-8)
              if rets_t.numel() > 1 else rets_t)

    _amp = (torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if use_amp and device.type == "cuda"
            else torch.autocast(device_type="cpu", enabled=False))
    with _amp:
        lp_new, v_new, ent = model.evaluate_actions(batch, acts_t, masks_t)

    lp_new = lp_new.float(); v_new = v_new.float(); ent = ent.float()
    ratio   = torch.exp(lp_new - lps_t)
    clipped = torch.clamp(ratio, 1 - cfg["ppo_clip_eps"], 1 + cfg["ppo_clip_eps"])
    pi_loss = -torch.min(ratio * advs_t, clipped * advs_t).mean()
    vf_loss = (v_new - rets_n).pow(2).mean()

    d_iters = cfg["ppo_entropy_coef_decay_iters"]
    if d_iters > 0:
        alpha = min(current_iter / d_iters, 1.0)
        c0 = cfg["ppo_entropy_coef_start"] * (0.4 if cfg["use_icm"] else 1.0)
        c1 = cfg["ppo_entropy_coef_end"]   * (0.6 if cfg["use_icm"] else 1.0)
        ec = (1.0 - alpha) * c0 + alpha * c1
    else:
        ec = cfg["ppo_entropy_coef"]

    return pi_loss + cfg["ppo_value_coef"] * vf_loss - ec * ent.mean()


def _wtask(payload: dict) -> dict:
    """
    Reptile per-task function (runs in subprocess).

    Reads meta-weights from shared memory (zero-copy).
    Runs K inner SGD steps on support set only (no query collection).
    Returns (φ' − θ) diffs for Reptile meta-update.
    """
    import torch, torch.optim as optim, torch.nn as nn, traceback as _tb

    try:
        task     = payload["task"]
        cfg      = payload["cfg"]
        icm_sd   = payload.get("icm_sd")
        cur_iter = payload["current_iter"]
        n_tasks  = payload["n_tasks"]
        device   = _W.device

        # Load meta-weights from pickle payload; move to worker device.
        sd = {k: v.to(device) for k, v in payload["weights_sd"].items()}
        _W.model.load_state_dict(sd, strict=True)
        fast_model = _W.model

        # Save θ₀ for Reptile diff: (φ' − θ₀)
        theta_0 = [p.detach().clone() for p in fast_model.parameters()]

        # ICM snapshot (eval, no backward in subprocess)
        icm = None
        if cfg["use_icm"] and icm_sd is not None:
            from .universal_gnn_policy import IntrinsicCuriosityModule
            if _W.icm is None:
                _W.icm = IntrinsicCuriosityModule(hidden=cfg["icm_hidden"]).to(device)
            _W.icm.load_state_dict({k: v.to(device) for k, v in icm_sd.items()})
            _W.icm.eval()
            icm = _W.icm

        inner_opt   = optim.SGD(fast_model.parameters(), lr=cfg["inner_lr"])
        all_triples = []

        # Persistent env pool — envs are generic, task loaded via reset().
        from .universal_v2x_env import UniversalV2XEnv as _VEnv
        _n_envs = cfg["support_episodes"]
        if _W.envs is None or len(_W.envs) < _n_envs:
            _W.envs = [
                _VEnv(_W.randomizer, channel_lookup=None,
                      max_steps=cfg["max_episode_steps"], use_icm=cfg["use_icm"])
                for _ in range(_n_envs)
            ]
        _reuse_envs = _W.envs

        # Reptile inner loop: K steps on support set (NO separate query collection)
        last_eps  = []
        last_loss = torch.tensor(0.0, device=device)

        if cfg.get("collect_once", False):
            # collect_once: single episode collection, K×E PPO epochs on reused batch.
            # Saves (K-1)/K collection overhead — 3× fewer env.step calls with K=3.
            # PPO clip handles distribution shift; Reptile diff is still φ'_final − θ₀.
            eps, trips = _wcollect(fast_model, task, cfg["support_episodes"], icm, cfg,
                                   _reuse_envs=_reuse_envs)
            all_triples.extend(trips)
            last_eps = eps
            total_epochs = cfg["inner_steps"] * cfg["ppo_epochs"]
            for _e in range(total_epochs):
                loss = _wppo(fast_model, eps, cfg, cur_iter)
                if torch.isnan(loss):
                    break
                last_loss = loss
                inner_opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(fast_model.parameters(),
                                         max_norm=cfg["inner_grad_clip"])
                inner_opt.step()
        else:
            for _s in range(cfg["inner_steps"]):
                eps, trips = _wcollect(fast_model, task, cfg["support_episodes"], icm, cfg,
                                       _reuse_envs=_reuse_envs)
                all_triples.extend(trips)
                last_eps = eps
                for _e in range(cfg["ppo_epochs"]):
                    loss = _wppo(fast_model, eps, cfg, cur_iter)
                    if torch.isnan(loss):
                        break
                    last_loss = loss
                    inner_opt.zero_grad()
                    loss.backward()
                    nn.utils.clip_grad_norm_(fast_model.parameters(),
                                             max_norm=cfg["inner_grad_clip"])
                    inner_opt.step()

        # Reptile: diffs = (φ' − θ₀) / n_tasks (averaged across tasks in main)
        diffs = [
            (i, (p.detach().cpu() - t0.cpu()) / n_tasks)
            for i, (p, t0) in enumerate(zip(fast_model.parameters(), theta_0))
        ]

        n_ep     = max(len(last_eps), 1)
        mean_rew = sum(ep.total_reward / max(len(ep.rewards), 1)
                       for ep in last_eps) / n_ep
        arrival  = sum(1.0 for ep in last_eps if ep.arrived) / n_ep
        ml_val   = float(last_loss.item()) if not torch.isnan(last_loss) else 0.0

        return {
            "diffs":       diffs,
            "stats":       (ml_val, mean_rew, arrival),
            "icm_triples": all_triples,
            "ok":          True,
        }
    except Exception:
        return {
            "diffs": None, "stats": (0.0, 0.0, 0.0),
            "icm_triples": [], "ok": False,
            "err": _tb.format_exc(),
        }
    finally:
        if _W.device.type == "cuda":
            torch.cuda.empty_cache()


class MAMLTrainer:
    """
    Reptile meta-RL trainer for UniversalGNNPolicy.

    Usage (on A100):
        randomizer = DomainRandomizer(train=True, seed=42)
        trainer = MAMLTrainer(randomizer, config=MAMLConfig())
        trainer.train()
    """

    def __init__(
        self,
        randomizer: DomainRandomizer,
        config: Optional[MAMLConfig] = None,
        channel_lookup=None,
    ) -> None:
        if not _TORCH_GEO_AVAILABLE:
            raise ImportError(
                "MAMLTrainer requires torch and torch_geometric. "
                "Run: pip install torch torch-geometric"
            )
        self.cfg = config or MAMLConfig()
        self._randomizer = randomizer
        self._channel_lookup = channel_lookup
        self._device = torch.device(self.cfg.device)

        self._holdout_randomizer = DomainRandomizer(train=False, seed=self.cfg.holdout_seed)
        self._return_stats = _RunningStats()

        self._model = UniversalGNNPolicy().to(self._device)
        _can_compile = (
            self.cfg.compile_model
            and hasattr(torch, "compile")
            and self._device.type not in ("mps",)
        )
        if _can_compile:
            self._model = torch.compile(self._model, dynamic=True)
        elif self.cfg.compile_model and self._device.type == "mps":
            print("[MAML] torch.compile 비활성화 (MPS 미지원)")

        self._meta_opt = optim.Adam(self._model.parameters(), lr=self.cfg.meta_lr, foreach=False)
        self._scheduler = self._build_scheduler()

        if self.cfg.use_icm:
            self._icm = IntrinsicCuriosityModule(hidden=self.cfg.icm_hidden).to(self._device)
            self._icm_opt = optim.Adam(self._icm.parameters(), lr=self.cfg.icm_lr, foreach=False)
            self._icm_reward_stats = _RunningStats()
        else:
            self._icm = None
            self._icm_opt = None
            self._icm_reward_stats = None
        self._icm_stats_lock    = threading.Lock()
        self._return_stats_lock = threading.Lock()

        self._save_dir = Path(self.cfg.save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._log: list[dict] = []
        self._iter = 0

        self._use_amp = self.cfg.use_amp and self._device.type == "cuda"
        if self._use_amp:
            print("[MAML] BF16 AMP enabled — A100 Tensor Core 활성화")

        self._bc_applied: bool = False

        # shared_memory removed — POSIX semaphore contention caused futex deadlock
        # across spawned workers. Weights are now pickled per-task in _meta_step().
        import torch.multiprocessing as _tmp
        _mp_ctx = _tmp.get_context("spawn")
        self._proc_pool = ProcessPoolExecutor(
            max_workers=self.cfg.n_workers,
            mp_context=_mp_ctx,
            initializer=_worker_init,
            initargs=(
                self._use_amp,
                42,                     # seed_base
                self.cfg.icm_hidden,
            ),
        )
        print(f"[MAML] Reptile ProcessPool: {self.cfg.n_workers} workers (spawn, pickle)")

    # ── BC warm-start helper ───────────────────────────────────────────────────
    def bc_apply(self, bc_result: dict) -> None:
        overrides = bc_result.get("maml_overrides", {})
        if "ppo_entropy_coef_start" in overrides:
            old = self.cfg.ppo_entropy_coef_start
            self.cfg.ppo_entropy_coef_start = overrides["ppo_entropy_coef_start"]
            print(
                f"[MAML] BC applied: ppo_entropy_coef_start {old:.3f} → "
                f"{self.cfg.ppo_entropy_coef_start:.3f}"
            )
        self._bc_applied = True

    # ── Main training loop ─────────────────────────────────────────────────────
    def train(self, resume_from: Optional[str] = None) -> None:
        if resume_from:
            self._load_checkpoint(resume_from)

        print(f"[MAML] Starting Reptile training on {self._device}")
        print(f"       {self.cfg.n_tasks_per_iter} tasks/iter × {self.cfg.meta_iterations} iters")
        print(f"       AMP={self._use_amp}  workers={self.cfg.n_workers}  "
              f"inner_steps={self.cfg.inner_steps}  support_eps={self.cfg.support_episodes}")
        t_start = time.time()

        if self._iter == 0:
            self._save_checkpoint("init")

        for it in range(self._iter, self.cfg.meta_iterations):
            self._iter = it

            stage = self._curriculum_stage(it)
            max_nodes, max_od_dist, speed_range = self._curriculum_params(stage)
            tasks = [
                self._randomizer.sample_task(
                    max_nodes=max_nodes,
                    max_od_dist_m=max_od_dist,
                    speed_range=speed_range,
                )
                for _ in range(self.cfg.n_tasks_per_iter)
            ]
            tasks = [t for t in tasks if t is not None]
            if not tasks:
                continue

            meta_diffs, meta_loss, mean_rew, mean_arrival = self._meta_step(tasks, stage, it)

            # Reptile outer update via Adam:
            # diffs = (φ' − θ); set grad = −diff so Adam moves θ toward φ'
            if any(d is not None for d in meta_diffs):
                self._meta_opt.zero_grad()
                for p, d in zip(self._model.parameters(), meta_diffs):
                    if d is not None:
                        p.grad = -d.to(self._device)
                torch.nn.utils.clip_grad_norm_(
                    self._model.parameters(), max_norm=self.cfg.meta_grad_clip
                )
                self._meta_opt.step()
                if self._scheduler:
                    self._scheduler.step()

            holdout_stats: dict = {}
            if (it + 1) % self.cfg.holdout_eval_every == 0:
                holdout_stats = self._evaluate_holdout()

            if it % self.cfg.log_every == 0:
                elapsed = time.time() - t_start
                self._log_iter(it, tasks, elapsed, meta_loss, mean_rew, mean_arrival,
                               holdout_stats)

            if (it + 1) % self.cfg.save_every == 0:
                self._save_checkpoint(it + 1)

        self._save_checkpoint("final")
        print(f"[MAML] Training complete — {time.time()-t_start:.0f}s total")

    # ── Reptile meta-step ──────────────────────────────────────────────────────
    def _meta_step(
        self,
        tasks: list[V2XTask],
        curriculum_stage: int,
        current_iter: int = 0,
    ) -> tuple[list, float, float, float]:
        """
        Reptile inner loop via ProcessPoolExecutor.

        Sync current meta-weights to shared memory (zero-copy; no pickle).
        All tasks run in parallel; each returns (φ' − θ) diffs.
        """
        n_params    = len(list(self._model.parameters()))
        accumulated: list[Optional["torch.Tensor"]] = [None] * n_params
        task_stats: list[tuple[float, float, float]] = []
        icm_triples: list[tuple] = []
        n_tasks = len(tasks)

        # Snapshot current meta-weights for pickle-based IPC (replaces shared memory).
        _src = self._model._orig_mod if hasattr(self._model, "_orig_mod") else self._model
        weights_sd = {k: v.detach().cpu() for k, v in _src.state_dict().items()}

        # ICM snapshot
        icm_sd = None
        if self._icm is not None:
            icm_sd = {k: v.detach().cpu() for k, v in self._icm.state_dict().items()}

        cfg_d = {
            "inner_lr":                     self.cfg.inner_lr,
            "inner_steps":                  self.cfg.inner_steps,
            "inner_grad_clip":              1.0,
            "support_episodes":             self.cfg.support_episodes,
            "ppo_epochs":                   self.cfg.ppo_epochs,
            "ppo_clip_eps":                 self.cfg.ppo_clip_eps,
            "ppo_value_coef":               self.cfg.ppo_value_coef,
            "ppo_entropy_coef":             self.cfg.ppo_entropy_coef,
            "ppo_entropy_coef_start":       self.cfg.ppo_entropy_coef_start,
            "ppo_entropy_coef_end":         self.cfg.ppo_entropy_coef_end,
            "ppo_entropy_coef_decay_iters": self.cfg.ppo_entropy_coef_decay_iters,
            "gamma":                        self.cfg.gamma,
            "gae_lambda":                   self.cfg.gae_lambda,
            "max_episode_steps":            self.cfg.max_episode_steps,
            "use_icm":                      self.cfg.use_icm,
            "icm_hidden":                   self.cfg.icm_hidden,
            "icm_reward_scale":             self.cfg.icm_reward_scale,
            "use_her":                      self.cfg.use_her,
            "her_ratio":                    self.cfg.her_ratio,
            "her_min_od_m":                 self.cfg.her_min_od_m,
            "collect_once":                 self.cfg.collect_once,
            "ppo_mini_bsz":                 self.cfg.ppo_mini_bsz,
        }

        # Submit all tasks; weights_sd pickled per-task (replaces shared memory)
        futures = [
            self._proc_pool.submit(
                _wtask,
                {"task": t, "cfg": cfg_d, "weights_sd": weights_sd,
                 "icm_sd": icm_sd, "current_iter": current_iter, "n_tasks": n_tasks}
            )
            for t in tasks
        ]
        _task_timeout = cfg_d.get("max_episode_steps", 100) * 10 + 120  # generous upper bound
        results = []
        _pool_broken = False
        for f in futures:
            try:
                results.append(f.result(timeout=_task_timeout))
            except Exception as e:
                _log.warning("[MAML] worker task failed/timeout (%s): %s",
                             type(e).__name__, str(e)[:200])
                results.append({"ok": False, "err": str(e), "diffs": [],
                                 "reward": 0.0, "arrival": 0.0, "n_trips": 0})
                if "BrokenProcessPool" in type(e).__name__ or "BrokenPool" in str(e):
                    _pool_broken = True
        if _pool_broken:
            _log.warning("[MAML] ProcessPool broken — recreating worker pool")
            try:
                self._proc_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            import torch.multiprocessing as _tmp2
            _mp_ctx2 = _tmp2.get_context("spawn")
            self._proc_pool = ProcessPoolExecutor(
                max_workers=self.cfg.n_workers,
                mp_context=_mp_ctx2,
                initializer=_worker_init,
                initargs=(self._use_amp, 42, self.cfg.icm_hidden),
            )

        for res in results:
            if not res["ok"]:
                _log.error("[MAML] worker error:\n%s", res.get("err", ""))
                continue
            if res["diffs"]:
                for i, d in res["diffs"]:
                    accumulated[i] = d if accumulated[i] is None else accumulated[i] + d
            task_stats.append(res["stats"])
            icm_triples.extend(res["icm_triples"])

        # ICM training on main thread
        if self._icm is not None and self._icm_opt is not None and icm_triples:
            ctxs_t  = [t[0] for t in icm_triples]
            ctxs_t1 = [t[1] for t in icm_triples]
            acts    = [t[2] for t in icm_triples]
            ct_th  = torch.tensor(ctxs_t,  dtype=torch.float32, device=self._device)
            ct1_th = torch.tensor(ctxs_t1, dtype=torch.float32, device=self._device)
            at_th  = torch.tensor(acts,    dtype=torch.long,    device=self._device)
            _aicm = (torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                     if self._use_amp else torch.autocast(device_type="cpu", enabled=False))
            with _aicm:
                fwd_loss, inv_loss, _ = self._icm(ct_th, at_th, ct1_th)
            icm_loss = (self.cfg.icm_beta * fwd_loss.float()
                        + (1.0 - self.cfg.icm_beta) * inv_loss.float())
            if not (torch.isnan(icm_loss) or torch.isinf(icm_loss)):
                self._icm_opt.zero_grad()
                icm_loss.backward()
                if any(p.grad is not None for p in self._icm.parameters()):
                    self._icm_opt.step()

        mean_loss    = sum(s[0] for s in task_stats) / max(len(task_stats), 1)
        mean_rew     = sum(s[1] for s in task_stats) / max(len(task_stats), 1)
        mean_arrival = sum(s[2] for s in task_stats) / max(len(task_stats), 1)
        return accumulated, mean_loss, mean_rew, mean_arrival

    def __del__(self) -> None:
        try:
            self._proc_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # ── Vectorized episode collection (main-process, used by holdout eval) ─────
    @torch.no_grad()
    def _collect_episodes_vec(
        self,
        model: "UniversalGNNPolicy",
        task: "V2XTask",
        n_episodes: int,
    ) -> list[_Episode]:
        from torch_geometric.data import Batch
        import random as _random

        model.eval()
        max_steps = self.cfg.max_episode_steps

        envs = [
            UniversalV2XEnv(
                self._randomizer,
                channel_lookup=self._channel_lookup,
                max_steps=max_steps,
                use_icm=self.cfg.use_icm,
            )
            for _ in range(n_episodes)
        ]

        episodes    = [_Episode() for _ in range(n_episodes)]
        obs_list    = [None] * n_episodes
        prev_ctxs   = [[0.0] * 6] * n_episodes
        step_counts = [0] * n_episodes
        last_infos  = [{} for _ in range(n_episodes)]

        for i, (env, ep) in enumerate(zip(envs, episodes)):
            obs, info = env.reset(task=task, resample_od=True)
            obs_list[i] = obs
            start = info.get("origin")
            if start:
                ep.node_ids_visited.append(start)

        active = list(range(n_episodes))

        _amp_ctx = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if self._use_amp else torch.autocast(device_type="cpu", enabled=False)
        )

        while active:
            valid_i, valid_obs_gpu, valid_masks = [], [], []
            for i in active:
                mask_list = envs[i].action_masks()
                mask = torch.tensor(mask_list, dtype=torch.bool, device=self._device)
                if not mask.any():
                    last_infos[i] = {}
                    continue
                valid_i.append(i)
                raw = obs_list[i]
                valid_obs_gpu.append(
                    raw.to(self._device, non_blocking=True) if hasattr(raw, "to") else raw
                )
                valid_masks.append(mask)

            if not valid_i:
                break

            batch = Batch.from_data_list(valid_obs_gpu)
            stacked_masks = torch.stack(valid_masks)
            with _amp_ctx:
                logits_batch, values_batch = model(batch, action_mask=stacked_masks)

            if torch.isnan(logits_batch.float()).any():
                break

            dists    = torch.distributions.Categorical(logits=logits_batch)
            actions  = dists.sample()
            lp       = dists.log_prob(actions)

            new_active = []
            for j, i in enumerate(valid_i):
                ep = episodes[i]
                ep.global_ctxs.append(list(prev_ctxs[i]))

                action_val = actions[j].item()
                obs_new, reward, terminated, truncated, info = envs[i].step(action_val)
                done = terminated or truncated
                step_counts[i] += 1
                last_infos[i] = info

                ctx_t1 = info.get("global_ctx", [0.0] * 6)
                prev_ctxs[i] = ctx_t1

                ep.obs_list.append(obs_new)
                ep.actions.append(action_val)
                ep.rewards.append(reward)
                ep.log_probs.append(lp[j].item())
                ep.values.append(values_batch[j].item())
                ep.masks.append(valid_masks[j].cpu().tolist())
                ep.total_reward   += reward
                ep.avg_latency_ms += info.get("latency_ms", 0.0)
                ep.avg_prr        += info.get("prr", 0.0)
                ep.avg_aoi_norm   += info.get("aoi_normalized", 0.0)
                ep.handover_count  = info.get("handover_count", 0)

                node_id = info.get("node_id")
                if node_id:
                    ep.node_ids_visited.append(node_id)

                if not done and step_counts[i] < max_steps:
                    obs_list[i] = obs_new
                    new_active.append(i)
                else:
                    obs_list[i] = None

            active = new_active

        model.train()

        result: list[_Episode] = []
        for i, (ep, env) in enumerate(zip(episodes, envs)):
            info = last_infos[i]
            ep.arrived = info.get("arrived", False)
            ep.n_steps = step_counts[i]
            if ep.obs_list:
                n = len(ep.obs_list)
                ep.avg_latency_ms /= n
                ep.avg_prr        /= n
                ep.avg_aoi_norm   /= n

            if self._icm is not None and len(ep.global_ctxs) >= 2:
                ctxs_t  = ep.global_ctxs[:-1]
                ctxs_t1 = ep.global_ctxs[1:]
                acts    = ep.actions[:len(ctxs_t)]
                ctx_t_th  = torch.tensor(ctxs_t,  dtype=torch.float32, device=self._device)
                ctx_t1_th = torch.tensor(ctxs_t1, dtype=torch.float32, device=self._device)
                acts_th   = torch.tensor(acts,     dtype=torch.long,    device=self._device)
                r_i = self._icm.compute_intrinsic_rewards(ctx_t_th, acts_th, ctx_t1_th)
                with self._icm_stats_lock:
                    self._icm_reward_stats.update(r_i)
                    r_i_norm = self._icm_reward_stats.normalize(r_i).cpu().tolist()
                for s_i, ri in enumerate(r_i_norm):
                    if s_i < len(ep.rewards):
                        ep.rewards[s_i] += self.cfg.icm_reward_scale * float(ri)

            if (self.cfg.use_her
                    and not ep.arrived
                    and len(ep.node_ids_visited) >= 3
                    and _random.random() < self.cfg.her_ratio):
                relabeled, h_goal = env.her_relabel_rewards(
                    visited_nodes=ep.node_ids_visited,
                    rewards=list(ep.rewards),
                    initial_dist=env._initial_dist,
                    min_od_m=self.cfg.her_min_od_m,
                )
                if h_goal is not None:
                    her_ep = _Episode(
                        obs_list=ep.obs_list,
                        actions=ep.actions,
                        rewards=relabeled,
                        log_probs=ep.log_probs,
                        values=ep.values,
                        masks=ep.masks,
                        arrived=True,
                        total_reward=sum(relabeled),
                        avg_latency_ms=ep.avg_latency_ms,
                        avg_prr=ep.avg_prr,
                        avg_aoi_norm=ep.avg_aoi_norm,
                        handover_count=ep.handover_count,
                        n_steps=ep.n_steps,
                        global_ctxs=ep.global_ctxs,
                        node_ids_visited=ep.node_ids_visited,
                    )
                    result.append(her_ep)

            result.append(ep)

        return result

    # ── Sequential episode collection (kept for adapt() / CPU debug) ──────────
    @torch.no_grad()
    def _collect_episodes(
        self,
        model: "UniversalGNNPolicy",
        env: "UniversalV2XEnv",
        task: "V2XTask",
        n_episodes: int,
    ) -> list[_Episode]:
        model.eval()
        max_steps = self.cfg.max_episode_steps
        import random as _random

        episodes = []
        for _ in range(n_episodes):
            ep   = _Episode()
            obs, reset_info = env.reset(task=task, resample_od=True)
            done = False
            step = 0
            info = {}

            start_node = reset_info.get("origin")
            if start_node:
                ep.node_ids_visited.append(start_node)

            prev_ctx = [0.0] * 6

            while not done and step < max_steps:
                data = obs.to(self._device, non_blocking=True) if hasattr(obs, "to") else obs
                mask_list = env.action_masks()
                mask = torch.tensor(mask_list, dtype=torch.bool, device=self._device)

                if not mask.any():
                    break

                _amp_ctx = (
                    torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                    if self._use_amp else torch.autocast(device_type="cpu", enabled=False)
                )
                with _amp_ctx:
                    logits, value = model(data, action_mask=mask)
                if torch.isnan(logits.float()).any():
                    break

                dist     = torch.distributions.Categorical(logits=logits)
                action   = dist.sample()
                log_prob = dist.log_prob(action)

                ep.global_ctxs.append(list(prev_ctx))

                obs, reward, terminated, truncated, info = env.step(action.item())
                done = terminated or truncated
                step += 1

                ctx_t1 = info.get("global_ctx", [0.0] * 6)
                prev_ctx = ctx_t1

                ep.obs_list.append(obs)
                ep.actions.append(action.item())
                ep.rewards.append(reward)
                ep.log_probs.append(log_prob.item())
                ep.values.append(value.item() if hasattr(value, "item") else float(value))
                ep.masks.append(mask_list)
                ep.total_reward   += reward
                ep.avg_latency_ms += info.get("latency_ms", 0.0)
                ep.avg_prr        += info.get("prr", 0.0)
                ep.avg_aoi_norm   += info.get("aoi_normalized", 0.0)
                ep.handover_count  = info.get("handover_count", 0)

                node_id = info.get("node_id")
                if node_id:
                    ep.node_ids_visited.append(node_id)

            ep.arrived = info.get("arrived", False)
            ep.n_steps = step
            if ep.obs_list:
                n = len(ep.obs_list)
                ep.avg_latency_ms /= n
                ep.avg_prr        /= n
                ep.avg_aoi_norm   /= n

            if self._icm is not None and len(ep.global_ctxs) >= 2:
                ctxs_t  = ep.global_ctxs[:-1]
                ctxs_t1 = ep.global_ctxs[1:]
                acts    = ep.actions[:len(ctxs_t)]

                ctx_t_th  = torch.tensor(ctxs_t,  dtype=torch.float32, device=self._device)
                ctx_t1_th = torch.tensor(ctxs_t1, dtype=torch.float32, device=self._device)
                acts_th   = torch.tensor(acts,     dtype=torch.long,    device=self._device)

                r_i = self._icm.compute_intrinsic_rewards(ctx_t_th, acts_th, ctx_t1_th)
                with self._icm_stats_lock:
                    self._icm_reward_stats.update(r_i)
                    r_i_norm = self._icm_reward_stats.normalize(r_i).cpu().tolist()
                for s_i, ri in enumerate(r_i_norm):
                    if s_i < len(ep.rewards):
                        ep.rewards[s_i] += self.cfg.icm_reward_scale * float(ri)

            if (self.cfg.use_her
                    and not ep.arrived
                    and len(ep.node_ids_visited) >= 3
                    and _random.random() < self.cfg.her_ratio):
                relabeled, h_goal = env.her_relabel_rewards(
                    visited_nodes=ep.node_ids_visited,
                    rewards=list(ep.rewards),
                    initial_dist=env._initial_dist,
                    min_od_m=self.cfg.her_min_od_m,
                )
                if h_goal is not None:
                    her_ep = _Episode(
                        obs_list       = ep.obs_list,
                        actions        = ep.actions,
                        rewards        = relabeled,
                        log_probs      = ep.log_probs,
                        values         = ep.values,
                        masks          = ep.masks,
                        arrived        = True,
                        total_reward   = sum(relabeled),
                        avg_latency_ms = ep.avg_latency_ms,
                        avg_prr        = ep.avg_prr,
                        avg_aoi_norm   = ep.avg_aoi_norm,
                        handover_count = ep.handover_count,
                        n_steps        = ep.n_steps,
                        global_ctxs    = ep.global_ctxs,
                        node_ids_visited = ep.node_ids_visited,
                    )
                    episodes.append(her_ep)

            episodes.append(ep)

        model.train()
        return episodes

    # ── PPO loss (main-process, used by adapt/holdout) ────────────────────────
    def _ppo_loss(
        self,
        model: "UniversalGNNPolicy",
        episodes: list[_Episode],
        iteration: int = 0,
    ) -> "torch.Tensor":
        from torch_geometric.data import Batch as _Batch

        all_obs, all_actions, all_old_lps = [], [], []
        all_rets, all_advs, all_masks = [], [], []

        for ep in episodes:
            if not ep.obs_list:
                continue
            rets, advs = self._compute_gae_returns(ep)
            for obs, act, old_lp, ret, adv, mask_list in zip(
                ep.obs_list, ep.actions, ep.log_probs, rets, advs, ep.masks
            ):
                all_obs.append(obs)
                all_actions.append(act)
                all_old_lps.append(old_lp)
                all_rets.append(ret)
                all_advs.append(adv)
                all_masks.append(mask_list)

        if not all_obs:
            return torch.tensor(0.0, device=self._device, requires_grad=True)

        obs_dev = []
        for o in all_obs:
            d = o.to(self._device, non_blocking=True) if hasattr(o, "to") else o
            if hasattr(d, "node_ids"):
                del d.node_ids
            obs_dev.append(d)
        batch = _Batch.from_data_list(obs_dev)

        acts_t    = torch.tensor(all_actions, dtype=torch.long,    device=self._device)
        old_lps_t = torch.tensor(all_old_lps, dtype=torch.float32, device=self._device)
        rets_t    = torch.tensor(all_rets,    dtype=torch.float32, device=self._device)
        advs_t    = torch.tensor(all_advs,    dtype=torch.float32, device=self._device)
        masks_t   = torch.tensor(all_masks,   dtype=torch.bool,    device=self._device)

        if advs_t.numel() > 1:
            advs_t = (advs_t - advs_t.mean()) / (advs_t.std() + 1e-8)

        with self._return_stats_lock:
            self._return_stats.update(rets_t)
            rets_norm = self._return_stats.normalize(rets_t)

        _amp_ctx = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if self._use_amp else torch.autocast(device_type="cpu", enabled=False)
        )
        with _amp_ctx:
            log_probs_new, values_new, entropy = model.evaluate_actions(batch, acts_t, masks_t)

        log_probs_new = log_probs_new.float()
        values_new    = values_new.float()
        entropy       = entropy.float()

        ratio   = torch.exp(log_probs_new - old_lps_t)
        clipped = torch.clamp(ratio, 1 - self.cfg.ppo_clip_eps, 1 + self.cfg.ppo_clip_eps)
        policy_loss = -torch.min(ratio * advs_t, clipped * advs_t).mean()
        value_loss  = (values_new - rets_norm).pow(2).mean()

        if self.cfg.ppo_entropy_coef_decay_iters > 0:
            alpha = min(iteration / self.cfg.ppo_entropy_coef_decay_iters, 1.0)
            coef_start = self.cfg.ppo_entropy_coef_start * (0.4 if self.cfg.use_icm else 1.0)
            coef_end   = self.cfg.ppo_entropy_coef_end   * (0.6 if self.cfg.use_icm else 1.0)
            entropy_coef = (1.0 - alpha) * coef_start + alpha * coef_end
        else:
            entropy_coef = self.cfg.ppo_entropy_coef

        return (policy_loss
                + self.cfg.ppo_value_coef * value_loss
                - entropy_coef * entropy.mean())

    def _compute_gae_returns(
        self, ep: _Episode
    ) -> tuple[list[float], list[float]]:
        T = len(ep.rewards)
        returns = [0.0] * T
        advs    = [0.0] * T
        gae     = 0.0
        truncated = (not ep.arrived) and bool(ep.values)
        next_val = float(ep.values[-1]) if truncated else 0.0

        for t in reversed(range(T)):
            delta = ep.rewards[t] + self.cfg.gamma * next_val - ep.values[t]
            gae   = delta + self.cfg.gamma * self.cfg.gae_lambda * gae
            returns[t] = gae + ep.values[t]
            advs[t]    = gae
            next_val   = ep.values[t]

        return returns, advs

    # ── Curriculum ────────────────────────────────────────────────────────────
    def _curriculum_stage(self, iteration: int) -> int:
        warmup = self.cfg.curriculum_warmup_iters
        if iteration < warmup // 4:
            return 0
        if iteration < warmup // 2:
            return 1
        if iteration < warmup:
            return 2
        return 3

    def _curriculum_params(self, stage: int) -> tuple:
        if stage == 0:
            return 100, 500.0, (10.0, 60.0)
        if stage == 1:
            return 500, 3000.0, (10.0, 90.0)
        if stage == 2:
            return 2000, float("inf"), (10.0, 120.0)
        return 5000, float("inf"), (10.0, 130.0)

    # ── D8: Holdout evaluation ─────────────────────────────────────────────────
    @torch.no_grad()
    def _evaluate_holdout(
        self, n_tasks: int = 10, n_episodes: int = 4
    ) -> dict:
        src = self._model._orig_mod if hasattr(self._model, "_orig_mod") else self._model
        src.eval()

        arrivals: list[float] = []
        latencies: list[float] = []
        prrs:      list[float] = []
        aois:      list[float] = []

        env = UniversalV2XEnv(
            self._holdout_randomizer,
            channel_lookup=self._channel_lookup,
            max_steps=self.cfg.max_episode_steps,
            use_icm=self.cfg.use_icm,
        )
        for _ in range(n_tasks):
            task = self._holdout_randomizer.sample_task()
            if task is None:
                continue
            eps = self._collect_episodes(src, env, task, n_episodes)
            for ep in eps:
                arrivals.append(1.0 if ep.arrived else 0.0)
                latencies.append(ep.avg_latency_ms)
                prrs.append(ep.avg_prr)
                aois.append(ep.avg_aoi_norm)

        src.train()

        stats = {
            "holdout_arrival_rate":    round(sum(arrivals)  / max(len(arrivals),  1), 3),
            "holdout_avg_latency_ms":  round(sum(latencies) / max(len(latencies), 1), 2),
            "holdout_avg_prr":         round(sum(prrs)      / max(len(prrs),      1), 3),
            "holdout_avg_aoi_norm":    round(sum(aois)      / max(len(aois),      1), 4),
        }
        print(
            f"[MAML holdout] arrival={stats['holdout_arrival_rate']:.0%}"
            f"  lat={stats['holdout_avg_latency_ms']:.1f}ms"
            f"  PRR={stats['holdout_avg_prr']:.3f}"
        )
        return stats

    # ── Persistence ───────────────────────────────────────────────────────────
    def _save_checkpoint(self, tag) -> None:
        path = self._save_dir / f"maml_gnn_{tag}.pt"
        ckpt = {
            "iteration":  self._iter,
            "state_dict": (
                self._model._orig_mod.state_dict()
                if hasattr(self._model, "_orig_mod") else self._model.state_dict()
            ),
            "meta_opt":   self._meta_opt.state_dict(),
            "config":     vars(self.cfg),
            "log":        self._log[-100:],
            "return_stats": {
                "n": self._return_stats.n,
                "mean": self._return_stats.mean,
                "M2":  self._return_stats.M2,
            },
        }
        if self._icm is not None and self._icm_opt is not None:
            ckpt["icm_state_dict"] = self._icm.state_dict()
            ckpt["icm_opt"] = self._icm_opt.state_dict()
            ckpt["icm_reward_stats"] = {
                "n": self._icm_reward_stats.n,
                "mean": self._icm_reward_stats.mean,
                "M2":  self._icm_reward_stats.M2,
            }
        torch.save(ckpt, path)
        print(f"[MAML] Checkpoint saved → {path}")

    def _load_checkpoint(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self._device)
        self._iter = ckpt.get("iteration", 0)
        target = self._model._orig_mod if hasattr(self._model, "_orig_mod") else self._model
        target.load_state_dict(ckpt["state_dict"])
        self._meta_opt.load_state_dict(ckpt["meta_opt"])
        self._log = ckpt.get("log", [])
        rs = ckpt.get("return_stats", {})
        if rs:
            self._return_stats.n    = rs.get("n", 0)
            self._return_stats.mean = rs.get("mean", 0.0)
            self._return_stats.M2   = rs.get("M2", 0.0)
        if self._icm is not None and "icm_state_dict" in ckpt:
            self._icm.load_state_dict(ckpt["icm_state_dict"])
            if self._icm_opt is not None and "icm_opt" in ckpt:
                self._icm_opt.load_state_dict(ckpt["icm_opt"])
            irs = ckpt.get("icm_reward_stats", {})
            if irs and self._icm_reward_stats is not None:
                self._icm_reward_stats.n    = irs.get("n", 0)
                self._icm_reward_stats.mean = irs.get("mean", 0.0)
                self._icm_reward_stats.M2   = irs.get("M2", 0.0)

        print(f"[MAML] Resumed from {path} at iter {self._iter}")

    def _build_scheduler(self):
        if not _TORCH_GEO_AVAILABLE:
            return None
        mode = self.cfg.meta_lr_schedule
        if mode == "cosine":
            return optim.lr_scheduler.CosineAnnealingLR(
                self._meta_opt, T_max=self.cfg.meta_iterations
            )
        if mode == "linear":
            return optim.lr_scheduler.LinearLR(
                self._meta_opt, start_factor=1.0, end_factor=0.1,
                total_iters=self.cfg.meta_iterations,
            )
        return None

    # ── D9: V2X KPI logging ────────────────────────────────────────────────────
    def _log_iter(
        self,
        it: int,
        tasks: list,
        elapsed: float,
        meta_loss: float = 0.0,
        mean_rew: float = 0.0,
        mean_arrival: float = 0.0,
        holdout_stats: Optional[dict] = None,
    ) -> None:
        lr = self._meta_opt.param_groups[0]["lr"]
        stage = self._curriculum_stage(it)

        entry = {
            "iter":           it,
            "curriculum_stage": stage,
            "n_tasks":        len(tasks),
            "lr":             round(lr, 6),
            "meta_loss":      round(meta_loss, 4),
            "mean_reward":    round(mean_rew, 3),
            "arrival_rate":   round(mean_arrival, 3),
            "elapsed_s":      round(elapsed, 1),
            "return_mean":    round(self._return_stats.mean, 3),
            "return_std":     round(self._return_stats.std, 3),
        }
        if holdout_stats:
            entry.update(holdout_stats)

        self._log.append(entry)
        ho_str = ""
        if holdout_stats:
            ho_str = f"  H-arr={holdout_stats.get('holdout_arrival_rate', 0):.0%}"
        print(
            f"[MAML iter {it:5d}|stg{stage}] tasks={len(tasks)}  lr={lr:.2e}"
            f"  loss={meta_loss:.4f}  rew={mean_rew:.3f}"
            f"  arr={mean_arrival:.0%}{ho_str}  {elapsed:.0f}s"
        )

    # ── Reptile adaptation (inference) ────────────────────────────────────────
    def adapt(
        self,
        task: V2XTask,
        n_adapt_episodes: int = 5,
    ) -> "UniversalGNNPolicy":
        """Fine-tune a copy of the meta-model on task (K Reptile inner steps)."""
        if not _TORCH_GEO_AVAILABLE:
            raise ImportError("torch_geometric required")

        adapted = copy.deepcopy(self._model)
        adapted.to(self._device)
        opt = optim.SGD(adapted.parameters(), lr=self.cfg.inner_lr)
        env = UniversalV2XEnv(self._randomizer, channel_lookup=self._channel_lookup)

        for _ in range(self.cfg.inner_steps):
            eps  = self._collect_episodes(adapted, env, task, n_adapt_episodes)
            loss = self._ppo_loss(adapted, eps, self._iter)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(adapted.parameters(), 0.5)
            opt.step()

        adapted.eval()
        return adapted
