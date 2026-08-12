"""
Graph World Model for V2X routing — Dreamer-style latent dynamics.

Operates entirely in the 6-dim global_ctx space:
  ctx = [step_p, snr_n, lat_n, aoi_n, speed_n, d2d_n]

This choice avoids variable-size graph tensors during imagination.
The GNN encoder's output is implicitly captured in ctx via the features
that the env computes (snr_n, lat_n from nearest BS, d2d_n from haversine).

Architecture
────────────
  DynamicsModel f : (ctx_t [6], a_onehot [5]) → ctx_{t+1} [6]
  RewardModel   r : (ctx_t [6], a_onehot [5]) → r̂  [1]
  CoverageModel c : (ctx_t [6])               → (snr_n, lat_n, cov_p) [3]

ICM synergy
───────────
  The DynamicsModel here IS the ICM forward model, but better:
  - Trained with an explicit data buffer (not just online batches)
  - Can handle multi-step predictions via recursive application
  - Shares no weights with ICM — separate, higher-quality dynamics estimate
  - When use_icm=True, ICM continues to use its own forward_net for intrinsic
    rewards; WorldModel.DynamicsModel is used for planning / imagination only

Imagination rollouts
────────────────────
  imagine_rollout(ctx_0, policy, horizon=10):
    For each step:
      1. Sample action from policy (deterministic=True for planning)
      2. Predict ctx_{t+1} = f(ctx_t, a)
      3. Predict reward r̂ = r(ctx_t, a)
      4. Repeat for horizon steps
    Returns synthetic _Episode-like object for PPO outer-loop update

Training: 2-stage
────────────────
  Stage 1 — offline pretraining from real experience buffer:
    L = MSE(f(ctx_t, a), ctx_{t+1}) + MSE(r̂, r) + MSE(ĉ, coverage_label)

  Stage 2 — online fine-tuning interleaved with PEARL training:
    New real transitions added to buffer every N outer iterations.

World Model + PEARL synergy:
  PEARL's task encoder q(z|τ) encodes REAL support transitions.
  The World Model EXTENDS these with imagination → longer effective horizon
  without additional env interaction.

References:
  [1] Hafner, D. et al., "Dream to Control: Learning Behaviors by Latent
      Imagination", ICLR 2020 (Dreamer). arXiv:1912.01603.
  [2] Hafner, D. et al., "Mastering Atari with Discrete World Models",
      ICLR 2021 (DreamerV2). arXiv:2010.02193.
  [3] Pan, F. et al., "Semantic Predictive Control for Explainable and
      Efficient Policy Learning", IEEE RA-L 2020. (planning in latent space)
"""
from __future__ import annotations

import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

CTX_DIM    = 6    # global_ctx dimension (matches GLOBAL_CTX_DIM in gnn_policy)
N_ACTIONS  = 5    # MAX_CANDIDATES

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    import torch.optim as optim
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False
    torch = None  # type: ignore
    nn    = None  # type: ignore


# ── Configuration ──────────────────────────────────────────────────────────────

@dataclass
class WorldModelConfig:
    """World Model hyperparameters."""
    # Architecture
    ctx_dim:       int   = CTX_DIM
    n_actions:     int   = N_ACTIONS
    hidden:        int   = 128
    n_layers:      int   = 2        # MLP depth

    # Training
    lr:            float = 3e-4
    weight_decay:  float = 1e-4
    batch_size:    int   = 256
    buffer_size:   int   = 50_000  # max transitions in replay buffer
    pretrain_steps: int  = 2_000   # gradient steps for Stage 1 pretraining
    online_steps:  int   = 50      # gradient steps per online update

    # Imagination
    imagination_horizon: int   = 8     # steps to imagine ahead
    imagination_episodes: int  = 16    # synthetic episodes per outer iter
    imagination_ratio:   float = 0.3   # fraction of PPO batch from imagination
    gamma:               float = 0.99  # discount for imagined returns

    # Loss weights
    dynamics_weight:  float = 1.0
    reward_weight:    float = 1.0
    coverage_weight:  float = 0.5


# ── Sub-models ─────────────────────────────────────────────────────────────────

