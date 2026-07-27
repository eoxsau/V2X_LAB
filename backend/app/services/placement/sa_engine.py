"""SA 배치 최적화 엔진 — 이종(BS/RSU) 확장 (배치설계 v2 §4·§6).

핵심 설계 결정은 **타입 보존형 swap** 하나다:

    BS 슬롯은 BS 후보끼리만 교체
    RSU 슬롯은 RSU 후보끼리만 교체
        ↓
    N_BS, N_RSU 두 개수 제약이 **자동으로** 유지된다 (별도 제약 처리 코드 불필요)

v1 SA를 고른 1순위 근거가 "'정확히 N개' 제약을 공짜로 지킨다"였는데, 타입을 보존하면
그 장점이 이종에서도 그대로 계승된다(§4-2).

⚠️ delta evaluation은 **일부러 빼뒀다** (2026-07-27 실측):
    설계 §6-3·§9는 delta를 포함하라고 하지만, 구식 구현을 실측해보니 모든 규모에서
    전체 재계산보다 **오히려 느렸다**:
        수요 191 / N=5   full 0.79 ms vs delta 1.04 ms
        수요 1000 / N=5  full 7.61 ms vs delta 54.63 ms  (7배)
    원인은 부분비용을 2번 계산하며 매번 공간 그리드를 새로 만드는 구조였다.
    게다가 정규화 분모가 전체 수요가 아니라 영향 수요라, 누적 비용이 **음수(−1310 ms)** 로
    발산했다 — 설계 §11이 요구한 "delta와 전체 재계산 주기적 대조"만 했어도 잡혔을 버그다.
    → 지금은 정확한 전체 재계산으로 간다. A_seg 캐시가 들어와 비용 구조가 바뀐 뒤에
      delta의 실익을 다시 재는 것이 순서다.
"""
from __future__ import annotations

import math
import random as _rng_module
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .scoring import ASegLookup, DemandPoint, ScoreResult, Station, score_placement, _no_a_seg


@dataclass
class PlacementResult:
    """최적화 결과 한 건."""
    tech: str
    n_bs: int
    n_rsu: int
    placed: list[dict] = field(default_factory=list)     # [{id, lat, lng, node_type, height_m}]
    cost_initial_ms: float = 0.0
    cost_final_ms: float = 0.0
    improvement_pct: float = 0.0
    uncovered_pct: float = 0.0
    outage_pct: float = 0.0
    n_candidates_bs: int = 0
    n_candidates_rsu: int = 0
    n_evaluations: int = 0
    stats: dict = field(default_factory=dict)


def _to_stations(cands: Sequence, idx: Sequence[int]) -> list[Station]:
    return [Station(id=cands[i].id, lat=cands[i].lat, lng=cands[i].lng,
                    node_type=cands[i].node_type) for i in idx]


class _Evaluator:
    """채점 호출 횟수를 세는 얇은 래퍼 — 실행 비용을 결과에 남기기 위해."""

    def __init__(self, cands, demand, tech, a_seg):
        self.c, self.d, self.tech, self.a = cands, demand, tech, a_seg
        self.n = 0

    def __call__(self, idx: Sequence[int]) -> ScoreResult:
        self.n += 1
        return score_placement(_to_stations(self.c, idx), self.d, self.tech, self.a)


def greedy_forward_init(
    ev: _Evaluator,
    bs_pool: list[int],
    rsu_pool: list[int],
    n_bs: int,
    n_rsu: int,
) -> list[int]:
    """이종 greedy warm-start (§6-2).

    v1의 "매 단계 비용을 가장 많이 낮추는 하나를 추가"에서 한 줄만 바뀐다:
    **아직 쿼터가 안 찬 타입 중** 가장 이득이 큰 후보를 추가한다.
    두 타입을 섞어 고르므로 staged(BS 먼저 → RSU)와 달리 상호작용을 일부 본다.
    """
    chosen: list[int] = []
    left = {"bs": list(bs_pool), "rsu": list(rsu_pool)}
    quota = {"bs": min(n_bs, len(bs_pool)), "rsu": min(n_rsu, len(rsu_pool))}

    while quota["bs"] + quota["rsu"] > 0:
        best_cost, best_ci, best_t = float("inf"), None, None
        for t in ("bs", "rsu"):
            if quota[t] <= 0:
                continue
            for ci in left[t]:
                cost = ev(chosen + [ci]).cost_ms
                if cost < best_cost:
                    best_cost, best_ci, best_t = cost, ci, t
        if best_ci is None:
            break
        chosen.append(best_ci)
        left[best_t].remove(best_ci)
        quota[best_t] -= 1
    return chosen


def sa_run(
    ev: _Evaluator,
    init_idx: list[int],
    type_of: dict[int, str],
    pool_by_type: dict[str, list[int]],
    *,
    t0: float = 5.0,
    alpha: float = 0.995,
    n_iter: int = 1500,
    rng: Optional[_rng_module.Random] = None,
) -> tuple[list[int], float]:
    """SA 단일 궤적. **타입 보존형 swap** — 나가는 슬롯과 같은 타입에서만 후보를 뽑는다.

    t0=5.0인 이유: 비용 단위가 ms이고 좋은 배치 간 차이가 수 ms 수준이라,
    v1의 T0=30은 초반에 거의 모든 이동을 수락해 사실상 무작위 탐색이 된다.
    """
    rng = rng or _rng_module.Random()
    cur = list(init_idx)
    cur_cost = ev(cur).cost_ms
    best, best_cost = list(cur), cur_cost
    placed = set(cur)
    T = t0

    for _ in range(n_iter):
        pos = rng.randrange(len(cur))
        out_ci = cur[pos]
        t = type_of[out_ci]
        # 같은 타입에서 아직 안 쓰인 후보만 — 이것이 개수 불변식을 지킨다
        avail = [i for i in pool_by_type[t] if i not in placed]
        if not avail:
            T *= alpha
            continue
        in_ci = rng.choice(avail)

        trial = list(cur)
        trial[pos] = in_ci
        new_cost = ev(trial).cost_ms
        delta = new_cost - cur_cost

        if delta < 0 or rng.random() < math.exp(-delta / max(T, 1e-9)):
            placed.discard(out_ci)
            placed.add(in_ci)
            cur, cur_cost = trial, new_cost
            if cur_cost < best_cost:
                best, best_cost = list(cur), cur_cost
        T *= alpha

    return best, best_cost


