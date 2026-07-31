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


# 통행 배정에서 빠지면 "그 축 교통량이 0"이 되어 눈에 띄는 도로 종류.
# 주택가 골목(residential)·이면도로는 몇 개 빠져도 정상이라 여기 넣지 않는다.
_MAJOR_ROAD_TYPES = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
})


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


def _routing_graph(net, vclass: str = "passenger") -> tuple[dict, dict]:
    """(정방향, 역방향) 인접 리스트 — `_scc_components`와 **같은** 통행 허용 규칙."""
    fwd: dict[str, list[str]] = {}
    bwd: dict[str, list[str]] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        fwd.setdefault(e.getID(), [])
        bwd.setdefault(e.getID(), [])
    for e in net.getEdges():
        eid = e.getID()
        if eid not in fwd:
            continue
        try:
            outgoing = e.getAllowedOutgoing(vclass)
        except Exception:
            outgoing = e.getOutgoing()
        for nxt in outgoing:
            nid = nxt.getID()
            if nid in fwd:
                fwd[eid].append(nid)
                bwd[nid].append(eid)
    return fwd, bwd


def _reachable(starts, graph: dict) -> set[str]:
    from collections import deque
    seen = set(starts)
    q = deque(seen)
    while q:
        u = q.popleft()
        for v in graph.get(u, []):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def routable_edges(net_or_file, vclass: str = "passenger") -> set[str]:
    """**통행이 실제로 지나갈 수 있는** 엣지 ID 집합.

    판정: 본토(최대 SCC)에서 **갈 수 있고**, 동시에 본토로 **돌아올 수 있는** 엣지.
    이 조건을 만족하면 본토의 임의의 O·D 쌍에 대해 `O → e → D` 경로가 존재하므로
    duarouter가 실제로 그 엣지를 쓸 수 있다. 최대 SCC는 이 집합의 부분집합이다.

    ⚠️ 예전에는 최대 SCC 자체를 썼는데, 그러면 **왕복이 안 되는 일방통행 간선**이
    통째로 빠진다. 대표적으로 고속도로는 상·하행이 서로 다른 엣지라 SCC가 성립하지
    않는다. 본토에서 올라타 램프로 내려올 수 있으면 통행에 쓸 수 있는데도 배제됐다.

    ⚠️ 다만 이 완화가 **잘린 고속도로를 살리지는 못한다.** 구역 경계에서 끊긴 고속도로는
    올라타면 내려올 방법이 없어(진출 램프가 구역 밖) 이 조건도 통과하지 못한다.
    2026-07-29 실측(area-1b93a715): 본토→고속도로 진입 4개, 고속도로→본토 진출 2개인데
    **교집합 0개**라 완화 전후가 동일했다(1,846 → 1,846). 그런 구역의 고속도로에 차를
    태우려면 구역 밖에서 들어오는 통과 교통이 필요하다(별도 과제).
    """
    net = _as_net(net_or_file)
    comps = _scc_components(net, vclass)
    if not comps:
        return set()
    main = set(comps[0])
    fwd, bwd = _routing_graph(net, vclass)
    return _reachable(main, fwd) & _reachable(main, bwd)


