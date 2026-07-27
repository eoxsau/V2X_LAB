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
import os
from typing import Optional

from .grid_mass import Zone, cell_of


def read_net(net_file: str):
    """net.xml 로드 (sumolib 지연 임포트).

    같은 net을 build_taz·component_summary 등 여러 곳에서 쓸 때는 한 번 읽어 객체를
    돌려쓸 것. 아래 함수들은 경로와 net 객체를 모두 받는다.
    """
    import sumolib  # 지연 임포트 (sumolib 없는 환경 보호)

    return sumolib.net.readNet(str(net_file))


def _as_net(net_or_file):
    if isinstance(net_or_file, (str, os.PathLike)):
        return read_net(net_or_file)
    return net_or_file


def net_bbox(
    net_or_file,
    vclass: str = "passenger",
    margin_m: float = 300.0,
) -> tuple[float, float, float, float]:
    """실제 도로가 깔린 범위 → (minlng, minlat, maxlng, maxlat). **건물 조회는 이걸 쓸 것.**

    ⚠️ net.xml의 `origBoundary`/`convBoundary`를 쓰면 안 된다 (2026-07-27 실측):
        Overpass가 bbox에 걸친 way의 **전 노드**를 반환하므로, 멀리 뻗은 도로·노선
        관계의 노드까지 파일에 들어온다. netconvert는 그 노드들까지 포함해 boundary를
        계산하지만 엣지로는 만들지 않는다. 그 결과 두 값이 크게 어긋난다 —
        `area-1b5adb59`에서 origBoundary는 6.1×13.4km(82km²)인데 실제 승용차 도로는
        1.5×1.0km였다. 그걸로 건물을 긁으면 존 897개 중 도로 있는 셀이 17개뿐이라
        질량의 95.8%가 배정 전에 증발한다(생존율 2.0%).
        (`.osm`의 `<bounds>` 태그는 맞는 값이지만 파일에 없을 수도 있어 신뢰 불가 —
         `area-0baecbba.osm`엔 아예 없다. 엣지 형상에서 직접 재는 게 유일하게 안전하다.)

    margin_m : 도로 범위 밖으로 넓힐 여유. 기본 300m(= 셀 한 칸). 도로 끝 셀에 걸친
        건물까지 수요로 잡아 `map_zones_to_taz` 재배정이 흡수하게 한다.
    """
    net = _as_net(net_or_file)
    lats: list[float] = []
    lngs: list[float] = []
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        for x, y in e.getShape():
            lng, lat = net.convertXY2LonLat(x, y)
            lats.append(lat)
            lngs.append(lng)
    if not lats:
        raise ValueError(f"net에 {vclass} 통행 가능한 엣지가 없습니다")

    dlat = margin_m / 111_320.0
    dlng = margin_m / (111_320.0 * max(math.cos(math.radians(sum(lats) / len(lats))), 1e-6))
    return (min(lngs) - dlng, min(lats) - dlat, max(lngs) + dlng, max(lats) + dlat)


def _scc_components(net, vclass: str = "passenger") -> list[list[str]]:
    """vclass 라우팅 그래프의 강연결성분(SCC)을 크기 내림차순으로.

    노드 = 엣지, 아크 = `getAllowedOutgoing(vclass)`. 이 함수는 duarouter가 실제로 쓰는
    것과 같은 통행 허용 규칙(from-lane·to-lane·connection 전부 vclass 허용)을 따르므로,
    여기서 서로 못 가는 엣지는 duarouter도 못 간다.
    """
    adj: dict[str, list[str]] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        adj.setdefault(e.getID(), [])
        try:
            outgoing = e.getAllowedOutgoing(vclass)
        except Exception:
            outgoing = e.getOutgoing()
        for nxt in outgoing:
            try:
                if not nxt.allows(vclass):
                    continue
            except Exception:
                pass
            adj[e.getID()].append(nxt.getID())
    # 후속 엣지 중 adj 키에 없는 것 제거(양쪽 다 vclass 통과 엣지여야 아크 성립)
    for eid, nxts in adj.items():
        adj[eid] = [n for n in nxts if n in adj]

    # Tarjan (반복형 — 엣지 수천 개에서 재귀 한계를 넘지 않도록)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    comps: list[list[str]] = []
    counter = 0

    for root in adj:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        work = [(root, iter(adj[root]))]
        while work:
            v, it = work[-1]
            descended = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(adj[w])))
                    descended = True
                    break
                if on_stack.get(w):
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                comps.append(comp)

    comps.sort(key=len, reverse=True)
    return comps


def routable_edges(net_or_file, vclass: str = "passenger") -> set[str]:
    """최대 강연결성분에 속한 엣지 ID 집합 — 서로 왕복 가능한 '본토' 도로망."""
    comps = _scc_components(_as_net(net_or_file), vclass)
    return set(comps[0]) if comps else set()


def component_summary(net_or_file, vclass: str = "passenger") -> dict:
    """vclass 그래프 연결성 진단 — 고립 섬이 얼마나 있는지.

    ⚠️ 반드시 vclass 필터 후에 볼 것. 전체 엣지 기준으로는 보행자 길이 다리를 놓아
    멀쩡해 보이지만, 승용차 기준으로 보면 섬이 드러난다 (2026-07-27 영등포 실측:
    전체 기준 최대 SCC 88% vs 승용차 기준 90%지만 50개 엣지가 31개 섬에 흩어져 있고,
    그 10%가 duarouter 실패의 51%를 만들었다).
    """
    comps = _scc_components(_as_net(net_or_file), vclass)
    total = sum(len(c) for c in comps)
    largest = len(comps[0]) if comps else 0
    return {
        "vclass_edges": total,
        "n_components": len(comps),
        "largest_component": largest,
        "largest_pct": round(largest / total * 100, 1) if total else 0.0,
        "isolated_edges": total - largest,
        "component_sizes_top5": [len(c) for c in comps[:5]],
    }