def optimize(
    bs_candidates: Sequence,
    rsu_candidates: Sequence,
    demand: Sequence[DemandPoint],
    n_bs: int,
    n_rsu: int,
    *,
    tech: str = "5G",
    a_seg: ASegLookup = _no_a_seg,
    n_greedy: int = 1,
    n_random: int = 2,
    sa_iter: int = 1500,
    seed: Optional[int] = None,
) -> PlacementResult:
    """multi-start SA 진입점 (§6-1).

    warm-start(greedy) 궤적 + random 궤적을 각각 SA로 굴리고 최선을 채택한다.
    **warm-start만으로 채우지 않는다** — greedy는 근시안이라 단독으로는 국소해에 갇힌다(v1 규칙).
    """
    cands = list(bs_candidates) + list(rsu_candidates)
    bs_pool = list(range(len(bs_candidates)))
    rsu_pool = list(range(len(bs_candidates), len(cands)))
    type_of = {i: ("bs" if i in set(bs_pool) else "rsu") for i in range(len(cands))}
    pool_by_type = {"bs": bs_pool, "rsu": rsu_pool}

    n_bs = min(n_bs, len(bs_pool))
    n_rsu = min(n_rsu, len(rsu_pool))
    ev = _Evaluator(cands, demand, tech, a_seg)
    rng = _rng_module.Random(seed)

    results: list[tuple[list[int], float, float]] = []   # (idx, final, initial)
    random_inits: list[float] = []   # 무작위 배치의 비용 — "최적화가 얼마나 벌었나"의 기준선

    for _ in range(max(n_greedy, 0)):
        init = greedy_forward_init(ev, bs_pool, rsu_pool, n_bs, n_rsu)
        init_cost = ev(init).cost_ms
        idx, cost = sa_run(ev, init, type_of, pool_by_type, n_iter=sa_iter,
                           rng=_rng_module.Random(rng.randint(0, 10 ** 9)))
        results.append((idx, cost, init_cost))

    for _ in range(max(n_random, 0)):
        init = (rng.sample(bs_pool, n_bs) if n_bs else []) + \
               (rng.sample(rsu_pool, n_rsu) if n_rsu else [])
        init_cost = ev(init).cost_ms
        random_inits.append(init_cost)     # 최적화 이득의 기준선
        idx, cost = sa_run(ev, init, type_of, pool_by_type, n_iter=sa_iter,
                           rng=_rng_module.Random(rng.randint(0, 10 ** 9)))
        results.append((idx, cost, init_cost))

    if not results:
        return PlacementResult(tech=tech, n_bs=n_bs, n_rsu=n_rsu)

    best_idx, best_cost, init_cost = min(results, key=lambda x: x[1])

    # ⚠️ 타입 보존 불변식 검증 (설계 §11) — A안의 전제가 코드에서 깨지지 않는지 방어적 확인
    got_bs = sum(1 for i in best_idx if type_of[i] == "bs")
    got_rsu = sum(1 for i in best_idx if type_of[i] == "rsu")
    if got_bs != n_bs or got_rsu != n_rsu:
        raise RuntimeError(
            f"타입 보존 불변식 위반: BS {got_bs}≠{n_bs}, RSU {got_rsu}≠{n_rsu}. "
            f"swap이 타입을 넘나들었습니다."
        )

    final = score_placement(_to_stations(cands, best_idx), demand, tech, a_seg, want_detail=True)
    return PlacementResult(
        tech=tech, n_bs=n_bs, n_rsu=n_rsu,
        placed=[{"id": cands[i].id, "lat": cands[i].lat, "lng": cands[i].lng,
                 "node_type": cands[i].node_type, "height_m": cands[i].height_m}
                for i in best_idx],
        cost_initial_ms=round(init_cost, 3),
        cost_final_ms=round(final.cost_ms, 3),
        improvement_pct=round((init_cost - final.cost_ms) / max(init_cost, 1e-9) * 100, 2),
        uncovered_pct=round(final.uncovered_pct, 2),
        outage_pct=round(final.outage_pct, 2),
        n_candidates_bs=len(bs_pool), n_candidates_rsu=len(rsu_pool),
        n_evaluations=ev.n,
        stats={
            "station_load": final.station_load,
            "n_starts": len(results),
            # ⚠️ improvement_pct는 "SA가 자기 출발점 대비 얼마나 벌었나"라, warm-start가
            # 이미 좋으면 0에 가깝게 나온다(실측: greedy 87.04 → SA 86.94, -0.1%).
            # 최적화 전체의 가치는 **무작위 배치 대비**로 봐야 드러난다
            # (같은 조건 실측: 무작위 671.66 → 최종 86.94, -87.1%).
            "random_baseline_ms": (round(sum(random_inits) / len(random_inits), 3)
                                   if random_inits else None),
            "gain_vs_random_pct": (
                round((sum(random_inits) / len(random_inits) - final.cost_ms)
                      / max(sum(random_inits) / len(random_inits), 1e-9) * 100, 2)
                if random_inits else None),
        },
    )
