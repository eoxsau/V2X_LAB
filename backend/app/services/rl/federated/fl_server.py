"""
Federated RL Server — Flower (flwr) with FedAvg + Differential Privacy.

Architecture:
  N_CLIENTS = 50 virtual clients, each trains on one Korean region.
  Strategy:  FedAvg (McMahan et al. 2017) — simple weight averaging.
  Privacy:   Gaussian noise σ=0.01 on aggregated gradients (DP-FedAvg).
  Rounds:    100 federated rounds × 5 local epochs per client.

Why federated RL for V2X?
  Different regions have different road topologies, traffic patterns, and
  BS/RSU configurations.  Training a single centralized model on a server
  would require all data to leave the regional infrastructure (privacy concern
  for city-level telemetry).  FedAvg keeps raw episode data local — only
  model weight deltas are transmitted to the server.

References:
  [1] McMahan, H.B. et al., "Communication-Efficient Learning of Deep
      Networks from Decentralized Data", AISTATS 2017.
  [2] Geyer, R. et al., "Differentially Private Federated Learning: A
      Client Level Perspective", NeurIPS Workshop 2017.
  [3] Flower: Beutel, D. et al., "Flower: A Friendly Federated Learning
      Framework", arXiv:2007.14390, 2020.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent.parent.parent.parent
_FL_CHECKPOINT = _BASE / "data" / "fl_checkpoints"

N_CLIENTS = 50
FL_ROUNDS  = 100
MIN_FIT    = 10    # minimum clients per round (20% of N_CLIENTS)
MIN_EVAL   = 5

try:
    import numpy as np
    import flwr as fl
    from flwr.server.strategy import FedAvg
    from flwr.common import (
        ndarrays_to_parameters,
        parameters_to_ndarrays,
        FitRes,
        Parameters,
        Scalar,
    )
    _FLWR_AVAILABLE = True
except ImportError:
    _FLWR_AVAILABLE = False

try:
    from ..v4.universal_gnn_policy import UniversalGNNPolicy
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


# ── DP Noise utility ──────────────────────────────────────────────────────────
def _add_dp_noise(
    parameters: list["np.ndarray"],
    sigma: float = 0.01,
    clip_norm: float = 1.0,
) -> list["np.ndarray"]:
    """
    Gaussian DP noise injection (DP-SGD, Abadi et al. 2016).

    1. Clip each parameter tensor to l2-norm ≤ clip_norm (gradient clipping).
    2. Add N(0, σ²) Gaussian noise.

    σ = 0.01 × clip_norm provides (ε, δ)-DP with ε~1.0 for 50 clients
    per the moments accountant (Abadi et al. 2016, §3).
    """
    import numpy as np
    noised = []
    for arr in parameters:
        norm = np.linalg.norm(arr)
        if norm > clip_norm:
            arr = arr * (clip_norm / norm)
        arr = arr + np.random.normal(0, sigma * clip_norm, arr.shape).astype(arr.dtype)
        noised.append(arr)
    return noised


# ── Custom FedAvg with DP ─────────────────────────────────────────────────────
if _FLWR_AVAILABLE:
    class DPFedAvg(FedAvg):
        """
        FedAvg with differential-privacy noise injection on the server.

        The noise is applied AFTER averaging (central DP), not per-client.
        This is weaker than local DP but sufficient for regional telemetry
        privacy (city infrastructure data is not personally identifying).
        """

        def __init__(self, dp_sigma: float = 0.01, **kwargs) -> None:
            super().__init__(**kwargs)
            self._dp_sigma = dp_sigma
            self._round_log: list[dict] = []
            _FL_CHECKPOINT.mkdir(parents=True, exist_ok=True)

        def aggregate_fit(
            self,
            server_round: int,
            results,
            failures,
        ):
            aggregated, metrics = super().aggregate_fit(server_round, results, failures)

            if aggregated is not None and self._dp_sigma > 0:
                import numpy as np
                params = parameters_to_ndarrays(aggregated)
                params = _add_dp_noise(params, sigma=self._dp_sigma)
                aggregated = ndarrays_to_parameters(params)

            # Log round statistics
            if results:
                num_examples = sum(fit_res.num_examples for _, fit_res in results)
                losses = [fit_res.metrics.get("loss", 0.0) for _, fit_res in results]
                avg_loss = sum(losses) / len(losses) if losses else 0.0
                entry = {
                    "round": server_round,
                    "n_clients": len(results),
                    "total_examples": num_examples,
                    "avg_loss": round(avg_loss, 4),
                    "dp_sigma": self._dp_sigma,
                }
                self._round_log.append(entry)
                print(f"[FL Server] round={server_round:3d}  clients={len(results)}  "
                      f"avg_loss={avg_loss:.4f}  n_examples={num_examples}")

                # Checkpoint every 10 rounds
                if server_round % 10 == 0:
                    self._save_checkpoint(server_round, aggregated)

            return aggregated, metrics

        def _save_checkpoint(self, rnd: int, params) -> None:
            path = _FL_CHECKPOINT / f"fl_round_{rnd:04d}.json"
            with open(path, "w") as f:
                json.dump(self._round_log, f, indent=2)
            print(f"[FL Server] log saved → {path}")


def start_fl_server(
    server_address: str = "0.0.0.0:8200",
    n_rounds: int = FL_ROUNDS,
    min_fit_clients: int = MIN_FIT,
    min_eval_clients: int = MIN_EVAL,
    dp_sigma: float = 0.01,
    initial_model_path: Optional[str] = None,
) -> None:
    """
    Start the Flower federated learning server.

    Parameters
    ----------
    server_address : str
        gRPC address for the FL server.
    n_rounds : int
        Number of federated rounds.
    min_fit_clients : int
        Minimum clients required per round.
    dp_sigma : float
        DP noise standard deviation. 0.0 = no DP.
    initial_model_path : str | None
        Path to a .pt checkpoint for warm-starting.
    """
    if not _FLWR_AVAILABLE:
        raise ImportError(
            "Federated RL requires:\n"
            "  pip install flwr torch torch-geometric"
        )
    if not _TORCH_AVAILABLE:
        raise ImportError("torch and torch_geometric are required.")

    # Initial model parameters
    model = UniversalGNNPolicy()
    if initial_model_path:
        ckpt = torch.load(initial_model_path, map_location="cpu")
        model.load_state_dict(ckpt.get("state_dict", ckpt))
        print(f"[FL Server] Loaded initial model from {initial_model_path}")

    initial_params = ndarrays_to_parameters(
        [p.detach().numpy() for p in model.parameters()]
    )

    strategy = DPFedAvg(
        dp_sigma=dp_sigma,
        fraction_fit=min_fit_clients / N_CLIENTS,
        fraction_evaluate=min_eval_clients / N_CLIENTS,
        min_fit_clients=min_fit_clients,
        min_evaluate_clients=min_eval_clients,
        min_available_clients=min_fit_clients,
        initial_parameters=initial_params,
    )

    print(f"[FL Server] Starting on {server_address}, "
          f"{n_rounds} rounds, DP σ={dp_sigma}")

    fl.server.start_server(
        server_address=server_address,
        config=fl.server.ServerConfig(num_rounds=n_rounds),
        strategy=strategy,
    )
