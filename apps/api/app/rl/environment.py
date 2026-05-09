from __future__ import annotations

from typing import Any

from app.rl.action_space import build_action_candidates
from app.rl.baseline_policy import select_baseline_action
from app.rl.episode_logger import append_episode_log, get_episode_logs
from app.rl.reward import calculate_reward_components
from app.rl.state_builder import build_rl_state


def build_rl_ready_payload(
    vehicles: list[dict[str, Any]],
    roads: list[dict[str, Any]],
    network_nodes: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> dict[str, object]:
    vehicle = vehicles[0] if vehicles else {}
    route = next((item for item in routes if item.get("id") == vehicle.get("current_route_id")), routes[0] if routes else {})
    state = build_rl_state(vehicle, roads, network_nodes, routes)
    actions = build_action_candidates(vehicle, routes)
    reward = calculate_reward_components(vehicle, route)
    baseline = select_baseline_action(vehicle, routes)
    append_episode_log("rl_ready_snapshot", {"vehicle_id": vehicle.get("id"), "baseline_action": baseline.get("action")})

    return {
        "status": "rl_ready_no_training",
        "state": state,
        "action_candidates": actions,
        "reward": reward,
        "episode_logs": get_episode_logs(),
        "policy_comparison": {
            "baseline_policy": baseline,
            "future_rl_policy": {
                "status": "placeholder_not_trained",
                "intended_library": "Stable-Baselines3",
            },
        },
        "note": "No RL training is running. This payload prepares interfaces for future Stable-Baselines3 integration.",
    }