def _mlp(in_dim: int, out_dim: int, hidden: int, n_layers: int) -> "nn.Sequential":
    """Shared MLP builder (ReLU activations, no final activation)."""
    layers: list = [nn.Linear(in_dim, hidden), nn.LayerNorm(hidden), nn.ReLU()]
    for _ in range(n_layers - 1):
        layers += [nn.Linear(hidden, hidden), nn.LayerNorm(hidden), nn.ReLU()]
    layers.append(nn.Linear(hidden, out_dim))
    return nn.Sequential(*layers)


if _TORCH_AVAILABLE:
    class DynamicsModel(nn.Module):
        """
        f(ctx_t, a_t) → ctx_{t+1}

        Predicts next global context given current context and action.
        Trained with MSE on real transitions; used for imagination rollouts.
        """
        def __init__(self, cfg: WorldModelConfig) -> None:
            super().__init__()
            self.net = _mlp(cfg.ctx_dim + cfg.n_actions, cfg.ctx_dim, cfg.hidden, cfg.n_layers)

        def forward(self, ctx: "torch.Tensor", a_onehot: "torch.Tensor") -> "torch.Tensor":
            return self.net(torch.cat([ctx, a_onehot], dim=-1))

    class RewardModel(nn.Module):
        """r̂(ctx_t, a_t) → scalar reward estimate."""
        def __init__(self, cfg: WorldModelConfig) -> None:
            super().__init__()
            self.net = _mlp(cfg.ctx_dim + cfg.n_actions, 1, cfg.hidden, cfg.n_layers)

        def forward(self, ctx: "torch.Tensor", a_onehot: "torch.Tensor") -> "torch.Tensor":
            return self.net(torch.cat([ctx, a_onehot], dim=-1)).squeeze(-1)

    class CoverageModel(nn.Module):
        """
        c(ctx_t) → (snr_n, lat_n, cov_prob)

        Decodes V2X quality metrics from context.
        These are NOT directly in ctx but can be predicted from the dynamics.
        ctx[1]=snr_n, ctx[2]=lat_n are already in ctx —
        this model is used to predict them at IMAGINED future steps
        where ctx_t+H is unavailable.
        """
        def __init__(self, cfg: WorldModelConfig) -> None:
            super().__init__()
            self.net = _mlp(cfg.ctx_dim, 3, cfg.hidden, cfg.n_layers)

        def forward(self, ctx: "torch.Tensor") -> "torch.Tensor":
            """Returns [snr_n, lat_n, cov_prob] ∈ [0,1] after sigmoid."""
            return torch.sigmoid(self.net(ctx))

    class WorldModel(nn.Module):
        """
        Unified wrapper: Dynamics + Reward + Coverage.

        During training, use world_model_loss() for joint optimization.
        During planning, use imagine_rollout().
        """

        def __init__(self, cfg: WorldModelConfig) -> None:
            super().__init__()
            self.cfg      = cfg
            self.dynamics = DynamicsModel(cfg)
            self.reward   = RewardModel(cfg)
            self.coverage = CoverageModel(cfg)

        def world_model_loss(
            self,
            ctx_t:    "torch.Tensor",    # [B, ctx_dim]
            actions:  "torch.Tensor",    # [B] long
            rewards:  "torch.Tensor",    # [B]
            ctx_t1:   "torch.Tensor",    # [B, ctx_dim]
        ) -> "torch.Tensor":
            """
            Joint training loss for all three sub-models.

            L = w_dyn · MSE(f(ctx_t, a), ctx_{t+1})
              + w_rew · MSE(r̂(ctx_t, a), r)
              + w_cov · MSE(c(ctx_t), ctx_{t+1}[snr, lat] + cov_label)
            """
            a_oh = F.one_hot(actions, num_classes=self.cfg.n_actions).float()

            # Dynamics loss
            pred_ctx_t1  = self.dynamics(ctx_t, a_oh)
            dyn_loss     = F.mse_loss(pred_ctx_t1, ctx_t1)

            # Reward loss
            pred_rewards = self.reward(ctx_t, a_oh)
            rew_loss     = F.mse_loss(pred_rewards, rewards)

            # Coverage loss: predict snr_n (ctx[1]), lat_n (ctx[2]) from current ctx
            # cov_prob is approximated as 1 when snr_n > 0.1 (ad-hoc label)
            cov_labels   = torch.stack([
                ctx_t1[:, 1],                               # snr_n at t+1
                ctx_t1[:, 2],                               # lat_n at t+1
                (ctx_t1[:, 1] > 0.1).float(),              # coverage proxy
            ], dim=-1)
            pred_cov     = self.coverage(ctx_t)
            cov_loss     = F.mse_loss(pred_cov, cov_labels)

            return (self.cfg.dynamics_weight  * dyn_loss
                    + self.cfg.reward_weight  * rew_loss
                    + self.cfg.coverage_weight * cov_loss)

        @torch.no_grad()
        def imagine_rollout(
            self,
            ctx_0:     "torch.Tensor",    # [ctx_dim] — starting context
            policy,                        # UniversalGNNPolicy (act deterministically)
            horizon:   int,
            device:    "torch.device",
        ) -> dict:
            """
            Imagine a horizon-step rollout starting from ctx_0.

            Uses the Dynamics and Reward models — no real env interaction.
            Returns a dict with 'ctxs', 'actions', 'rewards', 'values'
            compatible with _Episode fields in maml_trainer.

            Note: without real graph observations, the policy's GNN cannot
            produce meaningful logits.  Instead we use a RANDOM action sampled
            uniformly (imagination explores; the policy is used for VALUE only).
            This is the "dyna-style" imagination: random actions + learned reward.

            Synergy with PEARL: PEARL task encoder sees real ctx transitions;
            imagination extends the horizon WITHOUT additional env cost.
            """
            ctx = ctx_0.to(device).float()
            if ctx.dim() == 1:
                ctx = ctx.unsqueeze(0)   # [1, ctx_dim]

            ctxs    = [ctx.squeeze(0).cpu().tolist()]
            actions = []
            rewards = []

            for _ in range(horizon):
                act = torch.randint(0, self.cfg.n_actions, (1,), device=device)
                a_oh = F.one_hot(act, num_classes=self.cfg.n_actions).float()
                r_hat = self.reward(ctx, a_oh).item()
                ctx   = self.dynamics(ctx, a_oh)
                ctxs.append(ctx.squeeze(0).cpu().tolist())
                actions.append(act.item())
                rewards.append(r_hat)

            return {
                "ctxs":    ctxs,
                "actions": actions,
                "rewards": rewards,
            }


