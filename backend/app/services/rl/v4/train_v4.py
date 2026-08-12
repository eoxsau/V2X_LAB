"""
V4 Universal Policy — Training Entry Point.

Orchestrates the full training pipeline in the correct order:

  Phase 0 (optional): Behavior Cloning warm-start
    - Dijkstra teacher → cross-entropy → GNN policy pre-initialization
    - Lowers initial entropy from 0.05 to 0.02 (prevents destabilization)

  Phase 1: Meta-RL training (choose one)
    A. MAML  — First-order MAML with ICM + HER
    B. PEARL — Amortized meta-RL with TaskEncoder + WorldModel + ICM + HER

Usage:
    # MAML (default)
    python -m app.services.rl.v4.train_v4

    # PEARL
    python -m app.services.rl.v4.train_v4 --trainer pearl

    # With BC warm-start + custom iterations
    python -m app.services.rl.v4.train_v4 --trainer maml --bc --iters 5000

    # Resume from checkpoint
    python -m app.services.rl.v4.train_v4 --trainer maml --resume path/to/ckpt.pt

    # World Model pretraining enabled (PEARL only)
    python -m app.services.rl.v4.train_v4 --trainer pearl --world-model --bc --iters 5000

    # CPU debug run
    python -m app.services.rl.v4.train_v4 --trainer maml --device cpu --iters 20 --bc-tasks 5

Notes:
  - Seed 42 is the default for both train and holdout region splits.
    Change with --seed. Both DomainRandomizer instances must share the
    same seed so the 500/50 train/holdout split is reproducible.
  - Checkpoints are saved to app/services/rl/models/ (MAML) or
    checkpoints/pearl_v4/ (PEARL) by default.
  - The trained policy weights are loadable via:
      UniversalGNNPolicy.load("path/to/ckpt.pt")
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("v4.train")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V4 Universal V2X Policy — Meta-RL Training",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--trainer", choices=["maml", "pearl"], default="maml",
        help="Meta-RL algorithm: MAML (inner-loop gradient) or PEARL (amortized inference)",
    )

    # ── BC phase ────────────────────────────────────────────────────────────────
    bc = p.add_argument_group("Behavior Cloning (Phase 0)")
    bc.add_argument("--bc",        action="store_true", help="Run BC warm-start before meta-RL")
    bc.add_argument("--bc-tasks",  type=int, default=300, help="Dijkstra tasks for BC dataset")
    bc.add_argument("--bc-epochs", type=int, default=30,  help="BC training epochs")

    # ── MAML ────────────────────────────────────────────────────────────────────
    maml = p.add_argument_group("MAML config overrides")
    maml.add_argument("--iters",         type=int,   default=10_000, help="Meta-iterations")
    maml.add_argument("--inner-steps",   type=int,   default=5,      help="Inner SGD steps per task")
    maml.add_argument("--support-eps",   type=int,   default=4,      help="Support episodes per task")
    maml.add_argument("--query-eps",     type=int,   default=4,      help="Query episodes per task")
    maml.add_argument("--tasks-per-iter",type=int,   default=16,     help="Tasks per outer iteration")
    maml.add_argument("--no-icm",        action="store_true", help="Disable ICM")
    maml.add_argument("--no-her",        action="store_true", help="Disable HER")
    maml.add_argument("--ppo-epochs",    type=int,   default=4,      help="PPO gradient epochs per collected batch (GPU util)")
    maml.add_argument("--max-episode-steps", type=int, default=None, help="Max steps per episode (default: MAMLConfig default=100)")
    maml.add_argument("--save-every",        type=int, default=None, help="Checkpoint interval (default: MAMLConfig default=50)")
    maml.add_argument("--compile-model",    action="store_true",    help="torch.compile the GNN policy (A100 recommended, +20-30% throughput)")
    maml.add_argument("--collect-once",     action="store_true",    help="Collect episodes once per inner loop, reuse for all K inner steps (3× less env.step, +30-40% speed)")

    # ── PEARL ───────────────────────────────────────────────────────────────────
    pearl = p.add_argument_group("PEARL config overrides")
    pearl.add_argument("--z-dim",         type=int,   default=16,  help="Task latent dimension")
    pearl.add_argument("--kl-weight",     type=float, default=0.1, help="KL regularisation β")
    pearl.add_argument("--context-steps", type=int,   default=32,  help="Context transitions per task")
    pearl.add_argument("--world-model",   action="store_true",     help="Enable World Model imagination")
    pearl.add_argument("--wm-pretrain-steps", type=int, default=2000,
                       help="World Model offline pretraining gradient steps")

    # ── Common ──────────────────────────────────────────────────────────────────
    p.add_argument("--device",  default="auto", help="cuda / mps / cpu / auto")
    p.add_argument("--seed",    type=int, default=42, help="RNG seed for region split")
    p.add_argument("--resume",  default=None,         help="Path to checkpoint to resume from")
    p.add_argument("--save-dir",default=None,         help="Override checkpoint directory")
    p.add_argument("--config",  default=None,
                   help="JSON file with additional config overrides (applied last)")
    p.add_argument("--workers", type=int, default=1,
                   help="Parallel inner-loop workers (MAML only). >1 requires careful GPU memory.")
    return p.parse_args()


def _resolve_device(device_arg: str) -> str:
    if device_arg != "auto":
        return device_arg
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _load_json_overrides(path: Optional[str]) -> dict:
    if path is None:
        return {}
    with open(path) as f:
        return json.load(f)


# ── Phase 0: Behavior Cloning ──────────────────────────────────────────────────

def run_bc_phase(policy, randomizer, args, json_overrides: dict) -> dict:
    """Run Dijkstra-supervised BC pretraining. Returns bc_result dict."""
    from .bc_pretrain import BCPretrainer, BCConfig

    cfg = BCConfig(
        n_tasks=args.bc_tasks,
        n_epochs=args.bc_epochs,
    )
    # Apply JSON overrides to BCConfig
    for k, v in json_overrides.get("bc", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    log.info("=" * 60)
    log.info("Phase 0: Behavior Cloning  (tasks=%d, epochs=%d)", cfg.n_tasks, cfg.n_epochs)
    log.info("=" * 60)

    bc = BCPretrainer(policy, randomizer, config=cfg, device=args.device)
    result = bc.run()

    if result.get("skipped"):
        log.warning("[BC] Pretraining skipped (empty dataset or missing deps).")
    else:
        log.info(
            "[BC] Complete. acc=%.1f%%  final_loss=%.4f",
            result.get("final_acc", 0) * 100,
            result.get("final_loss", 0),
        )
    return result


# ── Phase 1A: MAML ────────────────────────────────────────────────────────────

def run_maml(args, json_overrides: dict) -> None:
    from .domain_randomizer import DomainRandomizer
    from .maml_trainer import MAMLTrainer, MAMLConfig
    from .universal_gnn_policy import UniversalGNNPolicy

    log.info("=" * 60)
    log.info("Phase 1: FOMAML Training")
    log.info("=" * 60)

    cfg = MAMLConfig(
        meta_iterations=args.iters,
        inner_steps=args.inner_steps,
        support_episodes=args.support_eps,
        query_episodes=args.query_eps,
        n_tasks_per_iter=args.tasks_per_iter,
        use_icm=not args.no_icm,
        use_her=not args.no_her,
        n_workers=args.workers,
        ppo_epochs=args.ppo_epochs,
        device=args.device,
    )
    if args.save_dir:
        cfg.save_dir = args.save_dir
    if args.max_episode_steps is not None:
        cfg.max_episode_steps = args.max_episode_steps
    if args.save_every is not None:
        cfg.save_every = args.save_every
    if args.compile_model:
        cfg.compile_model = True
    if args.collect_once:
        cfg.collect_once = True
    for k, v in json_overrides.get("maml", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    randomizer = DomainRandomizer(train=True, seed=args.seed)
    policy     = UniversalGNNPolicy(z_dim=0)   # MAML: no task-latent conditioning

    bc_result = {}
    if args.bc:
        bc_result = run_bc_phase(policy, randomizer, args, json_overrides)

    trainer = MAMLTrainer(randomizer, config=cfg)

    # Inject BC-pretrained weights into the trainer's meta-model
    if bc_result and not bc_result.get("skipped"):
        src = policy
        dst = (trainer._model._orig_mod
               if hasattr(trainer._model, "_orig_mod") else trainer._model)
        dst.load_state_dict(src.state_dict())
        trainer.bc_apply(bc_result)

    log.info("[MAML] device=%s  iters=%d  tasks/iter=%d  ICM=%s  HER=%s",
             cfg.device, cfg.meta_iterations, cfg.n_tasks_per_iter,
             cfg.use_icm, cfg.use_her)
    trainer.train(resume_from=args.resume)


# ── Phase 1B: PEARL ───────────────────────────────────────────────────────────

def run_pearl(args, json_overrides: dict) -> None:
    from .domain_randomizer import DomainRandomizer
    from .pearl_trainer import PEARLTrainer, PEARLConfig
    from .universal_gnn_policy import UniversalGNNPolicy
    from .world_model import WorldModelTrainer, WorldModelConfig

    log.info("=" * 60)
    log.info("Phase 1: PEARL Training  (z_dim=%d)", args.z_dim)
    log.info("=" * 60)

    cfg = PEARLConfig(
        z_dim=args.z_dim,
        kl_weight=args.kl_weight,
        context_steps=args.context_steps,
        use_world_model=args.world_model,
        n_meta_iters=args.iters,
        device=args.device,
    )
    if args.save_dir:
        cfg.checkpoint_dir = args.save_dir
    for k, v in json_overrides.get("pearl", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    randomizer = DomainRandomizer(train=True, seed=args.seed)
    policy     = UniversalGNNPolicy(z_dim=cfg.z_dim)

    bc_result = {}
    if args.bc:
        bc_result = run_bc_phase(policy, randomizer, args, json_overrides)
        if bc_result and not bc_result.get("skipped"):
            overrides = bc_result.get("maml_overrides", {})
            if "ppo_entropy_coef_start" in overrides:
                cfg.ppo_entropy_coef_start = overrides["ppo_entropy_coef_start"]
                log.info("[PEARL] BC applied: entropy_start → %.3f",
                         cfg.ppo_entropy_coef_start)

    # ── World Model (optional) ──────────────────────────────────────────────────
    wm_trainer = None
    if args.world_model:
        log.info("[WorldModel] Initialising...")
        wm_cfg    = WorldModelConfig()
        for k, v in json_overrides.get("world_model", {}).items():
            if hasattr(wm_cfg, k):
                setattr(wm_cfg, k, v)
        wm_trainer = WorldModelTrainer(config=wm_cfg, device=args.device)
        # World Model Stage 1 pretraining runs AFTER initial PEARL data collection.
        # If a pretrain_steps override forces early pretraining, do it here.
        if json_overrides.get("world_model", {}).get("pretrain_before_pearl", False):
            log.info("[WorldModel] Stage 1 pretraining (%d steps)...",
                     wm_cfg.pretrain_steps)
            wm_trainer.pretrain()

    # ── ICM (reuse same module across PEARL's outer loop) ──────────────────────
    icm = None
    if cfg.use_icm:
        from .universal_gnn_policy import IntrinsicCuriosityModule
        icm = IntrinsicCuriosityModule(hidden=64)

    trainer = PEARLTrainer(
        config=cfg,
        policy=policy,
        randomizer=randomizer,
        world_model_trainer=wm_trainer,
        icm=icm,
        device=args.device,
    )

    if args.resume:
        trainer.load_checkpoint(args.resume)

    log.info(
        "[PEARL] device=%s  iters=%d  z_dim=%d  kl_weight=%.3f  "
        "WorldModel=%s  ICM=%s  HER=%s  BC=%s",
        args.device, cfg.n_meta_iters, cfg.z_dim, cfg.kl_weight,
        args.world_model, cfg.use_icm, cfg.use_her, bool(args.bc),
    )
    trainer.train()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_and_validate()
    json_overrides = _load_json_overrides(args.config)

    t0 = time.time()
    try:
        if args.trainer == "maml":
            run_maml(args, json_overrides)
        else:
            run_pearl(args, json_overrides)
    except KeyboardInterrupt:
        log.info("Training interrupted by user after %.0fs.", time.time() - t0)
        sys.exit(0)
    except Exception:
        log.exception("Training failed.")
        sys.exit(1)

    log.info("Total wall time: %.0fs", time.time() - t0)


def _validate_resume_checkpoint(args: argparse.Namespace) -> None:
    """
    When resuming, verify the checkpoint z_dim matches --z-dim.
    A mismatch silently corrupts training (wrong critic input dimension).
    """
    if not args.resume:
        return
    path = args.resume
    try:
        import torch
        state = torch.load(path, map_location="cpu")
        cfg_z = state.get("config", {}).get("z_dim")
        if cfg_z is None:
            # Try to detect from critic weight shape
            policy_sd = (state.get("state_dict")
                         or state.get("policy")
                         or state.get("policy_state"))
            if policy_sd is not None:
                from .universal_gnn_policy import GNN_OUT, GLOBAL_CTX_DIM
                w = policy_sd.get("critic_mlp.0.weight")
                if w is not None:
                    cfg_z = max(0, int(w.shape[1]) - (3 * GNN_OUT + GLOBAL_CTX_DIM))
        if cfg_z is not None:
            expected_z = args.z_dim if args.trainer == "pearl" else 0
            if int(cfg_z) != expected_z:
                log.error(
                    "Checkpoint z_dim=%d does not match --z-dim=%d "
                    "(trainer=%s). Aborting to prevent silent mismatch.",
                    cfg_z, expected_z, args.trainer,
                )
                sys.exit(1)
            log.info("[resume] checkpoint z_dim=%d matches args ✓", cfg_z)
    except Exception as e:
        log.warning("[resume] could not validate checkpoint z_dim: %s", e)


def parse_and_validate() -> argparse.Namespace:
    args = _parse_args()
    args.device = _resolve_device(args.device)

    # Validate mutual exclusions
    if args.world_model and args.trainer != "pearl":
        log.error("--world-model is only supported with --trainer pearl")
        sys.exit(1)

    _validate_resume_checkpoint(args)

    log.info("V4 Training Configuration:")
    log.info("  trainer    = %s", args.trainer)
    log.info("  device     = %s", args.device)
    log.info("  seed       = %d", args.seed)
    log.info("  BC warmup  = %s", args.bc)
    log.info("  iters      = %d", args.iters)
    if args.trainer == "pearl":
        log.info("  z_dim      = %d", args.z_dim)
        log.info("  world_model= %s", args.world_model)
    return args


if __name__ == "__main__":
    main()