def boundary_taz(
    net_or_file,
    bbox: tuple[float, float, float, float],
    vclass: str = "passenger",
) -> tuple[dict, dict]:
    """구역 경계를 넘나드는 엣지 → 통과 교통용 진입·진출 TAZ.

    `(entry_taz, exit_taz)`를 돌려준다. 각각 `{"ext_in_N": [(edge_id, weight), ...], ...}`
    형태로 방위(N/S/E/W)별로 나뉜다.

    **방위를 나누는 이유**: 하나로 뭉치면 od2trips가 같은 쪽으로 들어와 같은 쪽으로 나가는
    통행을 만든다. 구역을 가로지르지 않으니 통과 교통이 아니다. 호출 측에서 서로 다른
    방위끼리만 OD를 만들면 반드시 구역을 지나간다.

    **가중치는 용량(차로수 × 제한속도)**이다. 길이 가중을 쓰면 고속도로 한 조각이 골목과
    같은 취급을 받는다 — 실제로 경계를 넘는 교통량은 그 도로가 얼마나 굵으냐로 갈린다.

    진입/진출 판정은 엣지 **양 끝 노드**가 구역 안인지로 한다:
        바깥 → 안 = 진입(여기서 출발하면 밖에서 들어오는 차가 된다)
        안 → 바깥 = 진출(여기서 도착하면 밖으로 나가는 차가 된다)
    """
    net = _as_net(net_or_file)
    minlng, minlat, maxlng, maxlat = bbox
    entry: dict[str, list] = {}
    exit_: dict[str, list] = {}

    def _inside(lng: float, lat: float) -> bool:
        return minlng <= lng <= maxlng and minlat <= lat <= maxlat

    cx, cy = (minlng + maxlng) / 2.0, (minlat + maxlat) / 2.0
    span_x = max(maxlng - minlng, 1e-9)
    span_y = max(maxlat - minlat, 1e-9)

    def _side(lng: float, lat: float) -> str:
        """이 점이 구역의 어느 쪽인가 — N/S/E/W.

        바깥 점은 '어느 변을 넘었나', 안쪽 점(망 자체의 끝)은 '중심에서 어느 방향인가'로
        정한다. 망 끝도 정상 방위를 받아야 호출 측의 "서로 다른 방위끼리만 짝짓기" 규칙이
        그대로 적용된다 — 별도 라벨을 주면 그 규칙에 걸려 짝이 아예 안 생긴다.
        """
        if not _inside(lng, lat):
            d = {"W": minlng - lng, "E": lng - maxlng, "S": minlat - lat, "N": lat - maxlat}
            return max(d, key=d.get)
        return (("E" if lng > cx else "W")
                if abs(lng - cx) / span_x > abs(lat - cy) / span_y
                else ("N" if lat > cy else "S"))

    comps = _scc_components(net, vclass)
    main = set(comps[0]) if comps else set()
    fwd, bwd = _routing_graph(net, vclass)

    # ── 1단계: 문 **후보**를 모은다 (연결성은 아직 안 본다) ────────────────────
    #   진입 후보 = 바깥→안으로 경계를 넘거나, 아무도 이어주지 않는 엣지(망의 시작점)
    #   진출 후보 = 안→바깥으로 넘거나, 더 갈 데가 없는 엣지(망의 끝)
    # 망의 시작·끝을 문으로 쓰는 이유: 고속도로는 나들목 간격이 km 단위라 어떤 창을 잡아도
    # 중간이 잘리고, 잘린 끝은 경계 안쪽에 생겨 (a) 조건에 안 걸린다. SUMO는 경로 끝에
    # 도달한 차를 제거하므로 out=0은 그대로 나가는 문, in=0은 들어오는 문이다.
    geom: dict[str, tuple] = {}
    ent_cand: set[str] = set()
    exit_cand: set[str] = set()
    for e in net.getEdges():
        eid = e.getID()
        if eid not in fwd:                 # vclass 통행 불가
            continue
        try:
            flng, flat = net.convertXY2LonLat(*e.getFromNode().getCoord())
            tlng, tlat = net.convertXY2LonLat(*e.getToNode().getCoord())
            w = max(e.getLaneNumber(), 1) * max(e.getSpeed(), 1.0)
        except Exception:
            continue
        f_in, t_in = _inside(flng, flat), _inside(tlng, tlat)
        geom[eid] = (flng, flat, tlng, tlat, w)
        if f_in != t_in:
            (ent_cand if t_in else exit_cand).add(eid)
        else:
            if not bwd.get(eid):
                ent_cand.add(eid)
            if not fwd.get(eid):
                exit_cand.add(eid)

    # ── 2단계: 후보를 **본토 또는 다른 문**과의 연결로 검증한다 ────────────────
    #
    # ⚠️ 2026-07-30 — 예전엔 `to_main`/`from_main`(본토 도달 여부)만 봤다. 그래서
    # **본토를 한 번도 지나지 않는 통과 경로가 통째로 탈락했다.** 고속도로를 타고 들어와
    # 고속도로로 빠지는 차가 바로 그 경우인데, 그게 통과 교통의 대표 패턴이다.
    #
    # 안양 net 실측 — 고속도로가 본토와 안 닿는 두 개의 편도 본선을 이룬다:
    #     남행: 546511395·58794139(시작) → 58794132 → ┬ 1454894395 → 본토
    #                                                 └ 58794095#1(끝) ← from_main=False
    #     북행: 본토 → 682918000 ─┬→ AddedOnRamp → 58858257#1(끝)
    #           58858257#0(시작) ─┘                  ← to_main=False
    # 4차로 2,076m와 2,186m(합 17.1 lane-km)가 이 이유로 문에서 빠져 있었다.
    #
    # → 도착지 후보 전체를 목표로 두고 "진입 후보에서 갈 수 있나", 출발지 후보 전체를
    #   출발로 두고 "진출 후보에 닿을 수 있나"를 본다. 후보를 전부 시드에 넣으므로
    #   한 번만 훑어도 고정점이다(반복 불필요).
    from_src = _reachable(main | ent_cand, fwd)   # 어디든 출발지에서 갈 수 있는 엣지
    to_dst = _reachable(main | exit_cand, bwd)    # 어디든 도착지에 닿을 수 있는 엣지

    for eid in ent_cand:
        if eid in to_dst:                          # 나갈 방법이 있어야 출발지로 쓸 수 있다
            flng, flat, _, _, w = geom[eid]
            entry.setdefault(f"ext_in_{_side(flng, flat)}", []).append((eid, w))
    for eid in exit_cand:
        if eid in from_src:                        # 올 방법이 있어야 도착지로 쓸 수 있다
            _, _, tlng, tlat, w = geom[eid]
            exit_.setdefault(f"ext_out_{_side(tlng, tlat)}", []).append((eid, w))
    return entry, exit_


