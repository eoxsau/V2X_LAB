"""채점 벡터화 — `scoring.score_placement`와 **같은 답을 내되** 수십 배 빠르게.

왜 이게 가능한가 (핵심 관찰):

    SINR(수요점 i, 후보 j)는 **어떤 조합을 뽑았는지와 무관하다.**

    거리도, 경로손실도, 차폐 A_seg도 전부 (i, j) 쌍만으로 정해진다. 배치에 따라 달라지는
    것은 오직 **"어느 스테이션에 붙는가"(argmax)** 와 **"그 스테이션에 몇 대가 붙었나"(큐)**
    뿐이다. 그러니 SINR·outage·전송지연을 (수요 × 후보) 행렬로 **한 번만** 계산해두면,
    배치 하나를 채점하는 일은 열 몇 개를 슬라이스해서 argmax 하는 것으로 끝난다.

실측 근거 (2026-07-28, 안양 구역 · 수요 2,406 × 스테이션 16):
    순수 Python  31.4 ms/회  — 내부 38,496회 루프
    이 모듈       0.1 ms 미만 — numpy 몇 줄
    N* 배치 최적화의 greedy 단계가 12,320회를 도므로 224초 → 수 초가 된다.

⚠️ **정답의 기준은 `scoring.score_placement`다.** 이 모듈은 성능 사본일 뿐이므로,
   지연 공식(formula_v31)이 바뀌면 여기도 같이 고쳐야 하고 `tests`의 동치 검증으로
   확인해야 한다. 검증이 깨지면 이 모듈을 쓰지 말고 원본으로 되돌릴 것.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from app.services.latency import formula_v31 as f31

from .scoring import ScoreResult, UNCOVERED_PENALTY_MS

_RSU_TYPES = {"rsu", "rsu_node"}


class FastScorer:
    """후보·수요를 고정해두고 배치(후보 인덱스 목록)만 바꿔가며 채점한다.

    생성 비용은 (수요 × 후보) 행렬 두어 개 — 2,406 × 1,032이면 float32로 약 20MB,
    계산은 numpy 벡터 연산이라 0.1초 남짓이다. 그 뒤 채점은 사실상 공짜다.
    """

    def __init__(self, candidates: Sequence, demand: Sequence, tech: str, a_seg=None):
        self.tech = tech
        self.n_cand = len(candidates)
        self.n_dem = len(demand)

        d_lat = np.fromiter((d.lat for d in demand), dtype=np.float64, count=self.n_dem)
        d_lng = np.fromiter((d.lng for d in demand), dtype=np.float64, count=self.n_dem)
        self.veh = np.fromiter((d.vehicle_count for d in demand),
                               dtype=np.float64, count=self.n_dem)
        self.total_veh = float(self.veh.sum()) or 1.0

        c_lat = np.fromiter((c.lat for c in candidates), dtype=np.float64, count=self.n_cand)
        c_lng = np.fromiter((c.lng for c in candidates), dtype=np.float64, count=self.n_cand)
        is_rsu = np.array([str(c.node_type or "").lower() in _RSU_TYPES for c in candidates])

        # ── 거리 (scoring._dist_m과 같은 평면 근사) ──────────────────────────
        dlat = (c_lat[None, :] - d_lat[:, None]) * 111_320.0
        mid = (d_lat[:, None] + c_lat[None, :]) * 0.5
        dlng = (c_lng[None, :] - d_lng[:, None]) * 111_320.0 * np.cos(np.radians(mid))
        dist = np.hypot(dlat, dlng)

        # ── 유닛 파라미터 (BS / RSU 두 벌뿐이라 마스크로 채운다) ─────────────
        p_bs = f31.resolve_unit(tech, "bs")
        p_rsu = f31.resolve_unit(tech, "rsu")
        alpha = np.where(is_rsu, p_rsu["alpha"], p_bs["alpha"])
        d_bp = np.where(is_rsu, p_rsu["d_BP"], p_bs["d_BP"])
        cover = np.where(is_rsu, p_rsu["d_edge"], p_bs["d_edge"])
        p_tx = f31.P_TX_DBM - np.where(is_rsu, f31.DELTA_P_RSU_DB, 0.0)
        self.tti = np.where(is_rsu, p_rsu["TTI"], p_bs["TTI"])
        self.c_tech = np.where(is_rsu, p_rsu["C_tech"], p_bs["C_tech"])
        bw = np.where(is_rsu, p_rsu["BW"], p_bs["BW"])
        noise_floor = np.where(is_rsu, p_rsu["noise_floor_dbm"], p_bs["noise_floor_dbm"])

        # ── 경로손실 (formula_v31._pl의 이중 기울기) ─────────────────────────
        dc = np.maximum(dist, 1.0)
        near = 22.0 * np.log10(dc)
        far = 22.0 * np.log10(d_bp)[None, :] + 40.0 * np.log10(dc / d_bp[None, :])
        pl = np.where(dc <= d_bp[None, :], near, far)

        a = self._dense_a_seg(a_seg, demand, candidates)
        sinr = (p_tx[None, :] - alpha[None, :] - pl - a) - noise_floor[None, :]

        self.covered = dist <= cover[None, :]
        self.outage = sinr < f31.SINR_MIN_DB

        # ── 배치와 무관한 지연 성분: L_base + L_transmission ─────────────────
        # MCS는 "임계값이 sinr 이하인 것 중 마지막" — 오름차순 표라 searchsorted로 같다.
        thr = np.array([t for t, _ in f31.MCS_TABLE])
        se_tab = np.array([s for _, s in f31.MCS_TABLE])
        k = np.clip(np.searchsorted(thr, sinr, side="right") - 1, 0, len(thr) - 1)
        se = se_tab[k]
        l_trans = (f31.PACKET_SIZE_BITS / (se * bw[None, :])) * 1000.0
        l_fixed = self.tti[None, :] * 0.5 + l_trans
        # outage면 L_OUTAGE_MS 한 값으로 대체된다(formula_v31이 조기 반환하는 그대로)
        self.l_fixed = np.where(self.outage, f31.L_OUTAGE_MS, l_fixed)

        # argmax가 미커버 열을 고르지 않도록 -inf로 막는다.
        # ⚠️ outage는 막지 **않는다** — 원본은 커버 안이면 outage여도 연결한다.
        #
        # 행렬을 (후보 × 수요)로 **전치해서** 들고 있는 이유: 채점할 때마다 스테이션
        # 16개를 골라내는데, (수요 × 후보) 배치에서는 그게 열 방향 gather라 캐시가 어긋난다.
        # 전치해두면 연속된 행 16개를 집는 것이 되어 훨씬 빠르다(실측 1.61 → 0.5ms대).
        self.sinr_T = np.ascontiguousarray(np.where(self.covered, sinr, -np.inf).T)
        self.covered_T = np.ascontiguousarray(self.covered.T)
        self.outage_T = np.ascontiguousarray(self.outage.T)
        self.l_fixed_T = np.ascontiguousarray(self.l_fixed.T)

    def _dense_a_seg(self, a_seg, demand, candidates) -> np.ndarray:
        """(수요 × 후보) A_seg 행렬. 콜백을 전수 호출하지 않고 내부 dict를 직접 읽는다."""
        a = np.zeros((self.n_dem, self.n_cand), dtype=np.float64)
        table = getattr(a_seg, "_t", None)
        if not table:
            return a
        d_index = {d.id: i for i, d in enumerate(demand)}
        c_index = {c.id: j for j, c in enumerate(candidates)}
        for (did, cid), v in table.items():
            i = d_index.get(did)
            j = c_index.get(cid)
            if i is not None and j is not None:
                a[i, j] = v
        return a

    def score(self, idx: Sequence[int], want_detail: bool = False) -> ScoreResult:
        """배치 하나 채점. `idx`는 후보 인덱스 목록(=스테이션)."""
        if len(idx) == 0:
            return ScoreResult(cost_ms=UNCOVERED_PENALTY_MS, uncovered_pct=100.0,
                               outage_pct=100.0)
        cols = np.asarray(idx, dtype=np.intp)
        sinr = self.sinr_T[cols]                         # (스테이션 × 수요) — 연속 행 gather
        best = np.argmax(sinr, axis=0)                   # 동점이면 첫 번째 — 원본과 같다
        covered = self.covered_T[cols].any(axis=0)

        # ── 스테이션별 접속 대수 → ρ → 큐 지연 ──────────────────────────────
        load = np.bincount(best[covered], weights=self.veh[covered], minlength=len(cols))
        rho = np.minimum(np.maximum(load, 0.0) / self.c_tech[cols], 0.99)
        l_queue = self.tti[cols] * rho / (1.0 - rho)

        dem_ix = np.arange(self.n_dem)
        fixed = self.l_fixed_T[cols[best], dem_ix]
        is_out = self.outage_T[cols[best], dem_ix]
        # outage면 큐를 더하지 않는다(원본이 조기 반환하므로)
        total = np.where(is_out, f31.L_OUTAGE_MS, fixed + l_queue[best])
        total = np.round(total, 3)                        # 원본의 round(.., 3)와 맞춘다
        total = np.where(covered, total, UNCOVERED_PENALTY_MS)

        weighted = float((self.veh * total).sum())
        unc = float(self.veh[~covered].sum())
        out_veh = float(self.veh[covered & is_out].sum())

        res = ScoreResult(
            cost_ms=weighted / self.total_veh,
            uncovered_pct=unc / self.total_veh * 100.0,
            outage_pct=out_veh / self.total_veh * 100.0,
        )
        if want_detail:
            res.station_load = {str(i): float(load[k]) for k, i in enumerate(cols)}
        return res
