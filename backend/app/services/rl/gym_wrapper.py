"""
V2XGymEnv — gymnasium wrapper for V2XRoutingEnv.

MaskablePPO (sb3-contrib) 호환:
  • observation_space : Box(37,)  float32  0–1 정규화
  • action_space      : Discrete(5)
  • action_masks()    : 유효한 도로 엣지만 True → 불가능한 행동 확률 0

Curriculum 지원:
  • set_curriculum_stage(stage) → 환경 파라미터 조정 (max_steps, 보상 가중치)
  • stage 0: 단거리/저부하 (수렴 용이)
  • stage 1: 중거리/중부하
  • stage 2: 장거리/고부하/건물 차폐 포함

사용 예:
    env = V2XGymEnv(graph, road_nodes, bs_nodes, origin_id, dest_id)
    model = MaskablePPO("MlpPolicy", env, verbose=1, device="auto")
    model.learn(100_000)
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import gymnasium as gym

from app.services.rl.v2x_routing_env import (
    V2XRoutingEnv,
    MAX_CANDIDATES,
    STATE_DIM,
    RewardWeights,
    DEFAULT_REWARD_WEIGHTS,
)

# ── Curriculum 단계별 파라미터 ─────────────────────────────────────────────────
_CURRICULUM_STAGES: list[dict] = [
    # Stage 0: 짧은 경로, 낮은 부하 → 빠른 초기 수렴
    {
        "max_steps": 80,
        "reward_weights": RewardWeights(
            w_goal_progress=2.0,
            w_distance=-0.3,
            w_latency=-0.5,
            w_bs_load=-0.3,
            w_handover=-0.3,
            w_blockage=-0.2,
            w_disconnection=-1.5,
            w_future_risk=-0.5,
            w_arrival=30.0,
            w_timeout=-5.0,
        ),
    },
    # Stage 1: 중간 경로, 중간 부하
    {
        "max_steps": 150,
        "reward_weights": RewardWeights(
            w_goal_progress=1.5,
            w_distance=-0.4,
            w_latency=-0.8,
            w_bs_load=-0.4,
            w_handover=-0.4,
            w_blockage=-0.3,
            w_disconnection=-2.0,
            w_future_risk=-0.8,
            w_arrival=25.0,
            w_timeout=-8.0,
        ),
    },
    # Stage 2: 장거리, 고부하, 건물 차폐 포함 (논문 최종 설정)
    {
        "max_steps": 200,
        "reward_weights": DEFAULT_REWARD_WEIGHTS,
    },
]


class V2XGymEnv(gym.Env):
    """
    gymnasium.Env 래퍼 — stable-baselines3 MaskablePPO/DQN 호환.

    Parameters
    ----------
    graph       : {"nodes": {id: {lat, lng}}, "adjacency": {id: [(neighbor, dist_m)]}}
    road_nodes  : graph["nodes"]
    bs_nodes    : [{id, name, lat, lng, capacity, load, coverage_radius_m, ...}]
    origin_id   : 출발 노드 ID
    dest_id     : 도착 노드 ID
    curriculum_stage : 0 / 1 / 2  (기본값 2 = 최종 설정)
    buildings_gdf    : GeoDataFrame (건물 차폐; None이면 차폐 없음)
    allocation_output: 자원 할당 결과 dict (선택)
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        graph: dict,
        road_nodes: dict,
        bs_nodes: list[dict],
        origin_id: str,
        dest_id: str,
        *,
        curriculum_stage: int = 2,
        buildings_gdf: Any = None,
        allocation_output: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._graph = graph
        self._road_nodes = road_nodes
        self._bs_nodes = bs_nodes
        self._origin_id = origin_id
        self._dest_id = dest_id
        self._buildings_gdf = buildings_gdf
        self._allocation_output = allocation_output

        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(STATE_DIM,), dtype=np.float32
        )
        self.action_space = gym.spaces.Discrete(MAX_CANDIDATES)

        self._stage = -1
        self._env: Optional[V2XRoutingEnv] = None
        self.set_curriculum_stage(curriculum_stage)

    # ── Curriculum ────────────────────────────────────────────────────────────
    def set_curriculum_stage(self, stage: int) -> None:
        stage = max(0, min(stage, len(_CURRICULUM_STAGES) - 1))
        if stage == self._stage:
            return
        self._stage = stage
        cfg = _CURRICULUM_STAGES[stage]
        self._env = V2XRoutingEnv(
            graph=self._graph,
            road_nodes=self._road_nodes,
            bs_nodes=self._bs_nodes,
            origin_id=self._origin_id,
            dest_id=self._dest_id,
            buildings_gdf=self._buildings_gdf,
            max_steps=cfg["max_steps"],
            reward_weights=cfg["reward_weights"],
            allocation_output=self._allocation_output,
        )

    @property
    def curriculum_stage(self) -> int:
        return self._stage

    # ── gymnasium API ─────────────────────────────────────────────────────────
    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        super().reset(seed=seed)
        obs = self._env.reset()
        return np.array(obs, dtype=np.float32), {}

    def step(self, action: int):
        r = self._env.step(int(action))
        obs = np.array(r.state_vector, dtype=np.float32)
        terminated = r.done
        truncated = False
        return obs, float(r.reward), terminated, truncated, r.info

    def action_masks(self) -> np.ndarray:
        """MaskablePPO가 호출하는 유효 행동 마스크 (True = 선택 가능)."""
        mask = np.zeros(MAX_CANDIDATES, dtype=bool)
        for i in self._env.valid_actions():
            if i < MAX_CANDIDATES:
                mask[i] = True
        # 유효 행동이 전혀 없으면 첫 번째 행동 허용 (환경이 done 처리)
        if not mask.any():
            mask[0] = True
        return mask

    def render(self):
        pass

    def close(self):
        pass