# ── Replay Buffer ──────────────────────────────────────────────────────────────

class TransitionBuffer:
    """
    Fixed-size circular buffer for (ctx_t, action, reward, ctx_{t+1}) transitions.

    World Model is trained offline from this buffer.
    ICM also stores intrinsic rewards here for diagnostics.
    """

    def __init__(self, maxlen: int = 50_000) -> None:
        self._buf: deque = deque(maxlen=maxlen)

    def add(
        self,
        ctx_t:   List[float],
        action:  int,
        reward:  float,
        ctx_t1:  List[float],
    ) -> None:
        self._buf.append((ctx_t, action, reward, ctx_t1))

    def add_episode(self, ep) -> None:
        """Add all transitions from a _Episode (maml_trainer format)."""
        ctxs = ep.global_ctxs
        for i, (act, rew) in enumerate(zip(ep.actions, ep.rewards)):
            if i + 1 < len(ctxs):
                self.add(ctxs[i], act, rew, ctxs[i + 1])

    def sample(self, batch_size: int) -> Tuple:
        """Sample a random mini-batch."""
        batch = random.sample(list(self._buf), min(batch_size, len(self._buf)))
        ctxs_t, acts, rews, ctxs_t1 = zip(*batch)
        return list(ctxs_t), list(acts), list(rews), list(ctxs_t1)

    def __len__(self) -> int:
        return len(self._buf)


# ── World Model Trainer ────────────────────────────────────────────────────────

