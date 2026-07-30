"""
PPO Training Pipeline — V2X 경로 선택 에이전트.

알고리즘: MaskablePPO (sb3-contrib) — action masking으로 유효하지 않은
  도로 엣지를 확률 0으로 강제, 탐색 효율 대폭 향상.

Curriculum 3단계:
  stage 0 → 1 → 2: 단거리·저부하 → 중거리 → 장거리·고부하·건물차폐
  각 단계에서 arrival_rate ≥ threshold 달성 시 자동 진급.

출처:
  • Schulman et al., "Proximal Policy Optimization Algorithms", arXiv 1707.06347
  • Huang & Ontañón, "A Closer Look at Invalid Action Masking", arXiv 2006.14171
    (action masking이 수렴 속도와 안정성 모두 개선함을 실험으로 증명)
  • Bengio et al., "Curriculum Learning", ICML 2009

사용 예:
    result = train_ppo(
        graph=_state["mock_graph"],
        bs_nodes=merged_network_nodes(),
        total_timesteps=500_000,
        model_name="ppo_v2x_route",
        device="auto",
        progress_callback=lambda info: print(info),
    )
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# ── 선택적 임포트 — 서버 시작 시 torch/sb3가 없어도 에러 없음 ─────────────────
try:
    import torch
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
    from stable_baselines3.common.env_util import make_vec_env
    from stable_baselines3.common.vec_env import DummyVecEnv
    _SB3_AVAILABLE = True
except ImportError:
    _SB3_AVAILABLE = False

from app.services.rl.gym_wrapper import V2XGymEnv

MODELS_DIR = Path(__file__).parent.parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ── Curriculum 진급 임계값 ─────────────────────────────────────────────────────
_CURRICULUM_THRESHOLDS = [0.70, 0.75, 1.0]   # arrival_rate per stage
_EVAL_FREQ = 5_000        # timesteps between evaluations
_EVAL_EPISODES = 20       # episodes per evaluation


# ── 진행률 콜백 ────────────────────────────────────────────────────────────────
class TrainingProgressCallback(BaseCallback):
    """학습 진행률을 외부 콜백으로 전달 (REST SSE 스트리밍용)."""

    def __init__(
        self,
        total_timesteps: int,
        progress_fn: Optional[Callable[[dict], None]] = None,
        report_every: int = 2_000,
    ):
        super().__init__(verbose=0)
        self._total = total_timesteps
        self._progress_fn = progress_fn
        self._every = report_every
        self._last_report = 0

    def _on_step(self) -> bool:
        if self.num_timesteps - self._last_report >= self._every:
            self._last_report = self.num_timesteps
            if self._progress_fn:
                ep_info = self.model.ep_info_buffer
                mean_rew = float(np.mean([e["r"] for e in ep_info])) if ep_info else 0.0
                mean_len = float(np.mean([e["l"] for e in ep_info])) if ep_info else 0.0
                self._progress_fn({
                    "timesteps": self.num_timesteps,
                    "total": self._total,
                    "pct": round(self.num_timesteps / self._total * 100, 1),
                    "mean_reward": round(mean_rew, 3),
                    "mean_ep_length": round(mean_len, 1),
                })
        return True


# ── Curriculum 콜백 ────────────────────────────────────────────────────────────
class CurriculumCallback(BaseCallback):
    """
    도착률이 임계값을 넘으면 curriculum_stage를 올린다.

    평가 환경(eval_env)을 독립적으로 관리하여 학습 환경에 영향 없음.
    """

    def __init__(
        self,
        eval_env: V2XGymEnv,
        stage_thresholds: list[float],
        eval_episodes: int = 20,
        eval_freq: int = 5_000,
        progress_fn: Optional[Callable[[dict], None]] = None,
    ):
        super().__init__(verbose=0)
        self._eval_env = eval_env
        self._thresholds = stage_thresholds
        self._n_eval = eval_episodes
        self._freq = eval_freq
        self._last_eval = 0
        self._current_stage = 0
        self._progress_fn = progress_fn

    def _on_step(self) -> bool:
        if self.num_timesteps - self._last_eval < self._freq:
            return True
        self._last_eval = self.num_timesteps

        # 현재 단계 평가
        arrivals = 0
        for _ in range(self._n_eval):
            obs, _ = self._eval_env.reset()
            done = False
            while not done:
                masks = self._eval_env.action_masks()
                action, _ = self.model.predict(obs, action_masks=masks, deterministic=True)
                obs, _, terminated, truncated, info = self._eval_env.step(int(action))
                done = terminated or truncated
                if info.get("arrived"):
                    arrivals += 1
                    break

        arrival_rate = arrivals / self._n_eval
        stage = self._eval_env.curriculum_stage

        if self._progress_fn:
            self._progress_fn({
                "timesteps": self.num_timesteps,
                "eval_arrival_rate": round(arrival_rate, 3),
                "curriculum_stage": stage,
                "stage_threshold": self._thresholds[stage],
            })

        # 진급 조건 달성 시 stage 올리기
        if (stage < len(self._thresholds) - 1
                and arrival_rate >= self._thresholds[stage]):
            new_stage = stage + 1
            self._eval_env.set_curriculum_stage(new_stage)
            # 학습 환경도 업그레이드
            for env in self.training_env.envs:
                if hasattr(env, "set_curriculum_stage"):
                    env.set_curriculum_stage(new_stage)
                elif hasattr(env, "env") and hasattr(env.env, "set_curriculum_stage"):
                    env.env.set_curriculum_stage(new_stage)
            if self._progress_fn:
                self._progress_fn({
                    "curriculum_upgrade": True,
                    "from_stage": stage,
                    "to_stage": new_stage,
                    "timesteps": self.num_timesteps,
                    "trigger_arrival_rate": round(arrival_rate, 3),
                })
        return True


# ── 핵심 학습 함수 ─────────────────────────────────────────────────────────────
def train_ppo(
    graph: dict,
    bs_nodes: list[dict],
    *,
    total_timesteps: int = 300_000,
    model_name: str = "ppo_v2x_route",
    device: str = "auto",
    n_envs: int = 4,
    learning_rate: float = 3e-4,
    n_steps: int = 2048,
    batch_size: int = 256,
    n_epochs: int = 10,
    gamma: float = 0.99,
    gae_lambda: float = 0.95,
    ent_coef: float = 0.01,
    buildings_gdf=None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    origin_id: Optional[str] = None,
    dest_id: Optional[str] = None,
) -> dict:
    """
    MaskablePPO로 V2X 경로 선택 에이전트를 학습시킨다.

    Parameters
    ----------
    graph           : mock_graph dict (nodes + adjacency)
    bs_nodes        : 기지국 노드 리스트
    total_timesteps : 총 학습 타임스텝 (논문 기본값 300K)
    model_name      : 저장 파일명 (확장자 없이)
    device          : "auto" | "cuda" | "mps" | "cpu"
    n_envs          : 병렬 환경 수 (GPU 활용 시 4-8 권장)
    progress_callback : 진행률 dict를 받는 콜백 (SSE 스트리밍용)

    Returns
    -------
    dict with: model_path, total_timesteps, training_time_s,
               final_mean_reward, device_used
    """
    if not _SB3_AVAILABLE:
        raise ImportError(
            "stable-baselines3, sb3-contrib, torch가 필요합니다. "
            "pip install stable-baselines3 sb3-contrib torch"
        )

    road_nodes = graph.get("nodes", {})
    if not road_nodes:
        raise ValueError("graph['nodes']가 비어 있습니다. mock_graph를 먼저 생성하세요.")

    # 원점/도착점 자동 선택
    node_ids = list(road_nodes.keys())
    if origin_id is None or origin_id not in road_nodes:
        origin_id = node_ids[0]
    if dest_id is None or dest_id not in road_nodes or dest_id == origin_id:
        dest_id = node_ids[len(node_ids) // 2]

    # ── 학습 환경 팩토리 ──────────────────────────────────────────────────────
    def _make_env(stage: int = 0):
        def _init():
            env = V2XGymEnv(
                graph, road_nodes, bs_nodes,
                origin_id, dest_id,
                curriculum_stage=stage,
                buildings_gdf=buildings_gdf,
            )
            # MaskablePPO는 action_masks()를 직접 호출하므로 ActionMasker 불필요
            return env
        return _init

    vec_env = DummyVecEnv([_make_env(stage=0) for _ in range(n_envs)])
    eval_env = V2XGymEnv(
        graph, road_nodes, bs_nodes,
        origin_id, dest_id, curriculum_stage=0
    )

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

    # ── 모델 초기화 ────────────────────────────────────────────────────────────
    policy_kwargs = dict(net_arch=[256, 256, 128])   # 3-layer MLP
    model = MaskablePPO(
        "MlpPolicy",
        vec_env,
        learning_rate=learning_rate,
        n_steps=n_steps,
        batch_size=batch_size,
        n_epochs=n_epochs,
        gamma=gamma,
        gae_lambda=gae_lambda,
        ent_coef=ent_coef,
        policy_kwargs=policy_kwargs,
        tensorboard_log=str(MODELS_DIR / "tb_logs"),
        device=device_used,
        verbose=0,
    )

    callbacks = [
        TrainingProgressCallback(
            total_timesteps=total_timesteps,
            progress_fn=progress_callback,
            report_every=2_000,
        ),
        CurriculumCallback(
            eval_env=eval_env,
            stage_thresholds=_CURRICULUM_THRESHOLDS,
            eval_episodes=_EVAL_EPISODES,
            eval_freq=_EVAL_FREQ,
            progress_fn=progress_callback,
        ),
    ]

    # ── 학습 실행 ──────────────────────────────────────────────────────────────
    start = time.time()
    model.learn(
        total_timesteps=total_timesteps,
        callback=callbacks,
        tb_log_name=model_name,
        reset_num_timesteps=True,
    )
    elapsed = round(time.time() - start, 1)

    # ── 모델 저장 ──────────────────────────────────────────────────────────────
    save_path = MODELS_DIR / model_name
    model.save(str(save_path))

    ep_info = model.ep_info_buffer
    mean_rew = float(np.mean([e["r"] for e in ep_info])) if ep_info else 0.0

    result = {
        "model_path": str(save_path) + ".zip",
        "model_name": model_name,
        "algorithm": "MaskablePPO",
        "total_timesteps": total_timesteps,
        "training_time_s": elapsed,
        "final_mean_reward": round(mean_rew, 3),
        "device_used": device_used,
        "policy_net_arch": policy_kwargs["net_arch"],
        "curriculum_stages": 3,
        "origin_id": origin_id,
        "dest_id": dest_id,
    }

    if progress_callback:
        progress_callback({"done": True, **result})

    return result
