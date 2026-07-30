"""
V4 Universal Policy — Domain Randomization + GNN + Meta-RL

Components:
  aoi_tracker         — Age of Information reward term
  domain_randomizer   — 500-region Korean OSM task sampler
  universal_gnn_policy — GATv2Conv actor-critic (variable graph)
  universal_v2x_env   — Gym env with domain-randomized episodes
  maml_trainer        — First-order MAML meta-RL trainer
  statistical_validator — N=30 multi-seed KPI evaluation
"""
from .aoi_tracker import AoITracker, AOI_THRESHOLD_MS
from .domain_randomizer import DomainRandomizer, V2XTask
from .universal_v2x_env import UniversalV2XEnv
from .statistical_validator import StatisticalValidator, run_n_seeds
from .maml_trainer import MAMLTrainer, MAMLConfig

try:
    from .universal_gnn_policy import UniversalGNNPolicy, build_graph_obs, MAX_CANDIDATES
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
    __all__ += ["UniversalGNNPolicy", "build_graph_obs", "MAX_CANDIDATES"]