class WorldModelTrainer:
    """
    Trains the WorldModel from real experience collected by MAMLTrainer / PEARLTrainer.

    Usage:
        wm_trainer = WorldModelTrainer(WorldModelConfig())
        # Stage 1: pretrain from existing data
        wm_trainer.pretrain(buffer)
        # Stage 2: online update during training loop
        for outer_iter in training_loop:
            wm_trainer.online_update(new_episodes)
            imagined = wm_trainer.imagine_batch(n=16, horizon=8, device=device)
    """

    def __init__(
        self,
        config: Optional[WorldModelConfig] = None,
        device: Optional[str] = None,
    ) -> None:
        if not _TORCH_AVAILABLE:
            raise ImportError("WorldModelTrainer requires torch. pip install torch")
        self.cfg    = config or WorldModelConfig()
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.wm     = WorldModel(self.cfg).to(self.device)
        self.opt    = optim.AdamW(
            self.wm.parameters(),
            lr=self.cfg.lr,
            weight_decay=self.cfg.weight_decay,
        )
        self.buffer = TransitionBuffer(maxlen=self.cfg.buffer_size)
        self._pretrained = False

    def pretrain(self, buffer: Optional[TransitionBuffer] = None) -> dict:
        """
        Stage 1: fit dynamics/reward/coverage from a pre-collected buffer.

        If buffer is None, uses self.buffer.
        Returns training stats dict.
        """
        buf = buffer or self.buffer
        if len(buf) < self.cfg.batch_size:
            log.warning(
                "[WorldModel] pretrain skipped: buffer has %d transitions "
                "(need %d). Run more MAML/PEARL iterations first.",
                len(buf), self.cfg.batch_size,
            )
            return {"skipped": True}

        self.wm.train()
        losses = []
        for step in range(self.cfg.pretrain_steps):
            loss = self._gradient_step(buf)
            losses.append(loss)
            if (step + 1) % 200 == 0:
                log.info(
                    "[WorldModel] pretrain step %d/%d  loss=%.4f",
                    step + 1, self.cfg.pretrain_steps, sum(losses[-50:]) / 50,
                )

        self._pretrained = True
        final_loss = sum(losses[-100:]) / max(len(losses[-100:]), 1)
        log.info("[WorldModel] Stage 1 complete. Final loss=%.4f", final_loss)
        return {"pretrain_steps": self.cfg.pretrain_steps, "final_loss": final_loss}

    def online_update(self, episodes: list) -> float:
        """
        Stage 2: add episodes to buffer + run N gradient steps.
        Returns mean loss.
        """
        for ep in episodes:
            self.buffer.add_episode(ep)

        if len(self.buffer) < self.cfg.batch_size:
            return 0.0

        self.wm.train()
        losses = [self._gradient_step(self.buffer) for _ in range(self.cfg.online_steps)]
        return sum(losses) / len(losses)

    def _gradient_step(self, buf: TransitionBuffer) -> float:
        ctxs_t, acts, rews, ctxs_t1 = buf.sample(self.cfg.batch_size)
        ctx_t  = torch.tensor(ctxs_t,  dtype=torch.float32, device=self.device)
        ctx_t1 = torch.tensor(ctxs_t1, dtype=torch.float32, device=self.device)
        acts_t = torch.tensor(acts,     dtype=torch.long,    device=self.device)
        rews_t = torch.tensor(rews,     dtype=torch.float32, device=self.device)

        loss = self.wm.world_model_loss(ctx_t, acts_t, rews_t, ctx_t1)
        self.opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.wm.parameters(), max_norm=5.0)
        self.opt.step()
        return loss.item()

    @torch.no_grad()
    def imagine_batch(
        self,
        n_episodes: int,
        horizon: Optional[int] = None,
        seed_ctxs: Optional[List[List[float]]] = None,
    ) -> List[dict]:
        """
        Generate n_episodes imagined rollouts.

        seed_ctxs: starting contexts (sampled from buffer if None).
        Returns list of rollout dicts compatible with _Episode.

        Anti-synergy note: imagined rollouts are NOT relabeled by HER
        (they have no real node_ids to relabel). ICM intrinsic rewards
        are also not applied (no real state transition). Only extrinsic
        reward model outputs are used.
        """
        if not self._pretrained and len(self.buffer) < self.cfg.batch_size:
            return []

        h = horizon or self.cfg.imagination_horizon
        rollouts = []

        for i in range(n_episodes):
            if seed_ctxs and i < len(seed_ctxs):
                ctx_0 = torch.tensor(seed_ctxs[i], dtype=torch.float32, device=self.device)
            elif len(self.buffer) > 0:
                raw = random.choice(list(self.buffer._buf))
                ctx_0 = torch.tensor(raw[0], dtype=torch.float32, device=self.device)
            else:
                ctx_0 = torch.zeros(self.cfg.ctx_dim, device=self.device)

            self.wm.eval()
            rollout = self.wm.imagine_rollout(ctx_0, policy=None, horizon=h, device=self.device)
            rollouts.append(rollout)

        return rollouts