def gate_routable_edges(
    net_or_file,
    bbox: tuple[float, float, float, float],
    vclass: str = "passenger",
) -> set[str]:
    """**통과 교통까지 감안한** 통행 가능 엣지 — `boundary_taz`의 문을 O/D로 인정한다.

    `routable_edges`는 "본토에서 갔다가 본토로 돌아올 수 있나"를 묻는다. 구역 안에서
    출발해 구역 안에서 끝나는 통행에는 그게 맞다. 하지만 통과 교통이 켜져 있으면 문이
    출발지·도착지가 되므로 판정 기준이 달라진다 — 잘린 고속도로가 여기서 살아난다.

    안양 net 실측: routable 211.0 lane-km(고속도로 0) → 이 함수 256.3(고속도로 37.1).
    승용차 전체가 257.9이므로 사실상 쓰이는 도로를 다 담는다.

    ⚠️ 통과 교통이 **꺼져 있으면 쓰지 말 것.** 그때는 문에 차가 안 깔리므로 고속도로가
       실제로 비어 있고, `routable_edges` 쪽이 사실에 맞다.
    """
    net = _as_net(net_or_file)
    entry, exit_ = boundary_taz(net, bbox, vclass)
    ent_e = {eid for taz in entry.values() for eid, _ in taz}
    exit_e = {eid for taz in exit_.values() for eid, _ in taz}
    comps = _scc_components(net, vclass)
    main = set(comps[0]) if comps else set()
    fwd, bwd = _routing_graph(net, vclass)
    return _reachable(main | ent_e, fwd) & _reachable(main | exit_e, bwd)


def excluded_major_roads(
    net_or_file,
    vclass: str = "passenger",
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> dict:
    """통행에 못 쓰이는 **주요 도로**를 종류별로 집계 — "여긴 왜 차가 없지?"의 답.

    주택가 골목이 몇 개 빠지는 건 정상이지만 고속도로·간선이 통째로 빠지면 그 축의
    교통량이 0이 되고, 기지국·RSU 배치까지 그 지역을 통째로 비운다. 그런데 지금까지
    그 사실이 **아무 데도 드러나지 않아** 배치 결과만 보고는 원인을 알 수 없었다.

    bbox : 주면 각 종류를 **통과 교통이 쓸 수 있는 몫**(`through_lane_km`)과 그마저도
        못 쓰는 몫(`dead_lane_km`)으로 나눈다.

        ⚠️ 왜 나눠야 하나 (2026-07-30). 이 함수는 `routable_edges`(구역 **내부** 통행
        기준)만 봤고, 호출부는 그걸로 "이 축의 교통량은 0이 됩니다"라고 경고했다.
        그런데 통과 교통이 켜져 있으면 **사실이 아니다** — 안양 실측에서 배제된 고속도로가
        차량의 15.8%를 나른다. 경고가 거짓이면 이미 해결된 문제를 미해결로 보이게 만들고,
        진짜로 비어 있는 축을 찾는 데 방해가 된다.
    """
    net = _as_net(net_or_file)
    usable = routable_edges(net, vclass)
    gate_usable = gate_routable_edges(net, bbox, vclass) if bbox is not None else set()
    out: dict[str, dict] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass) or e.getID() in usable:
                continue
        except Exception:
            continue
        t = (e.getType() or "?").split(".")[-1]
        if t not in _MAJOR_ROAD_TYPES:
            continue
        rec = out.setdefault(t, {"count": 0, "lane_km": 0.0,
                                 "through_lane_km": 0.0, "dead_lane_km": 0.0})
        lk = e.getLength() * e.getLaneNumber() / 1000.0
        rec["count"] += 1
        rec["lane_km"] += lk
        if e.getID() in gate_usable:
            rec["through_lane_km"] += lk       # 통과 교통은 쓸 수 있다
        else:
            rec["dead_lane_km"] += lk          # 아무 통행도 못 쓴다
    for rec in out.values():
        for k in ("lane_km", "through_lane_km", "dead_lane_km"):
            rec[k] = round(rec[k], 2)
    return out


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
        # ⚠️ 소수 2자리로 쓰면 시간대 슬라이스가 뭉개진다 (2026-07-27 실측):
        # 07~09시 15분 8구간의 factor 0.1046·0.1129·0.1257·0.1284·0.1305·0.1328·0.1313·0.1261이
        # 전부 0.11·0.12·0.13×6 으로 반올림되어, 의도한 1.26배 굴곡이 1.12배로 납작해졌다.
        # 굴곡이 사라지면 정체가 안 생기고, 그러면 배치 비교 자체가 성립하지 않는다(§5-1).
        f.write(f'{factor:.8f}\n')
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
