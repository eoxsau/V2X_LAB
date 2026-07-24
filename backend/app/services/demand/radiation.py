"""Radiation model — 존 질량장 → OD 행렬 T_ij (문서 traffic_demand_design_v2.md §6).

gravity model이 '거리'로 흐름을 감쇠시키는 반면, radiation model은 출발지·도착지 사이에
낀 모든 기회(intervening opportunities)로 감쇠시킨다. 파라미터가 없고(기본형) 질량 분포만
입력으로 받는다.

기본형 (Nature 2012, §6-2):
    P(i→j) = m_i·m_j / [(m_i + s_ij)(m_i + m_j + s_ij)]
    s_ij   = i에서 j보다 가까운 존들의 질량 합 (i, j 자신 제외)
    O_i    = N × m_i / Σm            (origin-constrained 총 유출)
    T_ij   = O_i · P(i→j)            (출발지별 Σ_j P = 1 정규화)

RMS 슬롯 (§6-5, λ = 통행거리 다이얼):
    P_>(a) = (1 − λ^(a+1)) / [(a+1)(1−λ)]   (λ=0 → 1/(a+1))
    a = m_i + s_ij ,  P(i→j) = P_>(a) − P_>(a + m_j)
    도시 내부에서 기본형이 통행을 너무 짧게 뽑을 때 λ를 올려 특성거리를 늘린다.
    ※ 서울 도심 실데이터 검증으로 기본 앵커 λ=0.9999 채택(통행거리 중앙 ~1.5km).

자기-존 흐름(i==j, 존 내부 통행)은 radiation이 직접 모델링하지 않으므로 제외한다
(존 내 통행은 disaggregation 단계에서 별도 처리 — §3-2·§7).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

_M_PER_DEG_LAT = 111_320.0


@dataclass
class ODFlow:
    """존 i → 존 j 기대 통행량."""
    i: int
    j: int
    trips: float


def _distance_matrix(coords: list[tuple[float, float]]) -> list[list[float]]:
    """국소 등거리 투영으로 존 중심 간 거리(m) 행렬."""
    lat0 = sum(c[0] for c in coords) / len(coords)
    coslat = math.cos(math.radians(lat0)) or 1e-6
    xy = [(lng * coslat * _M_PER_DEG_LAT, lat * _M_PER_DEG_LAT) for lat, lng in coords]
    n = len(xy)
    D = [[0.0] * n for _ in range(n)]
    for i in range(n):
        xi, yi = xy[i]
        Di = D[i]
        for j in range(i + 1, n):
            xj, yj = xy[j]
            d = math.hypot(xi - xj, yi - yj)
            Di[j] = d
            D[j][i] = d
    return D


def _p_gt(a: float, lam: float) -> float:
    """가장 가까운 a개 제안을 모두 거절할 확률 P_>(a)."""
    if lam <= 0.0:
        return 1.0 / (a + 1.0)
    try:
        num = 1.0 - lam ** (a + 1.0)   # 큰 a에서 λ^(a+1)→0 (올바른 극한)
    except OverflowError:
        num = 1.0
    return num / ((a + 1.0) * (1.0 - lam))


def radiation_od_matrix(
    masses: list[float],
    coords: list[tuple[float, float]],
    total_trips: float,
    lam: float = 0.9999,
    ref_opportunities: float = 100.0,
) -> list[ODFlow]:
    """질량장(존별 질량 + 중심좌표) → OD 흐름 목록.

    Parameters
    ----------
    masses : 존별 질량 m_i (연면적 등). 길이 = 존 수.
    coords : 존별 (lat, lng) 중심. masses와 같은 순서.
    total_trips : 총 유출 통행량 N (= N* × 사용자 배율). O_i 스케일에만 영향.
    lam : RMS λ (특성 통행거리 다이얼). 기본 0.9999 = 도심 앵커.
          서울 도심 실데이터 검증상 λ=0(기본형)은 통행거리가 셀크기(~300m)로 붕괴(고밀도
          도심에서 s_ij 폭증 → 최근접 존 흡수, 문서 §6-5 도시내부 실패). λ=0.9999에서
          통행거리 중앙 ~1.5km로 현실화. 지역 스케일 앵커이지 자유 노브 아님(§2-5).
          광역/저밀도로 가면 낮추고, 배정 검증(§9)으로 미세조정.
    ref_opportunities : λ>0일 때 질량→기회수 환산 기준(평균 존이 이만큼의 기회를 갖도록
                        재척도). λ^a 언더플로 방지 + λ의 의미(특성거리) 앵커. λ=0이면 무시.

    Returns
    -------
    list[ODFlow] — i≠j, trips>0 인 흐름. Σ_j trips(i→·) = O_i.
    """
    nz = len(masses)
    if nz < 2:
        return []
    total_mass = sum(masses) or 1.0
    D = _distance_matrix(coords)

    # RMS: 질량을 '기회 수'로 재척도 (λ 언더플로 방지, 비율 보존)
    if lam > 0.0:
        scale = ref_opportunities / (total_mass / nz)
        m = [x * scale for x in masses]
    else:
        m = masses

    flows: list[ODFlow] = []
    for i in range(nz):
        mi = m[i]
        if mi <= 0:
            continue
        order = sorted(range(nz), key=lambda j: D[i][j])
        running = 0.0                 # i에서 현재 존보다 가까운 존들의 누적 질량
        row: list[tuple[int, float]] = []
        for j in order:
            if j == i:
                running += m[j]        # i 자신도 누적에 포함(뒤에서 빼줌)
                continue
            mj = m[j]
            if mj <= 0:
                running += mj
                continue
            s_ij = max(0.0, running - mi)   # i, j 제외 '개입 기회'
            if lam <= 0.0:
                p = (mi * mj) / ((mi + s_ij) * (mi + mj + s_ij))
            else:
                a = mi + s_ij
                p = max(0.0, _p_gt(a, lam) - _p_gt(a + mj, lam))
            row.append((j, p))
            running += mj

        tot = sum(p for _, p in row)
        if tot <= 0:
            continue
        o_i = total_trips * (masses[i] / total_mass)   # O_i 는 원 질량 기준
        for j, p in row:
            t = o_i * (p / tot)
            if t > 0:
                flows.append(ODFlow(i=i, j=j, trips=t))
    return flows


def od_summary(flows: list[ODFlow], masses: list[float],
               coords: list[tuple[float, float]]) -> dict:
    """OD 품질 진단 — 구조성·방향 비대칭·통행거리(문서 §6-4)."""
    if not flows:
        return {"n_flows": 0}
    D = _distance_matrix(coords)
    total = sum(f.trips for f in flows)

    # 통행거리 분포 (통행량 가중)
    wlen = sum(f.trips * D[f.i][f.j] for f in flows) / total
    lens = sorted((D[f.i][f.j], f.trips) for f in flows)
    acc, med_len = 0.0, 0.0
    for d, t in lens:
        acc += t
        if acc >= total / 2:
            med_len = d
            break

    # 도착 쏠림: 존별 유입량, 질량과의 상관(고질량이 더 끌어당기나)
    nz = len(masses)
    inflow = [0.0] * nz
    for f in flows:
        inflow[f.j] += f.trips
    top = sorted(range(nz), key=lambda z: -inflow[z])[:max(1, nz // 20)]
    top_share = sum(inflow[z] for z in top) / total

    # 방향 비대칭 |T_ij−T_ji|/(T_ij+T_ji)
    tmap = {(f.i, f.j): f.trips for f in flows}
    asym = []
    seen = set()
    for (i, j), tij in tmap.items():
        if (j, i) in seen:
            continue
        seen.add((i, j))
        tji = tmap.get((j, i), 0.0)
        if tij + tji > 0:
            asym.append(abs(tij - tji) / (tij + tji))
    asym.sort()
    return {
        "n_flows": len(flows),
        "total_trips": total,
        "trip_len_median_m": round(med_len),
        "trip_len_mean_m": round(wlen),
        "dest_top5pct_share": round(top_share, 3),
        "asymmetry_median": round(asym[len(asym) // 2], 3) if asym else 0.0,
        "asymmetry_p90": round(asym[int(len(asym) * 0.9)], 3) if asym else 0.0,
    }
