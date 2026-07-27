"""배정 준비 — radiation OD를 SUMO od2trips 입력으로 변환 (문서 §7·§8).

두 산출물:
  1) TAZ 파일  : 격자 존 → 그 존 안의 도로 엣지 목록(길이 가중). 존 내부에서 어느 엣지로
                 출발/도착할지 od2trips가 가중 랜덤으로 뽑는다(§7 disaggregation).
  2) OD 파일   : radiation T_ij 를 SUMO O-format(VISUM)으로.

존↔엣지 정렬: build_zones와 동일한 cell_of(ref_lat, origin_shift) 로 셀 인덱스를 맞춰야
              같은 (ix,iy)에 건물 질량과 도로가 함께 담긴다.

이후 CLI 파이프라인(스크립트/오케스트레이션에서):
  od2trips  --net-file NET --taz-files TAZ --od-matrix-files OD --output-file TRIPS
  duarouter --net-file NET --route-files TRIPS --output-file ROUTES
  sumo      --net-file NET --route-files ROUTES --edgedata-output EDGEDATA ...
"""
from __future__ import annotations

import math
from typing import Optional

from .grid_mass import Zone, cell_of


def build_taz(
    net_file: str,
    cell_size_m: float,
    ref_lat: float,
    origin_shift_m: tuple[float, float] = (0.0, 0.0),
    vclass: str = "passenger",
) -> dict[str, list[tuple[str, float]]]:
    """net.xml 도로 엣지를 격자 셀(TAZ)로 묶는다.

    Returns: { "ix_iy": [(edge_id, length_m), ...] }  — vclass 통행 가능한 엣지만.
    """
    import sumolib  # 지연 임포트 (sumolib 없는 환경 보호)

    net = sumolib.net.readNet(net_file)
    taz: dict[str, list[tuple[str, float]]] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        shape = e.getShape()
        if not shape:
            continue
        mx, my = shape[len(shape) // 2]
        lng, lat = net.convertXY2LonLat(mx, my)
        ix, iy = cell_of(lat, lng, cell_size_m, ref_lat, origin_shift_m)
        taz.setdefault(f"{ix}_{iy}", []).append((e.getID(), e.getLength()))
    return taz


def zone_taz_id(z: Zone) -> str:
    """존 → TAZ id (셀 인덱스 문자열). build_taz의 키와 동일 규칙."""
    return f"{z.ix}_{z.iy}"


def write_taz_xml(taz: dict[str, list[tuple[str, float]]], path: str) -> None:
    """SUMO TAZ 파일. 존 내 엣지를 길이 가중(tazSource/tazSink weight)으로."""
    with open(path, "w", encoding="utf-8") as f:
        f.write('<tazs>\n')
        for tid, edges in taz.items():
            f.write(f'  <taz id="{tid}">\n')
            for eid, w in edges:
                f.write(f'    <tazSource id="{eid}" weight="{w:.2f}"/>\n')
                f.write(f'    <tazSink id="{eid}" weight="{w:.2f}"/>\n')
            f.write('  </taz>\n')
        f.write('</tazs>\n')


def write_od_o_format(
    flows,
    zone_taz: dict[int, Optional[str]],
    path: str,
    begin_h: float = 7.0,
    end_h: float = 8.0,
    factor: float = 1.0,
) -> int:
    """radiation ODFlow 목록 → SUMO O-format(VISUM $OR;D2).

    zone_taz: 존 인덱스 → TAZ id(엣지 있는 존만; 없으면 None → 스킵).
    시간(begin_h~end_h)은 시(hour) 단위. 여러 시간대 프로파일은 슬라이스별로 여러 파일.
    Returns: 기록된 OD 라인 수.
    """
    lines: list[tuple[str, str, float]] = []
    for fl in flows:
        o = zone_taz.get(fl.i)
        d = zone_taz.get(fl.j)
        if o is None or d is None or o == d:
            continue
        lines.append((o, d, fl.trips))
    with open(path, "w", encoding="utf-8") as f:
        f.write('$OR;D2\n')
        f.write('* From-Time  To-Time\n')
        f.write(f'{begin_h:.2f} {end_h:.2f}\n')
        f.write('* Factor\n')
        f.write(f'{factor:.2f}\n')
        f.write('* Origin  Destination  Count\n')
        for o, d, t in lines:
            f.write(f'{o} {d} {t:.4f}\n')
    return len(lines)


def map_zones_to_taz(
    zones: list[Zone],
    taz: dict,
    cell_size_m: float = 300.0,
    max_reassign_m: float = 900.0,
) -> dict[int, Optional[str]]:
    """존 인덱스 → TAZ id. 자기 셀에 도로가 없으면 **가장 가까운 도로 있는 셀로 재배정**.

    왜 재배정하나 (2026-07-27 실측 근거):
        건물이 있다 = 사람이 산다는 뜻인데, 그 셀에 승용차 통행 가능한 엣지가 없다고 해서
        그 사람들이 차를 안 타는 게 아니다. 골목을 걸어나가 옆 셀의 큰길에서 탄다.
        예전 구현은 도로 없는 셀을 그냥 None으로 버렸고, 그 결과 영등포 구역 스모크
        테스트에서 존 47개 중 24개(질량 48.7%)가 배정 전에 증발했다 —
        total_trips=5000을 넣어도 실제 배정은 1326통행. 이러면 N* 튜닝의 의미가 없다.

    Parameters
    ----------
    cell_size_m : build_zones/build_taz에 넘긴 것과 **같은 값**(셀 인덱스 → 미터 환산용).
    max_reassign_m : 이 거리를 넘으면 재배정하지 않고 None(버림). 기본 900m = 300m 셀 3칸.
        무한정 재배정하면 도로에서 몇 km 떨어진 산속 건물까지 도심 간선에 붙어버려
        질량장이 왜곡된다. 상한은 "걸어서 큰길까지" 정도가 타당.

    Returns
    -------
    dict[존 인덱스, TAZ id 또는 None] — None은 상한 안에 도로가 전혀 없는 존.
    """
    # TAZ 키("ix_iy")를 셀 좌표로 역파싱. 존과 TAZ는 같은 격자 위에 있으므로(같은 cell_of
    # 규칙) 거리 계산을 셀 인덱스 공간에서 하면 되고, 재투영이 필요 없다.
    taz_cells: dict[tuple[int, int], str] = {}
    for key in taz:
        ix_s, _, iy_s = key.partition("_")
        try:
            taz_cells[(int(ix_s), int(iy_s))] = key
        except ValueError:
            continue

    cell = max(float(cell_size_m), 1.0)
    reach = int(math.floor(max(0.0, max_reassign_m) / cell))   # 스캔 반경(셀)

    out: dict[int, Optional[str]] = {}
    for i, z in enumerate(zones):
        own = zone_taz_id(z)
        if own in taz:
            out[i] = own
            continue
        # 유클리드 거리 ≤ reach 셀인 후보는 모두 [-reach, reach]² 안에 있으므로
        # 이 정사각 이웃만 훑으면 최근접이 정확히 나온다(근사 아님).
        best_key, best_d2 = None, None
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                k = taz_cells.get((z.ix + dx, z.iy + dy))
                if k is None:
                    continue
                d2 = dx * dx + dy * dy
                if best_d2 is None or d2 < best_d2:
                    best_key, best_d2 = k, d2
        if best_key is not None and math.sqrt(best_d2) * cell <= max_reassign_m:
            out[i] = best_key
        else:
            out[i] = None
    return out


def taz_mapping_summary(
    zones: list[Zone],
    zone_taz: dict[int, Optional[str]],
    cell_size_m: float = 300.0,
) -> dict:
    """존→TAZ 매핑 품질 진단 — 자기 셀 / 재배정 / 버림이 각각 질량의 몇 %인지.

    수요가 어디서 새는지 보이게 하려는 것. total_trips를 아무리 튜닝해도 여기서
    새면 실제 배정량이 달라지므로, 파이프라인 실행 때마다 찍어볼 것.
    """
    if not zones:
        return {"n_zones": 0}
    total_m = sum(z.mass for z in zones) or 1.0
    n_own = n_re = n_drop = 0
    m_own = m_re = m_drop = 0.0
    dists: list[float] = []

    for i, z in enumerate(zones):
        tid = zone_taz.get(i)
        if tid is None:
            n_drop += 1
            m_drop += z.mass
        elif tid == zone_taz_id(z):
            n_own += 1
            m_own += z.mass
        else:
            n_re += 1
            m_re += z.mass
            ix_s, _, iy_s = tid.partition("_")
            dists.append(math.hypot(int(ix_s) - z.ix, int(iy_s) - z.iy) * cell_size_m)

    dists.sort()
    return {
        "n_zones": len(zones),
        "zones_own_cell": n_own,
        "zones_reassigned": n_re,
        "zones_dropped": n_drop,
        "mass_own_pct": round(m_own / total_m * 100, 1),
        "mass_reassigned_pct": round(m_re / total_m * 100, 1),
        "mass_dropped_pct": round(m_drop / total_m * 100, 1),
        "reassign_dist_median_m": round(dists[len(dists) // 2]) if dists else 0,
        "reassign_dist_max_m": round(dists[-1]) if dists else 0,
    }
