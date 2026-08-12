"""
PEARL — Probabilistic Embeddings for Actor-critic Reinforcement Learning.

Rakelly et al., "Efficient Off-Policy Meta-Reinforcement Learning via
Probabilistic Context Variables", ICML 2019. arXiv:1903.08254.

PEARL vs MAML design choice
────────────────────────────
MAML adapts via inner-loop gradient steps: fast but requires 2nd-order grads
(or FOMAML approximation) and fixes the adaptation budget to K steps.

PEARL adapts via amortized inference: a learned encoder q_φ(z|τ) maps a
context trajectory τ to a latent task variable z in a single forward pass.
The critic (and only the critic, following the paper) is conditioned on z.

Advantages in the V2X setting:
  1. No inner-loop gradient steps → 2–4× faster per outer iteration
  2. z captures task structure (graph topology, BS density, traffic load)
     continuously, not just at adaptation checkpoints
  3. ICM and HER still work on the query episodes; z conditions only critic
  4. World Model imagination extends the effective context without extra env cost

Key equations
─────────────
  Context encoder:
    z ~ q_φ(z|τ) = N(μ_φ(τ), σ_φ(τ))
    τ = {(s_i, a_i, r_i, s'_i)}_{i=1}^{C}   C context transitions

  ELBO loss:
    L = E_τ_query[L_PPO(π, V(·,z))] + β · KL[q_φ(z|τ) ‖ N(0,I)]

  Critic (conditioned on z):
    V(s, z) = MLP([h_cur ‖ h_dest ‖ h_global ‖ ctx ‖ z])
    — implemented via UniversalGNNPolicy(z_dim=16).forward(obs, z=z)

  Actor (NOT conditioned on z, following paper §3):
    π(a|s) = Pointer(h_cur, {h_neighbors})
    — same as MAML; z is critic-only

Synergy with ICM + HER + World Model
─────────────────────────────────────
  ICM:      Applied to QUERY episodes (same as MAML). Context episodes
            are NOT augmented with intrinsic rewards (they should reflect
            TRUE task structure for accurate z inference).
  HER:      Applied only to QUERY episodes. Context must preserve real
            extrinsic rewards.
  WorldModel: Imagination rollouts serve as ADDITIONAL context by providing
              more (s, a, r, s') pairs for the task encoder, extending the
              effective context length without extra env interaction.
            WARNING: imagined transitions are lower-quality; weight them
            at 0.5 via `imagination_weight` before feeding to task encoder.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# Dimension constants (must match universal_gnn_policy.py)
GLOBAL_CTX_DIM = 6
MAX_CANDIDATES = 5


try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import torch.nn.functional as F
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False
    torch = None  # type: ignore
    nn    = None  # type: ignore


# ── Task Encoder q_φ(z|τ) ──────────────────────────────────────────────────────

if _TORCH_AVAILABLE:
    class TaskEncoder(nn.Module):
        """
        Amortized task inference network.

        Input per transition: (ctx_t [6], a_onehot [5], reward [1], ctx_t1 [6])
          → 18-dim transition embedding

        Architecture:
          1. Linear projection: 18 → hidden (per-transition embedding)
          2. Mean pooling over C transitions (order-invariant, following PEARL §3)
          3. Linear head: hidden → z_dim * 2 (mean and log-var)

        Note: PEARL uses an LSTM in the original, but mean pooling is faster
        and performs equivalently on small context sets (Rakelly et al., §B.1).
        We offer a GRU variant when use_gru=True for order-sensitive context.

        Reparameterization trick:
          z = μ + ε · σ,  ε ~ N(0,I)
          Used during training (ELBO); at test time sample or use μ.
        """
        TRANS_DIM = GLOBAL_CTX_DIM + MAX_CANDIDATES + 1 + GLOBAL_CTX_DIM  # 18

        def __init__(
            self,
            z_dim:    int  = 16,
            hidden:   int  = 128,
            use_gru:  bool = False,
        ) -> None:
            super().__init__()
            self.z_dim   = z_dim
            self.hidden  = hidden
            self.use_gru = use_gru

            if use_gru:
                self.embed = nn.Linear(self.TRANS_DIM, hidden)
                self.gru   = nn.GRU(hidden, hidden, batch_first=True)
                self.head  = nn.Linear(hidden, z_dim * 2)
            else:
                # Mean-pooling encoder (PEARL default)
                self.embed = nn.Sequential(
                    nn.Linear(self.TRANS_DIM, hidden), nn.ReLU(),
                    nn.Linear(hidden, hidden),         nn.ReLU(),
                )
                self.head = nn.Linear(hidden, z_dim * 2)

        def forward(
            self,
            ctx_t:   "torch.Tensor",   # [B, C, 6]  or [C, 6]
            actions: "torch.Tensor",   # [B, C]     or [C] long
            rewards: "torch.Tensor",   # [B, C]     or [C]
            ctx_t1:  "torch.Tensor",   # [B, C, 6]  or [C, 6]
        ) -> Tuple["torch.Tensor", "torch.Tensor", "torch.Tensor"]:
            """
            Returns (z, mu, log_var).

            z is the reparameterized sample; mu and log_var for KL computation.
            All tensors: [B, z_dim] or [1, z_dim] if single task.
            """
            squeeze = ctx_t.dim() == 2
            if squeeze:
                ctx_t   = ctx_t.unsqueeze(0)
                actions = actions.unsqueeze(0)
                rewards = rewards.unsqueeze(0)
                ctx_t1  = ctx_t1.unsqueeze(0)

            B, C, _ = ctx_t.shape
            a_oh = F.one_hot(actions, num_classes=MAX_CANDIDATES).float()   # [B,C,5]
            r    = rewards.unsqueeze(-1)                                     # [B,C,1]
            x    = torch.cat([ctx_t, a_oh, r, ctx_t1], dim=-1)              # [B,C,18]

            if self.use_gru:
                emb  = F.relu(self.embed(x))          # [B,C,hidden]
                _, h = self.gru(emb)                  # h: [1,B,hidden]
                h    = h.squeeze(0)                   # [B,hidden]
                out  = self.head(h)
            else:
                emb = self.embed(x)                   # [B,C,hidden]
                agg = emb.mean(dim=1)                 # [B,hidden]  (mean pool)
                out = self.head(agg)                  # [B, z_dim*2]

            mu, log_var = out.chunk(2, dim=-1)
            # Clamp log_var for numerical stability
            log_var = log_var.clamp(-10.0, 2.0)
            sigma   = torch.exp(0.5 * log_var)
            z       = mu + sigma * torch.randn_like(sigma)

            if squeeze:
                return z.squeeze(0), mu.squeeze(0), log_var.squeeze(0)
            return z, mu, log_var

        @torch.no_grad()
        def infer_z(
            self,
            ctx_t:   "torch.Tensor",
            actions: "torch.Tensor",
            rewards: "torch.Tensor",
            ctx_t1:  "torch.Tensor",
            sample:  bool = False,
        ) -> "torch.Tensor":
            """Inference-mode z. Returns μ (deterministic) or a sample."""
            z, mu, _ = self.forward(ctx_t, actions, rewards, ctx_t1)
            return z if sample else mu


# ── Configuration ──────────────────────────────────────────────────────────────

@dataclass
class PEARLConfig:
    """PEARL training hyperparameters."""
    # Task encoder
    z_dim:            int   = 16       # latent task dimension
    encoder_hidden:   int   = 128
    use_gru:          bool  = False    # True → GRU encoder, False → mean-pool

    # ELBO / KL
    kl_weight:        float = 0.1     # β in L = L_PPO + β·KL
    kl_anneal_iters:  int   = 1000    # linearly ramp β from 0 → kl_weight

    # Context / Query
    context_steps:    int   = 32     # transitions to collect for task inference
    n_query_episodes: int   = 8      # episodes for PPO loss
    n_meta_tasks:     int   = 16     # tasks per outer iteration
    n_meta_iters:     int   = 5_000

    # PPO
    ppo_clip:         float = 0.2
    ppo_epochs:       int   = 4
    ppo_batch:        int   = 64
    ppo_value_coef:   float = 0.5
    ppo_entropy_coef_start: float = 0.02   # lower than MAML (BC warm-start)
    ppo_entropy_coef_end:   float = 0.003
    ppo_entropy_decay: int  = 5_000
    gae_gamma:        float = 0.99
    gae_lambda:       float = 0.95

    # Exploration
    use_icm:          bool  = True
    icm_reward_scale: float = 0.05
    use_her:          bool  = True
    her_ratio:        float = 0.4
    her_min_od_m:     float = 200.0

    # World Model imagination for context extension
    use_world_model:       bool  = True
    imagination_ctx_ratio: float = 0.5   # fraction of context from imagination
    imagination_weight:    float = 0.5   # down-weight imagined transitions

    # Optimizers
    lr_encoder:   float = 3e-4
    lr_policy:    float = 3e-4
    lr_outer:     float = 3e-4          # same optimizer for policy + encoder
    weight_decay: float = 1e-4
    grad_clip:    float = 10.0

    # Curriculum / Domain Randomization (matches MAMLConfig)
    curriculum_stage:       int   = 0
    curriculum_stage1_iter: int   = 500
    curriculum_stage2_iter: int   = 1500
    n_train_regions:        int   = 500
    n_holdout_regions:      int   = 50
    holdout_eval_interval:  int   = 50
    holdout_eval_tasks:     int   = 20

    # Checkpointing
    checkpoint_interval: int   = 100
    checkpoint_dir:      str   = "checkpoints/pearl_v4"

    # Device
    device: str = "auto"


# ── Running Statistics (Welford) ───────────────────────────────────────────────
# Duplicate from maml_trainer to keep pearl_trainer self-contained.

class _PearlRunningStats:
    """Welford online mean+variance for return normalization."""

    def __init__(self) -> None:
        self.n   = 0
        self.mu  = 0.0
        self.M2  = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta   = x - self.mu
        self.mu += delta / self.n
        self.M2 += delta * (x - self.mu)

    @property
    def std(self) -> float:
        return (self.M2 / max(self.n - 1, 1)) ** 0.5

    def normalize(self, x: float) -> float:
        return (x - self.mu) / max(self.std, 1e-8)


# ── Episode ────────────────────────────────────────────────────────────────────

@dataclass
class _PearlEpisode:
    """One collected episode in PEARL format."""
    observations:    list = field(default_factory=list)   # PyG Data objects
    actions:         list = field(default_factory=list)
    log_probs:       list = field(default_factory=list)
    values:          list = field(default_factory=list)
    rewards:         list = field(default_factory=list)
    dones:           list = field(default_factory=list)
    global_ctxs:     list = field(default_factory=list)   # 6-dim ctx per step
    node_ids_visited: list = field(default_factory=list)
    info:            dict = field(default_factory=dict)


# ── PEARL Trainer ──────────────────────────────────────────────────────────────

class PEARLTrainer:
    """
    PEARL meta-RL trainer for V2X routing.

    Replaces MAML's inner-loop adaptation with amortized task inference.

    Training loop:
    ─────────────
    for outer_iter in range(n_meta_iters):
        tasks = sample(n_meta_tasks)

        for task in tasks:
            # 1. Collect context trajectory τ (no z conditioning)
            ctx_eps = collect(task, context_steps, z=None)

            # 2. Encode z from context
            z, kl = task_encoder(ctx_eps)

            # 3. [Optional] Extend context with World Model imagination
            if use_world_model:
                imag = world_model.imagine_batch(n_episodes, seed_ctxs=ctx_eps.ctxs)
                z, kl = re-encode(ctx_eps + imag)

            # 4. Collect query trajectory conditioned on z
            qry_eps = collect(task, n_query_episodes, z=z)

            # 5. Apply ICM intrinsic rewards (query only)
            # 6. Apply HER relabeling (query only, failed episodes)
            # 7. Compute GAE returns

        # 8. Outer update: PPO loss + β·KL
        optimize(policy, encoder)

        if eval_interval: evaluate_holdout()
    """

    def __init__(
        self,
        config:       Optional[PEARLConfig]     = None,
        policy        = None,     # UniversalGNNPolicy instance (z_dim must match cfg.z_dim)
        randomizer    = None,     # DomainRandomizer
        world_model_trainer = None,  # WorldModelTrainer (optional)
        icm           = None,     # IntrinsicCuriosityModule (optional)
        device:       Optional[str] = None,
    ) -> None:
        if not _TORCH_AVAILABLE:
            raise ImportError("PEARLTrainer requires torch. pip install torch")

        self.cfg = config or PEARLConfig()
        dev_str  = device or self.cfg.device
        if dev_str == "auto":
            dev_str = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(dev_str)

        # Policy (must have z_dim == cfg.z_dim).
        # If None is passed, a fresh policy is created automatically.
        if policy is None:
            from .universal_gnn_policy import UniversalGNNPolicy as _P
            policy = _P(z_dim=self.cfg.z_dim)
        self._policy = policy
        assert getattr(self._policy, "z_dim", 0) == self.cfg.z_dim, (
            f"Policy z_dim={getattr(self._policy,'z_dim',0)} ≠ "
            f"cfg.z_dim={self.cfg.z_dim}. "
            "Instantiate UniversalGNNPolicy(z_dim=cfg.z_dim)."
        )
        self._policy.to(self._device)

        # Task encoder
        self._encoder = TaskEncoder(
            z_dim=self.cfg.z_dim,
            hidden=self.cfg.encoder_hidden,
            use_gru=self.cfg.use_gru,
        ).to(self._device)

        # Joint optimizer: policy + encoder
        params = list(self._encoder.parameters())
        if self._policy is not None:
            params += list(self._policy.parameters())
        self._opt = optim.AdamW(
            params,
            lr=self.cfg.lr_outer,
            weight_decay=self.cfg.weight_decay,
        )

        # ICM (optional, reused from MAML if provided)
        self._icm = icm
        if self._icm is not None:
            self._icm.to(self._device)
            self._icm_opt = optim.Adam(self._icm.parameters(), lr=1e-3)
            self._icm_stats = _PearlRunningStats()

        # World Model (optional)
        self._wm_trainer = world_model_trainer

        # Domain randomizer
        self._randomizer = randomizer

        # Stats
        self._return_stats = _PearlRunningStats()
        self._curriculum_stage = self.cfg.curriculum_stage
        self._iter = 0

        # Holdout randomizer: train=False → draws from the 50 disjoint holdout regions.
        # Must use same seed so the region shuffle and train/holdout split is identical.
        try:
            from .domain_randomizer import DomainRandomizer as _DR
            self._holdout_randomizer = _DR(train=False, seed=42)
        except Exception:
            self._holdout_randomizer = None

        log.info(
            "[PEARL] init  device=%s  z_dim=%d  kl_weight=%.3f  "
            "use_icm=%s  use_her=%s  use_wm=%s",
            self._device, self.cfg.z_dim, self.cfg.kl_weight,
            bool(self._icm), self.cfg.use_her,
            bool(self._wm_trainer) and self.cfg.use_world_model,
        )

    # ── KL annealing ────────────────────────────────────────────────────────────

    def _kl_weight(self) -> float:
        """Linear KL annealing: 0 → cfg.kl_weight over kl_anneal_iters."""
        t = min(self._iter / max(self.cfg.kl_anneal_iters, 1), 1.0)
        return t * self.cfg.kl_weight

    def _entropy_coef(self) -> float:
        """Linear entropy decay."""
        t  = min(self._iter / max(self.cfg.ppo_entropy_decay, 1), 1.0)
        ec = self.cfg.ppo_entropy_coef_start + t * (
            self.cfg.ppo_entropy_coef_end - self.cfg.ppo_entropy_coef_start
        )
        # Further halved when ICM active (mirrors MAMLTrainer entropy halving)
        return ec * (0.5 if self._icm is not None else 1.0)

    # ── Episode collection ───────────────────────────────────────────────────────

    def _make_env(self) -> "UniversalV2XEnv":
        """Create a fresh UniversalV2XEnv with the trainer's randomizer."""
        from .universal_v2x_env import UniversalV2XEnv
        return UniversalV2XEnv(
            self._randomizer,
            max_steps=200,
            use_icm=self._icm is not None,
        )

    def _collect_context(
        self,
        env: "UniversalV2XEnv",
        task,
        n_steps: int,
    ) -> Optional[_PearlEpisode]:
        """
        Collect up to n_steps transitions for task inference.
        z=None: actor uses task-agnostic prior (z vector of zeros).
        Context episodes should NOT be augmented with ICM rewards
        (would corrupt task structure for encoder).
        """
        return self._run_env(env, task, n_steps=n_steps, z=None, apply_icm=False)

    def _collect_query(
        self,
        env: "UniversalV2XEnv",
        task,
        n_episodes: int,
        z: "torch.Tensor",
    ) -> List[_PearlEpisode]:
        """
        Collect query episodes conditioned on task latent z.
        ICM and HER applied here.
        """
        eps = []
        for _ in range(n_episodes):
            ep = self._run_env(env, task, n_steps=None, z=z, apply_icm=True)
            if ep is not None:
                eps.append(ep)
        return eps

    def _run_env(
        self,
        env: "UniversalV2XEnv",
        task,
        n_steps: Optional[int],
        z: Optional["torch.Tensor"],
        apply_icm: bool,
    ) -> Optional[_PearlEpisode]:
        """
        Run one episode using the provided env (already holding the right randomizer).

        env.reset(task=task) supplies the graph, BS config, and initial O/D.
        The env's _build_obs() + _compute_global_ctx() are used for obs/ctx so that
        the PEARL collection path is exactly consistent with the MAML collection path.

        With z=None the policy runs without z-conditioning (prior: zeros).
        With z provided the critic is conditioned on z (PEARL mode).
        """
        ep = _PearlEpisode()
        try:
            obs, reset_info = env.reset(task=task, resample_od=True)
        except Exception as e:
            log.debug("[PEARL] env.reset failed: %s", e)
            return None

        steps = 0

        while True:
            # env._compute_global_ctx() gives the 6-dim context at current position
            ctx_raw = env._compute_global_ctx()

            obs_dev = obs.to(self._device) if hasattr(obs, "to") else obs

            # Remove non-tensor node_ids attribute before policy forward
            if hasattr(obs_dev, "node_ids"):
                node_ids_attr = obs_dev.node_ids
                del obs_dev.node_ids
            else:
                node_ids_attr = None

            with torch.no_grad():
                logits, value = self._policy(obs_dev, z=z)
                if torch.isnan(logits).any():
                    break
                dist   = torch.distributions.Categorical(logits=logits)
                action = dist.sample()
                lp     = dist.log_prob(action)

            # Restore node_ids so the obs stored in episode retains it
            if node_ids_attr is not None:
                obs_dev.node_ids = node_ids_attr

            ep.observations.append(obs)
            ep.actions.append(action.item())
            ep.log_probs.append(lp.item())
            ep.values.append(value.item() if value.numel() == 1 else float(value.squeeze()))
            ep.global_ctxs.append(ctx_raw)

            if node_ids_attr is not None:
                neighbor_ids = list(node_ids_attr)
                if action.item() < len(neighbor_ids):
                    ep.node_ids_visited.append(neighbor_ids[action.item()])

            obs, reward, done, trunc, step_info = env.step(action.item())
            node_id = step_info.get("node_id")
            if node_id and (not ep.node_ids_visited or ep.node_ids_visited[-1] != node_id):
                ep.node_ids_visited.append(node_id)

            ep.rewards.append(reward)
            ep.dones.append(done or trunc)

            steps += 1
            if done or trunc:
                ep.info = step_info
                break
            if n_steps is not None and steps >= n_steps:
                ep.info = step_info
                break

        return ep if ep.actions else None

    # ── Context encoding ─────────────────────────────────────────────────────────

    def _encode_context(
        self,
        context_ep: _PearlEpisode,
        imagined_rollouts: Optional[List[dict]] = None,
    ) -> Tuple["torch.Tensor", "torch.Tensor"]:
        """
        Encode task latent z from context transitions.

        Returns (z [z_dim], kl_loss scalar).

        Imagined rollouts (from World Model) are appended with down-weighting
        via `imagination_weight` in reward scaling.
        """
        ctxs_t  = context_ep.global_ctxs[:-1]
        acts    = context_ep.actions
        rews    = context_ep.rewards
        ctxs_t1 = context_ep.global_ctxs[1:]

        L = min(len(ctxs_t), len(acts), len(rews), len(ctxs_t1))
        if L == 0:
            # Return zero-vector prior
            z = torch.zeros(self.cfg.z_dim, device=self._device)
            return z, torch.tensor(0.0, device=self._device)

        # Extend with imagination if available
        if (imagined_rollouts and self.cfg.use_world_model
                and self._wm_trainer is not None):
            n_imag = max(1, int(L * self.cfg.imagination_ctx_ratio))
            for rollout in imagined_rollouts[:n_imag]:
                r_ctxs  = rollout.get("ctxs", [])
                r_acts  = rollout.get("actions", [])
                r_rews  = rollout.get("rewards", [])
                rL = min(len(r_ctxs)-1, len(r_acts), len(r_rews))
                for i in range(rL):
                    ctxs_t.append(r_ctxs[i])
                    acts.append(r_acts[i])
                    rews.append(r_rews[i] * self.cfg.imagination_weight)
                    ctxs_t1.append(r_ctxs[i+1])

        ctx_t  = torch.tensor(ctxs_t[:L],  dtype=torch.float32, device=self._device)
        acts_t = torch.tensor(acts[:L],    dtype=torch.long,    device=self._device)
        rews_t = torch.tensor(rews[:L],    dtype=torch.float32, device=self._device)
        ctx_t1 = torch.tensor(ctxs_t1[:L], dtype=torch.float32, device=self._device)

        z, mu, log_var = self._encoder(ctx_t, acts_t, rews_t, ctx_t1)

        # KL[q || N(0,I)] = -0.5 * Σ (1 + log_var - mu² - exp(log_var))
        kl = -0.5 * (1 + log_var - mu.pow(2) - log_var.exp()).sum()

        return z, kl

    # ── GAE return computation ───────────────────────────────────────────────────

    def _compute_returns(
        self, ep: _PearlEpisode,
    ) -> List[float]:
        """
        GAE-λ returns for one episode.

        Bootstrap value (same convention as maml_trainer._compute_gae_returns):
          - Truly terminal (arrived):   V(s_T) = 0   (absorbing state)
          - Truncated (step limit hit): V(s_T) = ep.values[-1]  (critic estimate)
        Using 0 for truncated episodes causes systematic return underestimation.
        """
        γ, λ = self.cfg.gae_gamma, self.cfg.gae_lambda
        T      = len(ep.rewards)
        returns = [0.0] * T
        gae    = 0.0

        # Distinguish truncated (hit max_steps) from truly terminal (arrived at goal).
        arrived   = ep.info.get("arrived", False) if ep.info else False
        truncated = (not arrived) and bool(ep.values)
        next_val  = float(ep.values[-1]) if truncated else 0.0

        for t in reversed(range(T)):
            mask  = 1 - float(ep.dones[t])   # 0 at last step; 1 otherwise
            nv    = ep.values[t + 1] if (t + 1) < len(ep.values) else next_val
            δ     = ep.rewards[t] + γ * nv * mask - ep.values[t]
            gae   = δ + γ * λ * mask * gae
            returns[t] = gae + ep.values[t]
            self._return_stats.update(returns[t])
        return returns

    # ── PPO loss on query batch ──────────────────────────────────────────────────

    def _ppo_loss(
        self,
        query_eps:  List[_PearlEpisode],
        z:          "torch.Tensor",
    ) -> "torch.Tensor":
        """
        Compute PPO CLIP loss on query episodes conditioned on z.

        Returns scalar loss tensor (attached to computation graph via policy and z).
        """
        all_obs, all_acts, all_lp_old, all_ret, all_adv = [], [], [], [], []

        for ep in query_eps:
            returns = self._compute_returns(ep)
            rets_n  = [self._return_stats.normalize(r) for r in returns]
            advs    = [r - v for r, v in zip(rets_n, ep.values)]

            all_obs.extend(ep.observations)
            all_acts.extend(ep.actions)
            all_lp_old.extend(ep.log_probs)
            all_ret.extend(rets_n)
            all_adv.extend(advs)

        if not all_obs:
            return torch.tensor(0.0, device=self._device, requires_grad=True)

        acts_t   = torch.tensor(all_acts,   dtype=torch.long,    device=self._device)
        lp_old_t = torch.tensor(all_lp_old, dtype=torch.float32, device=self._device)
        ret_t    = torch.tensor(all_ret,    dtype=torch.float32, device=self._device)
        adv_t    = torch.tensor(all_adv,    dtype=torch.float32, device=self._device)
        adv_t    = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

        # PPO mini-batches
        total_loss = torch.tensor(0.0, device=self._device)
        B   = len(all_obs)
        eps = self.cfg.ppo_clip
        ec  = self._entropy_coef()
        vc  = self.cfg.ppo_value_coef

        try:
            from torch_geometric.data import Batch as _PyGBatch
        except ImportError:
            log.warning("[PEARL] torch_geometric not available; PPO loss skipped.")
            return torch.tensor(0.0, device=self._device, requires_grad=True)

        for _ in range(self.cfg.ppo_epochs):
            idx = torch.randperm(B, device=self._device)
            for start in range(0, B, self.cfg.ppo_batch):
                i = idx[start: start + self.cfg.ppo_batch]
                obs_batch = [all_obs[j] for j in i.tolist()]

                obs_clean = []
                for d in obs_batch:
                    dc = d.to(self._device) if hasattr(d, "to") else d
                    if hasattr(dc, "node_ids"):
                        del dc.node_ids
                    obs_clean.append(dc)

                try:
                    pyg_batch = _PyGBatch.from_data_list(obs_clean)
                except Exception:
                    continue

                # z_batch: broadcast z over mini-batch (z is per-task, shared)
                z_b = z.unsqueeze(0).expand(len(i), -1)

                logits_new, values_new = self._policy(pyg_batch, z=z_b)
                dist_new = torch.distributions.Categorical(logits=logits_new)
                lp_new   = dist_new.log_prob(acts_t[i])
                entropy  = dist_new.entropy().mean()

                ratio = (lp_new - lp_old_t[i]).exp()
                a_i   = adv_t[i]
                surr1 = ratio * a_i
                surr2 = ratio.clamp(1 - eps, 1 + eps) * a_i
                pol_loss = -torch.min(surr1, surr2).mean()
                val_loss  = F.mse_loss(values_new.squeeze(-1), ret_t[i])

                loss = pol_loss + vc * val_loss - ec * entropy
                total_loss = total_loss + loss

        return total_loss / max(self.cfg.ppo_epochs, 1)

    # ── ICM update on query episodes ─────────────────────────────────────────────

    def _apply_icm(self, query_eps: List[_PearlEpisode]) -> None:
        """
        Augment query episode rewards with ICM intrinsic signal
        and update ICM parameters.

        Context episodes are NOT touched (see module docstring).
        """
        if self._icm is None:
            return

        for ep in query_eps:
            ctxs = ep.global_ctxs
            acts = ep.actions
            if len(ctxs) < 2:
                continue

            L = min(len(ctxs) - 1, len(acts))
            ctx_t  = torch.tensor(ctxs[:L],  dtype=torch.float32, device=self._device)
            ctx_t1 = torch.tensor(ctxs[1:L+1], dtype=torch.float32, device=self._device)
            acts_t = torch.tensor(acts[:L],  dtype=torch.long,    device=self._device)

            # Forward + inverse loss (update ICM weights)
            fwd_loss, inv_loss, intr = self._icm(ctx_t, acts_t, ctx_t1)
            icm_loss = (1 - 0.2) * fwd_loss + 0.2 * inv_loss
            self._icm_opt.zero_grad()
            icm_loss.backward()
            self._icm_opt.step()

            # Normalize and apply intrinsic reward
            intr_np = intr.detach().cpu().tolist()
            for i in range(min(L, len(ep.rewards))):
                self._icm_stats.update(intr_np[i])
                norm_intr = self._icm_stats.normalize(intr_np[i])
                ep.rewards[i] += self.cfg.icm_reward_scale * norm_intr

    # ── HER relabeling ────────────────────────────────────────────────────────────

    def _apply_her(
        self,
        env: "UniversalV2XEnv",
        query_eps: List[_PearlEpisode],
    ) -> None:
        """
        Hindsight Experience Replay on failed query episodes.

        her_relabel_rewards() is an instance method on UniversalV2XEnv that uses
        env._task.graph to recompute distance progress toward the hindsight goal.
        Only applied to query episodes; context episodes preserve true reward structure
        so that the task encoder sees genuine task dynamics.
        """
        if not self.cfg.use_her:
            return

        for ep in query_eps:
            # Only relabel failed episodes (dones[-1]=True but not arrived)
            arrived = ep.info.get("arrived", False) if ep.info else False
            if arrived:
                continue
            if not ep.dones or not ep.dones[-1]:
                continue
            if random.random() > self.cfg.her_ratio:
                continue
            if len(ep.node_ids_visited) < 3:
                continue

            init_dist = float(env._initial_dist if env._initial_dist else 500.0)
            relabeled, hg_id = env.her_relabel_rewards(
                visited_nodes=ep.node_ids_visited,
                rewards=list(ep.rewards),   # copy — ICM already baked in
                initial_dist=init_dist,
                min_od_m=self.cfg.her_min_od_m,
            )
            if hg_id is not None:
                ep.rewards = relabeled
                if ep.info is None:
                    ep.info = {}
                ep.info["her_goal"] = hg_id

    # ── Outer update ─────────────────────────────────────────────────────────────

    def _outer_update(
        self,
        total_ppo_loss: "torch.Tensor",
        total_kl:       "torch.Tensor",
    ) -> float:
        """Single outer gradient step for policy + encoder."""
        kl_w = self._kl_weight()
        loss  = total_ppo_loss + kl_w * total_kl

        self._opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            list(self._encoder.parameters())
            + (list(self._policy.parameters()) if self._policy else []),
            max_norm=self.cfg.grad_clip,
        )
        self._opt.step()
        return loss.item()

    # ── Main training loop ───────────────────────────────────────────────────────

    def train(self) -> None:
        """Run PEARL meta-training loop."""
        if self._randomizer is None:
            raise RuntimeError("PEARLTrainer requires a DomainRandomizer. "
                               "Set trainer._randomizer before calling train().")

        log.info("[PEARL] Starting meta-training for %d iters.", self.cfg.n_meta_iters)

        for outer_iter in range(self.cfg.n_meta_iters):
            self._iter = outer_iter

            # Curriculum stage advancement
            self._advance_curriculum()

            total_ppo_loss = torch.tensor(0.0, device=self._device)
            total_kl       = torch.tensor(0.0, device=self._device)
            n_tasks_ok     = 0

            max_nodes, max_od_dist, speed_range = self._curriculum_params()
            tasks = [
                self._randomizer.sample_task(
                    max_nodes=max_nodes,
                    max_od_dist_m=max_od_dist,
                    speed_range=speed_range,
                )
                for _ in range(self.cfg.n_meta_tasks)
            ]
            tasks = [t for t in tasks if t is not None]

            for task in tasks:
                try:
                    ppo_l, kl = self._process_task(task)
                    total_ppo_loss = total_ppo_loss + ppo_l
                    total_kl       = total_kl       + kl
                    n_tasks_ok    += 1
                except Exception as e:
                    log.debug("[PEARL] task failed: %s", e)

            if n_tasks_ok == 0:
                continue

            total_ppo_loss = total_ppo_loss / n_tasks_ok
            total_kl       = total_kl       / n_tasks_ok

            outer_loss = self._outer_update(total_ppo_loss, total_kl)

            # World Model online update
            if self._wm_trainer is not None and self.cfg.use_world_model:
                self._wm_trainer.online_update([])  # buffer updated inside _process_task

            if (outer_iter + 1) % 10 == 0:
                log.info(
                    "[PEARL] iter %d  loss=%.4f  ppo=%.4f  kl=%.4f  β=%.3f  "
                    "tasks=%d  entropy_coef=%.4f",
                    outer_iter + 1, outer_loss, total_ppo_loss.item(),
                    total_kl.item(), self._kl_weight(),
                    n_tasks_ok, self._entropy_coef(),
                )

            if (outer_iter + 1) % self.cfg.holdout_eval_interval == 0:
                self._evaluate_holdout()

            if (outer_iter + 1) % self.cfg.checkpoint_interval == 0:
                self._save_checkpoint(outer_iter + 1)

        log.info("[PEARL] Training complete.")

    def _process_task(
        self, task,
    ) -> Tuple["torch.Tensor", "torch.Tensor"]:
        """
        Process one task: collect context → encode z → collect query
        → ICM → HER → PPO loss.

        A single UniversalV2XEnv is created once per task to avoid redundant
        GraphTaskCache rebuilds (the cache is keyed on task identity and is
        preserved across episodes of the same task).

        Returns (ppo_loss, kl) tensors for accumulation.
        """
        try:
            env = self._make_env()
        except ImportError:
            return (torch.tensor(0.0, device=self._device),
                    torch.tensor(0.0, device=self._device))

        # 1. Collect context (no z, no ICM)
        ctx_ep = self._collect_context(env, task, self.cfg.context_steps)
        if ctx_ep is None or not ctx_ep.actions:
            return (torch.tensor(0.0, device=self._device),
                    torch.tensor(0.0, device=self._device))

        # 2. World Model imagination for context extension
        imagined = None
        if (self._wm_trainer is not None
                and self.cfg.use_world_model
                and self._wm_trainer._pretrained):
            seed_ctxs = ctx_ep.global_ctxs
            n_imag    = max(1, int(self.cfg.imagination_ctx_ratio *
                                   len(seed_ctxs)))
            imagined  = self._wm_trainer.imagine_batch(
                n_episodes=n_imag,
                seed_ctxs=seed_ctxs,
            )
            # Add ctx transitions to World Model buffer
            self._wm_trainer.buffer.add_episode(ctx_ep)

        # 3. Encode task latent z
        z, kl = self._encode_context(ctx_ep, imagined_rollouts=imagined)

        # 4. Collect query episodes conditioned on z
        query_eps = self._collect_query(env, task, self.cfg.n_query_episodes, z=z.detach())

        if not query_eps:
            return (torch.tensor(0.0, device=self._device), kl)

        # 5. Apply ICM to query episodes
        self._apply_icm(query_eps)

        # 6. Apply HER to failed query episodes (env instance needed for graph access)
        self._apply_her(env, query_eps)

        # 7. PPO loss conditioned on z (z is differentiable → flows through encoder)
        ppo_loss = self._ppo_loss(query_eps, z)

        return ppo_loss, kl

    # ── Curriculum ───────────────────────────────────────────────────────────────

    def _curriculum_params(self) -> tuple:
        """
        Returns (max_nodes, max_od_dist_m, speed_range) for current stage.
        Same values as MAMLTrainer._curriculum_params().
        """
        s = self._curriculum_stage
        if s == 0:
            return 100, 500.0, (30.0, 60.0)
        if s == 1:
            return 400, 2000.0, (30.0, 90.0)
        return 2000, float("inf"), (30.0, 120.0)

    def _advance_curriculum(self) -> None:
        """Advance internal curriculum stage based on outer iteration.

        DomainRandomizer does not have a set_curriculum_stage() method —
        curriculum is enforced by passing max_nodes/max_od_dist_m to
        sample_task() each iteration via _curriculum_params().
        """
        if (self._curriculum_stage == 0
                and self._iter >= self.cfg.curriculum_stage1_iter):
            self._curriculum_stage = 1
            log.info("[PEARL] Curriculum → Stage 1 (iter %d)", self._iter)
        elif (self._curriculum_stage == 1
              and self._iter >= self.cfg.curriculum_stage2_iter):
            self._curriculum_stage = 2
            log.info("[PEARL] Curriculum → Stage 2 (iter %d)", self._iter)

    # ── Holdout evaluation ───────────────────────────────────────────────────────

    def _evaluate_holdout(self) -> dict:
        """Evaluate on holdout tasks using deterministic z (μ only).

        Uses the separate holdout_randomizer (train=False) so holdout regions
        are always disjoint from training. Falls back to train randomizer if
        holdout randomizer unavailable.
        """
        eval_randomizer = self._holdout_randomizer or self._randomizer
        if eval_randomizer is None:
            return {}

        rewards = []
        for _ in range(self.cfg.holdout_eval_tasks):
            task = eval_randomizer.sample_task()
            if task is None:
                continue

            try:
                env = self._make_env()
            except ImportError:
                break

            ctx_ep = self._collect_context(env, task, self.cfg.context_steps)
            if ctx_ep is None or not ctx_ep.actions:
                continue

            with torch.no_grad():
                z, _ = self._encode_context(ctx_ep)

            ep = self._run_env(env, task, n_steps=None, z=z.detach(), apply_icm=False)
            if ep is not None and ep.rewards:
                rewards.append(sum(ep.rewards))

        if not rewards:
            return {}

        mean_r = sum(rewards) / len(rewards)
        log.info(
            "[PEARL] holdout  iter=%d  tasks=%d  mean_ep_reward=%.3f",
            self._iter, len(rewards), mean_r,
        )
        return {"holdout_mean_reward": mean_r, "holdout_n": len(rewards)}

    # ── Checkpointing ─────────────────────────────────────────────────────────────

    def _save_checkpoint(self, iteration: int) -> None:
        import os
        os.makedirs(self.cfg.checkpoint_dir, exist_ok=True)
        path = os.path.join(self.cfg.checkpoint_dir, f"pearl_iter{iteration}.pt")
        state = {
            "iteration":   iteration,
            "encoder":     self._encoder.state_dict(),
            "optimizer":   self._opt.state_dict(),
        }
        if self._policy is not None:
            sd = self._policy.state_dict()
            state["policy"]     = sd
            state["state_dict"] = sd   # V4InferenceModule._load_model() compatibility
            state["config"]     = {"z_dim": self._policy.z_dim}
        if self._icm is not None:
            state["icm"]     = self._icm.state_dict()
            state["icm_opt"] = self._icm_opt.state_dict()
        torch.save(state, path)
        log.info("[PEARL] checkpoint saved → %s", path)

    def load_checkpoint(self, path: str) -> None:
        state = torch.load(path, map_location=self._device)
        self._encoder.load_state_dict(state["encoder"])
        self._opt.load_state_dict(state["optimizer"])
        if self._policy is not None and "policy" in state:
            self._policy.load_state_dict(state["policy"])
        if self._icm is not None and "icm" in state:
            self._icm.load_state_dict(state["icm"])
            self._icm_opt.load_state_dict(state["icm_opt"])
        self._iter = state.get("iteration", 0)
        log.info("[PEARL] checkpoint loaded ← %s  (iter=%d)", path, self._iter)

    # ── Inference API (used after training) ──────────────────────────────────────

    @torch.no_grad()
    def infer_task_z(
        self,
        context_transitions: List[Tuple],
    ) -> "torch.Tensor":
        """
        Encode task latent from a list of (ctx_t, action, reward, ctx_t1) tuples.
        Returns z vector [z_dim] on CPU.
        """
        if not context_transitions:
            return torch.zeros(self.cfg.z_dim)

        ctxs_t, acts, rews, ctxs_t1 = zip(*context_transitions)
        ctx_t  = torch.tensor(list(ctxs_t),  dtype=torch.float32, device=self._device)
        acts_t = torch.tensor(list(acts),    dtype=torch.long,    device=self._device)
        rews_t = torch.tensor(list(rews),    dtype=torch.float32, device=self._device)
        ctx_t1 = torch.tensor(list(ctxs_t1), dtype=torch.float32, device=self._device)

        z = self._encoder.infer_z(ctx_t, acts_t, rews_t, ctx_t1, sample=False)
        return z.cpu()
