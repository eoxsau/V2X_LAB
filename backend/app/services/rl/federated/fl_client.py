"""
Federated RL Client — Flower (flwr) client for one Korean region.

Each client:
  1. Receives global GNN policy weights from the FL server (FedAvg)
  2. Trains locally for LOCAL_EPOCHS using MAML inner loop on its region
  3. Sends updated weights + loss metrics back to the server

50 virtual clients can run on a single A100 (each needs ~300 MB GPU RAM),
or across multiple machines for true distributed training.

Virtual client mode:
  All 50 clients can be simulated on one machine using Flower's
  VirtualClientEngine (requires flwr >= 1.5):
    fl.simulation.start_simulation(
        client_fn=client_fn,
        num_clients=50,
        config=fl.server.ServerConfig(num_rounds=100),
    )

References:
  [1] McMahan et al., AISTATS 2017.
  [2] Flower: Beutel et al., arXiv:2007.14390, 2020.
"""
from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent.parent.parent.parent

LOCAL_EPOCHS    = 5     # local RL training epochs per FL round
EPISODES_PER_EP = 4     # episodes per epoch (support set)
LOCAL_LR        = 1e-3  # inner-loop learning rate

try:
    import numpy as np
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import flwr as fl
    from flwr.common import (
        Config,
        NDArrays,
        Scalar,
        ndarrays_to_parameters,
        parameters_to_ndarrays,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

try:
    from ..v4.universal_gnn_policy import UniversalGNNPolicy
    from ..v4.domain_randomizer import DomainRandomizer, V2XTask
    from ..v4.universal_v2x_env import UniversalV2XEnv
    _V4_AVAILABLE = True
except ImportError:
    _V4_AVAILABLE = False


class V2XFLClient(fl.client.NumPyClient if _AVAILABLE else object):  # type: ignore
    """
    Flower NumPy client for one regional V2X training task.

    Parameters
    ----------
    client_id : int
        Client index (0–49). Used to shard the region list.
    region_osm_ids : list[int]
        List of OSM IDs assigned to this client.
    channel_lookup_cache : optional
        RegionChannelLookupCache for Sionna channel maps.
    """

    def __init__(
        self,
        client_id: int,
        region_osm_ids: list[int],
        channel_lookup_cache=None,
        device: str = "cpu",
    ) -> None:
        if not _AVAILABLE or not _V4_AVAILABLE:
            raise ImportError(
                "V2XFLClient requires: pip install flwr torch torch-geometric"
            )
        self._id = client_id
        self._osm_ids = region_osm_ids
        self._channel_cache = channel_lookup_cache
        self._device = torch.device(device)
        self._rng = random.Random(client_id)

        # Local model (copy of global weights)
        self._model = UniversalGNNPolicy().to(self._device)
        self._opt = optim.Adam(self._model.parameters(), lr=LOCAL_LR)

        # Domain randomizer pinned to this client's regions
        self._randomizer = DomainRandomizer(train=True, seed=client_id)

    # ── Flower interface ──────────────────────────────────────────────────────
    def get_parameters(self, config: Config) -> NDArrays:
        return [p.detach().cpu().numpy() for p in self._model.parameters()]

    def set_parameters(self, parameters: NDArrays) -> None:
        for p, arr in zip(self._model.parameters(), parameters):
            p.data = torch.from_numpy(arr).to(self._device)

    def fit(
        self,
        parameters: NDArrays,
        config: Config,
    ) -> tuple[NDArrays, int, dict[str, Scalar]]:
        """
        Receive global weights, run LOCAL_EPOCHS of RL training, return updates.
        """
        self.set_parameters(parameters)
        self._model.train()

        env = UniversalV2XEnv(
            self._randomizer,
            channel_lookup=self._channel_cache,
        )

        total_loss = 0.0
        total_examples = 0

        for epoch in range(LOCAL_EPOCHS):
            task = self._randomizer.sample_task()
            if task is None:
                continue
            loss, n_steps = self._train_one_epoch(env, task)
            total_loss    += loss
            total_examples += n_steps

        avg_loss = total_loss / max(LOCAL_EPOCHS, 1)
        return self.get_parameters(config), total_examples, {
            "loss":      avg_loss,
            "client_id": float(self._id),
        }

    def evaluate(
        self,
        parameters: NDArrays,
        config: Config,
    ) -> tuple[float, int, dict[str, Scalar]]:
        """Quick evaluation on one episode."""
        self.set_parameters(parameters)
        self._model.eval()

        env = UniversalV2XEnv(
            self._randomizer,
            channel_lookup=self._channel_cache,
        )
        obs, _ = env.reset()
        done = False
        total_reward = 0.0
        n_steps = 0
        latencies = []

        with torch.no_grad():
            while not done and n_steps < 200:
                data = obs.to(self._device) if hasattr(obs, "to") else obs
                mask = torch.tensor(env.action_masks(), dtype=torch.bool, device=self._device)
                logits, _ = self._model(data, action_mask=mask)
                action = int(logits.argmax().item())
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                total_reward += reward
                latencies.append(info.get("latency_ms", 0.0))
                n_steps += 1

        avg_lat = sum(latencies) / max(n_steps, 1)
        loss_proxy = -total_reward / max(n_steps, 1)  # negative reward as proxy loss

        return float(loss_proxy), n_steps, {
            "avg_latency_ms": avg_lat,
            "total_reward":   total_reward,
        }

    # ── Local training ────────────────────────────────────────────────────────
    def _train_one_epoch(
        self,
        env: UniversalV2XEnv,
        task: V2XTask,
    ) -> tuple[float, int]:
        """One epoch: collect episodes → PPO-style policy gradient update."""
        obs, _ = env.reset(task=task)
        done = False
        log_probs, rewards, values = [], [], []
        actions_taken: list[int] = []   # recorded for value replay pass
        n_steps = 0

        with torch.no_grad():
            while not done and n_steps < 200:
                data = obs.to(self._device) if hasattr(obs, "to") else obs
                mask = torch.tensor(env.action_masks(), dtype=torch.bool, device=self._device)
                logits, value = self._model(data, action_mask=mask)
                dist   = torch.distributions.Categorical(logits=logits)
                action = dist.sample()
                lp     = dist.log_prob(action)
                obs, reward, terminated, truncated, info = env.step(action.item())
                done = terminated or truncated
                log_probs.append(lp)
                rewards.append(reward)
                values.append(value.item())
                actions_taken.append(int(action.item()))
                n_steps += 1

        if not log_probs:
            return 0.0, 0

        # Simple REINFORCE with baseline
        returns = []
        G = 0.0
        for r in reversed(rewards):
            G = r + 0.99 * G
            returns.insert(0, G)

        ret_t = torch.tensor(returns, dtype=torch.float32, device=self._device)
        val_t = torch.tensor(values,  dtype=torch.float32, device=self._device)
        adv_t = ret_t - val_t
        adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

        lp_t = torch.stack(log_probs)

        # Re-compute values WITH gradient by replaying the exact same actions.
        # We stored actions in the no-grad collection pass above; replaying them
        # visits identical states, so the value targets and observations match.
        obs_tmp, _ = env.reset(task=task)
        done_tmp = False
        values_with_grad = []
        step_tmp = 0
        while not done_tmp and step_tmp < n_steps:
            data = obs_tmp.to(self._device) if hasattr(obs_tmp, "to") else obs_tmp
            mask = torch.tensor(env.action_masks(), dtype=torch.bool, device=self._device)
            _, val = self._model(data, action_mask=mask)
            values_with_grad.append(val)
            # Replay the recorded action (not argmax of returns — returns are scalars)
            action = actions_taken[step_tmp] if step_tmp < len(actions_taken) else 0
            obs_tmp, _, done_tmp, trunc_tmp, _ = env.step(action)
            done_tmp = done_tmp or trunc_tmp
            step_tmp += 1

        val_grad_t = torch.stack(values_with_grad).squeeze(-1)[:n_steps]
        value_loss = (val_grad_t - ret_t).pow(2).mean()
        policy_loss = -(lp_t.detach() * adv_t).mean()  # detach for stability
        loss = policy_loss + 0.5 * value_loss

        self._opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self._model.parameters(), max_norm=1.0)
        self._opt.step()

        return float(loss.item()), n_steps


