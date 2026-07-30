"""
First-Order MAML (FOMAML) Trainer for Universal V2X Policy.

Algorithm (Finn et al. 2017, §3):
  θ  = meta-parameters (GNN policy weights, shared across all tasks)
  α  = inner learning rate (task adaptation)
  β  = outer/meta learning rate (meta-update)

  For each meta-iteration:
    1. Sample N tasks τ₁…τ_N from DomainRandomizer
    2. For each τ_i:
       a. Clone model: φ_i ← θ
       b. Run K inner gradient steps on support set of τ_i: φ'_i
       c. Collect query-set episodes with φ'_i → compute L(φ'_i, τ_i)
    3. Meta-update: θ ← θ − β × (1/N) × Σᵢ ∇_{φ'_i} L(φ'_i, τ_i)

FOMAML drops the second-order term (∂²L/∂θ²), using ∇_{φ'_i} instead of
∇_θ L(φ'_i, τ_i). This makes it O(K) cheaper with minimal performance loss
(Nichol et al., "On First-Order Meta-Learning Algorithms", arXiv 2018).

PPO is used as the inner-loop RL algorithm (not raw policy gradient), which
stabilises learning across diverse tasks.

References:
  [1] Finn, C., Abbeel, P., Levine, S., "Model-Agnostic Meta-Learning for
      Fast Adaptation of Deep Networks", ICML 2017, arXiv:1703.03400.
  [2] Nichol, A., Achiam, J., Schulman, J., "On First-Order Meta-Learning
      Algorithms", arXiv:1803.02999, 2018.
  [3] Schulman, J. et al., "Proximal Policy Optimization Algorithms",
      arXiv:1707.06347, 2017.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .domain_randomizer import DomainRandomizer, V2XTask
from .universal_v2x_env import UniversalV2XEnv

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from .universal_gnn_policy import UniversalGNNPolicy, build_graph_obs
    _TORCH_GEO_AVAILABLE = True
except ImportError:
    _TORCH_GEO_AVAILABLE = False

# ── Hyper-parameters (A100 defaults) ──────────────────────────────────────────
@dataclass
class MAMLConfig:
    # Meta
    n_tasks_per_iter: int = 16         # N tasks per meta-iteration (batch)
    meta_iterations: int = 10_000      # total outer-loop iterations
    meta_lr: float = 3e-4              # β — Adam for outer loop
    meta_lr_schedule: str = "cosine"   # "constant" | "cosine" | "linear"

    # Inner loop (PPO)
    inner_steps: int = 5               # K — gradient steps per task
    inner_lr: float = 1e-3             # α — SGD for inner loop
    support_episodes: int = 4          # episodes to collect for support set
    query_episodes: int = 4            # episodes for query-set meta-gradient

    # PPO
    ppo_clip_eps: float = 0.2
    ppo_value_coef: float = 0.5
    ppo_entropy_coef: float = 0.01
    gamma: float = 0.99
    gae_lambda: float = 0.95

    # Curriculum (Bengio et al. 2009)
    curriculum_warmup_iters: int = 500  # iters before moving to full randomisation
    curriculum_start_stage: int = 0    # 0=easy, 1=medium, 2=hard

    # Persistence
    save_dir: str = "app/services/rl/models"
    save_every: int = 500              # checkpoint every N meta-iters
    log_every: int = 50

    # Device auto-detection: CUDA (A100) > MPS (Apple Silicon) > CPU
    device: str = (
        "cuda" if __import__("torch").cuda.is_available() else
        "mps"  if __import__("torch").backends.mps.is_available() else
        "cpu"
    )
    compile_model: bool = True         # torch.compile() on A100/MPS → ~1.4× speedup


@dataclass
class _Episode:
    """One collected episode (support or query)."""
    obs_list: list        = field(default_factory=list)
    actions: list         = field(default_factory=list)
    rewards: list         = field(default_factory=list)
    log_probs: list       = field(default_factory=list)
    values: list          = field(default_factory=list)
    masks: list           = field(default_factory=list)  # action masks
    arrived: bool         = False
    total_reward: float   = 0.0
    avg_latency_ms: float = 0.0


class MAMLTrainer:
    """
    First-order MAML trainer for UniversalGNNPolicy.

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

        # Meta-model (shared θ)
        self._model = UniversalGNNPolicy().to(self._device)
        if self.cfg.compile_model and hasattr(torch, "compile"):
            self._model = torch.compile(self._model)

        # Meta-optimizer (outer loop, Adam)
        self._meta_opt = optim.Adam(self._model.parameters(), lr=self.cfg.meta_lr)

        # LR scheduler
        self._scheduler = self._build_scheduler()

        # Logging
        self._save_dir = Path(self.cfg.save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._log: list[dict] = []
        self._iter = 0

    # ── Main training loop ─────────────────────────────────────────────────────
    def train(self, resume_from: Optional[str] = None) -> None:
        if resume_from:
            self._load_checkpoint(resume_from)

        print(f"[MAML] Starting training on {self._device}")
        print(f"       {self.cfg.n_tasks_per_iter} tasks/iter × {self.cfg.meta_iterations} iters")
        t_start = time.time()

        for it in range(self._iter, self.cfg.meta_iterations):
            self._iter = it

            # ── Curriculum stage ──────────────────────────────────────────────
            stage = self._curriculum_stage(it)

            # ── Sample N tasks ─────────────────────────────────────────────────
            tasks = [self._randomizer.sample_task() for _ in range(self.cfg.n_tasks_per_iter)]
            tasks = [t for t in tasks if t is not None]
            if not tasks:
                continue

            # ── Inner loops → collect meta-gradients ──────────────────────────
            meta_grads = self._meta_step(tasks, stage)

            # ── Outer update ──────────────────────────────────────────────────
            self._meta_opt.zero_grad()
            for p, g in zip(self._model.parameters(), meta_grads):
                if g is not None:
                    p.grad = g.to(self._device)
            self._meta_opt.step()
            if self._scheduler:
                self._scheduler.step()

            # ── Logging ───────────────────────────────────────────────────────
            if it % self.cfg.log_every == 0:
                elapsed = time.time() - t_start
                self._log_iter(it, tasks, elapsed)

            # ── Checkpoint ───────────────────────────────────────────────────
            if (it + 1) % self.cfg.save_every == 0:
                self._save_checkpoint(it + 1)

        self._save_checkpoint("final")
        print(f"[MAML] Training complete — {time.time()-t_start:.0f}s total")

    # ── Inner loop (FOMAML) ────────────────────────────────────────────────────
    def _meta_step(
        self,
        tasks: list[V2XTask],
        curriculum_stage: int,
    ) -> list[Optional["torch.Tensor"]]:
        """
        For each task:
          1. Clone θ → φ_i
          2. K inner SGD steps on support episodes
          3. Evaluate φ'_i on query episodes → gradient w.r.t φ'_i  (FOMAML)

        Returns list of accumulated meta-gradients (one per parameter).
        """
        n_params = len(list(self._model.parameters()))
        accumulated = [None] * n_params  # type: list[Optional[torch.Tensor]]

        for task in tasks:
            # Clone: φ_i ← θ
            fast_model = copy.deepcopy(self._model)
            fast_model.to(self._device)
            inner_opt = optim.SGD(fast_model.parameters(), lr=self.cfg.inner_lr)

            env = UniversalV2XEnv(
                self._randomizer,
                channel_lookup=self._channel_lookup,
            )

            # ── Support set: K inner gradient steps ───────────────────────────
            for _ in range(self.cfg.inner_steps):
                episodes = self._collect_episodes(
                    fast_model, env, task, self.cfg.support_episodes, curriculum_stage
                )
                loss = self._ppo_loss(fast_model, episodes)
                inner_opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(fast_model.parameters(), max_norm=0.5)
                inner_opt.step()

            # ── Query set: compute meta-gradient at φ'_i ─────────────────────
            query_eps = self._collect_episodes(
                fast_model, env, task, self.cfg.query_episodes, curriculum_stage
            )
            meta_loss = self._ppo_loss(fast_model, query_eps)
            fast_model.zero_grad()
            meta_loss.backward()

            # Accumulate (FOMAML: use gradient at φ'_i, not ∂²L/∂θ²)
            for i, p in enumerate(fast_model.parameters()):
                if p.grad is not None:
                    g = p.grad.cpu() / len(tasks)
                    accumulated[i] = g if accumulated[i] is None else accumulated[i] + g

        return accumulated

    # ── Episode collection ─────────────────────────────────────────────────────
    @torch.no_grad()
    def _collect_episodes(
        self,
        model: "UniversalGNNPolicy",
        env: "UniversalV2XEnv",
        task: "V2XTask",
        n_episodes: int,
        curriculum_stage: int,
    ) -> list[_Episode]:
        model.eval()
        episodes = []
        for _ in range(n_episodes):
            ep = _Episode()
            obs, _ = env.reset(task=task)
            done = False
            while not done:
                data = obs.to(self._device) if hasattr(obs, "to") else obs
                mask_list = env.action_masks()
                mask = torch.tensor(mask_list, dtype=torch.bool, device=self._device)

                logits, value = model(data, action_mask=mask)
                dist = torch.distributions.Categorical(logits=logits)
                action = dist.sample()
                log_prob = dist.log_prob(action)

                obs, reward, terminated, truncated, info = env.step(action.item())
                done = terminated or truncated

                ep.obs_list.append(obs)
                ep.actions.append(action.item())
                ep.rewards.append(reward)
                ep.log_probs.append(log_prob.item())
                ep.values.append(value.item())
                ep.masks.append(mask_list)
                ep.total_reward += reward
                ep.avg_latency_ms += info.get("latency_ms", 0.0)

            ep.arrived = info.get("arrived", False)
            if ep.obs_list:
                ep.avg_latency_ms /= len(ep.obs_list)
            episodes.append(ep)

        model.train()
        return episodes

    # ── PPO loss ───────────────────────────────────────────────────────────────
    def _ppo_loss(
        self,
        model: "UniversalGNNPolicy",
        episodes: list[_Episode],
    ) -> "torch.Tensor":
        """
        Clipped PPO surrogate loss.

        L = −E[min(r·A, clip(r, 1−ε, 1+ε)·A)]
          + c₁·V_loss − c₂·entropy
        """
        total_loss = torch.tensor(0.0, device=self._device, requires_grad=True)
        n_steps = 0

        for ep in episodes:
            if not ep.obs_list:
                continue
            returns = self._compute_gae_returns(ep)

            for t, (obs, action, old_log_prob, ret, adv, mask_list) in enumerate(zip(
                ep.obs_list, ep.actions, ep.log_probs, returns[0], returns[1], ep.masks
            )):
                data = obs.to(self._device) if hasattr(obs, "to") else obs
                mask = torch.tensor(mask_list, dtype=torch.bool, device=self._device)
                act_t = torch.tensor(action, device=self._device)

                log_probs_new, values_new, entropy = model.evaluate_actions(data, act_t, mask)

                ratio = torch.exp(log_probs_new - old_log_prob)
                adv_t = torch.tensor(adv, device=self._device, dtype=torch.float32)
                ret_t = torch.tensor(ret, device=self._device, dtype=torch.float32)

                clipped = torch.clamp(ratio, 1 - self.cfg.ppo_clip_eps, 1 + self.cfg.ppo_clip_eps)
                policy_loss = -torch.min(ratio * adv_t, clipped * adv_t)
                value_loss  = (values_new - ret_t).pow(2)

                step_loss = (
                    policy_loss
                    + self.cfg.ppo_value_coef * value_loss
                    - self.cfg.ppo_entropy_coef * entropy
                )
                total_loss = total_loss + step_loss
                n_steps += 1

        return total_loss / max(n_steps, 1)

    def _compute_gae_returns(
        self, ep: _Episode
    ) -> tuple[list[float], list[float]]:
        """Generalized Advantage Estimation (Schulman et al. 2016)."""
        T = len(ep.rewards)
        returns  = [0.0] * T
        advs     = [0.0] * T
        gae      = 0.0
        next_val = 0.0

        for t in reversed(range(T)):
            delta = ep.rewards[t] + self.cfg.gamma * next_val - ep.values[t]
            gae   = delta + self.cfg.gamma * self.cfg.gae_lambda * gae
            returns[t] = gae + ep.values[t]
            advs[t]    = gae
            next_val   = ep.values[t]

        # Normalize advantages
        if len(advs) > 1:
            a_mean = sum(advs) / len(advs)
            a_std  = (sum((a - a_mean) ** 2 for a in advs) / len(advs)) ** 0.5
            advs   = [(a - a_mean) / (a_std + 1e-8) for a in advs]

        return returns, advs

    # ── Curriculum ────────────────────────────────────────────────────────────
    def _curriculum_stage(self, iteration: int) -> int:
        """
        0 = easy (small regions, short O/D)
        1 = medium
        2 = full randomisation

        Refs: Bengio et al., "Curriculum Learning", ICML 2009.
        """
        warmup = self.cfg.curriculum_warmup_iters
        if iteration < warmup // 3:
            return 0
        if iteration < warmup:
            return 1
        return 2

    # ── Persistence ───────────────────────────────────────────────────────────
    def _save_checkpoint(self, tag) -> None:
        path = self._save_dir / f"maml_gnn_{tag}.pt"
        torch.save({
            "iteration": self._iter,
            "state_dict": (
                self._model._orig_mod.state_dict()
                if hasattr(self._model, "_orig_mod") else self._model.state_dict()
            ),
            "meta_opt": self._meta_opt.state_dict(),
            "config": vars(self.cfg),
            "log": self._log[-100:],
        }, path)
        print(f"[MAML] Checkpoint saved → {path}")

    def _load_checkpoint(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self._device)
        self._iter = ckpt.get("iteration", 0)
        target = self._model._orig_mod if hasattr(self._model, "_orig_mod") else self._model
        target.load_state_dict(ckpt["state_dict"])
        self._meta_opt.load_state_dict(ckpt["meta_opt"])
        self._log = ckpt.get("log", [])
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
                total_iters=self.cfg.meta_iterations
            )
        return None

    def _log_iter(self, it: int, tasks: list, elapsed: float) -> None:
        lr = self._meta_opt.param_groups[0]["lr"]
        entry = {
            "iter": it,
            "n_tasks": len(tasks),
            "lr": round(lr, 6),
            "elapsed_s": round(elapsed, 1),
        }
        self._log.append(entry)
        print(f"[MAML iter {it:5d}] tasks={len(tasks)}  lr={lr:.2e}  {elapsed:.0f}s")

    # ── 5-shot adaptation (inference) ─────────────────────────────────────────
    def adapt(
        self,
        task: V2XTask,
        n_adapt_episodes: int = 5,
    ) -> "UniversalGNNPolicy":
        """
        Fine-tune a copy of the meta-model on `task` using K=5 episodes.

        This is the inference-time MAML adaptation for a new user scenario.

        Returns the adapted model (does NOT modify self._model).
        """
        if not _TORCH_GEO_AVAILABLE:
            raise ImportError("torch_geometric required")

        adapted = copy.deepcopy(self._model)
        adapted.to(self._device)
        opt = optim.SGD(adapted.parameters(), lr=self.cfg.inner_lr)
        env = UniversalV2XEnv(self._randomizer, channel_lookup=self._channel_lookup)

        for _ in range(self.cfg.inner_steps):
            eps = self._collect_episodes(adapted, env, task, n_adapt_episodes, stage=2)
            loss = self._ppo_loss(adapted, eps)
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(adapted.parameters(), 0.5)
            opt.step()

        adapted.eval()
        return adapted
