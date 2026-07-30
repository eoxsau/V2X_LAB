"""
RL Agent Registry — 학습된 모델 로드 + 추론 서비스.

역할:
  1. 디스크에서 PPO/DQN 모델 로드 (서버 재시작 후에도 유지)
  2. V2XRoutingEnv로 rl_routing 경로 탐색 실행
  3. 사용 가능한 모델 목록 제공

사용:
    registry = AgentRegistry()
    registry.load("ppo_v2x_route")      # 또는 .zip 경로 직접
    path = registry.run_route(graph, bs_nodes, origin_id, dest_id)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

MODELS_DIR = Path(__file__).parent.parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

try:
    from sb3_contrib import MaskablePPO
    from stable_baselines3 import DQN
    from app.services.rl.gym_wrapper import V2XGymEnv
    from app.services.rl.v2x_routing_env import MAX_CANDIDATES
    _SB3_AVAILABLE = True
except ImportError:
    _SB3_AVAILABLE = False
    MaskablePPO = None  # type: ignore
    DQN = None          # type: ignore
    V2XGymEnv = None    # type: ignore
    MAX_CANDIDATES = 5  # type: ignore

try:
    import torch
    from app.services.rl.v4.universal_gnn_policy import UniversalGNNPolicy, build_graph_obs
    from app.services.rl.v4.domain_randomizer import DomainRandomizer
    from app.services.rl.v4.maml_trainer import MAMLTrainer, MAMLConfig
    _GNN_AVAILABLE = True
except ImportError:
    _GNN_AVAILABLE = False
    UniversalGNNPolicy = None   # type: ignore
    build_graph_obs = None      # type: ignore
    DomainRandomizer = None     # type: ignore
    MAMLTrainer = None          # type: ignore


class AgentRegistry:
    """
    싱글턴 패턴으로 사용 권장 (main.py에서 _RL_AGENT = AgentRegistry() 한 번만).

    여러 모델을 동시에 로드할 수 있으며 active_model로 전환 가능.
    """

    def __init__(self) -> None:
        self._models: dict[str, object] = {}          # name → model object
        self._meta: dict[str, dict] = {}              # name → metadata
        self._active: Optional[str] = None            # 현재 사용 중인 모델명

    # ── 모델 관리 ─────────────────────────────────────────────────────────────
    def load(self, model_name: str) -> dict:
        """
        models/ 디렉토리에서 모델을 로드한다.

        model_name: "ppo_v2x_route" 또는 절대/상대 경로 (.zip 포함 무관)
        """
        if not _SB3_AVAILABLE:
            raise ImportError("stable-baselines3, torch가 필요합니다.")

        path = Path(model_name)
        if not path.is_absolute():
            path = MODELS_DIR / model_name
        if path.suffix != ".zip":
            path = path.with_suffix(".zip")

        if not path.exists():
            raise FileNotFoundError(f"모델 파일을 찾을 수 없습니다: {path}")

        # 알고리즘 자동 감지 (파일명 기반)
        name = path.stem
        if "dqn" in name.lower():
            model = DQN.load(str(path))
            algo = "DQN"
        else:
            model = MaskablePPO.load(str(path))
            algo = "MaskablePPO"

        self._models[name] = model
        meta = {
            "name": name,
            "algorithm": algo,
            "path": str(path),
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "is_active": False,
        }

        # meta.json 읽기 (학습 시 저장된 부가 정보)
        meta_path = path.with_suffix(".json")
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta.update(json.load(f))
            except Exception:
                pass

        self._meta[name] = meta
        if self._active is None:
            self._active = name
            self._meta[name]["is_active"] = True

        return meta

    def set_active(self, model_name: str) -> None:
        if model_name not in self._models:
            raise KeyError(f"로드되지 않은 모델: {model_name}. 먼저 load()를 호출하세요.")
        for k in self._meta:
            self._meta[k]["is_active"] = False
        self._active = model_name
        self._meta[model_name]["is_active"] = True

    def unload(self, model_name: str) -> None:
        self._models.pop(model_name, None)
        self._meta.pop(model_name, None)
        if self._active == model_name:
            self._active = next(iter(self._models), None)

    def list_models(self) -> list[dict]:
        """로드된 모델 목록 + models/ 디렉토리의 미로드 파일 포함."""
        loaded = list(self._meta.values())
        loaded_names = {m["name"] for m in loaded}

        saved = []
        for p in MODELS_DIR.glob("*.zip"):
            if p.stem not in loaded_names:
                saved.append({
                    "name": p.stem,
                    "path": str(p),
                    "loaded": False,
                    "is_active": False,
                })

        return [
            {**m, "loaded": True} for m in loaded
        ] + saved

    @property
    def is_ready(self) -> bool:
        return self._active is not None and self._active in self._models

    @property
    def active_model_name(self) -> Optional[str]:
        return self._active

    # ── 추론: 경로 탐색 ───────────────────────────────────────────────────────
    def run_route(
        self,
        graph: dict,
        bs_nodes: list[dict],
        origin_id: str,
        dest_id: str,
        *,
        buildings_gdf=None,
        allocation_output: Optional[dict] = None,
        deterministic: bool = True,
    ) -> dict:
        """
        학습된 RL 에이전트로 origin → dest 경로를 탐색한다.

        Returns
        -------
        dict:
          path           : [node_id, ...] 방문 노드 순서
          node_sequence  : 상동 (라우팅 시스템 호환용 별칭)
          arrived        : 목적지 도달 여부
          steps          : 총 스텝 수
          total_reward   : 에피소드 누적 보상
          avg_latency_ms : 평균 예측 지연 (ms)
          handover_count : 핸드오버 횟수
          model_used     : 사용된 모델명
          algorithm      : PPO / DQN
        """
        if not self.is_ready:
            raise RuntimeError("로드된 활성 모델이 없습니다. load() 후 사용하세요.")

        model = self._models[self._active]
        algo = self._meta[self._active]["algorithm"]
        road_nodes = graph.get("nodes", {})

        env = V2XGymEnv(
            graph, road_nodes, bs_nodes,
            origin_id, dest_id,
            curriculum_stage=2,
            buildings_gdf=buildings_gdf,
            allocation_output=allocation_output,
        )

        obs, _ = env.reset()
        done = False
        path = [origin_id]
        total_reward = 0.0
        latencies: list[float] = []
        handovers = 0
        steps = 0
        arrived = False

        while not done and steps < 300:
            if algo == "MaskablePPO":
                masks = env.action_masks()
                action, _ = model.predict(obs, action_masks=masks, deterministic=deterministic)
            else:
                action, _ = model.predict(obs, deterministic=deterministic)

            obs, reward, terminated, truncated, info = env.step(int(action))
            done = terminated or truncated
            total_reward += reward
            steps += 1

            node_id = info.get("node_id")
            if node_id and node_id != path[-1]:
                path.append(str(node_id))

            lat = float(info.get("latency_ms", 0.0))
            latencies.append(lat)
            if info.get("handover"):
                handovers += 1
            if info.get("arrived"):
                arrived = True

        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else 0.0

        return {
            "path": path,
            "node_sequence": path,
            "arrived": arrived,
            "steps": steps,
            "total_reward": round(total_reward, 4),
            "avg_latency_ms": avg_lat,
            "handover_count": handovers,
            "model_used": self._active,
            "algorithm": algo,
        }

    # ── GNN (UniversalGNNPolicy / MAML) support ───────────────────────────────
    def load_gnn(self, model_path: str) -> dict:
        """
        Load a UniversalGNNPolicy checkpoint (.pt file produced by MAMLTrainer).

        The model is registered alongside SB3 models under algorithm="GNN-MAML".
        """
        if not _GNN_AVAILABLE:
            raise ImportError("torch and torch_geometric required for GNN models.")

        path = Path(model_path)
        if not path.exists():
            path = MODELS_DIR / model_path
        if path.suffix not in (".pt", ".pth"):
            path = Path(str(path) + ".pt")
        if not path.exists():
            raise FileNotFoundError(f"GNN 모델 파일을 찾을 수 없습니다: {path}")

        ckpt = torch.load(str(path), map_location="cpu")
        model = UniversalGNNPolicy()
        state = ckpt.get("state_dict", ckpt)
        model.load_state_dict(state)
        model.eval()

        name = path.stem
        self._models[name] = model
        meta = {
            "name": name,
            "algorithm": "GNN-MAML",
            "path": str(path),
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "is_active": False,
            "maml_iteration": ckpt.get("iteration", "unknown"),
        }
        self._meta[name] = meta
        if self._active is None:
            self._active = name
            self._meta[name]["is_active"] = True
        return meta

    def run_gnn_route(
        self,
        graph: dict,
        bs_nodes: list[dict],
        rsu_nodes: list[dict],
        origin_id: str,
        dest_id: str,
        channel_lookup=None,
        deterministic: bool = True,
        max_steps: int = 200,
    ) -> dict:
        """
        Run one episode with the active GNN (MAML) policy.

        Parameters
        ----------
        graph        : road graph {"nodes": ..., "adjacency": ...}
        bs_nodes     : list of BS dicts (same format as _state["network_nodes"])
        rsu_nodes    : list of RSU dicts
        origin_id    : start road node ID
        dest_id      : destination road node ID
        channel_lookup : optional ChannelLookup (Sionna SINR map)
        deterministic  : if True, argmax; else sample
        max_steps      : episode step limit

        Returns
        -------
        dict with path, arrived, avg_latency_ms, handover_count, model_used
        """
        if not self.is_ready:
            raise RuntimeError("로드된 활성 모델이 없습니다.")
        if not _GNN_AVAILABLE:
            raise ImportError("torch_geometric required for GNN inference.")

        model = self._models[self._active]
        algo  = self._meta[self._active].get("algorithm", "")
        if algo != "GNN-MAML":
            raise ValueError(f"활성 모델 '{self._active}'은 GNN이 아닙니다 (algorithm={algo}). "
                             "run_route()를 사용하세요.")

        from app.services.rl.v4.universal_v2x_env import UniversalV2XEnv
        from app.services.rl.v4.domain_randomizer import DomainRandomizer, V2XTask

        # Construct a V2XTask directly (no randomization) from provided inputs
        bbox = _graph_bbox(graph)
        task = V2XTask(
            region_id=0,
            region_name="user_scenario",
            graph=graph,
            bs_nodes=bs_nodes,
            rsu_nodes=rsu_nodes,
            origin_id=origin_id,
            dest_id=dest_id,
            traffic_density=10.0,
            network_mode="5G",
            bbox=bbox,
        )

        randomizer = DomainRandomizer(train=False, seed=0)
        env = UniversalV2XEnv(randomizer, channel_lookup=channel_lookup)
        obs, _ = env.reset(task=task)

        path = [origin_id]
        total_reward = 0.0
        latencies: list[float] = []
        handovers = 0
        steps = 0
        arrived = False

        with torch.no_grad():
            while steps < max_steps:
                data = obs.to("cpu") if hasattr(obs, "to") else obs
                mask = torch.tensor(env.action_masks(), dtype=torch.bool)
                logits, _ = model(data, action_mask=mask)
                if deterministic:
                    action = int(logits.argmax().item())
                else:
                    action = int(torch.distributions.Categorical(logits=logits).sample().item())

                obs, reward, terminated, truncated, info = env.step(action)
                total_reward += reward
                steps += 1

                node_id = info.get("node_id")
                if node_id and node_id != path[-1]:
                    path.append(str(node_id))

                latencies.append(float(info.get("latency_ms", 0.0)))
                if info.get("handover"):
                    handovers += 1
                if info.get("arrived"):
                    arrived = True

                if terminated or truncated:
                    break

        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        return {
            "path": path,
            "node_sequence": path,
            "arrived": arrived,
            "steps": steps,
            "total_reward": round(total_reward, 4),
            "avg_latency_ms": avg_lat,
            "handover_count": handovers,
            "model_used": self._active,
            "algorithm": "GNN-MAML",
        }

    def adapt_gnn(
        self,
        graph: dict,
        bs_nodes: list[dict],
        rsu_nodes: list[dict],
        origin_id: str,
        dest_id: str,
        n_adapt_episodes: int = 5,
        adapted_name: Optional[str] = None,
    ) -> dict:
        """
        MAML 5-shot adaptation: fine-tune a copy of the active GNN model
        on the user's specific scenario (graph + BS/RSU + O/D).

        The adapted model is registered under `adapted_name` and set as active.
        Returns the adaptation metadata.
        """
        if not _GNN_AVAILABLE:
            raise ImportError("torch_geometric required.")
        if not self.is_ready:
            raise RuntimeError("활성 GNN 모델이 없습니다. load_gnn()을 먼저 호출하세요.")

        from app.services.rl.v4.domain_randomizer import DomainRandomizer, V2XTask

        bbox = _graph_bbox(graph)
        task = V2XTask(
            region_id=0, region_name="adapt_scenario",
            graph=graph, bs_nodes=bs_nodes, rsu_nodes=rsu_nodes,
            origin_id=origin_id, dest_id=dest_id,
            traffic_density=10.0, network_mode="5G", bbox=bbox,
        )
        randomizer = DomainRandomizer(train=False, seed=0)
        trainer = MAMLTrainer(randomizer, config=MAMLConfig())
        # Load the current meta-model weights
        base_model = self._models[self._active]
        if hasattr(base_model, "_orig_mod"):
            base_model = base_model._orig_mod
        trainer._model.load_state_dict(base_model.state_dict())

        adapted = trainer.adapt(task, n_adapt_episodes=n_adapt_episodes)

        name = adapted_name or f"{self._active}_adapted"
        self._models[name] = adapted
        meta = {
            "name": name,
            "algorithm": "GNN-MAML-adapted",
            "is_active": True,
            "adapted_from": self._active,
            "adapt_episodes": n_adapt_episodes,
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        for k in self._meta:
            self._meta[k]["is_active"] = False
        self._meta[name] = meta
        self._active = name
        return meta


# ── Helper ────────────────────────────────────────────────────────────────────
def _graph_bbox(graph: dict) -> dict:
    lats = [d["lat"] for d in graph["nodes"].values()]
    lngs = [d["lng"] for d in graph["nodes"].values()]
    return {"s": min(lats), "n": max(lats), "w": min(lngs), "e": max(lngs)}


# ── 모듈-레벨 싱글턴 (main.py에서 임포트) ──────────────────────────────────────
_REGISTRY = AgentRegistry()


def get_registry() -> AgentRegistry:
    return _REGISTRY