def build_taz(
    net_file: str,
    cell_size_m: float,
    ref_lat: float,
    origin_shift_m: tuple[float, float] = (0.0, 0.0),
    vclass: str = "passenger",
    largest_component_only: bool = True,
) -> dict[str, list[tuple[str, float]]]:
    """net.xml 도로 엣지를 격자 셀(TAZ)로 묶는다.

    Parameters
    ----------
    net_file : 경로 또는 이미 읽은 sumolib net 객체.
    largest_component_only : 최대 승용차 SCC 밖의 고립 엣지를 TAZ에서 제외(기본 True).

        왜 거르나 (2026-07-27 영등포 실측 근거):
            od2trips는 TAZ에 담긴 엣지를 길이 가중으로 뽑아 출발·도착지로 삼는다.
            고립된 섬 도로가 TAZ에 있으면 그걸 뽑고, duarouter는 본토에서 그 섬으로
            가는 경로를 못 찾아 통행을 버린다. 승용차 엣지 498개 중 50개(10%)가
            31개 섬에 흩어져 있었고, 그것들이 라우팅 실패의 51%를 만들었다
            (TAZ가 길이 가중이라 긴 섬 도로가 자주 뽑힌다).

        `netconvert --keep-edges.components 1`은 대안이 아니다 — vClass를 구분하지 않아
        보행자 길로 이어진 것도 연결로 치기 때문에 실측에서 엣지 16개만 지웠고
        라우팅률은 그대로였다.

        섬만 있던 셀은 TAZ에서 사라지고, `map_zones_to_taz`의 최근접 재배정이 그 수요를
        가장 가까운 **연결된** 존으로 보낸다. 두 장치가 서로를 보완한다.

    Returns: { "ix_iy": [(edge_id, length_m), ...] }  — vclass 통행 가능한 엣지만.
    """
    net = _as_net(net_file)
    keep = routable_edges(net, vclass) if largest_component_only else None

    taz: dict[str, list[tuple[str, float]]] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        if keep is not None and e.getID() not in keep:
            continue
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


def _hhmm(hours: float) -> str:
    """소수 시간 → SUMO O-format의 `HH.MM`. 7.5 → "7.30" (7시 30분)."""
    total_min = int(round(hours * 60))
    return f"{total_min // 60}.{total_min % 60:02d}"


def write_od_o_format(
    flows,
    zone_taz: dict[int, Optional[str]],
    path: str,
    begin_h: float = 7.0,
    end_h: float = 8.0,
    factor: float = 1.0,
    keep_intra_zone: bool = True,
) -> int:
    """radiation ODFlow 목록 → SUMO O-format(VISUM $OR;D2).

    zone_taz: 존 인덱스 → TAZ id(엣지 있는 존만; 없으면 None → 스킵).
    시간(begin_h~end_h)은 시(hour) 단위. 여러 시간대 프로파일은 슬라이스별로 여러 파일.

    begin_h, end_h : **소수 시간**(7.5 = 7시 30분). 내부에서 SUMO O-format의 `HH.MM`으로
        변환해 기록한다.

        ⚠️ O-format의 시각은 소수 시간이 아니라 **`HH.MM`(시.분)** 이다 (2026-07-27 실측:
        `7.00 7.30` → 정확히 1800초 창, `7.00 7.50` → 3000초 창). 예전엔 `7.5`를
        `"7.50"`으로 그대로 찍어 **7시 30분이 아니라 7시 50분**이 됐다. 정시(7.0/8.0)만
        쓰던 동안엔 우연히 맞아떨어져 드러나지 않았고, 시간대 슬라이스를 넣자마자
        구간이 서로 겹치고 창 밖으로 새어 나갔다.

    keep_intra_zone : o == d (같은 TAZ) 통행을 살릴지. 기본 True.
        radiation은 애초에 i == j를 만들지 않으므로, 여기서 생기는 o == d는 전부
        **재배정이 서로 다른 두 존을 같은 TAZ로 합친 결과**다. 즉 실재하는 수요다.
        od2trips는 같은 TAZ 안에서도 source/sink 엣지를 따로 뽑으므로 주행이 성립한다
        (호출 측에서 `--different-source-sink`를 켜서 같은 엣지가 뽑히는 것을 막을 것).
        버리면 앞서 재배정으로 되살린 수요를 뒷단에서 다시 버리는 셈 —
        2026-07-27 영등포 실측에서 515통행(총량의 10.3%)이 여기서 증발했다.

    Returns: 기록된 OD 라인 수.
    """
    lines: list[tuple[str, str, float]] = []
    for fl in flows:
        o = zone_taz.get(fl.i)
        d = zone_taz.get(fl.j)
        if o is None or d is None:
            continue
        if o == d and not keep_intra_zone:
            continue
        lines.append((o, d, fl.trips))
    with open(path, "w", encoding="utf-8") as f:
        f.write('$OR;D2\n')
        f.write('* From-Time  To-Time\n')
        f.write(f'{_hhmm(begin_h)} {_hhmm(end_h)}\n')
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
