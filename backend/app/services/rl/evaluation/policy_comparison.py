"""
통합 정책 비교 평가기 — 논문·보고서 수준의 공정한 RL 비교.

핵심 원칙:
  1. 모든 정책이 동일한 V2XRoutingEnv에서 평가 (환경 통일)
  2. 동일한 시나리오(same origin/dest/graph/BS)로 비교 (조건 통일)
  3. 다중 시드 × 다중 시나리오 → 통계 유의성 검정 (Wilcoxon signed-rank)
  4. 95% 신뢰구간 보고 (논문 기준)

비교 정책:
  random   - 무작위 선택 (최저 하한 베이스라인)
  greedy   - total_cost 최소화 (결정론적 휴리스틱)
  coverage - 커버리지 최대화 (휴리스틱)
  ppo      - 강화학습 베이스라인 (MaskablePPO, V2XRoutingEnv에서 학습)
  dqn      - 강화학습 베이스라인 (DQN, V2XRoutingEnv에서 학습)  [선택]
  v4_gnn   - 제안 방법 (FOMAML GATv2Conv, 한국 2,146개 구역 사전학습)

출력:
  - JSON 보고서: results/policy_comparison_{timestamp}.json
  - 콘솔 요약 표

표준 참조:
  - Wilcoxon signed-rank: Hollander & Wolfe, 1999
  - 95% CI (t-분포): n ≥ 5 이상에서 유효
  - RL 비교 방법론: Henderson et al., NeurIPS 2018 ("Deep RL That Matters")
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.services.rl.v2x_routing_env import V2XRoutingEnv, MAX_CANDIDATES
from app.services.rl.rl_trainer import (
    SUPPORTED_POLICIES, run_episode, run_episodes, EpisodeResult,
    set_v4_adapter,
)

try:
    from app.services.rl.v4.sim_adapter import V4RoutingAdapter
    _V4_AVAILABLE = True
except ImportError:
    V4RoutingAdapter = None  # type: ignore
    _V4_AVAILABLE = False

log = logging.getLogger(__name__)

# 기본 비교 정책 (PPO/DQN은 모델 있을 때만 자동 포함)
DEFAULT_POLICIES = ["random", "greedy", "coverage", "v4_gnn"]
ALL_POLICIES = list(SUPPORTED_POLICIES)


@dataclass
class PolicyStats:
    policy: str
    n_episodes: int
    arrival_rate_mean: float
    arrival_rate_std: float
    arrival_rate_ci95_low: float
    arrival_rate_ci95_high: float
    avg_latency_mean: float
    avg_latency_std: float
    mean_reward: float
    mean_steps: float
    mean_handovers: float
    mean_disconnection_steps: float


@dataclass
class PairwiseTest:
    policy_a: str
    policy_b: str
    metric: str  # "arrival_rate" | "avg_latency_ms" | "total_reward"
    wilcoxon_statistic: float
    p_value: float
    significant: bool  # p < 0.05
    improvement_pct: float  # (a - b) / |b| × 100


@dataclass
class ComparisonReport:
    timestamp: str
    n_scenarios: int
    n_seeds: int
    graph_id: str
    policies: List[PolicyStats]
    pairwise_tests: List[PairwiseTest]
    best_policy_arrival: str
    best_policy_latency: str
    references: List[str] = field(default_factory=lambda: [
        "Wilcoxon, F. (1945). Individual comparisons by ranking methods. Biometrics, 1(6), 80-83.",
        "Henderson, P. et al. (2018). Deep reinforcement learning that matters. AAAI.",
        "3GPP TR 37.885 V15.3.0 (2019). Study on evaluation methodology of new V2X use cases.",
    ])


def _t_ci95(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    if n < 2:
        m = values[0] if values else 0.0
        return m, m
    m = sum(values) / n
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    t = 2.045 if n <= 30 else 1.96
    return m - t * se, m + t * se


def _std(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def _normal_cdf(z: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if z >= 0 else -1
    z = abs(z)
    t = 1.0 / (1.0 + p * z)
    poly = ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t
    return 0.5 * (1 + sign * (1 - poly * math.exp(-z * z)))


def _wilcoxon(x: List[float], y: List[float]) -> Tuple[float, float]:
    diffs = [xi - yi for xi, yi in zip(x, y) if xi != yi]
    n = len(diffs)
    if n == 0:
        return 0.0, 1.0
    ranked = sorted(enumerate(abs(d) for d in diffs), key=lambda t: t[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and ranked[j + 1][1] == ranked[j][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[ranked[k][0]] = avg
        i = j + 1
    W = min(
        sum(ranks[i] for i in range(n) if diffs[i] > 0),
        sum(ranks[i] for i in range(n) if diffs[i] < 0),
    )
    if n > 10:
        mu = n * (n + 1) / 4.0
        sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        z = (W - mu) / max(sigma, 1e-8)
        p = 2 * (1 - _normal_cdf(abs(z)))
    else:
        p = 1.0
    return W, p


class PolicyComparisonRunner:
    """
    모든 정책을 동일한 V2XRoutingEnv 시나리오에서 평가하고
    통계 유의성이 보장된 비교 보고서를 생성한다.

    사용법:
        runner = PolicyComparisonRunner(
            graph=mock_graph,
            road_nodes=mock_graph["nodes"],
            bs_nodes=bs_list,
            origin_dest_pairs=pairs,  # 여러 (출발, 목적지) 조합
            policies=["greedy", "coverage", "ppo", "v4_gnn"],
            n_seeds=30,
        )
        report = runner.run()
        runner.print_table(report)
        runner.save_report(report, "results/")
    """

    def __init__(
        self,
        graph: dict,
        road_nodes: dict,
        bs_nodes: list,
        origin_dest_pairs: List[Tuple[str, str]],
        policies: Optional[List[str]] = None,
        n_seeds: int = 30,
        max_steps: int = 200,
        buildings_gdf=None,
        graph_id: str = "sim",
        v4_adapter=None,  # V4RoutingAdapter instance (optional)
    ):
        self._graph = graph
        self._road_nodes = road_nodes
        self._bs_nodes = bs_nodes
        self._pairs = origin_dest_pairs
        self._policies = [p for p in (policies or DEFAULT_POLICIES) if p in SUPPORTED_POLICIES]
        self._n_seeds = n_seeds
        self._max_steps = max_steps
        self._buildings_gdf = buildings_gdf
        self._graph_id = graph_id
        self._v4_adapter = v4_adapter

        if not self._pairs:
            raise ValueError("origin_dest_pairs가 비어 있음 — 비교 실행 불가")
        if not self._policies:
            raise ValueError(f"유효한 정책 없음. SUPPORTED_POLICIES: {SUPPORTED_POLICIES}")

    def _make_env(self, origin_id: str, dest_id: str) -> V2XRoutingEnv:
        return V2XRoutingEnv(
            graph=self._graph,
            road_nodes=self._road_nodes,
            bs_nodes=self._bs_nodes,
            origin_id=origin_id,
            dest_id=dest_id,
            buildings_gdf=self._buildings_gdf,
            max_steps=self._max_steps,
        )

    def run(self) -> ComparisonReport:
        """
        전체 비교 실행. 반환값: ComparisonReport (JSON 직렬화 가능).

        내부 흐름:
          1. 모든 (시나리오 × 시드) 조합을 먼저 결정 (재현 가능성)
          2. 각 정책을 동일 조합으로 평가
          3. 정책 간 pairwise Wilcoxon 검정
          4. ComparisonReport 생성
        """
        log.info(
            "정책 비교 시작: 정책=%s, 시나리오=%d, 시드=%d",
            self._policies, len(self._pairs), self._n_seeds
        )
        t0 = time.time()

        # 재현 가능한 (시나리오, 시드) 조합 목록
        trials: List[Tuple[str, str, int]] = []
        for origin_id, dest_id in self._pairs:
            for seed in range(self._n_seeds):
                trials.append((origin_id, dest_id, seed))

        # V4 어댑터 준비: v4_gnn 정책이 포함된 경우 전역 등록
        uses_v4 = "v4_gnn" in self._policies and self._v4_adapter is not None
        _v4_last_dest: Optional[str] = None

        if uses_v4:
            set_v4_adapter(self._v4_adapter)
            log.info("V4 adapter 등록 완료. 목적지별 build_graph() 자동 수행.")

        # 정책별 에피소드 결과 수집
        results_by_policy: Dict[str, List[EpisodeResult]] = {p: [] for p in self._policies}

        for origin_id, dest_id, seed in trials:
            # dest가 달라지면 V4 그래프 임베딩 재빌드 (dist_to_goal_norm이 dest에 종속)
            if uses_v4 and dest_id != _v4_last_dest:
                log.debug("V4 build_graph 재빌드: dest_id=%s", dest_id)
                self._v4_adapter.build_graph(self._graph, self._bs_nodes, dest_id)
                _v4_last_dest = dest_id

            env = self._make_env(origin_id, dest_id)
            for policy in self._policies:
                result = run_episode(env, policy=policy, seed=seed, record_trajectory=False)
                results_by_policy[policy].append(result)

        # 정책별 통계 집계
        policy_stats: List[PolicyStats] = []
        for policy in self._policies:
            eps = results_by_policy[policy]
            arrivals = [1.0 if e.arrived else 0.0 for e in eps]
            latencies = [e.avg_latency_ms for e in eps]
            rewards = [e.total_reward for e in eps]
            steps = [float(e.steps) for e in eps]
            handovers = [float(e.handover_count) for e in eps]
            disc = [float(e.disconnection_steps) for e in eps]

            ci_lo, ci_hi = _t_ci95(arrivals)
            n = len(eps)
            mean = lambda v: sum(v) / n if n else 0.0

            policy_stats.append(PolicyStats(
                policy=policy,
                n_episodes=n,
                arrival_rate_mean=round(mean(arrivals), 4),
                arrival_rate_std=round(_std(arrivals), 4),
                arrival_rate_ci95_low=round(ci_lo, 4),
                arrival_rate_ci95_high=round(ci_hi, 4),
                avg_latency_mean=round(mean(latencies), 2),
                avg_latency_std=round(_std(latencies), 2),
                mean_reward=round(mean(rewards), 4),
                mean_steps=round(mean(steps), 2),
                mean_handovers=round(mean(handovers), 3),
                mean_disconnection_steps=round(mean(disc), 3),
            ))

        # Pairwise Wilcoxon 검정 (모든 쌍, arrival_rate 기준)
        pairwise: List[PairwiseTest] = []
        for i, pa in enumerate(self._policies):
            for pb in self._policies[i + 1:]:
                arr_a = [1.0 if e.arrived else 0.0 for e in results_by_policy[pa]]
                arr_b = [1.0 if e.arrived else 0.0 for e in results_by_policy[pb]]
                W, p = _wilcoxon(arr_a, arr_b)
                mean_a = sum(arr_a) / len(arr_a) if arr_a else 0
                mean_b = sum(arr_b) / len(arr_b) if arr_b else 0
                impr = ((mean_a - mean_b) / max(abs(mean_b), 0.01)) * 100
                pairwise.append(PairwiseTest(
                    policy_a=pa, policy_b=pb, metric="arrival_rate",
                    wilcoxon_statistic=round(W, 2),
                    p_value=round(p, 4),
                    significant=p < 0.05,
                    improvement_pct=round(impr, 2),
                ))

        # 최우수 정책 결정
        best_arr = max(policy_stats, key=lambda s: s.arrival_rate_mean)
        best_lat = min(policy_stats, key=lambda s: s.avg_latency_mean)

        elapsed = time.time() - t0
        log.info("비교 완료: %.1f초 | 최고 도달률=%s (%.1f%%) | 최저 지연=%s (%.1fms)",
                 elapsed, best_arr.policy, best_arr.arrival_rate_mean * 100,
                 best_lat.policy, best_lat.avg_latency_mean)

        return ComparisonReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            n_scenarios=len(self._pairs),
            n_seeds=self._n_seeds,
            graph_id=self._graph_id,
            policies=policy_stats,
            pairwise_tests=pairwise,
            best_policy_arrival=best_arr.policy,
            best_policy_latency=best_lat.policy,
        )

    @staticmethod
    def print_table(report: ComparisonReport) -> None:
        """콘솔 요약 표 출력 (논문 Table 수준)."""
        W = 80
        print("=" * W)
        print(f"  V2X Routing Policy Comparison — {report.timestamp[:10]}")
        print(f"  시나리오 {report.n_scenarios}개 × {report.n_seeds}시드 = {report.n_scenarios * report.n_seeds}에피소드/정책")
        print("=" * W)
        header = f"{'정책':<12} {'도달률':>8} {'±std':>6} {'95%CI':>14} {'평균지연(ms)':>12} {'보상':>8} {'핸드오버':>8}"
        print(header)
        print("-" * W)
        for s in sorted(report.policies, key=lambda x: -x.arrival_rate_mean):
            ci = f"[{s.arrival_rate_ci95_low:.3f},{s.arrival_rate_ci95_high:.3f}]"
            marker = " ★" if s.policy == report.best_policy_arrival else ""
            print(
                f"{s.policy + marker:<12} "
                f"{s.arrival_rate_mean:>7.1%} "
                f"{s.arrival_rate_std:>6.3f} "
                f"{ci:>14} "
                f"{s.avg_latency_mean:>12.1f} "
                f"{s.mean_reward:>8.2f} "
                f"{s.mean_handovers:>8.2f}"
            )
        print("-" * W)
        print("\n  Pairwise Wilcoxon signed-rank (arrival_rate, α=0.05):")
        for pt in report.pairwise_tests:
            sig = "**유의**" if pt.significant else "유의하지않음"
            print(f"  {pt.policy_a} vs {pt.policy_b}: "
                  f"W={pt.wilcoxon_statistic:.1f}, p={pt.p_value:.4f} [{sig}], "
                  f"개선={pt.improvement_pct:+.1f}%")
        print("=" * W)

    @staticmethod
    def save_report(report: ComparisonReport, output_dir: str = "results") -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        ts = report.timestamp.replace(":", "-").replace(".", "-")[:19]
        path = out / f"policy_comparison_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False)
        log.info("비교 보고서 저장: %s", path)
        return str(path)
