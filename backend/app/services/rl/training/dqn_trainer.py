"""
DQN Training Pipeline — PPO 비교 실험용.

알고리즘: DQN (Double DQN, stable-baselines3)
  • Experience Replay Buffer
  • Target Network (soft update)
  • Epsilon-Greedy 탐색

DQN vs PPO 비교가 논문의 핵심 실험 중 하나.
  - DQN: 오프-폴리시, 샘플 효율 높음, 불안정할 수 있음
  - PPO: 온-폴리시, 안정적, action masking 지원으로 V2X에 적합

참고 문헌:
  • Mnih et al., "Human-level control through deep RL", Nature 2015 (DQN)
  • Van Hasselt et al., "Deep Reinforcement Learning with Double Q-learning", AAAI 2016
  • stable-baselines3 DQN: https://stable-baselines3.readthedocs.io/en/master/modules/dqn.html

주의: DQN은 action masking을 기본 지원하지 않으므로,
  유효하지 않은 행동에 큰 음수 Q-값을 부여하는 방식으로 마스킹 근사.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    import torch
    from stable_baselines3 import DQN
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import DummyVecEnv
    _SB3_AVAILABLE = True
except ImportError:
    _SB3_AVAILABLE = False

from app.services.rl.gym_wrapper import V2XGymEnv
from app.services.rl.v2x_routing_env import MAX_CANDIDATES

MODELS_DIR = Path(__file__).parent.parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

_MASK_PENALTY = -1e6   # 유효하지 않은 행동 Q-값 패널티


# ── Action-Masked DQN 래퍼 ─────────────────────────────────────────────────────
class ActionMaskedDQNWrapper(V2XGymEnv):
    """
    DQN용 래퍼 — 유효하지 않은 행동을 선택하면 큰 음수 보상으로 패널티.

    DQN은 Q-값 학습 과정에서 자연스럽게 유효하지 않은 행동을 기피하게 된다.
    step() 호출 시 유효하지 않은 action_idx이면 -10 보상 + 동일 상태 유지.
    """

    def step(self, action: int):
        valid = list(self._env.valid_actions())
        if valid and int(action) not in valid:
            # 유효하지 않은 행동 → 패널티 보상, 상태 유지
            obs = np.array(self._env.get_state(), dtype=np.float32)
            return obs, -5.0, False, False, {"invalid_action": True}
        return super().step(action)


# ── 진행률 콜백 ────────────────────────────────────────────────────────────────
class DQNProgressCallback(BaseCallback):
    def __init__(
        self,
        total_timesteps: int,
        progress_fn: Optional[Callable[[dict], None]] = None,
        report_every: int = 2_000,
    ):
        super().__init__(verbose=0)
        self._total = total_timesteps
        self._fn = progress_fn
        self._every = report_every
        self._last = 0

    def _on_step(self) -> bool:
        if self.num_timesteps - self._last >= self._every and self._fn:
            self._last = self.num_timesteps
            ep_info = self.model.ep_info_buffer
            mean_rew = float(np.mean([e["r"] for e in ep_info])) if ep_info else 0.0
            self._fn({
                "algorithm": "DQN",
                "timesteps": self.num_timesteps,
                "total": self._total,
                "pct": round(self.num_timesteps / self._total * 100, 1),
                "mean_reward": round(mean_rew, 3),
                "epsilon": round(self.model.exploration_rate, 4),
            })
        return True


# ── 핵심 학습 함수 ─────────────────────────────────────────────────────────────
def train_dqn(
    graph: dict,
    bs_nodes: list[dict],
    *,
    total_timesteps: int = 200_000,
    model_name: str = "dqn_v2x_route",
    device: str = "auto",
    learning_rate: float = 1e-4,
    buffer_size: int = 50_000,
    batch_size: int = 128,
    gamma: float = 0.99,
    exploration_fraction: float = 0.3,
    exploration_final_eps: float = 0.05,
    target_update_interval: int = 1_000,
    buildings_gdf=None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    origin_id: Optional[str] = None,
    dest_id: Optional[str] = None,
) -> dict:
    """
    DQN으로 V2X 경로 선택 에이전트를 학습. PPO 비교용.

    Parameters
    ----------
    exploration_fraction : 전체 timesteps 중 epsilon 감소 구간 비율
    target_update_interval : target network 업데이트 주기 (timesteps)

    Returns
    -------
    dict with: model_path, total_timesteps, training_time_s,
               final_mean_reward, device_used, final_epsilon
    """
    if not _SB3_AVAILABLE:
        raise ImportError("stable-baselines3, torch가 필요합니다.")

    road_nodes = graph.get("nodes", {})
    if not road_nodes:
        raise ValueError("graph['nodes']가 비어 있습니다.")

    node_ids = list(road_nodes.keys())
    if origin_id is None or origin_id not in road_nodes:
        origin_id = node_ids[0]
    if dest_id is None or dest_id not in road_nodes or dest_id == origin_id:
        dest_id = node_ids[len(node_ids) // 2]

    # ── 디바이스 선택 ──────────────────────────────────────────────────────────
    if device == "auto":
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            device_used = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device_used = "mps"
        else:
            device_used = "cpu"
    else:
        device_used = device

    env = ActionMaskedDQNWrapper(
        graph, road_nodes, bs_nodes,
        origin_id, dest_id,
        curriculum_stage=2,
        buildings_gdf=buildings_gdf,
    )

    policy_kwargs = dict(net_arch=[256, 256, 128])
    model = DQN(
        "MlpPolicy",
        env,
        learning_rate=learning_rate,
        buffer_size=buffer_size,
        batch_size=batch_size,
        gamma=gamma,
        exploration_fraction=exploration_fraction,
        exploration_final_eps=exploration_final_eps,
        target_update_interval=target_update_interval,
        policy_kwargs=policy_kwargs,
        tensorboard_log=str(MODELS_DIR / "tb_logs"),
        device=device_used,
        verbose=0,
        optimize_memory_usage=False,
    )

    callback = DQNProgressCallback(
        total_timesteps=total_timesteps,
        progress_fn=progress_callback,
        report_every=2_000,
    )

    start = time.time()
    model.learn(
        total_timesteps=total_timesteps,
        callback=callback,
        tb_log_name=model_name,
        reset_num_timesteps=True,
    )
    elapsed = round(time.time() - start, 1)

    save_path = MODELS_DIR / model_name
    model.save(str(save_path))

    ep_info = model.ep_info_buffer
    mean_rew = float(np.mean([e["r"] for e in ep_info])) if ep_info else 0.0

    result = {
        "model_path": str(save_path) + ".zip",
        "model_name": model_name,
        "algorithm": "DQN",
        "total_timesteps": total_timesteps,
        "training_time_s": elapsed,
        "final_mean_reward": round(mean_rew, 3),
        "final_epsilon": round(model.exploration_rate, 4),
        "device_used": device_used,
        "policy_net_arch": policy_kwargs["net_arch"],
        "replay_buffer_size": buffer_size,
        "origin_id": origin_id,
        "dest_id": dest_id,
    }

    if progress_callback:
        progress_callback({"done": True, **result})

    return result
