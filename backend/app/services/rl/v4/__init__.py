"""
V4 Universal Policy — Domain Randomization + GNN + Meta-RL

Components:
  aoi_tracker           — Age of Information reward term
  domain_randomizer     — 500-region Korean OSM task sampler
  universal_gnn_policy  — GATv2Conv actor-critic (variable graph) + ICM
  universal_v2x_env     — Gym env with domain-randomized episodes + HER
  maml_trainer          — First-order MAML meta-RL trainer (ICM, HER, BC)
  bc_pretrain           — Dijkstra behavior cloning warm-start
  world_model           — Dreamer-style latent dynamics for imagination
  pearl_trainer         — PEARL amortized meta-RL trainer
  statistical_validator — N=30 multi-seed KPI evaluation
"""
from .aoi_tracker import AoITracker, AOI_THRESHOLD_MS
from .domain_randomizer import DomainRandomizer, V2XTask
from .universal_v2x_env import UniversalV2XEnv
from .statistical_validator import StatisticalValidator, run_n_seeds
from .maml_trainer import MAMLTrainer, MAMLConfig

try:
    from .universal_gnn_policy import (
        UniversalGNNPolicy, IntrinsicCuriosityModule,
        build_graph_obs, MAX_CANDIDATES,
    )
    from .bc_pretrain import BCPretrainer, BCConfig
    from .world_model import WorldModel, WorldModelTrainer, WorldModelConfig, TransitionBuffer
    from .pearl_trainer import PEARLTrainer, PEARLConfig, TaskEncoder
    _GNN_AVAILABLE = True
except ImportError:
    _GNN_AVAILABLE = False

__all__ = [
    "AoITracker", "AOI_THRESHOLD_MS",
    "DomainRandomizer", "V2XTask",
    "UniversalV2XEnv",
    "StatisticalValidator", "run_n_seeds",
    "MAMLTrainer", "MAMLConfig",
]
if _GNN_AVAILABLE:
    __all__ += [
        "UniversalGNNPolicy", "IntrinsicCuriosityModule",
        "build_graph_obs", "MAX_CANDIDATES",
        "BCPretrainer", "BCConfig",
        "WorldModel", "WorldModelTrainer", "WorldModelConfig", "TransitionBuffer",
        "PEARLTrainer", "PEARLConfig", "TaskEncoder",
    ]