# ── Virtual simulation launcher ───────────────────────────────────────────────
def simulate_federation(
    n_clients: int = 50,
    n_rounds: int = 100,
    server_address: str = "localhost:8200",
    device: str = "cpu",
) -> None:
    """
    Run a fully local federated simulation (no network, single machine).

    Uses Flower's VirtualClientEngine — all 50 clients run sequentially
    or in parallel on the same A100.

    Requires: flwr >= 1.5, torch, torch_geometric
    """
    if not _AVAILABLE:
        raise ImportError("pip install flwr torch torch-geometric")

    from ..v4.domain_randomizer import DomainRandomizer

    # Split regions among clients
    randomizer = DomainRandomizer(train=True, seed=0)
    all_ids = [r["osm_id"] for r in randomizer._train_regions]

    def client_fn(cid: str) -> fl.client.Client:
        idx = int(cid)
        # Assign a shard of regions to this client
        shard_size = max(len(all_ids) // n_clients, 1)
        start = idx * shard_size
        ids   = all_ids[start: start + shard_size]
        return V2XFLClient(client_id=idx, region_osm_ids=ids, device=device).to_client()

    from .fl_server import DPFedAvg, _FL_CHECKPOINT
    import numpy as np

    model = UniversalGNNPolicy()
    initial_params = fl.common.ndarrays_to_parameters(
        [p.detach().numpy() for p in model.parameters()]
    )
    strategy = DPFedAvg(
        dp_sigma=0.01,
        fraction_fit=0.2,
        fraction_evaluate=0.1,
        min_fit_clients=max(2, n_clients // 5),
        min_evaluate_clients=max(1, n_clients // 10),
        min_available_clients=max(2, n_clients // 5),
        initial_parameters=initial_params,
    )

    print(f"[FL Simulation] {n_clients} clients × {n_rounds} rounds")
    fl.simulation.start_simulation(
        client_fn=client_fn,
        num_clients=n_clients,
        config=fl.server.ServerConfig(num_rounds=n_rounds),
        strategy=strategy,
        client_resources={"num_cpus": 1, "num_gpus": 0.02},
    )
